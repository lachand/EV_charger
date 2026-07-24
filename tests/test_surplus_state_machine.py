"""Characterisation of the surplus state machine.

This is the safety net for extracting the decision layer. `solar_surplus.py` is
the most critical module and its state machine had no tests at all: the only file
importing it exercised the protection-cap helpers. The delays, ramp cooldowns and
battery hysteresis below are the subtlest logic in the integration and were
entirely uncovered.

These tests pin down *current* behaviour. They must pass unchanged before and
after the refactor — if one has to be edited to go green, that is a behaviour
change and must be treated as such rather than absorbed into the refactor.
"""

from __future__ import annotations

import asyncio
import types


class _State:
    def __init__(self, value, unit="W"):
        self.state = str(value)
        self.attributes = {"unit_of_measurement": unit}


class _Hass:
    def __init__(self, sensors):
        self.scheduled: list = []
        self.states = types.SimpleNamespace(get=lambda entity_id: self._sensors.get(entity_id))
        self._sensors = {k: _State(v) for k, v in sensors.items()}

    def set(self, entity_id, value):
        self._sensors[entity_id] = _State(value)

    def add_job(self, target, *args):
        # The services schedule an evaluation through the event loop; tests drive
        # it explicitly with tick() instead, so this only needs to not blow up.
        self.scheduled.append(args[0] if args else None)


class _Client:
    """The whole client surface the evaluation path touches: two writes."""

    def __init__(self, *, set_current_ok=True, set_enabled_ok=True):
        self.writes: list[tuple[str, object]] = []
        self._set_current_ok = set_current_ok
        self._set_enabled_ok = set_enabled_ok

    async def async_set_charge_current(self, value):
        self.writes.append(("current", value))
        return self._set_current_ok

    async def async_set_charge_enabled(self, value):
        self.writes.append(("enabled", value))
        return self._set_enabled_ok


class _Coordinator:
    def __init__(self, data):
        self.data = data
        self.refreshes = 0

    async def async_request_refresh(self):
        self.refreshes += 1


def _metrics(*, charging=False, current_target=None, total_power_kw=0.0, max_current=32):
    """Only the EVMetrics fields the evaluation path reads."""
    return types.SimpleNamespace(
        do_charge=charging,
        work_state_debug="WORKING" if charging else "IDLE",
        current_target=current_target,
        total_power=total_power_kw,
        power_l1=total_power_kw,
        max_current_cfg=max_current,
        adjust_current_options=[],
        session_energy_kwh=0.0,
    )


BASE_OPTIONS = {
    "surplus_mode_enabled": True,
    "surplus_sensor_entity_id": "sensor.grid",
    "surplus_start_threshold_w": 1400,
    "surplus_stop_threshold_w": 1000,
    "continuous_current": True,
}


class Harness:
    """A controller wired to fakes, with an injected clock."""

    def __init__(self, monkeypatch, *, options=None, sensors=None, metrics=None, client=None):
        from tuya_ev_charger import solar_surplus

        self.now = 1000.0
        monkeypatch.setattr(solar_surplus, "monotonic", lambda: self.now)

        merged = {**BASE_OPTIONS, **(options or {})}
        self.hass = _Hass(sensors or {"sensor.grid": -3000})
        self.client = client or _Client()
        self.coordinator = _Coordinator(metrics if metrics is not None else _metrics())
        entry = types.SimpleNamespace(options=merged, title="test", entry_id="test")

        # Real __init__: it does no I/O, so every field is initialised properly
        # rather than guessed at.
        self.controller = solar_surplus.SolarSurplusController(
            hass=self.hass,
            entry=entry,
            client=self.client,
            coordinator=self.coordinator,
        )

    def tick(self, seconds=0.0):
        """Advance the clock, then run one evaluation."""
        self.now += seconds
        asyncio.run(self.controller._async_evaluate_once("test"))
        return self.controller._last_decision_reason

    @property
    def writes(self):
        return self.client.writes


# --- guards before any regulation ------------------------------------------


def test_no_coordinator_data(monkeypatch):
    h = Harness(monkeypatch, metrics=None)
    h.coordinator.data = None
    assert h.tick() == "no_coordinator_data"
    assert h.writes == []


def test_surplus_mode_off_does_nothing(monkeypatch):
    h = Harness(monkeypatch, options={"surplus_mode_enabled": False})
    assert h.tick() == "mode_disabled"
    assert h.writes == []


def test_missing_grid_sensor(monkeypatch):
    h = Harness(monkeypatch, options={"surplus_sensor_entity_id": ""})
    assert h.tick() == "missing_grid_sensor"


def test_unavailable_grid_sensor(monkeypatch):
    h = Harness(monkeypatch, sensors={"sensor.grid": "unavailable"})
    assert h.tick() == "grid_sensor_unavailable"


# --- start path ------------------------------------------------------------


def test_below_start_threshold_never_starts(monkeypatch):
    # Exporting only 500 W, start threshold is 1400 W.
    h = Harness(monkeypatch, sensors={"sensor.grid": -500})
    assert h.tick() == "below_start_threshold"
    assert h.writes == []


def test_start_waits_for_the_start_delay(monkeypatch):
    from tuya_ev_charger.solar_surplus import FIXED_START_DELAY_S

    h = Harness(monkeypatch, sensors={"sensor.grid": -3000})

    # First sighting only arms the timer.
    assert h.tick() == "start_delay_pending"
    assert h.writes == []

    # Still inside the delay.
    assert h.tick(FIXED_START_DELAY_S - 1) == "start_delay_pending"
    assert h.writes == []

    # Delay elapsed: the charge starts at the minimum current.
    assert h.tick(2) == "surplus_start"
    assert ("enabled", True) in h.writes


def test_a_surplus_dip_disarms_the_start_timer(monkeypatch):
    """The delay exists to ride out transients; it must reset, not accumulate."""
    from tuya_ev_charger.solar_surplus import FIXED_START_DELAY_S

    h = Harness(monkeypatch, sensors={"sensor.grid": -3000})
    assert h.tick() == "start_delay_pending"

    h.hass.set("sensor.grid", -100)  # cloud
    assert h.tick(FIXED_START_DELAY_S - 5) == "below_start_threshold"
    assert h.controller._timers.start_candidate_since is None

    # Sun returns: the full delay must be served again from scratch.
    h.hass.set("sensor.grid", -3000)
    assert h.tick(1) == "start_delay_pending"
    assert h.tick(FIXED_START_DELAY_S - 1) == "start_delay_pending"


def test_a_failed_start_write_is_reported(monkeypatch):
    from tuya_ev_charger.solar_surplus import FIXED_START_DELAY_S

    h = Harness(
        monkeypatch,
        sensors={"sensor.grid": -3000},
        client=_Client(set_enabled_ok=False),
    )
    h.tick()
    assert h.tick(FIXED_START_DELAY_S + 1) == "failed_start_charge"


# --- stop path -------------------------------------------------------------


def _charging_harness(monkeypatch, **kwargs):
    """A controller already mid-session at 10 A."""
    options = kwargs.pop("options", {})
    h = Harness(
        monkeypatch,
        options=options,
        metrics=_metrics(charging=True, current_target=10, total_power_kw=2.3),
        **kwargs,
    )
    return h


def test_stop_waits_for_the_stop_delay(monkeypatch):
    from tuya_ev_charger.solar_surplus import FIXED_STOP_DELAY_S

    # Importing 2 kW: well below the stop threshold.
    h = _charging_harness(monkeypatch, sensors={"sensor.grid": 2000})

    assert h.tick() == "stop_delay_pending"
    assert h.writes == []

    assert h.tick(FIXED_STOP_DELAY_S - 1) == "stop_delay_pending"
    assert h.writes == []

    assert h.tick(2) == "below_stop_threshold"
    assert ("enabled", False) in h.writes


def test_recovering_surplus_disarms_the_stop_timer(monkeypatch):
    from tuya_ev_charger.solar_surplus import FIXED_STOP_DELAY_S

    h = _charging_harness(monkeypatch, sensors={"sensor.grid": 2000})
    assert h.tick() == "stop_delay_pending"

    h.hass.set("sensor.grid", -4000)
    h.tick(FIXED_STOP_DELAY_S - 5)
    assert h.controller._timers.stop_candidate_since is None
    assert ("enabled", False) not in h.writes


# --- ramp and cooldowns ----------------------------------------------------


def test_ramp_moves_one_step_and_then_waits_for_the_cooldown(monkeypatch):
    # Charging at 10 A with plenty of surplus: target is above, so it ramps up.
    h = _charging_harness(
        monkeypatch,
        sensors={"sensor.grid": -5000},
        options={"surplus_adjust_up_cooldown_s": 60},
    )

    assert h.tick() == "adjust_current"
    stepped = [v for kind, v in h.writes if kind == "current"]
    assert stepped == [11], "must move a single 1 A step, not jump to the target"

    # The charger now reports 11 A; the cooldown blocks the next step.
    h.coordinator.data = _metrics(charging=True, current_target=11, total_power_kw=2.5)
    assert h.tick(10) == "adjust_cooldown_active"
    assert [v for kind, v in h.writes if kind == "current"] == [11]

    # Once elapsed, one more step.
    assert h.tick(60) == "adjust_current"
    assert [v for kind, v in h.writes if kind == "current"] == [11, 12]


def test_up_and_down_cooldowns_are_independent(monkeypatch):
    """A long up-cooldown must not delay a reduction: they are separate timers."""
    h = _charging_harness(
        monkeypatch,
        sensors={"sensor.grid": -5000},
        options={
            "surplus_adjust_up_cooldown_s": 600,
            "surplus_adjust_down_cooldown_s": 0,
        },
    )
    assert h.tick() == "adjust_current"  # up, arms the up-cooldown

    # Surplus shrinks to ~2000 W (8 A) -- lower than the 11 A setpoint but still
    # comfortably above the stop threshold, so the target drops without stopping.
    h.coordinator.data = _metrics(charging=True, current_target=11, total_power_kw=2.5)
    h.hass.set("sensor.grid", 500)
    assert h.tick(1) == "adjust_current"
    assert [v for kind, v in h.writes if kind == "current"] == [11, 10]


def test_holding_when_target_matches(monkeypatch):
    # ~10 A of surplus while charging at 10 A.
    h = _charging_harness(monkeypatch, sensors={"sensor.grid": -2300})
    assert h.tick() in {"holding_current", "adjust_current", "target_already_reached"}


# --- battery hysteresis ----------------------------------------------------


def test_battery_hysteresis_blocks_below_high_then_allows_above(monkeypatch):
    """Below the high threshold on first sight, the battery may not contribute."""
    h = Harness(
        monkeypatch,
        sensors={"sensor.grid": -3000, "sensor.soc": 50},
        options={
            "surplus_battery_soc_sensor_entity_id": "sensor.soc",
            "surplus_battery_soc_high_threshold_pct": 80,
            "surplus_battery_soc_low_threshold_pct": 60,
        },
    )
    assert h.tick() == "battery_soc_below_threshold"

    # Crossing the high threshold enables it.
    h.hass.set("sensor.soc", 85)
    assert h.tick(1) == "start_delay_pending"


def test_battery_hysteresis_holds_between_the_thresholds(monkeypatch):
    """Once enabled it stays enabled until the *low* threshold, not the high."""
    h = Harness(
        monkeypatch,
        sensors={"sensor.grid": -3000, "sensor.soc": 85},
        options={
            "surplus_battery_soc_sensor_entity_id": "sensor.soc",
            "surplus_battery_soc_high_threshold_pct": 80,
            "surplus_battery_soc_low_threshold_pct": 60,
        },
    )
    h.tick()
    assert h.controller._battery_soc_hysteresis_enabled is True

    # 70 % is below high but above low: must stay enabled.
    h.hass.set("sensor.soc", 70)
    h.tick(1)
    assert h.controller._battery_soc_hysteresis_enabled is True

    # Below low: disabled again.
    h.hass.set("sensor.soc", 55)
    h.tick(1)
    assert h.controller._battery_soc_hysteresis_enabled is False


# --- protection caps ------------------------------------------------------


def test_protection_cap_writes_straight_down_without_ramping(monkeypatch):
    """An overload is not a passing cloud: no 1 A step, no cooldown."""
    h = _charging_harness(
        monkeypatch,
        sensors={"sensor.grid": -5000, "sensor.total_load": 7000},
        options={
            "max_inverter_power_w": 5500,
            "total_load_sensor_entity_id": "sensor.total_load",
        },
    )
    h.coordinator.data = _metrics(charging=True, current_target=32, total_power_kw=5.0)

    reason = h.tick()
    # Budget 5500 - (7000 - 5000) = 3500 W -> 15 A, written directly from 32 A.
    assert ("current", 15) in h.writes

    # One write per cycle. Continuing into surplus regulation after a protection
    # reduction wrote twice -- two beeps -- and the second write dropped the car
    # to 7 A, because the ramp did not recognise the 15 A it had just written and
    # restarted from the minimum. The cap says 15 A is fine; the car must get it.
    currents = [v for kind, v in h.writes if kind == "current"]
    assert currents == [15], f"protection reduction wrote more than once: {currents}"
    assert reason == "inverter_limit_reduced", (
        "the binding protection limit must be what the user sees, not the surplus "
        "action that ran afterwards"
    )


def test_protection_cap_stops_when_even_the_minimum_will_not_fit(monkeypatch):
    h = _charging_harness(
        monkeypatch,
        sensors={"sensor.grid": -5000, "sensor.total_load": 10000},
        options={
            "max_inverter_power_w": 5500,
            "total_load_sensor_entity_id": "sensor.total_load",
        },
    )
    assert h.tick() == "inverter_limit_no_headroom"
    assert ("enabled", False) in h.writes


# --- pause and force charge ----------------------------------------------


def test_pause_suspends_regulation(monkeypatch):
    h = Harness(monkeypatch, sensors={"sensor.grid": -3000})
    asyncio.run(h.controller.async_pause_for(duration_s=600))
    assert h.tick() == "surplus_paused_active"


def test_force_charge_is_clamped_to_the_cap_not_dropped_to_the_minimum(monkeypatch):
    """Forcing a charge overrides scheduling, never the installation's limits --
    but it must still charge as fast as those limits allow.

    Requesting 32 A under a 15 A cap has to give 15 A. Falling back to the
    minimum would hand the user the slowest possible rate in response to an
    explicit request for the fastest.
    """
    h = _charging_harness(
        monkeypatch,
        sensors={"sensor.grid": -5000, "sensor.total_load": 7000},
        options={
            "max_inverter_power_w": 5500,
            "total_load_sensor_entity_id": "sensor.total_load",
        },
    )
    h.coordinator.data = _metrics(charging=True, current_target=32, total_power_kw=5.0)
    asyncio.run(h.controller.async_force_charge_for(duration_s=600, current_a=32))
    h.tick()

    written = [v for kind, v in h.writes if kind == "current"]
    assert written, "force charge should set a current"
    assert max(written) <= 15, f"force charge exceeded the 15 A cap: {written}"
    assert written[-1] == 15, f"force charge should use the whole cap, wrote {written}"


# --- dry run and traceability ---------------------------------------------


def test_dry_run_reports_what_would_happen_without_writing(monkeypatch):
    h = Harness(monkeypatch, sensors={"sensor.grid": -3000})
    report = h.controller.async_dry_run()

    assert report["would_do"] == "idle"
    assert report["reason"] == "start_delay_pending"
    assert report["inputs"]["surplus_w"] == 3000
    assert h.writes == [], "a dry run must never touch the charger"


def test_dry_run_does_not_advance_the_real_timers(monkeypatch):
    """Asking the question must not change the answer.

    The gates arm the start timer as a side effect of being consulted, so the dry
    run is given a copy. Without that, calling the service would make the next
    real evaluation think the delay had already begun.
    """
    h = Harness(monkeypatch, sensors={"sensor.grid": -3000})

    h.controller.async_dry_run()
    assert h.controller._timers.start_candidate_since is None

    # Nor does it consume the delay: the real evaluation still has to serve it.
    assert h.tick() == "start_delay_pending"


def test_dry_run_does_not_disturb_the_forecast_state(monkeypatch):
    h = Harness(
        monkeypatch,
        sensors={"sensor.grid": -3000, "sensor.forecast": 4000},
        options={"surplus_forecast_sensor_entity_id": "sensor.forecast"},
    )
    h.tick()
    ema_before = h.controller._forecast_ema_surplus_w

    h.controller.async_dry_run()
    assert h.controller._forecast_ema_surplus_w == ema_before


def test_dry_run_without_data_says_so(monkeypatch):
    h = Harness(monkeypatch)
    h.coordinator.data = None
    assert "error" in h.controller.async_dry_run()


def test_the_decision_trace_names_the_gate_that_decided(monkeypatch):
    h = Harness(monkeypatch, sensors={"sensor.grid": -3000})
    h.tick()

    trace = h.controller.snapshot.decision_trace
    assert trace["decided_by"] == "start"
    # The gates that declined show how far evaluation got before deciding.
    assert "no_currents" in trace["gates_declined"]
    assert "mode_disabled" in trace["gates_declined"]
    assert trace["surplus_w"] == 3000
    assert trace["start_threshold_w"] == 1400


def test_the_trace_names_the_protection_limit_that_bound(monkeypatch):
    h = _charging_harness(
        monkeypatch,
        sensors={"sensor.grid": -5000, "sensor.total_load": 7000},
        options={
            "max_inverter_power_w": 5500,
            "total_load_sensor_entity_id": "sensor.total_load",
        },
    )
    h.coordinator.data = _metrics(charging=True, current_target=32, total_power_kw=5.0)
    h.tick()

    trace = h.controller.snapshot.decision_trace
    assert trace["decided_by"] == "protection_reduce"
    assert trace["protection_cap_a"] == 15
    assert trace["protection_source"] == "inverter_limit"


# --- predictive pre-emption ------------------------------------------------


def test_an_announced_hob_cuts_the_car_before_the_meter_moves(monkeypatch):
    """The limitation documented in 2.13.1, addressed end to end.

    The inverter cap can only react as fast as its sensor. Here the hob switch
    turns on and the total-load reading has not budged yet -- the car must come
    down on the announcement alone.
    """
    h = _charging_harness(
        monkeypatch,
        sensors={
            "sensor.grid": -5000,
            "sensor.total_load": 5500,  # car 5 kW + 500 W baseline; hob not seen
            "switch.hob": "off",
        },
        options={
            "max_inverter_power_w": 5500,
            "total_load_sensor_entity_id": "sensor.total_load",
            "load_reservations": "switch.hob: 3000",
        },
    )
    h.coordinator.data = _metrics(charging=True, current_target=21, total_power_kw=5.0)

    # Nothing announced yet: 5500 - (5500 - 5000) = 5000 W of headroom, so the
    # 21 A setpoint stands and nothing is written.
    h.tick()
    assert ("current", 8) not in h.writes

    # The hob switch flips. The power sensor still reads 5500.
    h.hass.set("switch.hob", "on")
    reason = h.tick(1)

    # 5500 - 500 - 3000 = 2000 W -> 8 A, written straight away.
    assert reason == "inverter_limit_reduced"
    assert ("current", 8) in h.writes


def test_the_reservation_expires_so_the_car_is_not_held_down_twice(monkeypatch):
    """Once the sensor reports the hob, the reservation must stop applying, or
    the same 3 kW would be subtracted twice for as long as the hob was on."""
    from tuya_ev_charger.preemption import DEFAULT_RESERVATION_WINDOW_S

    h = _charging_harness(
        monkeypatch,
        sensors={
            "sensor.grid": -5000,
            "sensor.total_load": 5500,
            "switch.hob": "on",
        },
        options={
            "max_inverter_power_w": 5500,
            "total_load_sensor_entity_id": "sensor.total_load",
            "load_reservations": "switch.hob: 3000",
        },
    )
    h.coordinator.data = _metrics(charging=True, current_target=8, total_power_kw=1.8)
    h.tick()

    # The hob is now in the measurement: 1800 car + 500 base + 3000 hob.
    h.hass.set("sensor.total_load", 5300)
    ladder = tuple(range(6, 33))
    cap_during = h.controller._inverter_limit_current(h.coordinator.data, ladder)

    h.now += DEFAULT_RESERVATION_WINDOW_S + 1
    cap_after = h.controller._inverter_limit_current(h.coordinator.data, ladder)

    assert cap_after > cap_during, (
        "the reservation still applied after its window, double-counting the hob"
    )


def test_the_announcing_entity_is_watched_for_state_changes(monkeypatch):
    """Reacting the instant it switches is the entire point; without the
    subscription the cap would wait for the next 30 s poll."""
    h = Harness(
        monkeypatch,
        options={
            "max_inverter_power_w": 5500,
            "total_load_sensor_entity_id": "sensor.total_load",
            "load_reservations": "switch.hob: 3000, switch.oven: 2500",
        },
    )
    tracked = h.controller._tracked_sensor_entities()
    assert "switch.hob" in tracked
    assert "switch.oven" in tracked
