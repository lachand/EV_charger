"""Custom DP mapping validation.

Before this, an invalid mapping was accepted by the form, logged at warning
level, and silently replaced by the default profile. The user saw a saved
dialog, then a charger reporting nothing, with no connection between the two.
"""

from __future__ import annotations

import json

import pytest


def _check(payload):
    from tuya_ev_charger.tuya_ev_charger import validate_custom_dp_profile

    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return validate_custom_dp_profile(raw)


def _valid_mapping():
    from tuya_ev_charger.tuya_ev_charger import known_dp_profile_fields

    return {name: str(100 + index) for index, name in enumerate(known_dp_profile_fields())}


def test_a_complete_valid_mapping_passes():
    assert _check(_valid_mapping()) is None


def test_a_partial_mapping_passes():
    """Unset fields fall back to the default profile, which is intended."""
    from tuya_ev_charger.tuya_ev_charger import known_dp_profile_fields

    first = known_dp_profile_fields()[0]
    assert _check({first: "101"}) is None


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_an_empty_mapping_is_not_an_error(blank):
    """Empty means "no custom mapping", which is the default state."""
    from tuya_ev_charger.tuya_ev_charger import validate_custom_dp_profile

    assert validate_custom_dp_profile(blank) is None


def test_malformed_json_says_so():
    problem = _check("{not json")
    assert problem is not None
    assert "JSON" in problem


def test_a_json_list_is_rejected():
    problem = _check("[1, 2, 3]")
    assert problem is not None
    assert "object" in problem


def test_an_unknown_field_is_named():
    """A typo would otherwise be dropped in silence."""
    problem = _check({"work_stat": "112"})
    assert problem is not None
    assert "work_stat" in problem


def test_an_empty_value_is_named():
    from tuya_ev_charger.tuya_ev_charger import known_dp_profile_fields

    first = known_dp_profile_fields()[0]
    problem = _check({first: "  "})
    assert problem is not None
    assert first in problem


def test_two_fields_on_the_same_dp_are_rejected():
    """Always a copy-paste mistake, and it produces wrong readings, not errors."""
    from tuya_ev_charger.tuya_ev_charger import known_dp_profile_fields

    first, second = known_dp_profile_fields()[:2]
    problem = _check({first: "101", second: "101"})
    assert problem is not None
    assert "101" in problem
    assert first in problem and second in problem


def test_a_valid_mapping_actually_resolves_to_that_profile():
    """Validation and parsing must agree, or the form would accept and drop."""
    from tuya_ev_charger.const import CHARGER_PROFILE_CUSTOM_JSON
    from tuya_ev_charger.tuya_ev_charger import _resolve_profile

    mapping = _valid_mapping()
    assert _check(mapping) is None

    name, profile = _resolve_profile(CHARGER_PROFILE_CUSTOM_JSON, json.dumps(mapping))
    assert name == CHARGER_PROFILE_CUSTOM_JSON
    for field, dp in mapping.items():
        assert getattr(profile, field) == dp
