from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TuyaEVChargerRuntimeData
from .const import DOMAIN
from .entity import TuyaEVChargerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime_data: TuyaEVChargerRuntimeData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TuyaEVChargerChargeSessionSwitch(entry, runtime_data)])


class TuyaEVChargerChargeSessionSwitch(TuyaEVChargerEntity, SwitchEntity):
    _attr_translation_key = "charge_session"
    _attr_icon = "mdi:ev-station"

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(entry=entry, runtime_data=runtime_data)
        self._attr_unique_id = f"{runtime_data.client.device_id}_charge_session"

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data
        if data is None:
            return False
        if data.do_charge is not None:
            return data.do_charge
        return data.raw_status.upper() == "WORKING"

    async def async_turn_on(self, **kwargs: object) -> None:
        if not await self._runtime_data.client.async_set_charge_enabled(True):
            raise HomeAssistantError("Unable to start charging session.")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: object) -> None:
        if not await self._runtime_data.client.async_set_charge_enabled(False):
            raise HomeAssistantError("Unable to stop charging session.")
        await self.coordinator.async_request_refresh()
