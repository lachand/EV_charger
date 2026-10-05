"""The shared readers for loosely typed values.

Four modules each had their own copy of these. They were merged only where the
copies were equivalent, so these tests pin the behaviour all of them relied on --
in particular the deliberate difference between `option_bool` (a user's setting,
"yes" counts, anything unrecognised is truthy) and `coerce_optional_bool` (a
sensor or DP reading, where unknown must stay distinct from off).

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (5, 5),
        ("5", 5),
        (5.9, 5),
        (True, 1),
        ("x", 7),
        (None, 7),
        ([1], 7),
        ({}, 7),
        (-10, 1),
        (10_000, 100),
    ],
)
def test_an_int_option_is_clamped_and_never_raises(raw, expected):
    from tuya_ev_charger.option_values import option_int

    assert option_int({"k": raw}, "k", 7, 1, 100) == expected


def test_a_missing_int_option_takes_the_default_and_the_default_is_clamped_too():
    from tuya_ev_charger.option_values import option_int

    assert option_int({}, "k", 7, 1, 100) == 7
    assert option_int({}, "k", 500, 1, 100) == 100


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        ("yes", True),
        ("YES", True),
        (" on ", True),
        ("1", True),
        ("no", False),
        ("off", False),
        ("0", False),
        (1, True),
        (0, False),
        # Unrecognised values fall back to Python truthiness: a long-standing
        # quirk, pinned so that merging the copies could not change it quietly.
        ("maybe", True),
        ("", False),
        (None, False),
    ],
)
def test_a_bool_option_reads_the_usual_spellings(raw, expected):
    from tuya_ev_charger.option_values import option_bool

    assert option_bool({"k": raw}, "k", False) is expected


def test_a_missing_bool_option_takes_the_default():
    from tuya_ev_charger.option_values import option_bool

    assert option_bool({}, "k", True) is True
    assert option_bool({}, "k", False) is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(5, 5), ("5", 5), (5.9, 5), (True, 1), ("x", None), (None, None), ([], None), ("", None)],
)
def test_an_optional_int_is_a_number_or_none(raw, expected):
    from tuya_ev_charger.option_values import coerce_optional_int

    assert coerce_optional_int(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        (2, True),
        ("ON", True),
        (" true ", True),
        ("off", False),
        ("0", False),
        # Not "yes"/"no": a reading is either clearly on, clearly off, or unknown.
        ("yes", None),
        ("no", None),
        ("maybe", None),
        ("", None),
        (None, None),
        (1.5, None),
    ],
)
def test_a_reading_is_on_off_or_unknown(raw, expected):
    from tuya_ev_charger.option_values import coerce_optional_bool

    assert coerce_optional_bool(raw) is expected


def test_the_two_boolean_readers_differ_on_yes_on_purpose():
    from tuya_ev_charger.option_values import coerce_optional_bool, option_bool

    assert option_bool({"k": "yes"}, "k", False) is True
    assert coerce_optional_bool("yes") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("None", ""),
        (" none ", ""),
        ("sensor.grid", "sensor.grid"),
        ("  sensor.grid ", "sensor.grid"),
        (5, "5"),
    ],
)
def test_a_cleared_picker_reads_as_unset_whichever_way_it_comes_back(raw, expected):
    from tuya_ev_charger.option_values import clean_optional_text

    assert clean_optional_text(raw) == expected


def test_the_three_former_copies_agree_with_the_shared_reader():
    """`_option_str`, `_option_entity` and the in-place normaliser were one rule."""
    from tuya_ev_charger.options_flow import _normalize_optional_entity_value, _option_entity
    from tuya_ev_charger.surplus_settings import _option_str

    for raw in (None, "", " ", "None", "none", "sensor.a", " sensor.a "):
        data = {"k": raw}
        _normalize_optional_entity_value(data, "k")
        assert _option_str(data and {"k": raw}, "k", "d") == data["k"]
        assert (_option_entity({"k": raw}, "k", "d") or "") == data["k"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0.16, 0.16),
        ("0.27", 0.27),
        (-0.05, -0.05),
        ("-3", -3.0),
        (0, 0.0),
        ("0,27", 0.0),
        ("", 0.0),
        (None, 0.0),
        ([], 0.0),
    ],
)
def test_a_float_option_keeps_its_sign_and_reads_junk_as_the_default(raw, expected):
    from tuya_ev_charger.option_values import option_float

    assert option_float({"k": raw}, "k") == expected


def test_a_missing_float_option_takes_the_given_default():
    from tuya_ev_charger.option_values import option_float

    assert option_float({}, "k", 2.5) == 2.5
    assert option_float({"k": "x"}, "k", 2.5) == 2.5


@pytest.mark.parametrize(
    ("raw", "expected"), [("  x ", "x"), ("x", "x"), (None, "d"), (5, "5"), ("", "")]
)
def test_a_text_option_is_trimmed_and_defaults_only_when_none(raw, expected):
    from tuya_ev_charger.option_values import option_text

    assert option_text({"k": raw}, "k", "d") == expected
