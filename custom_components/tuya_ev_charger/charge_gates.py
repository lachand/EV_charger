"""What the charger should do next, decided without touching Home Assistant.

`surplus_decision.py` extracted the arithmetic in 2.5.0, but the *orchestration*
stayed in a single 300-line method that grew a new branch with every feature
(load balancing, tariffs, departure, the inverter cap). That method reached 27
exit points, and the ordering between them was invisible: the `force_charge_for`
bug fixed in 2.13.1 was exactly an ordering mistake, and it could only be guarded
by asserting on the *source text* of the method.

Here the order is a list. Each gate looks at an immutable snapshot and either
declines (`None`, meaning "nothing to say, ask the next one") or returns the
`Verdict` that ends the cycle. The controller reads sensors, builds the snapshot,
runs the list, and applies the single verdict it gets back.

Two invariants are structural rather than hoped for:

* `GateContext.available_currents` is **already** narrowed by the protection caps
  when the context is built. The un-narrowed ladder is not in the context at all,
  so no gate — force charge included — can widen it again.
* Exactly one verdict comes out per cycle, so a decision cannot be silently
  overwritten by a later branch, and the charger cannot be written to twice.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum


class GateAction(StrEnum):
    """What the controller must carry out for a verdict."""

    # Nothing to do; regulation is not driving the charger.
    IDLE = "idle"
    # Charging and deliberately leaving the setpoint alone.
    HOLD = "hold"
    START_CHARGE = "start_charge"
    STOP_CHARGE = "stop_charge"
    SET_CURRENT = "set_current"
    # Force charge: set the current if needed, then make sure it is charging.
    FORCE_CHARGE = "force_charge"


class DecisionReason(StrEnum):
    """Every reason the regulation can report.

    Enumerated in one place so they can be translated and so a typo becomes an
    import error instead of a string nobody recognises.
    """

    STARTUP = "startup"
    RERUN = "rerun"
    EVALUATION_EXCEPTION = "evaluation_exception"

    # Nothing to decide with.
    NO_COORDINATOR_DATA = "no_coordinator_data"
    NO_ALLOWED_CURRENTS = "no_allowed_currents"

    # Protection limits. The prefix matches the limit that bound, so the user can
    # tell the breaker cap from the inverter cap.
    LOAD_LIMIT_NO_HEADROOM = "load_limit_no_headroom"
    LOAD_LIMIT_REDUCED = "load_limit_reduced"
    INVERTER_LIMIT_NO_HEADROOM = "inverter_limit_no_headroom"
    INVERTER_LIMIT_REDUCED = "inverter_limit_reduced"

    # Force charge.
    FORCE_CHARGE_REQUESTED = "force_charge_requested"
    FORCE_CHARGE_START = "force_charge_start"
    FORCE_CHARGE_HOLDING = "force_charge_holding"
    FORCE_CHARGE_ADJUST_CURRENT = "force_charge_adjust_current"
    FORCE_CHARGE_FAILED_SET_CURRENT = "force_charge_failed_set_current"
    FORCE_CHARGE_FAILED_START = "force_charge_failed_start"

    # Tariff planning.
    TARIFF_OFF_PEAK = "tariff_off_peak"
    TARIFF_DEADLINE = "tariff_deadline"
    TARIFF_WAITING_FOR_OFF_PEAK = "tariff_waiting_for_off_peak"
    TARIFF_UNRESTRICTED = "tariff_unrestricted"

    MODE_DISABLED = "mode_disabled"
    SURPLUS_PAUSED = "surplus_paused"
    SURPLUS_PAUSED_ACTIVE = "surplus_paused_active"
    MISSING_GRID_SENSOR = "missing_grid_sensor"
    GRID_SENSOR_UNAVAILABLE = "grid_sensor_unavailable"

    # While charging.
    MIN_RUNTIME_GUARD = "min_runtime_guard"
    STOP_DELAY_PENDING = "stop_delay_pending"
    NO_TARGET_CURRENT = "no_target_current"
    ADJUST_CURRENT = "adjust_current"
    ADJUST_COOLDOWN_ACTIVE = "adjust_cooldown_active"
    TARGET_ALREADY_REACHED = "target_already_reached"
    HOLDING_CURRENT = "holding_current"

    # Reasons a running session ends.
    BELOW_STOP_THRESHOLD = "below_stop_threshold"
    INSUFFICIENT_SURPLUS_CURRENT = "insufficient_surplus_current"
    BATTERY_SOC_BELOW_THRESHOLD = "battery_soc_below_threshold"
    SESSION_LIMIT_DURATION = "session_limit_duration"
    SESSION_LIMIT_ENERGY = "session_limit_energy"
    SESSION_LIMIT_END_TIME = "session_limit_end_time"

    # While idle.
    BELOW_START_THRESHOLD = "below_start_threshold"
    START_DELAY_PENDING = "start_delay_pending"
    SURPLUS_START = "surplus_start"

    # Outcomes of a write, produced by the apply layer rather than by a gate:
    # they describe what the charger did, not what we decided.
    FAILED_SET_STARTUP_CURRENT = "failed_set_startup_current"
    FAILED_START_CHARGE = "failed_start_charge"


@dataclass(slots=True)
class TimerState:
    """The regulation's memory between cycles.

    Mutable on purpose: hysteresis *is* state. Keeping it here rather than on the
    controller is what makes the start/stop delays and the ramp cooldowns
    testable without Home Assistant.
    """

    start_candidate_since: float | None = None
    stop_candidate_since: float | None = None
    last_increase_action_ts: float = 0.0
    last_decrease_action_ts: float = 0.0

    def copy(self) -> TimerState:
        """A detached copy, for asking what *would* happen.

        The gates advance these timers as a side effect of being consulted, so a
        dry run has to be given a copy or merely asking the question would change
        the answer to the next real evaluation.
        """
        return replace(self)


@dataclass(slots=True, frozen=True)
class GateContext:
    """Everything the gates may look at, resolved once by the controller."""

    now: float
    is_charging: bool
    session_active: bool
    current_target: int | None

    # Already narrowed by the protection caps -- see the module docstring. Empty
    # means nothing may be offered at all.
    available_currents: tuple[int, ...]
    protection_cap: int | None = None
    # "load_limit" or "inverter_limit", naming the cap that bound.
    cap_source: str | None = None

    surplus_mode_enabled: bool = False
    grid_sensor_configured: bool = False
    grid_power_w: float | None = None

    force_charge_active: bool = False
    force_charge_current_a: int | None = None
    pause_active: bool = False

    # None when no off-peak window is configured, so tariffs are not consulted.
    tariff_allowed: bool | None = None
    tariff_reason: DecisionReason | None = None
    tariff_is_deadline: bool = False

    battery_ready: bool = True
    available_surplus_w: float = 0.0
    start_threshold_w: float = 0.0
    stop_threshold_w: float = 0.0
    max_supported_current: int = 0
    target_current: int | None = None

    adjust_up_cooldown_s: float = 0.0
    adjust_down_cooldown_s: float = 0.0
    start_delay_s: float = 0.0
    stop_delay_s: float = 0.0
    ramp_step: int = 1
    min_run_time_s: float = 0.0
    # Already-computed session limit, if any (duration, energy, end time).
    session_limit_reason: DecisionReason | None = None

    @property
    def min_current(self) -> int:
        return min(self.available_currents) if self.available_currents else 0


@dataclass(slots=True, frozen=True)
class Verdict:
    action: GateAction
    reason: DecisionReason
    target_current: int | None = None
    regulation_active: bool = False
    # Surplus debug figures are meaningless when regulation did not run.
    clear_debug: bool = False
    # Stop the charger only if it is the one that started it.
    only_stop_own_session: bool = False
    # Gates consulted before this verdict, in order, followed by the one that
    # produced it. Populated by `evaluate`; a gate never sets it itself.
    consulted: tuple[str, ...] = ()
    decided_by: str = ""


Gate = Callable[[GateContext, TimerState], Verdict | None]


# --- individual gates ------------------------------------------------------
#
# Order is the list at the bottom of this module. Each returns None to defer.


def _gate_no_currents(ctx: GateContext, timers: TimerState) -> Verdict | None:
    if ctx.available_currents:
        return None
    # An empty ladder with a cap set means the cap left no usable current at all;
    # without one it means the charger advertised nothing we can use.
    if ctx.protection_cap is not None and ctx.cap_source:
        return Verdict(
            action=GateAction.STOP_CHARGE,
            reason=DecisionReason(f"{ctx.cap_source}_no_headroom"),
            clear_debug=True,
        )
    return Verdict(
        action=GateAction.IDLE,
        reason=DecisionReason.NO_ALLOWED_CURRENTS,
        clear_debug=True,
    )


def _gate_force_charge(ctx: GateContext, timers: TimerState) -> Verdict | None:
    """Force charge overrides scheduling, never the installation's limits.

    It runs on the already-narrowed ladder, and clamps a request that is not on
    it down to the highest current still offered -- answering "as fast as you
    can" with the minimum would invert the intent (fixed in 2.13.3).
    """
    if not ctx.force_charge_active:
        return None

    requested = ctx.force_charge_current_a or 0
    affordable = [value for value in ctx.available_currents if value <= requested]
    target = max(affordable) if affordable else ctx.min_current
    return Verdict(
        action=GateAction.FORCE_CHARGE,
        reason=DecisionReason.FORCE_CHARGE_HOLDING,
        target_current=target,
        regulation_active=True,
        clear_debug=True,
    )


def _gate_protection_reduce(ctx: GateContext, timers: TimerState) -> Verdict | None:
    """Bring the charger down to the cap in one write.

    Straight to the value: no ramp step, no cooldown. An overload is not a
    passing cloud, and stepping down 1 A at a time would trip the breaker long
    before arriving. The cycle ends here so surplus regulation cannot write a
    second time from a setpoint the charger has not yet reported (2.13.3).
    """
    if ctx.protection_cap is None or not ctx.cap_source:
        return None
    if ctx.current_target is None or ctx.current_target <= ctx.protection_cap:
        return None
    return Verdict(
        action=GateAction.SET_CURRENT,
        reason=DecisionReason(f"{ctx.cap_source}_reduced"),
        target_current=ctx.protection_cap,
        regulation_active=True,
    )


def _gate_tariff(ctx: GateContext, timers: TimerState) -> Verdict | None:
    """Off-peak windows and the departure deadline.

    Only consulted when surplus mode is off: free solar beats any off-peak rate,
    so a surplus charge must never be deferred for a tariff.
    """
    if ctx.surplus_mode_enabled or ctx.tariff_allowed is None:
        return None
    if not ctx.tariff_allowed:
        return Verdict(
            action=GateAction.STOP_CHARGE,
            reason=ctx.tariff_reason or DecisionReason.TARIFF_WAITING_FOR_OFF_PEAK,
            clear_debug=True,
        )
    if ctx.tariff_is_deadline and not ctx.is_charging:
        # Waiting any longer would miss the departure.
        return Verdict(
            action=GateAction.START_CHARGE,
            reason=DecisionReason.TARIFF_DEADLINE,
        )
    return None


def _gate_mode_disabled(ctx: GateContext, timers: TimerState) -> Verdict | None:
    if ctx.surplus_mode_enabled:
        return None
    return Verdict(
        action=GateAction.IDLE,
        reason=DecisionReason.MODE_DISABLED,
        clear_debug=True,
    )


def _gate_pause(ctx: GateContext, timers: TimerState) -> Verdict | None:
    if not ctx.pause_active:
        return None
    return Verdict(
        action=GateAction.STOP_CHARGE,
        reason=DecisionReason.SURPLUS_PAUSED_ACTIVE,
        clear_debug=True,
        # A charge someone else started is not ours to stop.
        only_stop_own_session=True,
    )


def _gate_grid_sensor(ctx: GateContext, timers: TimerState) -> Verdict | None:
    if not ctx.grid_sensor_configured:
        return Verdict(
            action=GateAction.IDLE,
            reason=DecisionReason.MISSING_GRID_SENSOR,
            clear_debug=True,
        )
    if ctx.grid_power_w is None:
        # Forget both delay timers: a reading gap must not count as progress
        # towards starting or stopping.
        timers.start_candidate_since = None
        timers.stop_candidate_since = None
        return Verdict(
            action=GateAction.IDLE,
            reason=DecisionReason.GRID_SENSOR_UNAVAILABLE,
            clear_debug=True,
        )
    return None


def _stop_reason(ctx: GateContext) -> DecisionReason | None:
    """Why a running session should end, in priority order."""
    if ctx.session_active and ctx.session_limit_reason is not None:
        return ctx.session_limit_reason
    if not ctx.battery_ready:
        return DecisionReason.BATTERY_SOC_BELOW_THRESHOLD
    if ctx.available_surplus_w <= ctx.stop_threshold_w:
        return DecisionReason.BELOW_STOP_THRESHOLD
    if ctx.max_supported_current < ctx.min_current:
        return DecisionReason.INSUFFICIENT_SURPLUS_CURRENT
    return None


def _gate_charging(ctx: GateContext, timers: TimerState) -> Verdict | None:
    """The whole branch that applies while a charge is running."""
    if not ctx.is_charging:
        return None

    timers.start_candidate_since = None
    stop_reason = _stop_reason(ctx)

    if stop_reason is not None:
        if _min_runtime_guard_applies(ctx, stop_reason):
            timers.stop_candidate_since = None
            return Verdict(
                action=GateAction.HOLD,
                reason=DecisionReason.MIN_RUNTIME_GUARD,
                regulation_active=True,
            )

        # The stop delay rides out a transient dip rather than cutting the
        # charge on one bad reading.
        if timers.stop_candidate_since is None:
            timers.stop_candidate_since = ctx.now
            return Verdict(
                action=GateAction.HOLD,
                reason=DecisionReason.STOP_DELAY_PENDING,
                regulation_active=True,
            )
        if ctx.now - timers.stop_candidate_since < ctx.stop_delay_s:
            return Verdict(
                action=GateAction.HOLD,
                reason=DecisionReason.STOP_DELAY_PENDING,
                regulation_active=True,
            )

        timers.stop_candidate_since = None
        return Verdict(action=GateAction.STOP_CHARGE, reason=stop_reason)

    timers.stop_candidate_since = None

    if ctx.target_current is None:
        return Verdict(
            action=GateAction.HOLD,
            reason=DecisionReason.NO_TARGET_CURRENT,
            regulation_active=True,
        )

    # The charger may report a setpoint the current ladder no longer contains,
    # for instance after its maximum changed.
    current = ctx.current_target
    if current is None or current not in ctx.available_currents:
        current = ctx.min_current

    if ctx.target_current == current:
        return Verdict(
            action=GateAction.HOLD,
            reason=DecisionReason.HOLDING_CURRENT,
            regulation_active=True,
        )

    increasing = ctx.target_current > current
    last_action = timers.last_increase_action_ts if increasing else timers.last_decrease_action_ts
    cooldown = ctx.adjust_up_cooldown_s if increasing else ctx.adjust_down_cooldown_s
    if ctx.now - last_action < cooldown:
        return Verdict(
            action=GateAction.HOLD,
            reason=DecisionReason.ADJUST_COOLDOWN_ACTIVE,
            regulation_active=True,
        )

    # Cars dislike large current jumps, so the setpoint is walked one step.
    next_current = _ramp(current, ctx.target_current, ctx.available_currents, ctx.ramp_step)
    if next_current == current:
        return Verdict(
            action=GateAction.HOLD,
            reason=DecisionReason.TARGET_ALREADY_REACHED,
            regulation_active=True,
        )
    return Verdict(
        action=GateAction.SET_CURRENT,
        reason=DecisionReason.ADJUST_CURRENT,
        target_current=next_current,
        regulation_active=True,
    )


def _gate_start(ctx: GateContext, timers: TimerState) -> Verdict | None:
    """The whole branch that applies while idle."""
    timers.stop_candidate_since = None

    if not ctx.battery_ready:
        timers.start_candidate_since = None
        return Verdict(
            action=GateAction.IDLE,
            reason=DecisionReason.BATTERY_SOC_BELOW_THRESHOLD,
        )

    if ctx.available_surplus_w < ctx.start_threshold_w:
        timers.start_candidate_since = None
        return Verdict(
            action=GateAction.IDLE,
            reason=DecisionReason.BELOW_START_THRESHOLD,
        )

    if ctx.max_supported_current < ctx.min_current:
        timers.start_candidate_since = None
        return Verdict(
            action=GateAction.IDLE,
            reason=DecisionReason.INSUFFICIENT_SURPLUS_CURRENT,
        )

    # The start delay must be served in full and from scratch: a dip resets it
    # above rather than banking partial progress.
    if timers.start_candidate_since is None:
        timers.start_candidate_since = ctx.now
        return Verdict(action=GateAction.IDLE, reason=DecisionReason.START_DELAY_PENDING)
    if ctx.now - timers.start_candidate_since < ctx.start_delay_s:
        return Verdict(action=GateAction.IDLE, reason=DecisionReason.START_DELAY_PENDING)

    timers.start_candidate_since = None
    return Verdict(
        action=GateAction.START_CHARGE,
        reason=DecisionReason.SURPLUS_START,
        target_current=ctx.min_current,
        regulation_active=True,
    )


# --- helpers ---------------------------------------------------------------


def _min_runtime_guard_applies(ctx: GateContext, stop_reason: DecisionReason) -> bool:
    """Whether a very short session may resist a normal surplus stop.

    Hard limits are never guarded; only the ordinary surplus oscillation is.
    Inert while the minimum run time is zero, which it is today.
    """
    if ctx.min_run_time_s <= 0:
        return False
    return stop_reason in {
        DecisionReason.BELOW_STOP_THRESHOLD,
        DecisionReason.INSUFFICIENT_SURPLUS_CURRENT,
        DecisionReason.BATTERY_SOC_BELOW_THRESHOLD,
    }


def _ramp(current: int, target: int, ladder: tuple[int, ...], step: int) -> int:
    from .surplus_decision import ramp_towards

    return ramp_towards(current, target, ladder, step=max(1, step))


def battery_hysteresis(soc: float, *, high: float, low: float, enabled: bool | None) -> bool:
    """Whether the house battery may feed the car.

    Two thresholds so a battery hovering at one level does not switch the car on
    and off: it takes `high` to start contributing and `low` to stop.
    """
    if enabled is None:
        return soc >= high
    if enabled and soc <= low:
        return False
    if not enabled and soc >= high:
        return True
    return enabled


# --- the order -------------------------------------------------------------
#
# This tuple *is* the priority. It is data, so a test can assert it directly --
# which is what replaced asserting on the source text of a 300-line method.
#
# Two placements carry the fixes from 2.13.1 and 2.13.3 and must not be swapped:
#   * force charge sits after the ladder was narrowed (done at context build), so
#     it can never exceed a protection cap;
#   * the protection reduce-write sits after force charge, so a forced charge
#     does not produce two writes -- and two beeps -- in one cycle.
GATES: tuple[Gate, ...] = (
    _gate_no_currents,
    _gate_force_charge,
    _gate_protection_reduce,
    _gate_tariff,
    _gate_mode_disabled,
    _gate_pause,
    _gate_grid_sensor,
    _gate_charging,
    _gate_start,
)


def evaluate(context: GateContext, timers: TimerState) -> Verdict:
    """Run the gates in order and return the one verdict for this cycle.

    The verdict records which gates were consulted and which one decided. That
    turns "why is it not charging?" into something answerable from the entity's
    attributes instead of from a reading of this file.
    """
    consulted: list[str] = []
    for gate in GATES:
        name = _gate_label(gate)
        verdict = gate(context, timers)
        if verdict is not None:
            return replace(verdict, consulted=tuple(consulted), decided_by=name)
        consulted.append(name)
    # _gate_start always decides, so this is unreachable; kept explicit rather
    # than relying on that.
    return Verdict(
        action=GateAction.IDLE,
        reason=DecisionReason.NO_TARGET_CURRENT,
        consulted=tuple(consulted),
    )


def _gate_label(gate: Gate) -> str:
    """`_gate_force_charge` -> `force_charge`, for readable attributes."""
    return gate.__name__.removeprefix("_gate_")
