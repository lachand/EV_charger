"""Surplus quick profiles.

`apply_surplus_profile` rewrites the config entry's options, which is the only
function in the integration that changes a user's stored settings — and it had no
tests. A mistake here silently reconfigures someone's installation.
"""

from __future__ import annotations

import pytest


def test_the_three_profiles_all_have_a_preset():
    """A profile offered in the UI but missing a preset raises KeyError on apply."""
    from tuya_ev_charger.const import SURPLUS_PROFILES
    from tuya_ev_charger.surplus_profiles import SURPLUS_PROFILE_PRESETS

    assert set(SURPLUS_PROFILES) == set(SURPLUS_PROFILE_PRESETS)


@pytest.mark.parametrize("profile", ["eco", "balanced", "fast"])
def test_applying_a_profile_sets_every_field_it_owns(profile):
    from tuya_ev_charger.surplus_profiles import SURPLUS_PROFILE_PRESETS, apply_surplus_profile

    result = apply_surplus_profile({}, profile)
    preset = SURPLUS_PROFILE_PRESETS[profile]

    assert result["surplus_profile"] == profile
    assert result["surplus_start_threshold_w"] == preset.start_threshold_w
    assert result["surplus_stop_threshold_w"] == preset.stop_threshold_w
    assert result["surplus_adjust_up_cooldown_s"] == preset.adjust_up_cooldown_s
    assert result["surplus_adjust_down_cooldown_s"] == preset.adjust_down_cooldown_s


@pytest.mark.parametrize("profile", ["eco", "balanced", "fast"])
def test_the_stop_threshold_never_exceeds_the_start_threshold(profile):
    """Inverted thresholds would start a charge and immediately stop it."""
    from tuya_ev_charger.surplus_profiles import SURPLUS_PROFILE_PRESETS

    preset = SURPLUS_PROFILE_PRESETS[profile]
    assert preset.stop_threshold_w <= preset.start_threshold_w


def test_the_profiles_are_ordered_from_cautious_to_eager():
    """`eco` must be the hardest to trigger and `fast` the easiest, otherwise the
    names mislead."""
    from tuya_ev_charger.surplus_profiles import SURPLUS_PROFILE_PRESETS as presets

    assert (
        presets["eco"].start_threshold_w
        > presets["balanced"].start_threshold_w
        > presets["fast"].start_threshold_w
    )
    # And eco must react most slowly upward.
    assert (
        presets["eco"].adjust_up_cooldown_s
        > presets["balanced"].adjust_up_cooldown_s
        > presets["fast"].adjust_up_cooldown_s
    )


def test_eco_gives_the_battery_no_budget_for_the_car():
    """That is what makes it eco: solar only, never the house battery."""
    from tuya_ev_charger.surplus_profiles import SURPLUS_PROFILE_PRESETS

    assert SURPLUS_PROFILE_PRESETS["eco"].max_battery_discharge_for_ev_w == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("eco", "eco"),
        ("FAST", "fast"),
        ("  balanced  ", "balanced"),
        # Names used by older releases, kept working so an existing entry does
        # not silently fall back to the default.
        ("aggressive", "fast"),
        ("conservative", "eco"),
        # Anything else is the default rather than an error.
        ("nonsense", "balanced"),
        ("", "balanced"),
        (None, "balanced"),
    ],
)
def test_normalisation(raw, expected):
    from tuya_ev_charger.surplus_profiles import normalize_surplus_profile

    assert normalize_surplus_profile(raw) == expected


@pytest.mark.parametrize(
    ("raw", "supported"),
    [
        ("eco", True),
        ("AGGRESSIVE", True),
        ("nonsense", False),
        ("", False),
        (None, False),
    ],
)
def test_support_check_accepts_legacy_names_but_rejects_junk(raw, supported):
    """The service validates with this before applying, so it must not accept a
    name that normalisation would silently turn into the default."""
    from tuya_ev_charger.surplus_profiles import is_supported_surplus_profile

    assert is_supported_surplus_profile(raw) is supported


def test_a_legacy_name_applies_the_profile_it_maps_to():
    from tuya_ev_charger.surplus_profiles import SURPLUS_PROFILE_PRESETS, apply_surplus_profile

    result = apply_surplus_profile({}, "conservative")
    assert result["surplus_profile"] == "eco"
    assert result["surplus_start_threshold_w"] == SURPLUS_PROFILE_PRESETS["eco"].start_threshold_w


def test_unrelated_options_are_preserved():
    """Switching profile must not wipe the user's sensors or protection limits."""
    from tuya_ev_charger.surplus_profiles import apply_surplus_profile

    options = {
        "surplus_sensor_entity_id": "sensor.grid",
        "max_inverter_power_w": 5500,
        "vehicles": "Zoe, Kangoo",
    }
    result = apply_surplus_profile(dict(options), "fast")
    for key, value in options.items():
        assert result[key] == value


def test_apply_mutates_in_place_so_callers_must_pass_a_copy():
    """Documenting a trap rather than a feature.

    The function edits the dict it is given *and* returns it. The one caller
    passes `dict(entry.options)`, so it is correct today — but a future caller
    handing over `entry.options` directly would mutate the live config entry
    without going through `async_update_entry`.
    """
    from tuya_ev_charger.surplus_profiles import apply_surplus_profile

    original = {"surplus_start_threshold_w": 999}
    returned = apply_surplus_profile(original, "fast")

    assert returned is original, "still mutating in place; callers must copy first"
    assert original["surplus_start_threshold_w"] != 999
