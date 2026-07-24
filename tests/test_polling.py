"""Poll cadence, and lending the charger's single connection away.

A Tuya charger accepts exactly one local connection. Polling it every 30 s whether
it is regulating a charge or asleep in an empty garage spends that slot for
nothing, and fights the Smart Life app for it.
"""

from __future__ import annotations

import asyncio
import types

import pytest


def _interval(status, *, base=30, regulating=False):
    from tuya_ev_charger.polling import poll_interval_s

    return poll_interval_s(base_interval_s=base, status=status, regulating=regulating)


def test_charging_polls_faster_than_the_configured_interval():
    """While regulating, reaction time is the feature."""
    assert _interval("charging") < 30


def test_sleeping_polls_slower_than_idle_which_is_slower_than_plugged_in():
    """The cadence must be ordered by how much can actually change."""
    assert _interval("sleep") > _interval("idle") > _interval("plugged_in")


def test_regulating_wins_over_the_reported_status():
    """The charger reports `idle` for a moment after we tell it to start; the
    loop still needs fresh readings through that gap."""
    assert _interval("idle", regulating=True) < _interval("idle")
    assert _interval("idle", regulating=True) == _interval("charging")


def test_an_unknown_status_is_treated_as_idle_not_as_charging():
    """Unrecognised means we do not know, which is no reason to hammer the single
    connection."""
    assert _interval("something_new") == _interval("idle")
    assert _interval(None) == _interval("idle")


def test_the_users_interval_is_respected_in_proportion():
    """Somebody who deliberately chose 120 s keeps that intent; only the shape
    relative to it is applied."""
    slow_charging = _interval("charging", base=120)
    fast_charging = _interval("charging", base=10)
    assert slow_charging > fast_charging


@pytest.mark.parametrize("status", ["charging", "idle", "sleep", "plugged_in", None])
def test_the_interval_stays_inside_its_bounds(status):
    """The floor protects the single connection; the ceiling keeps the charger
    from looking frozen for minutes."""
    from tuya_ev_charger.polling import MAX_INTERVAL_S, MIN_INTERVAL_S

    for base in (1, 5, 30, 300, 3600):
        value = _interval(status, base=base)
        assert MIN_INTERVAL_S <= value <= MAX_INTERVAL_S


# --- lending the connection away ------------------------------------------


def _coordinator():
    from tuya_ev_charger.coordinator import TuyaEVChargerDataUpdateCoordinator

    coordinator = TuyaEVChargerDataUpdateCoordinator.__new__(TuyaEVChargerDataUpdateCoordinator)
    clock = {"now": 1000.0}
    closes: list[int] = []

    class _Client:
        async def async_close(self):
            closes.append(1)

    coordinator.hass = types.SimpleNamespace(loop=types.SimpleNamespace(time=lambda: clock["now"]))
    coordinator.client = _Client()
    coordinator.entry = types.SimpleNamespace(title="test", entry_id="test")
    coordinator._release_until = None
    coordinator.data = "last-known"
    coordinator.clock = clock
    coordinator.closes = closes
    return coordinator


def test_releasing_closes_the_socket_and_stops_polling():
    """Closing matters as much as pausing: an open socket keeps the slot even if
    we stop reading from it."""
    coordinator = _coordinator()
    asyncio.run(coordinator.async_release_connection(duration_s=600))

    assert coordinator.closes == [1]
    assert coordinator.connection_released is True


def test_entities_keep_their_values_while_the_connection_is_lent_out():
    """The charger is not faulty, it is lent out -- so entities must hold rather
    than go unavailable."""
    coordinator = _coordinator()
    asyncio.run(coordinator.async_release_connection(duration_s=600))

    assert asyncio.run(coordinator._async_handle_release()) == "last-known"


def test_polling_resumes_on_its_own_when_the_release_expires():
    """It has to expire by itself: a user who forgets would otherwise be left
    with a permanently dead integration."""
    coordinator = _coordinator()
    asyncio.run(coordinator.async_release_connection(duration_s=600))

    coordinator.clock["now"] += 601
    assert coordinator.connection_released is False
    # None tells the update loop to poll normally again.
    assert asyncio.run(coordinator._async_handle_release()) is None
    assert coordinator._release_until is None


def test_the_release_can_be_cancelled_early():
    coordinator = _coordinator()
    asyncio.run(coordinator.async_release_connection(duration_s=600))
    coordinator.async_resume_connection()
    assert coordinator.connection_released is False
