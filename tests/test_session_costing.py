"""Session cost estimation.

The charger reports a completed session as "N kWh over M seconds" with no
timestamps, so the off-peak split is reconstructed. These tests pin down that
reconstruction, and the boundary where it should decline to answer at all.
"""

from __future__ import annotations

from datetime import datetime

import pytest


def _at(hour, minute=0):
    return datetime(2026, 7, 22, hour, minute)


def _windows(raw="22:00-06:00"):
    from tuya_ev_charger.charge_planner import parse_windows

    return parse_windows(raw)


def _split(**kwargs):
    from tuya_ev_charger.session_costing import split_session

    kwargs.setdefault("off_peak_windows", _windows())
    return split_session(**kwargs)


# --- splitting ------------------------------------------------------------


def test_a_session_entirely_inside_off_peak():
    split = _split(ended_at=_at(3, 0), duration_s=2 * 3600)
    assert (split.off_peak_minutes, split.peak_minutes) == (120, 0)
    assert split.off_peak_fraction == 1.0


def test_a_session_entirely_outside_off_peak():
    split = _split(ended_at=_at(20, 0), duration_s=2 * 3600)
    assert (split.off_peak_minutes, split.peak_minutes) == (0, 120)


def test_a_session_straddling_the_start_of_off_peak():
    """21:00 to 23:00: one hour peak, then one hour off-peak."""
    split = _split(ended_at=_at(23, 0), duration_s=2 * 3600)
    assert (split.off_peak_minutes, split.peak_minutes) == (60, 60)


def test_a_session_straddling_midnight_stays_off_peak():
    """23:00 to 01:00 is off-peak throughout, despite the date change."""
    split = _split(ended_at=_at(1, 0), duration_s=2 * 3600)
    assert split.peak_minutes == 0


def test_a_session_straddling_the_end_of_off_peak():
    """05:00 to 07:00: one hour off-peak, then one hour peak."""
    split = _split(ended_at=_at(7, 0), duration_s=2 * 3600)
    assert (split.off_peak_minutes, split.peak_minutes) == (60, 60)


def test_without_windows_everything_is_peak():
    split = _split(ended_at=_at(3, 0), duration_s=3600, off_peak_windows=())
    assert (split.off_peak_minutes, split.peak_minutes) == (0, 60)


@pytest.mark.parametrize("duration", [0, 30, -100])
def test_a_session_shorter_than_a_minute_has_no_split(duration):
    split = _split(ended_at=_at(3, 0), duration_s=duration)
    assert split.total_minutes == 0
    assert split.off_peak_fraction == 0.0


# --- costing --------------------------------------------------------------


def _cost(**kwargs):
    from tuya_ev_charger.session_costing import session_cost

    kwargs.setdefault("off_peak_price", 0.16)
    kwargs.setdefault("peak_price", 0.27)
    return session_cost(**kwargs)


def test_cost_of_a_fully_off_peak_session():
    from tuya_ev_charger.session_costing import SessionSplit

    assert _cost(energy_kwh=10.0, split=SessionSplit(120, 0)) == 1.6


def test_cost_of_a_fully_peak_session():
    from tuya_ev_charger.session_costing import SessionSplit

    assert _cost(energy_kwh=10.0, split=SessionSplit(0, 120)) == 2.7


def test_energy_is_apportioned_by_time():
    """Half the minutes off-peak means half the kWh billed off-peak."""
    from tuya_ev_charger.session_costing import SessionSplit

    assert _cost(energy_kwh=10.0, split=SessionSplit(60, 60)) == pytest.approx(2.15)


def test_no_price_configured_returns_none_not_zero():
    """A sensor stuck at 0 EUR reads as free electricity, not as unconfigured."""
    from tuya_ev_charger.session_costing import SessionSplit

    assert (
        _cost(energy_kwh=10.0, split=SessionSplit(60, 60), off_peak_price=0, peak_price=0) is None
    )


def test_only_a_peak_price_is_enough_to_produce_a_cost():
    """Most users on a flat tariff will fill in one price and leave the other."""
    from tuya_ev_charger.session_costing import SessionSplit

    assert (
        _cost(energy_kwh=10.0, split=SessionSplit(0, 120), off_peak_price=0, peak_price=0.27) == 2.7
    )


def test_a_session_with_no_energy_costs_nothing():
    from tuya_ev_charger.session_costing import SessionSplit

    assert _cost(energy_kwh=0.0, split=SessionSplit(0, 60)) == 0.0


def test_end_to_end_an_overnight_charge():
    """21:30 to 05:30, 30 kWh: 30 min peak, 450 min off-peak."""
    from tuya_ev_charger.session_costing import session_cost, split_session

    split = split_session(ended_at=_at(5, 30), duration_s=8 * 3600, off_peak_windows=_windows())
    assert (split.off_peak_minutes, split.peak_minutes) == (450, 30)

    cost = session_cost(energy_kwh=30.0, split=split, off_peak_price=0.16, peak_price=0.27)
    # 28.125 kWh off-peak, 1.875 kWh peak.
    assert cost == pytest.approx(4.5 + 0.50625, abs=1e-4)
