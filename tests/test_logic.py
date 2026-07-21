"""Current selection, evcc mapping and re-discovery decisions."""

from __future__ import annotations

import pytest


def _metrics_stub(**kwargs):
    from tuya_ev_charger.tuya_ev_charger import EVMetrics

    defaults = dict(
        voltage_l1=230.0, current_l1=0.0, power_l1=0.0, phases={}, total_power=0.0,
        session_energy_kwh=None, session_duration_s=None,
        last_session_energy_kwh=None, last_session_duration_s=None,
        temperature=20.0, work_state=None, work_state_debug="IDLE", status="idle",
        plug_in_action=None, do_charge=None, current_target=10, max_current_cfg=16,
        nfc_enabled=None, downcounter=None, selftest=None, alarm=None,
        adjust_current_options=(6, 8, 10, 13, 16), product_variant=None,
        charger_info={}, schedule_enabled=False, schedule_start=None, schedule_end=None,
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


def test_advertised_steps_can_be_restored():
    from tuya_ev_charger.helpers import allowed_currents

    currents = allowed_currents(_metrics_stub(), {"continuous_current": False})
    assert currents == (6, 8, 10, 13, 16)


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
