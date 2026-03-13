from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import (
    CONF_SURPLUS_ADJUST_DOWN_COOLDOWN_S,
    CONF_SURPLUS_ADJUST_UP_COOLDOWN_S,
    CONF_SURPLUS_ALLOW_BATTERY_DISCHARGE_FOR_EV,
    CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    CONF_SURPLUS_PROFILE,
    CONF_SURPLUS_START_THRESHOLD_W,
    CONF_SURPLUS_STOP_THRESHOLD_W,
    DEFAULT_SURPLUS_PROFILE,
    SURPLUS_PROFILE_AGGRESSIVE,
    SURPLUS_PROFILE_BALANCED,
    SURPLUS_PROFILE_CONSERVATIVE,
    SURPLUS_PROFILE_ECO,
    SURPLUS_PROFILE_FAST,
    SURPLUS_PROFILES,
)


@dataclass(frozen=True, slots=True)
class SurplusProfilePreset:
    start_threshold_w: int
    stop_threshold_w: int
    max_battery_discharge_for_ev_w: int
    adjust_up_cooldown_s: int
    adjust_down_cooldown_s: int


SURPLUS_PROFILE_PRESETS: dict[str, SurplusProfilePreset] = {
    SURPLUS_PROFILE_ECO: SurplusProfilePreset(
        start_threshold_w=2200,
        stop_threshold_w=1700,
        max_battery_discharge_for_ev_w=0,
        adjust_up_cooldown_s=30,
        adjust_down_cooldown_s=8,
    ),
    SURPLUS_PROFILE_BALANCED: SurplusProfilePreset(
        start_threshold_w=1600,
        stop_threshold_w=1200,
        max_battery_discharge_for_ev_w=22000,
        adjust_up_cooldown_s=20,
        adjust_down_cooldown_s=10,
    ),
    SURPLUS_PROFILE_FAST: SurplusProfilePreset(
        start_threshold_w=1200,
        stop_threshold_w=900,
        max_battery_discharge_for_ev_w=22000,
        adjust_up_cooldown_s=8,
        adjust_down_cooldown_s=5,
    ),
}

_LEGACY_ALIASES: dict[str, str] = {
    SURPLUS_PROFILE_AGGRESSIVE: SURPLUS_PROFILE_FAST,
    SURPLUS_PROFILE_CONSERVATIVE: SURPLUS_PROFILE_ECO,
}


def normalize_surplus_profile(raw_value: Any) -> str:
    normalized = str(raw_value or "").strip().lower()
    if normalized in SURPLUS_PROFILES:
        return normalized
    if normalized in _LEGACY_ALIASES:
        return _LEGACY_ALIASES[normalized]
    return DEFAULT_SURPLUS_PROFILE


def is_supported_surplus_profile(raw_value: Any) -> bool:
    normalized = str(raw_value or "").strip().lower()
    if normalized in SURPLUS_PROFILES:
        return True
    if normalized in _LEGACY_ALIASES:
        return True
    return False


def apply_surplus_profile(options: dict[str, Any], profile: str) -> dict[str, Any]:
    normalized = normalize_surplus_profile(profile)
    preset = SURPLUS_PROFILE_PRESETS[normalized]

    options[CONF_SURPLUS_PROFILE] = normalized
    options[CONF_SURPLUS_ALLOW_BATTERY_DISCHARGE_FOR_EV] = True
    options[CONF_SURPLUS_START_THRESHOLD_W] = preset.start_threshold_w
    options[CONF_SURPLUS_STOP_THRESHOLD_W] = preset.stop_threshold_w
    options[CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W] = (
        preset.max_battery_discharge_for_ev_w
    )
    options[CONF_SURPLUS_ADJUST_UP_COOLDOWN_S] = preset.adjust_up_cooldown_s
    options[CONF_SURPLUS_ADJUST_DOWN_COOLDOWN_S] = preset.adjust_down_cooldown_s
    return options
