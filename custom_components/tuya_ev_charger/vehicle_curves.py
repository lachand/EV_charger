"""Per-vehicle charge curves, persisted across restarts.

The `ChargeCurve` model is pure; this gives it a home. One curve per vehicle,
keyed by name, in a `Store` -- the same pattern as the vehicle energy tracker and
the session history, and for the same reason: the config entry is not the place
for data that grows over time.

Samples are held in memory and flushed on a timer rather than written on every
poll, because a charge produces a sample every few seconds and a `Store` write
each time would be needless disk churn.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .charge_curve import ChargeCurve

LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
# A charge samples every few seconds; persist at most this often.
SAVE_INTERVAL_S = 300.0
# The name a curve is filed under when no vehicle is selected, so single-car
# setups -- which never touch the vehicle picker -- still learn a curve.
DEFAULT_VEHICLE_KEY = "_default"


class VehicleChargeCurves:
    """Charge curves for every vehicle, loaded once and saved lazily."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"tuya_ev_charger_curves_{entry_id}"
        )
        self._curves: dict[str, ChargeCurve] = {}
        self._dirty = False
        self._last_saved_at: float | None = None

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        for name, raw in (data.get("curves") or {}).items():
            self._curves[name] = ChargeCurve.from_dict(raw)

    def _key(self, vehicle: str | None) -> str:
        return vehicle or DEFAULT_VEHICLE_KEY

    def curve_for(self, vehicle: str | None) -> ChargeCurve:
        """The curve for a vehicle, created empty on first use."""
        key = self._key(vehicle)
        curve = self._curves.get(key)
        if curve is None:
            curve = ChargeCurve()
            self._curves[key] = curve
        return curve

    def record(self, vehicle: str | None, delivered_kwh: float, power_kw: float) -> None:
        """Add one reading to a vehicle's curve, marking it for a later save."""
        self.curve_for(vehicle).record(delivered_kwh, power_kw)
        self._dirty = True

    async def async_flush(self, now: float, *, force: bool = False) -> None:
        """Persist if there is anything new and enough time has passed.

        Rate-limited on purpose: a charge would otherwise write to disk every few
        seconds. ``force`` bypasses the timer, for shutdown.
        """
        if not self._dirty:
            return
        if not force and self._last_saved_at is not None:
            if now - self._last_saved_at < SAVE_INTERVAL_S:
                return
        await self._store.async_save(
            {"curves": {name: curve.to_dict() for name, curve in self._curves.items()}}
        )
        self._dirty = False
        self._last_saved_at = now

    def points_for(self, vehicle: str | None) -> list[dict[str, float]]:
        return self.curve_for(vehicle).points()
