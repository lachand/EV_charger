"""Disabling entities the hardware cannot populate, exactly once.

The rule that matters: an entity is touched at most once, ever, and a user's own
choice always wins. Home Assistant records who disabled an entity but has no
"enabled by user" flag, so re-applying the policy on every start would silently
undo the user's choices -- hence the marker this module writes into the registry
entry's options.

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import asyncio
import types


class _Registry:
    def __init__(self):
        self.option_updates: list[tuple] = []
        self.entity_updates: list[tuple] = []

    def async_update_entity_options(self, entity_id, domain, options):
        self.option_updates.append((entity_id, domain, options))

    def async_update_entity(self, entity_id, **kwargs):
        self.entity_updates.append((entity_id, kwargs))


def _entity(key, *, disabled=None, handled=False, options=None):
    return types.SimpleNamespace(
        entity_id=f"sensor.charger_{key}",
        unique_id=f"dev_{key}",
        disabled_by=disabled,
        options=options
        if options is not None
        else ({"tuya_ev_charger": {"auto_disabled": True}} if handled else {}),
    )


def _run(monkeypatch, entities, keys):
    from tuya_ev_charger import entity_cleanup as module

    registry = _Registry()
    monkeypatch.setattr(module.er, "async_get", lambda hass: registry, raising=False)
    monkeypatch.setattr(
        module.er, "async_entries_for_config_entry", lambda reg, entry_id: entities, raising=False
    )
    count = asyncio.run(module.async_disable_entities(None, "e1", keys, reason="lacks"))
    return count, registry


def test_nothing_to_disable_touches_nothing(monkeypatch):
    count, registry = _run(monkeypatch, [_entity("voltage_l3")], set())

    assert count == 0
    assert registry.entity_updates == []


def test_a_matching_entity_is_disabled_and_marked(monkeypatch):
    from homeassistant.helpers import entity_registry as er

    count, registry = _run(monkeypatch, [_entity("voltage_l3")], {"voltage_l3"})

    assert count == 1
    assert registry.entity_updates == [
        ("sensor.charger_voltage_l3", {"disabled_by": er.RegistryEntryDisabler.INTEGRATION})
    ]
    ((_, domain, options),) = registry.option_updates
    assert domain == "tuya_ev_charger"
    assert options["auto_disabled"] is True


def test_an_entity_is_never_touched_twice(monkeypatch):
    count, registry = _run(monkeypatch, [_entity("voltage_l3", handled=True)], {"voltage_l3"})

    assert count == 0
    assert registry.entity_updates == []
    assert registry.option_updates == []


def test_a_users_own_choice_always_wins(monkeypatch):
    from homeassistant.helpers import entity_registry as er

    user_disabled = _entity("voltage_l3", disabled=er.RegistryEntryDisabler.USER)

    count, registry = _run(monkeypatch, [user_disabled], {"voltage_l3"})

    assert count == 0
    assert registry.entity_updates == []


def test_an_entity_already_disabled_by_something_else_is_marked_but_not_recounted(monkeypatch):
    from homeassistant.helpers import entity_registry as er

    other = _entity("voltage_l3", disabled=er.RegistryEntryDisabler.CONFIG_ENTRY)

    count, registry = _run(monkeypatch, [other], {"voltage_l3"})

    assert count == 0
    assert registry.entity_updates == []
    assert len(registry.option_updates) == 1


def test_only_entities_whose_id_ends_with_the_key_match(monkeypatch):
    entities = [_entity("voltage_l3"), _entity("voltage_l1"), _entity("power_l3")]

    count, registry = _run(monkeypatch, entities, {"voltage_l3"})

    assert count == 1
    assert [u[0] for u in registry.entity_updates] == ["sensor.charger_voltage_l3"]


def test_existing_entity_options_are_preserved_when_marking(monkeypatch):
    entity = _entity("voltage_l3", options={"tuya_ev_charger": {"other": 1}, "sensor": {"x": 2}})

    _, registry = _run(monkeypatch, [entity], {"voltage_l3"})

    ((_, _, options),) = registry.option_updates
    assert options == {"other": 1, "auto_disabled": True}


# --- which capabilities a charger lacks -------------------------------------------


def _metrics(*, phases=("L1",), plug_in="idle", nfc=True):
    return types.SimpleNamespace(
        phases={name: object() for name in phases}, plug_in_action=plug_in, nfc_enabled=nfc
    )


def test_a_three_phase_charger_with_every_feature_lacks_nothing():
    from tuya_ev_charger.entity_cleanup import unavailable_capability_keys

    assert unavailable_capability_keys(_metrics(phases=("L1", "L2", "L3"))) == set()


def test_a_single_phase_charger_lacks_the_other_two_phases():
    from tuya_ev_charger.entity_cleanup import unavailable_capability_keys

    keys = unavailable_capability_keys(_metrics())

    assert keys == {
        "voltage_l2",
        "current_l2",
        "power_l2",
        "voltage_l3",
        "current_l3",
        "power_l3",
    }


def test_features_the_firmware_never_reports_are_listed():
    from tuya_ev_charger.entity_cleanup import unavailable_capability_keys

    keys = unavailable_capability_keys(_metrics(phases=("L1", "L2", "L3"), plug_in=None, nfc=None))

    assert keys == {"plug_in_action", "nfc_enabled"}


def test_no_reading_yet_means_nothing_is_concluded():
    from tuya_ev_charger.entity_cleanup import unavailable_capability_keys

    assert unavailable_capability_keys(None) == set()
