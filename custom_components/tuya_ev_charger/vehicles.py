"""Per-vehicle energy tracking.

The charger has no idea which car is plugged in, so the user picks the active
vehicle with a select entity and we route the charger's own lifetime counter
into that vehicle's bucket.

State lives in a ``Store`` rather than the config entry options: the entry has an
update listener that reloads the whole integration on every change (see
``_async_update_listener`` in ``__init__.py``), which would restart the
coordinator every time a counter moved.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


class VehicleEnergyTracker:
    """Accumulates the charger's lifetime energy per vehicle."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.vehicles"
        )
        self._active: str | None = None
        self._totals: dict[str, float] = {}
        self._last_counter: float | None = None

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._active = data.get("active")
        raw_totals = data.get("totals")
        if isinstance(raw_totals, dict):
            self._totals = {
                str(name): float(value)
                for name, value in raw_totals.items()
                if isinstance(value, (int, float))
            }
        last = data.get("last_counter")
        self._last_counter = float(last) if isinstance(last, (int, float)) else None

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "active": self._active,
                "totals": self._totals,
                "last_counter": self._last_counter,
            }
        )

    @property
    def active_vehicle(self) -> str | None:
        return self._active

    def total_for(self, vehicle: str) -> float:
        return round(self._totals.get(vehicle, 0.0), 3)

    async def async_set_active_vehicle(self, vehicle: str) -> None:
        if vehicle == self._active:
            return
        self._active = vehicle
        await self._async_save()

    async def async_process_counter(self, counter_kwh: float | None) -> bool:
        """Attribute the increase of the charger's session counter.

        Returns True when a vehicle total changed. The counter resets to zero at
        the start of every session, so a decrease is re-baselined rather than
        attributed as a negative delta.
        """
        if counter_kwh is None:
            return False

        previous = self._last_counter
        self._last_counter = counter_kwh

        if previous is None:
            # First reading: establish a baseline, attribute nothing.
            await self._async_save()
            return False

        delta = counter_kwh - previous
        if delta < 0:
            LOGGER.debug(
                "Charger lifetime counter went backwards (%s -> %s); re-baselining.",
                previous,
                counter_kwh,
            )
            await self._async_save()
            return False
        if delta == 0:
            return False

        vehicle = self._active
        if vehicle is None:
            await self._async_save()
            return False

        self._totals[vehicle] = round(self._totals.get(vehicle, 0.0) + delta, 3)
        await self._async_save()
        return True


def configured_vehicles(raw: Any) -> list[str]:
    """Parse the user's comma-separated vehicle list into clean names."""
    names = [name.strip() for name in str(raw or "").split(",")]
    unique: list[str] = []
    for name in names:
        if name and name not in unique:
            unique.append(name)
    return unique
