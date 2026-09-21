"""The sensor platform.

No test here instantiated a single class from `sensor.py` before this file --
only the static `SENSOR_DESCRIPTIONS` metadata was checked elsewhere
(`test_logic.py`). The classes below carry real derived-state logic (a phase
reading that must read as absent rather than a misleading 0, a cost sensor
whose own docstring warns against showing 0 as free electricity, a decision
trace attached to exactly one sensor), none of it previously exercised.
"""

from __future__ import annotations

import types


def _sensor(cls, **fields):
    entity = cls.__new__(cls)
    for key, value in fields.items():
        setattr(entity, key, value)
    return entity


# --- _phase_attr -------------------------------------------------------------


def test_phase_attr_is_none_when_the_model_does_not_wire_that_phase():
    """Single-phase chargers report L2/L3 as absent, not zero -- showing 0
    would read as a real, if tiny, measurement."""
    from tuya_ev_charger.sensor import _phase_attr

    data = types.SimpleNamespace(phases={})
    assert _phase_attr("L2", "voltage")(data) is None


def test_phase_attr_reads_the_requested_measurement():
    from tuya_ev_charger.sensor import _phase_attr

    data = types.SimpleNamespace(phases={"L2": types.SimpleNamespace(voltage=231.5)})
    assert _phase_attr("L2", "voltage")(data) == 231.5


# --- connection health --------------------------------------------------------


def test_connection_health_is_available_even_when_the_coordinator_fails():
    """Deliberately not gated on coordinator success -- this sensor is most
    useful exactly when the charger is unreachable. Pinned so a future
    "cleanup" cannot add that gate back without a test noticing."""
    from tuya_ev_charger.sensor import TuyaEVChargerConnectionHealthSensor as S

    entity = _sensor(S)
    assert entity.available is True


# --- last session cost ---------------------------------------------------------


def _cost_sensor(session_history):
    from tuya_ev_charger.sensor import TuyaEVChargerLastSessionCostSensor as S

    return _sensor(S, _runtime_data=types.SimpleNamespace(session_history=session_history))


def test_no_session_history_reports_no_cost():
    entity = _cost_sensor(None)
    assert entity.native_value is None
    assert entity.extra_state_attributes is None


def test_a_session_with_no_price_configured_reports_no_cost_not_zero():
    """The risk the class docstring itself calls out: showing 0 would read as
    a working meter reporting free electricity."""
    history = types.SimpleNamespace(latest={"ended_at": "2024-01-01T10:00:00", "cost": None})
    entity = _cost_sensor(history)
    assert entity.native_value is None


def test_a_priced_session_reports_its_cost_and_attributes():
    history = types.SimpleNamespace(
        latest={
            "ended_at": "2024-01-01T10:00:00",
            "energy_kwh": 10.0,
            "cost": 2.5,
            "off_peak_minutes": 30,
            "peak_minutes": 10,
            "vehicle": "Zoe",
        }
    )
    entity = _cost_sensor(history)
    assert entity.native_value == 2.5
    assert entity.extra_state_attributes == {
        "ended_at": "2024-01-01T10:00:00",
        "energy_kwh": 10.0,
        "off_peak_minutes": 30,
        "peak_minutes": 10,
        "vehicle": "Zoe",
    }


# --- surplus controller sensor: decision trace ---------------------------------


def _surplus_sensor(key, *, controller):
    from tuya_ev_charger.sensor import (
        TuyaEVChargerSurplusControllerSensor as S,
    )
    from tuya_ev_charger.sensor import (
        TuyaEVChargerSurplusControllerSensorDescription as Description,
    )

    description = Description(key=key, value_fn=lambda snapshot: snapshot)
    return _sensor(
        S,
        entity_description=description,
        _runtime_data=types.SimpleNamespace(solar_surplus_controller=controller),
    )


def test_no_controller_attaches_no_decision_trace():
    entity = _surplus_sensor("surplus_last_decision_reason", controller=None)
    assert entity.extra_state_attributes is None


def test_only_the_decision_reason_sensor_carries_the_trace():
    """The other surplus sensors are plain numbers; attaching the same trace
    to each would repeat the payload several times per update."""
    controller = types.SimpleNamespace(
        snapshot=types.SimpleNamespace(decision_trace={"consulted": ["tariff"]})
    )
    entity = _surplus_sensor("surplus_raw_w", controller=controller)
    assert entity.extra_state_attributes is None


def test_the_decision_reason_sensor_carries_its_trace():
    controller = types.SimpleNamespace(
        snapshot=types.SimpleNamespace(decision_trace={"consulted": ["tariff"]})
    )
    entity = _surplus_sensor("surplus_last_decision_reason", controller=controller)
    assert entity.extra_state_attributes == {"consulted": ["tariff"]}
