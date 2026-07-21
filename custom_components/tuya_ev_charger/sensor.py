from __future__ import annotations

import re
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
from .const import (
    CARD_ROLE_ALARM,
    CARD_ROLE_CURRENT,
    CARD_ROLE_INDEX,
    CARD_ROLE_LAST_DECISION,
    CARD_ROLE_POWER,
    CARD_ROLE_SELFTEST,
    CARD_ROLE_SURPLUS_DISCHARGE_OVER_LIMIT,
    CARD_ROLE_SURPLUS_EFFECTIVE,
    CARD_ROLE_SURPLUS_RAW,
    CARD_ROLE_SURPLUS_TARGET_CURRENT,
    CARD_ROLE_TEMPERATURE,
    CARD_ROLE_VOLTAGE,
    CARD_ROLE_WORK_STATE,
    CONF_VEHICLES,
    DEFAULT_VEHICLES,
)
from .entity import TuyaEVChargerEntity
from .solar_surplus import SolarSurplusSnapshot
from .tuya_ev_charger import EVMetrics
from .vehicles import configured_vehicles


@dataclass(frozen=True, kw_only=True)
class TuyaEVChargerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[EVMetrics], float | int | str | None]


def _phase_attr(phase: str, attribute: str) -> Callable[[EVMetrics], float | None]:
    """Read one phase reading, or None when the model does not wire that phase.

    Single-phase chargers report L2/L3 as all zeros; returning None keeps those
    entities unavailable instead of showing a misleading 0 V / 0 A.
    """

    def _value(data: EVMetrics) -> float | None:
        measurements = data.phases.get(phase)
        if measurements is None:
            return None
        return getattr(measurements, attribute)

    return _value


@dataclass(frozen=True, kw_only=True)
class TuyaEVChargerSurplusControllerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[SolarSurplusSnapshot], float | int | str | None]


CARD_ROLE_BY_SENSOR_KEY: dict[str, str] = {
    "current_l1": CARD_ROLE_CURRENT,
    "power_l1": CARD_ROLE_POWER,
    "voltage_l1": CARD_ROLE_VOLTAGE,
    "temperature": CARD_ROLE_TEMPERATURE,
    "work_state": CARD_ROLE_WORK_STATE,
    "selftest": CARD_ROLE_SELFTEST,
    "alarm": CARD_ROLE_ALARM,
}

CARD_ROLE_BY_SURPLUS_SENSOR_KEY: dict[str, str] = {
    "surplus_last_decision_reason": CARD_ROLE_LAST_DECISION,
    "surplus_raw_w": CARD_ROLE_SURPLUS_RAW,
    "surplus_effective_w": CARD_ROLE_SURPLUS_EFFECTIVE,
    "surplus_battery_discharge_over_limit_w": CARD_ROLE_SURPLUS_DISCHARGE_OVER_LIMIT,
    "surplus_target_current_a": CARD_ROLE_SURPLUS_TARGET_CURRENT,
}


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
    *(
        description
        for phase in ("l2", "l3")
        for description in (
            TuyaEVChargerSensorDescription(
                key=f"voltage_{phase}",
                translation_key=f"voltage_{phase}",
                native_unit_of_measurement=UnitOfElectricPotential.VOLT,
                state_class=SensorStateClass.MEASUREMENT,
                device_class=SensorDeviceClass.VOLTAGE,
                suggested_display_precision=1,
                value_fn=_phase_attr(phase.upper(), "voltage"),
            ),
            TuyaEVChargerSensorDescription(
                key=f"current_{phase}",
                translation_key=f"current_{phase}",
                native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
                state_class=SensorStateClass.MEASUREMENT,
                device_class=SensorDeviceClass.CURRENT,
                suggested_display_precision=1,
                value_fn=_phase_attr(phase.upper(), "current"),
            ),
            TuyaEVChargerSensorDescription(
                key=f"power_{phase}",
                translation_key=f"power_{phase}",
                native_unit_of_measurement=UnitOfPower.KILO_WATT,
                state_class=SensorStateClass.MEASUREMENT,
                device_class=SensorDeviceClass.POWER,
                suggested_display_precision=2,
                value_fn=_phase_attr(phase.upper(), "power"),
            ),
        )
    ),
    TuyaEVChargerSensorDescription(
        key="power_total",
        translation_key="power_total",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
        suggested_display_precision=2,
        value_fn=lambda data: data.total_power,
    ),
    TuyaEVChargerSensorDescription(
        key="energy_session",
        translation_key="energy_session",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        # The charger has no lifetime meter, only a per-session counter that
        # resets. TOTAL_INCREASING is exactly the contract for that: Home
        # Assistant treats each reset as a new cycle and keeps a correct running
        # total, so this feeds the Energy Dashboard.
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_class=SensorDeviceClass.ENERGY,
        suggested_display_precision=2,
        value_fn=lambda data: data.session_energy_kwh,
    ),
    TuyaEVChargerSensorDescription(
        key="session_duration",
        translation_key="session_duration",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.session_duration_s,
    ),
    TuyaEVChargerSensorDescription(
        key="last_session_energy",
        translation_key="last_session_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        # Frozen record of the previous session, so no state_class: it must not
        # be summed into long-term statistics.
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=2,
        value_fn=lambda data: data.last_session_energy_kwh,
    ),
    TuyaEVChargerSensorDescription(
        key="last_session_duration",
        translation_key="last_session_duration",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.last_session_duration_s,
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
)

SURPLUS_CONTROLLER_SENSOR_DESCRIPTIONS: tuple[TuyaEVChargerSurplusControllerSensorDescription, ...] = (
    TuyaEVChargerSurplusControllerSensorDescription(
        key="surplus_last_decision_reason",
        translation_key="surplus_last_decision_reason",
        icon="mdi:comment-question-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.last_decision_reason,
    ),
    TuyaEVChargerSurplusControllerSensorDescription(
        key="surplus_raw_w",
        translation_key="surplus_raw_w",
        icon="mdi:solar-power",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
        suggested_display_precision=0,
        value_fn=lambda snapshot: snapshot.raw_surplus_w,
    ),
    TuyaEVChargerSurplusControllerSensorDescription(
        key="surplus_effective_w",
        translation_key="surplus_effective_w",
        icon="mdi:solar-power-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
        suggested_display_precision=0,
        value_fn=lambda snapshot: snapshot.effective_surplus_w,
    ),
    TuyaEVChargerSurplusControllerSensorDescription(
        key="surplus_battery_discharge_over_limit_w",
        translation_key="surplus_battery_discharge_over_limit_w",
        icon="mdi:battery-alert-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.POWER,
        suggested_display_precision=0,
        value_fn=lambda snapshot: snapshot.battery_discharge_over_limit_w,
    ),
    TuyaEVChargerSurplusControllerSensorDescription(
        key="surplus_target_current_a",
        translation_key="surplus_target_current_a",
        icon="mdi:current-ac",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.CURRENT,
        suggested_display_precision=0,
        value_fn=lambda snapshot: snapshot.target_current_a,
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
        TuyaEVChargerSurplusControllerSensor(entry, runtime_data, description)
        for description in SURPLUS_CONTROLLER_SENSOR_DESCRIPTIONS
    )
    entities.extend(
        TuyaEVChargerVehicleEnergySensor(entry, runtime_data, vehicle)
        for vehicle in configured_vehicles(
            entry.options.get(CONF_VEHICLES, DEFAULT_VEHICLES)
        )
    )
    async_add_entities(entities)


class TuyaEVChargerVehicleEnergySensor(TuyaEVChargerEntity, SensorEntity):
    """Cumulative energy attributed to one vehicle.

    Fed by the deltas of the charger's own lifetime counter, routed to whichever
    vehicle the "Active vehicle" select points at.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:car-electric"

    def __init__(
        self,
        entry: ConfigEntry,
        runtime_data: TuyaEVChargerRuntimeData,
        vehicle: str,
    ) -> None:
        super().__init__(entry=entry, runtime_data=runtime_data)
        self._vehicle = vehicle
        self._attr_name = f"{vehicle} energy"
        slug = re.sub(r"[^a-z0-9_]+", "_", vehicle.lower()).strip("_")
        self._attr_unique_id = f"{runtime_data.client.device_id}_vehicle_energy_{slug}"

    @property
    def native_value(self) -> float | None:
        tracker = self._runtime_data.vehicle_tracker
        if tracker is None:
            return None
        return tracker.total_for(self._vehicle)


class TuyaEVChargerSensor(TuyaEVChargerEntity, SensorEntity):
    entity_description: TuyaEVChargerSensorDescription

    def __init__(
        self,
        entry: ConfigEntry,
        runtime_data: TuyaEVChargerRuntimeData,
        description: TuyaEVChargerSensorDescription,
    ) -> None:
        card_role = CARD_ROLE_BY_SENSOR_KEY.get(description.key)
        card_index = CARD_ROLE_INDEX.get(card_role) if card_role is not None else None
        super().__init__(
            entry=entry,
            runtime_data=runtime_data,
            card_role=card_role,
            card_index=card_index,
        )
        self.entity_description = description
        self._attr_unique_id = f"{runtime_data.client.device_id}_{description.key}"

    @property
    def native_value(self) -> float | int | str | None:
        data = self.coordinator.data
        if data is None:
            return None
        return self.entity_description.value_fn(data)


class TuyaEVChargerSurplusControllerSensor(TuyaEVChargerEntity, SensorEntity):
    entity_description: TuyaEVChargerSurplusControllerSensorDescription

    def __init__(
        self,
        entry: ConfigEntry,
        runtime_data: TuyaEVChargerRuntimeData,
        description: TuyaEVChargerSurplusControllerSensorDescription,
    ) -> None:
        card_role = CARD_ROLE_BY_SURPLUS_SENSOR_KEY.get(description.key)
        card_index = CARD_ROLE_INDEX.get(card_role) if card_role is not None else None
        super().__init__(
            entry=entry,
            runtime_data=runtime_data,
            card_role=card_role,
            card_index=card_index,
        )
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
