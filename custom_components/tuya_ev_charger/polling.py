"""How often to poll, and when not to poll at all.

A Tuya charger accepts exactly one local connection. Polling it every 30 s
whether it is regulating a charge or asleep in an empty garage spends that slot
for nothing and fights the Smart Life app for it.

The interval is therefore matched to what is happening: fast while a charge is
being regulated, where the reaction time is the feature; slow when a cable is
merely plugged in; slower still when the charger is asleep and nothing can change
without a person being present.

Pure: a state in, seconds out.
"""

from __future__ import annotations

# Multipliers on the user's configured interval rather than absolute seconds, so
# somebody who deliberately set 5 s or 120 s keeps their intent and only gets the
# relative shape.
CHARGING_FACTOR = 0.34
PLUGGED_IN_FACTOR = 1.0
IDLE_FACTOR = 2.0
ASLEEP_FACTOR = 4.0

# Bounds, whatever the factors work out to. The floor protects the single
# connection; the ceiling keeps a charger from looking frozen for minutes.
MIN_INTERVAL_S = 5
MAX_INTERVAL_S = 300

# Statuses where regulation needs to see changes quickly.
_ACTIVE_STATUSES = frozenset({"charging", "waiting"})
# Statuses meaning a car is present but nothing is moving.
_PRESENT_STATUSES = frozenset({"plugged_in", "paused", "charged"})


def poll_interval_s(
    *,
    base_interval_s: int,
    status: str | None,
    regulating: bool,
) -> int:
    """Seconds to wait before the next poll.

    ``regulating`` wins over the status: while surplus or a protection limit is
    actively driving the charger, the loop needs fresh readings even if the
    charger has not yet reported that it is charging.
    """
    if regulating or (status in _ACTIVE_STATUSES):
        factor = CHARGING_FACTOR
    elif status in _PRESENT_STATUSES:
        factor = PLUGGED_IN_FACTOR
    elif status == "sleep":
        factor = ASLEEP_FACTOR
    else:
        # Includes `idle`, `fault` and an unknown status: nothing to regulate,
        # but not so dormant that a long wait is warranted.
        factor = IDLE_FACTOR

    return max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, round(base_interval_s * factor)))
