from __future__ import annotations

import asyncio
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TuyaEVChargerRuntimeData
from .const import DOMAIN
from .entity import TuyaEVChargerEntity

LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime_data: TuyaEVChargerRuntimeData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TuyaEVChargerRebootButton(entry, runtime_data)])


class TuyaEVChargerRebootButton(TuyaEVChargerEntity, ButtonEntity):
    _attr_translation_key = "reboot_charger"
    _attr_icon = "mdi:restart-alert"

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(entry=entry, runtime_data=runtime_data)
        self._attr_unique_id = f"{runtime_data.client.device_id}_reboot_charger"

    async def async_press(self) -> None:
        success = await self._runtime_data.client.async_reboot()
        if not success:
            raise HomeAssistantError("Unable to send reboot command to charger.")

        # The charger is expected to be unavailable for a short time right after reboot.
        await asyncio.sleep(3)
        try:
            await self.coordinator.async_request_refresh()
        except Exception as err:
            LOGGER.debug("Refresh after reboot failed while charger restarts: %s", err)
