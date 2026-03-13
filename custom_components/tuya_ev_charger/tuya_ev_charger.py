from __future__ import annotations

import asyncio
import json
import logging
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
    DP_SELFTEST,
    DP_WORK_STATE,
    DP_WORK_STATE_DEBUG,
)

LOGGER = logging.getLogger(__name__)
COMMAND_VERIFY_RETRIES = 3
COMMAND_VERIFY_DELAY_S = 0.5


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
    ),
}


@dataclass(slots=True, frozen=True)
class EVMetrics:
    voltage_l1: float
    current_l1: float
    power_l1: float
    temperature: float
    work_state: int | None
    work_state_debug: str
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
    def dp_profile(self) -> str:
        return self._dp_profile

    async def async_connect(self) -> None:
        self._device = tinytuya.Device(
            dev_id=self._device_id,
            address=self._host,
            local_key=self._local_key,
            version=self._protocol_version,
        )
        self._device.set_socketTimeout(5)

    async def async_set_charge_current(self, amperage: int) -> bool:
        if amperage < min(ALLOWED_CURRENTS) or amperage > max(ALLOWED_CURRENTS):
            raise ValueError(
                f"Current setpoint {amperage}A is out of supported range "
                f"({min(ALLOWED_CURRENTS)}-{max(ALLOWED_CURRENTS)}A)."
            )
        return await self._async_send_command(self._dp.current_target, amperage)

    async def async_set_charge_enabled(self, enabled: bool) -> bool:
        return await self._async_send_command(self._dp.do_charge, enabled)

    async def async_set_nfc_enabled(self, enabled: bool) -> bool:
        return await self._async_send_command(self._dp.nfc_cfg, enabled)

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
        l1_data = metrics_dict.get("L1", [0, 0, 0])
        if not isinstance(l1_data, list) or len(l1_data) < 3:
            l1_data = [0, 0, 0]

        raw_power = l1_data[2] if len(l1_data) > 2 else metrics_dict.get("p", 0)
        work_state_debug = _coerce_optional_text(dps.get(self._dp.work_state_debug)) or "UNKNOWN"
        return EVMetrics(
            voltage_l1=_coerce_float(l1_data[0]) / 10.0,
            current_l1=_coerce_float(l1_data[1]) / 10.0,
            power_l1=_coerce_float(raw_power) / 10.0,
            temperature=_coerce_float(metrics_dict.get("t", 0)) / 10.0,
            work_state=_coerce_optional_int(dps.get(self._dp.work_state)),
            work_state_debug=work_state_debug.strip().upper(),
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
        )

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

        if await self._async_verify_command(dp_id, value):
            return True

        LOGGER.error("Command accepted but not reflected in status for DP %s.", dp_id)
        return False

    async def _async_verify_command(self, dp_id: str, expected: Any) -> bool:
        for _ in range(COMMAND_VERIFY_RETRIES):
            await asyncio.sleep(COMMAND_VERIFY_DELAY_S)
            dps = await self._async_get_dps_payload()
            if dps is None:
                continue
            if _values_match(dps.get(dp_id), expected):
                return True
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
    if isinstance(value, str):
        text = value.strip()
    else:
        text = str(value).strip()
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
