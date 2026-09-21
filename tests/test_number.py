"""The charge-current number entity.

This is the write path: it makes the charger beep and can interrupt a running
charge, and it is the entity a user or an external controller drives directly.
It had no tests.
"""

from __future__ import annotations

import asyncio
import types

import pytest


class _Client:
    def __init__(self, *, ok=True):
        self.calls: list[tuple[int, object]] = []
        self._ok = ok

    async def async_set_charge_current(self, amperage, max_current=None):
        self.calls.append((amperage, max_current))
        return self._ok


def _metrics(*, current_target=10, max_current=32, options=None):
    return types.SimpleNamespace(
        current_target=current_target,
        max_current_cfg=max_current,
        adjust_current_options=options or [],
    )


def _entity(*, data=None, options=None, client=None):
    from tuya_ev_charger.number import TuyaEVChargerCurrentNumber

    entity = TuyaEVChargerCurrentNumber.__new__(TuyaEVChargerCurrentNumber)
    refreshes: list[int] = []

    coordinator = types.SimpleNamespace(data=data)

    async def _refresh():
        refreshes.append(1)

    coordinator.async_request_refresh = _refresh
    entity._entry = types.SimpleNamespace(options=options or {})
    entity._runtime_data = types.SimpleNamespace(client=client or _Client())
    entity.coordinator = coordinator
    entity.refreshes = refreshes
    return entity


def _set(entity, value):
    asyncio.run(entity.async_set_native_value(value))


# --- bounds come from the shared ladder ------------------------------------


def test_bounds_follow_the_charger_maximum():
    entity = _entity(data=_metrics(max_current=16))
    assert entity.native_min_value == 6.0
    assert entity.native_max_value == 16.0


def test_bounds_respect_the_installation_limit():
    """The 2.12.0 cap must bound the entity, not just surplus regulation."""
    entity = _entity(data=_metrics(max_current=32), options={"max_charge_current_a": 20})
    assert entity.native_max_value == 20.0


def test_bounds_respect_the_minimum_limit():
    entity = _entity(data=_metrics(max_current=32), options={"min_charge_current_a": 8})
    assert entity.native_min_value == 8.0


def test_the_allowed_list_is_exposed_as_an_attribute():
    entity = _entity(data=_metrics(max_current=10))
    entity._attr_extra_state_attributes = {}
    entity._with_technical_attributes = lambda payload: payload
    assert entity.extra_state_attributes["allowed_currents"] == [6, 7, 8, 9, 10]


# --- writing --------------------------------------------------------------


def test_a_new_value_is_written_and_the_coordinator_refreshed():
    client = _Client()
    entity = _entity(data=_metrics(current_target=10), client=client)

    _set(entity, 16)
    assert client.calls == [(16, 32)]
    assert entity.refreshes == [1]


def test_writing_the_value_already_held_is_skipped():
    """Every DP write beeps, and controllers re-assert the same setpoint on a
    timer -- so an unchanged value must not reach the charger."""
    client = _Client()
    entity = _entity(data=_metrics(current_target=10), client=client)

    _set(entity, 10)
    assert client.calls == []
    assert entity.refreshes == []


def test_floats_are_rounded_rather_than_refused():
    """Automations routinely send floats; 10.4 is friendlier rounded than
    rejected."""
    client = _Client()
    entity = _entity(data=_metrics(current_target=6), client=client)

    _set(entity, 10.4)
    assert client.calls == [(10, 32)]


def test_a_value_above_the_installation_limit_is_refused():
    """The cap is a limit, so the entity must reject rather than silently clamp:
    a caller asking for 32 A on a 20 A circuit has a bug worth surfacing."""
    from tuya_ev_charger.number import HomeAssistantError

    client = _Client()
    entity = _entity(
        data=_metrics(current_target=10),
        options={"max_charge_current_a": 20},
        client=client,
    )

    with pytest.raises(HomeAssistantError, match="Unsupported current setpoint"):
        _set(entity, 32)
    assert client.calls == [], "nothing may reach the charger for a refused value"


def test_a_value_below_the_charger_minimum_is_refused():
    from tuya_ev_charger.number import HomeAssistantError

    entity = _entity(data=_metrics(current_target=10))
    with pytest.raises(HomeAssistantError):
        _set(entity, 3)


def test_a_failed_write_raises_and_does_not_refresh():
    """Silently swallowing the failure would leave the UI showing a setpoint the
    charger never accepted."""
    from tuya_ev_charger.number import HomeAssistantError

    entity = _entity(data=_metrics(current_target=10), client=_Client(ok=False))
    with pytest.raises(HomeAssistantError, match="Unable to update"):
        _set(entity, 16)
    assert entity.refreshes == []


def test_the_charger_maximum_is_passed_through_to_the_client():
    """The client needs it to raise the charger's own limit before writing."""
    client = _Client()
    entity = _entity(data=_metrics(current_target=6, max_current=16), client=client)

    _set(entity, 14)
    assert client.calls == [(14, 16)]


def test_a_write_still_works_before_the_first_poll():
    """With no data yet the entity must not crash; it writes without a maximum."""
    client = _Client()
    entity = _entity(data=None, client=client)

    _set(entity, 10)
    assert client.calls == [(10, None)]


def test_the_reported_value_tracks_the_charger():
    entity = _entity(data=_metrics(current_target=13))
    assert entity.native_value == 13.0

    entity.coordinator.data = _metrics(current_target=None)
    assert entity.native_value is None


# --- surplus option thresholds (SOC/power cross-adjustment) -----------------
#
# TuyaEVChargerSurplusOptionNumber -- a different class from the one above --
# had no tests at all. Its write path silently rewrites four option keys per
# call to keep the SOC high/low pair and the start/stop power pair coherent; a
# regression here writes a low >= high or a stop > start that nothing else
# catches.


def _option_entity(option_key, *, options=None):
    from tuya_ev_charger.number import SURPLUS_OPTION_NUMBER_DESCRIPTIONS
    from tuya_ev_charger.number import TuyaEVChargerSurplusOptionNumber as S

    description = next(d for d in SURPLUS_OPTION_NUMBER_DESCRIPTIONS if d.option_key == option_key)
    entity = S.__new__(S)
    updates: list[dict] = []
    entity.entity_description = description
    entity._entry = types.SimpleNamespace(options=dict(options or {}))
    entity.hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(
            async_update_entry=lambda e, options: updates.append(options)
        )
    )
    entity.async_write_ha_state = lambda: None
    entity.updates = updates
    return entity


def test_native_value_dispatches_by_option_key():
    from tuya_ev_charger.const import (
        CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
        CONF_SURPLUS_START_THRESHOLD_W,
        CONF_SURPLUS_STOP_THRESHOLD_W,
    )

    soc = _option_entity(
        CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
        options={CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT: 92},
    )
    assert soc.native_value == 92.0

    power = _option_entity(
        CONF_SURPLUS_START_THRESHOLD_W,
        options={CONF_SURPLUS_START_THRESHOLD_W: 1800, CONF_SURPLUS_STOP_THRESHOLD_W: 1000},
    )
    assert power.native_value == 1800.0


def test_lowering_the_high_soc_threshold_pulls_the_low_one_down_too():
    from tuya_ev_charger.const import (
        CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
        CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
    )

    entity = _option_entity(
        CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
        options={
            CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT: 95,
            CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT: 90,
        },
    )
    asyncio.run(entity.async_set_native_value(85))
    written = entity.updates[-1]
    assert written[CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT] == 85
    assert written[CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT] == 84, "must stay below the new high"


def test_raising_the_low_soc_threshold_pushes_the_high_one_up_too():
    from tuya_ev_charger.const import (
        CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
        CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
    )

    entity = _option_entity(
        CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
        options={
            CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT: 95,
            CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT: 90,
        },
    )
    asyncio.run(entity.async_set_native_value(97))
    written = entity.updates[-1]
    assert written[CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT] == 97
    assert written[CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT] == 98, "must stay above the new low"


def test_lowering_the_start_threshold_pulls_the_stop_threshold_down_too():
    from tuya_ev_charger.const import CONF_SURPLUS_START_THRESHOLD_W, CONF_SURPLUS_STOP_THRESHOLD_W

    entity = _option_entity(
        CONF_SURPLUS_START_THRESHOLD_W,
        options={CONF_SURPLUS_START_THRESHOLD_W: 1600, CONF_SURPLUS_STOP_THRESHOLD_W: 1200},
    )
    asyncio.run(entity.async_set_native_value(1000))
    written = entity.updates[-1]
    assert written[CONF_SURPLUS_START_THRESHOLD_W] == 1000
    assert written[CONF_SURPLUS_STOP_THRESHOLD_W] == 1000, "stop must never exceed start"


def test_raising_the_stop_threshold_above_start_is_clamped_to_start():
    from tuya_ev_charger.const import CONF_SURPLUS_START_THRESHOLD_W, CONF_SURPLUS_STOP_THRESHOLD_W

    entity = _option_entity(
        CONF_SURPLUS_STOP_THRESHOLD_W,
        options={CONF_SURPLUS_START_THRESHOLD_W: 1600, CONF_SURPLUS_STOP_THRESHOLD_W: 1200},
    )
    asyncio.run(entity.async_set_native_value(2000))
    written = entity.updates[-1]
    assert written[CONF_SURPLUS_STOP_THRESHOLD_W] == 1600, "stop must never exceed start"


# --- long-term statistics eligibility (B13) --------------------------------


def test_vehicle_energy_sensors_qualify_for_long_term_statistics():
    """Per-vehicle energy already feeds the Energy dashboard and long-term
    statistics: an ENERGY sensor with TOTAL_INCREASING and a kWh unit is picked
    up by the recorder automatically. This pins that contract, since dropping the
    state class would silently remove the per-vehicle history without any error.
    """
    from tuya_ev_charger.sensor import TuyaEVChargerVehicleEnergySensor as V

    assert str(V._attr_device_class) == "energy"
    assert str(V._attr_state_class) == "total_increasing"
    assert str(V._attr_native_unit_of_measurement) == "kWh"
