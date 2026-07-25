from __future__ import annotations

from typing import ClassVar

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
    CONF_VEHICLES,
    DEFAULT_SURPLUS_PROFILE,
    DEFAULT_VEHICLES,
    SURPLUS_PROFILES,
)
from .entity import TuyaEVChargerEntity
from .surplus_profiles import apply_surplus_profile, normalize_surplus_profile
from .tuya_ev_charger import PLUG_IN_ACTION_OPTIONS
from .vehicles import configured_vehicles

PARALLEL_UPDATES = 1  # The charger accepts one local connection; writes are serialised.


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _ = hass
    runtime_data: TuyaEVChargerRuntimeData = entry.runtime_data
    entities: list[SelectEntity] = [
        TuyaEVChargerSurplusProfileSelect(entry, runtime_data),
        TuyaEVChargerPlugInActionSelect(entry, runtime_data),
    ]

    # Only offer the vehicle picker once the user has named their vehicles.
    if configured_vehicles(entry.options.get(CONF_VEHICLES, DEFAULT_VEHICLES)):
        entities.append(TuyaEVChargerVehicleSelect(entry, runtime_data))
    async_add_entities(entities)


class TuyaEVChargerPlugInActionSelect(TuyaEVChargerEntity, SelectEntity):
    """What the charger does when a cable is plugged in (DP 154)."""

    _attr_translation_key = "plug_in_action"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options: ClassVar[list[str]] = list(PLUG_IN_ACTION_OPTIONS)

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(entry=entry, runtime_data=runtime_data)
        self._attr_unique_id = f"{runtime_data.client.device_id}_plug_in_action"

    @property
    def available(self) -> bool:
        # Not every firmware reports DP 154.
        data = self.coordinator.data
        return super().available and data is not None and data.plug_in_action is not None

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data
        return data.plug_in_action if data is not None else None

    async def async_select_option(self, option: str) -> None:
        # Skip a write the charger would only beep at.
        if option == self.current_option:
            return
        if not await self._runtime_data.client.async_set_plug_in_action(option):
            raise HomeAssistantError("Unable to update the plug-in action.")
        await self.coordinator.async_request_refresh()


class TuyaEVChargerVehicleSelect(TuyaEVChargerEntity, SelectEntity):
    """Picks which vehicle the charger's energy should be attributed to."""

    _attr_translation_key = "active_vehicle"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(entry=entry, runtime_data=runtime_data)
        self._attr_unique_id = f"{runtime_data.client.device_id}_active_vehicle"

    @property
    def options(self) -> list[str]:
        return configured_vehicles(self._entry.options.get(CONF_VEHICLES, DEFAULT_VEHICLES))

    @property
    def current_option(self) -> str | None:
        tracker = self._runtime_data.vehicle_tracker
        if tracker is None:
            return None
        active = tracker.active_vehicle
        # Fall back to the first configured vehicle when the stored one was renamed.
        if active in self.options:
            return active
        return self.options[0] if self.options else None

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise HomeAssistantError(f"Unknown vehicle '{option}'.")
        tracker = self._runtime_data.vehicle_tracker
        if tracker is None:
            raise HomeAssistantError("Vehicle tracking is unavailable.")
        await tracker.async_set_active_vehicle(option)
        self.async_write_ha_state()


class TuyaEVChargerSurplusProfileSelect(TuyaEVChargerEntity, SelectEntity):
    _attr_translation_key = "surplus_profile"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options: ClassVar[list[str]] = list(SURPLUS_PROFILES)

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
