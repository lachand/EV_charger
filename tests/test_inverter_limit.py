"""Inverter-output cap, and the three-phase power bug it exposed (issue #22).

The point of this feature is a measurement the grid meter cannot make. On a
hybrid inverter with the house on its backup output, the battery covers a sudden
household draw, so the grid meter stays near zero while the inverter is being
overloaded past its rating. Load balancing, which reads the grid meter, sees
nothing. These tests pin down that the grid-based cap is fooled and the
total-load cap is not.
"""

from __future__ import annotations

import types

import pytest


class _State:
    def __init__(self, value, unit="W"):
        self.state = str(value)
        self.attributes = {"unit_of_measurement": unit}


class _States:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, entity_id):
        return self._mapping.get(entity_id)


class _Metrics:
    """Only the fields the cap helpers read."""

    def __init__(self, *, total_power_kw=5.0, current_target=None):
        self.total_power = total_power_kw
        self.power_l1 = total_power_kw / 3.0  # as if only L1 were read
        self.current_target = current_target


def _controller(options, sensor_values):
    from tuya_ev_charger import solar_surplus
    from tuya_ev_charger.solar_surplus import (
        SolarSurplusController,
        _settings_from_entry,
    )

    entry = types.SimpleNamespace(options=options)
    ctrl = SolarSurplusController.__new__(SolarSurplusController)
    ctrl._settings = _settings_from_entry(entry)
    ctrl._hass = types.SimpleNamespace(
        states=_States({k: _State(v) for k, v in sensor_values.items()})
    )
    return ctrl, solar_surplus


# 6 kW inverter, cap set to 5500 W as the reporter proposed.
LADDER = tuple(range(6, 33))  # 6..32 A
INVERTER_OPTS = {
    "max_inverter_power_w": 5500,
    "total_load_sensor_entity_id": "sensor.total_load",
}


def test_the_grid_cap_is_fooled_by_the_battery():
    """The reporter's core claim, proven: grid ~0 while the car draws 5 kW makes
    load balancing compute a *larger* budget than the whole inverter rating."""
    from tuya_ev_charger.surplus_decision import headroom_for_car_w

    # House limit 6000, car 5000 W, grid meter reads 0 (battery masks the hob).
    headroom = headroom_for_car_w(grid_power_w=0.0, ev_power_w=5000.0, house_limit_w=6000.0)
    assert headroom == 11000.0  # 11 kW of "headroom" on a 6 kW inverter


def test_the_total_load_cap_sees_the_real_overload():
    """Same instant, read against total load instead: 5 kW car + 2 kW hob = 7 kW
    through a 5500 W cap leaves room for well under the car's current draw."""
    ctrl, _ = _controller(INVERTER_OPTS, {"sensor.total_load": 7000})
    cap = ctrl._inverter_limit_current(_Metrics(total_power_kw=5.0), LADDER)
    # Budget = 5500 - (7000 - 5000) = 3500 W -> 3500/230 = 15 A.
    assert cap == 15


def test_the_cap_stops_charging_when_no_headroom_remains():
    """Total load already at the ceiling: the car must come all the way down."""
    ctrl, _ = _controller(INVERTER_OPTS, {"sensor.total_load": 8000})
    cap = ctrl._inverter_limit_current(_Metrics(total_power_kw=5.0), LADDER)
    # Budget = 5500 - (8000 - 5000) = 2500 W -> 10 A. Still chargeable...
    assert cap == 10
    # ...but push the house to 10 kW and even 6 A does not fit.
    ctrl, _ = _controller(INVERTER_OPTS, {"sensor.total_load": 10000})
    assert ctrl._inverter_limit_current(_Metrics(total_power_kw=5.0), LADDER) == 0


def test_disabled_when_ceiling_is_zero():
    ctrl, _ = _controller(
        {"max_inverter_power_w": 0, "total_load_sensor_entity_id": "sensor.total_load"},
        {"sensor.total_load": 7000},
    )
    assert ctrl._inverter_limit_current(_Metrics(), LADDER) is None


def test_disabled_when_the_sensor_is_missing_or_unavailable():
    """No reading means no cap: capping blind is worse than not capping."""
    ctrl, _ = _controller(INVERTER_OPTS, {})  # sensor not present
    assert ctrl._inverter_limit_current(_Metrics(), LADDER) is None

    ctrl, _ = _controller(INVERTER_OPTS, {"sensor.total_load": "unavailable"})
    assert ctrl._inverter_limit_current(_Metrics(), LADDER) is None


def test_the_tighter_of_the_two_caps_binds():
    """Both limits configured: the protection takes whichever is lower, and
    reports which one it was."""
    from tuya_ev_charger.surplus_decision import headroom_for_car_w  # noqa: F401

    options = {
        **INVERTER_OPTS,
        "max_house_power_w": 9200,
        "surplus_sensor_entity_id": "sensor.grid",
    }
    # Grid at 3000 W import, car 5000 W: load-balancing budget
    #   9200 - (3000 - 5000) = 11200 W -> 32 A (full).
    # Inverter, total load 7000 W: 3500 W -> 15 A. Inverter wins.
    ctrl, _ = _controller(options, {"sensor.grid": 3000, "sensor.total_load": 7000})
    cap, source = ctrl._protection_cap(_Metrics(total_power_kw=5.0), LADDER)
    assert cap == 15
    assert source == "inverter_limit"


def test_neither_limit_configured_gives_no_cap():
    ctrl, _ = _controller({}, {})
    assert ctrl._protection_cap(_Metrics(), LADDER) == (None, None)


def test_ev_power_uses_total_not_l1():
    """The bug this fix corrects: on three phases, L1 alone under-reports the
    car's draw by up to 3x, over-stating headroom for every cap."""
    from tuya_ev_charger.solar_surplus import _ev_power_w

    # 11 kW three-phase: total_power 11 kW, L1 ~3.67 kW.
    metrics = _Metrics(total_power_kw=11.0)
    assert _ev_power_w(metrics) == pytest.approx(11000.0)
    # Not the ~3667 W that reading power_l1 * 1000 would have given.
    assert _ev_power_w(metrics) > 10000.0


def test_ev_power_handles_missing_total():
    """total_power can be None before the first full read; must not crash."""
    from tuya_ev_charger.solar_surplus import _ev_power_w

    assert _ev_power_w(types.SimpleNamespace(total_power=None, power_l1=0.0)) == 0.0
