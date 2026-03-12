from __future__ import annotations

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TuyaEVChargerRuntimeData
from .const import ALLOWED_CURRENTS
from .entity import TuyaEVChargerEntity
from .tuya_ev_charger import EVMetrics

CURRENT_SETPOINT_DESCRIPTION = NumberEntityDescription(
    key="charge_current",
    translation_key="charge_current",
    icon="mdi:current-ac",
    native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
    native_min_value=float(min(ALLOWED_CURRENTS)),
    native_max_value=float(max(ALLOWED_CURRENTS)),
    native_step=1.0,
    mode=NumberMode.BOX,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime_data: TuyaEVChargerRuntimeData = entry.runtime_data
    async_add_entities([TuyaEVChargerCurrentNumber(entry, runtime_data)])


class TuyaEVChargerCurrentNumber(TuyaEVChargerEntity, NumberEntity):
    entity_description = CURRENT_SETPOINT_DESCRIPTION

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(entry=entry, runtime_data=runtime_data)
        self._attr_unique_id = f"{runtime_data.client.device_id}_charge_current"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if data is None or data.current_target is None:
            return None
        return float(data.current_target)

    @property
    def native_min_value(self) -> float:
        return float(min(self._allowed_currents()))

    @property
    def native_max_value(self) -> float:
        return float(max(self._allowed_currents()))

    async def async_set_native_value(self, value: float) -> None:
        amperage = int(value)
        if float(amperage) != value:
            raise HomeAssistantError("Current setpoint must be an integer.")

        allowed_currents = self._allowed_currents()
        if amperage not in allowed_currents:
            raise HomeAssistantError(
                f"Unsupported current setpoint: {amperage}A (allowed: {allowed_currents})."
            )

        success = await self._runtime_data.client.async_set_charge_current(amperage)
        if not success:
            raise HomeAssistantError("Unable to update current setpoint on charger.")

        await self.coordinator.async_request_refresh()

    def _allowed_currents(self) -> tuple[int, ...]:
        data: EVMetrics | None = self.coordinator.data
        min_current = min(ALLOWED_CURRENTS)
        max_current = max(ALLOWED_CURRENTS)

        if data is not None and data.max_current_cfg is not None:
            max_current = min(max_current, data.max_current_cfg)

        if data is not None and data.adjust_current_options:
            options = tuple(
                sorted(
                    {
                        value
                        for value in data.adjust_current_options
                        if min_current <= value <= max_current
                    }
                )
            )
            if options:
                return options

        return tuple(range(min_current, max_current + 1))
