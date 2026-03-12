from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TuyaEVChargerRuntimeData
from .const import DOMAIN
from .coordinator import TuyaEVChargerDataUpdateCoordinator


class TuyaEVChargerEntity(CoordinatorEntity[TuyaEVChargerDataUpdateCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(runtime_data.coordinator)
        self._entry = entry
        self._runtime_data = runtime_data

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


def _first_non_empty(data: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None
