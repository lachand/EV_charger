from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_SURPLUS_ADJUST_COOLDOWN_S,
    CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    CONF_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
    CONF_SURPLUS_LINE_VOLTAGE,
    CONF_SURPLUS_MODE,
    CONF_SURPLUS_MODE_ENABLED,
    CONF_SURPLUS_SENSOR_ENTITY_ID,
    CONF_SURPLUS_SENSOR_INVERTED,
    CONF_SURPLUS_START_DELAY_S,
    CONF_SURPLUS_START_THRESHOLD_W,
    CONF_SURPLUS_STOP_DELAY_S,
    CONF_SURPLUS_STOP_THRESHOLD_W,
    CONF_SURPLUS_TARGET_OFFSET_W,
    DEFAULT_SURPLUS_ADJUST_COOLDOWN_S,
    DEFAULT_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
    DEFAULT_SURPLUS_LINE_VOLTAGE,
    DEFAULT_SURPLUS_MODE,
    DEFAULT_SURPLUS_MODE_ENABLED,
    DEFAULT_SURPLUS_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_SENSOR_INVERTED,
    DEFAULT_SURPLUS_START_DELAY_S,
    DEFAULT_SURPLUS_START_THRESHOLD_W,
    DEFAULT_SURPLUS_STOP_DELAY_S,
    DEFAULT_SURPLUS_STOP_THRESHOLD_W,
    DEFAULT_SURPLUS_TARGET_OFFSET_W,
    MAX_SURPLUS_DELAY_S,
    MAX_SURPLUS_LINE_VOLTAGE,
    MAX_SURPLUS_TARGET_OFFSET_W,
    MAX_SURPLUS_THRESHOLD_W,
    MIN_SURPLUS_DELAY_S,
    MIN_SURPLUS_LINE_VOLTAGE,
    MIN_SURPLUS_TARGET_OFFSET_W,
    MIN_SURPLUS_THRESHOLD_W,
    SURPLUS_MODE_CLASSIC,
    SURPLUS_MODE_ZERO_INJECTION,
    SURPLUS_MODES,
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
    line_voltage: int
    start_threshold_w: int
    stop_threshold_w: int
    target_offset_w: int
    start_delay_s: int
    stop_delay_s: int
    adjust_cooldown_s: int


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
        self._start_candidate_since: float | None = None
        self._stop_candidate_since: float | None = None
        self._last_action_ts: float = 0.0

    async def async_start(self) -> None:
        if not self._settings.mode_enabled:
            return

        if not self._settings.grid_sensor_entity_id:
            LOGGER.warning(
                "Solar surplus mode enabled for %s but no grid power sensor is configured.",
                self._entry.title,
            )
            return

        sensor_entities = [self._settings.grid_sensor_entity_id]
        if self._settings.curtailment_sensor_entity_id:
            sensor_entities.append(self._settings.curtailment_sensor_entity_id)

        self._unsub_coordinator = self._coordinator.async_add_listener(
            self._handle_coordinator_update
        )
        self._unsub_sensor = async_track_state_change_event(
            self._hass,
            sensor_entities,
            self._handle_sensor_update,
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

            if not self._rerun_requested:
                break
            self._rerun_requested = False
            current_reason = "rerun"

    async def _async_evaluate_once(self, reason: str) -> None:
        _ = reason
        data = self._coordinator.data
        if data is None:
            return

        grid_power_w = self._read_grid_power_w()
        if grid_power_w is None:
            self._start_candidate_since = None
            self._stop_candidate_since = None
            return

        available_surplus_w = self._available_surplus_w(data, grid_power_w)
        available_currents = allowed_currents(data)
        if not available_currents:
            return

        min_current = min(available_currents)
        max_supported_current = _current_supported_by_surplus(
            available_currents,
            available_surplus_w,
            self._settings.line_voltage,
        )
        now = monotonic()

        if _is_charging(data):
            self._start_candidate_since = None

            should_stop = (
                available_surplus_w <= self._settings.stop_threshold_w
                or max_supported_current < min_current
            )
            if should_stop:
                if self._stop_candidate_since is None:
                    self._stop_candidate_since = now
                    return
                if now - self._stop_candidate_since < self._settings.stop_delay_s:
                    return

                self._stop_candidate_since = None
                if await self._client.async_set_charge_enabled(False):
                    self._last_action_ts = now
                    await self._coordinator.async_request_refresh()
                return

            self._stop_candidate_since = None
            desired_current = max(min_current, max_supported_current)
            if desired_current != data.current_target:
                if now - self._last_action_ts < self._settings.adjust_cooldown_s:
                    return
                if await self._client.async_set_charge_current(desired_current):
                    self._last_action_ts = now
                    await self._coordinator.async_request_refresh()
            return

        self._stop_candidate_since = None
        can_start = (
            available_surplus_w >= self._settings.start_threshold_w
            and max_supported_current >= min_current
        )
        if not can_start:
            self._start_candidate_since = None
            return

        if self._start_candidate_since is None:
            self._start_candidate_since = now
            return
        if now - self._start_candidate_since < self._settings.start_delay_s:
            return

        self._start_candidate_since = None
        desired_current = max(min_current, max_supported_current)
        if desired_current != data.current_target:
            if not await self._client.async_set_charge_current(desired_current):
                return
        if await self._client.async_set_charge_enabled(True):
            self._last_action_ts = now
            await self._coordinator.async_request_refresh()

    def _available_surplus_w(self, data: EVMetrics, grid_power_w: float) -> float:
        # Positive grid power means import. Reconstruct natural surplus by
        # adding EV consumption back into the grid balance.
        ev_power_w = _ev_power_w(data)
        reconstructed_surplus_w = ev_power_w - grid_power_w

        if self._settings.mode == SURPLUS_MODE_ZERO_INJECTION:
            reconstructed_surplus_w += self._read_curtailment_power_w()

        return reconstructed_surplus_w + self._settings.target_offset_w

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

    def _read_sensor_power_w(self, entity_id: str) -> float | None:
        state = self._hass.states.get(entity_id)
        if state is None:
            return None
        raw = state.state
        if raw in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
            return None

        try:
            value = float(str(raw).replace(",", "."))
        except ValueError:
            LOGGER.debug(
                "Unable to parse power sensor '%s' value '%s'.",
                entity_id,
                raw,
            )
            return None

        unit = str(state.attributes.get("unit_of_measurement", "")).strip().lower()
        if unit == "kw":
            return value * 1000.0
        return value


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
        adjust_cooldown_s=_option_int(
            options,
            CONF_SURPLUS_ADJUST_COOLDOWN_S,
            DEFAULT_SURPLUS_ADJUST_COOLDOWN_S,
            MIN_SURPLUS_DELAY_S,
            MAX_SURPLUS_DELAY_S,
        ),
    )


def _option_str(options: Any, key: str, default: str) -> str:
    value = options.get(key, default)
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
