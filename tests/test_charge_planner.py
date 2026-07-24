"""Off-peak windows and departure deadlines.

The costly mistakes here are asymmetric: refusing to charge when the user needs
the car is far worse than charging an hour too early, so the deadline path is
tested hardest.
"""

from __future__ import annotations

from datetime import datetime, time

import pytest


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 22, hour, minute)


# --- parsing --------------------------------------------------------------


def test_parses_several_windows():
    from tuya_ev_charger.charge_planner import parse_windows

    assert parse_windows("22:00-06:00, 12:30-14:30") == (
        (time(22, 0), time(6, 0)),
        (time(12, 30), time(14, 30)),
    )


@pytest.mark.parametrize("raw", ["", "   ", "nonsense", "22:00", "25:00-26:00", "22:00-22:00", ","])
def test_malformed_windows_are_skipped_not_fatal(raw):
    """A typo in an option must narrow the schedule, never break startup."""
    from tuya_ev_charger.charge_planner import parse_windows

    assert parse_windows(raw) == ()


# --- window membership ----------------------------------------------------


@pytest.mark.parametrize(
    ("moment", "inside"),
    [
        (time(23, 0), True),  # inside the wrap, before midnight
        (time(2, 0), True),  # inside the wrap, after midnight
        (time(5, 59), True),
        (time(6, 0), False),  # end is exclusive
        (time(21, 59), False),
        (time(22, 0), True),  # start is inclusive
    ],
)
def test_window_wrapping_past_midnight(moment, inside):
    """Off-peak windows almost always cross midnight."""
    from tuya_ev_charger.charge_planner import is_within_windows, parse_windows

    assert is_within_windows(moment, parse_windows("22:00-06:00")) is inside


def test_daytime_window_does_not_wrap():
    from tuya_ev_charger.charge_planner import is_within_windows, parse_windows

    windows = parse_windows("12:30-14:30")
    assert is_within_windows(time(13, 0), windows) is True
    assert is_within_windows(time(23, 0), windows) is False


# --- time arithmetic ------------------------------------------------------


def test_minutes_until_crosses_midnight():
    from tuya_ev_charger.charge_planner import minutes_until

    assert minutes_until(_at(23, 0), time(7, 0)) == 480


def test_minutes_until_a_time_already_passed_today_targets_tomorrow():
    from tuya_ev_charger.charge_planner import minutes_until

    assert minutes_until(_at(9, 0), time(7, 0)) == 22 * 60


def test_minutes_needed():
    from tuya_ev_charger.charge_planner import minutes_needed

    assert minutes_needed(7.4, 7.4) == 60
    assert minutes_needed(0.0, 7.4) == 0
    assert minutes_needed(5.0, 0.0) == 0  # no power, no estimate


# --- planning -------------------------------------------------------------


def _request(**kwargs):
    from tuya_ev_charger.charge_planner import PlanRequest, parse_windows

    base = {"now": _at(20, 0), "off_peak_windows": parse_windows("22:00-06:00")}
    base.update(kwargs)
    return PlanRequest(**base)


def test_no_windows_configured_never_blocks():
    """This feature must not be why a charge fails for someone not using it."""
    from tuya_ev_charger.charge_planner import ChargeWindow, plan_charge

    plan = plan_charge(_request(off_peak_windows=()))
    assert plan.allowed is True
    assert plan.window is ChargeWindow.UNRESTRICTED


def test_charging_is_allowed_during_off_peak():
    from tuya_ev_charger.charge_planner import ChargeWindow, plan_charge

    plan = plan_charge(_request(now=_at(23, 0)))
    assert plan.allowed is True
    assert plan.window is ChargeWindow.OFF_PEAK


def test_peak_hours_wait_when_there_is_no_deadline():
    from tuya_ev_charger.charge_planner import ChargeWindow, plan_charge

    plan = plan_charge(_request(now=_at(20, 0)))
    assert plan.allowed is False
    assert plan.window is ChargeWindow.WAITING_FOR_OFF_PEAK


def test_a_comfortable_deadline_still_waits_for_off_peak():
    """20:00, leaving at 07:00, two hours of charge needed: no reason to rush."""
    from tuya_ev_charger.charge_planner import ChargeWindow, plan_charge

    plan = plan_charge(
        _request(
            now=_at(20, 0),
            departure=time(7, 0),
            energy_needed_kwh=14.8,
            charge_power_kw=7.4,
        )
    )
    assert plan.allowed is False
    assert plan.window is ChargeWindow.WAITING_FOR_OFF_PEAK
    assert plan.minutes_needed == 120


def test_a_tight_deadline_overrides_the_tariff():
    """Leaving at 22:00 needing three hours: waiting would miss it."""
    from tuya_ev_charger.charge_planner import ChargeWindow, plan_charge

    plan = plan_charge(
        _request(
            now=_at(20, 0),
            departure=time(22, 0),
            energy_needed_kwh=22.2,
            charge_power_kw=7.4,
        )
    )
    assert plan.allowed is True
    assert plan.window is ChargeWindow.DEADLINE


def test_the_safety_margin_starts_the_charge_early():
    """Exactly enough time is not enough: ramp-up and pauses eat into it."""
    from tuya_ev_charger.charge_planner import plan_charge

    # 120 minutes to departure, 110 minutes of charging needed. Without a margin
    # this would wait; with one it starts.
    plan = plan_charge(
        _request(
            now=_at(20, 0),
            departure=time(22, 0),
            energy_needed_kwh=13.6,
            charge_power_kw=7.4,
        )
    )
    assert plan.allowed is True


def test_a_deadline_without_an_energy_target_is_ignored():
    """Without knowing how much is needed, the deadline says nothing."""
    from tuya_ev_charger.charge_planner import plan_charge

    plan = plan_charge(_request(now=_at(20, 0), departure=time(22, 0), energy_needed_kwh=0.0))
    assert plan.allowed is False
