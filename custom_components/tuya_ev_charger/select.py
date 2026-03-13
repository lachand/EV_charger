from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TuyaEVChargerRuntimeData
from .const import (
    CARD_ROLE_INDEX,
    CARD_ROLE_SURPLUS_PROFILE,
    CONF_SURPLUS_PROFILE,
    DEFAULT_SURPLUS_PROFILE,
    SURPLUS_PROFILES,
)
from .entity import TuyaEVChargerEntity
from .surplus_profiles import apply_surplus_profile, normalize_surplus_profile


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _ = hass
    runtime_data: TuyaEVChargerRuntimeData = entry.runtime_data
    async_add_entities([TuyaEVChargerSurplusProfileSelect(entry, runtime_data)])


class TuyaEVChargerSurplusProfileSelect(TuyaEVChargerEntity, SelectEntity):
    _attr_translation_key = "surplus_profile"
    _attr_icon = "mdi:tune-variant"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(SURPLUS_PROFILES)

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(
            entry=entry,
            runtime_data=runtime_data,
            card_role=CARD_ROLE_SURPLUS_PROFILE,
            card_index=CARD_ROLE_INDEX[CARD_ROLE_SURPLUS_PROFILE],
        )
        self._attr_unique_id = f"{runtime_data.client.device_id}_surplus_profile"

    @property
    def current_option(self) -> str:
        raw = self._entry.options.get(CONF_SURPLUS_PROFILE, DEFAULT_SURPLUS_PROFILE)
        return normalize_surplus_profile(raw)

    async def async_select_option(self, option: str) -> None:
        if option not in SURPLUS_PROFILES:
            raise HomeAssistantError(f"Unsupported surplus profile '{option}'.")
        normalized = normalize_surplus_profile(option)
        if normalized == self.current_option:
            return
        new_options = apply_surplus_profile(dict(self._entry.options), normalized)
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        self.async_write_ha_state()
