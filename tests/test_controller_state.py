"""The controller's persisted memory: deadlines survive as wall-clock time."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _api(_stub_homeassistant):
    global ControllerState, from_stored, to_stored
    from tuya_ev_charger.controller_state import ControllerState, from_stored, to_stored


def test_round_trip_keeps_remaining_time():
    state = ControllerState(
        force_charge_until_ts=1600.0,
        force_charge_current_a=16,
        pause_until_ts=1300.0,
        session_active=True,
        session_energy_kwh=4.2,
        session_total_s=900.0,
        session_off_peak_s=300.0,
    )
    stored = to_stored(state, now_mono=1000.0, now_wall=5_000_000.0)
    # New process: a different monotonic origin, 60 s later on the wall clock.
    restored = from_stored(stored, now_mono=50.0, now_wall=5_000_060.0)
    assert restored.force_charge_until_ts == 50.0 + 540.0
    assert restored.pause_until_ts == 50.0 + 240.0
    assert restored.force_charge_current_a == 16
    assert restored.session_active is True
    assert restored.session_energy_kwh == 4.2
    assert restored.session_total_s == 900.0
    assert restored.session_off_peak_s == 300.0


def test_a_deadline_passed_during_the_outage_is_dropped():
    stored = to_stored(
        ControllerState(force_charge_until_ts=1100.0, force_charge_current_a=10),
        now_mono=1000.0,
        now_wall=5_000_000.0,
    )
    restored = from_stored(stored, now_mono=5.0, now_wall=5_000_200.0)
    assert restored.force_charge_until_ts is None
    assert restored.force_charge_current_a is None


def test_none_deadlines_stay_none():
    stored = to_stored(ControllerState(), now_mono=1.0, now_wall=2.0)
    restored = from_stored(stored, now_mono=1.0, now_wall=2.0)
    assert restored == ControllerState()


def test_a_wall_clock_jump_between_save_and_load_shifts_the_deadline():
    stored = to_stored(ControllerState(pause_until_ts=1600.0), 1000.0, 5_000_000.0)
    # The clock was set back an hour: the pause is now far in the future.
    restored = from_stored(stored, now_mono=0.0, now_wall=5_000_000.0 - 3600.0)
    assert restored.pause_until_ts == 600.0 + 3600.0


def test_empty_or_malformed_data_gives_defaults():
    assert from_stored(None, 0.0, 0.0) == ControllerState()
    assert from_stored({}, 0.0, 0.0) == ControllerState()
    assert from_stored("junk", 0.0, 0.0) == ControllerState()
    bad = {
        "force_charge_until": "soon",
        "pause_until": True,
        "session_active": "yes",
        "session_energy_kwh": "x",
        "session_total_s": -5,
        "session_off_peak_s": -1.5,
    }
    assert from_stored(bad, 0.0, 0.0) == ControllerState()


def test_negative_energy_and_tallies_are_clamped():
    restored = from_stored({"session_energy_kwh": -3.0, "session_total_s": -1}, 0.0, 0.0)
    assert restored.session_energy_kwh == 0.0
    assert restored.session_total_s == 0.0
