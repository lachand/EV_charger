from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    RestoreSensor,
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
from homeassistant.util import dt as dt_util

from . import TuyaEVChargerRuntimeData
from .const import (
    CARD_ROLE_ALARM,
    CARD_ROLE_CURRENT,
    CARD_ROLE_INDEX,
    CARD_ROLE_POWER,
    CARD_ROLE_SELFTEST,
    CARD_ROLE_TEMPERATURE,
    CARD_ROLE_VOLTAGE,
    CARD_ROLE_WORK_STATE,
)
from .entity import TuyaEVChargerEntity
from .tuya_ev_charger import EVMetrics


# Power (kW) above which the charger is considered to be actively charging.
# The charger can remain in WORKING after a completed charge, so a live power
# reading is what distinguishes "charging" (C) from "connected, done" (B).
_EVCC_CHARGING_POWER_THRESHOLD_KW = 0.1

# work_state_debug values that mean a vehicle is plugged in but not charging.
_EVCC_CONNECTED_STATES = frozenset({"IDLEINS", "WAIT", "PAUSE"})


def _evcc_status(data: EVMetrics) -> str:
    """Map the charger state to evcc's IEC 61851 status letters (A/B/C).

    A = ready (no vehicle), B = connected (plugged in, not charging),
    C = charging.

    work_state_debug vocabulary: IDLE (no car), IDLEINS (plugged in, idle),
    WAIT (scheduled), PAUSE (paused), WORKING (charging), SLEEP (idle, no car).
    WORKING can persist after a completed charge, so it only counts as charging
    while power is actually being drawn -- otherwise the car is connected but
    idle.
    """
    state = (data.work_state_debug or "").strip().upper()
    charging_power = (data.power_l1 or 0.0) >= _EVCC_CHARGING_POWER_THRESHOLD_KW
    if state == "WORKING":
        return "C" if charging_power else "B"
    if charging_power:
        return "C"
    if state in _EVCC_CONNECTED_STATES:
        return "B"
    return "A"


@dataclass(frozen=True, kw_only=True)
class TuyaEVChargerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[EVMetrics], float | int | str | None]


CARD_ROLE_BY_SENSOR_KEY: dict[str, str] = {
    "current_l1": CARD_ROLE_CURRENT,
    "power_l1": CARD_ROLE_POWER,
    "voltage_l1": CARD_ROLE_VOLTAGE,
    "temperature": CARD_ROLE_TEMPERATURE,
    "work_state": CARD_ROLE_WORK_STATE,
    "selftest": CARD_ROLE_SELFTEST,
    "alarm": CARD_ROLE_ALARM,
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
        key="evcc_status",
        translation_key="evcc_status",
        icon="mdi:ev-station",
        device_class=SensorDeviceClass.ENUM,
        options=["A", "B", "C"],
        value_fn=_evcc_status,
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

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _ = hass
    runtime_data: TuyaEVChargerRuntimeData = entry.runtime_data
    async_add_entities(
        [
            TuyaEVChargerSensor(entry, runtime_data, description)
            for description in SENSOR_DESCRIPTIONS
        ]
        + [TuyaEVChargerEnergySensor(entry, runtime_data)]
    )


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


class TuyaEVChargerEnergySensor(TuyaEVChargerEntity, RestoreSensor):
    """Cumulative charged energy, integrated from live power.

    The charger exposes no cumulative-energy datapoint, so this Riemann-sums
    power_l1 over the time between coordinator updates into a monotonic kWh
    counter (suitable for evcc's charge meter and the HA Energy dashboard).
    The running total survives restarts via RestoreSensor.
    """

    _attr_translation_key = "charge_energy_total"
    _attr_icon = "mdi:lightning-bolt"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3

    def __init__(
        self,
        entry: ConfigEntry,
        runtime_data: TuyaEVChargerRuntimeData,
    ) -> None:
        super().__init__(entry=entry, runtime_data=runtime_data)
        self._attr_unique_id = f"{runtime_data.client.device_id}_charge_energy_total"
        self._energy_kwh: float = 0.0
        self._last_ts: float | None = None

    @property
    def native_value(self) -> float:
        return round(self._energy_kwh, 6)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and last.native_value is not None:
            try:
                self._energy_kwh = max(0.0, float(last.native_value))
            except (TypeError, ValueError):
                self._energy_kwh = 0.0

    @callback
    def _handle_coordinator_update(self) -> None:
        now = dt_util.utcnow().timestamp()
        data = self.coordinator.data
        if (
            data is not None
            and data.power_l1 is not None
            and self._last_ts is not None
        ):
            elapsed_h = max(0.0, now - self._last_ts) / 3600.0
            self._energy_kwh += max(0.0, data.power_l1) * elapsed_h
        self._last_ts = now
        super()._handle_coordinator_update()
