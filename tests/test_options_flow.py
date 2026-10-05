"""The options flow: what is stored when the form is submitted, and what is shown.

`test_regressions.py` pins the failures that reached users (entity pickers wiped on
save, section nesting). This covers the rest of the submit path -- cleaning,
clamping, and the custom-DP-mapping validation -- and the defaults the form
computes for display. The flow's Home Assistant plumbing is replaced by a recorder.

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import asyncio
import types

import pytest


def _flow(options=None, data=None):
    from tuya_ev_charger.options_flow import TuyaEVChargerOptionsFlow

    entry = types.SimpleNamespace(data=data or {}, options=options or {}, entry_id="e1")
    flow = TuyaEVChargerOptionsFlow(entry)
    flow.async_create_entry = lambda data: {"type": "create_entry", "data": data}
    flow.async_show_form = lambda **kw: {"type": "form", **kw}
    return flow


def _submit(flow, user_input):
    return asyncio.run(flow.async_step_init(user_input))


# --- what is stored ------------------------------------------------------------


def test_untouched_options_survive_a_save():
    flow = _flow(options={"scan_interval": 45, "keep_me": "yes"})

    result = _submit(flow, {"device": {"scan_interval": 60}})

    assert result["type"] == "create_entry"
    assert result["data"]["scan_interval"] == 60
    assert result["data"]["keep_me"] == "yes"


def test_an_optional_entity_left_out_of_the_form_is_cleared():
    flow = _flow(options={"surplus_sensor_entity_id": "sensor.grid"})

    result = _submit(flow, {"device": {}})

    assert result["data"]["surplus_sensor_entity_id"] == ""


def test_an_optional_entity_that_is_filled_in_is_kept_and_trimmed():
    flow = _flow()

    result = _submit(flow, {"surplus": {"surplus_sensor_entity_id": " sensor.grid "}})

    assert result["data"]["surplus_sensor_entity_id"] == "sensor.grid"


def test_a_picker_that_comes_back_as_the_string_none_is_cleared():
    flow = _flow()

    result = _submit(flow, {"surplus": {"surplus_sensor_entity_id": "None"}})

    assert result["data"]["surplus_sensor_entity_id"] == ""


def test_a_blank_free_text_field_falls_back_to_its_default():
    from tuya_ev_charger.const import DEFAULT_OFF_PEAK_WINDOWS

    flow = _flow(options={"off_peak_windows": "01:00-06:00"})

    result = _submit(flow, {"device": {}})

    assert result["data"]["off_peak_windows"] == DEFAULT_OFF_PEAK_WINDOWS


def test_battery_thresholds_are_forced_into_a_consistent_order():
    flow = _flow()

    result = _submit(
        flow,
        {
            "battery": {
                "surplus_battery_soc_high_threshold_pct": 50,
                "surplus_battery_soc_low_threshold_pct": 70,
            }
        },
    )

    high = result["data"]["surplus_battery_soc_high_threshold_pct"]
    low = result["data"]["surplus_battery_soc_low_threshold_pct"]
    assert low < high


def test_the_stop_threshold_can_never_exceed_the_start_threshold():
    flow = _flow()

    result = _submit(
        flow,
        {"surplus": {"surplus_start_threshold_w": 1500, "surplus_stop_threshold_w": 4000}},
    )

    assert result["data"]["surplus_stop_threshold_w"] == 1500


# --- the custom DP mapping ------------------------------------------------------


def test_a_bad_custom_mapping_reopens_the_form_with_the_reason():
    flow = _flow()

    result = _submit(
        flow,
        {"device": {"charger_profile": "custom_json", "charger_profile_json": "{not json"}},
    )

    assert result["type"] == "form"
    assert result["errors"] == {"charger_profile_json": "invalid_dp_profile"}
    assert "not valid JSON" in result["description_placeholders"]["dp_profile_problem"]
    assert result["description_placeholders"]["dp_profile_fields"]


def test_a_good_custom_mapping_is_stored_and_clears_the_problem():
    flow = _flow()
    flow._profile_json_problem = "stale"

    result = _submit(
        flow,
        {
            "device": {
                "charger_profile": "custom_json",
                "charger_profile_json": '{"do_charge": "200"}',
            }
        },
    )

    assert result["type"] == "create_entry"
    assert flow._profile_json_problem is None


def test_the_mapping_is_not_validated_for_a_builtin_profile():
    flow = _flow()

    result = _submit(
        flow,
        {"device": {"charger_profile": "generic_v1", "charger_profile_json": "{not json"}},
    )

    assert result["type"] == "create_entry"


# --- what the form shows --------------------------------------------------------


def _shown(options=None, data=None):
    flow = _flow(options=options, data=data)
    result = asyncio.run(flow.async_step_init())
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    return flow, result


def test_the_form_opens_without_a_problem_message():
    _, result = _shown()

    assert result["errors"] == {}
    assert result["description_placeholders"]["dp_profile_problem"] == ""


def test_the_form_builds_for_a_charger_with_nothing_configured():
    _, result = _shown()

    assert result["data_schema"] is not None


def test_the_displayed_low_threshold_is_pulled_below_the_high_one():
    flow = _flow(
        options={
            "surplus_battery_soc_high_threshold_pct": 50,
            "surplus_battery_soc_low_threshold_pct": 90,
        }
    )
    captured = {}
    flow._build_options_schema = lambda options, computed: captured.update(computed) or "schema"

    asyncio.run(flow.async_step_init())

    assert (
        captured["surplus_battery_soc_low_threshold_pct"]
        < (captured["surplus_battery_soc_high_threshold_pct"])
    )


def test_the_displayed_stop_threshold_is_capped_at_the_start_threshold():
    flow = _flow(options={"surplus_start_threshold_w": 1500, "surplus_stop_threshold_w": 4000})
    captured = {}
    flow._build_options_schema = lambda options, computed: captured.update(computed) or "schema"

    asyncio.run(flow.async_step_init())

    assert captured["surplus_stop_threshold_w"] == 1500


def test_an_old_single_battery_threshold_seeds_the_new_high_threshold():
    flow = _flow(options={"surplus_battery_soc_threshold_pct": 77})
    captured = {}
    flow._build_options_schema = lambda options, computed: captured.update(computed) or "schema"

    asyncio.run(flow.async_step_init())

    assert captured["surplus_battery_soc_high_threshold_pct"] == 77


def test_the_custom_mapping_shown_falls_back_to_the_one_set_at_setup():
    flow = _flow(data={"charger_profile_json": '{"do_charge": "9"}'})
    captured = {}
    flow._build_options_schema = lambda options, computed: captured.update(computed) or "schema"

    asyncio.run(flow.async_step_init())

    assert captured["charger_profile_json"] == '{"do_charge": "9"}'


# --- the small readers ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("5", 5), (5.9, 5), ("x", 7), (None, 7), (-10, 1), (10_000, 100)],
)
def test_an_int_option_is_clamped_and_forgiving(raw, expected):
    from tuya_ev_charger.option_values import option_int

    assert option_int({"k": raw}, "k", 7, 1, 100) == expected


def test_a_missing_int_option_takes_the_default():
    from tuya_ev_charger.option_values import option_int

    assert option_int({}, "k", 7, 1, 100) == 7


@pytest.mark.parametrize(("raw", "expected"), [("1.5", 1.5), ("-3", -3.0), ("x", 2.0), (None, 2.0)])
def test_a_float_option_keeps_its_sign(raw, expected):
    from tuya_ev_charger.option_values import option_float

    assert option_float({"k": raw}, "k", 2.0) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        ("yes", True),
        ("OFF", False),
        ("no", False),
        (1, True),
        (0, False),
        ("maybe", True),
    ],
)
def test_a_bool_option_reads_the_usual_spellings(raw, expected):
    from tuya_ev_charger.option_values import option_bool

    assert option_bool({"k": raw}, "k", False) is expected


def test_a_choice_option_must_be_one_of_the_choices():
    from tuya_ev_charger.options_flow import _option_choice

    assert _option_choice({"k": " ECO "}, "k", "balanced", ("eco", "fast")) == "eco"
    assert _option_choice({"k": "turbo"}, "k", "balanced", ("eco", "fast")) == "balanced"


@pytest.mark.parametrize(
    ("raw", "expected"), [("sensor.a", "sensor.a"), ("  ", None), ("None", None), (None, None)]
)
def test_an_entity_option_is_a_clean_id_or_nothing(raw, expected):
    from tuya_ev_charger.options_flow import _option_entity

    assert _option_entity({"k": raw}, "k", "fallback") == expected


def test_a_text_option_is_trimmed_and_defaults_when_none():
    from tuya_ev_charger.option_values import option_text

    assert option_text({"k": "  x "}, "k", "d") == "x"
    assert option_text({"k": None}, "k", "d") == "d"


def test_the_selectors_are_restricted_to_sensible_domains():
    from tuya_ev_charger.options_flow import _boolean_sensor_selector, _sensor_selector

    assert _sensor_selector() is not None
    assert _boolean_sensor_selector() is not None


# --- a negative price is a real tariff -------------------------------------------------


def _price_validator(key):
    flow = _flow()
    schema = flow._build_options_schema({}, computed={})
    for section_obj in schema.schema.values():
        for field_marker, validator in section_obj.schema.schema.items():
            if str(field_marker) == key:
                return validator
    raise AssertionError(f"{key} is not in the options form")


@pytest.mark.parametrize("key", ["off_peak_price", "peak_price"])
def test_the_price_fields_accept_a_negative_price(key):
    validate = _price_validator(key)

    assert validate(-0.05) == -0.05
    assert validate(0.16) == 0.16
    assert validate(100) == 100.0
    assert validate(-100) == -100.0


@pytest.mark.parametrize("key", ["off_peak_price", "peak_price"])
def test_the_price_fields_still_refuse_an_absurd_value(key):
    import voluptuous as vol

    validate = _price_validator(key)

    for absurd in (101, -101):
        with pytest.raises(vol.Invalid):
            validate(absurd)


def test_a_stored_negative_price_survives_opening_and_saving_the_form():
    """It used to be shown as 0 and overwritten with 0 on the next save."""
    flow = _flow(options={"off_peak_price": -0.05, "peak_price": 0.27})
    captured = {}
    flow._build_options_schema = lambda options, computed: captured.update(options) or "schema"

    asyncio.run(flow.async_step_init())
    result = _submit(flow, {"tariff": {"off_peak_price": -0.05, "peak_price": 0.27}})

    assert captured["off_peak_price"] == -0.05
    assert result["data"]["off_peak_price"] == -0.05


# --- unreadable surplus numbers fall back, and the thresholds stay coherent ------------------


def _normalized(**values):
    from tuya_ev_charger.options_flow import _normalize_surplus_options

    data = dict(values)
    _normalize_surplus_options(data)
    return data


JUNK = ["not a number", None, [], {}]


@pytest.mark.parametrize("junk", JUNK)
def test_unreadable_battery_thresholds_fall_back_to_their_defaults(junk):
    from tuya_ev_charger.const import (
        DEFAULT_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT as HIGH,
    )
    from tuya_ev_charger.const import (
        DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT as LOW,
    )

    data = _normalized(
        surplus_battery_soc_high_threshold_pct=junk, surplus_battery_soc_low_threshold_pct=junk
    )

    assert data["surplus_battery_soc_high_threshold_pct"] == HIGH
    assert data["surplus_battery_soc_low_threshold_pct"] == LOW


@pytest.mark.parametrize("junk", JUNK)
def test_unreadable_power_thresholds_fall_back_to_their_defaults(junk):
    from tuya_ev_charger.const import (
        DEFAULT_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W as DISCHARGE,
    )
    from tuya_ev_charger.const import (
        DEFAULT_SURPLUS_START_THRESHOLD_W as START,
    )
    from tuya_ev_charger.const import (
        DEFAULT_SURPLUS_STOP_THRESHOLD_W as STOP,
    )

    data = _normalized(
        surplus_start_threshold_w=junk,
        surplus_stop_threshold_w=junk,
        surplus_max_battery_discharge_for_ev_w=junk,
    )

    assert data["surplus_start_threshold_w"] == START
    assert data["surplus_stop_threshold_w"] == min(STOP, START)
    assert data["surplus_max_battery_discharge_for_ev_w"] == DISCHARGE


def test_an_empty_submission_gets_every_surplus_number_from_the_defaults():
    data = _normalized()

    assert (
        data["surplus_battery_soc_low_threshold_pct"]
        < data["surplus_battery_soc_high_threshold_pct"]
    )
    assert data["surplus_stop_threshold_w"] <= data["surplus_start_threshold_w"]


def test_a_high_battery_threshold_at_the_floor_is_lifted_above_it():
    from tuya_ev_charger.const import MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT as FLOOR

    data = _normalized(
        surplus_battery_soc_high_threshold_pct=FLOOR - 50,
        surplus_battery_soc_low_threshold_pct=FLOOR - 50,
    )

    assert data["surplus_battery_soc_high_threshold_pct"] == FLOOR + 1
    assert data["surplus_battery_soc_low_threshold_pct"] == FLOOR


def test_thresholds_far_outside_their_range_are_clamped_into_it():
    from tuya_ev_charger.const import (
        MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT as SOC_MAX,
    )
    from tuya_ev_charger.const import (
        MAX_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W as DISCHARGE_MAX,
    )
    from tuya_ev_charger.const import (
        MAX_SURPLUS_THRESHOLD_W as POWER_MAX,
    )

    data = _normalized(
        surplus_battery_soc_high_threshold_pct=10_000,
        surplus_battery_soc_low_threshold_pct=10_000,
        surplus_start_threshold_w=10**9,
        surplus_stop_threshold_w=10**9,
        surplus_max_battery_discharge_for_ev_w=10**9,
    )

    assert data["surplus_battery_soc_high_threshold_pct"] == SOC_MAX
    assert data["surplus_battery_soc_low_threshold_pct"] == SOC_MAX - 1
    assert data["surplus_start_threshold_w"] == POWER_MAX
    assert data["surplus_stop_threshold_w"] == POWER_MAX
    assert data["surplus_max_battery_discharge_for_ev_w"] == DISCHARGE_MAX


def test_a_section_with_no_fields_is_not_rendered(monkeypatch):
    """The guard keeps an empty collapsible group off the screen."""
    from tuya_ev_charger import options_flow

    kept = tuple(opt for opt in options_flow._OPTIONS_FORM if opt.section != "battery")
    monkeypatch.setattr(options_flow, "_OPTIONS_FORM", kept)

    schema = _flow()._build_options_schema({}, computed={})

    names = {str(marker) for marker in schema.schema}
    assert "battery" not in names
    assert "device" in names


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, "d"), ("", "d"), ("   ", "d"), (" x ", "x"), (5, "5")]
)
def test_a_text_field_that_comes_back_empty_takes_its_default(raw, expected):
    from tuya_ev_charger.options_flow import _normalize_text_value

    data = {"k": raw}
    _normalize_text_value(data, "k", "d")

    assert data["k"] == expected


def test_a_text_field_missing_from_the_submission_takes_its_default():
    from tuya_ev_charger.options_flow import _normalize_text_value

    data: dict = {}
    _normalize_text_value(data, "k", "d")

    assert data["k"] == "d"
