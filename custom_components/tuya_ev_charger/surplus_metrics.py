"""Reading the charger's live metrics for the surplus logic.

Pure functions of one metrics snapshot, shared by the controller and the
protection caps. Split out of ``solar_surplus.py``; behaviour is unchanged.
"""

from __future__ import annotations

from .charger_metrics import EVMetrics

# Fallback line voltage when the charger's own reading is missing or implausible.
FIXED_LINE_VOLTAGE_V = 230


def is_charging(data: EVMetrics) -> bool:
    if data.do_charge is not None:
        return data.do_charge
    return data.work_state_debug == "WORKING"


def ev_power_w(data: EVMetrics) -> float:
    """The car's total draw in watts, across all wired phases.

    `total_power` sums the phases (in kW); on a three-phase charger, reading L1
    alone would under-report by up to 3x, which in turn over-states the headroom
    every current cap is computed against — the protection would then allow the
    very overload it exists to prevent.
    """
    return max(0.0, (data.total_power or 0.0) * 1000.0)


def line_voltage(data: EVMetrics | None) -> int:
    """The charger's own measured line voltage, when it looks sane, else the
    nominal fallback.

    Solar-heavy grids commonly run a few percent above nominal from PV export
    raising local voltage -- a fixed 230 V would then under-estimate real power
    draw for a given current, letting the protection caps overshoot their
    configured limit by roughly that same percentage. The reading already
    exists (DP 102, L1) at no extra cost to poll. Only L1 is read: L2/L3 can be
    ambiguous between "not wired" and "wired but idle" (#41), but that
    ambiguity does not apply to L1, which is always decoded whenever the
    charger answers at all.
    """
    if data is None or data.voltage_l1 < 100.0:
        return FIXED_LINE_VOLTAGE_V
    return round(data.voltage_l1)
