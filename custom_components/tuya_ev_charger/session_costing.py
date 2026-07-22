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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

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
    worse than showing nothing.
    """
    if peak_price <= 0 and off_peak_price <= 0:
        return None
    if energy_kwh <= 0:
        return 0.0

    # With no windows configured everything is peak, and the split reflects that
    # already — no special case needed.
    off_peak_kwh = energy_kwh * split.off_peak_fraction
    peak_kwh = energy_kwh - off_peak_kwh
    return round(off_peak_kwh * off_peak_price + peak_kwh * peak_price, 4)
