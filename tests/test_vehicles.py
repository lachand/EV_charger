"""Per-vehicle energy accounting.

This is the only module whose bugs would show up as wrong numbers rather than
errors, so it is worth pinning down precisely. The counter it consumes is the
charger's *session* energy, which resets to zero at the start of every session.
"""

from __future__ import annotations

import asyncio

import pytest


class _MemoryStore:
    """Stands in for Home Assistant's Store, keeping the payload in memory."""

    def __init__(self, *args, **kwargs):
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


@pytest.fixture
def tracker(monkeypatch):
    from tuya_ev_charger import vehicles

    monkeypatch.setattr(vehicles, "Store", _MemoryStore)
    instance = vehicles.VehicleEnergyTracker(hass=None, entry_id="test")
    asyncio.run(instance.async_load())
    return instance


def _feed(tracker, *readings):
    async def _run():
        for reading in readings:
            await tracker.async_process_counter(reading)

    asyncio.run(_run())


def test_configured_vehicles_parsing():
    from tuya_ev_charger.vehicles import configured_vehicles

    assert configured_vehicles("Zoe, Kangoo") == ["Zoe", "Kangoo"]
    assert configured_vehicles("  Zoe ,, Kangoo  ") == ["Zoe", "Kangoo"]
    assert configured_vehicles("Zoe, Zoe") == ["Zoe"]  # deduplicated
    assert configured_vehicles("") == []
    assert configured_vehicles(None) == []


def test_first_reading_only_sets_a_baseline(tracker):
    """The counter is already part-way through when we first see it."""
    asyncio.run(tracker.async_set_active_vehicle("Zoe"))
    _feed(tracker, 5.0)
    assert tracker.total_for("Zoe") == 0.0


def test_increments_are_attributed_to_the_active_vehicle(tracker):
    asyncio.run(tracker.async_set_active_vehicle("Zoe"))
    _feed(tracker, 5.0, 6.0, 8.5)
    assert tracker.total_for("Zoe") == pytest.approx(3.5)


def test_a_session_reset_is_not_a_negative_delta(tracker):
    """The counter drops to zero when a new session starts.

    Treating that as a delta would subtract a whole session from the total.
    """
    asyncio.run(tracker.async_set_active_vehicle("Zoe"))
    _feed(tracker, 2.0, 5.0)      # first session: +3
    _feed(tracker, 0.0)           # new session begins
    _feed(tracker, 4.0)           # second session: +4
    assert tracker.total_for("Zoe") == pytest.approx(7.0)


def test_switching_vehicles_splits_the_energy(tracker):
    asyncio.run(tracker.async_set_active_vehicle("Zoe"))
    _feed(tracker, 0.0, 4.0)
    asyncio.run(tracker.async_set_active_vehicle("Kangoo"))
    _feed(tracker, 10.0)

    assert tracker.total_for("Zoe") == pytest.approx(4.0)
    assert tracker.total_for("Kangoo") == pytest.approx(6.0)


def test_energy_charged_with_no_vehicle_selected_is_dropped(tracker):
    """Better to lose it than to attribute it to the wrong car."""
    _feed(tracker, 1.0, 5.0)
    assert tracker.active_vehicle is None


def test_missing_readings_are_ignored(tracker):
    asyncio.run(tracker.async_set_active_vehicle("Zoe"))
    _feed(tracker, 2.0, None, 4.0)
    assert tracker.total_for("Zoe") == pytest.approx(2.0)


def test_totals_survive_a_restart(tracker, monkeypatch):
    from tuya_ev_charger import vehicles

    asyncio.run(tracker.async_set_active_vehicle("Zoe"))
    _feed(tracker, 0.0, 6.0)

    # Same backing store, a fresh tracker: this is what a restart looks like.
    saved = tracker._store.data
    monkeypatch.setattr(vehicles, "Store", _MemoryStore)
    revived = vehicles.VehicleEnergyTracker(hass=None, entry_id="test")
    revived._store.data = saved
    asyncio.run(revived.async_load())

    assert revived.total_for("Zoe") == pytest.approx(6.0)
    assert revived.active_vehicle == "Zoe"


def test_unknown_vehicle_reads_as_zero(tracker):
    assert tracker.total_for("Never seen") == 0.0
