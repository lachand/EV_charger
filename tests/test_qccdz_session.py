"""qccdz firmwares have no DP 105, so the session record is built from DP 54/25 (#46).

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

START = 1_792_190_549  # DP 54 from a real 1.1.27-EU read


def _dps(evse_state, energy_hundredths, start=START):
    return {
        "4": 10,
        "23": "1.1.27-EU",
        "24": 21,
        "25": energy_hundredths,
        "35": "charge",
        "38": evse_state,
        "48": 22,
        "49": 94,
        "50": 2370,
        "54": str(start),
    }


def _poll(clock, dps, now):
    from tuya_ev_charger.charger_metrics import decode_qccdz_metrics
    from tuya_ev_charger.const import CHARGER_PROFILE_QCCDZ_V1
    from tuya_ev_charger.dp_profile import resolve_profile

    _, profile = resolve_profile(CHARGER_PROFILE_QCCDZ_V1, "")
    return clock.apply(decode_qccdz_metrics(dps, profile), dps, now)


def test_a_session_is_reported_once_the_car_is_unplugged():
    from tuya_ev_charger.qccdz_session import QccdzSessionClock

    clock = QccdzSessionClock()
    now = START + 60
    m = _poll(clock, _dps("plugged_in_charging", 10), now)
    assert m.last_session_energy_kwh is None
    for _ in range(10):
        now += 30
        m = _poll(clock, _dps("plugged_in_charging", 50), now)
    assert m.session_duration_s == 300
    assert m.last_session_duration_s is None

    # Full, then left plugged in for an hour: not charging time.
    now += 30
    m = _poll(clock, _dps("session_ending", 1503), now)
    now += 3600
    m = _poll(clock, _dps("session_ending", 1503), now)
    assert m.last_session_energy_kwh is None
    assert m.session_duration_s == 330

    now += 30
    unplugged = _poll(clock, _dps("not_plugged_in", 1503), now)
    later = _poll(clock, _dps("not_plugged_in", 1503), now + 600)

    assert unplugged.last_session_energy_kwh == 15.03
    assert unplugged.last_session_duration_s == now - START
    assert unplugged.session_duration_s == 330
    # Same record on every later poll, so the history logs it once.
    assert later.last_session_duration_s == unplugged.last_session_duration_s
    assert later.last_session_energy_kwh == unplugged.last_session_energy_kwh


def test_a_gap_between_polls_does_not_count_as_charging():
    from tuya_ev_charger.qccdz_session import QccdzSessionClock

    clock = QccdzSessionClock()
    _poll(clock, _dps("plugged_in_charging", 10), START + 30)
    m = _poll(clock, _dps("plugged_in_charging", 90), START + 3600)

    assert m.session_duration_s == 0


def test_a_new_start_moment_starts_a_new_session():
    from tuya_ev_charger.qccdz_session import QccdzSessionClock

    clock = QccdzSessionClock()
    _poll(clock, _dps("plugged_in_charging", 10), START + 30)
    _poll(clock, _dps("plugged_in_charging", 20), START + 60)
    _poll(clock, _dps("not_plugged_in", 20), START + 90)

    nxt = START + 86_400
    m = _poll(clock, _dps("plugged_in_charging", 5, start=nxt), nxt + 30)

    assert m.session_duration_s == 0
    assert m.last_session_duration_s is None
    assert m.last_session_energy_kwh is None


def test_without_dp_54_nothing_is_reported():
    from tuya_ev_charger.qccdz_session import QccdzSessionClock

    dps = _dps("not_plugged_in", 1503)
    del dps["54"]
    m = _poll(QccdzSessionClock(), dps, START)

    assert m.last_session_duration_s is None
    assert m.session_duration_s is None
