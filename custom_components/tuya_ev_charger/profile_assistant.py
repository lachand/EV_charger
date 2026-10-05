"""The profile assistant: guessing a charger's data-point layout from a raw read.

For an unfamiliar charger the DP numbers are the unknown; this reads every DP once
and reports which ones look like the metrics blob, the info blob, the on/off flag,
the current setpoint and the operating state, and which built-in profile fits best.
Split out of ``solar_surplus.py``; behaviour is unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from .const import (
    CHARGER_PROFILE_DEPOW_V2,
    DP_CHARGER_INFO,
    DP_CURRENT_TARGET,
    DP_DO_CHARGE,
    DP_METRICS,
    DP_WORK_STATE_DEBUG,
)
from .option_values import coerce_optional_bool, coerce_optional_int
from .tuya_ev_charger import TuyaEVChargerClient


async def profile_assistant_report(client: TuyaEVChargerClient) -> dict[str, Any]:
    dps = await client.async_get_raw_dps()
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
        if looks_like_metrics(value):
            candidates["metrics"].append(dp_id)
        if looks_like_charger_info(value):
            candidates["charger_info"].append(dp_id)
        if coerce_optional_bool(value) is not None:
            candidates["do_charge"].append(dp_id)
        if looks_like_current_target(value):
            candidates["current_target"].append(dp_id)
        if looks_like_state_debug(value):
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


def looks_like_metrics(value: Any) -> bool:
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


def looks_like_charger_info(value: Any) -> bool:
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


def looks_like_current_target(value: Any) -> bool:
    parsed = coerce_optional_int(value)
    if parsed is None:
        return False
    return 6 <= parsed <= 32


def looks_like_state_debug(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().upper()
    return normalized in {"STANDBY", "WORKING", "DONE", "FAULT"}
