"""What the surplus controller does once it has decided: writes, lifecycle, estimates.

`test_surplus_state_machine.py` drives the *decision* and `test_surplus_inputs.py`
the *inputs*. This covers the part that acts on the charger -- start, stop, force,
and what each refusal turns into -- plus the evaluation loop that survives a failing
evaluation, the session limits, and the power estimates a deadline is planned on.

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import asyncio
import types

import pytest


class _Client:
    def __init__(self, *, current_ok=True, enable_ok=True):
        self.calls: list[tuple] = []
        self._current_ok = current_ok
        self._enable_ok = enable_ok

    async def async_set_charge_current(self, value):
        self.calls.append(("current", value))
        return self._current_ok

    async def async_set_charge_enabled(self, value):
        self.calls.append(("enabled", value))
        return self._enable_ok


class _Coordinator:
    def __init__(self):
        self.data = None
        self.refreshes = 0
        self.regulating = False
        self.vehicle_curves = None
        self.vehicle_tracker = None
        self.session_history = None

    async def async_request_refresh(self):
        self.refreshes += 1


def _controller(client=None, options=None):
    from tuya_ev_charger import solar_surplus

    hass = types.SimpleNamespace(
        states=types.SimpleNamespace(get=lambda _e: None),
        add_job=lambda target, *args: hass.jobs.append((target, args)),
        jobs=[],
    )
    entry = types.SimpleNamespace(options=options or {}, title="t", entry_id="e1")
    coordinator = _Coordinator()
    controller = solar_surplus.SolarSurplusController(
        hass=hass, entry=entry, client=client or _Client(), coordinator=coordinator
    )
    controller.hass = hass
    controller.coord = coordinator
    return controller


def _data(*, charging=False, current_target=10, power_kw=0.0, voltage=230.0):
    return types.SimpleNamespace(
        work_state_debug="WORKING" if charging else "IDLE",
        do_charge=charging,
        current_target=current_target,
        total_power=power_kw,
        voltage_l1=voltage,
        phases={},
    )


def _verdict(**kw):
    from tuya_ev_charger.charge_gates import DecisionReason, GateAction, Verdict

    fields = {"action": GateAction.IDLE, "reason": DecisionReason.START_DELAY_PENDING}
    fields.update(kw)
    return Verdict(**fields)


def _run(coro):
    return asyncio.run(coro)


# --- lifecycle and triggers -----------------------------------------------------------


def test_a_coordinator_update_and_a_sensor_update_each_schedule_an_evaluation():
    c = _controller()

    c._handle_coordinator_update()
    c._handle_sensor_update(object())

    assert [args for _, args in c.hass.jobs] == [("coordinator_update",), ("sensor_update",)]


def test_shutdown_cancels_an_evaluation_that_is_still_running():
    c = _controller()
    cancelled = []
    c._evaluation_task = types.SimpleNamespace(cancel=lambda: cancelled.append(1))

    _run(c.async_shutdown())

    assert cancelled == [1]
    assert c._evaluation_task is None


def test_a_request_while_an_evaluation_runs_is_remembered_not_started_twice():
    c = _controller()
    c._evaluation_task = types.SimpleNamespace(done=lambda: False)

    c._async_schedule_evaluation("again")

    assert c._rerun_requested is True


# --- the evaluation loop -----------------------------------------------------------------


def test_an_evaluation_that_raises_is_logged_and_stops_regulating(caplog):
    c = _controller()
    c._regulation_active = True
    notified = []
    c.async_add_update_listener(lambda: notified.append(1))

    async def _boom(reason):
        raise RuntimeError("bad sensor")

    c._async_evaluate_once = _boom

    with caplog.at_level("ERROR"):
        _run(c._async_evaluation_loop("test"))

    assert "evaluation failed" in caplog.text
    assert c._regulation_active is False
    assert c._last_decision_reason == "evaluation_exception"
    assert notified == [1]


def test_a_request_made_during_an_evaluation_replays_exactly_once():
    c = _controller()
    seen = []

    async def _evaluate(reason):
        seen.append(reason)
        if len(seen) == 1:
            c._rerun_requested = True  # a sensor changed mid-evaluation

    c._async_evaluate_once = _evaluate

    _run(c._async_evaluation_loop("first"))

    assert seen == ["first", "rerun"]
    assert c._rerun_requested is False


# --- config problems that need measurements ----------------------------------------------


def test_a_charger_that_keeps_reporting_several_phases_suggests_three_phase_setup():
    from tuya_ev_charger.config_diagnosis import ConfigProblem

    c = _controller(options={"installation_phases": 1})
    for _ in range(10):
        c._phase_count.observe(phase_count=3)

    assert ConfigProblem.INSTALLATION_PHASES_LIKELY_THREE.value in c.config_problems()


def test_no_three_phase_suggestion_when_the_installation_is_already_three_phase():
    from tuya_ev_charger.config_diagnosis import ConfigProblem

    c = _controller(options={"installation_phases": 3})
    for _ in range(10):
        c._phase_count.observe(phase_count=3)

    assert ConfigProblem.INSTALLATION_PHASES_LIKELY_THREE.value not in c.config_problems()


# --- starting a charge ---------------------------------------------------------------------


def test_a_refused_startup_current_is_reported_and_nothing_is_started():
    from tuya_ev_charger.charge_gates import DecisionReason

    client = _Client(current_ok=False)
    c = _controller(client)

    reason = _run(
        c._async_start_charge(_verdict(target_current=16), _data(current_target=10), 100.0)
    )

    assert reason == DecisionReason.FAILED_SET_STARTUP_CURRENT
    assert client.calls == [("current", 16)]
    assert c._session_active is False


def test_a_refused_start_is_reported_and_no_session_begins():
    from tuya_ev_charger.charge_gates import DecisionReason

    client = _Client(enable_ok=False)
    c = _controller(client)

    reason = _run(c._async_start_charge(_verdict(target_current=None), _data(), 100.0))

    assert reason == DecisionReason.FAILED_START_CHARGE
    assert c._session_active is False
    assert c.coord.refreshes == 0


def test_a_successful_start_opens_a_session_and_arms_the_ramp_timers():
    client = _Client()
    c = _controller(client)
    verdict = _verdict(target_current=16)

    reason = _run(c._async_start_charge(verdict, _data(current_target=10), 100.0))

    assert reason == verdict.reason
    assert client.calls == [("current", 16), ("enabled", True)]
    assert c._session_active is True
    assert c._timers.last_increase_action_ts == 100.0
    assert c._timers.last_decrease_action_ts == 100.0
    assert c.coord.refreshes == 1


def test_a_startup_current_already_in_place_is_not_rewritten():
    client = _Client()
    c = _controller(client)

    _run(c._async_start_charge(_verdict(target_current=10), _data(current_target=10), 100.0))

    assert client.calls == [("enabled", True)]


# --- stopping a charge ----------------------------------------------------------------------


def test_stopping_an_idle_charger_does_nothing():
    client = _Client()
    c = _controller(client)

    _run(c._async_stop_charge(_verdict(), _data(charging=False), 100.0))
    _run(c._async_stop_charge(_verdict(), None, 100.0))

    assert client.calls == []


def test_a_pause_does_not_interrupt_a_charge_started_from_the_app():
    client = _Client()
    c = _controller(client)

    _run(c._async_stop_charge(_verdict(only_stop_own_session=True), _data(charging=True), 100.0))

    assert client.calls == []


def test_a_stop_ends_our_own_session_and_refreshes():
    client = _Client()
    c = _controller(client)
    c._start_session(50.0)

    _run(c._async_stop_charge(_verdict(only_stop_own_session=True), _data(charging=True), 100.0))

    assert client.calls == [("enabled", False)]
    assert c._session_active is False
    assert c.coord.refreshes == 1


def test_a_stop_the_charger_refuses_leaves_the_session_open():
    client = _Client(enable_ok=False)
    c = _controller(client)
    c._start_session(50.0)

    _run(c._async_stop_charge(_verdict(), _data(charging=True), 100.0))

    assert c._session_active is True
    assert c.coord.refreshes == 0


# --- forcing a charge -----------------------------------------------------------------------


def test_a_forced_charge_that_cannot_set_its_current_says_so():
    from tuya_ev_charger.charge_gates import DecisionReason

    c = _controller(_Client(current_ok=False))

    reason = _run(
        c._async_force_charge_verdict(_verdict(target_current=32), _data(charging=True), 1.0)
    )

    assert reason == DecisionReason.FORCE_CHARGE_FAILED_SET_CURRENT


def test_a_forced_charge_that_cannot_start_says_so():
    from tuya_ev_charger.charge_gates import DecisionReason

    c = _controller(_Client(enable_ok=False))

    reason = _run(c._async_force_charge_verdict(_verdict(target_current=None), _data(), 1.0))

    assert reason == DecisionReason.FORCE_CHARGE_FAILED_START


def test_a_forced_charge_starts_an_idle_charger():
    from tuya_ev_charger.charge_gates import DecisionReason

    client = _Client()
    c = _controller(client)

    reason = _run(c._async_force_charge_verdict(_verdict(target_current=None), _data(), 1.0))

    assert reason == DecisionReason.FORCE_CHARGE_START
    assert client.calls == [("enabled", True)]
    assert c._session_active is True


def test_a_forced_charge_already_running_adjusts_then_holds():
    from tuya_ev_charger.charge_gates import DecisionReason

    client = _Client()
    c = _controller(client)

    adjusted = _run(
        c._async_force_charge_verdict(
            _verdict(target_current=32), _data(charging=True, current_target=10), 1.0
        )
    )
    held = _run(
        c._async_force_charge_verdict(
            _verdict(target_current=32), _data(charging=True, current_target=32), 2.0
        )
    )

    assert adjusted == DecisionReason.FORCE_CHARGE_ADJUST_CURRENT
    assert held == DecisionReason.FORCE_CHARGE_HOLDING
    assert c._session_active is True


def test_writing_a_current_stamps_the_matching_ramp_timer():
    c = _controller()

    _run(c._async_write_current(16, _data(current_target=10), 100.0))
    _run(c._async_write_current(8, _data(current_target=16), 200.0))

    assert c._timers.last_increase_action_ts == 100.0
    assert c._timers.last_decrease_action_ts == 200.0


def test_a_current_write_the_charger_refuses_changes_no_timer():
    c = _controller(_Client(current_ok=False))

    assert _run(c._async_write_current(16, _data(current_target=10), 100.0)) is False
    assert c.coord.refreshes == 0


def test_starting_a_session_twice_keeps_the_first_start():
    c = _controller()

    c._start_session(10.0)
    c._start_session(99.0)

    assert c._session_started_ts == 10.0


# --- session limits -------------------------------------------------------------------------


def test_no_session_limit_applies_outside_a_session():
    assert _controller()._session_limit_reason(1e9) is None


def test_the_session_duration_limit_ends_a_long_session(monkeypatch):
    from tuya_ev_charger import solar_surplus
    from tuya_ev_charger.charge_gates import DecisionReason

    monkeypatch.setattr(solar_surplus, "FIXED_MAX_SESSION_DURATION_MIN", 60)
    c = _controller()
    c._start_session(0.0)

    assert c._session_limit_reason(3599.0) is None
    assert c._session_limit_reason(3600.0) == DecisionReason.SESSION_LIMIT_DURATION


def test_the_session_energy_limit_ends_a_session_that_delivered_enough(monkeypatch):
    from tuya_ev_charger import solar_surplus
    from tuya_ev_charger.charge_gates import DecisionReason

    monkeypatch.setattr(solar_surplus, "FIXED_MAX_SESSION_ENERGY_KWH", 10.0)
    c = _controller()
    c._start_session(0.0)

    c._session_energy_kwh = 9.9
    assert c._session_limit_reason(1.0) is None
    c._session_energy_kwh = 10.0
    assert c._session_limit_reason(1.0) == DecisionReason.SESSION_LIMIT_ENERGY


def test_the_session_end_time_limit_applies_after_that_time_of_day(monkeypatch):
    import datetime

    from tuya_ev_charger import solar_surplus
    from tuya_ev_charger.charge_gates import DecisionReason

    monkeypatch.setattr(solar_surplus, "FIXED_MAX_SESSION_END_TIME", "22:30")
    c = _controller()
    c._start_session(0.0)

    monkeypatch.setattr(
        solar_surplus.dt_util, "now", lambda: datetime.datetime(2026, 1, 1, 22, 29), raising=False
    )
    assert c._session_limit_reason(1.0) is None
    monkeypatch.setattr(
        solar_surplus.dt_util, "now", lambda: datetime.datetime(2026, 1, 1, 22, 30), raising=False
    )
    assert c._session_limit_reason(1.0) == DecisionReason.SESSION_LIMIT_END_TIME


# --- charge-curve learning --------------------------------------------------------------------


def test_a_curve_sample_is_recorded_against_the_active_vehicle():
    c = _controller()
    recorded = []
    c.coord.vehicle_curves = types.SimpleNamespace(
        record=lambda vehicle, energy, power: recorded.append((vehicle, energy, power))
    )
    c.coord.vehicle_tracker = types.SimpleNamespace(active_vehicle="Zoe")
    c._session_energy_kwh = 3.0

    c._record_curve_sample(7.1)

    assert recorded == [("Zoe", 3.0, 7.1)]


def test_without_curve_storage_a_sample_is_ignored():
    _controller()._record_curve_sample(7.1)  # must not raise


# --- the power a deadline is planned on --------------------------------------------------------


def test_while_charging_the_measured_power_is_the_estimate():
    c = _controller()

    assert c._estimate_charge_power_kw(_data(charging=True, power_kw=3.7), (6, 16)) == 3.7


def test_with_no_current_to_offer_the_estimate_is_zero():
    assert _controller()._estimate_charge_power_kw(_data(), ()) == 0.0


def test_an_idle_charger_is_rated_by_its_highest_available_current():
    c = _controller(options={"installation_phases": 1})

    kw = c._estimate_charge_power_kw(_data(voltage=230.0), (6, 16))

    assert kw == pytest.approx(16 * 230.0 / 1000.0)


class _Curve:
    def __init__(self, minutes):
        self._minutes = minutes

    def minutes_for(self, delivered, needed):
        return self._minutes


def _curved(minutes):
    c = _controller(options={"installation_phases": 1})
    c.coord.vehicle_curves = types.SimpleNamespace(curve_for=lambda vehicle: _Curve(minutes))
    return c


def test_nothing_needed_plans_on_the_flat_rate():
    c = _curved(120)

    flat = c._estimate_charge_power_kw(_data(), (16,))

    assert c._planning_power_kw(_data(), (16,), 0.0) == flat


def test_without_a_learned_curve_the_flat_rate_stands():
    c = _controller(options={"installation_phases": 1})

    flat = c._estimate_charge_power_kw(_data(), (16,))

    assert c._planning_power_kw(_data(), (16,), 10.0) == flat


@pytest.mark.parametrize("minutes", [None, 0, -5])
def test_a_curve_that_does_not_cover_the_range_falls_back_to_the_flat_rate(minutes):
    c = _curved(minutes)

    flat = c._estimate_charge_power_kw(_data(), (16,))

    assert c._planning_power_kw(_data(), (16,), 10.0) == flat


def test_a_taper_only_ever_slows_the_plan_down():
    flat_kw = 16 * 230.0 / 1000.0  # 3.68 kW
    slow = _curved(minutes=300)  # 10 kWh in 5 h = 2 kW
    fast = _curved(minutes=60)  # 10 kWh in 1 h = 10 kW, faster than the hardware

    assert slow._planning_power_kw(_data(), (16,), 10.0) == pytest.approx(2.0)
    assert fast._planning_power_kw(_data(), (16,), 10.0) == pytest.approx(flat_kw)


def test_a_missing_grid_reading_means_no_load_cap():
    c = _controller(options={"max_house_power_w": 6000})

    assert c._caps.load_limit_current(_data(), (6, 16)) is None


# --- the reservations outlive an in-place options change ------------------------------------
#
# The tracker that remembers when each appliance announced itself lives inside the
# controller's `ProtectionCaps`, which is built once. An options change applied in
# place swaps the settings, not the caps, so an announcement already in flight must
# keep its clock. These tests pin that, so a future "reload settings" cannot rebuild
# the caps and silently restart every reservation window.


class _Clock:
    now = 1000.0


def _reserving(monkeypatch, table, states):
    from tuya_ev_charger import solar_surplus

    clock = _Clock()
    clock.now = 1000.0
    monkeypatch.setattr(solar_surplus, "monotonic", lambda: clock.now)
    c = _controller(options={"load_reservations": table})
    c.hass.states = types.SimpleNamespace(
        get=lambda entity_id: (
            types.SimpleNamespace(state=states[entity_id]) if entity_id in states else None
        )
    )
    c.clock = clock
    c.states = states
    return c


def test_an_announcement_in_flight_keeps_its_clock_across_apply_settings(monkeypatch):
    c = _reserving(monkeypatch, "switch.hob: 3000", {"switch.hob": "on"})
    caps = c._caps

    assert caps.reserved_power_w() == 3000.0
    announced_at = dict(caps._reservations.announced_at)

    c.clock.now = 1060.0
    _run(c.async_apply_settings())

    assert c._caps is caps
    assert caps.reserved_power_w() == 3000.0
    assert caps._reservations.announced_at == announced_at  # the window did not restart
    c.clock.now = 1121.0  # 121 s after the announcement: the window (120 s) has closed
    assert caps.reserved_power_w() == 0.0


def test_a_changed_reservation_table_is_read_immediately(monkeypatch):
    c = _reserving(
        monkeypatch,
        "switch.hob: 3000",
        {"switch.hob": "on", "switch.oven": "on"},
    )
    assert c._caps.reserved_power_w() == 3000.0

    c._entry.options = {"load_reservations": "switch.oven: 2500"}
    _run(c.async_apply_settings())

    assert c._caps.reserved_power_w() == 2500.0  # the hob is no longer listed


def test_a_table_emptied_in_place_reserves_nothing(monkeypatch):
    c = _reserving(monkeypatch, "switch.hob: 3000", {"switch.hob": "on"})
    assert c._caps.reserved_power_w() == 3000.0

    c._entry.options = {"load_reservations": ""}
    _run(c.async_apply_settings())

    assert c._caps.reserved_power_w() == 0.0


def test_the_caps_always_read_the_controllers_current_settings(monkeypatch):
    c = _reserving(monkeypatch, "switch.hob: 3000", {"switch.hob": "on"})
    before = c._settings

    c._entry.options = {"load_reservations": "switch.hob: 1000"}
    _run(c.async_apply_settings())

    assert c._settings is not before
    assert c._caps.reserved_power_w() == 1000.0


def test_an_appliance_removed_from_the_table_keeps_a_stale_clock_until_it_is_observed_again(
    monkeypatch,
):
    """Characterisation, not a promise: only listed entities are observed, so an
    entry dropped from the table is neither refreshed nor forgotten. It is harmless
    in practice (the next observation of the re-listed entity, while off, forgets
    it) and is pinned here so a change to it is a decision rather than an accident.
    """
    c = _reserving(monkeypatch, "switch.hob: 3000", {"switch.hob": "on"})
    c._caps.reserved_power_w()
    c._entry.options = {"load_reservations": "switch.oven: 2500"}
    _run(c.async_apply_settings())
    c.clock.now = 1500.0

    c._caps.reserved_power_w()

    assert "switch.hob" in c._caps._reservations.announced_at
