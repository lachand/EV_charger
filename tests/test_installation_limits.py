"""Circuit rating vs charger rating (issue #21).

A 32 A charger on a 25 A breaker must never be offered 32 A, by the number
entity or by surplus regulation. Exceeding it does not fail cleanly: the breaker
trips after a long session, long after the cause.

`allowed_currents()` is the single funnel both paths go through, so these tests
cover both.
"""

from __future__ import annotations

import pytest


class _Metrics:
    """Only the two fields allowed_currents() reads."""

    def __init__(self, max_current_cfg=32, adjust_current_options=None):
        self.max_current_cfg = max_current_cfg
        self.adjust_current_options = adjust_current_options or []


def _currents(options=None, **metrics):
    from tuya_ev_charger.helpers import allowed_currents

    return allowed_currents(_Metrics(**metrics), options or {})


def test_without_a_limit_the_full_range_is_offered():
    assert _currents() == tuple(range(6, 33))


def test_the_ceiling_caps_the_offered_currents():
    """The reporter's case: a 32 A charger on a circuit good for 20 A."""
    limited = _currents({"max_charge_current_a": 20})
    assert max(limited) == 20
    assert 32 not in limited


def test_the_ceiling_cannot_raise_the_charger_maximum():
    """A limit is a limit: 40 A on a 16 A charger must not offer 40 A."""
    limited = _currents({"max_charge_current_a": 40}, max_current_cfg=16)
    assert max(limited) == 16


def test_the_floor_raises_the_minimum():
    """Some cars refuse to start below 8 A."""
    limited = _currents({"min_charge_current_a": 8})
    assert min(limited) == 8
    assert 6 not in limited


def test_both_limits_together():
    assert _currents({"min_charge_current_a": 8, "max_charge_current_a": 20}) == tuple(
        range(8, 21)
    )


@pytest.mark.parametrize("value", [0, None, "", "abc", -5])
def test_a_missing_or_junk_limit_disables_it(value):
    """0 means "no override"; nothing typed by hand may narrow the range wrongly."""
    assert _currents({"max_charge_current_a": value}) == tuple(range(6, 33))


def test_an_impossible_ceiling_still_offers_one_current():
    """An empty tuple reads as "this charger reports no currents" and kills the
    entity, which is a worse failure than offering the lowest step."""
    limited = _currents({"max_charge_current_a": 3})
    assert limited == (6,)


def test_an_impossible_floor_still_offers_one_current():
    limited = _currents({"min_charge_current_a": 32}, max_current_cfg=16)
    assert limited == (16,)


def test_limits_apply_to_the_discrete_ladder_too():
    """With continuous mode off, the advertised steps are filtered as well."""
    limited = _currents(
        {"continuous_current": False, "max_charge_current_a": 13},
        adjust_current_options=[6, 8, 10, 13, 16],
    )
    assert limited == (6, 8, 10, 13)


def test_the_surplus_regulator_cannot_exceed_the_limit():
    """The reporter's second point: their workaround could not stop this path.

    The regulator picks from the same tuple, so a surplus large enough for 32 A
    still resolves to the capped ceiling.
    """
    from tuya_ev_charger.surplus_decision import current_supported_by

    available = _currents({"max_charge_current_a": 20})
    huge_surplus_w = 32 * 230

    assert current_supported_by(huge_surplus_w, available) == 20
