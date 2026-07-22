from __future__ import annotations

import asyncio
import json
import logging
import socket
from dataclasses import dataclass
from typing import Any

import tinytuya  # type: ignore

from .const import (
    ALLOWED_CURRENTS,
    CHARGER_PROFILE_CUSTOM_JSON,
    CHARGER_PROFILE_DEPOW_V2,
    CHARGER_PROFILE_GENERIC_V1,
    CHARGER_PROFILES,
    DEFAULT_CHARGER_PROFILE,
    DEFAULT_CHARGER_PROFILE_JSON,
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
    DP_SCHEDULE,
    DP_SELFTEST,
    DP_SOCKET_CFG,
    DP_WORK_STATE,
    DP_WORK_STATE_DEBUG,
    TUYA_CONTROL_PORT,
    ConnectionFault,
)

LOGGER = logging.getLogger(__name__)
# The charger's relay and status can lag a re-read by several seconds, notably
# when do_charge turns off. 3 x 0.5s was too tight and produced false "not
# reflected" errors on chargers that *do* report the DP, just late.
COMMAND_VERIFY_RETRIES = 8
COMMAND_VERIFY_DELAY_S = 1.0

# Socket tuning. tinytuya defaults to 5 retries x 5s delay, so a single failed
# read blocks for ~35s and floods the log. A charger only accepts one local
# connection at a time, so we fail fast and let the next poll retry instead.
SOCKET_TIMEOUT_S = 5
SOCKET_RETRY_LIMIT = 1
SOCKET_RETRY_DELAY_S = 1

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


def _configure_device(device: tinytuya.Device) -> None:
    device.set_socketTimeout(SOCKET_TIMEOUT_S)
    device.set_socketRetryLimit(SOCKET_RETRY_LIMIT)
    device.set_socketRetryDelay(SOCKET_RETRY_DELAY_S)


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


class TuyaEVChargerClient:
    def __init__(
        self,
        device_id: str,
        host: str,
        local_key: str,
        protocol_version: str,
        charger_profile: str = DEFAULT_CHARGER_PROFILE,
        charger_profile_json: str = DEFAULT_CHARGER_PROFILE_JSON,
    ) -> None:
        self._device_id = device_id
        self._host = host
        self._local_key = local_key
        self._protocol_version = protocol_version
        self._dp_profile, self._dp = _resolve_profile(
            charger_profile,
            charger_profile_json,
        )
        self._device: tinytuya.Device | None = None

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def host(self) -> str:
        return self._host

    @property
    def local_key(self) -> str:
        return self._local_key

    @property
    def dp_profile(self) -> str:
        return self._dp_profile

    async def async_connect(self) -> None:
        if self._device is not None:
            # Close the previous socket so it never lingers on the charger's
            # single local-connection slot.
            self._device.close()
        device = tinytuya.Device(
            dev_id=self._device_id,
            address=self._host,
            local_key=self._local_key,
            version=self._protocol_version,
        )
        _configure_device(device)
        self._device = device

    async def async_update_host(self, host: str) -> None:
        """Point the client at a new IP (after a DHCP change) and reconnect."""
        self._host = host
        await self.async_connect()

    async def async_update_local_key(self, local_key: str) -> None:
        """Adopt a rotated local_key (after re-pairing) and reconnect."""
        self._local_key = local_key
        await self.async_connect()

    async def _async_probe_port(self) -> str:
        """Classify what the control port does when we knock on it."""

        def _connect() -> str:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(SOCKET_TIMEOUT_S)
            try:
                sock.connect((self._host, TUYA_CONTROL_PORT))
                return ConnectionFault.OK
            except ConnectionRefusedError:
                # The host is up and actively rejecting us: a Tuya charger takes
                # a single local connection, so something else almost certainly
                # holds it.
                return ConnectionFault.REFUSED
            except OSError:
                return ConnectionFault.UNREACHABLE
            finally:
                sock.close()

        return await asyncio.to_thread(_connect)

    async def async_classify_fault(self) -> str:
        """Work out why reads are failing, so the user gets an actionable message.

        Separates a wrong or absent address, from a port that refuses us, from a
        port that talks but whose payload no longer decrypts (rotated local_key).
        """
        verdict = await self._async_probe_port()
        if verdict != ConnectionFault.OK:
            return verdict
        # The port answers, so a failed read points at the credentials.
        return (
            ConnectionFault.OK
            if await self.async_probe_host(self._host)
            else ConnectionFault.UNDECRYPTABLE
        )

    async def async_tcp_reachable(self) -> bool:
        """True when the control port accepts a TCP connection."""
        return await self._async_probe_port() == ConnectionFault.OK

    async def async_close(self) -> None:
        """Close the socket so it never lingers on the charger's single slot.

        A Tuya charger accepts only one local connection at a time; not closing
        on unload/reload leaves a zombie socket that makes the device refuse
        every later connection (including our own next instance).
        """
        if self._device is not None:
            await asyncio.to_thread(self._device.close)
            self._device = None

    async def async_probe_host(self, host: str) -> bool:
        """Return True if our charger answers at ``host``.

        Opens a throwaway connection with our own device_id/local_key and reads
        the live status (grid voltage & co). Only the real charger decrypts the
        reply with our local_key, so a successful read confirms identity without
        relying on the MAC or the advertised device_id. The socket is always
        closed afterwards so the probe never holds the charger's single local
        connection slot, and the live client is left untouched.
        """

        def _probe() -> bool:
            device = tinytuya.Device(
                dev_id=self._device_id,
                address=host,
                local_key=self._local_key,
                version=self._protocol_version,
            )
            _configure_device(device)
            try:
                payload: Any = device.status()
            except Exception:
                return False
            finally:
                device.close()
            return (
                isinstance(payload, dict)
                and "Error" not in payload
                and isinstance(payload.get("dps"), dict)
                and bool(payload["dps"])
            )

        return await asyncio.to_thread(_probe)

    async def async_set_charge_current(self, amperage: int, max_current: int | None = None) -> bool:
        upper = max(ALLOWED_CURRENTS)
        if max_current is not None:
            # Respect the charger's own hardware limit (DP 152) on top of the
            # range the integration supports.
            upper = min(upper, max_current)
        if amperage < min(ALLOWED_CURRENTS) or amperage > upper:
            raise ValueError(
                f"Current setpoint {amperage}A is out of supported range "
                f"({min(ALLOWED_CURRENTS)}-{upper}A)."
            )
        return await self._async_send_command(self._dp.current_target, amperage)

    async def async_set_charge_enabled(self, enabled: bool) -> bool:
        return await self._async_send_command(self._dp.do_charge, enabled)

    async def async_set_nfc_enabled(self, enabled: bool) -> bool:
        return await self._async_send_command(self._dp.nfc_cfg, enabled)

    async def async_set_plug_in_action(self, action: str) -> bool:
        """Choose what the charger does when a cable is plugged in.

        "idle" stops the car auto-starting a charge, which is the supported way
        to hold a session rather than driving the current below the 6 A the
        IEC 61851 pilot signal defines.
        """
        for raw_value, name in PLUG_IN_ACTION_MAP.items():
            if name == action:
                return await self._async_send_command(
                    self._dp.plug_in_action, raw_value
                )
        raise ValueError(f"Unsupported plug-in action '{action}'.")

    async def async_set_work_state(self, state: int) -> bool:
        """Write the charger's operating state (DP 101).

        Writable per tuya_local's config for this product. Used to put the
        charger back into "ready to charge" after a session, which is what
        clears a stale power reading on some firmwares.
        """
        return await self._async_send_command(self._dp.work_state, state)

    async def async_reboot(self) -> bool:
        # Depending on firmware variants, reboot may accept bool, int, or string payloads.
        for payload in (True, 1, "1"):
            if await self._async_send_command(self._dp.reboot, payload, verify=False):
                return True
        return False

    async def async_get_metrics(self) -> EVMetrics | None:
        dps = await self._async_get_dps_payload()
        if dps is None:
            return None

        metrics_dict = _parse_json_object(dps.get(self._dp.metrics, "{}"))
        charger_info = _parse_json_object(dps.get(self._dp.charger_info, "{}"))
        schedule_dict = _parse_json_object(dps.get(DP_SCHEDULE, "{}"))
        history_dict = _parse_json_object(dps.get(self._dp.charge_history, "{}"))

        work_state_debug = _coerce_optional_text(dps.get(self._dp.work_state_debug)) or "UNKNOWN"
        work_state_debug = work_state_debug.strip().upper()

        # The charger keeps reporting the last power reading after a session
        # ends, which corrupts surplus regulation and "car full" detection, so
        # treat anything but an active session as zero power.
        charging = work_state_debug == WORK_STATE_CHARGING
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
            last_session_duration_s=_coerce_optional_int(history_dict.get("d")),
            temperature=_coerce_float(metrics_dict.get("t", 0)) / 10.0,
            work_state=_coerce_optional_int(dps.get(self._dp.work_state)),
            work_state_debug=work_state_debug,
            status=STATUS_MAP.get(work_state_debug),
            plug_in_action=PLUG_IN_ACTION_MAP.get(
                _coerce_optional_int(dps.get(self._dp.plug_in_action))
            ),
            do_charge=_coerce_optional_bool(dps.get(self._dp.do_charge)),
            current_target=_coerce_optional_int(dps.get(self._dp.current_target)),
            max_current_cfg=_coerce_optional_int(dps.get(self._dp.max_current_cfg)),
            nfc_enabled=_coerce_optional_bool(dps.get(self._dp.nfc_cfg)),
            downcounter=_coerce_optional_int(dps.get(self._dp.downcounter)),
            selftest=_coerce_optional_text(dps.get(self._dp.selftest)),
            alarm=_coerce_optional_json_text(dps.get(self._dp.alarm)),
            adjust_current_options=_parse_int_list(dps.get(self._dp.adjust_current)),
            product_variant=_coerce_optional_int(dps.get(self._dp.product_variant)),
            charger_info=charger_info,
            schedule_enabled=schedule_dict.get("m", 0) == 2,
            schedule_start=_coerce_optional_text(schedule_dict.get("ss")),
            schedule_end=_coerce_optional_text(schedule_dict.get("se")),
        )

    async def async_set_schedule(self, enabled: bool, start: str, end: str) -> bool:
        payload = json.dumps(
            {"m": 2 if enabled else 0, "dt": 0, "ss": start, "se": end},
            separators=(",", ":"),
        )
        return await self._async_send_command(DP_SCHEDULE, payload, verify=False)

    async def async_get_raw_dps(self) -> dict[str, Any] | None:
        return await self._async_get_dps_payload()

    async def _async_send_command(self, dp_id: str, value: Any, verify: bool = True) -> bool:
        device = self._get_device()
        response: Any = await asyncio.to_thread(device.set_value, dp_id, value)
        if not (isinstance(response, dict) and "Error" not in response):
            LOGGER.error("Command rejected for DP %s: %s", dp_id, response)
            return False

        if not verify:
            return True

        verdict = await self._async_verify_command(dp_id, value)
        if verdict is not False:
            return True

        LOGGER.error("Command accepted but not reflected in status for DP %s.", dp_id)
        return False

    async def _async_verify_command(self, dp_id: str, expected: Any) -> bool | None:
        """Check the charger echoes back a written DP.

        Returns True on a match, False on a genuine mismatch, and None when the
        DP is simply absent from the status payload. Several models never report
        the write-only DPs their profile declares (for example DP 140 does not
        exist on the depow 3.5kW), so demanding an echo there would fail every
        command even though the charger obeyed it.
        """
        saw_dp = False
        for _ in range(COMMAND_VERIFY_RETRIES):
            await asyncio.sleep(COMMAND_VERIFY_DELAY_S)
            dps = await self._async_get_dps_payload()
            if dps is None:
                continue
            if dp_id not in dps:
                continue
            saw_dp = True
            if _values_match(dps.get(dp_id), expected):
                return True

        if not saw_dp:
            LOGGER.debug(
                "DP %s is not reported by this charger; assuming the command was applied.",
                dp_id,
            )
            return None
        return False

    async def _async_get_dps_payload(self) -> dict[str, Any] | None:
        device = self._get_device()
        payload: Any = await asyncio.to_thread(device.status)

        if not isinstance(payload, dict):
            LOGGER.error("Invalid status payload type: %s", type(payload).__name__)
            return None

        if "Error" in payload:
            LOGGER.error("Charger returned an error payload: %s", payload["Error"])
            return None

        dps: Any = payload.get("dps", {})
        if not isinstance(dps, dict):
            LOGGER.error("Missing or invalid DPS payload.")
            return None
        return dps

    def _get_device(self) -> tinytuya.Device:
        if self._device is None:
            raise RuntimeError("Device client is not initialized. Call async_connect first.")
        return self._device


def _resolve_profile(profile: str, custom_json: str) -> tuple[str, DPProfile]:
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
        name
        for name, value in payload.items()
        if value is None or not str(value).strip()
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
        phases[name] = PhaseMetrics(
            voltage=voltage,
            current=current,
            # kW, to match the reported field's unit.
            power=round(voltage * current / 1000.0, 3) if charging else 0.0,
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
        value = _coerce_optional_int(item)
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


def _coerce_optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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


def _values_match(received: Any, expected: Any) -> bool:
    expected_bool = _coerce_optional_bool(expected)
    if expected_bool is not None:
        received_bool = _coerce_optional_bool(received)
        return received_bool is not None and received_bool == expected_bool

    expected_int = _coerce_optional_int(expected)
    if expected_int is not None:
        received_int = _coerce_optional_int(received)
        return received_int is not None and received_int == expected_int

    if isinstance(expected, str):
        return str(received).strip() == expected.strip()
    return received == expected
