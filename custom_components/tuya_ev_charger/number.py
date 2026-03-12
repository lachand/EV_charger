from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TuyaEVChargerRuntimeData
from .const import (
    ALLOWED_CURRENTS,
    CONF_SURPLUS_ADJUST_DOWN_COOLDOWN_S,
    CONF_SURPLUS_ADJUST_UP_COOLDOWN_S,
    CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    CONF_SURPLUS_LINE_VOLTAGE,
    CONF_SURPLUS_MAX_SESSION_DURATION_MIN,
    CONF_SURPLUS_MAX_SESSION_ENERGY_KWH,
    CONF_SURPLUS_MIN_RUN_TIME_S,
    CONF_SURPLUS_RAMP_STEP_A,
    CONF_SURPLUS_START_DELAY_S,
    CONF_SURPLUS_START_THRESHOLD_W,
    CONF_SURPLUS_STOP_DELAY_S,
    CONF_SURPLUS_STOP_THRESHOLD_W,
    CONF_SURPLUS_TARGET_OFFSET_W,
    CONF_TARIFF_MAX_PRICE_EUR_KWH,
    DEFAULT_SURPLUS_ADJUST_DOWN_COOLDOWN_S,
    DEFAULT_SURPLUS_ADJUST_UP_COOLDOWN_S,
    DEFAULT_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    DEFAULT_SURPLUS_LINE_VOLTAGE,
    DEFAULT_SURPLUS_MAX_SESSION_DURATION_MIN,
    DEFAULT_SURPLUS_MAX_SESSION_ENERGY_KWH,
    DEFAULT_SURPLUS_MIN_RUN_TIME_S,
    DEFAULT_SURPLUS_RAMP_STEP_A,
    DEFAULT_SURPLUS_START_DELAY_S,
    DEFAULT_SURPLUS_START_THRESHOLD_W,
    DEFAULT_SURPLUS_STOP_DELAY_S,
    DEFAULT_SURPLUS_STOP_THRESHOLD_W,
    DEFAULT_SURPLUS_TARGET_OFFSET_W,
    DEFAULT_TARIFF_MAX_PRICE_EUR_KWH,
    MAX_SURPLUS_DELAY_S,
    MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MAX_SURPLUS_LINE_VOLTAGE,
    MAX_SURPLUS_SESSION_DURATION_MIN,
    MAX_SURPLUS_SESSION_ENERGY_KWH,
    MAX_SURPLUS_RAMP_STEP_A,
    MAX_SURPLUS_TARGET_OFFSET_W,
    MAX_SURPLUS_THRESHOLD_W,
    MAX_TARIFF_PRICE_EUR_KWH,
    MIN_SURPLUS_DELAY_S,
    MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MIN_SURPLUS_LINE_VOLTAGE,
    MIN_SURPLUS_SESSION_DURATION_MIN,
    MIN_SURPLUS_SESSION_ENERGY_KWH,
    MIN_SURPLUS_RAMP_STEP_A,
    MIN_SURPLUS_TARGET_OFFSET_W,
    MIN_SURPLUS_THRESHOLD_W,
    MIN_TARIFF_PRICE_EUR_KWH,
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
    default_value: float
    minimum: float
    maximum: float
    enforce_int: bool = True


SURPLUS_OPTION_NUMBER_DESCRIPTIONS: tuple[SurplusOptionNumberDescription, ...] = (
    SurplusOptionNumberDescription(
        key="surplus_start_threshold_w",
        translation_key="surplus_start_threshold_w",
        option_key=CONF_SURPLUS_START_THRESHOLD_W,
        default_value=DEFAULT_SURPLUS_START_THRESHOLD_W,
        minimum=MIN_SURPLUS_THRESHOLD_W,
        maximum=MAX_SURPLUS_THRESHOLD_W,
        native_unit_of_measurement=UnitOfPower.WATT,
        native_step=1.0,
        icon="mdi:play-speed",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SurplusOptionNumberDescription(
        key="surplus_stop_threshold_w",
        translation_key="surplus_stop_threshold_w",
        option_key=CONF_SURPLUS_STOP_THRESHOLD_W,
        default_value=DEFAULT_SURPLUS_STOP_THRESHOLD_W,
        minimum=MIN_SURPLUS_THRESHOLD_W,
        maximum=MAX_SURPLUS_THRESHOLD_W,
        native_unit_of_measurement=UnitOfPower.WATT,
        native_step=1.0,
        icon="mdi:stop-circle-outline",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SurplusOptionNumberDescription(
        key="surplus_target_offset_w",
        translation_key="surplus_target_offset_w",
        option_key=CONF_SURPLUS_TARGET_OFFSET_W,
        default_value=DEFAULT_SURPLUS_TARGET_OFFSET_W,
        minimum=MIN_SURPLUS_TARGET_OFFSET_W,
        maximum=MAX_SURPLUS_TARGET_OFFSET_W,
        native_unit_of_measurement=UnitOfPower.WATT,
        native_step=1.0,
        icon="mdi:plus-minus-variant",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SurplusOptionNumberDescription(
        key="surplus_start_delay_s",
        translation_key="surplus_start_delay_s",
        option_key=CONF_SURPLUS_START_DELAY_S,
        default_value=DEFAULT_SURPLUS_START_DELAY_S,
        minimum=MIN_SURPLUS_DELAY_S,
        maximum=MAX_SURPLUS_DELAY_S,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_step=1.0,
        icon="mdi:timer-play-outline",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SurplusOptionNumberDescription(
        key="surplus_stop_delay_s",
        translation_key="surplus_stop_delay_s",
        option_key=CONF_SURPLUS_STOP_DELAY_S,
        default_value=DEFAULT_SURPLUS_STOP_DELAY_S,
        minimum=MIN_SURPLUS_DELAY_S,
        maximum=MAX_SURPLUS_DELAY_S,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_step=1.0,
        icon="mdi:timer-stop-outline",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SurplusOptionNumberDescription(
        key="surplus_adjust_up_cooldown_s",
        translation_key="surplus_adjust_up_cooldown_s",
        option_key=CONF_SURPLUS_ADJUST_UP_COOLDOWN_S,
        default_value=DEFAULT_SURPLUS_ADJUST_UP_COOLDOWN_S,
        minimum=MIN_SURPLUS_DELAY_S,
        maximum=MAX_SURPLUS_DELAY_S,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_step=1.0,
        icon="mdi:arrow-up-bold-circle-outline",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SurplusOptionNumberDescription(
        key="surplus_adjust_down_cooldown_s",
        translation_key="surplus_adjust_down_cooldown_s",
        option_key=CONF_SURPLUS_ADJUST_DOWN_COOLDOWN_S,
        default_value=DEFAULT_SURPLUS_ADJUST_DOWN_COOLDOWN_S,
        minimum=MIN_SURPLUS_DELAY_S,
        maximum=MAX_SURPLUS_DELAY_S,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_step=1.0,
        icon="mdi:arrow-down-bold-circle-outline",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SurplusOptionNumberDescription(
        key="surplus_ramp_step_a",
        translation_key="surplus_ramp_step_a",
        option_key=CONF_SURPLUS_RAMP_STEP_A,
        default_value=DEFAULT_SURPLUS_RAMP_STEP_A,
        minimum=MIN_SURPLUS_RAMP_STEP_A,
        maximum=MAX_SURPLUS_RAMP_STEP_A,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        native_step=1.0,
        icon="mdi:stairs",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SurplusOptionNumberDescription(
        key="surplus_battery_soc_threshold_pct",
        translation_key="surplus_battery_soc_threshold_pct",
        option_key=CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        default_value=DEFAULT_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        minimum=MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        maximum=MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        native_unit_of_measurement=PERCENTAGE,
        native_step=1.0,
        icon="mdi:battery-heart-variant",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SurplusOptionNumberDescription(
        key="surplus_line_voltage",
        translation_key="surplus_line_voltage",
        option_key=CONF_SURPLUS_LINE_VOLTAGE,
        default_value=DEFAULT_SURPLUS_LINE_VOLTAGE,
        minimum=MIN_SURPLUS_LINE_VOLTAGE,
        maximum=MAX_SURPLUS_LINE_VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        native_step=1.0,
        icon="mdi:sine-wave",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SurplusOptionNumberDescription(
        key="surplus_min_run_time_s",
        translation_key="surplus_min_run_time_s",
        option_key=CONF_SURPLUS_MIN_RUN_TIME_S,
        default_value=DEFAULT_SURPLUS_MIN_RUN_TIME_S,
        minimum=MIN_SURPLUS_DELAY_S,
        maximum=MAX_SURPLUS_DELAY_S,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_step=1.0,
        icon="mdi:timer-sand",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SurplusOptionNumberDescription(
        key="surplus_max_session_duration_min",
        translation_key="surplus_max_session_duration_min",
        option_key=CONF_SURPLUS_MAX_SESSION_DURATION_MIN,
        default_value=DEFAULT_SURPLUS_MAX_SESSION_DURATION_MIN,
        minimum=MIN_SURPLUS_SESSION_DURATION_MIN,
        maximum=MAX_SURPLUS_SESSION_DURATION_MIN,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_step=1.0,
        icon="mdi:timer-cog-outline",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SurplusOptionNumberDescription(
        key="surplus_max_session_energy_kwh",
        translation_key="surplus_max_session_energy_kwh",
        option_key=CONF_SURPLUS_MAX_SESSION_ENERGY_KWH,
        default_value=DEFAULT_SURPLUS_MAX_SESSION_ENERGY_KWH,
        minimum=MIN_SURPLUS_SESSION_ENERGY_KWH,
        maximum=MAX_SURPLUS_SESSION_ENERGY_KWH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        native_step=0.1,
        icon="mdi:lightning-bolt-circle",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        enforce_int=False,
    ),
    SurplusOptionNumberDescription(
        key="tariff_max_price_eur_kwh",
        translation_key="tariff_max_price_eur_kwh",
        option_key=CONF_TARIFF_MAX_PRICE_EUR_KWH,
        default_value=DEFAULT_TARIFF_MAX_PRICE_EUR_KWH,
        minimum=MIN_TARIFF_PRICE_EUR_KWH,
        maximum=MAX_TARIFF_PRICE_EUR_KWH,
        native_unit_of_measurement="EUR/kWh",
        native_step=0.01,
        icon="mdi:cash-multiple",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        enforce_int=False,
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
        value = _option_float(
            self._entry.options,
            self.entity_description.option_key,
            self.entity_description.default_value,
            self.entity_description.minimum,
            self.entity_description.maximum,
        )
        return float(value)

    @property
    def native_min_value(self) -> float:
        return float(self.entity_description.minimum)

    @property
    def native_max_value(self) -> float:
        return float(self.entity_description.maximum)

    async def async_set_native_value(self, value: float) -> None:
        if self.entity_description.enforce_int:
            coerced: float = int(value)
            if float(coerced) != value:
                raise HomeAssistantError("This value must be an integer.")
        else:
            coerced = float(value)

        if coerced < self.entity_description.minimum or coerced > self.entity_description.maximum:
            raise HomeAssistantError(
                f"Value must be between {self.entity_description.minimum} and "
                f"{self.entity_description.maximum}."
            )

        self._validate_hysteresis(coerced)

        new_options = dict(self._entry.options)
        if self.entity_description.enforce_int:
            new_options[self.entity_description.option_key] = int(coerced)
        else:
            new_options[self.entity_description.option_key] = round(float(coerced), 4)
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        self.async_write_ha_state()

    def _validate_hysteresis(self, new_value: float) -> None:
        key = self.entity_description.option_key
        start_threshold = int(
            _option_float(
            self._entry.options,
            CONF_SURPLUS_START_THRESHOLD_W,
            DEFAULT_SURPLUS_START_THRESHOLD_W,
            MIN_SURPLUS_THRESHOLD_W,
            MAX_SURPLUS_THRESHOLD_W,
            )
        )
        stop_threshold = int(
            _option_float(
            self._entry.options,
            CONF_SURPLUS_STOP_THRESHOLD_W,
            DEFAULT_SURPLUS_STOP_THRESHOLD_W,
            MIN_SURPLUS_THRESHOLD_W,
            MAX_SURPLUS_THRESHOLD_W,
            )
        )
        if key == CONF_SURPLUS_START_THRESHOLD_W:
            start_threshold = int(new_value)
        if key == CONF_SURPLUS_STOP_THRESHOLD_W:
            stop_threshold = int(new_value)
        if start_threshold <= stop_threshold:
            raise HomeAssistantError(
                "Start threshold must be strictly greater than stop threshold."
            )


def _option_float(
    options: Mapping[str, object],
    key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(options.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))
