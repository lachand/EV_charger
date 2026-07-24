"""The charge-current number entity.

This is the write path: it makes the charger beep and can interrupt a running
charge, and it is the entity a user or an external controller drives directly.
It had no tests.
"""

from __future__ import annotations

import asyncio
import types

import pytest


class _Client:
    def __init__(self, *, ok=True):
        self.calls: list[tuple[int, object]] = []
        self._ok = ok

    async def async_set_charge_current(self, amperage, max_current=None):
        self.calls.append((amperage, max_current))
        return self._ok


def _metrics(*, current_target=10, max_current=32, options=None):
    return types.SimpleNamespace(
        current_target=current_target,
        max_current_cfg=max_current,
        adjust_current_options=options or [],
    )


def _entity(*, data=None, options=None, client=None):
    from tuya_ev_charger.number import TuyaEVChargerCurrentNumber

    entity = TuyaEVChargerCurrentNumber.__new__(TuyaEVChargerCurrentNumber)
    refreshes: list[int] = []

    coordinator = types.SimpleNamespace(data=data)

    async def _refresh():
        refreshes.append(1)

    coordinator.async_request_refresh = _refresh
    entity._entry = types.SimpleNamespace(options=options or {})
    entity._runtime_data = types.SimpleNamespace(client=client or _Client())
    entity.coordinator = coordinator
    entity.refreshes = refreshes
    return entity


def _set(entity, value):
    asyncio.run(entity.async_set_native_value(value))


# --- bounds come from the shared ladder ------------------------------------


def test_bounds_follow_the_charger_maximum():
    entity = _entity(data=_metrics(max_current=16))
    assert entity.native_min_value == 6.0
    assert entity.native_max_value == 16.0


def test_bounds_respect_the_installation_limit():
    """The 2.12.0 cap must bound the entity, not just surplus regulation."""
    entity = _entity(data=_metrics(max_current=32), options={"max_charge_current_a": 20})
    assert entity.native_max_value == 20.0


def test_bounds_respect_the_minimum_limit():
    entity = _entity(data=_metrics(max_current=32), options={"min_charge_current_a": 8})
    assert entity.native_min_value == 8.0


def test_the_allowed_list_is_exposed_as_an_attribute():
    entity = _entity(data=_metrics(max_current=10))
    entity._attr_extra_state_attributes = {}
    entity._with_technical_attributes = lambda payload: payload
    assert entity.extra_state_attributes["allowed_currents"] == [6, 7, 8, 9, 10]


# --- writing --------------------------------------------------------------


def test_a_new_value_is_written_and_the_coordinator_refreshed():
    client = _Client()
    entity = _entity(data=_metrics(current_target=10), client=client)

    _set(entity, 16)
    assert client.calls == [(16, 32)]
    assert entity.refreshes == [1]


def test_writing_the_value_already_held_is_skipped():
    """Every DP write beeps, and controllers re-assert the same setpoint on a
    timer -- so an unchanged value must not reach the charger."""
    client = _Client()
    entity = _entity(data=_metrics(current_target=10), client=client)

    _set(entity, 10)
    assert client.calls == []
    assert entity.refreshes == []


def test_floats_are_rounded_rather_than_refused():
    """Automations routinely send floats; 10.4 is friendlier rounded than
    rejected."""
    client = _Client()
    entity = _entity(data=_metrics(current_target=6), client=client)

    _set(entity, 10.4)
    assert client.calls == [(10, 32)]


def test_a_value_above_the_installation_limit_is_refused():
    """The cap is a limit, so the entity must reject rather than silently clamp:
    a caller asking for 32 A on a 20 A circuit has a bug worth surfacing."""
    from tuya_ev_charger.number import HomeAssistantError

    client = _Client()
    entity = _entity(
        data=_metrics(current_target=10),
        options={"max_charge_current_a": 20},
        client=client,
    )

    with pytest.raises(HomeAssistantError, match="Unsupported current setpoint"):
        _set(entity, 32)
    assert client.calls == [], "nothing may reach the charger for a refused value"


def test_a_value_below_the_charger_minimum_is_refused():
    from tuya_ev_charger.number import HomeAssistantError

    entity = _entity(data=_metrics(current_target=10))
    with pytest.raises(HomeAssistantError):
        _set(entity, 3)


def test_a_failed_write_raises_and_does_not_refresh():
    """Silently swallowing the failure would leave the UI showing a setpoint the
    charger never accepted."""
    from tuya_ev_charger.number import HomeAssistantError

    entity = _entity(data=_metrics(current_target=10), client=_Client(ok=False))
    with pytest.raises(HomeAssistantError, match="Unable to update"):
        _set(entity, 16)
    assert entity.refreshes == []


def test_the_charger_maximum_is_passed_through_to_the_client():
    """The client needs it to raise the charger's own limit before writing."""
    client = _Client()
    entity = _entity(data=_metrics(current_target=6, max_current=16), client=client)

    _set(entity, 14)
    assert client.calls == [(14, 16)]


def test_a_write_still_works_before_the_first_poll():
    """With no data yet the entity must not crash; it writes without a maximum."""
    client = _Client()
    entity = _entity(data=None, client=client)

    _set(entity, 10)
    assert client.calls == [(10, None)]


def test_the_reported_value_tracks_the_charger():
    entity = _entity(data=_metrics(current_target=13))
    assert entity.native_value == 13.0

    entity.coordinator.data = _metrics(current_target=None)
    assert entity.native_value is None
