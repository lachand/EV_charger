"""Learning what the car actually achieves.

The failure this fixes is asymmetric and was the dangerous way round: the planner
assumed the *charger's* rating, so a car limited to 3.7 kW on a 7.4 kW charger had
its charging time halved and was started far too late to meet the deadline. So
these tests care most about the direction of every error.
"""

from __future__ import annotations

import pytest


def _record(*, duration_s=7200, energy_kwh=14.8, vehicle=None):
    return {"duration_s": duration_s, "energy_kwh": energy_kwh, "vehicle": vehicle}


# --- one session -----------------------------------------------------------


def test_a_session_average_is_energy_over_time():
    from tuya_ev_charger.charge_curve import session_average_kw

    assert session_average_kw(3600, 7.4) == pytest.approx(7.4)
    assert session_average_kw(7200, 7.4) == pytest.approx(3.7)


@pytest.mark.parametrize(
    ("duration_s", "energy_kwh"),
    [
        (600, 5.0),  # ten minutes: mostly handshake and ramp-up
        (7200, 0.4),  # a trickle, whatever the duration
        (0, 5.0),
        (None, 5.0),
        (7200, None),
        ("nonsense", 5.0),
    ],
)
def test_untrustworthy_sessions_are_rejected_not_averaged(duration_s, energy_kwh):
    """Averaging a two-minute top-up in would drag the learned rate down and make
    every later plan start needlessly early."""
    from tuya_ev_charger.charge_curve import session_average_kw

    assert session_average_kw(duration_s, energy_kwh) is None


# --- learning across sessions ---------------------------------------------


def test_nothing_is_claimed_from_too_few_sessions():
    """One session is an anecdote, not a charging curve."""
    from tuya_ev_charger.charge_curve import MIN_SESSIONS, learned_power_kw

    assert learned_power_kw([_record()]) is None
    assert learned_power_kw([_record()] * (MIN_SESSIONS - 1)) is None
    assert learned_power_kw([_record()] * MIN_SESSIONS) is not None


def test_the_best_observed_rate_is_learned_not_the_mean():
    """The mean is dragged down by every surplus session that deliberately
    charged at 6 A -- true of those sessions, useless as a capability."""
    from tuya_ev_charger.charge_curve import learned_power_kw

    sessions = [
        _record(duration_s=7200, energy_kwh=14.8),  # 7.4 kW, charged from the grid
        _record(duration_s=7200, energy_kwh=2.8),  # 1.4 kW, surplus at 6 A
        _record(duration_s=7200, energy_kwh=3.0),  # 1.5 kW, surplus again
    ]
    learned = learned_power_kw(sessions)
    assert learned == pytest.approx(7.4 * 0.95)


def test_the_learned_rate_carries_a_safety_margin():
    """Planning at exactly the observed rate leaves no room for a cooler session."""
    from tuya_ev_charger.charge_curve import learned_power_kw

    learned = learned_power_kw([_record(duration_s=3600, energy_kwh=7.4)] * 3)
    assert learned < 7.4


def test_vehicles_are_learned_separately():
    """Two cars sharing a charger have no reason to charge alike."""
    from tuya_ev_charger.charge_curve import learned_power_kw

    sessions = [
        *[_record(duration_s=3600, energy_kwh=11.0, vehicle="Kangoo")] * 3,
        *[_record(duration_s=3600, energy_kwh=3.7, vehicle="Zoe")] * 3,
    ]
    fast = learned_power_kw(sessions, vehicle="Kangoo")
    slow = learned_power_kw(sessions, vehicle="Zoe")
    assert fast > slow
    assert slow == pytest.approx(3.7 * 0.95)


def test_an_unknown_vehicle_learns_nothing():
    from tuya_ev_charger.charge_curve import learned_power_kw

    sessions = [_record(vehicle="Zoe")] * 5
    assert learned_power_kw(sessions, vehicle="Kangoo") is None


def test_short_sessions_do_not_count_towards_the_minimum():
    """Three unusable sessions are still nothing to plan against."""
    from tuya_ev_charger.charge_curve import learned_power_kw

    assert learned_power_kw([_record(duration_s=300, energy_kwh=0.5)] * 5) is None


# --- which number the planner uses ----------------------------------------


def test_learning_may_only_ever_lengthen_the_plan():
    """The core safety rule. A car slower than its charger must lower the assumed
    power -- more time, earlier start. Learning must never claim the car is
    faster than the hardware, which would shorten the plan and risk the very
    deadline it protects.
    """
    from tuya_ev_charger.charge_curve import planning_power_kw

    # The real case: 3.7 kW car on a 7.4 kW charger.
    assert planning_power_kw(theoretical_kw=7.4, learned_kw=3.5) == 3.5

    # A record claiming more than the hardware allows is ignored.
    assert planning_power_kw(theoretical_kw=7.4, learned_kw=11.0) == 7.4


def test_without_history_the_theoretical_rate_stands():
    from tuya_ev_charger.charge_curve import planning_power_kw

    assert planning_power_kw(theoretical_kw=7.4, learned_kw=None) == 7.4
    assert planning_power_kw(theoretical_kw=7.4, learned_kw=0.0) == 7.4


def test_learning_still_answers_when_the_theoretical_rate_is_unknown():
    """No ladder yet, e.g. before the first poll."""
    from tuya_ev_charger.charge_curve import planning_power_kw

    assert planning_power_kw(theoretical_kw=0.0, learned_kw=3.5) == 3.5


def test_the_end_to_end_effect_on_a_deadline():
    """The bug, stated as time. 20 kWh needed by 07:00 on a 7.4 kW charger with a
    3.7 kW car: planning at the charger's rating asks for half the time really
    required, so the charge starts hours too late."""
    from tuya_ev_charger.charge_curve import learned_power_kw, planning_power_kw
    from tuya_ev_charger.charge_planner import minutes_needed

    sessions = [_record(duration_s=7200, energy_kwh=7.4)] * 3  # 3.7 kW observed
    learned = learned_power_kw(sessions)

    assumed = minutes_needed(20.0, planning_power_kw(theoretical_kw=7.4, learned_kw=None))
    informed = minutes_needed(20.0, planning_power_kw(theoretical_kw=7.4, learned_kw=learned))

    assert assumed == pytest.approx(162, abs=2)
    assert informed > assumed * 1.9, "the learned plan must allow roughly twice as long"
