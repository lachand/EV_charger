from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TuyaEVChargerRuntimeData
from .const import (
    CONF_SURPLUS_MODE,
    CONF_SURPLUS_MODE_ENABLED,
    DEFAULT_SURPLUS_MODE,
    DEFAULT_SURPLUS_MODE_ENABLED,
    SURPLUS_MODE_CLASSIC,
    SURPLUS_MODE_ZERO_INJECTION,
)
from .entity import TuyaEVChargerEntity

SURPLUS_STRATEGY_OFF = "off"
SURPLUS_STRATEGY_OPTIONS = (
    SURPLUS_STRATEGY_OFF,
    SURPLUS_MODE_CLASSIC,
    SURPLUS_MODE_ZERO_INJECTION,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _ = hass
    runtime_data: TuyaEVChargerRuntimeData = entry.runtime_data
    async_add_entities([TuyaEVChargerSurplusStrategySelect(entry, runtime_data)])


class TuyaEVChargerSurplusStrategySelect(TuyaEVChargerEntity, SelectEntity):
    _attr_translation_key = "surplus_strategy"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:tune-variant"
    _attr_options = list(SURPLUS_STRATEGY_OPTIONS)

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(entry=entry, runtime_data=runtime_data)
        self._attr_unique_id = f"{runtime_data.client.device_id}_surplus_strategy"

    @property
    def current_option(self) -> str:
        if not bool(
            self._entry.options.get(
                CONF_SURPLUS_MODE_ENABLED,
                DEFAULT_SURPLUS_MODE_ENABLED,
            )
        ):
            return SURPLUS_STRATEGY_OFF
        mode = str(self._entry.options.get(CONF_SURPLUS_MODE, DEFAULT_SURPLUS_MODE)).lower()
        if mode in SURPLUS_STRATEGY_OPTIONS:
            return mode
        return DEFAULT_SURPLUS_MODE

    async def async_select_option(self, option: str) -> None:
        if option not in SURPLUS_STRATEGY_OPTIONS:
            raise HomeAssistantError(f"Unsupported surplus strategy: {option}")

        new_options = dict(self._entry.options)
        if option == SURPLUS_STRATEGY_OFF:
            new_options[CONF_SURPLUS_MODE_ENABLED] = False
        else:
            new_options[CONF_SURPLUS_MODE_ENABLED] = True
            new_options[CONF_SURPLUS_MODE] = option

        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        self.async_write_ha_state()
