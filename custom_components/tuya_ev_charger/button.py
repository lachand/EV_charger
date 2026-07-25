from __future__ import annotations

import asyncio
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TuyaEVChargerRuntimeData
from .const import CARD_ROLE_INDEX, CARD_ROLE_REBOOT, WORK_STATE_READY_TO_CHARGE
from .entity import TuyaEVChargerEntity

PARALLEL_UPDATES = 1  # The charger accepts one local connection; writes are serialised.

LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime_data: TuyaEVChargerRuntimeData = entry.runtime_data
    async_add_entities(
        [
            TuyaEVChargerRebootButton(entry, runtime_data),
            TuyaEVChargerReadyToChargeButton(entry, runtime_data),
        ]
    )


class TuyaEVChargerReadyToChargeButton(TuyaEVChargerEntity, ButtonEntity):
    """Puts the charger back into "ready to charge" (operating state 200).

    Some firmwares keep reporting the last power value after a session ends
    until the charger is returned to this state; the Tuya app exposes it but
    Home Assistant had no equivalent. DP 101 is writable per tuya_local's config
    for this product.
    """

    _attr_translation_key = "ready_to_charge"

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(entry=entry, runtime_data=runtime_data)
        self._attr_unique_id = f"{runtime_data.client.device_id}_ready_to_charge"

    async def async_press(self) -> None:
        if not await self._runtime_data.client.async_set_work_state(WORK_STATE_READY_TO_CHARGE):
            raise HomeAssistantError("Unable to set the charger to ready-to-charge.")
        await self.coordinator.async_request_refresh()


class TuyaEVChargerRebootButton(TuyaEVChargerEntity, ButtonEntity):
    _attr_translation_key = "reboot_charger"

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(
            entry=entry,
            runtime_data=runtime_data,
            card_role=CARD_ROLE_REBOOT,
            card_index=CARD_ROLE_INDEX[CARD_ROLE_REBOOT],
        )
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
