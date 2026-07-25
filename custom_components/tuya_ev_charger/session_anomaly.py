"""Spotting a charge that is going wrong before the user does.

The session history records every completed charge. A degrading contactor, a
cable making poor contact, or a connector heating and derating all show up there
first -- as sessions that deliver their energy slower than the same car used to,
or a run of charges that keep cutting short. Nobody watches for that today.

Pure, and deliberately conservative: like the config diagnosis, a false alarm
about a healthy charger costs more than a missed one, so each check demands
several sessions of agreement before it says anything.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .charge_curve import session_average_kw


class SessionAnomaly(StrEnum):
    """Each value is also the repair issue's translation key."""

    # Recent sessions are charging markedly slower than the car's established best.
    CHARGING_SLOWER_THAN_USUAL = "charging_slower_than_usual"
    # Several sessions in a row ended far short of a typical charge.
    REPEATED_SHORT_SESSIONS = "repeated_short_sessions"


# How many of the most recent sessions a check looks at.
RECENT_WINDOW = 5
# A session must be at least this far below the established best rate to count as
# slow. 0.7 = 30% slower; a normal surplus day easily varies more than 10-20%.
SLOW_RATE_FRACTION = 0.7
# This many recent sessions must agree before "slower than usual" is raised.
SLOW_SESSIONS_REQUIRED = 3
# A session delivering less than this share of the typical energy is "short".
SHORT_ENERGY_FRACTION = 0.4
SHORT_SESSIONS_REQUIRED = 3


@dataclass(slots=True, frozen=True)
class _Session:
    duration_s: int
    energy_kwh: float
    average_kw: float | None


def _prepare(sessions: Iterable[Mapping[str, Any]]) -> list[_Session]:
    prepared: list[_Session] = []
    for record in sessions:
        try:
            duration = int(record.get("duration_s") or 0)
            energy = float(record.get("energy_kwh") or 0.0)
        except (TypeError, ValueError):
            continue
        prepared.append(_Session(duration, energy, session_average_kw(duration, energy)))
    return prepared


def detect_anomalies(
    sessions: Iterable[Mapping[str, Any]],
    *,
    established_best_kw: float | None,
    typical_energy_kwh: float | None,
) -> list[SessionAnomaly]:
    """Anomalies visible in the recent history.

    ``sessions`` newest first, matching the session store. ``established_best_kw``
    and ``typical_energy_kwh`` describe what "normal" is for this car; when either
    is unknown the corresponding check stays silent rather than guess.
    """
    prepared = _prepare(sessions)
    anomalies: list[SessionAnomaly] = []

    if _charging_slower(prepared, established_best_kw):
        anomalies.append(SessionAnomaly.CHARGING_SLOWER_THAN_USUAL)
    if _repeatedly_short(prepared, typical_energy_kwh):
        anomalies.append(SessionAnomaly.REPEATED_SHORT_SESSIONS)

    return anomalies


def _charging_slower(sessions: list[_Session], established_best_kw: float | None) -> bool:
    if not established_best_kw or established_best_kw <= 0:
        return False
    threshold = established_best_kw * SLOW_RATE_FRACTION
    # Only sessions whose rate is meaningful; a slow *surplus* session is not a
    # fault, but a run of them below a fraction of the car's proven best, when the
    # car has proven it can go faster, is worth a look.
    rated = [s for s in sessions[:RECENT_WINDOW] if s.average_kw is not None]
    if len(rated) < SLOW_SESSIONS_REQUIRED:
        return False
    slow = [s for s in rated if s.average_kw < threshold]
    return len(slow) >= SLOW_SESSIONS_REQUIRED


def _repeatedly_short(sessions: list[_Session], typical_energy_kwh: float | None) -> bool:
    if not typical_energy_kwh or typical_energy_kwh <= 0:
        return False
    threshold = typical_energy_kwh * SHORT_ENERGY_FRACTION
    recent = sessions[:SHORT_SESSIONS_REQUIRED]
    if len(recent) < SHORT_SESSIONS_REQUIRED:
        return False
    return all(s.energy_kwh < threshold for s in recent)


def typical_energy_kwh(sessions: Iterable[Mapping[str, Any]]) -> float | None:
    """The car's usual session energy, as the median of trustworthy sessions.

    Median rather than mean so one enormous or one aborted charge does not move
    the baseline the short-session check compares against.
    """
    energies = sorted(s.energy_kwh for s in _prepare(sessions) if s.average_kw is not None)
    if len(energies) < RECENT_WINDOW:
        return None
    return energies[len(energies) // 2]
