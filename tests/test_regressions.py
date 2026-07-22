"""Guards for failures that actually reached users.

Each test here maps to a released bug, so they are worth their weight even
though the suite is otherwise light.
"""

from __future__ import annotations

import json
import pathlib

import pytest
import voluptuous as vol

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT = REPO_ROOT / "custom_components" / "tuya_ev_charger"


class _FakeSelector:
    """Stands in for Home Assistant's EntitySelector.

    Rejects non-entity values the way cv.entity_id_or_uuid does, and exposes
    serialize() the way the frontend serialiser expects.
    """

    def serialize(self):
        return {"selector": {"entity": {"domain": ["sensor"]}}}

    def __call__(self, data):
        if not isinstance(data, str) or "." not in data:
            raise vol.Invalid(f"Entity {data} is neither a valid entity ID nor a UUID")
        return data


def test_optional_entity_schema_serialises():
    """2.0.1 shipped vol.Any(selector, None): valid, but unserialisable.

    Home Assistant converts the options schema to JSON to draw the form, so a
    schema it cannot serialise makes the dialog return 500 before it opens.
    """
    voluptuous_serialize = pytest.importorskip("voluptuous_serialize")

    def serializer(schema):
        if isinstance(schema, _FakeSelector):
            return schema.serialize()
        return voluptuous_serialize.UNSUPPORTED

    schema = vol.Schema(
        {vol.Optional("sensor", description={"suggested_value": ""}): _FakeSelector()}
    )
    # Must not raise.
    assert voluptuous_serialize.convert(schema, custom_serializer=serializer)


def test_optional_entity_left_empty_still_validates():
    """The bug the 2.0.1 change was trying to fix, kept covered."""
    schema = vol.Schema(
        {vol.Optional("sensor", description={"suggested_value": ""}): _FakeSelector()}
    )
    assert schema({}) == {}
    assert schema({"sensor": "sensor.grid"}) == {"sensor": "sensor.grid"}
    with pytest.raises(vol.Invalid):
        schema({"sensor": "not_an_entity"})


def test_options_form_declares_no_defaults_on_entity_pickers():
    """A voluptuous default is validated on insert, so None fails the selector."""
    source = (COMPONENT / "config_flow.py").read_text()
    options_step = source.split('step_id="init"')[1]
    assert "vol.Any(_sensor_selector" not in options_step
    assert options_step.count('description={"suggested_value"') == 5


def test_diagnostics_redacts_every_secret():
    """Diagnostics get attached to public issues; cloud creds are account-wide."""
    from tuya_ev_charger.diagnostics import TO_REDACT

    for secret in ("local_key", "cloud_api_key", "cloud_api_secret", "mac", "device_id"):
        assert secret in TO_REDACT, f"{secret} would be published in diagnostics"


def test_every_module_imports():
    """A constant imported but never defined breaks the whole integration."""
    import importlib

    for path in sorted(COMPONENT.glob("*.py")):
        if path.stem == "__init__":
            continue  # importing the package would need the full HA runtime
        importlib.import_module(f"tuya_ev_charger.{path.stem}")


def test_translation_files_are_valid_and_aligned():
    strings = json.loads((COMPONENT / "strings.json").read_text())
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    french = json.loads((COMPONENT / "translations" / "fr.json").read_text())

    # strings.json is Home Assistant's English source, not a second French copy:
    # any locale without its own file falls back to it.
    assert strings["entity"]["sensor"]["voltage_l1"]["name"] == "Voltage L1"
    assert set(english["entity"]["sensor"]) == set(french["entity"]["sensor"])


def test_fault_diagnosis_is_throttled():
    """Diagnosing costs a TCP connect and often a full read.

    2.2.0 ran it on every failed poll, so a charger that stayed broken was
    probed every 30 s forever. Only one diagnosis may happen per cooldown.
    """
    import asyncio
    import types

    from tuya_ev_charger.coordinator import TuyaEVChargerDataUpdateCoordinator
    from tuya_ev_charger.const import ConnectionFault

    coordinator = TuyaEVChargerDataUpdateCoordinator.__new__(
        TuyaEVChargerDataUpdateCoordinator
    )
    coordinator._last_fault = None
    coordinator._last_fault_at = 0.0
    calls: list[int] = []

    class _Client:
        async def async_classify_fault(self):
            calls.append(1)
            return ConnectionFault.REFUSED

    clock = {"now": 1000.0}
    coordinator.client = _Client()
    coordinator.hass = types.SimpleNamespace(
        loop=types.SimpleNamespace(time=lambda: clock["now"])
    )

    from tuya_ev_charger.const import FAULT_DIAGNOSIS_COOLDOWN_SECONDS

    start = clock["now"]

    # A 30 s poll interval, stopping short of the cooldown boundary.
    polls_inside = (FAULT_DIAGNOSIS_COOLDOWN_SECONDS // 30) - 1

    async def _run():
        first = await coordinator._async_classify_fault_throttled()
        for _ in range(polls_inside):
            clock["now"] += 30
            await coordinator._async_classify_fault_throttled()
        assert clock["now"] < start + FAULT_DIAGNOSIS_COOLDOWN_SECONDS
        return first

    verdict = asyncio.run(_run())
    assert verdict == ConnectionFault.REFUSED
    assert len(calls) == 1, f"diagnosed {len(calls)} times inside the cooldown"

    # Once the cooldown has elapsed it may diagnose again.
    clock["now"] = start + FAULT_DIAGNOSIS_COOLDOWN_SECONDS
    asyncio.run(coordinator._async_classify_fault_throttled())
    assert len(calls) == 2


def test_success_clears_the_cached_fault():
    """A stale verdict must not survive the charger coming back."""
    import types

    from tuya_ev_charger.coordinator import TuyaEVChargerDataUpdateCoordinator
    from tuya_ev_charger.const import ConnectionFault

    coordinator = TuyaEVChargerDataUpdateCoordinator.__new__(
        TuyaEVChargerDataUpdateCoordinator
    )
    coordinator._last_fault = ConnectionFault.REFUSED
    coordinator._last_fault_at = 1000.0
    coordinator.hass = types.SimpleNamespace()
    coordinator.entry = types.SimpleNamespace(entry_id="test")

    coordinator._async_note_success()
    assert coordinator._last_fault is None


def test_routine_poll_does_not_block_on_the_scan():
    """Listening for a broadcast takes seconds; the update loop must not wait.

    On a routine poll the relocation is scheduled in the background. The first
    refresh is the exception: setup rebuilds the client from the stored host on
    every retry, so an in-memory fix from a background task would be lost.
    """
    import asyncio
    import types

    from tuya_ev_charger.coordinator import TuyaEVChargerDataUpdateCoordinator

    def _make(data):
        c = TuyaEVChargerDataUpdateCoordinator.__new__(
            TuyaEVChargerDataUpdateCoordinator
        )
        c._last_rediscovery_at = 0.0
        c._relocating = None
        c.data = data
        c.hass = types.SimpleNamespace(
            loop=types.SimpleNamespace(time=lambda: 100_000.0)
        )
        c.scheduled = False
        c._schedule_relocation = lambda: setattr(c, "scheduled", True)
        return c

    # Routine poll: scheduled, and the caller is not made to wait.
    routine = _make(object())
    assert asyncio.run(routine._async_try_rediscover_host()) is False
    assert routine.scheduled is True

    # First refresh: resolved inline instead.
    first = _make(None)
    first._async_relocate = lambda: _returns(True)
    assert asyncio.run(first._async_try_rediscover_host()) is True
    assert first.scheduled is False


async def _returns(value):
    return value
