"""Services: registered up front, and every failure is a translated error.

The services used to be registered from `async_setup_entry`, so they did not exist
until an entry was configured and went away with the last one. They are now
registered from `async_setup`; each handler finds its entry at call time and says
why it cannot when it does not. The handlers are closures, so the tests capture
them through a fake `hass.services` and call them the way Home Assistant would.

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import ast
import asyncio
import json
import pathlib
import re
import types

import pytest

COMPONENT = pathlib.Path(__file__).resolve().parents[1] / "custom_components/tuya_ev_charger"


class _Hass:
    def __init__(self, entries=()):
        self.registered: dict[str, object] = {}
        self.updated: list[tuple[object, dict]] = []

        def _register(_domain, name, handler, schema=None):
            self.registered[name] = handler

        def _update(entry, options):
            self.updated.append((entry, options))

        self.services = types.SimpleNamespace(async_register=_register)
        self.config_entries = types.SimpleNamespace(
            async_entries=lambda _domain: list(entries), async_update_entry=_update
        )


class _Tracker:
    def __init__(self):
        self.totals: list[tuple[str, float]] = []

    async def async_set_total(self, vehicle, energy):
        self.totals.append((vehicle, energy))


class _Coordinator:
    def __init__(self):
        self.listeners_updated = 0
        self.resumed = 0
        self.released: list[int] = []

    def async_update_listeners(self):
        self.listeners_updated += 1

    def async_resume_connection(self):
        self.resumed += 1

    async def async_release_connection(self, duration_s):
        self.released.append(duration_s)


def _entry(entry_id="one", *, options=None, tracker=None, coordinator=None):
    runtime = types.SimpleNamespace(
        vehicle_tracker=tracker,
        solar_surplus_controller=None,
        coordinator=coordinator or _Coordinator(),
    )
    return types.SimpleNamespace(
        entry_id=entry_id, title=entry_id, options=options or {}, runtime_data=runtime
    )


def _call(**data):
    return types.SimpleNamespace(data=data)


def _setup(entries=()):
    import tuya_ev_charger as integration

    hass = _Hass(entries)
    assert asyncio.run(integration.async_setup(hass, {})) is True
    return hass


def test_services_exist_before_any_entry_is_configured():
    from tuya_ev_charger import const

    hass = _setup()

    assert set(hass.registered) == {
        const.SERVICE_FORCE_CHARGE_FOR,
        const.SERVICE_PAUSE_SURPLUS,
        const.SERVICE_PROFILE_ASSISTANT,
        const.SERVICE_SET_SURPLUS_PROFILE,
        const.SERVICE_RELEASE_CONNECTION,
        const.SERVICE_DRY_RUN_SURPLUS,
        const.SERVICE_SET_VEHICLE_ENERGY,
    }


def test_services_match_services_yaml():
    hass = _setup()
    declared = set(re.findall(r"^([a-z_]+):", (COMPONENT / "services.yaml").read_text(), re.M))

    assert set(hass.registered) == declared


def test_a_call_with_no_loaded_entry_says_so():
    hass = _setup([])

    with pytest.raises(Exception, match="no_loaded_entries"):
        asyncio.run(hass.registered["release_connection"](_call(duration_minutes=5)))


def test_an_entry_without_runtime_data_does_not_count_as_loaded():
    unloaded = types.SimpleNamespace(entry_id="x", title="x", options={}, runtime_data=None)
    hass = _setup([unloaded])

    with pytest.raises(Exception, match="no_loaded_entries"):
        asyncio.run(hass.registered["release_connection"](_call(duration_minutes=5)))


def test_two_loaded_entries_need_an_entry_id():
    hass = _setup([_entry("one"), _entry("two")])

    with pytest.raises(Exception, match="multiple_entries"):
        asyncio.run(hass.registered["release_connection"](_call(duration_minutes=5)))


def test_an_unknown_entry_id_is_rejected_with_the_id():
    hass = _setup([_entry("one")])

    with pytest.raises(Exception, match="entry_not_loaded") as excinfo:
        asyncio.run(
            hass.registered["release_connection"](_call(entry_id="nope", duration_minutes=5))
        )

    assert excinfo.value.translation_placeholders == {"entry_id": "nope"}


def test_the_only_loaded_entry_is_used_without_an_entry_id():
    coordinator = _Coordinator()
    hass = _setup([_entry("one", coordinator=coordinator)])

    asyncio.run(hass.registered["release_connection"](_call(duration_minutes=2)))

    assert coordinator.released == [120]


def test_release_connection_with_zero_minutes_resumes_instead():
    coordinator = _Coordinator()
    hass = _setup([_entry("one", coordinator=coordinator)])

    asyncio.run(hass.registered["release_connection"](_call(duration_minutes=0)))

    assert coordinator.resumed == 1
    assert coordinator.released == []


def test_an_unsupported_surplus_profile_is_a_validation_error():
    entry = _entry("one")
    hass = _setup([entry])

    with pytest.raises(Exception, match="unsupported_surplus_profile"):
        asyncio.run(hass.registered["set_surplus_profile"](_call(profile="turbo")))

    assert hass.updated == []


def test_a_supported_surplus_profile_rewrites_the_options():
    entry = _entry("one", options={"keep": "me"})
    hass = _setup([entry])

    asyncio.run(hass.registered["set_surplus_profile"](_call(profile="eco")))

    ((updated_entry, options),) = hass.updated
    assert updated_entry is entry
    assert options["surplus_profile"] == "eco"
    assert options["keep"] == "me"


def test_vehicle_energy_needs_the_vehicles_option():
    hass = _setup([_entry("one", tracker=None)])

    with pytest.raises(Exception, match="vehicle_tracking_disabled"):
        asyncio.run(hass.registered["set_vehicle_energy"](_call(vehicle="Zoe", energy_kwh=1200.0)))


def test_vehicle_energy_for_an_unknown_vehicle_lists_the_known_ones():
    entry = _entry("one", options={"vehicles": "Zoe, Leaf"}, tracker=_Tracker())
    hass = _setup([entry])

    with pytest.raises(Exception, match="unknown_vehicle_configured") as excinfo:
        asyncio.run(hass.registered["set_vehicle_energy"](_call(vehicle="Model3", energy_kwh=10.0)))

    assert excinfo.value.translation_placeholders["configured"] == "Zoe, Leaf"
    assert entry.runtime_data.vehicle_tracker.totals == []


def test_vehicle_energy_for_a_known_vehicle_is_recorded_and_listeners_nudged():
    tracker = _Tracker()
    coordinator = _Coordinator()
    entry = _entry("one", options={"vehicles": "Zoe"}, tracker=tracker, coordinator=coordinator)
    hass = _setup([entry])

    asyncio.run(hass.registered["set_vehicle_energy"](_call(vehicle="Zoe", energy_kwh=1200.0)))

    assert tracker.totals == [("Zoe", 1200.0)]
    assert coordinator.listeners_updated == 1


def test_dry_run_without_a_controller_is_a_validation_error():
    hass = _setup([_entry("one")])

    with pytest.raises(Exception, match="surplus_controller_unavailable_entry"):
        asyncio.run(hass.registered["dry_run_surplus"](_call()))


# --- the keys the code raises must exist in every translation ------------------


def _raised_keys() -> set[str]:
    """Every key passed to `charger_error` / `validation_error`, by reading the code."""
    keys: set[str] = set()
    for path in COMPONENT.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"charger_error", "validation_error"}
            ):
                # The key may be a conditional ("a" if enabled else "b"): take both.
                keys |= {
                    n.value
                    for n in ast.walk(node.args[0])
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                }
    return keys


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json", "translations/fr.json"])
def test_every_raised_error_key_is_translated(name):
    translated = set(json.loads((COMPONENT / name).read_text())["exceptions"])
    raised = _raised_keys()

    assert raised, "the scan found no raised keys; the pattern is stale"
    assert raised <= translated, f"{name} lacks {sorted(raised - translated)}"
    assert translated <= raised, f"{name} has unused {sorted(translated - raised)}"
