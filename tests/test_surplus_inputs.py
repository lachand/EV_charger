"""How the surplus controller reads the world: sensors, tariffs, lifecycle.

`test_surplus_state_machine.py` drives the decision. This covers what feeds it --
the rules for reading each optional sensor, and above all what each one does when
it is missing or unreadable. The rules are deliberately not uniform (a safety veto
fails closed, a tariff signal fails open, a power cap just does not apply), so each
is pinned individually.

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import asyncio
import types

import pytest


class _State:
    def __init__(self, value, unit=None):
        self.state = str(value)
        self.attributes = {"unit_of_measurement": unit} if unit else {}


class _Hass:
    def __init__(self, sensors=None):
        self.sensors = dict(sensors or {})
        self.states = types.SimpleNamespace(get=lambda entity_id: self.sensors.get(entity_id))
        self.jobs: list = []

    def add_job(self, target, *args):
        self.jobs.append((target, args))


def _controller(options=None, sensors=None, monkeypatch=None):
    from tuya_ev_charger import solar_surplus

    hass = _Hass({k: v if isinstance(v, _State) else _State(v) for k, v in (sensors or {}).items()})
    entry = types.SimpleNamespace(options=options or {}, title="t", entry_id="e1")
    subs: list = []
    coordinator = types.SimpleNamespace(
        data=None, async_add_listener=lambda cb: (subs.append(cb), lambda: subs.remove(cb))[1]
    )
    controller = solar_surplus.SolarSurplusController(
        hass=hass, entry=entry, client=types.SimpleNamespace(), coordinator=coordinator
    )
    controller.hass = hass
    controller.subs = subs
    return controller


# --- numeric power sensors ------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "unit", "expected"),
    [
        ("1500", None, 1500.0),
        ("1,5", "kW", 1500.0),
        ("-2.5", " KW ", -2500.0),
        ("unavailable", None, None),
        ("unknown", None, None),
        ("", None, None),
        ("garbage", None, None),
    ],
)
def test_a_power_sensor_is_read_in_watts_whatever_its_unit(state, unit, expected):
    c = _controller(sensors={"sensor.p": _State(state, unit)})

    assert c._inputs.read_sensor_power_w("sensor.p") == expected


def test_a_missing_or_unconfigured_sensor_reads_as_none():
    c = _controller()

    assert c._inputs.read_sensor_power_w("") is None
    assert c._inputs.read_sensor_power_w("sensor.absent") is None
    assert c._inputs.read_sensor_numeric("sensor.absent") is None


def test_the_grid_sensor_sign_can_be_flipped():
    options = {"surplus_sensor_entity_id": "sensor.grid"}
    plain = _controller(options, {"sensor.grid": 800})
    flipped = _controller({**options, "surplus_sensor_inverted": True}, {"sensor.grid": 800})

    assert plain._inputs.read_grid_power_w() == 800
    assert flipped._inputs.read_grid_power_w() == -800
    assert _controller(options)._inputs.read_grid_power_w() is None


def test_curtailed_power_is_never_negative_and_defaults_to_zero():
    options = {"surplus_curtailment_sensor_entity_id": "sensor.curt"}

    assert _controller()._inputs.read_curtailment_power_w() == 0.0
    assert _controller(options)._inputs.read_curtailment_power_w() == 0.0  # sensor absent
    assert _controller(options, {"sensor.curt": 300})._inputs.read_curtailment_power_w() == 300
    assert _controller(options, {"sensor.curt": -300})._inputs.read_curtailment_power_w() == 0.0
    flipped = {**options, "surplus_curtailment_sensor_inverted": True}
    assert _controller(flipped, {"sensor.curt": -300})._inputs.read_curtailment_power_w() == 300


# --- the house battery ----------------------------------------------------------

BATTERY = {"surplus_battery_net_discharge_sensor_entity_id": "sensor.bat"}


def test_battery_discharge_is_measured_only_when_a_sensor_is_configured():
    assert _controller()._inputs.read_battery_net_discharge_w() is None
    assert _controller(BATTERY)._inputs.read_battery_net_discharge_w() is None  # unreadable
    assert _controller(BATTERY, {"sensor.bat": 700})._inputs.read_battery_net_discharge_w() == 700
    assert _controller(BATTERY, {"sensor.bat": -700})._inputs.read_battery_net_discharge_w() == 0.0
    flipped = {**BATTERY, "surplus_battery_net_discharge_sensor_inverted": True}
    assert _controller(flipped, {"sensor.bat": -700})._inputs.read_battery_net_discharge_w() == 700


def test_discharge_over_the_allowance_counts_only_the_excess():
    allowed = {
        **BATTERY,
        "surplus_allow_battery_discharge_for_ev": True,
        "surplus_max_battery_discharge_for_ev_w": 500,
    }

    assert _controller(allowed, {"sensor.bat": 800})._inputs.battery_discharge_over_limit_w() == 300
    assert _controller(allowed, {"sensor.bat": 400})._inputs.battery_discharge_over_limit_w() == 0
    # Not allowed at all: any discharge is over the limit.
    forbidden = {**BATTERY, "surplus_allow_battery_discharge_for_ev": False}
    assert (
        _controller(forbidden, {"sensor.bat": 200})._inputs.battery_discharge_over_limit_w() == 200
    )
    # No reading: nothing to hold against the car.
    assert _controller(allowed)._inputs.battery_discharge_over_limit_w() == 0.0


def test_the_allowance_is_zero_unless_discharge_is_allowed():
    off = _controller(
        {
            "surplus_allow_battery_discharge_for_ev": False,
            "surplus_max_battery_discharge_for_ev_w": 900,
        }
    )
    on = _controller(
        {
            "surplus_allow_battery_discharge_for_ev": True,
            "surplus_max_battery_discharge_for_ev_w": 900,
        }
    )

    assert off._inputs.allowed_battery_discharge_w() == 0.0
    assert on._inputs.allowed_battery_discharge_w() == 900.0


SOC = {
    "surplus_battery_soc_sensor_entity_id": "sensor.soc",
    "surplus_battery_soc_high_threshold_pct": 80,
    "surplus_battery_soc_low_threshold_pct": 40,
}


def test_without_a_battery_sensor_the_battery_never_blocks():
    assert _controller()._inputs.is_battery_ready() is True


def test_an_unreadable_battery_level_blocks_rather_than_guesses():
    assert _controller(SOC)._inputs.is_battery_ready() is False
    assert _controller(SOC, {"sensor.soc": "unavailable"})._inputs.is_battery_ready() is False


def test_the_battery_gate_has_hysteresis_between_its_thresholds():
    c = _controller(SOC, {"sensor.soc": 85})
    assert c._inputs.is_battery_ready() is True  # crossed the high threshold

    c.hass.sensors["sensor.soc"] = _State(60)
    assert c._inputs.is_battery_ready() is True  # in between: stays on

    c.hass.sensors["sensor.soc"] = _State(30)
    assert c._inputs.is_battery_ready() is False  # fell below the low threshold

    c.hass.sensors["sensor.soc"] = _State(60)
    assert c._inputs.is_battery_ready() is False  # in between: stays off


# --- the external veto fails closed ---------------------------------------------

EXTERNAL = {"external_charge_allowed_sensor_entity_id": "binary_sensor.grid_ok"}


@pytest.mark.parametrize(
    ("state", "inverted", "expected"),
    [
        ("on", False, True),
        ("off", False, False),
        ("on", True, False),
        ("off", True, True),
        ("unavailable", False, False),
        ("unknown", True, False),
        ("banana", False, False),
    ],
)
def test_the_external_condition_is_a_safety_veto(state, inverted, expected):
    options = {**EXTERNAL, "external_charge_allowed_sensor_inverted": inverted}
    c = _controller(options, {"binary_sensor.grid_ok": state})

    assert c._inputs.read_external_charge_allowed() is expected


def test_the_external_condition_never_blocks_when_not_configured():
    assert _controller()._inputs.read_external_charge_allowed() is True


def test_a_missing_external_sensor_blocks():
    assert _controller(EXTERNAL)._inputs.read_external_charge_allowed() is False


# --- tariff: off-peak ------------------------------------------------------------

OFF_PEAK_SENSOR = {"off_peak_sensor_entity_id": "binary_sensor.hc"}


def test_no_tariff_configured_means_no_restriction():
    assert _controller()._inputs.resolve_off_peak_now() is None


@pytest.mark.parametrize(
    ("state", "inverted", "expected"),
    [("on", False, True), ("off", False, False), ("on", True, False), ("off", True, True)],
)
def test_an_off_peak_sensor_is_the_source_of_truth(state, inverted, expected):
    options = {**OFF_PEAK_SENSOR, "off_peak_sensor_inverted": inverted}
    c = _controller(options, {"binary_sensor.hc": state})

    assert c._inputs.resolve_off_peak_now() is expected


@pytest.mark.parametrize("state", ["unavailable", "unknown", "banana"])
def test_an_unreadable_off_peak_sensor_fails_open_not_back_to_windows(state):
    """A silent second source would make "why is it not charging" depend on availability."""
    options = {**OFF_PEAK_SENSOR, "off_peak_windows": "00:00-23:59"}
    c = _controller(options, {"binary_sensor.hc": state})

    assert c._inputs.resolve_off_peak_now() is None


def test_a_missing_off_peak_sensor_fails_open():
    assert _controller(OFF_PEAK_SENSOR)._inputs.resolve_off_peak_now() is None


def test_off_peak_windows_are_used_when_there_is_no_sensor(monkeypatch):
    import datetime

    from tuya_ev_charger import surplus_reader

    monkeypatch.setattr(
        surplus_reader.dt_util, "now", lambda: datetime.datetime(2026, 1, 1, 2, 30), raising=False
    )

    inside = _controller({"off_peak_windows": "01:00-06:00"})
    outside = _controller({"off_peak_windows": "08:00-12:00"})

    assert inside._inputs.resolve_off_peak_now() is True
    assert outside._inputs.resolve_off_peak_now() is False


# --- tariff: critical peak --------------------------------------------------------

CRITICAL = {"critical_peak_sensor_entity_id": "binary_sensor.red"}


@pytest.mark.parametrize(
    ("is_off_peak", "state", "inverted", "expected"),
    [
        (False, "on", False, True),
        (False, "off", False, False),
        (False, "on", True, False),
        (True, "on", False, False),  # off-peak pricing is untouched by this signal
        (None, "on", False, False),
        (False, "unavailable", False, False),
        (False, "banana", False, False),
    ],
)
def test_a_critical_peak_only_counts_during_peak_hours(is_off_peak, state, inverted, expected):
    options = {**CRITICAL, "critical_peak_sensor_inverted": inverted}
    c = _controller(options, {"binary_sensor.red": state})

    assert c._inputs.resolve_critical_peak_now(is_off_peak) is expected


def test_critical_peak_is_false_when_unconfigured_or_missing():
    assert _controller()._inputs.resolve_critical_peak_now(False) is False
    assert _controller(CRITICAL)._inputs.resolve_critical_peak_now(False) is False


# --- which entities are watched ---------------------------------------------------


def test_every_configured_sensor_is_watched_once():
    c = _controller(
        {
            "surplus_sensor_entity_id": "sensor.grid",
            "total_load_sensor_entity_id": "sensor.grid",  # duplicate
            "off_peak_sensor_entity_id": "binary_sensor.hc",
            "critical_peak_sensor_entity_id": "binary_sensor.red",
        }
    )

    assert c._inputs.tracked_sensor_entities() == [
        "sensor.grid",
        "binary_sensor.hc",
        "binary_sensor.red",
    ]


def test_nothing_is_watched_when_nothing_is_configured():
    assert _controller()._inputs.tracked_sensor_entities() == []


# --- lifecycle ---------------------------------------------------------------------


def test_start_subscribes_and_schedules_a_first_evaluation(monkeypatch):
    from tuya_ev_charger import solar_surplus

    tracked = []
    monkeypatch.setattr(
        solar_surplus,
        "async_track_state_change_event",
        lambda hass, entities, cb: (tracked.append(list(entities)), lambda: tracked.append("off"))[
            1
        ],
    )
    c = _controller({"surplus_sensor_entity_id": "sensor.grid"})

    asyncio.run(c.async_start())

    assert c.subs  # listening to the coordinator
    assert tracked == [["sensor.grid"]]
    assert [args for _, args in c.hass.jobs] == [("startup",)]


def test_start_without_a_grid_sensor_in_surplus_mode_warns_but_still_runs(monkeypatch, caplog):
    c = _controller({"surplus_mode_enabled": True})

    with caplog.at_level("WARNING"):
        asyncio.run(c.async_start())

    assert "no grid power sensor" in caplog.text
    assert c.hass.jobs


def test_applying_settings_rebinds_the_listeners_and_forces_an_evaluation(monkeypatch):
    from tuya_ev_charger import solar_surplus

    events = []
    monkeypatch.setattr(
        solar_surplus,
        "async_track_state_change_event",
        lambda hass, entities, cb: (
            events.append(("on", list(entities))),
            lambda: events.append("off"),
        )[1],
    )
    c = _controller({"surplus_sensor_entity_id": "sensor.grid"})
    asyncio.run(c.async_start())

    c._entry.options = {"surplus_sensor_entity_id": "sensor.other"}
    asyncio.run(c.async_apply_settings())

    assert events == [("on", ["sensor.grid"]), "off", ("on", ["sensor.other"])]
    assert c._settings.grid_sensor_entity_id == "sensor.other"
    assert c.hass.jobs[-1][1] == ("options_updated",)


def test_shutdown_drops_every_subscription_and_listener(monkeypatch):
    from tuya_ev_charger import solar_surplus

    events = []
    monkeypatch.setattr(
        solar_surplus,
        "async_track_state_change_event",
        lambda hass, entities, cb: lambda: events.append("sensor_off"),
    )
    c = _controller({"surplus_sensor_entity_id": "sensor.grid"})
    asyncio.run(c.async_start())
    c.async_add_update_listener(lambda: None)

    asyncio.run(c.async_shutdown())

    assert events == ["sensor_off"]
    assert c.subs == []
    assert c._listeners == []


def test_a_listener_can_unsubscribe_and_a_broken_one_does_not_stop_the_rest():
    c = _controller()
    calls = []

    def _broken():
        raise RuntimeError("boom")

    c.async_add_update_listener(_broken)
    unsubscribe = c.async_add_update_listener(lambda: calls.append("ok"))

    c._notify_state_listeners()
    assert calls == ["ok"]

    unsubscribe()
    unsubscribe()  # idempotent
    c._notify_state_listeners()
    assert calls == ["ok"]


# --- the profile assistant -----------------------------------------------------------


def _assistant(dps):
    c = _controller()

    async def _raw():
        return dps

    c._client.async_get_raw_dps = _raw
    return asyncio.run(c.async_profile_assistant_report())


def test_the_assistant_reports_an_unreadable_charger():
    assert "error" in _assistant(None)


def test_the_assistant_recognises_the_known_depow_layout():
    from tuya_ev_charger.const import (
        CHARGER_PROFILE_DEPOW_V2,
        DP_CHARGER_INFO,
        DP_CURRENT_TARGET,
        DP_DO_CHARGE,
        DP_METRICS,
        DP_WORK_STATE_DEBUG,
    )

    dps = {
        DP_METRICS: '{"L1": [2300, 100, 2300]}',
        DP_CHARGER_INFO: '{"model": "x"}',
        DP_DO_CHARGE: True,
        DP_CURRENT_TARGET: 10,
        DP_WORK_STATE_DEBUG: "WORKING",
    }

    report = _assistant(dps)

    assert report["suggested_profile"] == CHARGER_PROFILE_DEPOW_V2
    assert report["candidates"]["metrics"] == [DP_METRICS]
    assert report["candidates"]["charger_info"] == [DP_CHARGER_INFO]
    assert DP_DO_CHARGE in report["candidates"]["do_charge"]
    assert report["detected_dp_ids"] == sorted(dps)


def test_an_unfamiliar_layout_suggests_the_generic_profile():
    report = _assistant({"1": "x", "2": 16})

    assert report["suggested_profile"] == "generic_v1"
    assert report["candidates"]["current_target"] == ["2"]
    assert len(report["sample_values"]) == 2


# --- helpers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("22:30", 1350),
        ("0:00", 0),
        ("23:59", 1439),
        ("", None),
        ("24:00", None),
        ("12:60", None),
        ("12", None),
        ("ab:cd", None),
        (" 7:05 ", 425),
    ],
)
def test_the_session_end_time_is_parsed_strictly(raw, expected):
    from tuya_ev_charger.surplus_settings import parse_end_time

    assert parse_end_time(raw) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"L1": []}, True),
        ('{"L1": []}', True),
        ('{"L2": []}', False),
        ("nope", False),
        (5, False),
        ("[1]", False),
    ],
)
def test_metrics_payloads_are_recognised_by_their_phase_keys(value, expected):
    from tuya_ev_charger.profile_assistant import looks_like_metrics

    assert looks_like_metrics(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"Model": 1}, True),
        ('{"manufacturer": "x"}', True),
        ({"other": 1}, False),
        ('{"other": 1}', False),
        ("nope", False),
        ("[1]", False),
        (5, False),
    ],
)
def test_charger_info_payloads_are_recognised_by_their_keys(value, expected):
    from tuya_ev_charger.profile_assistant import looks_like_charger_info

    assert looks_like_charger_info(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(6, True), ("32", True), (5, False), (33, False), ("x", False), (None, False)],
)
def test_a_current_target_is_a_whole_number_in_the_charger_range(value, expected):
    from tuya_ev_charger.profile_assistant import looks_like_current_target

    assert looks_like_current_target(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"), [("working", True), (" DONE ", True), ("IDLE", False), (5, False)]
)
def test_a_state_string_is_recognised_case_insensitively(value, expected):
    from tuya_ev_charger.profile_assistant import looks_like_state_debug

    assert looks_like_state_debug(value) is expected


# --- the reader on its own -----------------------------------------------------------


def test_the_reader_works_without_a_controller():
    """Reading is separate from deciding: hass and settings are all it needs."""
    from tuya_ev_charger.surplus_reader import SurplusReader
    from tuya_ev_charger.surplus_settings import settings_from_entry

    hass = _Hass({"sensor.grid": _State("1,2", "kW")})
    settings = settings_from_entry(
        types.SimpleNamespace(options={"surplus_sensor_entity_id": "sensor.grid"})
    )
    reader = SurplusReader(hass, lambda: settings)

    assert reader.read_grid_power_w() == 1200.0
    assert reader.tracked_sensor_entities() == ["sensor.grid"]


def test_the_reader_sees_settings_that_change_in_place():
    """The controller swaps its settings on an options change; a copy would go stale."""
    c = _controller({"surplus_sensor_entity_id": "sensor.a"}, {"sensor.a": 100, "sensor.b": 900})
    assert c._inputs.read_grid_power_w() == 100

    c._entry.options = {"surplus_sensor_entity_id": "sensor.b"}
    asyncio.run(c.async_apply_settings())

    assert c._inputs.read_grid_power_w() == 900


def test_the_battery_gate_memory_belongs_to_the_reader():
    c = _controller(SOC, {"sensor.soc": 85})

    c._inputs.is_battery_ready()

    assert c._inputs._battery_soc_hysteresis_enabled is True
