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
