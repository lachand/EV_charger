"""Turning a charger's raw data points into readings the rest of the code uses.

Everything here is a pure function of a payload: no connection, no I/O. The
decoding is tolerant by design, because firmwares disagree about types (a flag can
arrive as a bool, an int or a string) and about which DPs they report at all, and
one odd value must not take down a poll. Split out of ``tuya_ev_charger.py`` so the
decoding can be tested without a client; behaviour is unchanged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from .const import DP_SCHEDULE
from .dp_profile import DPProfile
from .option_values import coerce_optional_bool, coerce_optional_int

LOGGER = logging.getLogger(__name__)

PHASE_NAMES: tuple[str, ...] = ("L1", "L2", "L3")
# DP 109 states observed across models: SLEEP (standby), IDLE (ready, unplugged),
# IDLEINS (cable inserted, not charging), WORKING (charging).
WORK_STATE_CHARGING = "WORKING"

# Friendly, translatable status decoded from the raw DP 109 string. The mapping
# matches tuya_local's config for this exact product (`dewall_evcharger.yaml`,
# product id gxrtu5vljdthtd3g), so the values stay stable for automations
# instead of exposing firmware strings. IDLEINS in particular reads as a bare
# "IDLEINS" today, which means nothing to a user.
STATUS_MAP: dict[str, str] = {
    "SLEEP": "sleep",
    "IDLE": "idle",
    "IDLEINS": "plugged_in",
    "WORKING": "charging",
    "WAIT": "waiting",
    "ERRORPAUSE": "fault",
    "PAUSE": "paused",
    "STOP": "charged",
}
# dict.fromkeys keeps order while tolerating two raw states sharing a value.
STATUS_OPTIONS: tuple[str, ...] = tuple(dict.fromkeys(STATUS_MAP.values()))

# DP 154 decides what the charger does when a cable is plugged in. "idle" is the
# clean way to stop a car auto-starting a charge.
PLUG_IN_ACTION_MAP: dict[int, str] = {0: "prompt", 1: "charge", 2: "idle"}
PLUG_IN_ACTION_OPTIONS: tuple[str, ...] = tuple(PLUG_IN_ACTION_MAP.values())

# IEC 61851 status letters, the vocabulary evcc consumes: A = no vehicle,
# B = connected but not charging, C = charging.
EVCC_STATUS_OPTIONS: tuple[str, ...] = ("A", "B", "C")
# Above this the charger is really delivering. WORKING can linger after a
# completed charge, so power is what separates "charging" from "connected".
EVCC_CHARGING_POWER_KW = 0.1
# Statuses that mean a vehicle is plugged in but not drawing.
_EVCC_CONNECTED = frozenset({"plugged_in", "waiting", "paused", "charged", "fault"})


def evcc_status(status: str | None, total_power: float) -> str:
    """Map our decoded status to the A/B/C letter evcc expects."""
    if status == "charging":
        return "C" if total_power >= EVCC_CHARGING_POWER_KW else "B"
    if total_power >= EVCC_CHARGING_POWER_KW:
        return "C"
    if status in _EVCC_CONNECTED:
        return "B"
    return "A"


@dataclass(slots=True, frozen=True)
class PhaseMetrics:
    """Per-phase readings decoded from DP 102.

    Voltage and current use a verified /10 scale (2270 -> 227.0 V, 87 -> 8.7 A).
    Power is expressed in kW and derived from voltage x current rather than read
    from the third array element: the reported value is quantised to 0.1 kW
    (measured 19 for 227.0 V x 8.7 A = 1.975 kW), so deriving it is both finer
    grained and independent of a per-model scale. The reported value is kept in
    ``raw_power`` for diagnostics.
    """

    voltage: float
    current: float
    power: float
    raw_power: float


@dataclass(slots=True, frozen=True)
class EVMetrics:
    voltage_l1: float
    current_l1: float
    power_l1: float
    phases: dict[str, PhaseMetrics]
    total_power: float
    session_energy_kwh: float | None
    session_duration_s: int | None
    last_session_energy_kwh: float | None
    last_session_duration_s: int | None
    temperature: float
    work_state: int | None
    work_state_debug: str
    status: str | None
    plug_in_action: str | None
    do_charge: bool | None
    current_target: int | None
    max_current_cfg: int | None
    nfc_enabled: bool | None
    downcounter: int | None
    selftest: str | None
    alarm: str | None
    adjust_current_options: tuple[int, ...] | None
    product_variant: int | None
    charger_info: dict[str, Any]
    schedule_enabled: bool
    schedule_start: str | None
    schedule_end: str | None


def decode_metrics(dps: dict[str, Any], dp: DPProfile) -> EVMetrics:
    """Decode one status read (a DP-number -> value mapping) into ``EVMetrics``."""
    metrics_dict = _parse_json_object(dps.get(dp.metrics, "{}"))
    charger_info = _parse_json_object(dps.get(dp.charger_info, "{}"))
    schedule_dict = _parse_json_object(dps.get(DP_SCHEDULE, "{}"))
    history_dict = _parse_json_object(dps.get(dp.charge_history, "{}"))

    work_state_debug = _coerce_optional_text(dps.get(dp.work_state_debug)) or "UNKNOWN"
    work_state_debug = work_state_debug.strip().upper()
    do_charge = coerce_optional_bool(dps.get(dp.do_charge))

    # The charger keeps reporting the last power reading after a session
    # ends, which corrupts surplus regulation and "car full" detection, so
    # treat anything but an active session as zero power. A model that
    # reports DP 140 counts as charging when it says so, even if its DP 109
    # string is one we do not map to "charging" (#35).
    plug_in_raw = coerce_optional_int(dps.get(dp.plug_in_action))
    charging = work_state_debug == WORK_STATE_CHARGING or do_charge is True
    phases = _parse_phases(metrics_dict, charging)
    l1 = phases.get("L1")

    return EVMetrics(
        voltage_l1=l1.voltage if l1 else 0.0,
        current_l1=l1.current if l1 else 0.0,
        power_l1=l1.power if l1 else 0.0,
        phases=phases,
        total_power=round(sum(phase.power for phase in phases.values()), 3),
        # DP 102 tracks the *running* session: "e" in 0.1 kWh, "d" in 0.1 s
        # (verified against 2h37 of charging at ~2 kW giving 5.2 kWh).
        session_energy_kwh=_tenths(metrics_dict.get("e")),
        session_duration_s=_deciseconds(metrics_dict.get("d")),
        # DP 105 is a frozen record of the last *completed* session, with its
        # duration in plain seconds.
        last_session_energy_kwh=_tenths(history_dict.get("c")),
        last_session_duration_s=coerce_optional_int(history_dict.get("d")),
        temperature=_coerce_float(metrics_dict.get("t", 0)) / 10.0,
        work_state=coerce_optional_int(dps.get(dp.work_state)),
        work_state_debug=work_state_debug,
        status=STATUS_MAP.get(work_state_debug),
        plug_in_action=(PLUG_IN_ACTION_MAP.get(plug_in_raw) if plug_in_raw is not None else None),
        do_charge=do_charge,
        current_target=coerce_optional_int(dps.get(dp.current_target)),
        max_current_cfg=coerce_optional_int(dps.get(dp.max_current_cfg)),
        nfc_enabled=coerce_optional_bool(dps.get(dp.nfc_cfg)),
        downcounter=coerce_optional_int(dps.get(dp.downcounter)),
        selftest=_coerce_optional_text(dps.get(dp.selftest)),
        alarm=_coerce_optional_json_text(dps.get(dp.alarm)),
        adjust_current_options=_parse_int_list(dps.get(dp.adjust_current)),
        product_variant=coerce_optional_int(dps.get(dp.product_variant)),
        charger_info=charger_info,
        schedule_enabled=schedule_dict.get("m", 0) == 2,
        schedule_start=_coerce_optional_text(schedule_dict.get("ss")),
        schedule_end=_coerce_optional_text(schedule_dict.get("se")),
    )


def _parse_phases(
    metrics_dict: dict[str, Any],
    charging: bool,
) -> dict[str, PhaseMetrics]:
    """Decode the per-phase arrays of DP 102.

    Single-phase chargers report L2/L3 as all-zero; those phases are omitted so
    the entities show as unavailable rather than a misleading 0 V.
    """
    phases: dict[str, PhaseMetrics] = {}
    for name in PHASE_NAMES:
        raw = metrics_dict.get(name)
        if not isinstance(raw, list) or len(raw) < 3:
            continue
        voltage = _coerce_float(raw[0]) / 10.0
        current = _coerce_float(raw[1]) / 10.0
        raw_power = _coerce_float(raw[2])
        if name != "L1" and voltage == 0.0 and current == 0.0:
            # Phase not wired on this model.
            continue
        # Some firmwares keep echoing the last current (and power) reading
        # once a session ends, so treat both as zero outside an active
        # session -- same reasoning as the existing power reset below.
        metered_current = current if charging else 0.0
        phases[name] = PhaseMetrics(
            voltage=voltage,
            current=metered_current,
            # kW, to match the reported field's unit.
            power=round(voltage * metered_current / 1000.0, 3),
            raw_power=round(raw_power / 10.0, 2),
        )
    return phases


def _tenths(raw_value: Any) -> float | None:
    """Decode a counter reported in tenths of a unit (0.1 kWh)."""
    value = _coerce_optional_float(raw_value)
    if value is None:
        return None
    return round(value / 10.0, 2)


def _deciseconds(raw_value: Any) -> int | None:
    """Decode a duration reported in tenths of a second."""
    value = _coerce_optional_float(raw_value)
    if value is None:
        return None
    return int(value / 10.0)


def _parse_json_object(raw_value: Any) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return raw_value
    if not isinstance(raw_value, str):
        return {}

    try:
        decoded: Any = json.loads(raw_value)
    except json.JSONDecodeError:
        LOGGER.debug("Unable to decode JSON object: %s", raw_value)
        return {}

    if isinstance(decoded, dict):
        return decoded
    return {}


def _parse_int_list(raw_value: Any) -> tuple[int, ...] | None:
    parsed_list: list[Any]
    if isinstance(raw_value, list):
        parsed_list = raw_value
    elif isinstance(raw_value, str):
        try:
            decoded: Any = json.loads(raw_value)
        except json.JSONDecodeError:
            return None
        if not isinstance(decoded, list):
            return None
        parsed_list = decoded
    else:
        return None

    cleaned: list[int] = []
    for item in parsed_list:
        value = coerce_optional_int(item)
        if value is None:
            continue
        cleaned.append(value)
    if not cleaned:
        return None
    return tuple(sorted(set(cleaned)))


def _coerce_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _coerce_optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = value.strip() if isinstance(value, str) else str(value).strip()
    if not text:
        return None
    return text


def _coerce_optional_json_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    try:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    except TypeError:
        return _coerce_optional_text(value)


def values_match(received: Any, expected: Any) -> bool:
    # Compare on the type actually written. `expected` is a real bool for the
    # on/off DPs and an int for the numeric ones (DP 101 work state, DP 150
    # current) -- coercing an int like 300 through bool() first made it "match"
    # a read-back of 200, so a write that never took looked verified.
    if isinstance(expected, bool):
        received_bool = coerce_optional_bool(received)
        return received_bool is not None and received_bool == expected

    expected_int = coerce_optional_int(expected)
    if expected_int is not None:
        received_int = coerce_optional_int(received)
        return received_int is not None and received_int == expected_int

    if isinstance(expected, str):
        return str(received).strip() == expected.strip()
    return bool(received == expected)
