"""What a charging session cost, as pure arithmetic.

Same shape as `surplus_decision` and `charge_planner`: values in, values out, no
Home Assistant.

The charger reports a completed session as a duration and an energy total, with
no timestamps and no breakdown. So the split between off-peak and peak is
reconstructed from the session's wall-clock window and the configured off-peak
ranges, and energy is apportioned by time. That assumes a roughly constant
charging power across the session, which is true for a wallbox holding a
setpoint and false while a nearly-full car tapers — the estimate is documented
as an estimate rather than dressed up as a meter reading.

That reconstruction is now the fallback. `SessionMeter` prices the energy while
it flows, poll by poll, and splits it between grid, house battery and solar; it
is used whenever it saw the session.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from typing import Any

from .charge_planner import is_within_windows

# Sampling step for the off-peak split. One minute is finer than any tariff
# boundary and keeps the walk trivial: a two-hour session is 120 iterations.
_STEP = timedelta(minutes=1)


@dataclass(slots=True, frozen=True)
class SessionSplit:
    off_peak_minutes: int
    peak_minutes: int

    @property
    def total_minutes(self) -> int:
        return self.off_peak_minutes + self.peak_minutes

    @property
    def off_peak_fraction(self) -> float:
        """Share of the session spent off-peak, 0.0 when it had no duration."""
        if self.total_minutes <= 0:
            return 0.0
        return self.off_peak_minutes / self.total_minutes


def split_session(
    *,
    ended_at: datetime,
    duration_s: int,
    off_peak_windows: tuple[tuple[time, time], ...],
) -> SessionSplit:
    """Split a session into off-peak and peak minutes.

    Anchored on the end rather than the start because that is what is actually
    known: the charger announces a completed session, so "now" is the end and
    the start is inferred.
    """
    minutes = max(0, int(duration_s) // 60)
    if minutes == 0:
        return SessionSplit(0, 0)
    if not off_peak_windows:
        return SessionSplit(0, minutes)

    started_at = ended_at - timedelta(seconds=int(duration_s))
    off_peak = 0
    moment = started_at
    for _ in range(minutes):
        if is_within_windows(moment.time(), off_peak_windows):
            off_peak += 1
        moment += _STEP
    return SessionSplit(off_peak, minutes - off_peak)


def session_cost(
    *,
    energy_kwh: float,
    split: SessionSplit,
    off_peak_price: float,
    peak_price: float,
) -> float | None:
    """Cost of a session, or None when there is no price to apply.

    Returns None rather than 0.0 when unpriced: a sensor showing 0 € for every
    session looks like a working meter reporting free electricity, which is
    worse than showing nothing. "Unpriced" means both prices are exactly zero;
    a negative price is a real tariff (paid to consume) and is costed, so the
    cost can come out negative.
    """
    if peak_price == 0 and off_peak_price == 0:
        return None
    if energy_kwh <= 0:
        return 0.0

    # With no windows configured everything is peak, and the split reflects that
    # already — no special case needed.
    off_peak_kwh = energy_kwh * split.off_peak_fraction
    peak_kwh = energy_kwh - off_peak_kwh
    return round(off_peak_kwh * off_peak_price + peak_kwh * peak_price, 4)


def split_increment(
    energy_kwh: float,
    *,
    ev_power_w: float,
    grid_power_w: float | None,
    battery_discharge_w: float | None,
) -> tuple[float, float, float]:
    """Where an energy increment came from: (grid, battery, solar) kWh.

    The car is treated as the last load in the house: it only takes the grid
    power the house is importing at that moment, then what the house battery is
    discharging, and the rest is solar that would otherwise have been exported.
    `grid_power_w` is positive when importing; without it, or without a power
    reading to apportion by, everything is grid.
    """
    if grid_power_w is None or ev_power_w <= 0:
        return energy_kwh, 0.0, 0.0
    grid = energy_kwh * min(1.0, max(0.0, grid_power_w) / ev_power_w)
    local = energy_kwh - grid
    battery = min(local, energy_kwh * max(0.0, battery_discharge_w or 0.0) / ev_power_w)
    return grid, battery, local - battery


_METER_TOTALS = ("off_peak_kwh", "peak_kwh", "solar_kwh", "battery_kwh", "cost")


@dataclass(slots=True)
class SessionMeter:
    """The running session's energy, priced as it flows.

    Fed on every poll with the charger's own session counter (`e` on depow, DP 25
    on qccdz), so the energy is the charger's and only its origin and price are
    sampled: each increment is priced at the rate in force when it was drawn. A
    pause and restart inside the session changes nothing; a counter that drops
    means a new session. `clear` is called once the session has been logged.
    """

    counter_kwh: float | None = None
    off_peak_kwh: float = 0.0
    peak_kwh: float = 0.0
    solar_kwh: float = 0.0
    battery_kwh: float = 0.0
    cost: float = 0.0
    # Set when grid energy had to be priced from a guess: no price read yet, or
    # energy the meter did not see (see `add_unsampled`).
    estimated: bool = False
    last_grid_price: float | None = None

    @property
    def grid_kwh(self) -> float:
        return self.off_peak_kwh + self.peak_kwh

    @property
    def energy_kwh(self) -> float:
        return self.grid_kwh + self.solar_kwh + self.battery_kwh

    def clear(self) -> None:
        """Start from zero, keeping the counter as the baseline."""
        for name in _METER_TOTALS:
            setattr(self, name, 0.0)
        self.estimated = False

    def add(
        self,
        counter_kwh: float | None,
        *,
        ev_power_w: float,
        grid_power_w: float | None,
        battery_discharge_w: float | None,
        off_peak: bool | None,
        grid_price: float | None,
        local_price: float,
    ) -> bool:
        """Account for the counter's increase since the last poll.

        Returns True when the meter changed and is worth saving.
        """
        if counter_kwh is None:
            return False
        previous = self.counter_kwh
        self.counter_kwh = counter_kwh
        if previous is None:
            return True
        if counter_kwh < previous:
            self.clear()
            previous = 0.0
        delta = counter_kwh - previous
        if delta <= 0:
            return counter_kwh != previous
        grid, battery, solar = split_increment(
            delta,
            ev_power_w=ev_power_w,
            grid_power_w=grid_power_w,
            battery_discharge_w=battery_discharge_w,
        )
        self._add_grid(grid, off_peak=off_peak, grid_price=grid_price)
        self.battery_kwh += battery
        self.solar_kwh += solar
        self.cost += (battery + solar) * local_price
        return True

    def add_unsampled(
        self, energy_kwh: float, *, off_peak: bool | None, grid_price: float | None
    ) -> None:
        """Energy the charger reports but the meter never saw (Home Assistant was
        down when it was drawn): priced as grid at the current rate, and flagged."""
        self._add_grid(energy_kwh, off_peak=off_peak, grid_price=grid_price)
        self.estimated = True

    def _add_grid(
        self, energy_kwh: float, *, off_peak: bool | None, grid_price: float | None
    ) -> None:
        # No off-peak source configured means everything is peak, as in `split_session`.
        if off_peak:
            self.off_peak_kwh += energy_kwh
        else:
            self.peak_kwh += energy_kwh
        if grid_price is not None:
            self.last_grid_price = grid_price
        if energy_kwh <= 0:
            return
        if self.last_grid_price is None:
            self.estimated = True
            return
        self.cost += energy_kwh * self.last_grid_price

    def to_stored(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_stored(cls, raw: Any) -> SessionMeter:
        """Rebuild a saved meter; anything malformed gives an empty one."""
        if not isinstance(raw, dict):
            return cls()
        try:
            meter = cls(**{key: raw[key] for key in raw if key in cls.__dataclass_fields__})
        except TypeError:
            return cls()
        numbers = (
            meter.counter_kwh,
            meter.last_grid_price,
            *(getattr(meter, n) for n in _METER_TOTALS),
        )
        if not all(value is None or _is_number(value) for value in numbers):
            return cls()
        if any(getattr(meter, n) < 0 for n in _METER_TOTALS if n != "cost"):
            return cls()
        return meter


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)
