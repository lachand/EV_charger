from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .charge_curve import ChargeCurve, learned_power_kw, planning_power_kw
from .charge_gates import (
    DecisionReason,
    GateAction,
    GateContext,
    TimerState,
    Verdict,
    evaluate,
)
from .charge_planner import (
    ChargeWindow,
    Plan,
    PlanRequest,
    parse_clock,
    parse_windows,
    plan_charge,
)
from .charger_metrics import EVMetrics
from .config_diagnosis import (
    ConfigProblem,
    DiagnosisInputs,
    GridSignDetector,
    PhaseCountDetector,
    static_problems,
)
from .const import (
    DOMAIN,
)
from .coordinator import TuyaEVChargerDataUpdateCoordinator
from .helpers import allowed_currents
from .profile_assistant import profile_assistant_report
from .session_costing import SessionSplit
from .surplus_caps import ProtectionCaps
from .surplus_decision import (
    ForecastState,
    SurplusInputs,
    apply_forecast,
    current_supported_by,
    raw_surplus_w,
)
from .surplus_metrics import ev_power_w, is_charging, line_voltage
from .surplus_reader import SurplusReader
from .surplus_settings import parse_end_time, settings_from_entry
from .tuya_ev_charger import TuyaEVChargerClient

LOGGER = logging.getLogger(__name__)

# Internal tuning. Policy thresholds (start/stop/discharge) are configurable.
FIXED_START_DELAY_S = 30
FIXED_STOP_DELAY_S = 60
FIXED_RAMP_STEP_A = 1
FIXED_MIN_RUN_TIME_S = 0
FIXED_MAX_SESSION_DURATION_MIN = 0
FIXED_MAX_SESSION_ENERGY_KWH = 0.0
FIXED_MAX_SESSION_END_TIME = ""
FIXED_FORECAST_WEIGHT_PCT = 35
FIXED_FORECAST_SMOOTHING_S = 180
FIXED_FORECAST_DROP_GUARD_W = 500


@dataclass(slots=True, frozen=True)
class SolarSurplusSnapshot:
    mode_enabled: bool
    regulation_active: bool
    paused: bool
    force_charge_active: bool
    last_decision_reason: str
    raw_surplus_w: float | None
    effective_surplus_w: float | None
    battery_discharge_over_limit_w: float | None
    target_current_a: int | None
    # Why the last decision came out that way: which gates were consulted, which
    # one decided, and the figures it weighed. Answers "why is it not charging?"
    # from the entity's attributes rather than from a reading of the source.
    decision_trace: dict[str, Any]


class SolarSurplusController:
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: TuyaEVChargerClient,
        coordinator: TuyaEVChargerDataUpdateCoordinator,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._client = client
        self._coordinator = coordinator
        self._settings = settings_from_entry(entry)
        self._inputs = SurplusReader(hass, lambda: self._settings)
        # `monotonic` is looked up at call time so a patched clock in tests applies.
        self._caps = ProtectionCaps(hass, lambda: self._settings, self._inputs, lambda: monotonic())
        self._unsub_sensor: CALLBACK_TYPE | None = None
        self._unsub_coordinator: CALLBACK_TYPE | None = None
        self._evaluation_task: asyncio.Task[None] | None = None
        self._rerun_requested = False
        self._shut_down = False
        self._listeners: list[Callable[[], None]] = []

        # The regulation's memory between cycles, owned by the pure layer so the
        # delays and cooldowns can be exercised without Home Assistant.
        self._timers = TimerState()

        self._force_charge_until_ts: float | None = None
        self._force_charge_current_a: int | None = None
        self._pause_until_ts: float | None = None

        self._session_active = False
        self._session_started_ts: float | None = None
        self._session_energy_kwh: float = 0.0
        self._last_energy_sample_ts: float | None = None
        # Live off-peak/peak tally for the current session, sampled at the same
        # cadence as the energy above. Only accumulated when an off-peak sensor
        # is configured -- see `_update_session_energy` and
        # `session_off_peak_split`.
        self._session_total_s: float = 0.0
        self._session_off_peak_s: float = 0.0

        self._regulation_active = False
        self._last_decision_reason = "startup"
        self._last_raw_surplus_w: float | None = None
        self._last_available_surplus_w: float | None = None
        self._last_battery_discharge_over_limit_w: float | None = None
        self._last_target_current_a: int | None = None
        self._forecast_ema_surplus_w: float | None = None
        self._forecast_last_sample_ts: float | None = None
        self._last_decision_trace: dict[str, Any] = {}
        self._grid_sign = GridSignDetector()
        self._phase_count = PhaseCountDetector()

    @property
    def snapshot(self) -> SolarSurplusSnapshot:
        now = monotonic()
        return SolarSurplusSnapshot(
            mode_enabled=self._settings.mode_enabled,
            regulation_active=self._regulation_active,
            paused=self._is_pause_active(now),
            force_charge_active=self._is_force_charge_active(now),
            last_decision_reason=self._last_decision_reason,
            raw_surplus_w=self._last_raw_surplus_w,
            effective_surplus_w=self._last_available_surplus_w,
            battery_discharge_over_limit_w=self._last_battery_discharge_over_limit_w,
            target_current_a=self._last_target_current_a,
            decision_trace=dict(self._last_decision_trace),
        )

    def async_add_update_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _unsubscribe

    async def async_start(self) -> None:
        self._unsub_coordinator = self._coordinator.async_add_listener(
            self._handle_coordinator_update
        )

        sensor_entities = self._inputs.tracked_sensor_entities()
        if sensor_entities:
            self._unsub_sensor = async_track_state_change_event(
                self._hass,
                sensor_entities,
                self._handle_sensor_update,
            )

        if self._settings.mode_enabled and not self._settings.grid_sensor_entity_id:
            LOGGER.warning(
                "Solar surplus mode enabled for %s but no grid power sensor is configured.",
                self._entry.title,
            )
        self._schedule_evaluation("startup")

    async def async_apply_settings(self) -> None:
        """Re-read the config entry after an options change, without a reload.

        The controller snapshots ``_settings`` once in ``__init__``, so a surplus
        option only ever took effect through the blanket reload the update
        listener does -- which drops the charger's single local connection (#36).
        This re-reads the snapshot, rebinds the tracked-sensor listeners in case
        that set changed, and forces an evaluation against the new values.
        """
        self._settings = settings_from_entry(self._entry)

        if self._unsub_sensor is not None:
            self._unsub_sensor()
            self._unsub_sensor = None
        sensor_entities = self._inputs.tracked_sensor_entities()
        if sensor_entities:
            self._unsub_sensor = async_track_state_change_event(
                self._hass,
                sensor_entities,
                self._handle_sensor_update,
            )

        self._schedule_evaluation("options_updated")

    async def async_shutdown(self) -> None:
        # A sensor callback already queued on the loop must not start a new
        # evaluation after this point.
        self._shut_down = True
        if self._unsub_sensor is not None:
            self._unsub_sensor()
            self._unsub_sensor = None
        if self._unsub_coordinator is not None:
            self._unsub_coordinator()
            self._unsub_coordinator = None
        if self._evaluation_task is not None:
            self._evaluation_task.cancel()
            self._evaluation_task = None
        self._listeners.clear()

    async def async_force_charge_for(
        self,
        duration_s: int,
        current_a: int | None = None,
    ) -> None:
        now = monotonic()
        clamped_duration = max(0, int(duration_s))
        self._force_charge_until_ts = now + clamped_duration if clamped_duration else None
        self._force_charge_current_a = current_a
        self._set_decision("force_charge_requested" if clamped_duration else "force_charge_cleared")
        self._notify_state_listeners()
        self._schedule_evaluation("force_charge_service")

    async def async_pause_for(self, duration_s: int) -> None:
        now = monotonic()
        clamped_duration = max(0, int(duration_s))
        self._pause_until_ts = now + clamped_duration if clamped_duration else None
        self._set_decision("surplus_paused" if clamped_duration else "surplus_pause_cleared")
        self._notify_state_listeners()
        self._schedule_evaluation("pause_service")

    async def async_profile_assistant_report(self) -> dict[str, Any]:
        return await profile_assistant_report(self._client)

    def _handle_coordinator_update(self) -> None:
        self._schedule_evaluation("coordinator_update")

    def _handle_sensor_update(self, event: Event[EventStateChangedData]) -> None:
        _ = event
        self._schedule_evaluation("sensor_update")

    def _schedule_evaluation(self, reason: str) -> None:
        # Sensor callbacks can come from worker threads. Marshal scheduling
        # back to the Home Assistant event loop.
        self._hass.add_job(self._async_schedule_evaluation, reason)

    @callback
    def _async_schedule_evaluation(self, reason: str) -> None:
        if self._shut_down:
            return
        if self._evaluation_task is not None and not self._evaluation_task.done():
            self._rerun_requested = True
            return
        # A background task of the entry, so unloading the entry cancels it.
        self._evaluation_task = self._entry.async_create_background_task(
            self._hass,
            self._async_evaluation_loop(reason),
            name=f"{DOMAIN}_surplus_evaluation",
        )

    async def _async_evaluation_loop(self, reason: str) -> None:
        current_reason = reason
        while True:
            try:
                await self._async_evaluate_once(current_reason)
            except Exception:
                LOGGER.exception("Solar surplus evaluation failed for %s", self._entry.title)
                self._set_decision("evaluation_exception")
                self._regulation_active = False
                self._notify_state_listeners()

            if not self._rerun_requested:
                break
            self._rerun_requested = False
            current_reason = "rerun"

    async def _async_evaluate_once(self, reason: str) -> None:
        """Decide once, then act once.

        The decision lives in `charge_gates.evaluate`, which knows nothing about
        Home Assistant. This method only resolves the inputs, hands them over, and
        carries out the single verdict that comes back.
        """
        _ = reason
        now = monotonic()
        data = self._coordinator.data
        if data is None:
            await self._async_apply(
                Verdict(
                    action=GateAction.IDLE,
                    reason=DecisionReason.NO_COORDINATOR_DATA,
                    clear_debug=True,
                ),
                data=None,
                now=now,
            )
            return

        charging = is_charging(data)
        grid_power_for_energy = (
            self._inputs.read_grid_power_w() if self._settings.grid_sensor_entity_id else None
        )
        self._update_session_energy(now, data, charging, grid_power_for_energy)

        context = self._build_gate_context(now, data, charging)
        if context.grid_power_w is not None:
            self._grid_sign.observe(ev_power_w=ev_power_w(data), grid_power_w=context.grid_power_w)
        self._phase_count.observe(phase_count=len(data.phases))
        verdict = evaluate(context, self._timers)
        await self._async_apply(verdict, data=data, now=now, context=context)

    def _build_gate_context(
        self, now: float, data: EVMetrics, is_charging: bool, *, update_state: bool = True
    ) -> GateContext:
        """Resolve every input the gates may look at.

        The protection caps narrow the current ladder *here*, so the un-narrowed
        ladder never reaches a gate and cannot be widened again by one. That is
        the 2.13.1 fix expressed as a property of the data rather than as an
        ordering to be remembered.
        """
        available_currents = allowed_currents(data, self._entry.options)
        protection_cap, cap_source = self._caps.protection_cap(data, available_currents)
        if protection_cap is not None:
            available_currents = tuple(
                value for value in available_currents if value <= protection_cap
            )

        grid_power_w = (
            self._inputs.read_grid_power_w() if self._settings.grid_sensor_entity_id else None
        )

        battery_ready = self._inputs.is_battery_ready()
        available_surplus_w = 0.0
        max_supported_current = 0
        target_current: int | None = None
        if self._settings.mode_enabled and grid_power_w is not None:
            available_surplus_w = self._available_surplus_w(
                data=data,
                grid_power_w=grid_power_w,
                battery_ready=battery_ready,
                now=now,
                update_state=update_state,
            )
            if update_state:
                self._last_available_surplus_w = available_surplus_w
            max_supported_current = _current_supported_by_surplus(
                available_currents,
                available_surplus_w,
                line_voltage(data),
                self._settings.installation_phases,
            )
            min_current = min(available_currents) if available_currents else 0
            target_current = (
                max(min_current, max_supported_current)
                if available_currents and max_supported_current >= min_current
                else None
            )
            if update_state:
                self._last_target_current_a = target_current

        # Computed unconditionally -- `_gate_tariff` still only acts on it when
        # surplus mode is off, but `_gate_battery_floor_tariff_fallback` needs
        # it while surplus mode is on, too. Harmless when mode is on and no
        # windows are configured: `_plan_tariff` returns None either way.
        tariff_allowed: bool | None = None
        tariff_reason: DecisionReason | None = None
        tariff_is_deadline = False
        plan = self._plan_tariff(data, available_currents)
        if plan is not None:
            tariff_allowed = plan.allowed
            tariff_reason = DecisionReason(f"tariff_{plan.window.value}")
            tariff_is_deadline = plan.window is ChargeWindow.DEADLINE

        return GateContext(
            now=now,
            is_charging=is_charging,
            session_active=self._session_active,
            current_target=data.current_target,
            available_currents=available_currents,
            protection_cap=protection_cap,
            cap_source=cap_source,
            surplus_mode_enabled=self._settings.mode_enabled,
            grid_sensor_configured=bool(self._settings.grid_sensor_entity_id),
            grid_power_w=grid_power_w,
            force_charge_active=self._is_force_charge_active(now),
            force_charge_current_a=self._force_charge_current_a,
            pause_active=self._is_pause_active(now),
            external_charge_allowed=self._inputs.read_external_charge_allowed(),
            tariff_allowed=tariff_allowed,
            tariff_reason=tariff_reason,
            tariff_is_deadline=tariff_is_deadline,
            battery_ready=battery_ready,
            available_surplus_w=available_surplus_w,
            start_threshold_w=float(self._settings.start_threshold_w),
            stop_threshold_w=float(self._settings.stop_threshold_w),
            max_supported_current=max_supported_current,
            target_current=target_current,
            adjust_up_cooldown_s=float(self._settings.adjust_up_cooldown_s),
            adjust_down_cooldown_s=float(self._settings.adjust_down_cooldown_s),
            start_delay_s=float(FIXED_START_DELAY_S),
            stop_delay_s=float(FIXED_STOP_DELAY_S),
            ramp_step=FIXED_RAMP_STEP_A,
            min_run_time_s=float(FIXED_MIN_RUN_TIME_S),
            session_limit_reason=self._session_limit_reason(now),
        )

    def config_problems(self) -> list[str]:
        """Settings that are switched on but cannot do anything.

        Combines the static checks with two findings that need measurements: a
        grid sensor whose sign convention is reversed, and a charger that keeps
        reporting more phases than Installation phases assumes.
        """
        settings = self._settings
        problems = static_problems(
            DiagnosisInputs(
                surplus_mode_enabled=settings.mode_enabled,
                grid_sensor_entity_id=settings.grid_sensor_entity_id,
                max_house_power_w=settings.max_house_power_w,
                max_inverter_power_w=settings.max_inverter_power_w,
                total_load_sensor_entity_id=settings.total_load_sensor_entity_id,
                off_peak_windows_raw=settings.off_peak_windows,
                off_peak_windows_parsed=len(parse_windows(settings.off_peak_windows)),
                departure_time=settings.departure_time,
                departure_energy_kwh=settings.departure_energy_kwh,
            )
        )
        values = [problem.value for problem in problems]
        if self._grid_sign.inverted:
            values.append(ConfigProblem.GRID_SENSOR_SIGN_INVERTED.value)
        if settings.installation_phases == 1 and self._phase_count.likely_three_phase:
            values.append(ConfigProblem.INSTALLATION_PHASES_LIKELY_THREE.value)
        return values

    def async_dry_run(self) -> dict[str, Any]:
        """What regulation would do right now, without writing to the charger.

        The decision layer is pure, so it can simply be run against the live
        inputs and its verdict reported. Nothing is sent to the charger and no
        timer is disturbed: the real `TimerState` is copied first, so asking the
        question cannot change the answer to the next real evaluation.
        """
        data = self._coordinator.data
        if data is None:
            return {"error": "no data from the charger yet"}

        now = monotonic()
        context = self._build_gate_context(now, data, is_charging(data), update_state=False)
        verdict = evaluate(context, self._timers.copy())

        return {
            "would_do": str(verdict.action),
            "reason": str(verdict.reason),
            "target_current_a": verdict.target_current,
            "decided_by": verdict.decided_by,
            "gates_declined": list(verdict.consulted),
            "inputs": {
                "is_charging": context.is_charging,
                "current_target_a": context.current_target,
                "available_currents": list(context.available_currents),
                "protection_cap_a": context.protection_cap,
                "protection_source": context.cap_source,
                "grid_power_w": context.grid_power_w,
                "surplus_w": round(context.available_surplus_w),
                "start_threshold_w": round(context.start_threshold_w),
                "stop_threshold_w": round(context.stop_threshold_w),
                "battery_ready": context.battery_ready,
                "surplus_mode_enabled": context.surplus_mode_enabled,
            },
        }

    def _record_decision_trace(
        self,
        verdict: Verdict,
        reason: DecisionReason,
        context: GateContext | None,
    ) -> None:
        """Keep the reasoning behind the last decision, for the entity to expose.

        Only the figures that actually bear on a decision, so the attribute stays
        readable: the gates that declined show how far evaluation got, and the
        numbers show what the deciding gate weighed.
        """
        trace: dict[str, Any] = {
            "decided_by": verdict.decided_by,
            "gates_declined": list(verdict.consulted),
            "action": str(verdict.action),
        }
        if reason is not verdict.reason:
            # The write failed, so the reported reason is not the decided one.
            trace["decided_reason"] = str(verdict.reason)
        if verdict.target_current is not None:
            trace["target_current_a"] = verdict.target_current
        if context is not None:
            trace.update(
                {
                    "available_currents": list(context.available_currents),
                    "surplus_w": round(context.available_surplus_w),
                    "start_threshold_w": round(context.start_threshold_w),
                    "stop_threshold_w": round(context.stop_threshold_w),
                    "battery_ready": context.battery_ready,
                }
            )
            if context.protection_cap is not None:
                trace["protection_cap_a"] = context.protection_cap
                trace["protection_source"] = context.cap_source
        self._last_decision_trace = trace

    async def _async_apply(
        self,
        verdict: Verdict,
        *,
        data: EVMetrics | None,
        now: float,
        context: GateContext | None = None,
    ) -> None:
        """Carry out one verdict. The only place that writes to the charger.

        Collapsing 25 near-identical exit blocks into this method is what makes a
        decision impossible to overwrite by a later branch, and the charger
        impossible to write to twice in one cycle.
        """
        reason: DecisionReason = verdict.reason

        if verdict.action is GateAction.FORCE_CHARGE:
            reason = await self._async_force_charge_verdict(verdict, data, now)
        elif verdict.action is GateAction.SET_CURRENT and verdict.target_current is not None:
            await self._async_write_current(verdict.target_current, data, now)
        elif verdict.action is GateAction.START_CHARGE:
            reason = await self._async_start_charge(verdict, data, now)
        elif verdict.action is GateAction.STOP_CHARGE:
            await self._async_stop_charge(verdict, data, now)

        self._record_decision_trace(verdict, reason, context)
        self._set_decision(str(reason))
        self._regulation_active = verdict.regulation_active
        # Lets the coordinator poll faster while regulation is actually driving
        # the charger, rather than waiting for it to report "charging".
        self._coordinator.regulating = verdict.regulation_active
        if verdict.clear_debug:
            self._clear_surplus_debug_state()
        self._notify_state_listeners()

    async def _async_write_current(self, target: int, data: EVMetrics | None, now: float) -> bool:
        if not await self._client.async_set_charge_current(target):
            return False
        if data is not None and data.current_target is not None:
            if target > data.current_target:
                self._timers.last_increase_action_ts = now
            else:
                self._timers.last_decrease_action_ts = now
        await self._coordinator.async_request_refresh()
        return True

    async def _async_start_charge(
        self, verdict: Verdict, data: EVMetrics | None, now: float
    ) -> DecisionReason:
        target = verdict.target_current
        if (
            target is not None
            and data is not None
            and data.current_target != target
            and not await self._client.async_set_charge_current(target)
        ):
            return DecisionReason.FAILED_SET_STARTUP_CURRENT

        if not await self._client.async_set_charge_enabled(True):
            return DecisionReason.FAILED_START_CHARGE

        self._timers.last_increase_action_ts = now
        self._timers.last_decrease_action_ts = now
        self._start_session(now)
        await self._coordinator.async_request_refresh()
        return verdict.reason

    async def _async_stop_charge(
        self, verdict: Verdict, data: EVMetrics | None, now: float
    ) -> None:
        if data is None or not is_charging(data):
            return
        # A charge started outside surplus mode -- from the app, say -- is not
        # ours to interrupt when merely pausing.
        if verdict.only_stop_own_session and not self._session_active:
            return
        # Field reports (#39) of a session ending on its own need a way to see
        # which gate stopped it -- a manual toggle plus off-peak windows lands
        # here via _gate_tariff, which does not set only_stop_own_session.
        LOGGER.debug(
            "Stopping charge: reason=%s only_stop_own_session=%s session_active=%s",
            getattr(verdict.reason, "value", verdict.reason),
            verdict.only_stop_own_session,
            self._session_active,
        )
        if await self._client.async_set_charge_enabled(False):
            self._register_stop(now)
            await self._coordinator.async_request_refresh()

    async def _async_force_charge_verdict(
        self, verdict: Verdict, data: EVMetrics | None, now: float
    ) -> DecisionReason:
        target = verdict.target_current
        reason = DecisionReason.FORCE_CHARGE_HOLDING
        if data is not None and target is not None and data.current_target != target:
            if not await self._async_write_current(target, data, now):
                return DecisionReason.FORCE_CHARGE_FAILED_SET_CURRENT
            reason = DecisionReason.FORCE_CHARGE_ADJUST_CURRENT

        if data is not None and not is_charging(data):
            if not await self._client.async_set_charge_enabled(True):
                return DecisionReason.FORCE_CHARGE_FAILED_START
            self._start_session(now)
            await self._coordinator.async_request_refresh()
            return DecisionReason.FORCE_CHARGE_START

        if not self._session_active:
            self._start_session(now)
        return reason

    def _session_limit_reason(self, now: float) -> DecisionReason | None:
        if not self._session_active:
            return None

        if FIXED_MAX_SESSION_DURATION_MIN > 0 and self._session_started_ts is not None:
            duration_s = now - self._session_started_ts
            if duration_s >= FIXED_MAX_SESSION_DURATION_MIN * 60:
                return DecisionReason.SESSION_LIMIT_DURATION

        if (
            FIXED_MAX_SESSION_ENERGY_KWH > 0
            and self._session_energy_kwh >= FIXED_MAX_SESSION_ENERGY_KWH
        ):
            return DecisionReason.SESSION_LIMIT_ENERGY

        end_minutes = parse_end_time(FIXED_MAX_SESSION_END_TIME)
        if end_minutes is not None:
            now_dt = dt_util.now()
            now_minutes = now_dt.hour * 60 + now_dt.minute
            if now_minutes >= end_minutes:
                return DecisionReason.SESSION_LIMIT_END_TIME

        return None

    def _available_surplus_w(
        self,
        data: EVMetrics,
        grid_power_w: float,
        battery_ready: bool,
        now: float,
        update_state: bool,
    ) -> float:
        raw_surplus_w, discharge_over_limit_w = self._raw_surplus_w(
            data=data,
            grid_power_w=grid_power_w,
            battery_ready=battery_ready,
        )
        effective_surplus_w = self._apply_forecast_model(
            now=now,
            raw_surplus_w=raw_surplus_w,
            update_state=update_state,
        )
        if update_state:
            self._last_raw_surplus_w = raw_surplus_w
            self._last_battery_discharge_over_limit_w = discharge_over_limit_w
        return effective_surplus_w

    def _raw_surplus_w(
        self,
        data: EVMetrics,
        grid_power_w: float,
        battery_ready: bool,
    ) -> tuple[float, float]:
        discharge_over_limit_w = self._inputs.battery_discharge_over_limit_w()
        # Curtailment only counts in zero-injection setups, which is what having
        # the sensor configured signals.
        curtailed_w = (
            self._inputs.read_curtailment_power_w()
            if self._settings.curtailment_sensor_entity_id
            else 0.0
        )
        inputs = SurplusInputs(
            grid_power_w=grid_power_w,
            ev_power_w=ev_power_w(data),
            curtailed_power_w=curtailed_w,
            battery_discharge_over_limit_w=discharge_over_limit_w,
        )
        return raw_surplus_w(inputs, battery_ready=battery_ready), discharge_over_limit_w

    def _apply_forecast_model(
        self,
        *,
        now: float,
        raw_surplus_w: float,
        update_state: bool,
    ) -> float:
        result = apply_forecast(
            raw_w=raw_surplus_w,
            forecast_w=self._inputs.read_sensor_power_w(self._settings.forecast_sensor_entity_id),
            now=now,
            state=ForecastState(
                ema_w=self._forecast_ema_surplus_w,
                last_sample_ts=self._forecast_last_sample_ts,
            ),
            weight_pct=FIXED_FORECAST_WEIGHT_PCT,
            smoothing_s=FIXED_FORECAST_SMOOTHING_S,
            drop_guard_w=FIXED_FORECAST_DROP_GUARD_W,
        )
        if update_state:
            self._forecast_ema_surplus_w = result.state.ema_w
            self._forecast_last_sample_ts = result.state.last_sample_ts
        return result.effective_surplus_w

    def _plan_tariff(
        self,
        data: EVMetrics,
        available_currents: tuple[int, ...],
    ) -> Plan | None:
        """Whether the tariff schedule allows charging right now.

        Returns None when no tariff restriction is configured at all -- no
        sensor and no off-peak window -- so the caller can skip the feature
        entirely rather than reason about an "always allowed" plan.
        """
        is_off_peak_now = self._inputs.resolve_off_peak_now()
        if is_off_peak_now is None:
            return None

        target_kwh = float(self._settings.departure_energy_kwh)
        needed_kwh = max(0.0, target_kwh - self._session_energy_kwh)
        return plan_charge(
            PlanRequest(
                now=dt_util.now(),
                is_off_peak=is_off_peak_now,
                departure=parse_clock(self._settings.departure_time),
                # Only what is still missing counts towards the deadline.
                energy_needed_kwh=needed_kwh,
                charge_power_kw=self._planning_power_kw(data, available_currents, needed_kwh),
                critical_peak=self._inputs.resolve_critical_peak_now(is_off_peak_now),
            )
        )

    def _planning_power_kw(
        self,
        data: EVMetrics,
        available_currents: tuple[int, ...],
        needed_kwh: float,
    ) -> float:
        """The power to plan a deadline against, taper included where known.

        The flat rate answers "how fast on average"; the curve answers "how much
        longer once the battery is nearly full", which is what makes a plan honest
        when a lot of energy is needed. When the curve covers the range, the time
        it integrates is folded back into an effective power the planner consumes
        unchanged. Otherwise the flat learned rate stands.
        """
        flat_kw = self._estimate_charge_power_kw(data, available_currents)
        if needed_kwh <= 0:
            return flat_kw

        curve = self._active_charge_curve()
        if curve is None:
            return flat_kw
        minutes = curve.minutes_for(self._session_energy_kwh, needed_kwh)
        if minutes is None or minutes <= 0:
            return flat_kw

        taper_kw = needed_kwh / (minutes / 60.0)
        # The taper only ever slows things down; never let it claim the car is
        # faster than the flat estimate, for the same reason learning may not.
        return min(flat_kw, taper_kw) if flat_kw > 0 else taper_kw

    def _active_charge_curve(self) -> ChargeCurve | None:
        curves = getattr(self._coordinator, "vehicle_curves", None)
        if curves is None:
            return None
        tracker = getattr(self._coordinator, "vehicle_tracker", None)
        vehicle = tracker.active_vehicle if tracker is not None else None
        curve: ChargeCurve | None = curves.curve_for(vehicle)
        return curve

    def _estimate_charge_power_kw(
        self,
        data: EVMetrics,
        available_currents: tuple[int, ...],
    ) -> float:
        """Power to assume when estimating how long the charge will take.

        While charging, the measurement -- nothing beats it.

        Otherwise the charger's own rating, corrected by what this car has
        actually been seen to achieve. The rating alone says nothing about the
        *car*: a vehicle limited to 3.7 kW on a 7.4 kW charger had its charging
        time halved and was started far too late to meet its deadline. Learning
        may only ever lower the figure, never raise it above the hardware.
        """
        if data.total_power and data.total_power > 0:
            return float(data.total_power)
        if not available_currents:
            return 0.0

        theoretical_kw = (
            max(available_currents)
            * line_voltage(data)
            * self._settings.installation_phases
            / 1000.0
        )
        return planning_power_kw(
            theoretical_kw=theoretical_kw,
            learned_kw=self._learned_power_kw(),
        )

    def _learned_power_kw(self) -> float | None:
        """The rate this car has demonstrated across recorded sessions."""
        history = getattr(self._coordinator, "session_history", None)
        if history is None:
            return None
        tracker = getattr(self._coordinator, "vehicle_tracker", None)
        vehicle = tracker.active_vehicle if tracker is not None else None
        try:
            return learned_power_kw(history.sessions, vehicle=vehicle)
        except Exception as err:  # pragma: no cover  # noqa: BLE001 - accounting is best effort
            LOGGER.debug("Could not learn a charge rate: %s", err)
            return None

    def _is_force_charge_active(self, now: float) -> bool:
        return self._force_charge_until_ts is not None and now < self._force_charge_until_ts

    def _is_pause_active(self, now: float) -> bool:
        return self._pause_until_ts is not None and now < self._pause_until_ts

    def _start_session(self, now: float) -> None:
        if self._session_active:
            return
        self._session_active = True
        self._session_started_ts = now
        self._session_energy_kwh = 0.0
        self._last_energy_sample_ts = now
        # Reset here, not in `_register_stop`: the coordinator reads the
        # previous session's tally (`session_off_peak_split`) after the charge
        # has already stopped, the same way it already reads
        # `_session_energy_kwh` for the departure estimate across that gap.
        self._session_total_s = 0.0
        self._session_off_peak_s = 0.0

    def _register_stop(self, now: float) -> None:
        self._session_active = False
        self._session_started_ts = None
        self._last_energy_sample_ts = None
        self._last_decrease_action_ts = now

    def _update_session_energy(
        self,
        now: float,
        data: EVMetrics,
        is_charging: bool,
        _grid_power_w: float | None,
    ) -> None:
        _ = _grid_power_w

        if not is_charging or not self._session_active:
            self._last_energy_sample_ts = now if is_charging else None
            return

        if self._last_energy_sample_ts is None:
            self._last_energy_sample_ts = now
            return

        elapsed_s = max(0.0, now - self._last_energy_sample_ts)
        self._last_energy_sample_ts = now
        if elapsed_s <= 0:
            return

        power_kw = ev_power_w(data) / 1000.0
        session_increment_kwh = power_kw * (elapsed_s / 3600.0)
        self._session_energy_kwh += max(0.0, session_increment_kwh)
        self._record_curve_sample(power_kw)

        # Only worth tallying live when there is an off-peak sensor to sample --
        # without one, `session_off_peak_split` returns None and the coordinator
        # reconstructs the split from the configured windows exactly as before
        # this feature existed.
        if self._settings.off_peak_sensor_entity_id:
            self._session_total_s += elapsed_s
            if self._inputs.resolve_off_peak_now():
                self._session_off_peak_s += elapsed_s

    def session_off_peak_split(self) -> SessionSplit | None:
        """The live-sampled off-peak/peak split for the session just ended.

        None when there is nothing to report: no off-peak sensor configured, or
        a session with no live-tracked seconds -- one that predates a Home
        Assistant restart, or was started outside this controller (e.g. from
        the Tuya app, which `_update_session_energy` never sees since
        `_session_active` was never set for it). The caller
        (`coordinator._build_session_record`) falls back to reconstructing the
        split from the configured windows in that case, exactly as it did
        before this feature existed.

        Not reset here: like `_session_energy_kwh`, the tally is read after the
        charge has already stopped (`_register_stop` runs first), so clearing
        it happens at the start of the *next* session instead -- see
        `_start_session`.
        """
        if not self._settings.off_peak_sensor_entity_id or self._session_total_s <= 0:
            return None

        total_minutes = max(0, int(self._session_total_s) // 60)
        off_peak_minutes = min(total_minutes, round(self._session_off_peak_s / 60))
        return SessionSplit(off_peak_minutes, total_minutes - off_peak_minutes)

    def _record_curve_sample(self, power_kw: float) -> None:
        """Feed one (delivered, power) reading into this car's charge curve.

        Records against energy delivered *before* this increment, so a bucket
        holds the power seen while at that fill level. Best-effort: learning a
        curve must never disturb the charge it is watching.
        """
        curves = getattr(self._coordinator, "vehicle_curves", None)
        if curves is None:
            return
        tracker = getattr(self._coordinator, "vehicle_tracker", None)
        vehicle = tracker.active_vehicle if tracker is not None else None
        try:
            curves.record(vehicle, self._session_energy_kwh, power_kw)
        except Exception as err:  # pragma: no cover  # noqa: BLE001 - accounting is best effort
            LOGGER.debug("Could not record a charge-curve sample: %s", err)

    def _set_decision(self, reason: str) -> None:
        self._last_decision_reason = reason

    def _clear_surplus_debug_state(self) -> None:
        self._last_raw_surplus_w = None
        self._last_available_surplus_w = None
        self._last_battery_discharge_over_limit_w = None
        self._last_target_current_a = None

    def _notify_state_listeners(self) -> None:
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                LOGGER.exception("Failed to update solar surplus listener state")


def _current_supported_by_surplus(
    available_currents: tuple[int, ...],
    effective_surplus_w: float,
    line_voltage: int,
    phases: int,
) -> int:
    return current_supported_by(
        effective_surplus_w, available_currents, line_voltage=line_voltage, phases=phases
    )
