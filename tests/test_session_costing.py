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


# --- a negative price is a real tariff ------------------------------------------------


def test_a_negative_off_peak_price_is_costed_not_treated_as_unpriced():
    """Paid to consume at certain hours: the session earns money, it is not "free"."""
    from tuya_ev_charger.session_costing import SessionSplit

    cost = _cost(energy_kwh=10.0, split=SessionSplit(120, 0), off_peak_price=-0.05, peak_price=0)

    assert cost == -0.5


def test_a_negative_price_beside_a_positive_one_is_blended_by_time():
    from tuya_ev_charger.session_costing import SessionSplit

    cost = _cost(energy_kwh=10.0, split=SessionSplit(60, 60), off_peak_price=-0.10, peak_price=0.30)

    assert cost == 1.0  # 5 kWh at -0.10 and 5 kWh at 0.30


def test_only_two_exactly_zero_prices_mean_unpriced():
    from tuya_ev_charger.session_costing import SessionSplit

    assert (
        _cost(energy_kwh=10.0, split=SessionSplit(60, 60), off_peak_price=0, peak_price=0) is None
    )
    assert (
        _cost(energy_kwh=10.0, split=SessionSplit(60, 60), off_peak_price=-0.01, peak_price=0)
        is not None
    )


# --- metering as the energy flows -------------------------------------------------


def _meter(**kwargs):
    from tuya_ev_charger.session_costing import SessionMeter

    return SessionMeter(**kwargs)


def _feed(meter, counter, **kwargs):
    kwargs.setdefault("off_peak", None)
    kwargs.setdefault("grid_price", None)
    return meter.add(counter, **kwargs)


def test_the_first_reading_is_a_baseline():
    meter = _meter()

    _feed(meter, 12.3, grid_price=0.2)

    assert meter.energy_kwh == 0.0
    assert meter.cost == 0.0


def test_each_increment_is_priced_at_the_rate_in_force():
    """The night of 2026-10-20: off-peak until 06:00, a short top-up after."""
    meter = _meter()
    _feed(meter, 0.0)
    _feed(meter, 15.20, off_peak=True, grid_price=0.1356)
    _feed(meter, 15.63, off_peak=False, grid_price=0.1654)

    assert meter.off_peak_kwh == pytest.approx(15.20)
    assert meter.peak_kwh == pytest.approx(0.43)
    assert meter.cost == pytest.approx(2.13, abs=0.005)


def test_a_pause_and_restart_does_not_clear_the_session():
    """The bug this replaces: the tally was cleared on every charge start."""
    meter = _meter()
    _feed(meter, 0.0)
    _feed(meter, 10.18, off_peak=True, grid_price=0.1356)
    _feed(meter, 10.18, off_peak=True, grid_price=0.1356)  # car full, waiting
    _feed(meter, 12.07, off_peak=False, grid_price=0.1654)  # topped up after 06:00

    assert meter.energy_kwh == pytest.approx(12.07)
    assert meter.cost == pytest.approx(10.18 * 0.1356 + 1.89 * 0.1654)


def test_a_counter_that_drops_starts_a_new_session():
    meter = _meter()
    _feed(meter, 0.0)
    _feed(meter, 8.0, grid_price=0.2)

    _feed(meter, 0.5, grid_price=0.3)

    assert meter.energy_kwh == pytest.approx(0.5)
    assert meter.cost == pytest.approx(0.15)


def test_an_unreadable_price_keeps_the_last_one():
    meter = _meter()
    _feed(meter, 0.0)
    _feed(meter, 1.0, grid_price=0.2)
    _feed(meter, 2.0, grid_price=None)

    assert meter.cost == pytest.approx(0.4)
    assert meter.estimated is False


def test_grid_energy_with_no_price_ever_read_is_flagged():
    meter = _meter()
    _feed(meter, 0.0)
    _feed(meter, 1.0, grid_price=None)

    assert meter.cost == 0.0
    assert meter.estimated is True


def test_energy_the_meter_missed_is_added_at_the_current_rate_and_flagged():
    meter = _meter()
    _feed(meter, 0.0)
    _feed(meter, 3.0, off_peak=True, grid_price=0.1)

    meter.add_unsampled(1.0, off_peak=False, grid_price=0.3)

    assert (meter.off_peak_kwh, meter.peak_kwh) == pytest.approx((3.0, 1.0))
    assert meter.cost == pytest.approx(0.6)
    assert meter.estimated is True


def test_clearing_keeps_the_counter_baseline():
    meter = _meter()
    _feed(meter, 0.0)
    _feed(meter, 5.0, grid_price=0.2)

    meter.clear()
    _feed(meter, 5.0, grid_price=0.2)  # same counter after unplug: nothing new

    assert meter.energy_kwh == 0.0
    assert meter.counter_kwh == 5.0


def test_the_meter_survives_a_round_trip_through_storage():
    from tuya_ev_charger.session_costing import SessionMeter

    meter = _meter()
    _feed(meter, 0.0)
    _feed(meter, 2.0, off_peak=True, grid_price=0.1)

    restored = SessionMeter.from_stored(meter.to_stored())

    assert restored == meter
    assert SessionMeter.from_stored(None) == SessionMeter()
    assert SessionMeter.from_stored({"cost": "x", "peak_kwh": -1}) == SessionMeter()
