"""The surplus-regulation-active binary sensor.

It mirrors the controller snapshot and re-renders on the controller's own update
callback, so it reflects a decision the instant it is taken rather than at the
next poll.
"""

from __future__ import annotations

import types


def _sensor(*, controller=None):
    from tuya_ev_charger.binary_sensor import (
        TuyaEVChargerSurplusRegulationActiveBinarySensor as B,
    )

    entity = B.__new__(B)
    entity._runtime_data = types.SimpleNamespace(
        solar_surplus_controller=controller,
        client=types.SimpleNamespace(device_id="bf23"),
    )
    entity._entry = types.SimpleNamespace(title="t", entry_id="e1")
    entity._card_role = None
    entity._card_index = None
    return entity


def _snapshot(**kwargs):
    base = {
        "regulation_active": False,
        "mode_enabled": False,
        "paused": False,
        "force_charge_active": False,
        "last_decision_reason": "startup",
    }
    base.update(kwargs)
    return types.SimpleNamespace(**base)


def test_it_reflects_the_controller_snapshot():
    controller = types.SimpleNamespace(snapshot=_snapshot(regulation_active=True))
    assert _sensor(controller=controller).is_on is True


def test_it_is_off_without_a_controller():
    assert _sensor(controller=None).is_on is False


def test_the_attributes_expose_the_decision_state():
    controller = types.SimpleNamespace(
        snapshot=_snapshot(mode_enabled=True, last_decision_reason="surplus_start")
    )
    attrs = _sensor(controller=controller).extra_state_attributes
    assert attrs["mode_enabled"] is True
    assert attrs["last_decision_reason"] == "surplus_start"


def test_it_subscribes_and_unsubscribes_to_the_controller():
    """The subscription is what makes it update on a decision, not a poll; the
    unsubscribe is what stops a leak when the entity is removed."""
    import asyncio

    unsubscribed = []
    controller = types.SimpleNamespace(
        snapshot=_snapshot(),
        async_add_update_listener=lambda cb: lambda: unsubscribed.append(1),
    )
    entity = _sensor(controller=controller)
    entity.async_write_ha_state = lambda: None

    async def _super():
        return None

    # Bypass the HA Entity base methods the stub does not provide.
    import tuya_ev_charger.binary_sensor as mod

    monkey = mod.TuyaEVChargerEntity
    orig_added = getattr(monkey, "async_added_to_hass", None)
    orig_remove = getattr(monkey, "async_will_remove_from_hass", None)
    monkey.async_added_to_hass = lambda self: _super()
    monkey.async_will_remove_from_hass = lambda self: _super()
    try:
        asyncio.run(entity.async_added_to_hass())
        assert entity._unsub_listener is not None
        asyncio.run(entity.async_will_remove_from_hass())
        assert unsubscribed == [1]
    finally:
        if orig_added:
            monkey.async_added_to_hass = orig_added
        if orig_remove:
            monkey.async_will_remove_from_hass = orig_remove
