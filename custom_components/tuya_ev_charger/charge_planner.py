"""When to charge: off-peak windows and departure deadlines.

Pure, like `surplus_decision`: times and numbers in, a decision out, no Home
Assistant. The controller supplies the clock and applies the result.

Off-peak windows are used rather than an hourly price feed because that is what
most tariffs actually look like — a couple of fixed ranges, known in advance and
printed on the bill — and it needs no external integration to work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum

# A charge is never planned closer than this to the deadline: the car's own
# ramp-up, a cable check or a brief pause would otherwise eat into the margin.
DEPARTURE_SAFETY_MARGIN_MIN = 20


class ChargeWindow(StrEnum):
    """Why the planner is, or is not, allowing a charge right now."""

    OFF_PEAK = "off_peak"
    # Peak hours, but the deadline no longer leaves room to wait.
    DEADLINE = "deadline"
    # Peak hours and no deadline pressure: wait.
    WAITING_FOR_OFF_PEAK = "waiting_for_off_peak"
    # No tariff configured at all, so nothing to arbitrate.
    UNRESTRICTED = "unrestricted"


def parse_windows(raw: str) -> tuple[tuple[time, time], ...]:
    """Parse ``22:00-06:00, 12:30-14:30`` into time ranges.

    Malformed ranges are skipped rather than raising: a typo in an option should
    narrow the schedule, never break the integration at startup.
    """
    windows: list[tuple[time, time]] = []
    for chunk in str(raw or "").split(","):
        piece = chunk.strip()
        if not piece or "-" not in piece:
            continue
        start_text, _, end_text = piece.partition("-")
        start = parse_clock(start_text)
        end = parse_clock(end_text)
        if start is None or end is None or start == end:
            continue
        windows.append((start, end))
    return tuple(windows)


def parse_clock(raw: str) -> time | None:
    """Parse ``07:30`` into a time, or None when absent or malformed."""
    text = str(raw or "").strip()
    if not text or ":" not in text:
        return None
    hours, _, minutes = text.partition(":")
    try:
        return time(int(hours), int(minutes))
    except ValueError:
        return None


def is_within_windows(moment: time, windows: tuple[tuple[time, time], ...]) -> bool:
    """Whether a time of day falls inside any window.

    Windows may wrap past midnight, which off-peak ones almost always do.
    """
    for start, end in windows:
        if start < end:
            if start <= moment < end:
                return True
        # Wrapping window, e.g. 22:00-06:00.
        elif moment >= start or moment < end:
            return True
    return False


def minutes_until(now: datetime, target: time) -> int:
    """Minutes from ``now`` to the next occurrence of ``target``."""
    candidate = now.replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return int((candidate - now).total_seconds() // 60)


def minutes_needed(energy_kwh: float, power_kw: float) -> int:
    """How long the remaining energy takes at the given power."""
    if energy_kwh <= 0 or power_kw <= 0:
        return 0
    return int((energy_kwh / power_kw) * 60)


@dataclass(slots=True, frozen=True)
class PlanRequest:
    now: datetime
    off_peak_windows: tuple[tuple[time, time], ...] = ()
    # Deadline and the energy still to deliver by then. Both are needed for the
    # deadline to mean anything.
    departure: time | None = None
    energy_needed_kwh: float = 0.0
    charge_power_kw: float = 0.0


@dataclass(slots=True, frozen=True)
class Plan:
    allowed: bool
    window: ChargeWindow
    # Minutes of charging still required; useful for diagnostics and for the
    # user to see why the planner is impatient.
    minutes_needed: int = 0
    minutes_to_departure: int = 0


def plan_charge(request: PlanRequest) -> Plan:
    """Decide whether charging is allowed right now.

    With no off-peak windows there is nothing to arbitrate and charging is
    always allowed: this feature must never be the reason a charge fails to
    start for someone who has not configured it.
    """
    if not request.off_peak_windows:
        return Plan(allowed=True, window=ChargeWindow.UNRESTRICTED)

    if is_within_windows(request.now.time(), request.off_peak_windows):
        return Plan(allowed=True, window=ChargeWindow.OFF_PEAK)

    # Peak hours. Only a deadline that can no longer be met by waiting justifies
    # charging now.
    if request.departure is None or request.energy_needed_kwh <= 0:
        return Plan(allowed=False, window=ChargeWindow.WAITING_FOR_OFF_PEAK)

    needed = minutes_needed(request.energy_needed_kwh, request.charge_power_kw)
    remaining = minutes_until(request.now, request.departure)
    if needed and remaining <= needed + DEPARTURE_SAFETY_MARGIN_MIN:
        return Plan(
            allowed=True,
            window=ChargeWindow.DEADLINE,
            minutes_needed=needed,
            minutes_to_departure=remaining,
        )

    return Plan(
        allowed=False,
        window=ChargeWindow.WAITING_FOR_OFF_PEAK,
        minutes_needed=needed,
        minutes_to_departure=remaining,
    )
