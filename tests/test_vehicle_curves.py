"""Per-vehicle charge-curve storage.

Two behaviours matter beyond load/save: a single-car setup that never touches the
vehicle picker must still learn a curve, and the store must not be written on
every poll.
"""

from __future__ import annotations

import asyncio

import pytest


class _MemoryStore:
    def __init__(self, *args, **kwargs):
        self.data = None
        self.saves = 0

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data
        self.saves += 1


@pytest.fixture
def curves(monkeypatch):
    from tuya_ev_charger import vehicle_curves

    monkeypatch.setattr(vehicle_curves, "Store", _MemoryStore)
    instance = vehicle_curves.VehicleChargeCurves(hass=None, entry_id="test")
    asyncio.run(instance.async_load())
    return instance


def _teach(curves, vehicle, *, delivered=1.0, power=7.0, times=5):
    for _ in range(times):
        curves.record(vehicle, delivered, power)


def test_a_single_car_setup_learns_under_a_default_key(curves):
    """No vehicle selected must still produce a curve, or single-car users never
    benefit."""
    _teach(curves, None)
    assert curves.points_for(None), "the default vehicle learnt nothing"


def test_vehicles_keep_separate_curves(curves):
    _teach(curves, "Zoe", power=3.0)
    _teach(curves, "Kangoo", power=7.0)
    assert curves.points_for("Zoe")[0]["power_kw"] == pytest.approx(3.0, abs=0.2)
    assert curves.points_for("Kangoo")[0]["power_kw"] == pytest.approx(7.0, abs=0.2)


def test_nothing_is_written_before_the_save_interval(curves):
    _teach(curves, "Zoe")
    asyncio.run(curves.async_flush(now=100.0))
    asyncio.run(curves.async_flush(now=200.0))  # within the interval
    assert curves._store.saves == 1, "wrote to disk more than once in the interval"


def test_a_forced_flush_ignores_the_interval(curves):
    """Shutdown must persist immediately whatever the timer says."""
    _teach(curves, "Zoe")
    asyncio.run(curves.async_flush(now=100.0))
    curves.record("Zoe", 3.0, 6.0)
    asyncio.run(curves.async_flush(now=101.0, force=True))
    assert curves._store.saves == 2


def test_a_clean_curve_set_is_not_written(curves):
    """No samples, no write."""
    asyncio.run(curves.async_flush(now=100.0, force=True))
    assert curves._store.saves == 0


def test_curves_survive_a_restart(curves, monkeypatch):
    from tuya_ev_charger import vehicle_curves

    _teach(curves, "Zoe", power=3.7)
    asyncio.run(curves.async_flush(now=100.0, force=True))

    store = curves._store
    monkeypatch.setattr(vehicle_curves, "Store", lambda *a, **kw: store)
    revived = vehicle_curves.VehicleChargeCurves(hass=None, entry_id="test")
    asyncio.run(revived.async_load())
    assert revived.points_for("Zoe")[0]["power_kw"] == pytest.approx(3.7, abs=0.2)
