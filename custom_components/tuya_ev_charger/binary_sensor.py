from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TuyaEVChargerRuntimeData
from .entity import TuyaEVChargerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _ = hass
    runtime_data: TuyaEVChargerRuntimeData = entry.runtime_data
    async_add_entities(
        [TuyaEVChargerSurplusRegulationActiveBinarySensor(entry, runtime_data)]
    )


class TuyaEVChargerSurplusRegulationActiveBinarySensor(
    TuyaEVChargerEntity,
    BinarySensorEntity,
):
    _attr_translation_key = "surplus_regulation_active"
    _attr_icon = "mdi:solar-power-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(entry=entry, runtime_data=runtime_data)
        self._attr_unique_id = f"{runtime_data.client.device_id}_surplus_regulation_active"
        self._unsub_listener: Callable[[], None] | None = None

    @property
    def is_on(self) -> bool:
        controller = self._runtime_data.solar_surplus_controller
        if controller is None:
            return False
        return controller.snapshot.regulation_active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        controller = self._runtime_data.solar_surplus_controller
        if controller is None:
            return {}
        snapshot = controller.snapshot
        return {
            "mode_enabled": snapshot.mode_enabled,
            "paused": snapshot.paused,
            "force_charge_active": snapshot.force_charge_active,
            "last_decision_reason": snapshot.last_decision_reason,
            "last_start_reason": snapshot.last_start_reason,
            "last_stop_reason": snapshot.last_stop_reason,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        controller = self._runtime_data.solar_surplus_controller
        if controller is None:
            return

        @callback
        def _handle_update() -> None:
            self.async_write_ha_state()

        self._unsub_listener = controller.async_add_update_listener(_handle_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_listener is not None:
            self._unsub_listener()
            self._unsub_listener = None
        await super().async_will_remove_from_hass()
