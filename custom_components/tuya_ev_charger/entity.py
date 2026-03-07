from __future__ import annotations

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
        self._runtime_data = runtime_data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime_data.client.device_id)},
            name=entry.title,
            manufacturer="Tuya",
            model="EV Charger",
            configuration_url=f"http://{runtime_data.client.host}",
        )
