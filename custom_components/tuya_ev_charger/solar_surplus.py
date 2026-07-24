from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .charge_gates import (
    DecisionReason,
    GateAction,
    GateContext,
    TimerState,
    Verdict,
    battery_hysteresis,
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
from .config_diagnosis import (
    ConfigProblem,
    DiagnosisInputs,
    GridSignDetector,
    static_problems,
)
from .const import (
    CHARGER_PROFILE_DEPOW_V2,
    CONF_DEPARTURE_ENERGY_KWH,
    CONF_DEPARTURE_TIME,
    CONF_MAX_HOUSE_POWER_W,
    CONF_MAX_INVERTER_POWER_W,
    CONF_OFF_PEAK_WINDOWS,
    CONF_SURPLUS_ADJUST_DOWN_COOLDOWN_S,
    CONF_SURPLUS_ADJUST_UP_COOLDOWN_S,
    CONF_SURPLUS_ALLOW_BATTERY_DISCHARGE_FOR_EV,
    CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_ENTITY_ID,
    CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_INVERTED,
    CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
    CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
    CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
    CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    CONF_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
    CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
    CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    CONF_SURPLUS_MODE_ENABLED,
    CONF_SURPLUS_SENSOR_ENTITY_ID,
    CONF_SURPLUS_SENSOR_INVERTED,
    CONF_SURPLUS_START_THRESHOLD_W,
    CONF_SURPLUS_STOP_THRESHOLD_W,
    CONF_TOTAL_LOAD_SENSOR_ENTITY_ID,
    DEFAULT_DEPARTURE_ENERGY_KWH,
    DEFAULT_DEPARTURE_TIME,
    DEFAULT_MAX_HOUSE_POWER_W,
    DEFAULT_MAX_INVERTER_POWER_W,
    DEFAULT_OFF_PEAK_WINDOWS,
    DEFAULT_SURPLUS_ADJUST_DOWN_COOLDOWN_S,
    DEFAULT_SURPLUS_ADJUST_UP_COOLDOWN_S,
    DEFAULT_SURPLUS_ALLOW_BATTERY_DISCHARGE_FOR_EV,
    DEFAULT_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_INVERTED,
    DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
    DEFAULT_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    DEFAULT_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
    DEFAULT_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    DEFAULT_SURPLUS_MODE_ENABLED,
    DEFAULT_SURPLUS_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_SENSOR_INVERTED,
    DEFAULT_SURPLUS_START_THRESHOLD_W,
    DEFAULT_SURPLUS_STOP_THRESHOLD_W,
    DEFAULT_TOTAL_LOAD_SENSOR_ENTITY_ID,
    DP_CHARGER_INFO,
    DP_CURRENT_TARGET,
    DP_DO_CHARGE,
    DP_METRICS,
    DP_WORK_STATE_DEBUG,
    MAX_DEPARTURE_ENERGY_KWH,
    MAX_MAX_HOUSE_POWER_W,
    MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MAX_SURPLUS_DELAY_S,
    MAX_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    MAX_SURPLUS_THRESHOLD_W,
    MIN_DEPARTURE_ENERGY_KWH,
    MIN_MAX_HOUSE_POWER_W,
    MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MIN_SURPLUS_DELAY_S,
    MIN_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    MIN_SURPLUS_THRESHOLD_W,
)
from .coordinator import TuyaEVChargerDataUpdateCoordinator
from .helpers import allowed_currents
from .surplus_decision import (
    DEFAULT_LINE_VOLTAGE_V,
    ForecastState,
    SurplusInputs,
    apply_forecast,
    cap_to_available_power,
    current_supported_by,
    headroom_for_car_w,
    raw_surplus_w,
)
from .tuya_ev_charger import EVMetrics, TuyaEVChargerClient

LOGGER = logging.getLogger(__name__)

# Internal tuning. Policy thresholds (start/stop/discharge) are configurable.
FIXED_LINE_VOLTAGE_V = 230
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
class SolarSurplusSettings:
    mode_enabled: bool
    grid_sensor_entity_id: str
    max_house_power_w: int
    max_inverter_power_w: int
    total_load_sensor_entity_id: str
    off_peak_windows: str
    departure_time: str
    departure_energy_kwh: int
    grid_sensor_inverted: bool
    curtailment_sensor_entity_id: str
    curtailment_sensor_inverted: bool
    battery_soc_sensor_entity_id: str
    battery_soc_high_threshold_pct: int
    battery_soc_low_threshold_pct: int
    battery_net_discharge_sensor_entity_id: str
    battery_net_discharge_sensor_inverted: bool
    allow_battery_discharge_for_ev: bool
    max_battery_discharge_for_ev_w: int
    start_threshold_w: int
    stop_threshold_w: int
    adjust_up_cooldown_s: int
    adjust_down_cooldown_s: int
    forecast_sensor_entity_id: str


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
        self._settings = _settings_from_entry(entry)
        self._unsub_sensor = None
        self._unsub_coordinator = None
        self._evaluation_task = None
        self._rerun_requested = False
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

        self._regulation_active = False
        self._last_decision_reason = "startup"
        self._last_raw_surplus_w: float | None = None
        self._last_available_surplus_w: float | None = None
        self._last_battery_discharge_over_limit_w: float | None = None
        self._last_target_current_a: int | None = None
        self._forecast_ema_surplus_w: float | None = None
        self._forecast_last_sample_ts: float | None = None
        self._battery_soc_hysteresis_enabled: bool | None = None
        self._last_decision_trace: dict[str, Any] = {}
        self._grid_sign = GridSignDetector()

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

        sensor_entities = self._tracked_sensor_entities()
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

    async def async_shutdown(self) -> None:
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
        dps = await self._client.async_get_raw_dps()
        if dps is None:
            return {"error": "Unable to read DPS payload from charger."}

        candidates: dict[str, list[str]] = {
            "metrics": [],
            "charger_info": [],
            "do_charge": [],
            "current_target": [],
            "work_state_debug": [],
        }
        for dp_id, value in dps.items():
            if _looks_like_metrics(value):
                candidates["metrics"].append(dp_id)
            if _looks_like_charger_info(value):
                candidates["charger_info"].append(dp_id)
            if _coerce_optional_bool(value) is not None:
                candidates["do_charge"].append(dp_id)
            if _looks_like_current_target(value):
                candidates["current_target"].append(dp_id)
            if _looks_like_state_debug(value):
                candidates["work_state_debug"].append(dp_id)

        known_depows = {
            DP_METRICS,
            DP_CHARGER_INFO,
            DP_DO_CHARGE,
            DP_CURRENT_TARGET,
            DP_WORK_STATE_DEBUG,
        }
        suggestion = (
            CHARGER_PROFILE_DEPOW_V2 if known_depows.issubset(set(dps.keys())) else "generic_v1"
        )

        return {
            "suggested_profile": suggestion,
            "detected_dp_ids": sorted(dps.keys()),
            "candidates": candidates,
            "sample_values": {key: dps[key] for key in sorted(dps.keys())[:15]},
        }

    def _tracked_sensor_entities(self) -> list[str]:
        sensor_entities: list[str] = []
        for entity_id in (
            self._settings.grid_sensor_entity_id,
            self._settings.total_load_sensor_entity_id,
            self._settings.curtailment_sensor_entity_id,
            self._settings.battery_soc_sensor_entity_id,
            self._settings.battery_net_discharge_sensor_entity_id,
            self._settings.forecast_sensor_entity_id,
        ):
            if entity_id and entity_id not in sensor_entities:
                sensor_entities.append(entity_id)
        return sensor_entities

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
        if self._evaluation_task is not None and not self._evaluation_task.done():
            self._rerun_requested = True
            return
        self._evaluation_task = self._hass.async_create_task(self._async_evaluation_loop(reason))

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

        is_charging = _is_charging(data)
        grid_power_for_energy = (
            self._read_grid_power_w() if self._settings.grid_sensor_entity_id else None
        )
        self._update_session_energy(now, data, is_charging, grid_power_for_energy)

        context = self._build_gate_context(now, data, is_charging)
        if context.grid_power_w is not None:
            self._grid_sign.observe(ev_power_w=_ev_power_w(data), grid_power_w=context.grid_power_w)
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
        protection_cap, cap_source = self._protection_cap(data, available_currents)
        if protection_cap is not None:
            available_currents = tuple(
                value for value in available_currents if value <= protection_cap
            )

        grid_power_w = self._read_grid_power_w() if self._settings.grid_sensor_entity_id else None

        battery_ready = self._is_battery_ready()
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
                available_currents, available_surplus_w, FIXED_LINE_VOLTAGE_V
            )
            min_current = min(available_currents) if available_currents else 0
            target_current = (
                max(min_current, max_supported_current)
                if available_currents and max_supported_current >= min_current
                else None
            )
            if update_state:
                self._last_target_current_a = target_current

        tariff_allowed: bool | None = None
        tariff_reason: DecisionReason | None = None
        tariff_is_deadline = False
        if not self._settings.mode_enabled:
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

        Combines the static checks with the one finding that needs measurements:
        a grid sensor whose sign convention is reversed.
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
        context = self._build_gate_context(now, data, _is_charging(data), update_state=False)
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
        if data is None or not _is_charging(data):
            return
        # A charge started outside surplus mode -- from the app, say -- is not
        # ours to interrupt when merely pausing.
        if verdict.only_stop_own_session and not self._session_active:
            return
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

        if data is not None and not _is_charging(data):
            if not await self._client.async_set_charge_enabled(True):
                return DecisionReason.FORCE_CHARGE_FAILED_START
            self._start_session(now)
            await self._coordinator.async_request_refresh()
            return DecisionReason.FORCE_CHARGE_START

        if not self._session_active:
            self._start_session(now)
        return reason

    def _session_limit_reason(self, now: float) -> str | None:
        if not self._session_active:
            return None

        if FIXED_MAX_SESSION_DURATION_MIN > 0 and self._session_started_ts is not None:
            duration_s = now - self._session_started_ts
            if duration_s >= FIXED_MAX_SESSION_DURATION_MIN * 60:
                return "session_limit_duration"

        if (
            FIXED_MAX_SESSION_ENERGY_KWH > 0
            and self._session_energy_kwh >= FIXED_MAX_SESSION_ENERGY_KWH
        ):
            return "session_limit_energy"

        end_minutes = _parse_end_time(FIXED_MAX_SESSION_END_TIME)
        if end_minutes is not None:
            now_dt = dt_util.now()
            now_minutes = now_dt.hour * 60 + now_dt.minute
            if now_minutes >= end_minutes:
                return "session_limit_end_time"

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
        discharge_over_limit_w = self._battery_discharge_over_limit_w()
        # Curtailment only counts in zero-injection setups, which is what having
        # the sensor configured signals.
        curtailed_w = (
            self._read_curtailment_power_w() if self._settings.curtailment_sensor_entity_id else 0.0
        )
        inputs = SurplusInputs(
            grid_power_w=grid_power_w,
            ev_power_w=_ev_power_w(data),
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
            forecast_w=self._read_sensor_power_w(self._settings.forecast_sensor_entity_id),
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

        Returns None when no off-peak window is configured, so the caller can
        skip the feature entirely rather than reason about an "always allowed"
        plan.
        """
        windows = parse_windows(self._settings.off_peak_windows)
        if not windows:
            return None

        target_kwh = float(self._settings.departure_energy_kwh)
        return plan_charge(
            PlanRequest(
                now=dt_util.now(),
                off_peak_windows=windows,
                departure=parse_clock(self._settings.departure_time),
                # Only what is still missing counts towards the deadline.
                energy_needed_kwh=max(0.0, target_kwh - self._session_energy_kwh),
                charge_power_kw=self._estimate_charge_power_kw(data, available_currents),
            )
        )

    def _estimate_charge_power_kw(
        self,
        data: EVMetrics,
        available_currents: tuple[int, ...],
    ) -> float:
        """Power to assume when estimating how long the charge will take.

        While charging, the measurement. Otherwise a deliberately pessimistic
        single-phase estimate: under-estimating power over-estimates the time
        needed and starts the charge earlier, which is the harmless direction to
        be wrong in when a departure deadline is at stake.
        """
        if data.total_power and data.total_power > 0:
            return float(data.total_power)
        if not available_currents:
            return 0.0
        return max(available_currents) * DEFAULT_LINE_VOLTAGE_V / 1000.0

    def _protection_cap(
        self,
        data: EVMetrics,
        available_currents: tuple[int, ...],
    ) -> tuple[int | None, str | None]:
        """The tighter of the load-balancing and inverter caps.

        Returns the binding cap and which limit produced it (``load_limit`` or
        ``inverter_limit``), for the decision reason. ``(None, None)`` when
        neither is configured or neither has a usable reading — a cap computed
        from a missing measurement would either stop a healthy charge or fail to
        protect, both worse than not capping.
        """
        candidates = (
            ("load_limit", self._load_limit_current(data, available_currents)),
            ("inverter_limit", self._inverter_limit_current(data, available_currents)),
        )
        binding_source: str | None = None
        binding_cap: int | None = None
        for source, cap in candidates:
            if cap is None:
                continue
            if binding_cap is None or cap < binding_cap:
                binding_cap, binding_source = cap, source
        return binding_cap, binding_source

    def _load_limit_current(
        self,
        data: EVMetrics,
        available_currents: tuple[int, ...],
    ) -> int | None:
        """Highest current that keeps the house under its subscribed limit.

        Returns None when load balancing is off or the grid reading is missing:
        capping blind would be worse than not capping, since a stale or absent
        measurement would either stop a healthy charge or fail to protect.
        """
        limit_w = self._settings.max_house_power_w
        if limit_w <= 0:
            return None
        grid_power_w = self._read_grid_power_w()
        if grid_power_w is None:
            return None

        headroom_w = headroom_for_car_w(
            grid_power_w=grid_power_w,
            ev_power_w=_ev_power_w(data),
            house_limit_w=float(limit_w),
        )
        return cap_to_available_power(available_currents, headroom_w)

    def _inverter_limit_current(
        self,
        data: EVMetrics,
        available_currents: tuple[int, ...],
    ) -> int | None:
        """Highest current that keeps total inverter output under its rating.

        The measurement point is what distinguishes this from load balancing.
        Load balancing reads the grid meter; on a hybrid inverter with the house
        on its backup output, the battery covers a sudden household draw so the
        grid meter stays near zero while the inverter is being overloaded past
        its rating. This must therefore read *total* load — household plus car —
        not the grid.

        `headroom_for_car_w` is reused unchanged: passing total load where it
        expects grid power gives ``limit - (total_load - ev_power)``, i.e. the
        rating minus what the house draws without the car, which is exactly the
        budget left for charging.

        Returns None, like load balancing, when disabled or the reading is
        missing: a cap off a stale total-load figure is worse than none.
        """
        limit_w = self._settings.max_inverter_power_w
        if limit_w <= 0:
            return None
        total_load_w = self._read_sensor_power_w(self._settings.total_load_sensor_entity_id)
        if total_load_w is None:
            return None

        headroom_w = headroom_for_car_w(
            grid_power_w=total_load_w,
            ev_power_w=_ev_power_w(data),
            house_limit_w=float(limit_w),
        )
        return cap_to_available_power(available_currents, headroom_w)

    def _read_grid_power_w(self) -> float | None:
        value = self._read_sensor_power_w(self._settings.grid_sensor_entity_id)
        if value is None:
            return None
        if self._settings.grid_sensor_inverted:
            return -value
        return value

    def _read_curtailment_power_w(self) -> float:
        if not self._settings.curtailment_sensor_entity_id:
            return 0.0
        value = self._read_sensor_power_w(self._settings.curtailment_sensor_entity_id)
        if value is None:
            return 0.0
        if self._settings.curtailment_sensor_inverted:
            value = -value
        return max(0.0, value)

    def _read_battery_net_discharge_w(self) -> float | None:
        if not self._settings.battery_net_discharge_sensor_entity_id:
            return None
        value = self._read_sensor_power_w(self._settings.battery_net_discharge_sensor_entity_id)
        if value is None:
            return None
        if self._settings.battery_net_discharge_sensor_inverted:
            value = -value
        return max(0.0, value)

    def _battery_discharge_over_limit_w(self) -> float:
        measured_net_discharge_w = self._read_battery_net_discharge_w()
        if measured_net_discharge_w is None:
            return 0.0
        return max(0.0, measured_net_discharge_w - self._allowed_battery_discharge_w())

    def _allowed_battery_discharge_w(self) -> float:
        if not self._settings.allow_battery_discharge_for_ev:
            return 0.0
        return max(0.0, float(self._settings.max_battery_discharge_for_ev_w))

    def _is_battery_ready(self) -> bool:
        if not self._settings.battery_soc_sensor_entity_id:
            return True

        soc = self._read_sensor_numeric(self._settings.battery_soc_sensor_entity_id)
        if soc is None:
            return False

        self._battery_soc_hysteresis_enabled = battery_hysteresis(
            soc,
            high=float(self._settings.battery_soc_high_threshold_pct),
            low=float(self._settings.battery_soc_low_threshold_pct),
            enabled=self._battery_soc_hysteresis_enabled,
        )
        return self._battery_soc_hysteresis_enabled

    def _read_sensor_power_w(self, entity_id: str) -> float | None:
        if not entity_id:
            return None
        value = self._read_sensor_numeric(entity_id)
        if value is None:
            return None
        state = self._hass.states.get(entity_id)
        if state is None:
            return None
        unit = str(state.attributes.get("unit_of_measurement", "")).strip().lower()
        if unit == "kw":
            return value * 1000.0
        return value

    def _read_sensor_numeric(self, entity_id: str) -> float | None:
        state = self._hass.states.get(entity_id)
        if state is None:
            return None
        raw = state.state
        if raw in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
            return None
        try:
            return float(str(raw).replace(",", "."))
        except ValueError:
            LOGGER.debug(
                "Unable to parse numeric sensor '%s' value '%s'.",
                entity_id,
                raw,
            )
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

        session_increment_kwh = (_ev_power_w(data) / 1000.0) * (elapsed_s / 3600.0)
        self._session_energy_kwh += max(0.0, session_increment_kwh)

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


def _settings_from_entry(entry: ConfigEntry) -> SolarSurplusSettings:
    options = entry.options

    legacy_high = _option_int(
        options,
        CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        DEFAULT_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    )
    high = _option_int(
        options,
        CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
        legacy_high,
        MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    )
    if high <= MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT:
        high = MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT + 1
    low = _option_int(
        options,
        CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
        min(DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT, high),
        MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    )

    if low >= high:
        low = max(MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, high - 1)
    max_battery_discharge_for_ev_w = _option_int(
        options,
        CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        DEFAULT_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        MIN_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        MAX_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    )
    start_threshold_w = _option_int(
        options,
        CONF_SURPLUS_START_THRESHOLD_W,
        DEFAULT_SURPLUS_START_THRESHOLD_W,
        MIN_SURPLUS_THRESHOLD_W,
        MAX_SURPLUS_THRESHOLD_W,
    )
    stop_threshold_w = _option_int(
        options,
        CONF_SURPLUS_STOP_THRESHOLD_W,
        DEFAULT_SURPLUS_STOP_THRESHOLD_W,
        MIN_SURPLUS_THRESHOLD_W,
        MAX_SURPLUS_THRESHOLD_W,
    )
    if stop_threshold_w > start_threshold_w:
        stop_threshold_w = start_threshold_w
    adjust_up_cooldown_s = _option_int(
        options,
        CONF_SURPLUS_ADJUST_UP_COOLDOWN_S,
        DEFAULT_SURPLUS_ADJUST_UP_COOLDOWN_S,
        MIN_SURPLUS_DELAY_S,
        MAX_SURPLUS_DELAY_S,
    )
    adjust_down_cooldown_s = _option_int(
        options,
        CONF_SURPLUS_ADJUST_DOWN_COOLDOWN_S,
        DEFAULT_SURPLUS_ADJUST_DOWN_COOLDOWN_S,
        MIN_SURPLUS_DELAY_S,
        MAX_SURPLUS_DELAY_S,
    )

    return SolarSurplusSettings(
        mode_enabled=_option_bool(
            options,
            CONF_SURPLUS_MODE_ENABLED,
            DEFAULT_SURPLUS_MODE_ENABLED,
        ),
        off_peak_windows=_option_str(options, CONF_OFF_PEAK_WINDOWS, DEFAULT_OFF_PEAK_WINDOWS),
        departure_time=_option_str(options, CONF_DEPARTURE_TIME, DEFAULT_DEPARTURE_TIME),
        departure_energy_kwh=_option_int(
            options,
            CONF_DEPARTURE_ENERGY_KWH,
            DEFAULT_DEPARTURE_ENERGY_KWH,
            MIN_DEPARTURE_ENERGY_KWH,
            MAX_DEPARTURE_ENERGY_KWH,
        ),
        max_house_power_w=_option_int(
            options,
            CONF_MAX_HOUSE_POWER_W,
            DEFAULT_MAX_HOUSE_POWER_W,
            MIN_MAX_HOUSE_POWER_W,
            MAX_MAX_HOUSE_POWER_W,
        ),
        max_inverter_power_w=_option_int(
            options,
            CONF_MAX_INVERTER_POWER_W,
            DEFAULT_MAX_INVERTER_POWER_W,
            MIN_MAX_HOUSE_POWER_W,
            MAX_MAX_HOUSE_POWER_W,
        ),
        total_load_sensor_entity_id=_option_str(
            options,
            CONF_TOTAL_LOAD_SENSOR_ENTITY_ID,
            DEFAULT_TOTAL_LOAD_SENSOR_ENTITY_ID,
        ),
        grid_sensor_entity_id=_option_str(
            options,
            CONF_SURPLUS_SENSOR_ENTITY_ID,
            DEFAULT_SURPLUS_SENSOR_ENTITY_ID,
        ),
        grid_sensor_inverted=_option_bool(
            options,
            CONF_SURPLUS_SENSOR_INVERTED,
            DEFAULT_SURPLUS_SENSOR_INVERTED,
        ),
        curtailment_sensor_entity_id=_option_str(
            options,
            CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
            DEFAULT_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
        ),
        curtailment_sensor_inverted=_option_bool(
            options,
            CONF_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
            DEFAULT_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
        ),
        battery_soc_sensor_entity_id=_option_str(
            options,
            CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
            DEFAULT_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
        ),
        battery_soc_high_threshold_pct=high,
        battery_soc_low_threshold_pct=low,
        battery_net_discharge_sensor_entity_id=_option_str(
            options,
            CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_ENTITY_ID,
            DEFAULT_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_ENTITY_ID,
        ),
        battery_net_discharge_sensor_inverted=_option_bool(
            options,
            CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_INVERTED,
            DEFAULT_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_INVERTED,
        ),
        allow_battery_discharge_for_ev=_option_bool(
            options,
            CONF_SURPLUS_ALLOW_BATTERY_DISCHARGE_FOR_EV,
            DEFAULT_SURPLUS_ALLOW_BATTERY_DISCHARGE_FOR_EV,
        ),
        max_battery_discharge_for_ev_w=max_battery_discharge_for_ev_w,
        start_threshold_w=start_threshold_w,
        stop_threshold_w=stop_threshold_w,
        adjust_up_cooldown_s=adjust_up_cooldown_s,
        adjust_down_cooldown_s=adjust_down_cooldown_s,
        forecast_sensor_entity_id=_option_str(
            options,
            CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
            DEFAULT_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
        ),
    )


def _option_str(options: Any, key: str, default: str) -> str:
    value = options.get(key, default)
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "none":
        return ""
    return text


def _option_bool(options: Any, key: str, default: bool) -> bool:
    value = options.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "on", "yes"}:
            return True
        if lowered in {"0", "false", "off", "no"}:
            return False
    return bool(value)


def _option_int(
    options: Any,
    key: str,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    try:
        parsed = int(options.get(key, default))
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _is_charging(data: EVMetrics) -> bool:
    if data.do_charge is not None:
        return data.do_charge
    return data.work_state_debug == "WORKING"


def _ev_power_w(data: EVMetrics) -> float:
    """The car's total draw in watts, across all wired phases.

    `total_power` sums the phases (in kW); on a three-phase charger, reading L1
    alone would under-report by up to 3x, which in turn over-states the headroom
    every current cap is computed against — the protection would then allow the
    very overload it exists to prevent.
    """
    return max(0.0, (data.total_power or 0.0) * 1000.0)


def _current_supported_by_surplus(
    available_currents: tuple[int, ...],
    effective_surplus_w: float,
    line_voltage: int,
) -> int:
    return current_supported_by(effective_surplus_w, available_currents, line_voltage=line_voltage)


def _parse_end_time(raw: str) -> int | None:
    text = raw.strip()
    if not text:
        return None
    parts = text.split(":", 1)
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if hour < 0 or hour > 23:
        return None
    if minute < 0 or minute > 59:
        return None
    return hour * 60 + minute


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "on"}:
            return True
        if lowered in {"false", "0", "off"}:
            return False
    return None


def _coerce_optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _looks_like_metrics(value: Any) -> bool:
    if isinstance(value, dict) and "L1" in value:
        return True
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return False
        if isinstance(payload, dict) and "L1" in payload:
            return True
    return False


def _looks_like_charger_info(value: Any) -> bool:
    if isinstance(value, dict):
        keys = {str(key).lower() for key in value}
        if {"model", "manufacturer"}.intersection(keys):
            return True
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return False
        if isinstance(payload, dict):
            keys = {str(key).lower() for key in payload}
            return bool({"model", "manufacturer"}.intersection(keys))
    return False


def _looks_like_current_target(value: Any) -> bool:
    parsed = _coerce_optional_int(value)
    if parsed is None:
        return False
    return 6 <= parsed <= 32


def _looks_like_state_debug(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().upper()
    return normalized in {"STANDBY", "WORKING", "DONE", "FAULT"}
