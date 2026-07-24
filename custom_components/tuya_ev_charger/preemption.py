"""Reserving power for a load that has announced itself but not yet arrived.

2.13.1 shipped the inverter cap with an honest limitation: a cap can only ever be
as fast as the sensor feeding it, and an induction hob is a ~2 kW step in well
under a second. Reacting to the measurement means reacting after the inverter has
already been overloaded.

But Home Assistant usually knows *before* the meter does. A hob switch turns on,
a smart plug reports `on`, an oven door closes. Those are announcements, available
immediately, and holding back their expected draw the moment they appear turns a
race against the sensor into something that can be won.

A reservation is a **bridge over the sensor's latency, not a standing
allowance**. It applies for a bounded window and then expires, because by then
the appliance is in the measurement and counting it twice would halve the car's
current for as long as the hob stayed on. That expiry is the whole design.

Pure: entity states, a reservation table and a clock in, watts out.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

# States meaning "this appliance is drawing power now". Anything else -- `off`,
# `unavailable`, `unknown` -- reserves nothing: a reservation that never cleared
# would permanently shrink the car's budget.
ACTIVE_STATES = frozenset({"on", "true", "open", "heat", "cool", "running"})

# How long a reservation stands before the measurement takes over. Long enough
# for a slow cloud-polled power sensor to catch up, short enough that a
# mis-declared appliance cannot hold the car down for long.
DEFAULT_RESERVATION_WINDOW_S = 120.0


def parse_reservations(raw: str) -> dict[str, float]:
    """Parse ``switch.hob: 3000, switch.oven: 2500`` into a reservation table.

    Malformed entries are skipped rather than raising, like the off-peak windows:
    a typo should narrow the feature, never stop the integration loading. A
    non-positive wattage is dropped, since reserving 0 W is the same as not
    listing the entity at all.
    """
    table: dict[str, float] = {}
    for chunk in str(raw or "").split(","):
        piece = chunk.strip()
        if not piece or ":" not in piece:
            continue
        entity_id, _, watts = piece.rpartition(":")
        entity_id = entity_id.strip()
        # An entity id is `domain.object_id`; without the dot it is not one.
        if "." not in entity_id:
            continue
        try:
            value = float(watts.strip())
        except ValueError:
            continue
        if value > 0:
            table[entity_id] = value
    return table


def is_announcing(state: str | None) -> bool:
    """Whether an entity's state means the appliance is drawing power."""
    if state is None:
        return False
    return str(state).strip().lower() in ACTIVE_STATES


@dataclass(slots=True)
class ReservationTracker:
    """Remembers when each appliance announced itself.

    Mutable, like `TimerState`: a reservation is inherently about elapsed time.
    Kept out of the controller so the expiry can be exercised without Home
    Assistant.
    """

    window_s: float = DEFAULT_RESERVATION_WINDOW_S
    announced_at: dict[str, float] = field(default_factory=dict)

    def observe(
        self,
        reservations: Mapping[str, float],
        states: Mapping[str, str | None],
        now: float,
    ) -> None:
        """Note which appliances are announcing, and since when."""
        for entity_id in reservations:
            if is_announcing(states.get(entity_id)):
                # Only the *first* sighting starts the clock, so a long-running
                # appliance does not keep renewing its own reservation.
                self.announced_at.setdefault(entity_id, now)
            else:
                # Switched off: forget it, so a later switch-on reserves again.
                self.announced_at.pop(entity_id, None)

    def reserved_w(self, reservations: Mapping[str, float], now: float) -> float:
        """Watts still to hold back, ignoring reservations that have expired."""
        return sum(
            watts
            for entity_id, watts in reservations.items()
            if self._within_window(entity_id, now)
        )

    def active(self, reservations: Mapping[str, float], now: float) -> dict[str, float]:
        """The reservations currently applying, for the decision trace."""
        return {
            entity_id: watts
            for entity_id, watts in reservations.items()
            if self._within_window(entity_id, now)
        }

    def _within_window(self, entity_id: str, now: float) -> bool:
        started = self.announced_at.get(entity_id)
        if started is None:
            return False
        return (now - started) < self.window_s

    def reset(self) -> None:
        self.announced_at.clear()


def headroom_with_reservations(
    *,
    limit_w: float,
    measured_load_w: float,
    ev_power_w: float,
    reserved_w: float,
) -> float:
    """Power left for the car, honouring announcements not yet measured.

    The car's own draw is removed from the measured load to get what the rest of
    the house is using; the reservation is then subtracted on top, because while
    the window is open the announced appliance is by definition *not* in that
    measurement yet. Once the window closes the reservation is zero and this is
    the plain measured headroom again.
    """
    house_without_car_w = measured_load_w - ev_power_w
    return limit_w - house_without_car_w - reserved_w
