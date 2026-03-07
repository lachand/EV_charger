from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import tinytuya  # type: ignore

from .const import (
    ALLOWED_CURRENTS,
    DP_CURRENT_TARGET,
    DP_DO_CHARGE,
    DP_MAX_CURRENT_CFG,
    DP_METRICS,
    DP_REBOOT,
    DP_WORK_STATE,
)

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class EVMetrics:
    voltage_l1: float
    current_l1: float
    power_l1: float
    temperature: float
    raw_status: str
    do_charge: bool | None
    current_target: int | None
    max_current_cfg: int | None


class TuyaEVChargerClient:
    def __init__(
        self,
        device_id: str,
        host: str,
        local_key: str,
        protocol_version: str,
    ) -> None:
        self._device_id = device_id
        self._host = host
        self._local_key = local_key
        self._protocol_version = protocol_version
        self._device: tinytuya.Device | None = None

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def host(self) -> str:
        return self._host

    async def async_connect(self) -> None:
        self._device = tinytuya.Device(
            dev_id=self._device_id,
            address=self._host,
            local_key=self._local_key,
            version=self._protocol_version,
        )
        self._device.set_socketTimeout(5)

    async def async_set_charge_current(self, amperage: int) -> bool:
        if amperage not in ALLOWED_CURRENTS:
            raise ValueError(f"Current setpoint {amperage}A is not supported.")
        return await self._async_send_command(DP_CURRENT_TARGET, amperage)

    async def async_set_charge_enabled(self, enabled: bool) -> bool:
        return await self._async_send_command(DP_DO_CHARGE, enabled)

    async def async_reboot(self) -> bool:
        # Depending on firmware variants, reboot may accept bool, int, or string payloads.
        for payload in (True, 1, "1"):
            if await self._async_send_command(DP_REBOOT, payload):
                return True
        return False

    async def async_get_metrics(self) -> EVMetrics | None:
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

        metrics_dict = _parse_metrics_json(dps.get(DP_METRICS, "{}"))
        l1_data = metrics_dict.get("L1", [0, 0, 0])
        if not isinstance(l1_data, list) or len(l1_data) < 3:
            l1_data = [0, 0, 0]

        raw_power = l1_data[2] if len(l1_data) > 2 else metrics_dict.get("p", 0)

        return EVMetrics(
            voltage_l1=_coerce_float(l1_data[0]) / 10.0,
            current_l1=_coerce_float(l1_data[1]) / 10.0,
            power_l1=_coerce_float(raw_power) / 10.0,
            temperature=_coerce_float(metrics_dict.get("t", 0)) / 10.0,
            raw_status=str(dps.get(DP_WORK_STATE, "UNKNOWN")),
            do_charge=_coerce_optional_bool(dps.get(DP_DO_CHARGE)),
            current_target=_coerce_optional_int(dps.get(DP_CURRENT_TARGET)),
            max_current_cfg=_coerce_optional_int(dps.get(DP_MAX_CURRENT_CFG)),
        )

    async def _async_send_command(self, dp_id: str, value: Any) -> bool:
        device = self._get_device()
        response: Any = await asyncio.to_thread(device.set_value, dp_id, value)
        if isinstance(response, dict) and "Error" not in response:
            return True
        LOGGER.error("Command rejected for DP %s: %s", dp_id, response)
        return False

    def _get_device(self) -> tinytuya.Device:
        if self._device is None:
            raise RuntimeError("Device client is not initialized. Call async_connect first.")
        return self._device


def _parse_metrics_json(raw_metrics: Any) -> dict[str, Any]:
    if isinstance(raw_metrics, dict):
        return raw_metrics
    if not isinstance(raw_metrics, str):
        return {}

    try:
        decoded: Any = json.loads(raw_metrics)
    except json.JSONDecodeError:
        LOGGER.error("Unable to decode metrics JSON: %s", raw_metrics)
        return {}

    if isinstance(decoded, dict):
        return decoded
    return {}


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
