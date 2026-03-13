from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TuyaEVChargerRuntimeData
from .const import (
    ALLOWED_CURRENTS,
    CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
    CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
    CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    DEFAULT_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
    DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
    DEFAULT_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
)
from .entity import TuyaEVChargerEntity
from .helpers import allowed_currents

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


@dataclass(frozen=True, kw_only=True)
class SurplusOptionNumberDescription(NumberEntityDescription):
    option_key: str
    default_value: int


SURPLUS_OPTION_NUMBER_DESCRIPTIONS: tuple[SurplusOptionNumberDescription, ...] = (
    SurplusOptionNumberDescription(
        key="surplus_battery_soc_high_threshold_pct",
        translation_key="surplus_battery_soc_high_threshold_pct",
        option_key=CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
        default_value=DEFAULT_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
        native_unit_of_measurement=PERCENTAGE,
        native_step=1.0,
        icon="mdi:battery-high",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SurplusOptionNumberDescription(
        key="surplus_battery_soc_low_threshold_pct",
        translation_key="surplus_battery_soc_low_threshold_pct",
        option_key=CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
        default_value=DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
        native_unit_of_measurement=PERCENTAGE,
        native_step=1.0,
        icon="mdi:battery-low",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _ = hass
    runtime_data: TuyaEVChargerRuntimeData = entry.runtime_data
    entities: list[NumberEntity] = [TuyaEVChargerCurrentNumber(entry, runtime_data)]
    entities.extend(
        TuyaEVChargerSurplusOptionNumber(entry, runtime_data, description)
        for description in SURPLUS_OPTION_NUMBER_DESCRIPTIONS
    )
    async_add_entities(entities)


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

        allowed = self._allowed_currents()
        if amperage not in allowed:
            raise HomeAssistantError(
                f"Unsupported current setpoint: {amperage}A (allowed: {allowed})."
            )

        success = await self._runtime_data.client.async_set_charge_current(amperage)
        if not success:
            raise HomeAssistantError("Unable to update current setpoint on charger.")

        await self.coordinator.async_request_refresh()

    def _allowed_currents(self) -> tuple[int, ...]:
        return allowed_currents(self.coordinator.data)


class TuyaEVChargerSurplusOptionNumber(TuyaEVChargerEntity, NumberEntity):
    entity_description: SurplusOptionNumberDescription

    def __init__(
        self,
        entry: ConfigEntry,
        runtime_data: TuyaEVChargerRuntimeData,
        description: SurplusOptionNumberDescription,
    ) -> None:
        super().__init__(entry=entry, runtime_data=runtime_data)
        self.entity_description = description
        self._attr_unique_id = f"{runtime_data.client.device_id}_{description.key}"

    @property
    def native_value(self) -> float:
        high, low = _current_soc_thresholds(self._entry.options)
        if self.entity_description.option_key == CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT:
            return float(high)
        return float(low)

    @property
    def native_min_value(self) -> float:
        return float(MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT)

    @property
    def native_max_value(self) -> float:
        return float(MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT)

    async def async_set_native_value(self, value: float) -> None:
        coerced = int(value)
        if float(coerced) != value:
            raise HomeAssistantError("This value must be an integer.")
        high, low = _current_soc_thresholds(self._entry.options)

        if self.entity_description.option_key == CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT:
            high = max(
                MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT + 1,
                min(MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, coerced),
            )
            if low >= high:
                low = max(MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, high - 1)
        else:
            low = max(
                MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
                min(MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, coerced),
            )
            if low >= high:
                high = min(MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, low + 1)
                if low >= high:
                    low = max(MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, high - 1)

        new_options = dict(self._entry.options)
        new_options[CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT] = high
        new_options[CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT] = low
        # Keep legacy key aligned for backward compatibility.
        new_options[CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT] = high
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        self.async_write_ha_state()


def _legacy_high_threshold_default(options: Mapping[str, object]) -> int:
    return _option_int(
        options,
        CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        DEFAULT_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    )


def _current_soc_thresholds(options: Mapping[str, object]) -> tuple[int, int]:
    high = _option_int(
        options,
        CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
        _legacy_high_threshold_default(options),
        MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    )
    if high <= MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT:
        high = MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT + 1

    low = _option_int(
        options,
        CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
        DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
        MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    )
    if low >= high:
        low = max(MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, high - 1)
    return high, low


def _option_int(
    options: Mapping[str, object],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(options.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))
