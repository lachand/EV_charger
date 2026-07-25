"""Spotting a charge going wrong.

The failure mode to avoid is a false alarm: telling someone their charger is
degrading when it is fine. So these tests weigh what must *not* raise as heavily
as what must, and every check demands several agreeing sessions.
"""

from __future__ import annotations

import pytest


def _session(*, duration_s=7200, energy_kwh=14.8):
    return {"duration_s": duration_s, "energy_kwh": energy_kwh}


def _detect(sessions, *, best=7.4, typical=14.8):
    from tuya_ev_charger.session_anomaly import detect_anomalies

    return detect_anomalies(sessions, established_best_kw=best, typical_energy_kwh=typical)


# --- charging slower than usual -------------------------------------------


def test_a_run_of_slow_sessions_is_flagged():
    """The car has proven 7.4 kW; three recent charges at ~3 kW is a derating
    contactor or a poor connection, not a coincidence."""
    from tuya_ev_charger.session_anomaly import SessionAnomaly

    slow = [_session(duration_s=7200, energy_kwh=6.0)] * 3  # 3 kW
    assert SessionAnomaly.CHARGING_SLOWER_THAN_USUAL in _detect(slow)


def test_a_single_slow_session_is_not_enough():
    """One slow charge is a cloudy afternoon on surplus, not a fault."""
    from tuya_ev_charger.session_anomaly import SessionAnomaly

    sessions = [
        _session(duration_s=7200, energy_kwh=6.0),  # slow
        _session(duration_s=7200, energy_kwh=14.8),  # normal
        _session(duration_s=7200, energy_kwh=14.8),
    ]
    assert SessionAnomaly.CHARGING_SLOWER_THAN_USUAL not in _detect(sessions)


def test_normal_speed_sessions_are_not_flagged():
    fast = [_session(duration_s=7200, energy_kwh=14.8)] * 5  # 7.4 kW
    assert _detect(fast) == []


def test_slowness_needs_a_known_best_rate():
    """Without an established best there is nothing to be slow against."""
    slow = [_session(duration_s=7200, energy_kwh=6.0)] * 3
    assert _detect(slow, best=None) == []


def test_deliberately_slow_surplus_sessions_alone_do_not_alarm():
    """If the car has never proven it can go fast, slow sessions are just how it
    is used -- surplus at 6 A -- not a degradation."""
    slow = [_session(duration_s=7200, energy_kwh=6.0)] * 5
    # best unknown because nothing fast was ever seen
    assert _detect(slow, best=None) == []


# --- repeated short sessions ----------------------------------------------


def test_repeated_short_sessions_are_flagged():
    """Three charges in a row cutting out at a fraction of the usual energy: a
    cable dropping the connection, say."""
    from tuya_ev_charger.session_anomaly import SessionAnomaly

    short = [_session(duration_s=1200, energy_kwh=2.0)] * 3
    assert SessionAnomaly.REPEATED_SHORT_SESSIONS in _detect(short, typical=15.0)


def test_two_short_then_a_full_one_is_not_flagged():
    from tuya_ev_charger.session_anomaly import SessionAnomaly

    sessions = [
        _session(energy_kwh=2.0),
        _session(energy_kwh=2.0),
        _session(energy_kwh=15.0),
    ]
    assert SessionAnomaly.REPEATED_SHORT_SESSIONS not in _detect(sessions, typical=15.0)


def test_short_sessions_need_a_known_typical_energy():
    short = [_session(energy_kwh=2.0)] * 3
    # best=None isolates this from the slow-rate check.
    assert _detect(short, best=None, typical=None) == []


def test_a_car_that_simply_tops_up_briefly_is_not_a_fault():
    """If short *is* typical and slow *is* the norm, nothing is wrong. A car whose
    established best is itself ~4 kW is not charging slower than usual at 4 kW."""
    short = [_session(duration_s=1800, energy_kwh=2.0)] * 3
    assert _detect(short, best=4.0, typical=2.0) == []


# --- the typical-energy baseline ------------------------------------------


def test_typical_energy_is_the_median_not_the_mean():
    """One huge session must not drag the baseline the short check uses."""
    from tuya_ev_charger.session_anomaly import typical_energy_kwh

    sessions = [
        _session(energy_kwh=10.0),
        _session(energy_kwh=11.0),
        _session(energy_kwh=12.0),
        _session(energy_kwh=13.0),
        _session(energy_kwh=100.0),  # one outlier
    ]
    assert typical_energy_kwh(sessions) == pytest.approx(12.0)


def test_typical_energy_needs_enough_history():
    from tuya_ev_charger.session_anomaly import typical_energy_kwh

    assert typical_energy_kwh([_session()] * 3) is None


def test_a_healthy_history_has_no_anomalies():
    """The end-to-end reassurance: a normal mix raises nothing."""
    sessions = [
        _session(duration_s=7200, energy_kwh=14.8),
        _session(duration_s=3600, energy_kwh=7.4),
        _session(duration_s=7200, energy_kwh=12.0),
        _session(duration_s=5400, energy_kwh=10.0),
        _session(duration_s=7200, energy_kwh=14.8),
    ]
    assert _detect(sessions) == []
