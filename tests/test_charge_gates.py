"""The decision layer, tested without Home Assistant.

This is what the extraction bought: the ordering between gates and the timing
logic — start and stop delays, ramp cooldowns, battery hysteresis — are now plain
values that can be asserted directly. Before, the order lived inside a 300-line
method and could only be checked by grepping its source text.
"""

from __future__ import annotations

import pytest


def _ctx(**kwargs):
    from tuya_ev_charger.charge_gates import GateContext

    base = {
        "now": 1000.0,
        "is_charging": False,
        "session_active": False,
        "current_target": None,
        "available_currents": tuple(range(6, 33)),
        "surplus_mode_enabled": True,
        "grid_sensor_configured": True,
        "grid_power_w": -3000.0,
        "start_threshold_w": 1400.0,
        "stop_threshold_w": 1000.0,
        "start_delay_s": 30.0,
        "stop_delay_s": 60.0,
    }
    base.update(kwargs)
    return GateContext(**base)


def _timers(**kwargs):
    from tuya_ev_charger.charge_gates import TimerState

    return TimerState(**kwargs)


def _run(ctx, timers=None):
    from tuya_ev_charger.charge_gates import evaluate

    return evaluate(ctx, timers if timers is not None else _timers())


# --- the order is data -----------------------------------------------------


def test_the_gate_order_is_asserted_directly():
    """Replaces the old `inspect.getsource(...).index(...)` guard.

    Two placements carry released bug fixes. Force charge must come after the
    ladder has been narrowed (2.13.1), and the protection reduce-write must come
    after force charge so a forced charge does not write twice (2.13.3).
    """
    from tuya_ev_charger.charge_gates import GATES

    names = [gate.__name__ for gate in GATES]
    assert names.index("_gate_force_charge") < names.index("_gate_protection_reduce")
    # Nothing may decide before the "is there any current at all" check.
    assert names[0] == "_gate_no_currents"
    # The idle branch is the fallback, so it must be last.
    assert names[-1] == "_gate_start"
    # The external safety veto must win over even a force-charge request.
    assert names[1] == "_gate_external_charge_blocked"
    assert names.index("_gate_external_charge_blocked") < names.index("_gate_force_charge")
    # The battery-floor/off-peak fallback is the no-solar tier: it must not
    # need a grid sensor, but must still lose to an explicit pause.
    assert (
        names.index("_gate_pause")
        < names.index("_gate_battery_floor_tariff_fallback")
        < names.index("_gate_grid_sensor")
    )


def test_the_narrowed_ladder_is_the_only_one_a_gate_can_see():
    """The 2.13.1 fix, now a property of the type rather than of the ordering.

    The context carries no un-narrowed ladder, so no gate can widen it back.
    """
    from tuya_ev_charger.charge_gates import GateContext

    fields = set(GateContext.__dataclass_fields__)
    assert "available_currents" in fields
    assert not {"unrestricted_currents", "raw_currents", "all_currents"} & fields


def test_force_charge_cannot_exceed_a_cap():
    from tuya_ev_charger.charge_gates import GateAction

    # A 15 A cap has already narrowed the ladder; 32 A is requested.
    verdict = _run(
        _ctx(
            available_currents=tuple(range(6, 16)),
            protection_cap=15,
            cap_source="inverter_limit",
            force_charge_active=True,
            force_charge_current_a=32,
        )
    )
    assert verdict.action is GateAction.FORCE_CHARGE
    # Clamped to the cap, not dropped to the minimum (2.13.3).
    assert verdict.target_current == 15


def test_force_charge_below_the_ladder_falls_back_to_the_minimum():
    verdict = _run(_ctx(force_charge_active=True, force_charge_current_a=3))
    assert verdict.target_current == 6


# --- no usable current ----------------------------------------------------


def test_a_cap_with_no_headroom_stops_and_names_the_limit():
    from tuya_ev_charger.charge_gates import DecisionReason, GateAction

    verdict = _run(_ctx(available_currents=(), protection_cap=3, cap_source="inverter_limit"))
    assert verdict.action is GateAction.STOP_CHARGE
    assert verdict.reason is DecisionReason.INVERTER_LIMIT_NO_HEADROOM


def test_an_empty_ladder_without_a_cap_is_a_different_reason():
    from tuya_ev_charger.charge_gates import DecisionReason

    verdict = _run(_ctx(available_currents=()))
    assert verdict.reason is DecisionReason.NO_ALLOWED_CURRENTS


# --- start delay ----------------------------------------------------------


def test_the_start_delay_must_be_served_in_full():
    from tuya_ev_charger.charge_gates import DecisionReason, GateAction

    timers = _timers()
    ctx = _ctx(available_surplus_w=3000.0, max_supported_current=13)

    first = _run(ctx, timers)
    assert first.reason is DecisionReason.START_DELAY_PENDING
    assert timers.start_candidate_since == 1000.0

    inside = _run(_ctx(**{**_fields(ctx), "now": 1029.0}), timers)
    assert inside.reason is DecisionReason.START_DELAY_PENDING

    elapsed = _run(_ctx(**{**_fields(ctx), "now": 1031.0}), timers)
    assert elapsed.action is GateAction.START_CHARGE
    assert elapsed.reason is DecisionReason.SURPLUS_START
    assert elapsed.target_current == 6
    assert timers.start_candidate_since is None


def test_a_dip_resets_the_start_delay_rather_than_banking_it():
    from tuya_ev_charger.charge_gates import DecisionReason

    timers = _timers()
    ready = _ctx(available_surplus_w=3000.0, max_supported_current=13)
    _run(ready, timers)
    assert timers.start_candidate_since is not None

    # Surplus collapses: the timer is forgotten.
    _run(_ctx(now=1020.0, available_surplus_w=100.0, max_supported_current=0), timers)
    assert timers.start_candidate_since is None

    # Back in the sun, the whole delay starts again.
    again = _run(_ctx(**{**_fields(ready), "now": 1025.0}), timers)
    assert again.reason is DecisionReason.START_DELAY_PENDING
    assert timers.start_candidate_since == 1025.0


# --- stop delay -----------------------------------------------------------


def _charging(**kwargs):
    base = {
        "is_charging": True,
        "session_active": True,
        "current_target": 10,
        "available_surplus_w": 2300.0,
        "max_supported_current": 10,
        "target_current": 10,
    }
    base.update(kwargs)
    return _ctx(**base)


def test_the_stop_delay_must_be_served_in_full():
    from tuya_ev_charger.charge_gates import DecisionReason, GateAction

    timers = _timers()
    # Surplus below the stop threshold.
    dying = _charging(available_surplus_w=500.0, max_supported_current=2)

    first = _run(dying, timers)
    assert first.reason is DecisionReason.STOP_DELAY_PENDING
    assert first.action is GateAction.HOLD

    inside = _run(_ctx(**{**_fields(dying), "now": 1059.0}), timers)
    assert inside.reason is DecisionReason.STOP_DELAY_PENDING

    elapsed = _run(_ctx(**{**_fields(dying), "now": 1061.0}), timers)
    assert elapsed.action is GateAction.STOP_CHARGE
    assert elapsed.reason is DecisionReason.BELOW_STOP_THRESHOLD


def test_recovering_surplus_resets_the_stop_delay():
    timers = _timers()
    _run(_charging(available_surplus_w=500.0, max_supported_current=2), timers)
    assert timers.stop_candidate_since is not None

    _run(_charging(now=1020.0), timers)
    assert timers.stop_candidate_since is None


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"battery_ready": False}, "battery_soc_below_threshold"),
        ({"available_surplus_w": 500.0, "max_supported_current": 2}, "below_stop_threshold"),
        ({"max_supported_current": 3}, "insufficient_surplus_current"),
    ],
)
def test_stop_reasons_are_prioritised(kwargs, expected):
    """Battery first, then the threshold, then the current: the most specific
    explanation the user can act on comes first."""
    timers = _timers(stop_candidate_since=900.0)  # delay already served
    verdict = _run(_charging(**kwargs), timers)
    assert verdict.reason.value == expected


# --- ramp and cooldowns ---------------------------------------------------


def test_the_ramp_moves_one_step_at_a_time():
    from tuya_ev_charger.charge_gates import DecisionReason

    verdict = _run(_charging(current_target=10, target_current=20))
    assert verdict.reason is DecisionReason.ADJUST_CURRENT
    assert verdict.target_current == 11, "cars dislike jumps; one step only"


def test_the_up_cooldown_blocks_only_increases():
    from tuya_ev_charger.charge_gates import DecisionReason

    timers = _timers(last_increase_action_ts=1000.0, last_decrease_action_ts=0.0)
    ctx = _charging(now=1010.0, current_target=10, adjust_up_cooldown_s=60.0)

    # Increase: blocked.
    blocked = _run(_ctx(**{**_fields(ctx), "target_current": 20}), timers)
    assert blocked.reason is DecisionReason.ADJUST_COOLDOWN_ACTIVE

    # Decrease at the same instant: allowed, because the timers are independent.
    allowed = _run(_ctx(**{**_fields(ctx), "target_current": 8}), timers)
    assert allowed.reason is DecisionReason.ADJUST_CURRENT
    assert allowed.target_current == 9


def test_holding_when_the_target_is_already_the_setpoint():
    from tuya_ev_charger.charge_gates import DecisionReason, GateAction

    verdict = _run(_charging(current_target=10, target_current=10))
    assert verdict.action is GateAction.HOLD
    assert verdict.reason is DecisionReason.HOLDING_CURRENT


def test_a_setpoint_off_the_ladder_is_snapped_to_the_minimum():
    """The charger can report a value the ladder no longer contains, e.g. after
    a protection cap narrowed it."""
    verdict = _run(
        _charging(available_currents=tuple(range(6, 16)), current_target=32, target_current=15)
    )
    assert verdict.target_current == 7, "ramps up from the snapped minimum, not from 32"


# --- gating before regulation ---------------------------------------------


def test_surplus_mode_off_leaves_the_charger_alone():
    from tuya_ev_charger.charge_gates import DecisionReason, GateAction

    verdict = _run(_ctx(surplus_mode_enabled=False))
    assert verdict.action is GateAction.IDLE
    assert verdict.reason is DecisionReason.MODE_DISABLED


def test_a_pause_only_stops_our_own_session():
    from tuya_ev_charger.charge_gates import DecisionReason

    verdict = _run(_ctx(pause_active=True, is_charging=True))
    assert verdict.reason is DecisionReason.SURPLUS_PAUSED_ACTIVE
    assert verdict.only_stop_own_session is True, (
        "a charge started from the app is not ours to stop"
    )


def test_external_charge_block_overrides_even_a_force_charge_request():
    """The scenario this exists for -- protecting the house battery during a
    grid outage -- must win over every other reason the charger might
    otherwise be told to run."""
    from tuya_ev_charger.charge_gates import DecisionReason, GateAction

    verdict = _run(
        _ctx(
            external_charge_allowed=False,
            force_charge_active=True,
            force_charge_current_a=16,
            is_charging=True,
        )
    )
    assert verdict.action is GateAction.STOP_CHARGE
    assert verdict.reason is DecisionReason.EXTERNAL_CHARGE_BLOCKED


def test_external_charge_allowed_by_default_when_unconfigured():
    """Not configuring the sensor must never be why a charge fails to run."""
    from tuya_ev_charger.charge_gates import DecisionReason

    verdict = _run(_ctx(is_charging=True))
    assert verdict.reason is not DecisionReason.EXTERNAL_CHARGE_BLOCKED


def test_an_unavailable_grid_reading_forgets_both_delay_timers():
    from tuya_ev_charger.charge_gates import DecisionReason

    timers = _timers(start_candidate_since=900.0, stop_candidate_since=900.0)
    verdict = _run(_ctx(grid_power_w=None), timers)
    assert verdict.reason is DecisionReason.GRID_SENSOR_UNAVAILABLE
    assert timers.start_candidate_since is None
    assert timers.stop_candidate_since is None


def test_tariffs_never_defer_a_surplus_charge():
    """Free solar beats any off-peak rate, so the tariff gate stands down when
    surplus mode is on."""
    from tuya_ev_charger.charge_gates import DecisionReason

    verdict = _run(
        _ctx(
            surplus_mode_enabled=True,
            tariff_allowed=False,
            tariff_reason=DecisionReason.TARIFF_WAITING_FOR_OFF_PEAK,
            available_surplus_w=100.0,
        )
    )
    assert verdict.reason is not DecisionReason.TARIFF_WAITING_FOR_OFF_PEAK


def test_a_tariff_refusal_stops_the_charge_when_surplus_is_off():
    from tuya_ev_charger.charge_gates import DecisionReason, GateAction

    verdict = _run(
        _ctx(
            surplus_mode_enabled=False,
            tariff_allowed=False,
            tariff_reason=DecisionReason.TARIFF_WAITING_FOR_OFF_PEAK,
        )
    )
    assert verdict.action is GateAction.STOP_CHARGE
    assert verdict.reason is DecisionReason.TARIFF_WAITING_FOR_OFF_PEAK


def test_a_deadline_starts_the_charge_regardless_of_the_hour():
    from tuya_ev_charger.charge_gates import DecisionReason, GateAction

    verdict = _run(_ctx(surplus_mode_enabled=False, tariff_allowed=True, tariff_is_deadline=True))
    assert verdict.action is GateAction.START_CHARGE
    assert verdict.reason is DecisionReason.TARIFF_DEADLINE


# --- battery-floor / off-peak fallback --------------------------------------


def test_battery_floor_fallback_defers_when_no_window_is_configured():
    """Backward-compat pin: nobody without an off-peak window configured may
    see any behavioural change -- hitting the floor must still hard-stop."""
    from tuya_ev_charger.charge_gates import DecisionReason, GateAction

    verdict = _run(_ctx(battery_ready=False))
    assert verdict.action is GateAction.IDLE
    assert verdict.reason is DecisionReason.BATTERY_SOC_BELOW_THRESHOLD


def test_battery_floor_fallback_stops_hard_outside_the_window():
    from tuya_ev_charger.charge_gates import DecisionReason, GateAction

    verdict = _run(_ctx(battery_ready=False, tariff_allowed=False))
    assert verdict.action is GateAction.IDLE
    assert verdict.reason is DecisionReason.BATTERY_SOC_BELOW_THRESHOLD


def test_battery_floor_fallback_starts_charging_inside_the_window():
    from tuya_ev_charger.charge_gates import DecisionReason, GateAction

    verdict = _run(_ctx(battery_ready=False, tariff_allowed=True))
    assert verdict.action is GateAction.START_CHARGE
    assert verdict.reason is DecisionReason.BATTERY_FLOOR_OFF_PEAK_START
    assert verdict.target_current == 6, "starts at the snapped minimum, like solar surplus does"


def test_battery_floor_fallback_ramps_towards_the_ceiling_while_charging():
    from tuya_ev_charger.charge_gates import DecisionReason, GateAction

    verdict = _run(_charging(battery_ready=False, tariff_allowed=True, current_target=6))
    assert verdict.action is GateAction.SET_CURRENT
    assert verdict.reason is DecisionReason.BATTERY_FLOOR_OFF_PEAK_CHARGING
    assert verdict.target_current == 7


def test_battery_floor_fallback_holds_once_at_the_ceiling():
    from tuya_ev_charger.charge_gates import DecisionReason, GateAction

    verdict = _run(_charging(battery_ready=False, tariff_allowed=True, current_target=32))
    assert verdict.action is GateAction.HOLD
    assert verdict.reason is DecisionReason.BATTERY_FLOOR_OFF_PEAK_CHARGING


def test_battery_floor_fallback_does_not_need_a_grid_sensor():
    """It is explicitly the no-solar tier; requiring a grid sensor would trap
    the installs -- a nightly window, no solar sensor wired up -- it exists
    for."""
    from tuya_ev_charger.charge_gates import DecisionReason, GateAction

    verdict = _run(_ctx(battery_ready=False, tariff_allowed=True, grid_sensor_configured=False))
    assert verdict.action is GateAction.START_CHARGE
    assert verdict.reason is DecisionReason.BATTERY_FLOOR_OFF_PEAK_START


def test_a_pause_still_wins_over_the_battery_floor_fallback():
    from tuya_ev_charger.charge_gates import DecisionReason

    verdict = _run(
        _ctx(battery_ready=False, tariff_allowed=True, pause_active=True, is_charging=True)
    )
    assert verdict.reason is DecisionReason.SURPLUS_PAUSED_ACTIVE


# --- battery hysteresis ---------------------------------------------------


@pytest.mark.parametrize(
    ("soc", "enabled", "expected"),
    [
        # First sight: only the high threshold enables it.
        (50.0, None, False),
        (85.0, None, True),
        # Enabled: stays on between the thresholds, off below the low one.
        (70.0, True, True),
        (61.0, True, True),
        (60.0, True, False),
        # Disabled: needs the high threshold again, not merely the low one.
        (70.0, False, False),
        (80.0, False, True),
    ],
)
def test_battery_hysteresis(soc, enabled, expected):
    """Two thresholds so a battery hovering at one level cannot switch the car on
    and off repeatedly."""
    from tuya_ev_charger.charge_gates import battery_hysteresis

    assert battery_hysteresis(soc, high=80.0, low=60.0, enabled=enabled) is expected


# --- helper ---------------------------------------------------------------


def _fields(ctx):
    """The context's values as a dict, for building a variant of it."""
    from dataclasses import fields

    return {f.name: getattr(ctx, f.name) for f in fields(ctx)}
