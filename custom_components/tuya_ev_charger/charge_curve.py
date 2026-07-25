"""What the car actually achieves, learned from the sessions already recorded.

The departure planner needs one number: how fast will this car charge? Until now
it assumed the charger's own rating -- 32 A at 230 V is 7.36 kW -- which says
nothing about what the *car* accepts. A vehicle limited to 3.7 kW on a 7.4 kW
charger therefore had its charging time halved, and the planner started it far too
late to meet the deadline. The pessimism already in that estimate guards against
assuming three phases where there is one; it does nothing about the car.

Session history has held duration, energy and vehicle since 2.10.0, so the answer
is already on disk: average power is energy over time, per session, per car.

Two rules keep this safe. It may only ever make the estimate **more**
conservative, never less -- a freak record cannot shorten the plan and cause a
missed departure. And it needs several sessions before it says anything, because
one interrupted charge is not a charging curve.

Pure: records in, kilowatts out.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Below this a session says nothing useful: a two-minute top-up is dominated by
# handshake and ramp-up rather than by the car's sustained rate.
MIN_SESSION_MINUTES = 20
MIN_SESSION_KWH = 1.0

# One session is an anecdote. Three is a pattern worth planning against.
MIN_SESSIONS = 3

# The car may draw a little more than it has been seen to; planning at exactly the
# observed rate leaves no room for a session that ran slightly cooler.
SAFETY_FACTOR = 0.95


def session_average_kw(duration_s: Any, energy_kwh: Any) -> float | None:
    """Average power of one session, or None when it cannot be trusted.

    Short or tiny sessions are rejected rather than averaged in: their duration is
    mostly handshake, so they would drag the learned rate down and make every
    future plan start needlessly early.
    """
    try:
        seconds = float(duration_s or 0)
        energy = float(energy_kwh or 0)
    except (TypeError, ValueError):
        return None

    if seconds < MIN_SESSION_MINUTES * 60 or energy < MIN_SESSION_KWH:
        return None
    hours = seconds / 3600.0
    if hours <= 0:
        return None
    return energy / hours


def learned_power_kw(
    sessions: Iterable[Mapping[str, Any]],
    *,
    vehicle: str | None = None,
) -> float | None:
    """The rate this car has demonstrated, or None when there is not enough data.

    Takes the **highest** trustworthy session average rather than the mean. The
    mean would be dragged down by every surplus-mode session that deliberately
    charged at 6 A, and answer "this car charges at 1.4 kW" -- true of those
    sessions, useless as a capability. The best observed rate is what the car has
    proven it can do.

    Filtered by vehicle when one is given, since two cars sharing a charger have
    no reason to charge alike.
    """
    averages: list[float] = []
    for record in sessions:
        if vehicle is not None and record.get("vehicle") != vehicle:
            continue
        average = session_average_kw(record.get("duration_s"), record.get("energy_kwh"))
        if average is not None:
            averages.append(average)

    if len(averages) < MIN_SESSIONS:
        return None
    return max(averages) * SAFETY_FACTOR


def planning_power_kw(
    *,
    theoretical_kw: float,
    learned_kw: float | None,
) -> float:
    """The rate to plan a departure against.

    The lower of the two, always. Learning may reveal that the car is slower than
    the charger -- which lengthens the plan and starts the charge earlier, the safe
    direction. It may never claim the car is *faster* than the hardware allows,
    because that would shorten the plan and risk the deadline it exists to protect.
    """
    if learned_kw is None or learned_kw <= 0:
        return theoretical_kw
    if theoretical_kw <= 0:
        return learned_kw
    return min(theoretical_kw, learned_kw)


# --- the taper curve -------------------------------------------------------
#
# The learned *rate* above answers "how fast, on average". It cannot answer "how
# much longer once the battery is nearly full", because a charge tapers: the last
# few kWh come far slower than the first. Modelling that needs the instantaneous
# power against how much has already been delivered, which the controller has on
# every poll -- so this records power in buckets of delivered energy and, over
# many sessions, draws the car's actual curve.
#
# The x-axis is energy delivered in the session, not state of charge, because SoC
# is not available on most setups. Sessions that start at different SoC therefore
# blur the curve, but the shape it reveals -- high and flat, then falling -- is
# real and is what makes a remaining-time estimate honest near the top.

# Delivered-energy bucket width. Fine enough to show a taper, coarse enough that a
# bucket fills within a handful of sessions.
BUCKET_KWH = 2.0
# Ignore readings below this: regulation and surplus deliberately charge slowly,
# and a 0-power sample between phases would drag a bucket down.
MIN_SAMPLE_KW = 0.3
# Recent sessions weigh more, so a battery that ages, or a swapped car, is
# followed rather than averaged with history forever.
SAMPLE_ALPHA = 0.2
# A bucket needs a few samples before it is drawn, for the same reason a single
# session is not a curve.
MIN_BUCKET_SAMPLES = 3


def _bucket_index(delivered_kwh: float) -> int:
    return int(max(0.0, delivered_kwh) // BUCKET_KWH)


class ChargeCurve:
    """Power the car draws as a function of energy already delivered.

    Buckets keyed by delivered-energy index; each holds an exponential moving
    average of the power seen there and a sample count. Pure and serialisable, so
    it can be persisted per vehicle and exercised without Home Assistant.
    """

    __slots__ = ("_count", "_ema")

    def __init__(
        self,
        ema: dict[int, float] | None = None,
        count: dict[int, int] | None = None,
    ) -> None:
        self._ema: dict[int, float] = dict(ema or {})
        self._count: dict[int, int] = dict(count or {})

    def record(self, delivered_kwh: float, power_kw: float) -> None:
        """Add one (delivered, power) reading to its bucket."""
        if power_kw < MIN_SAMPLE_KW:
            return
        index = _bucket_index(delivered_kwh)
        seen = self._ema.get(index)
        if seen is None:
            self._ema[index] = power_kw
        else:
            self._ema[index] = seen + (power_kw - seen) * SAMPLE_ALPHA
        self._count[index] = self._count.get(index, 0) + 1

    def power_at(self, delivered_kwh: float) -> float | None:
        """Modelled power at a delivered-energy point, or None if not yet learnt.

        Falls back to the nearest lower learnt bucket, so a gap between two filled
        buckets does not read as "unknown".
        """
        index = _bucket_index(delivered_kwh)
        for candidate in range(index, -1, -1):
            if self._count.get(candidate, 0) >= MIN_BUCKET_SAMPLES:
                return self._ema[candidate]
        return None

    def minutes_for(self, from_kwh: float, added_kwh: float) -> int | None:
        """Time to add ``added_kwh`` starting from ``from_kwh`` already delivered.

        Integrates the curve bucket by bucket, so a charge finishing in the taper
        is correctly estimated as taking longer than its flat-rate equivalent.
        Returns None when the curve does not cover the range asked for, so the
        caller falls back to the flat rate rather than trusting an extrapolation:
        the taper beyond the highest bucket we have seen is exactly what we do not
        know, and guessing it flat would defeat the purpose.
        """
        if added_kwh <= 0:
            return 0
        top = self._highest_learnt_kwh()
        if top is None or from_kwh + added_kwh > top:
            return None
        remaining = added_kwh
        position = max(0.0, from_kwh)
        minutes = 0.0
        # Bound the walk so a curve that never covers the range cannot loop.
        for _ in range(1000):
            power = self.power_at(position)
            if power is None or power <= 0:
                return None
            # How far to the next bucket boundary, capped by what is left to add.
            to_boundary = BUCKET_KWH - (position % BUCKET_KWH)
            step = min(remaining, to_boundary)
            minutes += (step / power) * 60.0
            remaining -= step
            position += step
            if remaining <= 1e-9:
                return int(round(minutes))
        return None

    def _highest_learnt_kwh(self) -> float | None:
        """Top of the highest sufficiently-sampled bucket, or None if empty.

        This is the edge of what has actually been observed; `minutes_for` will
        not integrate past it.
        """
        learnt = [i for i, n in self._count.items() if n >= MIN_BUCKET_SAMPLES]
        if not learnt:
            return None
        return (max(learnt) + 1) * BUCKET_KWH

    def points(self) -> list[dict[str, float]]:
        """The learnt curve, for display: delivered-energy against power."""
        return [
            {"delivered_kwh": round(index * BUCKET_KWH, 1), "power_kw": round(self._ema[index], 3)}
            for index in sorted(self._ema)
            if self._count.get(index, 0) >= MIN_BUCKET_SAMPLES
        ]

    @property
    def sample_count(self) -> int:
        return sum(self._count.values())

    def to_dict(self) -> dict[str, Any]:
        # String keys, because JSON stores have no integer keys.
        return {
            "ema": {str(k): round(v, 4) for k, v in self._ema.items()},
            "count": {str(k): v for k, v in self._count.items()},
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> ChargeCurve:
        if not raw:
            return cls()
        ema = {int(k): float(v) for k, v in (raw.get("ema") or {}).items()}
        count = {int(k): int(v) for k, v in (raw.get("count") or {}).items()}
        return cls(ema, count)
