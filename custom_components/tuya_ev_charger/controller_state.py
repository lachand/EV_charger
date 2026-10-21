"""The part of the surplus controller's memory that must outlive a restart.

Pure: no Home Assistant import, so the conversion can be tested on its own.

The controller dates its deadlines with `time.monotonic()`, which is meaningless
after a restart. They are converted to wall-clock time only at the two borders
(`to_stored` / `from_stored`). Inside the controller everything stays monotonic,
so tests that patch `monotonic` are untouched and a wall-clock jump (NTP, DST)
while running cannot move a deadline. The alternative -- dating everything with
`dt_util.utcnow()` -- was rejected: a wide diff for a gain only needed here.

Only the session, the force-charge and the pause are kept. The delays and
cooldowns (`TimerState`) are deliberately not: they are seconds-to-minutes long
and re-arming them after a restart is harmless.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

STORAGE_VERSION = 1


@dataclass(frozen=True, slots=True)
class ControllerState:
    """Controller memory; deadlines are monotonic on the controller side."""

    force_charge_until_ts: float | None = None
    force_charge_current_a: int | None = None
    pause_until_ts: float | None = None
    session_active: bool = False
    session_energy_kwh: float = 0.0
    session_total_s: float = 0.0
    session_off_peak_s: float = 0.0


def _to_wall(until: float | None, now_mono: float, now_wall: float) -> float | None:
    return None if until is None else now_wall + (until - now_mono)


def _from_wall(raw: Any, now_mono: float, now_wall: float) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    remaining = raw - now_wall
    # Already elapsed while Home Assistant was down.
    return now_mono + remaining if remaining > 0 else None


def _non_negative(raw: Any) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return 0.0
    return max(0.0, float(raw))


def to_stored(state: ControllerState, now_mono: float, now_wall: float) -> dict[str, Any]:
    return {
        "force_charge_until": _to_wall(state.force_charge_until_ts, now_mono, now_wall),
        "force_charge_current_a": state.force_charge_current_a,
        "pause_until": _to_wall(state.pause_until_ts, now_mono, now_wall),
        "session_active": state.session_active,
        "session_energy_kwh": state.session_energy_kwh,
        "session_total_s": state.session_total_s,
        "session_off_peak_s": state.session_off_peak_s,
    }


def from_stored(raw: Any, now_mono: float, now_wall: float) -> ControllerState:
    """Rebuild the state; absent or malformed data falls back to defaults."""
    if not isinstance(raw, dict):
        return ControllerState()
    force_until = _from_wall(raw.get("force_charge_until"), now_mono, now_wall)
    current = raw.get("force_charge_current_a")
    return ControllerState(
        force_charge_until_ts=force_until,
        force_charge_current_a=(
            current
            if force_until is not None
            and isinstance(current, int)
            and not isinstance(current, bool)
            else None
        ),
        pause_until_ts=_from_wall(raw.get("pause_until"), now_mono, now_wall),
        session_active=raw.get("session_active") is True,
        session_energy_kwh=_non_negative(raw.get("session_energy_kwh")),
        session_total_s=_non_negative(raw.get("session_total_s")),
        session_off_peak_s=_non_negative(raw.get("session_off_peak_s")),
    )
