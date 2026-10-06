"""Reading the surplus options out of a config entry.

Split out of ``solar_surplus.py``: parsing and clamping the options is pure (no
I/O, no Home Assistant state), and it is most of what made that file long.
Behaviour is unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_CAP_ONLY_REGULATION,
    CONF_CRITICAL_PEAK_SENSOR_ENTITY_ID,
    CONF_CRITICAL_PEAK_SENSOR_INVERTED,
    CONF_DEPARTURE_ENERGY_KWH,
    CONF_DEPARTURE_TIME,
    CONF_EXTERNAL_CHARGE_ALLOWED_SENSOR_ENTITY_ID,
    CONF_EXTERNAL_CHARGE_ALLOWED_SENSOR_INVERTED,
    CONF_INSTALLATION_PHASES,
    CONF_LOAD_RESERVATIONS,
    CONF_MAX_HOUSE_POWER_W,
    CONF_MAX_INVERTER_POWER_W,
    CONF_OFF_PEAK_SENSOR_ENTITY_ID,
    CONF_OFF_PEAK_SENSOR_INVERTED,
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
    DEFAULT_CAP_ONLY_REGULATION,
    DEFAULT_CRITICAL_PEAK_SENSOR_ENTITY_ID,
    DEFAULT_CRITICAL_PEAK_SENSOR_INVERTED,
    DEFAULT_DEPARTURE_ENERGY_KWH,
    DEFAULT_DEPARTURE_TIME,
    DEFAULT_EXTERNAL_CHARGE_ALLOWED_SENSOR_ENTITY_ID,
    DEFAULT_EXTERNAL_CHARGE_ALLOWED_SENSOR_INVERTED,
    DEFAULT_INSTALLATION_PHASES,
    DEFAULT_LOAD_RESERVATIONS,
    DEFAULT_MAX_HOUSE_POWER_W,
    DEFAULT_MAX_INVERTER_POWER_W,
    DEFAULT_OFF_PEAK_SENSOR_ENTITY_ID,
    DEFAULT_OFF_PEAK_SENSOR_INVERTED,
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
from .option_values import clean_optional_text, option_bool, option_int

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class SolarSurplusSettings:
    mode_enabled: bool
    grid_sensor_entity_id: str
    max_house_power_w: int
    max_inverter_power_w: int
    cap_only_regulation: bool
    total_load_sensor_entity_id: str
    # How many phases the charger is wired on -- one setpoint applies to
    # every phase, so this scales every watts<->amps conversion (#41).
    installation_phases: int
    load_reservations: str
    off_peak_windows: str
    off_peak_sensor_entity_id: str
    off_peak_sensor_inverted: bool
    critical_peak_sensor_entity_id: str
    critical_peak_sensor_inverted: bool
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
    external_charge_allowed_sensor_entity_id: str
    external_charge_allowed_sensor_inverted: bool


def settings_from_entry(entry: ConfigEntry) -> SolarSurplusSettings:
    options = entry.options

    legacy_high = option_int(
        options,
        CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        DEFAULT_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    )
    high = option_int(
        options,
        CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
        legacy_high,
        MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    )
    if high <= MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT:
        high = MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT + 1
    low = option_int(
        options,
        CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
        min(DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT, high),
        MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    )

    if low >= high:
        low = max(MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, high - 1)
    max_battery_discharge_for_ev_w = option_int(
        options,
        CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        DEFAULT_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        MIN_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        MAX_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    )
    start_threshold_w = option_int(
        options,
        CONF_SURPLUS_START_THRESHOLD_W,
        DEFAULT_SURPLUS_START_THRESHOLD_W,
        MIN_SURPLUS_THRESHOLD_W,
        MAX_SURPLUS_THRESHOLD_W,
    )
    stop_threshold_w = option_int(
        options,
        CONF_SURPLUS_STOP_THRESHOLD_W,
        DEFAULT_SURPLUS_STOP_THRESHOLD_W,
        MIN_SURPLUS_THRESHOLD_W,
        MAX_SURPLUS_THRESHOLD_W,
    )
    if stop_threshold_w > start_threshold_w:
        stop_threshold_w = start_threshold_w
    adjust_up_cooldown_s = option_int(
        options,
        CONF_SURPLUS_ADJUST_UP_COOLDOWN_S,
        DEFAULT_SURPLUS_ADJUST_UP_COOLDOWN_S,
        MIN_SURPLUS_DELAY_S,
        MAX_SURPLUS_DELAY_S,
    )
    adjust_down_cooldown_s = option_int(
        options,
        CONF_SURPLUS_ADJUST_DOWN_COOLDOWN_S,
        DEFAULT_SURPLUS_ADJUST_DOWN_COOLDOWN_S,
        MIN_SURPLUS_DELAY_S,
        MAX_SURPLUS_DELAY_S,
    )

    return SolarSurplusSettings(
        mode_enabled=option_bool(
            options,
            CONF_SURPLUS_MODE_ENABLED,
            DEFAULT_SURPLUS_MODE_ENABLED,
        ),
        off_peak_windows=_option_str(options, CONF_OFF_PEAK_WINDOWS, DEFAULT_OFF_PEAK_WINDOWS),
        off_peak_sensor_entity_id=_option_str(
            options,
            CONF_OFF_PEAK_SENSOR_ENTITY_ID,
            DEFAULT_OFF_PEAK_SENSOR_ENTITY_ID,
        ),
        off_peak_sensor_inverted=option_bool(
            options,
            CONF_OFF_PEAK_SENSOR_INVERTED,
            DEFAULT_OFF_PEAK_SENSOR_INVERTED,
        ),
        critical_peak_sensor_entity_id=_option_str(
            options,
            CONF_CRITICAL_PEAK_SENSOR_ENTITY_ID,
            DEFAULT_CRITICAL_PEAK_SENSOR_ENTITY_ID,
        ),
        critical_peak_sensor_inverted=option_bool(
            options,
            CONF_CRITICAL_PEAK_SENSOR_INVERTED,
            DEFAULT_CRITICAL_PEAK_SENSOR_INVERTED,
        ),
        departure_time=_option_str(options, CONF_DEPARTURE_TIME, DEFAULT_DEPARTURE_TIME),
        departure_energy_kwh=option_int(
            options,
            CONF_DEPARTURE_ENERGY_KWH,
            DEFAULT_DEPARTURE_ENERGY_KWH,
            MIN_DEPARTURE_ENERGY_KWH,
            MAX_DEPARTURE_ENERGY_KWH,
        ),
        max_house_power_w=option_int(
            options,
            CONF_MAX_HOUSE_POWER_W,
            DEFAULT_MAX_HOUSE_POWER_W,
            MIN_MAX_HOUSE_POWER_W,
            MAX_MAX_HOUSE_POWER_W,
        ),
        max_inverter_power_w=option_int(
            options,
            CONF_MAX_INVERTER_POWER_W,
            DEFAULT_MAX_INVERTER_POWER_W,
            MIN_MAX_HOUSE_POWER_W,
            MAX_MAX_HOUSE_POWER_W,
        ),
        cap_only_regulation=option_bool(
            options,
            CONF_CAP_ONLY_REGULATION,
            DEFAULT_CAP_ONLY_REGULATION,
        ),
        installation_phases=_option_installation_phases(
            options,
            CONF_INSTALLATION_PHASES,
            DEFAULT_INSTALLATION_PHASES,
        ),
        total_load_sensor_entity_id=_option_str(
            options,
            CONF_TOTAL_LOAD_SENSOR_ENTITY_ID,
            DEFAULT_TOTAL_LOAD_SENSOR_ENTITY_ID,
        ),
        load_reservations=_option_str(options, CONF_LOAD_RESERVATIONS, DEFAULT_LOAD_RESERVATIONS),
        grid_sensor_entity_id=_option_str(
            options,
            CONF_SURPLUS_SENSOR_ENTITY_ID,
            DEFAULT_SURPLUS_SENSOR_ENTITY_ID,
        ),
        grid_sensor_inverted=option_bool(
            options,
            CONF_SURPLUS_SENSOR_INVERTED,
            DEFAULT_SURPLUS_SENSOR_INVERTED,
        ),
        curtailment_sensor_entity_id=_option_str(
            options,
            CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
            DEFAULT_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
        ),
        curtailment_sensor_inverted=option_bool(
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
        battery_net_discharge_sensor_inverted=option_bool(
            options,
            CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_INVERTED,
            DEFAULT_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_INVERTED,
        ),
        allow_battery_discharge_for_ev=option_bool(
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
        external_charge_allowed_sensor_entity_id=_option_str(
            options,
            CONF_EXTERNAL_CHARGE_ALLOWED_SENSOR_ENTITY_ID,
            DEFAULT_EXTERNAL_CHARGE_ALLOWED_SENSOR_ENTITY_ID,
        ),
        external_charge_allowed_sensor_inverted=option_bool(
            options,
            CONF_EXTERNAL_CHARGE_ALLOWED_SENSOR_INVERTED,
            DEFAULT_EXTERNAL_CHARGE_ALLOWED_SENSOR_INVERTED,
        ),
    )


def _option_str(options: Any, key: str, default: str) -> str:
    return clean_optional_text(options.get(key, default))


def _option_installation_phases(options: Any, key: str, default: str) -> int:
    """1 or 3 only -- a malformed or legacy value narrows to the single-phase
    default rather than dividing by a nonsensical phase count."""
    try:
        parsed = int(options.get(key, default))
    except (TypeError, ValueError):
        return 1
    return parsed if parsed == 3 else 1


def parse_end_time(raw: str) -> int | None:
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
