"""Reserving power for an announced load (issue #22's remaining limitation).

The inverter cap can only react as fast as its sensor, and a hob is +2 kW in
under a second. Reservations bridge that gap — but a reservation that outlives
the sensor's catch-up would be counted twice and halve the car's current for as
long as the appliance stayed on. The expiry is therefore the part worth testing
hardest.
"""

from __future__ import annotations

import pytest


def _tracker(window_s=120.0):
    from tuya_ev_charger.preemption import ReservationTracker

    return ReservationTracker(window_s=window_s)


TABLE = {"switch.hob": 3000.0, "switch.oven": 2500.0}


# --- parsing ---------------------------------------------------------------


def test_parsing_a_reservation_table():
    from tuya_ev_charger.preemption import parse_reservations

    assert parse_reservations("switch.hob: 3000, switch.oven:2500") == {
        "switch.hob": 3000.0,
        "switch.oven": 2500.0,
    }


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "switch.hob",  # no wattage
        "hob: 3000",  # not an entity id
        "switch.hob: abc",  # not a number
        "switch.hob: 0",  # reserving nothing is the same as not listing it
        "switch.hob: -500",
        ",",
    ],
)
def test_malformed_entries_are_skipped_not_fatal(raw):
    """A typo must narrow the feature, never stop the integration loading."""
    from tuya_ev_charger.preemption import parse_reservations

    assert parse_reservations(raw) == {}


def test_one_bad_entry_does_not_discard_the_good_ones():
    from tuya_ev_charger.preemption import parse_reservations

    assert parse_reservations("switch.hob: 3000, nonsense, switch.oven: 2500") == {
        "switch.hob": 3000.0,
        "switch.oven": 2500.0,
    }


# --- which states announce -------------------------------------------------


@pytest.mark.parametrize("state", ["on", "ON", " on ", "true", "heat", "running"])
def test_states_that_announce(state):
    from tuya_ev_charger.preemption import is_announcing

    assert is_announcing(state) is True


@pytest.mark.parametrize("state", ["off", "unavailable", "unknown", "", None, "idle"])
def test_states_that_do_not_announce(state):
    """`unavailable` in particular: a dropped integration must not permanently
    shrink the car's budget."""
    from tuya_ev_charger.preemption import is_announcing

    assert is_announcing(state) is False


# --- the reservation window ------------------------------------------------


def test_an_announcement_reserves_immediately():
    tracker = _tracker()
    tracker.observe(TABLE, {"switch.hob": "on"}, now=1000.0)
    assert tracker.reserved_w(TABLE, now=1000.0) == 3000.0


def test_the_reservation_expires_so_it_is_not_counted_twice():
    """By then the hob is in the measurement. Holding the reservation as well
    would halve the car's current for as long as the hob stayed on."""
    tracker = _tracker(window_s=120.0)
    tracker.observe(TABLE, {"switch.hob": "on"}, now=1000.0)

    assert tracker.reserved_w(TABLE, now=1119.0) == 3000.0
    assert tracker.reserved_w(TABLE, now=1121.0) == 0.0


def test_staying_on_does_not_renew_the_reservation():
    """Only the first sighting starts the clock; otherwise an appliance left on
    would hold its reservation forever."""
    tracker = _tracker(window_s=120.0)
    for moment in range(1000, 1120, 10):
        tracker.observe(TABLE, {"switch.hob": "on"}, now=float(moment))

    assert tracker.reserved_w(TABLE, now=1121.0) == 0.0


def test_switching_off_and_on_again_reserves_afresh():
    tracker = _tracker(window_s=120.0)
    tracker.observe(TABLE, {"switch.hob": "on"}, now=1000.0)
    tracker.observe(TABLE, {"switch.hob": "off"}, now=1200.0)
    assert tracker.reserved_w(TABLE, now=1200.0) == 0.0

    tracker.observe(TABLE, {"switch.hob": "on"}, now=1300.0)
    assert tracker.reserved_w(TABLE, now=1300.0) == 3000.0


def test_several_appliances_stack():
    tracker = _tracker()
    tracker.observe(TABLE, {"switch.hob": "on", "switch.oven": "on"}, now=1000.0)
    assert tracker.reserved_w(TABLE, now=1000.0) == 5500.0


def test_an_entity_that_disappears_reserves_nothing():
    tracker = _tracker()
    tracker.observe(TABLE, {}, now=1000.0)
    assert tracker.reserved_w(TABLE, now=1000.0) == 0.0


def test_active_lists_what_is_reserving():
    tracker = _tracker()
    tracker.observe(TABLE, {"switch.hob": "on", "switch.oven": "off"}, now=1000.0)
    assert tracker.active(TABLE, now=1000.0) == {"switch.hob": 3000.0}


# --- the arithmetic --------------------------------------------------------


def test_a_reservation_shrinks_the_car_budget_before_the_meter_moves():
    """The scenario from #22: 6 kW inverter, car at 5 kW, hob just switched on
    and not yet visible to the sensor."""
    from tuya_ev_charger.preemption import headroom_with_reservations

    # Nothing reserved: the meter still shows only the car plus a small baseline,
    # so the car looks entitled to keep its 5 kW.
    without = headroom_with_reservations(
        limit_w=5500.0, measured_load_w=5500.0, ev_power_w=5000.0, reserved_w=0.0
    )
    assert without == 5000.0

    # With the hob's 3 kW reserved, the budget collapses at once.
    with_reservation = headroom_with_reservations(
        limit_w=5500.0, measured_load_w=5500.0, ev_power_w=5000.0, reserved_w=3000.0
    )
    assert with_reservation == 2000.0


def test_once_the_window_closes_the_measurement_alone_decides():
    """The same instant with the hob now measured and the reservation expired
    must give the same answer -- otherwise the two would disagree at the seam."""
    from tuya_ev_charger.preemption import headroom_with_reservations

    # Hob now in the reading: house is 500 baseline + 3000 hob + 5000 car.
    measured = headroom_with_reservations(
        limit_w=5500.0, measured_load_w=8500.0, ev_power_w=5000.0, reserved_w=0.0
    )
    assert measured == 2000.0


def test_a_reservation_can_drive_the_budget_negative():
    """Which is what stops the charge outright, and is correct: the announced
    load alone already exceeds the limit."""
    from tuya_ev_charger.preemption import headroom_with_reservations

    assert (
        headroom_with_reservations(
            limit_w=5500.0, measured_load_w=5500.0, ev_power_w=5000.0, reserved_w=6000.0
        )
        < 0
    )


def test_reset_forgets_every_announcement():
    tracker = _tracker()
    tracker.observe(TABLE, {"switch.hob": "on"}, now=1000.0)
    tracker.reset()
    assert tracker.reserved_w(TABLE, now=1000.0) == 0.0
