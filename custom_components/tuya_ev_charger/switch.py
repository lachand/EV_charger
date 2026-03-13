from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TuyaEVChargerRuntimeData
from .const import (
    CONF_SURPLUS_MODE_ENABLED,
    DEFAULT_SURPLUS_MODE_ENABLED,
)
from .entity import TuyaEVChargerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime_data: TuyaEVChargerRuntimeData = entry.runtime_data
    async_add_entities(
        [
            TuyaEVChargerChargeSessionSwitch(entry, runtime_data),
            TuyaEVChargerNfcSwitch(entry, runtime_data),
            TuyaEVChargerSurplusModeSwitch(entry, runtime_data),
        ]
    )


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
        return data.work_state_debug == "WORKING"

    async def async_turn_on(self, **kwargs: object) -> None:
        if not await self._runtime_data.client.async_set_charge_enabled(True):
            raise HomeAssistantError("Unable to start charging session.")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: object) -> None:
        if not await self._runtime_data.client.async_set_charge_enabled(False):
            raise HomeAssistantError("Unable to stop charging session.")
        await self.coordinator.async_request_refresh()


class TuyaEVChargerNfcSwitch(TuyaEVChargerEntity, SwitchEntity):
    _attr_translation_key = "nfc_enabled"
    _attr_icon = "mdi:nfc"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(entry=entry, runtime_data=runtime_data)
        self._attr_unique_id = f"{runtime_data.client.device_id}_nfc_enabled"

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data
        if data is None or data.nfc_enabled is None:
            return False
        return data.nfc_enabled

    async def async_turn_on(self, **kwargs: object) -> None:
        if not await self._runtime_data.client.async_set_nfc_enabled(True):
            raise HomeAssistantError("Unable to enable NFC.")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: object) -> None:
        if not await self._runtime_data.client.async_set_nfc_enabled(False):
            raise HomeAssistantError("Unable to disable NFC.")
        await self.coordinator.async_request_refresh()


class TuyaEVChargerSurplusModeSwitch(TuyaEVChargerEntity, SwitchEntity):
    _attr_translation_key = "surplus_mode"
    _attr_icon = "mdi:solar-power-variant"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(entry=entry, runtime_data=runtime_data)
        self._attr_unique_id = f"{runtime_data.client.device_id}_surplus_mode"

    @property
    def is_on(self) -> bool:
        return bool(
            self._entry.options.get(
                CONF_SURPLUS_MODE_ENABLED,
                DEFAULT_SURPLUS_MODE_ENABLED,
            )
        )

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._async_set_mode(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._async_set_mode(False)

    async def _async_set_mode(self, enabled: bool) -> None:
        if enabled == self.is_on:
            return
        new_options = dict(self._entry.options)
        new_options[CONF_SURPLUS_MODE_ENABLED] = enabled
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        self.async_write_ha_state()
