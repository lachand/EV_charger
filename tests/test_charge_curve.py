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


# --- the taper curve -------------------------------------------------------


def _curve():
    from tuya_ev_charger.charge_curve import ChargeCurve

    return ChargeCurve()


def _teach(curve, samples, *, repeat=5):
    """Feed (delivered_kwh, power_kw) pairs enough times to fill their buckets."""
    for _ in range(repeat):
        for delivered, power in samples:
            curve.record(delivered, power)
    return curve


def test_a_bucket_needs_several_samples_before_it_is_trusted():
    from tuya_ev_charger.charge_curve import MIN_BUCKET_SAMPLES

    curve = _curve()
    for _ in range(MIN_BUCKET_SAMPLES - 1):
        curve.record(1.0, 7.0)
    assert curve.power_at(1.0) is None

    curve.record(1.0, 7.0)
    assert curve.power_at(1.0) == pytest.approx(7.0)


def test_the_curve_captures_a_taper():
    """High and flat, then falling: exactly what a filling battery does."""
    curve = _teach(
        _curve(),
        [(1.0, 7.4), (5.0, 7.4), (9.0, 7.4), (13.0, 4.0), (15.0, 2.0)],
    )
    assert curve.power_at(1.0) == pytest.approx(7.4, abs=0.1)
    assert curve.power_at(13.0) == pytest.approx(4.0, abs=0.3)
    assert curve.power_at(15.0) == pytest.approx(2.0, abs=0.3)
    # The shape falls, it does not rise.
    assert curve.power_at(15.0) < curve.power_at(1.0)


def test_low_power_samples_are_ignored():
    """A 0-power reading between phases, or a deliberate 6 A surplus session, must
    not drag the modelled capability down."""
    from tuya_ev_charger.charge_curve import MIN_SAMPLE_KW

    curve = _curve()
    for _ in range(10):
        curve.record(1.0, MIN_SAMPLE_KW - 0.1)
    assert curve.power_at(1.0) is None


def test_a_gap_between_buckets_falls_back_to_the_last_known_power():
    curve = _curve()
    _teach(curve, [(1.0, 7.0)])  # only the first bucket is filled
    # 5 kWh delivered has no bucket of its own yet; use the last one learnt.
    assert curve.power_at(5.0) == pytest.approx(7.0)


def test_minutes_for_a_flat_curve_matches_the_simple_calculation():
    curve = _teach(_curve(), [(d, 7.4) for d in (1.0, 3.0, 5.0, 7.0, 9.0)])
    # 7.4 kWh at a flat 7.4 kW is one hour.
    assert curve.minutes_for(0.0, 7.4) == pytest.approx(60, abs=2)


def test_minutes_for_charges_in_the_taper_take_longer():
    """The whole point: finishing near the top is slower than the flat rate says."""
    curve = _teach(
        _curve(),
        [(1.0, 7.0), (3.0, 7.0), (5.0, 7.0), (11.0, 2.0), (13.0, 2.0), (15.0, 2.0)],
    )
    # Adding 6 kWh from empty is mostly at 7 kW.
    from_bottom = curve.minutes_for(0.0, 6.0)
    # Adding 6 kWh starting already at 10 kWh delivered is mostly in the 2 kW taper.
    from_top = curve.minutes_for(10.0, 6.0)
    assert from_top > from_bottom * 2


def test_minutes_for_returns_none_when_the_curve_does_not_cover_the_range():
    """Better to defer to the flat rate than integrate over unknown buckets."""
    curve = _teach(_curve(), [(1.0, 7.0)])
    assert curve.minutes_for(20.0, 5.0) is None


def test_the_curve_survives_serialisation():
    curve = _teach(_curve(), [(1.0, 7.4), (13.0, 3.0)])
    from tuya_ev_charger.charge_curve import ChargeCurve

    revived = ChargeCurve.from_dict(curve.to_dict())
    assert revived.power_at(1.0) == pytest.approx(curve.power_at(1.0))
    assert revived.power_at(13.0) == pytest.approx(curve.power_at(13.0))
    assert revived.points() == curve.points()


def test_points_lists_only_filled_buckets_in_order():
    curve = _curve()
    _teach(curve, [(1.0, 7.0), (13.0, 3.0)])
    curve.record(5.0, 6.0)  # a single sample, not enough
    points = curve.points()
    assert [p["delivered_kwh"] for p in points] == [0.0, 12.0]


def test_recent_sessions_move_the_curve():
    """A car swapped for a slower one must be followed, not averaged forever."""
    curve = _teach(_curve(), [(1.0, 7.4)], repeat=10)
    for _ in range(40):
        curve.record(1.0, 3.0)
    assert curve.power_at(1.0) < 5.0
