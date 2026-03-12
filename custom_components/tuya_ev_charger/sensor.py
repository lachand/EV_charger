from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TuyaEVChargerRuntimeData
from .entity import TuyaEVChargerEntity
from .solar_surplus import SolarSurplusSnapshot
from .tuya_ev_charger import EVMetrics


@dataclass(frozen=True, kw_only=True)
class TuyaEVChargerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[EVMetrics], float | int | str | None]


@dataclass(frozen=True, kw_only=True)
class TuyaEVChargerSurplusSensorDescription(SensorEntityDescription):
    value_fn: Callable[[SolarSurplusSnapshot], float | int | str | None]


SENSOR_DESCRIPTIONS: tuple[TuyaEVChargerSensorDescription, ...] = (
    TuyaEVChargerSensorDescription(
        key="voltage_l1",
        translation_key="voltage_l1",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.VOLTAGE,
        suggested_display_precision=1,
        value_fn=lambda data: data.voltage_l1,
    ),
    TuyaEVChargerSensorDescription(
        key="current_l1",
        translation_key="current_l1",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.CURRENT,
        suggested_display_precision=1,
        value_fn=lambda data: data.current_l1,
    ),
    TuyaEVChargerSensorDescription(
        key="power_l1",
        translation_key="power_l1",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
        suggested_display_precision=2,
        value_fn=lambda data: data.power_l1,
    ),
    TuyaEVChargerSensorDescription(
        key="temperature",
        translation_key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.TEMPERATURE,
        suggested_display_precision=1,
        value_fn=lambda data: data.temperature,
    ),
    TuyaEVChargerSensorDescription(
        key="work_state",
        translation_key="work_state",
        icon="mdi:counter",
        value_fn=lambda data: data.work_state,
    ),
    TuyaEVChargerSensorDescription(
        key="work_state_debug",
        translation_key="work_state_debug",
        icon="mdi:state-machine",
        value_fn=lambda data: data.work_state_debug,
    ),
    TuyaEVChargerSensorDescription(
        key="downcounter",
        translation_key="downcounter",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-outline",
        value_fn=lambda data: data.downcounter,
    ),
    TuyaEVChargerSensorDescription(
        key="selftest",
        translation_key="selftest",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:check-decagram-outline",
        value_fn=lambda data: data.selftest,
    ),
    TuyaEVChargerSensorDescription(
        key="alarm",
        translation_key="alarm",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:alert-circle-outline",
        value_fn=lambda data: data.alarm,
    ),
    TuyaEVChargerSensorDescription(
        key="charge_history",
        translation_key="charge_history",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:history",
        value_fn=lambda data: data.charge_history,
    ),
    TuyaEVChargerSensorDescription(
        key="charge_history_timestamp",
        translation_key="charge_history_timestamp",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:calendar-clock",
        value_fn=lambda data: data.charge_history_timestamp,
    ),
    TuyaEVChargerSensorDescription(
        key="charge_history_start_time",
        translation_key="charge_history_start_time",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:play-circle-outline",
        value_fn=lambda data: data.charge_history_start_time,
    ),
    TuyaEVChargerSensorDescription(
        key="charge_history_end_time",
        translation_key="charge_history_end_time",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:stop-circle-outline",
        value_fn=lambda data: data.charge_history_end_time,
    ),
    TuyaEVChargerSensorDescription(
        key="charge_history_duration_s",
        translation_key="charge_history_duration_s",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:timer-outline",
        value_fn=lambda data: data.charge_history_duration_s,
    ),
    TuyaEVChargerSensorDescription(
        key="charge_history_raw_c",
        translation_key="charge_history_raw_c",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:sigma",
        suggested_display_precision=2,
        value_fn=lambda data: data.charge_history_raw_c,
    ),
    TuyaEVChargerSensorDescription(
        key="adjust_current_options",
        translation_key="adjust_current_options",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:format-list-numbered",
        value_fn=lambda data: (
            ",".join(str(value) for value in data.adjust_current_options)
            if data.adjust_current_options
            else None
        ),
    ),
    TuyaEVChargerSensorDescription(
        key="product_variant",
        translation_key="product_variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:identifier",
        value_fn=lambda data: data.product_variant,
    ),
    TuyaEVChargerSensorDescription(
        key="dp_num",
        translation_key="dp_num",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:counter",
        value_fn=lambda data: data.dp_num,
    ),
)

SURPLUS_SENSOR_DESCRIPTIONS: tuple[TuyaEVChargerSurplusSensorDescription, ...] = (
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_available_power",
        translation_key="surplus_available_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:solar-power",
        value_fn=lambda snapshot: (
            round(snapshot.available_surplus_w, 0)
            if snapshot.available_surplus_w is not None
            else None
        ),
    ),
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_target_current",
        translation_key="surplus_target_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.CURRENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:current-ac",
        value_fn=lambda snapshot: snapshot.target_current_a,
    ),
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_forecast_sensor_power",
        translation_key="surplus_forecast_sensor_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:weather-partly-cloudy",
        value_fn=lambda snapshot: (
            round(snapshot.forecast_sensor_w, 0)
            if snapshot.forecast_sensor_w is not None
            else None
        ),
    ),
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_forecast_blended_power",
        translation_key="surplus_forecast_blended_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chart-bell-curve-cumulative",
        value_fn=lambda snapshot: (
            round(snapshot.forecast_blended_surplus_w, 0)
            if snapshot.forecast_blended_surplus_w is not None
            else None
        ),
    ),
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_last_start_reason",
        translation_key="surplus_last_start_reason",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:play-circle-outline",
        value_fn=lambda snapshot: snapshot.last_start_reason,
    ),
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_last_stop_reason",
        translation_key="surplus_last_stop_reason",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:stop-circle-outline",
        value_fn=lambda snapshot: snapshot.last_stop_reason,
    ),
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_last_decision_reason",
        translation_key="surplus_last_decision_reason",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:comment-alert-outline",
        value_fn=lambda snapshot: snapshot.last_decision_reason,
    ),
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_session_energy",
        translation_key="surplus_session_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-charging-medium",
        value_fn=lambda snapshot: snapshot.session_energy_kwh,
    ),
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_dry_run_action",
        translation_key="surplus_dry_run_action",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:test-tube",
        value_fn=lambda snapshot: snapshot.dry_run_action,
    ),
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_dry_run_reason",
        translation_key="surplus_dry_run_reason",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:information-outline",
        value_fn=lambda snapshot: snapshot.dry_run_reason,
    ),
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_dry_run_target_current",
        translation_key="surplus_dry_run_target_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.CURRENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:current-ac",
        value_fn=lambda snapshot: snapshot.dry_run_target_current_a,
    ),
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_departure_required_current",
        translation_key="surplus_departure_required_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.CURRENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:clock-check-outline",
        value_fn=lambda snapshot: snapshot.departure_required_current_a,
    ),
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_departure_remaining_energy",
        translation_key="surplus_departure_remaining_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-clock-outline",
        value_fn=lambda snapshot: snapshot.departure_remaining_energy_kwh,
    ),
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_energy_today",
        translation_key="surplus_energy_today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:calendar-today",
        value_fn=lambda snapshot: snapshot.energy_today_kwh,
    ),
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_energy_week",
        translation_key="surplus_energy_week",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=3,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:calendar-week",
        value_fn=lambda snapshot: snapshot.energy_week_kwh,
    ),
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_efficiency_today",
        translation_key="surplus_efficiency_today",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chart-donut",
        value_fn=lambda snapshot: snapshot.surplus_efficiency_today_pct,
    ),
    TuyaEVChargerSurplusSensorDescription(
        key="surplus_efficiency_week",
        translation_key="surplus_efficiency_week",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chart-donut-variant",
        value_fn=lambda snapshot: snapshot.surplus_efficiency_week_pct,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _ = hass
    runtime_data: TuyaEVChargerRuntimeData = entry.runtime_data
    entities: list[SensorEntity] = [
        TuyaEVChargerSensor(entry, runtime_data, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    entities.extend(
        TuyaEVChargerSurplusSensor(entry, runtime_data, description)
        for description in SURPLUS_SENSOR_DESCRIPTIONS
    )
    async_add_entities(entities)


class TuyaEVChargerSensor(TuyaEVChargerEntity, SensorEntity):
    entity_description: TuyaEVChargerSensorDescription

    def __init__(
        self,
        entry: ConfigEntry,
        runtime_data: TuyaEVChargerRuntimeData,
        description: TuyaEVChargerSensorDescription,
    ) -> None:
        super().__init__(entry=entry, runtime_data=runtime_data)
        self.entity_description = description
        self._attr_unique_id = f"{runtime_data.client.device_id}_{description.key}"

    @property
    def native_value(self) -> float | int | str | None:
        data = self.coordinator.data
        if data is None:
            return None
        return self.entity_description.value_fn(data)


class TuyaEVChargerSurplusSensor(TuyaEVChargerEntity, SensorEntity):
    entity_description: TuyaEVChargerSurplusSensorDescription

    def __init__(
        self,
        entry: ConfigEntry,
        runtime_data: TuyaEVChargerRuntimeData,
        description: TuyaEVChargerSurplusSensorDescription,
    ) -> None:
        super().__init__(entry=entry, runtime_data=runtime_data)
        self.entity_description = description
        self._attr_unique_id = f"{runtime_data.client.device_id}_{description.key}"
        self._unsub_listener: Callable[[], None] | None = None

    @property
    def native_value(self) -> float | int | str | None:
        controller = self._runtime_data.solar_surplus_controller
        if controller is None:
            return None
        return self.entity_description.value_fn(controller.snapshot)

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
