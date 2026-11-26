"""Believe the energy counter when the charger's state says nothing is charging.

After a reboot a depow_v2 charger was seen reporting DP 109 ``IDLEINS`` and
DP 140 false while the car was charging, with DP 102 (energy, voltage) still up
to date. The decoder then zeroes current and power (#35), so the switch, the
surplus regulation and the house-power limit all acted on a charge they could
not see: the limit counted the car as household load and ramped it down, and no
stop could be sent to a charge believed already stopped.

Session energy only goes up when current flows, so a counter that keeps rising
outranks the two state DPs. This module never reads the charger: it reinterprets
an already decoded reading. The evidence is dropped as soon as something may have
changed the charge -- a command of ours, or a new state from the charger -- and
a charge that goes on has to prove itself again.

Known costs:

- If the charge ends while the charger stays in the very same incoherent state,
  it keeps reading as charging for up to ``HOLD_S``, with current and power taken
  from a DP 102 that some firmwares leave frozen: the protection caps would then
  discount a car that no longer draws. The alternative (arm on one rise, hold
  briefly) would turn the last 0.1 kWh step of a normal stop into a phantom
  charge. Never letting a rebuilt reading widen a cap would close this, at the
  price of changing the caps and the surplus code before any raw dump has shown
  how DP 102 really behaves here.
- Below about 0.6 kW the rises are ``HOLD_S`` apart or more, so such a charge is
  never seen. A longer window would lengthen the phantom charge above.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from .charger_metrics import (
    STATUS_MAP,
    WORK_STATE_CHARGING,
    EVMetrics,
    _parse_json_object,
    _parse_phases,
)
from .dp_profile import DPProfile

LOGGER = logging.getLogger(__name__)

# One rise is what a normal stop looks like: the counter ticks over just as the
# state leaves WORKING.
RISES_TO_ARM = 2
# The counter moves in 0.1 kWh steps: about 4.4 min apart for a car taking the
# lowest setpoint, 6 A single-phase. A bit more than two of those.
HOLD_S = 600.0
# Unplugged or faulted: whatever the counter did before no longer says anything.
_NO_SESSION_STATUSES = frozenset({"idle", "sleep", "fault"})


class ChargingEvidence:
    def __init__(self) -> None:
        self._energy_kwh: float | None = None
        self._energy_at = 0.0
        self._rises = 0
        self._last_rise_at = 0.0
        # The raw DP 109 state at arming time; None while not armed.
        self._armed_state: str | None = None

    def reset(self) -> None:
        """Forget the evidence after a charge command the charger accepted.

        Held as charging, an obeyed stop would swallow the next start: the switch
        and the controller write nothing when the state already reads as wanted.
        The read-back cannot tell an obeyed stop from an ignored one (DP 140
        already reads false here), so only new rises can.
        """
        self._disarm("a charge command was sent")

    def apply(
        self, metrics: EVMetrics, dps: dict[str, Any], dp: DPProfile, now: float
    ) -> EVMetrics:
        """Return ``metrics``, rewritten as charging when the energy says so."""
        energy = metrics.session_energy_kwh
        # A reading from before a gap in polling is no baseline: whatever rose
        # since may have risen long ago.
        previous = self._energy_kwh if now - self._energy_at < HOLD_S else None
        if energy is not None:
            self._energy_kwh = energy
            self._energy_at = now

        if metrics.status == "charging" or metrics.do_charge is True:
            self._disarm("the charger reports the charge itself")
            return metrics

        if self._rises and now - self._last_rise_at >= HOLD_S:
            self._disarm("no energy rise lately")
        if self._armed_state not in (None, metrics.work_state_debug):
            self._disarm(f"the charger moved to {metrics.work_state_debug}")
            return metrics

        if metrics.status in _NO_SESSION_STATUSES:
            self._disarm(f"the charger reports {metrics.work_state_debug}")
        elif energy is not None and previous is not None:
            if energy < previous:
                self._disarm("the session counter was reset")
            elif energy > previous:
                self._rises += 1
                self._last_rise_at = now

        if self._rises < RISES_TO_ARM:
            return metrics

        if self._armed_state is None:
            self._armed_state = metrics.work_state_debug
            LOGGER.warning(
                "Session energy rising while the charger reports %s; treating as charging.",
                metrics.work_state_debug,
            )
        phases = _parse_phases(_parse_json_object(dps.get(dp.metrics, "{}")), charging=True)
        l1 = phases.get("L1")
        return replace(
            metrics,
            status=STATUS_MAP[WORK_STATE_CHARGING],
            do_charge=True,
            phases=phases,
            current_l1=l1.current if l1 else 0.0,
            power_l1=l1.power if l1 else 0.0,
            total_power=round(sum(phase.power for phase in phases.values()), 3),
        )

    def _disarm(self, why: str) -> None:
        if self._armed_state is not None:
            LOGGER.debug("No longer treating the session as charging: %s.", why)
        self._rises = 0
        self._armed_state = None
