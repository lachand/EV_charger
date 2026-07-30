"""Current selection, evcc mapping and re-discovery decisions."""

from __future__ import annotations

import pytest


def _metrics_stub(**kwargs):
    from tuya_ev_charger.tuya_ev_charger import EVMetrics

    defaults = dict(
        voltage_l1=230.0,
        current_l1=0.0,
        power_l1=0.0,
        phases={},
        total_power=0.0,
        session_energy_kwh=None,
        session_duration_s=None,
        last_session_energy_kwh=None,
        last_session_duration_s=None,
        temperature=20.0,
        work_state=None,
        work_state_debug="IDLE",
        status="idle",
        plug_in_action=None,
        do_charge=None,
        current_target=10,
        max_current_cfg=16,
        nfc_enabled=None,
        downcounter=None,
        selftest=None,
        alarm=None,
        adjust_current_options=(6, 8, 10, 13, 16),
        product_variant=None,
        charger_info={},
        schedule_enabled=False,
        schedule_start=None,
        schedule_end=None,
    )
    defaults.update(kwargs)
    return EVMetrics(**defaults)


def test_continuous_current_is_the_default():
    """DP 107 only lists the app's shortcuts; 1 A steps are accepted."""
    from tuya_ev_charger.helpers import allowed_currents

    currents = allowed_currents(_metrics_stub(), {})
    assert 11 in currents and 7 in currents
    assert min(currents) == 6


def test_current_is_capped_by_the_charger_maximum():
    from tuya_ev_charger.helpers import allowed_currents

    currents = allowed_currents(_metrics_stub(max_current_cfg=16), {})
    assert max(currents) == 16


def test_continuous_ceiling_is_not_limited_by_a_narrower_preset_list():
    """DP 107 can advertise fewer shortcuts than DP 152 actually allows.

    Regression test: a charger whose preset list tops out below its real
    hardware maximum (observed in the field: presets (6, 8, 10, 13) on a unit
    whose DP 152 correctly reports 16A) must still offer up to the DP 152
    value in continuous mode. The preset list should only ever inform the
    floor, never lower the ceiling below max_current_cfg.
    """
    from tuya_ev_charger.helpers import allowed_currents

    currents = allowed_currents(
        _metrics_stub(max_current_cfg=16, adjust_current_options=(6, 8, 10, 13)),
        {},
    )
    assert max(currents) == 16
    assert min(currents) == 6


def test_advertised_steps_can_be_restored():
    from tuya_ev_charger.helpers import allowed_currents

    currents = allowed_currents(_metrics_stub(), {"continuous_current": False})
    assert currents == (6, 8, 10, 13, 16)


def test_continuous_ceiling_falls_back_to_presets_when_dp152_is_absent():
    """Some firmwares never report DP 152; the preset ceiling is the only
    signal left, and must not fall back to the global 32 A maximum."""
    from tuya_ev_charger.helpers import allowed_currents

    currents = allowed_currents(
        _metrics_stub(max_current_cfg=None, adjust_current_options=(6, 8, 10, 13)),
        {},
    )
    assert max(currents) == 13
    assert min(currents) == 6


@pytest.mark.parametrize(
    ("status", "power", "expected"),
    [
        ("charging", 1.9, "C"),
        # WORKING lingers after a completed charge, so no power means connected.
        ("charging", 0.0, "B"),
        ("plugged_in", 0.0, "B"),
        ("paused", 0.0, "B"),
        ("charged", 0.0, "B"),
        ("fault", 0.0, "B"),
        ("idle", 0.0, "A"),
        ("sleep", 0.0, "A"),
        (None, 0.0, "A"),
    ],
)
def test_evcc_status(status, power, expected):
    from tuya_ev_charger.tuya_ev_charger import evcc_status

    assert evcc_status(status, power) == expected


@pytest.mark.parametrize(
    ("device_id", "expected"),
    [
        ("bf23dbbd3d2eb2c804aswb", True),
        ("192.168.1.236", False),  # legacy entries stored the IP here
        ("", False),
    ],
)
def test_gwid_detection(device_id, expected):
    from tuya_ev_charger.coordinator import _looks_like_gwid

    assert _looks_like_gwid(device_id) is expected


def test_advanced_entities_are_created_disabled():
    """A fresh install should not open on 47 entities."""
    from tuya_ev_charger import sensor
    from tuya_ev_charger.const import ADVANCED_ENTITY_KEYS

    described = {
        d.key: d
        for d in (*sensor.SENSOR_DESCRIPTIONS, *sensor.SURPLUS_CONTROLLER_SENSOR_DESCRIPTIONS)
    }
    for key in ADVANCED_ENTITY_KEYS & set(described):
        assert described[key].entity_registry_enabled_default is False, key


def test_everyday_entities_stay_enabled():
    """The policy must never hide what the charger is actually for."""
    from tuya_ev_charger import sensor

    everyday = {
        "voltage_l1",
        "current_l1",
        "power_l1",
        "power_total",
        "energy_session",
        "status",
        "temperature",
        "alarm",
    }
    for d in sensor.SENSOR_DESCRIPTIONS:
        if d.key in everyday:
            assert d.entity_registry_enabled_default is True, d.key


def test_unavailable_capabilities_are_detected():
    """Only what the hardware truly lacks may be disabled without asking."""
    from tuya_ev_charger.entity_cleanup import unavailable_capability_keys
    from tuya_ev_charger.tuya_ev_charger import PhaseMetrics

    single = _metrics_stub(
        phases={"L1": PhaseMetrics(230.0, 0.0, 0.0, 0.0)}, plug_in_action=None, nfc_enabled=None
    )
    keys = unavailable_capability_keys(single)
    assert {"voltage_l2", "power_l3", "plug_in_action", "nfc_enabled"} <= keys
    assert not any(k.endswith("_l1") for k in keys)

    three_phase = _metrics_stub(
        phases={p: PhaseMetrics(235.0, 16.0, 3.76, 3.7) for p in ("L1", "L2", "L3")},
        plug_in_action="charge",
        nfc_enabled=True,
    )
    assert unavailable_capability_keys(three_phase) == set()
