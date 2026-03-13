from __future__ import annotations

import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TuyaEVChargerRuntimeData
from .const import (
    ATTR_CARD_INDEX,
    ATTR_CARD_ROLE,
    ATTR_CHARGER_DEVICE_ID,
    ATTR_CHARGER_ENTRY_ID,
    ATTR_CHARGER_TOKEN,
    DOMAIN,
)
from .coordinator import TuyaEVChargerDataUpdateCoordinator


class TuyaEVChargerEntity(CoordinatorEntity[TuyaEVChargerDataUpdateCoordinator]):
    _attr_has_entity_name = True

    def __init__(
        self,
        entry: ConfigEntry,
        runtime_data: TuyaEVChargerRuntimeData,
        *,
        card_role: str | None = None,
        card_index: int | None = None,
    ) -> None:
        super().__init__(runtime_data.coordinator)
        self._entry = entry
        self._runtime_data = runtime_data
        self._card_role = card_role
        self._card_index = card_index
        self._attr_extra_state_attributes = self._technical_state_attributes()

    @property
    def device_info(self) -> DeviceInfo:
        data = self.coordinator.data
        charger_info = data.charger_info if data is not None else {}

        manufacturer = _first_non_empty(
            charger_info,
            ("manufacturer", "brand", "vendor"),
        ) or "Tuya"
        model = _first_non_empty(
            charger_info,
            ("model", "product_model", "device_model", "product"),
        ) or "EV Charger"
        sw_version = _first_non_empty(
            charger_info,
            ("sw_version", "firmware_version", "version"),
        )
        serial_number = _first_non_empty(
            charger_info,
            ("serial_number", "sn", "serial"),
        )
        hw_version = str(data.product_variant) if data and data.product_variant is not None else None

        return DeviceInfo(
            identifiers={(DOMAIN, self._runtime_data.client.device_id)},
            name=self._entry.title,
            manufacturer=manufacturer,
            model=model,
            serial_number=serial_number,
            sw_version=sw_version,
            hw_version=hw_version,
            configuration_url=f"http://{self._runtime_data.client.host}",
        )

    def _technical_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            ATTR_CHARGER_TOKEN: _normalize_token(self._entry.title),
            ATTR_CHARGER_ENTRY_ID: self._entry.entry_id,
            ATTR_CHARGER_DEVICE_ID: self._runtime_data.client.device_id,
        }
        if self._card_role is not None:
            attrs[ATTR_CARD_ROLE] = self._card_role
        if self._card_index is not None:
            attrs[ATTR_CARD_INDEX] = self._card_index
        return attrs

    def _with_technical_attributes(
        self, extra_attributes: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        attrs = self._technical_state_attributes()
        if extra_attributes is not None:
            attrs.update(extra_attributes)
        return attrs


def _first_non_empty(data: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _normalize_token(raw: str) -> str:
    lowered = raw.strip().lower()
    return re.sub(r"[^a-z0-9_]", "_", lowered).strip("_")
