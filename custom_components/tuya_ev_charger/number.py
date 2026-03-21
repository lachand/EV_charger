from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TuyaEVChargerRuntimeData
from .const import (
    ALLOWED_CURRENTS,
    CARD_ROLE_CHARGE_CURRENT,
    CARD_ROLE_INDEX,
    CARD_ROLE_SURPLUS_START_THRESHOLD,
    CARD_ROLE_SURPLUS_STOP_THRESHOLD,
    CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    CONF_SURPLUS_START_THRESHOLD_W,
    CONF_SURPLUS_STOP_THRESHOLD_W,
    CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
    CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
    CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    DEFAULT_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    DEFAULT_SURPLUS_START_THRESHOLD_W,
    DEFAULT_SURPLUS_STOP_THRESHOLD_W,
    DEFAULT_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
    DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
    DEFAULT_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MAX_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    MAX_SURPLUS_THRESHOLD_W,
    MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MIN_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    MIN_SURPLUS_THRESHOLD_W,
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
    min_value: int
    max_value: int


SURPLUS_OPTION_NUMBER_DESCRIPTIONS: tuple[SurplusOptionNumberDescription, ...] = (
    SurplusOptionNumberDescription(
        key="surplus_battery_soc_high_threshold_pct",
        translation_key="surplus_battery_soc_high_threshold_pct",
        option_key=CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
        default_value=DEFAULT_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
        min_value=MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        max_value=MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
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
        min_value=MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        max_value=MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        native_unit_of_measurement=PERCENTAGE,
        native_step=1.0,
        icon="mdi:battery-low",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SurplusOptionNumberDescription(
        key="surplus_start_threshold_w",
        translation_key="surplus_start_threshold_w",
        option_key=CONF_SURPLUS_START_THRESHOLD_W,
        default_value=DEFAULT_SURPLUS_START_THRESHOLD_W,
        min_value=MIN_SURPLUS_THRESHOLD_W,
        max_value=MAX_SURPLUS_THRESHOLD_W,
        native_unit_of_measurement=UnitOfPower.WATT,
        native_step=1.0,
        icon="mdi:solar-power",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SurplusOptionNumberDescription(
        key="surplus_stop_threshold_w",
        translation_key="surplus_stop_threshold_w",
        option_key=CONF_SURPLUS_STOP_THRESHOLD_W,
        default_value=DEFAULT_SURPLUS_STOP_THRESHOLD_W,
        min_value=MIN_SURPLUS_THRESHOLD_W,
        max_value=MAX_SURPLUS_THRESHOLD_W,
        native_unit_of_measurement=UnitOfPower.WATT,
        native_step=1.0,
        icon="mdi:solar-power-variant-outline",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SurplusOptionNumberDescription(
        key="surplus_max_battery_discharge_for_ev_w",
        translation_key="surplus_max_battery_discharge_for_ev_w",
        option_key=CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        default_value=DEFAULT_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        min_value=MIN_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        max_value=MAX_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        native_unit_of_measurement=UnitOfPower.WATT,
        native_step=1.0,
        icon="mdi:battery-arrow-down",
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
        super().__init__(
            entry=entry,
            runtime_data=runtime_data,
            card_role=CARD_ROLE_CHARGE_CURRENT,
            card_index=CARD_ROLE_INDEX[CARD_ROLE_CHARGE_CURRENT],
        )
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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._with_technical_attributes(
            {"allowed_currents": list(self._allowed_currents())}
        )

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
        _role_by_key: dict[str, str] = {
            "surplus_start_threshold_w": CARD_ROLE_SURPLUS_START_THRESHOLD,
            "surplus_stop_threshold_w": CARD_ROLE_SURPLUS_STOP_THRESHOLD,
        }
        card_role = _role_by_key.get(description.key)
        super().__init__(
            entry=entry,
            runtime_data=runtime_data,
            card_role=card_role,
            card_index=CARD_ROLE_INDEX[card_role] if card_role is not None else None,
        )
        self.entity_description = description
        self._attr_unique_id = f"{runtime_data.client.device_id}_{description.key}"

    @property
    def native_value(self) -> float:
        high, low = _current_soc_thresholds(self._entry.options)
        if self.entity_description.option_key == CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT:
            return float(high)
        if self.entity_description.option_key == CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT:
            return float(low)

        start_threshold_w, stop_threshold_w = _current_surplus_power_thresholds(self._entry.options)
        if self.entity_description.option_key == CONF_SURPLUS_START_THRESHOLD_W:
            return float(start_threshold_w)
        if self.entity_description.option_key == CONF_SURPLUS_STOP_THRESHOLD_W:
            return float(stop_threshold_w)

        return float(
            _option_int(
                self._entry.options,
                self.entity_description.option_key,
                self.entity_description.default_value,
                self.entity_description.min_value,
                self.entity_description.max_value,
            )
        )

    @property
    def native_min_value(self) -> float:
        return float(self.entity_description.min_value)

    @property
    def native_max_value(self) -> float:
        return float(self.entity_description.max_value)

    async def async_set_native_value(self, value: float) -> None:
        coerced = int(value)
        if float(coerced) != value:
            raise HomeAssistantError("This value must be an integer.")
        high, low = _current_soc_thresholds(self._entry.options)
        start_threshold_w, stop_threshold_w = _current_surplus_power_thresholds(self._entry.options)
        clamped = max(
            self.entity_description.min_value,
            min(self.entity_description.max_value, coerced),
        )

        if self.entity_description.option_key == CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT:
            high = max(
                MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT + 1,
                min(MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, clamped),
            )
            if low >= high:
                low = max(MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, high - 1)
        elif self.entity_description.option_key == CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT:
            low = max(
                MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
                min(MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, clamped),
            )
            if low >= high:
                high = min(MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, low + 1)
                if low >= high:
                    low = max(MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, high - 1)
        elif self.entity_description.option_key == CONF_SURPLUS_START_THRESHOLD_W:
            start_threshold_w = clamped
            if stop_threshold_w > start_threshold_w:
                stop_threshold_w = start_threshold_w
        elif self.entity_description.option_key == CONF_SURPLUS_STOP_THRESHOLD_W:
            stop_threshold_w = min(clamped, start_threshold_w)

        new_options = dict(self._entry.options)
        new_options[CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT] = high
        new_options[CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT] = low
        new_options[CONF_SURPLUS_START_THRESHOLD_W] = start_threshold_w
        new_options[CONF_SURPLUS_STOP_THRESHOLD_W] = stop_threshold_w
        if (
            self.entity_description.option_key
            == CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W
        ):
            new_options[CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W] = clamped
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


def _current_surplus_power_thresholds(options: Mapping[str, object]) -> tuple[int, int]:
    start_threshold_w = _option_int(
        options,
        CONF_SURPLUS_START_THRESHOLD_W,
        DEFAULT_SURPLUS_START_THRESHOLD_W,
        MIN_SURPLUS_THRESHOLD_W,
        MAX_SURPLUS_THRESHOLD_W,
    )
    stop_threshold_w = _option_int(
        options,
        CONF_SURPLUS_STOP_THRESHOLD_W,
        DEFAULT_SURPLUS_STOP_THRESHOLD_W,
        MIN_SURPLUS_THRESHOLD_W,
        MAX_SURPLUS_THRESHOLD_W,
    )
    if stop_threshold_w > start_threshold_w:
        stop_threshold_w = start_threshold_w
    return start_threshold_w, stop_threshold_w


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
