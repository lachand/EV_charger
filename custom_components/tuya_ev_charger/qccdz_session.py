"""A session record for `qccdz` firmwares, which have no DP 105 (#46).

The depow firmwares report a finished session in DP 105 (duration, energy) and the
coordinator logs it from there. A dé on `qccdz` only reports when the session
started (DP 54, a Unix timestamp as a string) and its energy so far (DP 25, which
it keeps after the car is unplugged). This fills the same `EVMetrics` fields from
those, so the session log, its cost and the anomaly checks work unchanged:

- `session_duration_s`: seconds spent charging, summed between polls (DP 102's
  role on depow);
- `last_session_duration_s` / `last_session_energy_kwh`: set once the car is
  unplugged, with the duration frozen at the first poll that sees it, so every
  later poll repeats the same record and the history logs it once.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .charger_metrics import EVMetrics
from .const import QCCDZ_DP_SESSION_START
from .option_values import coerce_optional_int

# A longer gap between two polls is a stall or a restart, not charging time.
# ponytail: time charged across a gap is lost; DP 41 charge_record may carry the
# real figure once its layout is known.
_MAX_POLL_GAP_S = 300.0


class QccdzSessionClock:
    def __init__(self) -> None:
        self._start: int | None = None
        self._charging_s = 0.0
        self._last_poll: float | None = None
        self._was_charging = False
        self._duration_s: int | None = None

    def apply(self, metrics: EVMetrics, dps: dict[str, Any], now: float) -> EVMetrics:
        start = coerce_optional_int(dps.get(QCCDZ_DP_SESSION_START))
        if start is None:
            return metrics
        if start != self._start:
            self._start = start
            self._charging_s = 0.0
            self._duration_s = None
        elif self._was_charging and self._last_poll is not None:
            gap = now - self._last_poll
            if 0 < gap <= _MAX_POLL_GAP_S:
                self._charging_s += gap
        self._last_poll = now
        self._was_charging = metrics.status == "charging"

        energy = metrics.session_energy_kwh
        ended = metrics.status == "idle" and energy is not None and energy > 0
        if ended and self._duration_s is None:
            self._duration_s = max(0, int(now) - start)
        return replace(
            metrics,
            session_duration_s=int(self._charging_s),
            last_session_duration_s=self._duration_s if ended else None,
            last_session_energy_kwh=energy if ended else None,
        )
