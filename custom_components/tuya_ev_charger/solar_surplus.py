from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from math import ceil
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    CHARGER_PROFILE_DEPOW_V2,
    CONF_SURPLUS_ADJUST_COOLDOWN_S,
    CONF_SURPLUS_ADJUST_DOWN_COOLDOWN_S,
    CONF_SURPLUS_ADJUST_UP_COOLDOWN_S,
    CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
    CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    CONF_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
    CONF_SURPLUS_DEPARTURE_MODE_ENABLED,
    CONF_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
    CONF_SURPLUS_DEPARTURE_TIME,
    CONF_SURPLUS_DRY_RUN_CONTINUOUS_ENABLED,
    CONF_SURPLUS_FORECAST_DROP_GUARD_W,
    CONF_SURPLUS_FORECAST_MODE_ENABLED,
    CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
    CONF_SURPLUS_FORECAST_SMOOTHING_S,
    CONF_SURPLUS_FORECAST_WEIGHT_PCT,
    CONF_SURPLUS_LINE_VOLTAGE,
    CONF_SURPLUS_MAX_SESSION_DURATION_MIN,
    CONF_SURPLUS_MAX_SESSION_END_TIME,
    CONF_SURPLUS_MAX_SESSION_ENERGY_KWH,
    CONF_SURPLUS_MIN_RUN_TIME_S,
    CONF_SURPLUS_MODE,
    CONF_SURPLUS_MODE_ENABLED,
    CONF_SURPLUS_RAMP_STEP_A,
    CONF_SURPLUS_SENSOR_ENTITY_ID,
    CONF_SURPLUS_SENSOR_INVERTED,
    CONF_SURPLUS_START_DELAY_S,
    CONF_SURPLUS_START_THRESHOLD_W,
    CONF_SURPLUS_STOP_DELAY_S,
    CONF_SURPLUS_STOP_THRESHOLD_W,
    CONF_SURPLUS_TARGET_OFFSET_W,
    CONF_TARIFF_ALLOWED_VALUES,
    CONF_TARIFF_MAX_PRICE_EUR_KWH,
    CONF_TARIFF_MODE,
    CONF_TARIFF_PRICE_SENSOR_ENTITY_ID,
    CONF_TARIFF_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_ADJUST_COOLDOWN_S,
    DEFAULT_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    DEFAULT_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
    DEFAULT_SURPLUS_DEPARTURE_MODE_ENABLED,
    DEFAULT_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
    DEFAULT_SURPLUS_DEPARTURE_TIME,
    DEFAULT_SURPLUS_DRY_RUN_CONTINUOUS_ENABLED,
    DEFAULT_SURPLUS_FORECAST_DROP_GUARD_W,
    DEFAULT_SURPLUS_FORECAST_MODE_ENABLED,
    DEFAULT_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_FORECAST_SMOOTHING_S,
    DEFAULT_SURPLUS_FORECAST_WEIGHT_PCT,
    DEFAULT_SURPLUS_LINE_VOLTAGE,
    DEFAULT_SURPLUS_MAX_SESSION_DURATION_MIN,
    DEFAULT_SURPLUS_MAX_SESSION_END_TIME,
    DEFAULT_SURPLUS_MAX_SESSION_ENERGY_KWH,
    DEFAULT_SURPLUS_MIN_RUN_TIME_S,
    DEFAULT_SURPLUS_MODE,
    DEFAULT_SURPLUS_MODE_ENABLED,
    DEFAULT_SURPLUS_RAMP_STEP_A,
    DEFAULT_SURPLUS_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_SENSOR_INVERTED,
    DEFAULT_SURPLUS_START_DELAY_S,
    DEFAULT_SURPLUS_START_THRESHOLD_W,
    DEFAULT_SURPLUS_STOP_DELAY_S,
    DEFAULT_SURPLUS_STOP_THRESHOLD_W,
    DEFAULT_SURPLUS_TARGET_OFFSET_W,
    DEFAULT_TARIFF_ALLOWED_VALUES,
    DEFAULT_TARIFF_MAX_PRICE_EUR_KWH,
    DEFAULT_TARIFF_MODE,
    DEFAULT_TARIFF_PRICE_SENSOR_ENTITY_ID,
    DEFAULT_TARIFF_SENSOR_ENTITY_ID,
    DP_CHARGER_INFO,
    DP_CURRENT_TARGET,
    DP_DO_CHARGE,
    DP_METRICS,
    DP_WORK_STATE_DEBUG,
    MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MAX_SURPLUS_DELAY_S,
    MAX_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
    MAX_SURPLUS_FORECAST_DROP_GUARD_W,
    MAX_SURPLUS_FORECAST_WEIGHT_PCT,
    MAX_SURPLUS_LINE_VOLTAGE,
    MAX_SURPLUS_RAMP_STEP_A,
    MAX_SURPLUS_SESSION_DURATION_MIN,
    MAX_SURPLUS_SESSION_ENERGY_KWH,
    MAX_SURPLUS_TARGET_OFFSET_W,
    MAX_SURPLUS_THRESHOLD_W,
    MAX_TARIFF_PRICE_EUR_KWH,
    MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MIN_SURPLUS_DELAY_S,
    MIN_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
    MIN_SURPLUS_END_TIME_LEN,
    MIN_SURPLUS_FORECAST_DROP_GUARD_W,
    MIN_SURPLUS_FORECAST_WEIGHT_PCT,
    MIN_SURPLUS_LINE_VOLTAGE,
    MIN_SURPLUS_RAMP_STEP_A,
    MIN_SURPLUS_SESSION_DURATION_MIN,
    MIN_SURPLUS_SESSION_ENERGY_KWH,
    MIN_SURPLUS_TARGET_OFFSET_W,
    MIN_SURPLUS_THRESHOLD_W,
    MIN_TARIFF_PRICE_EUR_KWH,
    SURPLUS_MODE_ZERO_INJECTION,
    SURPLUS_MODES,
    TARIFF_MODE_DISABLED,
    TARIFF_MODE_HPHC,
    TARIFF_MODE_SPOT,
    TARIFF_MODE_TEMPO,
    TARIFF_MODES,
)
from .coordinator import TuyaEVChargerDataUpdateCoordinator
from .helpers import allowed_currents
from .tuya_ev_charger import EVMetrics, TuyaEVChargerClient

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class SolarSurplusSettings:
    mode_enabled: bool
    mode: str
    grid_sensor_entity_id: str
    grid_sensor_inverted: bool
    curtailment_sensor_entity_id: str
    curtailment_sensor_inverted: bool
    battery_soc_sensor_entity_id: str
    battery_soc_threshold_pct: int
    line_voltage: int
    start_threshold_w: int
    stop_threshold_w: int
    target_offset_w: int
    start_delay_s: int
    stop_delay_s: int
    adjust_up_cooldown_s: int
    adjust_down_cooldown_s: int
    ramp_step_a: int
    forecast_mode_enabled: bool
    forecast_sensor_entity_id: str
    forecast_weight_pct: int
    forecast_smoothing_s: int
    forecast_drop_guard_w: int
    dry_run_continuous_enabled: bool
    departure_mode_enabled: bool
    departure_time: str
    departure_target_energy_kwh: float
    min_run_time_s: int
    max_session_duration_min: int
    max_session_energy_kwh: float
    max_session_end_time: str
    tariff_mode: str
    tariff_sensor_entity_id: str
    tariff_allowed_values: str
    tariff_price_sensor_entity_id: str
    tariff_max_price_eur_kwh: float


@dataclass(slots=True, frozen=True)
class SolarSurplusSnapshot:
    mode_enabled: bool
    regulation_active: bool
    paused: bool
    force_charge_active: bool
    available_surplus_w: float | None
    forecast_sensor_w: float | None
    forecast_blended_surplus_w: float | None
    target_current_a: int | None
    departure_required_current_a: int | None
    departure_remaining_energy_kwh: float
    tariff_allowed: bool | None
    tariff_reason: str
    last_decision_reason: str
    last_start_reason: str | None
    last_stop_reason: str | None
    dry_run_action: str
    dry_run_reason: str
    dry_run_target_current_a: int | None
    session_runtime_s: int
    session_energy_kwh: float
    energy_today_kwh: float
    energy_week_kwh: float
    surplus_efficiency_today_pct: float | None
    surplus_efficiency_week_pct: float | None


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

        self._start_candidate_since: float | None = None
        self._stop_candidate_since: float | None = None
        self._last_increase_action_ts: float = 0.0
        self._last_decrease_action_ts: float = 0.0

        self._force_charge_until_ts: float | None = None
        self._force_charge_current_a: int | None = None
        self._pause_until_ts: float | None = None

        self._session_active = False
        self._session_started_ts: float | None = None
        self._session_energy_kwh: float = 0.0
        self._last_energy_sample_ts: float | None = None
        self._departure_window_key: str | None = None
        self._departure_delivered_kwh: float = 0.0

        self._regulation_active = False
        self._last_available_surplus_w: float | None = None
        self._last_forecast_sensor_w: float | None = None
        self._last_forecast_blended_surplus_w: float | None = None
        self._last_target_current_a: int | None = None
        self._last_departure_required_current_a: int | None = None
        self._last_departure_remaining_energy_kwh: float = 0.0
        self._last_decision_reason = "startup"
        self._last_start_reason: str | None = None
        self._last_stop_reason: str | None = None
        self._last_dry_run_action = "idle"
        self._last_dry_run_reason = "startup"
        self._last_dry_run_target_current_a: int | None = None
        self._last_tariff_reason = "tariff_not_evaluated"
        self._tariff_allowed: bool | None = None
        self._forecast_ema_surplus_w: float | None = None
        self._forecast_last_sample_ts: float | None = None
        self._energy_day_key: str | None = None
        self._energy_week_key: str | None = None
        self._energy_today_kwh: float = 0.0
        self._energy_week_kwh: float = 0.0
        self._surplus_energy_today_kwh: float = 0.0
        self._surplus_energy_week_kwh: float = 0.0

    @property
    def snapshot(self) -> SolarSurplusSnapshot:
        self._roll_energy_buckets(dt_util.now())
        now = monotonic()
        return SolarSurplusSnapshot(
            mode_enabled=self._settings.mode_enabled,
            regulation_active=self._regulation_active,
            paused=self._is_pause_active(now),
            force_charge_active=self._is_force_charge_active(now),
            available_surplus_w=self._last_available_surplus_w,
            forecast_sensor_w=self._last_forecast_sensor_w,
            forecast_blended_surplus_w=self._last_forecast_blended_surplus_w,
            target_current_a=self._last_target_current_a,
            departure_required_current_a=self._last_departure_required_current_a,
            departure_remaining_energy_kwh=round(
                max(0.0, self._last_departure_remaining_energy_kwh),
                3,
            ),
            tariff_allowed=self._tariff_allowed,
            tariff_reason=self._last_tariff_reason,
            last_decision_reason=self._last_decision_reason,
            last_start_reason=self._last_start_reason,
            last_stop_reason=self._last_stop_reason,
            dry_run_action=self._last_dry_run_action,
            dry_run_reason=self._last_dry_run_reason,
            dry_run_target_current_a=self._last_dry_run_target_current_a,
            session_runtime_s=self._session_runtime_s(now),
            session_energy_kwh=round(self._session_energy_kwh, 3),
            energy_today_kwh=round(self._energy_today_kwh, 3),
            energy_week_kwh=round(self._energy_week_kwh, 3),
            surplus_efficiency_today_pct=_efficiency_pct(
                self._surplus_energy_today_kwh,
                self._energy_today_kwh,
            ),
            surplus_efficiency_week_pct=_efficiency_pct(
                self._surplus_energy_week_kwh,
                self._energy_week_kwh,
            ),
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
        if clamped_duration:
            self._set_decision("force_charge_requested")
        else:
            self._set_decision("force_charge_cleared")
        self._notify_state_listeners()
        self._schedule_evaluation("force_charge_service")

    async def async_pause_for(self, duration_s: int) -> None:
        now = monotonic()
        clamped_duration = max(0, int(duration_s))
        self._pause_until_ts = now + clamped_duration if clamped_duration else None
        if clamped_duration:
            self._set_decision("surplus_paused")
        else:
            self._set_decision("surplus_pause_cleared")
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

    async def async_dry_run_report(self) -> dict[str, Any]:
        now = monotonic()
        data = self._coordinator.data
        report: dict[str, Any] = {
            "entry_title": self._entry.title,
            "mode_enabled": self._settings.mode_enabled,
            "force_charge_active": self._is_force_charge_active(now),
            "pause_active": self._is_pause_active(now),
            "session_runtime_s": self._session_runtime_s(now),
            "session_energy_kwh": round(self._session_energy_kwh, 3),
        }
        report.update(
            self._build_dry_run_report(
                now=now,
                data=data,
                update_preview_state=False,
            )
        )
        return report

    def _tracked_sensor_entities(self) -> list[str]:
        sensor_entities: list[str] = []
        for entity_id in (
            self._settings.grid_sensor_entity_id,
            self._settings.curtailment_sensor_entity_id,
            self._settings.battery_soc_sensor_entity_id,
            self._settings.forecast_sensor_entity_id,
            self._settings.tariff_sensor_entity_id,
            self._settings.tariff_price_sensor_entity_id,
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
        if self._evaluation_task is not None and not self._evaluation_task.done():
            self._rerun_requested = True
            return
        self._evaluation_task = self._hass.async_create_task(
            self._async_evaluation_loop(reason)
        )

    async def _async_evaluation_loop(self, reason: str) -> None:
        current_reason = reason
        while True:
            try:
                await self._async_evaluate_once(current_reason)
            except Exception:
                LOGGER.exception(
                    "Solar surplus evaluation failed for %s", self._entry.title
                )
                self._set_decision("evaluation_exception")
                self._regulation_active = False
                self._notify_state_listeners()

            if not self._rerun_requested:
                break
            self._rerun_requested = False
            current_reason = "rerun"

    async def _async_evaluate_once(self, reason: str) -> None:
        _ = reason
        now = monotonic()
        data = self._coordinator.data
        self._build_dry_run_report(
            now=now,
            data=data,
            update_preview_state=self._settings.dry_run_continuous_enabled,
        )
        if data is None:
            self._set_decision("no_coordinator_data")
            self._regulation_active = False
            self._last_available_surplus_w = None
            self._last_target_current_a = None
            self._notify_state_listeners()
            return

        is_charging = _is_charging(data)
        grid_power_for_energy = (
            self._read_grid_power_w() if self._settings.grid_sensor_entity_id else None
        )
        self._update_session_energy(now, data, is_charging, grid_power_for_energy)

        available_currents = allowed_currents(data)
        if not available_currents:
            self._set_decision("no_allowed_currents")
            self._regulation_active = False
            self._last_target_current_a = None
            self._notify_state_listeners()
            return
        min_current = min(available_currents)

        force_charge_active = self._is_force_charge_active(now)
        pause_active = self._is_pause_active(now)

        tariff_allowed, tariff_reason = self._is_tariff_allowed()
        self._tariff_allowed = tariff_allowed
        self._last_tariff_reason = tariff_reason

        if force_charge_active:
            await self._async_apply_force_charge(now, data, available_currents, min_current)
            self._regulation_active = True
            self._notify_state_listeners()
            return

        if not self._settings.mode_enabled:
            self._set_decision("mode_disabled")
            self._regulation_active = False
            self._last_available_surplus_w = None
            self._last_target_current_a = None
            self._notify_state_listeners()
            return

        if pause_active:
            self._set_decision("surplus_paused_active")
            self._regulation_active = False
            self._last_target_current_a = None
            if is_charging and self._session_active:
                if await self._client.async_set_charge_enabled(False):
                    self._register_stop(now, "surplus_paused_active")
                    await self._coordinator.async_request_refresh()
            self._notify_state_listeners()
            return

        if not self._settings.grid_sensor_entity_id:
            self._set_decision("missing_grid_sensor")
            self._regulation_active = False
            self._last_available_surplus_w = None
            self._last_target_current_a = None
            self._notify_state_listeners()
            return

        grid_power_w = self._read_grid_power_w()
        if grid_power_w is None:
            self._set_decision("grid_sensor_unavailable")
            self._regulation_active = False
            self._last_available_surplus_w = None
            self._last_target_current_a = None
            self._start_candidate_since = None
            self._stop_candidate_since = None
            self._notify_state_listeners()
            return

        battery_ready = self._is_battery_threshold_met()
        available_surplus_w = self._available_surplus_w(
            data=data,
            grid_power_w=grid_power_w,
            battery_ready=battery_ready,
            now=now,
            update_state=True,
        )
        max_supported_current = _current_supported_by_surplus(
            available_currents,
            available_surplus_w,
            self._settings.line_voltage,
        )
        target_current_from_surplus = (
            max(min_current, max_supported_current) if max_supported_current >= min_current else None
        )
        (
            departure_required_current,
            departure_remaining_energy_kwh,
        ) = self._required_departure_current(
            available_currents=available_currents,
            min_current=min_current,
        )
        target_current = target_current_from_surplus
        if departure_required_current is not None:
            target_current = max(
                departure_required_current,
                target_current or 0,
            )
            if target_current < min_current:
                target_current = None

        self._last_departure_required_current_a = departure_required_current
        self._last_departure_remaining_energy_kwh = departure_remaining_energy_kwh
        self._last_available_surplus_w = available_surplus_w
        self._last_target_current_a = target_current

        if is_charging and not self._session_active:
            self._start_session(now, "detected_charging")

        if is_charging:
            self._start_candidate_since = None
            stop_reason = self._stop_reason(
                now=now,
                battery_ready=battery_ready,
                tariff_allowed=tariff_allowed,
                available_surplus_w=available_surplus_w,
                max_supported_current=max_supported_current,
                min_current=min_current,
                guaranteed_current_a=departure_required_current,
            )
            if stop_reason:
                if self._min_runtime_guard_applies(now, stop_reason):
                    self._stop_candidate_since = None
                    self._set_decision("min_runtime_guard")
                    self._regulation_active = True
                    self._notify_state_listeners()
                    return

                immediate_stop = stop_reason.startswith("session_limit_")
                if not immediate_stop:
                    if self._stop_candidate_since is None:
                        self._stop_candidate_since = now
                        self._set_decision("stop_delay_pending")
                        self._regulation_active = True
                        self._notify_state_listeners()
                        return
                    if now - self._stop_candidate_since < self._settings.stop_delay_s:
                        self._set_decision("stop_delay_pending")
                        self._regulation_active = True
                        self._notify_state_listeners()
                        return
                self._stop_candidate_since = None
                if await self._client.async_set_charge_enabled(False):
                    self._register_stop(now, stop_reason)
                    await self._coordinator.async_request_refresh()
                self._set_decision(stop_reason)
                self._regulation_active = False
                self._notify_state_listeners()
                return

            self._stop_candidate_since = None
            if target_current is None:
                self._set_decision("no_target_current")
                self._regulation_active = True
                self._notify_state_listeners()
                return

            current_target = data.current_target
            if current_target is None or current_target not in available_currents:
                current_target = min_current

            if target_current != current_target:
                increasing = target_current > current_target
                last_action_ts = (
                    self._last_increase_action_ts
                    if increasing
                    else self._last_decrease_action_ts
                )
                cooldown_s = (
                    self._settings.adjust_up_cooldown_s
                    if increasing
                    else self._settings.adjust_down_cooldown_s
                )
                if now - last_action_ts >= cooldown_s:
                    next_current = _ramp_current(
                        current=current_target,
                        target=target_current,
                        ramp_step=max(1, self._settings.ramp_step_a),
                        minimum=min_current,
                    )
                    if next_current != current_target:
                        if await self._client.async_set_charge_current(next_current):
                            if increasing:
                                self._last_increase_action_ts = now
                            else:
                                self._last_decrease_action_ts = now
                            await self._coordinator.async_request_refresh()
                            self._set_decision("adjust_current")
                    else:
                        self._set_decision("target_already_reached")
                else:
                    self._set_decision("adjust_cooldown_active")
            else:
                self._set_decision("holding_current")
            self._regulation_active = True
            self._notify_state_listeners()
            return

        if self._session_active:
            self._register_stop(now, "charging_stopped")

        self._stop_candidate_since = None
        if not tariff_allowed:
            self._start_candidate_since = None
            self._set_decision(tariff_reason)
            self._regulation_active = False
            self._notify_state_listeners()
            return
        guaranteed_start = departure_required_current is not None
        if not guaranteed_start:
            if not battery_ready:
                self._start_candidate_since = None
                self._set_decision("battery_soc_below_threshold")
                self._regulation_active = False
                self._notify_state_listeners()
                return
            if self._is_past_end_time():
                self._start_candidate_since = None
                self._set_decision("past_session_end_time")
                self._regulation_active = False
                self._notify_state_listeners()
                return
            if available_surplus_w < self._settings.start_threshold_w:
                self._start_candidate_since = None
                self._set_decision("below_start_threshold")
                self._regulation_active = False
                self._notify_state_listeners()
                return
            if max_supported_current < min_current:
                self._start_candidate_since = None
                self._set_decision("insufficient_surplus_current")
                self._regulation_active = False
                self._notify_state_listeners()
                return

            if self._start_candidate_since is None:
                self._start_candidate_since = now
                self._set_decision("start_delay_pending")
                self._regulation_active = False
                self._notify_state_listeners()
                return
            if now - self._start_candidate_since < self._settings.start_delay_s:
                self._set_decision("start_delay_pending")
                self._regulation_active = False
                self._notify_state_listeners()
                return
        else:
            self._start_candidate_since = None

        self._start_candidate_since = None
        startup_current = min_current
        if guaranteed_start and target_current is not None:
            startup_current = target_current
        if data.current_target != startup_current:
            if not await self._client.async_set_charge_current(startup_current):
                self._set_decision("failed_set_startup_current")
                self._regulation_active = False
                self._notify_state_listeners()
                return
        if await self._client.async_set_charge_enabled(True):
            self._last_increase_action_ts = now
            self._last_decrease_action_ts = now
            self._start_session(now, "departure_start" if guaranteed_start else "surplus_start")
            self._set_decision("departure_start" if guaranteed_start else "surplus_start")
            self._regulation_active = True
            await self._coordinator.async_request_refresh()
        else:
            self._set_decision("failed_enable_charge")
            self._regulation_active = False
        self._notify_state_listeners()

    async def _async_apply_force_charge(
        self,
        now: float,
        data: EVMetrics,
        available_currents: tuple[int, ...],
        min_current: int,
    ) -> None:
        desired_current = self._force_charge_current_a
        if desired_current is None or desired_current not in available_currents:
            desired_current = min_current

        if data.current_target != desired_current:
            if await self._client.async_set_charge_current(desired_current):
                self._last_increase_action_ts = now
                await self._coordinator.async_request_refresh()

        if not _is_charging(data):
            if await self._client.async_set_charge_enabled(True):
                self._start_session(now, "force_charge")
                await self._coordinator.async_request_refresh()
        elif not self._session_active:
            self._start_session(now, "force_charge")

        self._last_target_current_a = desired_current
        self._set_decision("force_charge_active")

    def _stop_reason(
        self,
        *,
        now: float,
        battery_ready: bool,
        tariff_allowed: bool,
        available_surplus_w: float,
        max_supported_current: int,
        min_current: int,
        guaranteed_current_a: int | None,
    ) -> str | None:
        limit_reason = self._session_limit_reason(now)
        if limit_reason:
            return limit_reason
        if not tariff_allowed:
            return self._last_tariff_reason or "tariff_blocked"
        if guaranteed_current_a is not None and guaranteed_current_a >= min_current:
            return None
        if not battery_ready:
            return "battery_soc_below_threshold"
        if available_surplus_w <= self._settings.stop_threshold_w:
            return "below_stop_threshold"
        if max_supported_current < min_current:
            return "insufficient_surplus_current"
        return None

    def _session_limit_reason(self, now: float) -> str | None:
        if not self._session_active:
            return None
        runtime_s = self._session_runtime_s(now)
        if (
            self._settings.max_session_duration_min > 0
            and runtime_s >= self._settings.max_session_duration_min * 60
        ):
            return "session_limit_duration"
        if (
            self._settings.max_session_energy_kwh > 0
            and self._session_energy_kwh >= self._settings.max_session_energy_kwh
        ):
            return "session_limit_energy"
        if self._is_past_end_time():
            return "session_limit_end_time"
        return None

    def _min_runtime_guard_applies(self, now: float, stop_reason: str) -> bool:
        if self._settings.min_run_time_s <= 0:
            return False
        if not self._session_active:
            return False
        if stop_reason.startswith("session_limit_"):
            return False
        if stop_reason in {"battery_soc_below_threshold", "surplus_paused_active"}:
            return False
        return self._session_runtime_s(now) < self._settings.min_run_time_s

    def _is_tariff_allowed(self) -> tuple[bool, str]:
        mode = self._settings.tariff_mode
        if mode == TARIFF_MODE_DISABLED:
            return True, "tariff_disabled"

        if mode == TARIFF_MODE_SPOT:
            if not self._settings.tariff_price_sensor_entity_id:
                return False, "tariff_price_sensor_missing"
            value = self._read_sensor_numeric(self._settings.tariff_price_sensor_entity_id)
            if value is None:
                return False, "tariff_price_unavailable"
            if value <= self._settings.tariff_max_price_eur_kwh:
                return True, "tariff_spot_allowed"
            return False, "tariff_spot_too_high"

        if not self._settings.tariff_sensor_entity_id:
            return False, "tariff_sensor_missing"
        state = self._read_sensor_text(self._settings.tariff_sensor_entity_id)
        if state is None:
            return False, "tariff_sensor_unavailable"
        normalized = state.strip().lower()
        allowed_values = _split_csv(self._settings.tariff_allowed_values)
        if not allowed_values:
            if mode == TARIFF_MODE_HPHC:
                allowed_values = _split_csv(DEFAULT_TARIFF_ALLOWED_VALUES)
            elif mode == TARIFF_MODE_TEMPO:
                allowed_values = ["bleu", "blue", "hc_bleu", "offpeak_blue"]
        if normalized in allowed_values:
            return True, "tariff_label_allowed"
        return False, "tariff_label_blocked"

    def _is_past_end_time(self) -> bool:
        end_time = _parse_end_time(self._settings.max_session_end_time)
        if end_time is None:
            return False
        now = dt_util.now()
        now_minutes = now.hour * 60 + now.minute
        return now_minutes >= end_time

    def _required_departure_current(
        self,
        *,
        available_currents: tuple[int, ...],
        min_current: int,
    ) -> tuple[int | None, float]:
        if not self._settings.departure_mode_enabled:
            self._departure_window_key = None
            self._departure_delivered_kwh = 0.0
            return None, 0.0
        departure_minutes = _parse_end_time(self._settings.departure_time)
        if departure_minutes is None:
            self._departure_window_key = None
            self._departure_delivered_kwh = 0.0
            return None, 0.0
        self._refresh_departure_window_state(departure_minutes)
        target_energy_kwh = max(0.0, self._settings.departure_target_energy_kwh)
        if target_energy_kwh <= 0.0:
            return None, 0.0

        remaining_energy_kwh = max(0.0, target_energy_kwh - self._departure_delivered_kwh)
        if remaining_energy_kwh <= 0.0:
            return None, 0.0

        hours_until_departure = _hours_until_next_departure(departure_minutes)
        if hours_until_departure <= 0.0:
            return None, remaining_energy_kwh

        required_power_w = (remaining_energy_kwh * 1000.0) / hours_until_departure
        if required_power_w <= 0.0 or self._settings.line_voltage <= 0:
            return None, remaining_energy_kwh

        required_current_a = ceil(required_power_w / float(self._settings.line_voltage))
        if required_current_a < min_current:
            # Not urgent yet: let surplus mode drive charging naturally.
            return None, remaining_energy_kwh

        higher_or_equal = [
            current for current in available_currents if current >= required_current_a
        ]
        if higher_or_equal:
            return min(higher_or_equal), remaining_energy_kwh
        return max(available_currents), remaining_energy_kwh

    def _refresh_departure_window_state(self, departure_minutes: int) -> None:
        now = dt_util.now()
        now_minutes = now.hour * 60 + now.minute
        target_date = now.date()
        if departure_minutes <= now_minutes:
            target_date = target_date + timedelta(days=1)

        window_key = f"{target_date.isoformat()}@{departure_minutes:04d}"
        if self._departure_window_key != window_key:
            self._departure_window_key = window_key
            self._departure_delivered_kwh = 0.0

    def _build_dry_run_report(
        self,
        *,
        now: float,
        data: EVMetrics | None,
        update_preview_state: bool,
    ) -> dict[str, Any]:
        report: dict[str, Any] = {}
        action = "idle"
        reason = "no_coordinator_data"
        target_current: int | None = None
        departure_required_current: int | None = None
        departure_remaining_energy_kwh = 0.0

        if data is None:
            report["status"] = "no_coordinator_data"
            self._update_dry_run_preview(
                enabled=update_preview_state,
                action=action,
                reason=reason,
                target_current=target_current,
                departure_required_current=departure_required_current,
                departure_remaining_energy_kwh=departure_remaining_energy_kwh,
            )
            return report

        available_currents = allowed_currents(data)
        if not available_currents:
            action = "idle"
            reason = "no_allowed_currents"
            report["status"] = "no_allowed_currents"
            self._update_dry_run_preview(
                enabled=update_preview_state,
                action=action,
                reason=reason,
                target_current=target_current,
                departure_required_current=departure_required_current,
                departure_remaining_energy_kwh=departure_remaining_energy_kwh,
            )
            return report

        min_current = min(available_currents)
        force_charge_active = self._is_force_charge_active(now)
        pause_active = self._is_pause_active(now)
        grid_power_w = self._read_grid_power_w()
        battery_ready = self._is_battery_threshold_met()
        tariff_allowed, tariff_reason = self._is_tariff_allowed()
        is_charging = _is_charging(data)

        report["is_charging"] = is_charging
        report["grid_power_w"] = grid_power_w
        report["battery_ready"] = battery_ready
        report["tariff_allowed"] = tariff_allowed
        report["tariff_reason"] = tariff_reason

        if force_charge_active:
            action = "force_charge"
            reason = "force_charge_active"
            target_current = self._force_charge_current_a or min_current
            report["status"] = "force_charge_active"
            self._update_dry_run_preview(
                enabled=update_preview_state,
                action=action,
                reason=reason,
                target_current=target_current,
                departure_required_current=departure_required_current,
                departure_remaining_energy_kwh=departure_remaining_energy_kwh,
            )
            report["predicted_action"] = action
            report["predicted_reason"] = reason
            report["target_current_a"] = target_current
            return report

        if not self._settings.mode_enabled:
            action = "stay_stopped"
            reason = "mode_disabled"
            report["status"] = "mode_disabled"
            self._update_dry_run_preview(
                enabled=update_preview_state,
                action=action,
                reason=reason,
                target_current=target_current,
                departure_required_current=departure_required_current,
                departure_remaining_energy_kwh=departure_remaining_energy_kwh,
            )
            report["predicted_action"] = action
            report["predicted_reason"] = reason
            return report

        if pause_active:
            action = "pause"
            reason = "surplus_paused_active"
            report["status"] = "surplus_paused_active"
            self._update_dry_run_preview(
                enabled=update_preview_state,
                action=action,
                reason=reason,
                target_current=target_current,
                departure_required_current=departure_required_current,
                departure_remaining_energy_kwh=departure_remaining_energy_kwh,
            )
            report["predicted_action"] = action
            report["predicted_reason"] = reason
            return report

        if not self._settings.grid_sensor_entity_id:
            action = "stay_stopped"
            reason = "missing_grid_sensor"
            report["status"] = "missing_grid_sensor"
            self._update_dry_run_preview(
                enabled=update_preview_state,
                action=action,
                reason=reason,
                target_current=target_current,
                departure_required_current=departure_required_current,
                departure_remaining_energy_kwh=departure_remaining_energy_kwh,
            )
            report["predicted_action"] = action
            report["predicted_reason"] = reason
            return report

        if grid_power_w is None:
            action = "stay_stopped"
            reason = "grid_sensor_unavailable"
            report["status"] = "grid_sensor_unavailable"
            self._update_dry_run_preview(
                enabled=update_preview_state,
                action=action,
                reason=reason,
                target_current=target_current,
                departure_required_current=departure_required_current,
                departure_remaining_energy_kwh=departure_remaining_energy_kwh,
            )
            report["predicted_action"] = action
            report["predicted_reason"] = reason
            return report

        available_surplus_w = self._available_surplus_w(
            data=data,
            grid_power_w=grid_power_w,
            battery_ready=battery_ready,
            now=now,
            update_state=False,
        )
        max_supported_current = _current_supported_by_surplus(
            available_currents,
            available_surplus_w,
            self._settings.line_voltage,
        )
        target_current_from_surplus = (
            max(min_current, max_supported_current) if max_supported_current >= min_current else None
        )
        (
            departure_required_current,
            departure_remaining_energy_kwh,
        ) = self._required_departure_current(
            available_currents=available_currents,
            min_current=min_current,
        )
        target_current = target_current_from_surplus
        if departure_required_current is not None:
            target_current = max(departure_required_current, target_current or 0)
            if target_current < min_current:
                target_current = None

        report["status"] = "ok"
        report["available_surplus_w"] = round(available_surplus_w, 1)
        report["target_current_a"] = target_current
        report["current_target_a"] = data.current_target
        report["start_threshold_w"] = self._settings.start_threshold_w
        report["stop_threshold_w"] = self._settings.stop_threshold_w
        report["forecast_sensor_w"] = self._last_forecast_sensor_w
        report["forecast_blended_surplus_w"] = self._last_forecast_blended_surplus_w
        report["departure_required_current_a"] = departure_required_current
        report["departure_remaining_energy_kwh"] = round(
            departure_remaining_energy_kwh,
            3,
        )

        if is_charging:
            stop_reason = self._stop_reason(
                now=now,
                battery_ready=battery_ready,
                tariff_allowed=tariff_allowed,
                available_surplus_w=available_surplus_w,
                max_supported_current=max_supported_current,
                min_current=min_current,
                guaranteed_current_a=departure_required_current,
            )
            if stop_reason:
                action = "stop"
                reason = stop_reason
            elif target_current is None:
                action = "hold"
                reason = "no_target_current"
            elif data.current_target is None or data.current_target != target_current:
                action = "adjust"
                reason = "target_mismatch"
            else:
                action = "hold"
                reason = "holding_current"
        else:
            guaranteed_start = departure_required_current is not None
            if not tariff_allowed:
                action = "stay_stopped"
                reason = tariff_reason
            elif guaranteed_start:
                action = "start"
                reason = "departure_start"
            elif not battery_ready:
                action = "stay_stopped"
                reason = "battery_soc_below_threshold"
            elif self._is_past_end_time():
                action = "stay_stopped"
                reason = "past_session_end_time"
            elif available_surplus_w < self._settings.start_threshold_w:
                action = "stay_stopped"
                reason = "below_start_threshold"
            elif max_supported_current < min_current:
                action = "stay_stopped"
                reason = "insufficient_surplus_current"
            else:
                action = "start"
                reason = "surplus_start"

        report["predicted_action"] = action
        report["predicted_reason"] = reason
        self._update_dry_run_preview(
            enabled=update_preview_state,
            action=action,
            reason=reason,
            target_current=target_current,
            departure_required_current=departure_required_current,
            departure_remaining_energy_kwh=departure_remaining_energy_kwh,
        )
        return report

    def _update_dry_run_preview(
        self,
        *,
        enabled: bool,
        action: str,
        reason: str,
        target_current: int | None,
        departure_required_current: int | None,
        departure_remaining_energy_kwh: float,
    ) -> None:
        if not enabled:
            return
        self._last_dry_run_action = action
        self._last_dry_run_reason = reason
        self._last_dry_run_target_current_a = target_current
        self._last_departure_required_current_a = departure_required_current
        self._last_departure_remaining_energy_kwh = max(
            0.0,
            departure_remaining_energy_kwh,
        )

    def _available_surplus_w(
        self,
        data: EVMetrics,
        grid_power_w: float,
        battery_ready: bool,
        now: float,
        update_state: bool,
    ) -> float:
        raw_surplus_w = self._raw_surplus_w(
            data=data,
            grid_power_w=grid_power_w,
            battery_ready=battery_ready,
        )
        return self._apply_forecast_model(
            now=now,
            raw_surplus_w=raw_surplus_w,
            update_state=update_state,
        )

    def _raw_surplus_w(
        self,
        data: EVMetrics,
        grid_power_w: float,
        battery_ready: bool,
    ) -> float:
        # Positive grid power means import. Reconstruct natural surplus by
        # adding EV consumption back into the grid balance.
        ev_power_w = _ev_power_w(data)
        reconstructed_surplus_w = ev_power_w - grid_power_w

        if self._settings.mode == SURPLUS_MODE_ZERO_INJECTION and battery_ready:
            reconstructed_surplus_w += self._read_curtailment_power_w()

        return reconstructed_surplus_w + self._settings.target_offset_w

    def _apply_forecast_model(
        self,
        *,
        now: float,
        raw_surplus_w: float,
        update_state: bool,
    ) -> float:
        if not self._settings.forecast_mode_enabled:
            if update_state:
                self._last_forecast_sensor_w = None
                self._last_forecast_blended_surplus_w = raw_surplus_w
                self._forecast_ema_surplus_w = raw_surplus_w
                self._forecast_last_sample_ts = now
            return raw_surplus_w

        forecast_sensor_w = self._read_sensor_power_w(self._settings.forecast_sensor_entity_id)
        weight = max(0.0, min(1.0, self._settings.forecast_weight_pct / 100.0))
        blended_surplus_w = raw_surplus_w
        if forecast_sensor_w is not None:
            blended_surplus_w = (raw_surplus_w * (1.0 - weight)) + (
                forecast_sensor_w * weight
            )

        ema_value = self._forecast_ema_surplus_w
        if ema_value is None:
            ema_value = blended_surplus_w
        else:
            elapsed_s = 0.0
            if self._forecast_last_sample_ts is not None:
                elapsed_s = max(0.0, now - self._forecast_last_sample_ts)
            smoothing_s = max(1.0, float(self._settings.forecast_smoothing_s))
            alpha = min(1.0, elapsed_s / smoothing_s) if elapsed_s > 0 else 0.0
            ema_value = ema_value + (blended_surplus_w - ema_value) * alpha

        effective_surplus_w = max(
            blended_surplus_w,
            ema_value - float(self._settings.forecast_drop_guard_w),
        )

        if update_state:
            self._last_forecast_sensor_w = forecast_sensor_w
            self._last_forecast_blended_surplus_w = blended_surplus_w
            self._forecast_ema_surplus_w = ema_value
            self._forecast_last_sample_ts = now
        return effective_surplus_w

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

    def _is_battery_threshold_met(self) -> bool:
        if not self._settings.battery_soc_sensor_entity_id:
            return True
        soc = self._read_sensor_numeric(self._settings.battery_soc_sensor_entity_id)
        if soc is None:
            return False
        return soc >= float(self._settings.battery_soc_threshold_pct)

    def _read_sensor_power_w(self, entity_id: str) -> float | None:
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

    def _read_sensor_text(self, entity_id: str) -> str | None:
        state = self._hass.states.get(entity_id)
        if state is None:
            return None
        raw = state.state
        if raw in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
            return None
        return str(raw)

    def _is_force_charge_active(self, now: float) -> bool:
        return self._force_charge_until_ts is not None and now < self._force_charge_until_ts

    def _is_pause_active(self, now: float) -> bool:
        return self._pause_until_ts is not None and now < self._pause_until_ts

    def _start_session(self, now: float, reason: str) -> None:
        if self._session_active:
            return
        self._session_active = True
        self._session_started_ts = now
        self._session_energy_kwh = 0.0
        self._last_energy_sample_ts = now
        self._last_start_reason = reason

    def _register_stop(self, now: float, reason: str) -> None:
        self._session_active = False
        self._session_started_ts = None
        self._last_energy_sample_ts = None
        self._last_stop_reason = reason
        self._last_decrease_action_ts = now

    def _session_runtime_s(self, now: float) -> int:
        if not self._session_active or self._session_started_ts is None:
            return 0
        return max(0, int(now - self._session_started_ts))

    def _update_session_energy(
        self,
        now: float,
        data: EVMetrics,
        is_charging: bool,
        grid_power_w: float | None,
    ) -> None:
        if not self._session_active:
            self._last_energy_sample_ts = now if is_charging else None
            return
        if not is_charging:
            self._last_energy_sample_ts = None
            return
        if self._last_energy_sample_ts is None:
            self._last_energy_sample_ts = now
            return

        elapsed_h = max(0.0, now - self._last_energy_sample_ts) / 3600.0
        self._last_energy_sample_ts = now
        if elapsed_h <= 0.0:
            return
        session_increment_kwh = max(0.0, data.power_l1) * elapsed_h
        if session_increment_kwh <= 0.0:
            return
        self._session_energy_kwh += session_increment_kwh

        now_dt = dt_util.now()
        self._roll_energy_buckets(now_dt)
        self._energy_today_kwh += session_increment_kwh
        self._energy_week_kwh += session_increment_kwh

        surplus_increment_kwh = 0.0
        if grid_power_w is not None:
            ev_power_w = _ev_power_w(data)
            grid_import_w = max(0.0, grid_power_w)
            locally_supplied_w = max(0.0, ev_power_w - grid_import_w)
            locally_supplied_w = min(ev_power_w, locally_supplied_w)
            surplus_increment_kwh = (locally_supplied_w / 1000.0) * elapsed_h

        self._surplus_energy_today_kwh += surplus_increment_kwh
        self._surplus_energy_week_kwh += surplus_increment_kwh

        if self._settings.departure_mode_enabled:
            departure_minutes = _parse_end_time(self._settings.departure_time)
            if departure_minutes is not None:
                self._refresh_departure_window_state(departure_minutes)
                self._departure_delivered_kwh += session_increment_kwh

    def _roll_energy_buckets(self, now_dt: Any) -> None:
        day_key = now_dt.date().isoformat()
        iso = now_dt.isocalendar()
        week_key = f"{iso.year}-W{iso.week:02d}"

        if self._energy_day_key != day_key:
            self._energy_day_key = day_key
            self._energy_today_kwh = 0.0
            self._surplus_energy_today_kwh = 0.0
        if self._energy_week_key != week_key:
            self._energy_week_key = week_key
            self._energy_week_kwh = 0.0
            self._surplus_energy_week_kwh = 0.0

    def _set_decision(self, reason: str) -> None:
        self._last_decision_reason = reason

    def _notify_state_listeners(self) -> None:
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                LOGGER.exception("Failed to update solar surplus listener state")


def _settings_from_entry(entry: ConfigEntry) -> SolarSurplusSettings:
    options = entry.options
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
    if start_threshold_w <= stop_threshold_w:
        if stop_threshold_w >= MAX_SURPLUS_THRESHOLD_W:
            stop_threshold_w = MAX_SURPLUS_THRESHOLD_W - 1
        start_threshold_w = stop_threshold_w + 1

    legacy_adjust_cooldown_s = _option_int(
        options,
        CONF_SURPLUS_ADJUST_COOLDOWN_S,
        DEFAULT_SURPLUS_ADJUST_COOLDOWN_S,
        MIN_SURPLUS_DELAY_S,
        MAX_SURPLUS_DELAY_S,
    )

    return SolarSurplusSettings(
        mode_enabled=_option_bool(
            options, CONF_SURPLUS_MODE_ENABLED, DEFAULT_SURPLUS_MODE_ENABLED
        ),
        mode=_option_choice(
            options,
            CONF_SURPLUS_MODE,
            DEFAULT_SURPLUS_MODE,
            SURPLUS_MODES,
        ),
        grid_sensor_entity_id=_option_str(
            options, CONF_SURPLUS_SENSOR_ENTITY_ID, DEFAULT_SURPLUS_SENSOR_ENTITY_ID
        ),
        grid_sensor_inverted=_option_bool(
            options, CONF_SURPLUS_SENSOR_INVERTED, DEFAULT_SURPLUS_SENSOR_INVERTED
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
        battery_soc_threshold_pct=_option_int(
            options,
            CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
            DEFAULT_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
            MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
            MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        ),
        line_voltage=_option_int(
            options,
            CONF_SURPLUS_LINE_VOLTAGE,
            DEFAULT_SURPLUS_LINE_VOLTAGE,
            MIN_SURPLUS_LINE_VOLTAGE,
            MAX_SURPLUS_LINE_VOLTAGE,
        ),
        start_threshold_w=start_threshold_w,
        stop_threshold_w=stop_threshold_w,
        target_offset_w=_option_int(
            options,
            CONF_SURPLUS_TARGET_OFFSET_W,
            DEFAULT_SURPLUS_TARGET_OFFSET_W,
            MIN_SURPLUS_TARGET_OFFSET_W,
            MAX_SURPLUS_TARGET_OFFSET_W,
        ),
        start_delay_s=_option_int(
            options,
            CONF_SURPLUS_START_DELAY_S,
            DEFAULT_SURPLUS_START_DELAY_S,
            MIN_SURPLUS_DELAY_S,
            MAX_SURPLUS_DELAY_S,
        ),
        stop_delay_s=_option_int(
            options,
            CONF_SURPLUS_STOP_DELAY_S,
            DEFAULT_SURPLUS_STOP_DELAY_S,
            MIN_SURPLUS_DELAY_S,
            MAX_SURPLUS_DELAY_S,
        ),
        adjust_up_cooldown_s=_option_int(
            options,
            CONF_SURPLUS_ADJUST_UP_COOLDOWN_S,
            legacy_adjust_cooldown_s,
            MIN_SURPLUS_DELAY_S,
            MAX_SURPLUS_DELAY_S,
        ),
        adjust_down_cooldown_s=_option_int(
            options,
            CONF_SURPLUS_ADJUST_DOWN_COOLDOWN_S,
            legacy_adjust_cooldown_s,
            MIN_SURPLUS_DELAY_S,
            MAX_SURPLUS_DELAY_S,
        ),
        ramp_step_a=_option_int(
            options,
            CONF_SURPLUS_RAMP_STEP_A,
            DEFAULT_SURPLUS_RAMP_STEP_A,
            MIN_SURPLUS_RAMP_STEP_A,
            MAX_SURPLUS_RAMP_STEP_A,
        ),
        forecast_mode_enabled=_option_bool(
            options,
            CONF_SURPLUS_FORECAST_MODE_ENABLED,
            DEFAULT_SURPLUS_FORECAST_MODE_ENABLED,
        ),
        forecast_sensor_entity_id=_option_str(
            options,
            CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
            DEFAULT_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
        ),
        forecast_weight_pct=_option_int(
            options,
            CONF_SURPLUS_FORECAST_WEIGHT_PCT,
            DEFAULT_SURPLUS_FORECAST_WEIGHT_PCT,
            MIN_SURPLUS_FORECAST_WEIGHT_PCT,
            MAX_SURPLUS_FORECAST_WEIGHT_PCT,
        ),
        forecast_smoothing_s=_option_int(
            options,
            CONF_SURPLUS_FORECAST_SMOOTHING_S,
            DEFAULT_SURPLUS_FORECAST_SMOOTHING_S,
            MIN_SURPLUS_DELAY_S,
            MAX_SURPLUS_DELAY_S,
        ),
        forecast_drop_guard_w=_option_int(
            options,
            CONF_SURPLUS_FORECAST_DROP_GUARD_W,
            DEFAULT_SURPLUS_FORECAST_DROP_GUARD_W,
            MIN_SURPLUS_FORECAST_DROP_GUARD_W,
            MAX_SURPLUS_FORECAST_DROP_GUARD_W,
        ),
        dry_run_continuous_enabled=_option_bool(
            options,
            CONF_SURPLUS_DRY_RUN_CONTINUOUS_ENABLED,
            DEFAULT_SURPLUS_DRY_RUN_CONTINUOUS_ENABLED,
        ),
        departure_mode_enabled=_option_bool(
            options,
            CONF_SURPLUS_DEPARTURE_MODE_ENABLED,
            DEFAULT_SURPLUS_DEPARTURE_MODE_ENABLED,
        ),
        departure_time=_option_end_time(
            options,
            CONF_SURPLUS_DEPARTURE_TIME,
            DEFAULT_SURPLUS_DEPARTURE_TIME,
        ),
        departure_target_energy_kwh=_option_float(
            options,
            CONF_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
            DEFAULT_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
            MIN_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
            MAX_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
        ),
        min_run_time_s=_option_int(
            options,
            CONF_SURPLUS_MIN_RUN_TIME_S,
            DEFAULT_SURPLUS_MIN_RUN_TIME_S,
            MIN_SURPLUS_DELAY_S,
            MAX_SURPLUS_DELAY_S,
        ),
        max_session_duration_min=_option_int(
            options,
            CONF_SURPLUS_MAX_SESSION_DURATION_MIN,
            DEFAULT_SURPLUS_MAX_SESSION_DURATION_MIN,
            MIN_SURPLUS_SESSION_DURATION_MIN,
            MAX_SURPLUS_SESSION_DURATION_MIN,
        ),
        max_session_energy_kwh=_option_float(
            options,
            CONF_SURPLUS_MAX_SESSION_ENERGY_KWH,
            DEFAULT_SURPLUS_MAX_SESSION_ENERGY_KWH,
            MIN_SURPLUS_SESSION_ENERGY_KWH,
            MAX_SURPLUS_SESSION_ENERGY_KWH,
        ),
        max_session_end_time=_option_end_time(
            options,
            CONF_SURPLUS_MAX_SESSION_END_TIME,
            DEFAULT_SURPLUS_MAX_SESSION_END_TIME,
        ),
        tariff_mode=_option_choice(
            options,
            CONF_TARIFF_MODE,
            DEFAULT_TARIFF_MODE,
            TARIFF_MODES,
        ),
        tariff_sensor_entity_id=_option_str(
            options,
            CONF_TARIFF_SENSOR_ENTITY_ID,
            DEFAULT_TARIFF_SENSOR_ENTITY_ID,
        ),
        tariff_allowed_values=_option_str(
            options,
            CONF_TARIFF_ALLOWED_VALUES,
            DEFAULT_TARIFF_ALLOWED_VALUES,
        ),
        tariff_price_sensor_entity_id=_option_str(
            options,
            CONF_TARIFF_PRICE_SENSOR_ENTITY_ID,
            DEFAULT_TARIFF_PRICE_SENSOR_ENTITY_ID,
        ),
        tariff_max_price_eur_kwh=_option_float(
            options,
            CONF_TARIFF_MAX_PRICE_EUR_KWH,
            DEFAULT_TARIFF_MAX_PRICE_EUR_KWH,
            MIN_TARIFF_PRICE_EUR_KWH,
            MAX_TARIFF_PRICE_EUR_KWH,
        ),
    )


def _option_str(options: Any, key: str, default: str) -> str:
    value = options.get(key, default)
    if value is None:
        return ""
    return str(value).strip()


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


def _option_float(
    options: Any,
    key: str,
    default: float,
    min_value: float,
    max_value: float,
) -> float:
    try:
        parsed = float(options.get(key, default))
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _option_choice(
    options: Any,
    key: str,
    default: str,
    choices: tuple[str, ...],
) -> str:
    value = str(options.get(key, default)).strip().lower()
    if value in choices:
        return value
    return default


def _option_end_time(options: Any, key: str, default: str) -> str:
    value = str(options.get(key, default) or "").strip()
    if not value:
        return ""
    return value if _parse_end_time(value) is not None else ""


def _is_charging(data: EVMetrics) -> bool:
    if data.do_charge is not None:
        return data.do_charge
    return data.work_state_debug == "WORKING"


def _ev_power_w(data: EVMetrics) -> float:
    return max(0.0, data.power_l1 * 1000.0)


def _current_supported_by_surplus(
    available_currents: tuple[int, ...],
    effective_surplus_w: float,
    line_voltage: int,
) -> int:
    if line_voltage <= 0 or effective_surplus_w <= 0:
        return 0
    target_current = int(effective_surplus_w // line_voltage)
    candidates = [current for current in available_currents if current <= target_current]
    if not candidates:
        return 0
    return max(candidates)


def _ramp_current(current: int, target: int, ramp_step: int, minimum: int) -> int:
    if target == current:
        return current
    if target > current:
        return min(target, current + ramp_step)
    return max(minimum, max(target, current - ramp_step))


def _split_csv(raw: str) -> list[str]:
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _parse_end_time(raw: str) -> int | None:
    text = raw.strip()
    if len(text) < MIN_SURPLUS_END_TIME_LEN:
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


def _hours_until_next_departure(target_minutes: int) -> float:
    now = dt_util.now()
    now_minutes = now.hour * 60 + now.minute
    remaining_minutes = target_minutes - now_minutes
    if remaining_minutes <= 0:
        remaining_minutes += 24 * 60
    return max(0.0, remaining_minutes / 60.0)


def _efficiency_pct(surplus_energy_kwh: float, total_energy_kwh: float) -> float | None:
    if total_energy_kwh <= 0.0:
        return None
    ratio = max(0.0, min(1.0, surplus_energy_kwh / total_energy_kwh))
    return round(ratio * 100.0, 1)


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
    if not isinstance(value, str):
        return False
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    return "L1" in payload or "p" in payload or "t" in payload


def _looks_like_charger_info(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    marker_keys = {"model", "product", "manufacturer", "serial_number", "sn"}
    return any(key in payload for key in marker_keys)


def _looks_like_current_target(value: Any) -> bool:
    parsed = _coerce_optional_int(value)
    if parsed is None:
        return False
    return 5 <= parsed <= 40


def _looks_like_state_debug(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    if len(text) > 32:
        return False
    return text.upper() == text
