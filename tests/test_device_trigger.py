"""Device triggers.

Two things can go wrong here and neither raises: picking the `evcc_status`
sensor instead of `status` (the automation would then wait for a state that
never comes), and getting a transition backwards.
"""

from __future__ import annotations

import asyncio
import types

import pytest


class _RegistryEntry:
    def __init__(self, entity_id, unique_id, domain="sensor", platform="tuya_ev_charger"):
        self.entity_id = entity_id
        self.unique_id = unique_id
        self.domain = domain
        self.platform = platform


def _with_entities(monkeypatch, entries):
    from tuya_ev_charger import device_trigger

    monkeypatch.setattr(
        device_trigger.er, "async_get", lambda hass: None, raising=False
    )
    monkeypatch.setattr(
        device_trigger.er,
        "async_entries_for_device",
        lambda registry, device_id: entries,
        raising=False,
    )


_CHARGER_ENTITIES = [
    _RegistryEntry("sensor.charger_evcc_status", "bf23_evcc_status"),
    _RegistryEntry("sensor.charger_work_state", "bf23_work_state"),
    _RegistryEntry("sensor.charger_status", "bf23_status"),
    _RegistryEntry("switch.charger_charge_session", "bf23_charge_session", domain="switch"),
]


def test_the_status_sensor_is_found_and_evcc_status_is_not(monkeypatch):
    """Both unique_ids end in `_status`; picking the wrong one waits forever."""
    from tuya_ev_charger.device_trigger import _status_entity_id

    _with_entities(monkeypatch, _CHARGER_ENTITIES)
    assert _status_entity_id(None, "dev") == "sensor.charger_status"


def test_a_renamed_entity_is_still_found(monkeypatch):
    """Matching is on unique_id, which the user cannot rename."""
    from tuya_ev_charger.device_trigger import _status_entity_id

    _with_entities(
        monkeypatch,
        [_RegistryEntry("sensor.borne_de_recharge_etat", "bf23_status")],
    )
    assert _status_entity_id(None, "dev") == "sensor.borne_de_recharge_etat"


def test_another_integrations_status_sensor_is_ignored(monkeypatch):
    """Devices can carry entities from more than one integration."""
    from tuya_ev_charger.device_trigger import _status_entity_id

    _with_entities(
        monkeypatch,
        [_RegistryEntry("sensor.other_status", "x_status", platform="other")],
    )
    assert _status_entity_id(None, "dev") is None


def test_no_triggers_without_a_status_sensor(monkeypatch):
    """Better an empty list than triggers pointing at nothing."""
    from tuya_ev_charger.device_trigger import async_get_triggers

    _with_entities(monkeypatch, [])
    assert asyncio.run(async_get_triggers(None, "dev")) == []


def test_every_trigger_is_offered_for_the_device(monkeypatch):
    from tuya_ev_charger.device_trigger import TRIGGER_TRANSITIONS, async_get_triggers

    _with_entities(monkeypatch, _CHARGER_ENTITIES)
    triggers = asyncio.run(async_get_triggers(None, "dev"))

    assert {trigger["type"] for trigger in triggers} == set(TRIGGER_TRANSITIONS)
    assert {trigger["entity_id"] for trigger in triggers} == {"sensor.charger_status"}
    assert {trigger["domain"] for trigger in triggers} == {"tuya_ev_charger"}


@pytest.mark.parametrize(
    ("trigger_type", "expected"),
    [
        ("charge_started", (None, "charging")),
        ("charge_complete", (None, "charged")),
        ("fault", (None, "fault")),
        ("plugged_in", (None, "plugged_in")),
        # The transition that justifies the module: unplugging mid-charge is not
        # a state, and charging -> charged is a normal finish, not an unplug.
        ("unplugged_while_charging", ("charging", "idle")),
    ],
)
def test_transitions(trigger_type, expected):
    from tuya_ev_charger.device_trigger import TRIGGER_TRANSITIONS

    assert TRIGGER_TRANSITIONS[trigger_type] == expected


def test_every_trigger_targets_a_real_status_value():
    """A typo would produce a trigger that silently never fires."""
    from tuya_ev_charger.device_trigger import TRIGGER_TRANSITIONS
    from tuya_ev_charger.tuya_ev_charger import STATUS_OPTIONS

    for from_state, to_state in TRIGGER_TRANSITIONS.values():
        assert to_state in STATUS_OPTIONS
        assert from_state is None or from_state in STATUS_OPTIONS


def test_every_trigger_has_a_translated_name():
    """An untranslated trigger shows up as a raw key in the automation editor."""
    import json
    import pathlib

    from tuya_ev_charger.device_trigger import TRIGGER_TRANSITIONS

    root = pathlib.Path(__file__).resolve().parents[1] / "custom_components/tuya_ev_charger"
    for name in ("strings.json", "translations/en.json", "translations/fr.json"):
        payload = json.loads((root / name).read_text(encoding="utf-8"))
        names = payload["device_automation"]["trigger_type"]
        assert set(names) == set(TRIGGER_TRANSITIONS), name


def test_attaching_builds_the_right_state_trigger(monkeypatch):
    from tuya_ev_charger import device_trigger

    captured = {}

    async def _validate(hass, config):
        return config

    async def _attach(hass, config, action, info, *, platform_type):
        captured.update(config)
        captured["platform_type"] = platform_type
        return lambda: None

    monkeypatch.setattr(
        device_trigger.state_trigger, "async_validate_trigger_config", _validate,
        raising=False,
    )
    monkeypatch.setattr(
        device_trigger.state_trigger, "async_attach_trigger", _attach, raising=False
    )
    _with_entities(monkeypatch, _CHARGER_ENTITIES)

    asyncio.run(
        device_trigger.async_attach_trigger(
            None,
            {"device_id": "dev", "type": "unplugged_while_charging"},
            action=None,
            trigger_info=types.SimpleNamespace(),
        )
    )

    assert captured["entity_id"] == "sensor.charger_status"
    assert captured["from"] == "charging"
    assert captured["to"] == "idle"
    assert captured["platform_type"] == "device"


def test_a_trigger_without_a_from_state_omits_it(monkeypatch):
    """Sending from=None would only fire on a transition out of nothing."""
    from tuya_ev_charger import device_trigger

    captured = {}

    async def _validate(hass, config):
        return config

    async def _attach(hass, config, action, info, *, platform_type):
        captured.update(config)
        return lambda: None

    monkeypatch.setattr(
        device_trigger.state_trigger, "async_validate_trigger_config", _validate,
        raising=False,
    )
    monkeypatch.setattr(
        device_trigger.state_trigger, "async_attach_trigger", _attach, raising=False
    )
    _with_entities(monkeypatch, _CHARGER_ENTITIES)

    asyncio.run(
        device_trigger.async_attach_trigger(
            None,
            {"device_id": "dev", "type": "charge_complete"},
            action=None,
            trigger_info=types.SimpleNamespace(),
        )
    )

    assert "from" not in captured
    assert captured["to"] == "charged"
