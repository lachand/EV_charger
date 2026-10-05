"""How a charger's data points (DPs) are laid out, and custom mappings of them.

A Tuya charger reports everything as numbered DPs, and the numbering differs by
model. A profile names which DP carries which fact. This module owns the built-in
profiles and the validation of a user's custom mapping; it knows nothing about the
connection. Split out of ``tuya_ev_charger.py``; behaviour is unchanged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .const import (
    CHARGER_PROFILE_CUSTOM_JSON,
    CHARGER_PROFILE_DEPOW_V2,
    CHARGER_PROFILE_GENERIC_V1,
    CHARGER_PROFILES,
    DEFAULT_CHARGER_PROFILE,
    DP_ADJUST_CURRENT,
    DP_ALARM,
    DP_CHARGE_HISTORY,
    DP_CHARGER_INFO,
    DP_CURRENT_TARGET,
    DP_DO_CHARGE,
    DP_DOWNCOUNTER,
    DP_MAX_CURRENT_CFG,
    DP_METRICS,
    DP_NFC_CFG,
    DP_NUM,
    DP_PRODUCT_VARIANT,
    DP_REBOOT,
    DP_SELFTEST,
    DP_SOCKET_CFG,
    DP_WORK_STATE,
    DP_WORK_STATE_DEBUG,
)

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class DPProfile:
    metrics: str
    charger_info: str
    work_state: str
    work_state_debug: str
    do_charge: str
    current_target: str
    max_current_cfg: str
    nfc_cfg: str
    downcounter: str
    selftest: str
    alarm: str
    charge_history: str
    adjust_current: str
    product_variant: str
    dp_num: str
    reboot: str
    plug_in_action: str


DP_PROFILE_MAP: dict[str, DPProfile] = {
    CHARGER_PROFILE_DEPOW_V2: DPProfile(
        metrics=DP_METRICS,
        charger_info=DP_CHARGER_INFO,
        work_state=DP_WORK_STATE,
        work_state_debug=DP_WORK_STATE_DEBUG,
        do_charge=DP_DO_CHARGE,
        current_target=DP_CURRENT_TARGET,
        max_current_cfg=DP_MAX_CURRENT_CFG,
        nfc_cfg=DP_NFC_CFG,
        downcounter=DP_DOWNCOUNTER,
        selftest=DP_SELFTEST,
        alarm=DP_ALARM,
        charge_history=DP_CHARGE_HISTORY,
        adjust_current=DP_ADJUST_CURRENT,
        product_variant=DP_PRODUCT_VARIANT,
        dp_num=DP_NUM,
        reboot=DP_REBOOT,
        plug_in_action=DP_SOCKET_CFG,
    ),
    # Generic profile currently mirrors depow_v2 mappings and is meant as
    # an extension point for additional charger firmwares.
    CHARGER_PROFILE_GENERIC_V1: DPProfile(
        metrics=DP_METRICS,
        charger_info=DP_CHARGER_INFO,
        work_state=DP_WORK_STATE,
        work_state_debug=DP_WORK_STATE_DEBUG,
        do_charge=DP_DO_CHARGE,
        current_target=DP_CURRENT_TARGET,
        max_current_cfg=DP_MAX_CURRENT_CFG,
        nfc_cfg=DP_NFC_CFG,
        downcounter=DP_DOWNCOUNTER,
        selftest=DP_SELFTEST,
        alarm=DP_ALARM,
        charge_history=DP_CHARGE_HISTORY,
        adjust_current=DP_ADJUST_CURRENT,
        product_variant=DP_PRODUCT_VARIANT,
        dp_num=DP_NUM,
        reboot=DP_REBOOT,
        plug_in_action=DP_SOCKET_CFG,
    ),
}


def resolve_profile(profile: str, custom_json: str) -> tuple[str, DPProfile]:
    normalized = str(profile).strip().lower()
    if normalized == CHARGER_PROFILE_CUSTOM_JSON:
        custom_profile = _parse_custom_dp_profile(custom_json)
        if custom_profile is not None:
            return CHARGER_PROFILE_CUSTOM_JSON, custom_profile
        LOGGER.warning(
            "Invalid custom charger profile JSON mapping, falling back to '%s'.",
            DEFAULT_CHARGER_PROFILE,
        )
        return DEFAULT_CHARGER_PROFILE, DP_PROFILE_MAP[DEFAULT_CHARGER_PROFILE]
    if normalized in CHARGER_PROFILES and normalized in DP_PROFILE_MAP:
        return normalized, DP_PROFILE_MAP[normalized]
    return DEFAULT_CHARGER_PROFILE, DP_PROFILE_MAP[DEFAULT_CHARGER_PROFILE]


def _parse_custom_dp_profile(raw_json: str) -> DPProfile | None:
    text = str(raw_json).strip()
    if not text:
        return None
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        LOGGER.debug("Unable to decode custom charger profile JSON.")
        return None
    if not isinstance(payload, dict):
        return None

    base_profile = DP_PROFILE_MAP[DEFAULT_CHARGER_PROFILE]
    values: dict[str, str] = {}
    for field_name in DPProfile.__dataclass_fields__:
        raw_value = payload.get(field_name, getattr(base_profile, field_name))
        if raw_value is None:
            return None
        text_value = str(raw_value).strip()
        if not text_value:
            return None
        values[field_name] = text_value
    return DPProfile(**values)


def validate_custom_dp_profile(raw_json: str) -> str | None:
    """Why a custom DP mapping would be rejected, or None when it is usable.

    The parser above silently falls back to the default profile and logs a
    warning, which the user never sees: the form accepts the JSON, the charger
    then reports nothing, and the mapping looks applied. This says what is wrong
    while the dialog is still open.
    """
    text = str(raw_json or "").strip()
    if not text:
        return None

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as err:
        return f"not valid JSON ({err.msg} at line {err.lineno})"
    if not isinstance(payload, dict):
        return "must be a JSON object mapping field names to DP numbers"

    known = set(DPProfile.__dataclass_fields__)
    unknown = sorted(set(payload) - known)
    if unknown:
        return f"unknown field(s): {', '.join(unknown)}"

    empty = sorted(
        name for name, value in payload.items() if value is None or not str(value).strip()
    )
    if empty:
        return f"empty value(s) for: {', '.join(empty)}"

    # Two fields on the same DP is always a mistake and produces silently wrong
    # readings rather than an error.
    seen: dict[str, str] = {}
    for name, value in payload.items():
        dp = str(value).strip()
        if dp in seen:
            return f"'{name}' and '{seen[dp]}' both map to DP {dp}"
        seen[dp] = name
    return None


def known_dp_profile_fields() -> tuple[str, ...]:
    """Field names a custom mapping may set, for showing in the UI."""
    return tuple(DPProfile.__dataclass_fields__)
