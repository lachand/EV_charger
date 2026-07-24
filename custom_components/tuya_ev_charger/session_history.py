"""A rolling log of completed charging sessions.

The charger keeps exactly one: DP 105 is overwritten by the next session. So
"how much did I charge last month" is unanswerable from the device, and from
Home Assistant's own history only indirectly.

This records each completed session as it is announced, with its estimated cost
and the vehicle it was attributed to, and keeps the last few dozen.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
# Enough for a couple of months of daily charging. The whole list is rewritten
# on every session, so it is deliberately not an unbounded archive; long-term
# analysis belongs in the recorder, not here.
MAX_SESSIONS = 60


@dataclass(slots=True, frozen=True)
class SessionRecord:
    # ISO 8601, local time.
    ended_at: str
    duration_s: int
    energy_kwh: float
    off_peak_minutes: int
    peak_minutes: int
    cost: float | None = None
    vehicle: str | None = None


class SessionHistory:
    """Store-backed list of completed sessions, newest first."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"tuya_ev_charger_sessions_{entry_id}"
        )
        self._sessions: list[dict[str, Any]] = []
        # The signature of the last session seen, so the same DP 105 record is
        # not logged again on every poll. It only changes when a session ends.
        self._last_signature: tuple[int, float] | None = None

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._sessions = list(data.get("sessions", []))
        signature = data.get("last_signature")
        if isinstance(signature, list) and len(signature) == 2:
            self._last_signature = (int(signature[0]), float(signature[1]))

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "sessions": self._sessions,
                "last_signature": list(self._last_signature) if self._last_signature else None,
            }
        )

    @property
    def sessions(self) -> list[dict[str, Any]]:
        return list(self._sessions)

    @property
    def latest(self) -> dict[str, Any] | None:
        return self._sessions[0] if self._sessions else None

    def is_new_session(self, duration_s: int | None, energy_kwh: float | None) -> bool:
        """Whether DP 105 now describes a session that has not been logged.

        The charger has no session id, so identity is (duration, energy). Two
        genuinely identical back-to-back sessions would collapse into one; that
        is preferable to logging the same session on every poll, which is what
        happens without this check.
        """
        if duration_s is None or energy_kwh is None:
            return False
        if duration_s <= 0 and energy_kwh <= 0:
            return False
        return (int(duration_s), float(energy_kwh)) != self._last_signature

    async def async_record(self, record: SessionRecord) -> None:
        self._last_signature = (record.duration_s, record.energy_kwh)
        self._sessions.insert(0, asdict(record))
        del self._sessions[MAX_SESSIONS:]
        await self._async_save()

    async def async_note_seen(self, duration_s: int, energy_kwh: float) -> None:
        """Accept the current DP 105 record without logging it.

        Used on the first poll after a restart: the charger's last session may
        long predate this run, and logging it would invent a session that just
        happened.
        """
        self._last_signature = (int(duration_s), float(energy_kwh))
        await self._async_save()

    def total_cost(self) -> float | None:
        """Cost of every priced session on record."""
        priced = [session["cost"] for session in self._sessions if session.get("cost") is not None]
        if not priced:
            return None
        return round(sum(priced), 2)

    def total_energy_kwh(self) -> float:
        return round(sum(float(session.get("energy_kwh") or 0.0) for session in self._sessions), 3)
