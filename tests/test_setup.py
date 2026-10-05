"""Entry setup and unload, and the small helpers around them.

`async_setup_entry` wires seven collaborators together and is the one place a
mistake means the integration does not load at all, so it is tested end to end
with every collaborator replaced by a recorder. The order matters (the network
identity must be persisted *before* the update listener exists, or saving it
would reload the entry), so the tests pin the order too.

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import asyncio
import types

import pytest

GWID = "bf1234567890abcdefxyz1"


class _Recorder:
    def __init__(self):
        self.events: list[str] = []

    def note(self, event):
        self.events.append(event)


def _entry(*, data=None, options=None, version=1):
    entry = types.SimpleNamespace(
        entry_id="e1",
        title="charger",
        version=version,
        data={
            "device_id": GWID,
            "host": "192.168.1.10",
            "local_key": "key",
            "protocol_version": "3.5",
            **(data or {}),
        },
        options=options or {},
        runtime_data=None,
        unloads=[],
    )
    entry.add_update_listener = lambda listener: ("listener", listener)
    entry.async_on_unload = lambda cb: entry.unloads.append(cb)
    return entry


# --- the option helpers ---------------------------------------------------------


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, None),
        ({"scan_interval": "45"}, 45),
        ({"scan_interval": "garbage"}, None),
        ({"scan_interval": 1}, "min"),
        ({"scan_interval": 10_000_000}, "max"),
    ],
)
def test_the_scan_interval_is_clamped_and_forgiving(options, expected):
    from tuya_ev_charger import (
        DEFAULT_SCAN_INTERVAL_SECONDS,
        MAX_SCAN_INTERVAL_SECONDS,
        MIN_SCAN_INTERVAL_SECONDS,
        _scan_interval_seconds,
    )

    wanted = {
        None: DEFAULT_SCAN_INTERVAL_SECONDS,
        "min": MIN_SCAN_INTERVAL_SECONDS,
        "max": MAX_SCAN_INTERVAL_SECONDS,
    }.get(expected, expected)

    assert _scan_interval_seconds(_entry(options=options)) == wanted


def test_the_charger_profile_prefers_options_then_data_then_the_default():
    from tuya_ev_charger import _charger_profile
    from tuya_ev_charger.const import CHARGER_PROFILES, DEFAULT_CHARGER_PROFILE

    other = next(p for p in CHARGER_PROFILES if p != DEFAULT_CHARGER_PROFILE)

    assert _charger_profile(_entry()) == DEFAULT_CHARGER_PROFILE
    assert _charger_profile(_entry(data={"charger_profile": other.upper()})) == other
    assert _charger_profile(_entry(options={"charger_profile": other})) == other
    assert _charger_profile(_entry(options={"charger_profile": "nonsense"})) == (
        DEFAULT_CHARGER_PROFILE
    )


def test_the_custom_profile_json_is_trimmed():
    from tuya_ev_charger import _charger_profile_json

    assert _charger_profile_json(_entry(options={"charger_profile_json": '  {"a": 1} '})) == (
        '{"a": 1}'
    )
    assert _charger_profile_json(_entry(options={"charger_profile_json": None})) == ""


# --- migration ------------------------------------------------------------------


def test_a_current_entry_migrates_trivially():
    from tuya_ev_charger import async_migrate_entry
    from tuya_ev_charger.const import CONFIG_ENTRY_VERSION

    assert asyncio.run(async_migrate_entry(None, _entry(version=CONFIG_ENTRY_VERSION))) is True


def test_an_entry_from_a_newer_release_is_refused_rather_than_misread():
    from tuya_ev_charger import async_migrate_entry
    from tuya_ev_charger.const import CONFIG_ENTRY_VERSION

    assert asyncio.run(async_migrate_entry(None, _entry(version=CONFIG_ENTRY_VERSION + 1))) is False


# --- persisting what the coordinator learned ------------------------------------


class _Hass:
    def __init__(self):
        self.updated: list[dict] = []
        self.forwarded: list = []
        self.unloaded: list = []
        self.reloaded: list = []

        async def _forward(entry, platforms):
            self.forwarded.append((entry, tuple(platforms)))

        async def _unload(entry, platforms):
            self.unloaded.append((entry, tuple(platforms)))
            return True

        async def _reload(entry_id):
            self.reloaded.append(entry_id)

        def _update(entry, data):
            self.updated.append(data)

        self.config_entries = types.SimpleNamespace(
            async_forward_entry_setups=_forward,
            async_unload_platforms=_unload,
            async_reload=_reload,
            async_update_entry=_update,
        )


def _coordinator(*, host="192.168.1.10", new_key=None, discovery=None):
    return types.SimpleNamespace(
        client=types.SimpleNamespace(host=host),
        new_local_key=new_key,
        last_discovery=discovery,
        data="metrics",
    )


def test_nothing_changed_means_nothing_is_saved():
    from tuya_ev_charger import _async_reconcile_network_info

    hass = _Hass()
    asyncio.run(_async_reconcile_network_info(hass, _entry(), _coordinator()))

    assert hass.updated == []


def test_a_relocated_host_a_rotated_key_and_the_mac_are_persisted_together():
    from tuya_ev_charger import _async_reconcile_network_info

    hass = _Hass()
    coordinator = _coordinator(
        host="192.168.1.99", new_key="newkey", discovery={"mac": "AA:BB:CC:DD:EE:FF"}
    )

    asyncio.run(_async_reconcile_network_info(hass, _entry(), coordinator))

    (data,) = hass.updated
    assert data["host"] == "192.168.1.99"
    assert data["local_key"] == "newkey"
    assert data["mac"] == "aa:bb:cc:dd:ee:ff"
    assert data["device_id"] == GWID  # nothing else is lost


def test_a_known_mac_is_not_overwritten_by_a_scan():
    from tuya_ev_charger import _async_reconcile_network_info

    hass = _Hass()
    entry = _entry(data={"mac": "aa:aa:aa:aa:aa:aa"})
    coordinator = _coordinator(discovery={"mac": "BB:BB:BB:BB:BB:BB"})

    asyncio.run(_async_reconcile_network_info(hass, entry, coordinator))

    assert hass.updated == []


def test_a_malformed_mac_is_ignored():
    from tuya_ev_charger import _normalized_mac

    assert _normalized_mac("AABBCCDDEEFF") is None
    assert _normalized_mac("") is None
    assert _normalized_mac(None) is None
    assert _normalized_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"


# --- async_setup_entry ----------------------------------------------------------


def _patch_setup(monkeypatch, *, connect_error=None, rec=None):
    """Replace every collaborator `async_setup_entry` builds with a recorder."""
    import tuya_ev_charger as pkg

    rec = rec or _Recorder()

    class _Client:
        def __init__(self, **kwargs):
            rec.note("client")
            self.kwargs = kwargs
            self.host = kwargs["host"]
            self.closed = False

        async def async_connect(self):
            rec.note("connect")
            if connect_error:
                raise connect_error

        async def async_close(self):
            rec.note("close")

    class _Coordinator:
        def __init__(self, hass, client, entry, update_interval):
            self.client = client
            self.update_interval = update_interval
            self.new_local_key = None
            self.last_discovery = None
            self.data = types.SimpleNamespace()

        async def async_config_entry_first_refresh(self):
            rec.note("first_refresh")

    class _Store:
        def __init__(self, name):
            self.name = name

        def __call__(self, hass, entry_id):
            rec.note(f"create:{self.name}")
            return types.SimpleNamespace(
                async_load=lambda: _noted(rec, f"load:{self.name}"),
                async_flush=lambda now, force=False: _noted(rec, "flush"),
            )

    class _Controller:
        def __init__(self, **kwargs):
            rec.note("controller")

        async def async_start(self):
            rec.note("controller_start")

        async def async_shutdown(self):
            rec.note("controller_shutdown")

        def config_problems(self):
            return ["a_problem"]

    async def _tidy(hass, entry, coordinator):
        rec.note("tidy")

    monkeypatch.setattr(pkg, "TuyaEVChargerClient", _Client)
    monkeypatch.setattr(pkg, "TuyaEVChargerDataUpdateCoordinator", _Coordinator)
    monkeypatch.setattr(pkg, "VehicleEnergyTracker", _Store("tracker"))
    monkeypatch.setattr(pkg, "SessionHistory", _Store("history"))
    monkeypatch.setattr(pkg, "VehicleChargeCurves", _Store("curves"))
    monkeypatch.setattr(pkg, "SolarSurplusController", _Controller)
    monkeypatch.setattr(pkg, "_async_tidy_entities", _tidy)
    monkeypatch.setattr(
        pkg,
        "async_sync_config_problems",
        lambda hass, entry_id, problems: rec.note(f"sync:{problems}"),
    )
    return rec


async def _noted(rec, event):
    rec.note(event)


def test_setup_wires_the_runtime_data_and_forwards_the_platforms(monkeypatch):
    import tuya_ev_charger as pkg

    rec = _patch_setup(monkeypatch)
    hass, entry = _Hass(), _entry()

    assert asyncio.run(pkg.async_setup_entry(hass, entry)) is True

    runtime = entry.runtime_data
    assert isinstance(runtime, pkg.TuyaEVChargerRuntimeData)
    assert runtime.client.kwargs["device_id"] == GWID
    assert runtime.client.kwargs["host"] == "192.168.1.10"
    # The stores are shared with the coordinator, which logs sessions into them.
    assert runtime.coordinator.vehicle_tracker is runtime.vehicle_tracker
    assert runtime.coordinator.session_history is runtime.session_history
    assert runtime.coordinator.vehicle_curves is runtime.vehicle_curves
    assert runtime.coordinator.solar_surplus_controller is runtime.solar_surplus_controller
    assert hass.forwarded == [(entry, tuple(pkg.PLATFORMS))]
    assert entry.unloads == [("listener", pkg._async_update_listener)]
    assert "sync:['a_problem']" in rec.events


def test_setup_order_persists_the_network_identity_before_listening_for_changes(monkeypatch):
    """Saving the host after the listener exists would reload the entry in a loop."""
    import tuya_ev_charger as pkg

    rec = _patch_setup(monkeypatch)
    hass, entry = _Hass(), _entry()

    original = pkg._async_reconcile_network_info

    async def _reconcile(hass_, entry_, coordinator):
        rec.note(f"reconcile(listener_registered={bool(entry_.unloads)})")
        await original(hass_, entry_, coordinator)

    monkeypatch.setattr(pkg, "_async_reconcile_network_info", _reconcile)

    asyncio.run(pkg.async_setup_entry(hass, entry))

    assert "reconcile(listener_registered=False)" in rec.events
    assert rec.events.index("first_refresh") < rec.events.index(
        "reconcile(listener_registered=False)"
    )
    assert rec.events.index("controller_start") < rec.events.index("tidy")


def test_a_client_that_cannot_be_built_means_not_ready_not_a_crash(monkeypatch):
    import tuya_ev_charger as pkg
    from homeassistant.exceptions import ConfigEntryNotReady

    _patch_setup(monkeypatch, connect_error=OSError("no route"))
    entry = _entry()

    with pytest.raises(ConfigEntryNotReady, match="no route"):
        asyncio.run(pkg.async_setup_entry(_Hass(), entry))

    assert entry.runtime_data is None


# --- async_unload_entry ---------------------------------------------------------


def test_unload_stops_the_controller_flushes_curves_and_frees_the_socket(monkeypatch):
    import tuya_ev_charger as pkg

    rec = _patch_setup(monkeypatch)
    hass, entry = _Hass(), _entry()
    asyncio.run(pkg.async_setup_entry(hass, entry))
    rec.events.clear()

    assert asyncio.run(pkg.async_unload_entry(hass, entry)) is True

    assert hass.unloaded == [(entry, tuple(pkg.PLATFORMS))]
    # The socket is the charger's only slot: it is released last, after the
    # controller that writes to it has stopped.
    assert rec.events == ["controller_shutdown", "flush", "close"]


def test_unloading_an_entry_that_never_finished_setup_is_safe():
    import tuya_ev_charger as pkg

    hass, entry = _Hass(), _entry()

    assert asyncio.run(pkg.async_unload_entry(hass, entry)) is True


# --- tidying the entity registry ------------------------------------------------


def _tidy(monkeypatch, *, registry_entries):
    import tuya_ev_charger as pkg

    calls = types.SimpleNamespace(disabled=[], offered=[], cleared=[])

    async def _disable(hass, entry_id, keys, reason):
        calls.disabled.append((set(keys), reason))

    monkeypatch.setattr(pkg, "async_disable_entities", _disable)
    monkeypatch.setattr(pkg, "unavailable_capability_keys", lambda data: {"voltage_l3"})
    monkeypatch.setattr(pkg.er, "async_get", lambda hass: "registry", raising=False)
    monkeypatch.setattr(
        pkg.er,
        "async_entries_for_config_entry",
        lambda registry, entry_id: registry_entries,
        raising=False,
    )
    monkeypatch.setattr(
        pkg, "async_offer_entity_cleanup", lambda hass, entry_id, n: calls.offered.append(n)
    )
    monkeypatch.setattr(pkg, "async_clear", lambda hass, entry_id, kind: calls.cleared.append(kind))
    return pkg, calls


def _registry_entry(unique_id, *, disabled=None, handled=False):
    return types.SimpleNamespace(
        unique_id=unique_id,
        disabled_by=disabled,
        options={"tuya_ev_charger": {"auto_disabled": True}} if handled else {},
    )


def test_entities_the_hardware_lacks_are_disabled_outright(monkeypatch):
    pkg, calls = _tidy(monkeypatch, registry_entries=[])

    asyncio.run(pkg._async_tidy_entities(_Hass(), _entry(), _coordinator()))

    assert calls.disabled == [({"voltage_l3"}, "this charger does not expose")]


def test_working_advanced_entities_are_only_offered_to_the_user(monkeypatch):
    from tuya_ev_charger.const import ADVANCED_ENTITY_KEYS

    advanced = sorted(ADVANCED_ENTITY_KEYS)[0]
    pkg, calls = _tidy(
        monkeypatch,
        registry_entries=[
            _registry_entry(f"{GWID}_{advanced}"),  # offered
            _registry_entry(f"{GWID}_{advanced}", disabled="user"),  # already off
            _registry_entry(f"{GWID}_charge_current"),  # not advanced
        ],
    )

    asyncio.run(pkg._async_tidy_entities(_Hass(), _entry(), _coordinator()))

    assert calls.offered == [1]
    assert calls.cleared == []


def test_nothing_left_to_offer_clears_the_notice(monkeypatch):
    from tuya_ev_charger.repairs import ISSUE_TIDY_ENTITIES

    pkg, calls = _tidy(monkeypatch, registry_entries=[])

    asyncio.run(pkg._async_tidy_entities(_Hass(), _entry(), _coordinator()))

    assert calls.offered == []
    assert calls.cleared == [ISSUE_TIDY_ENTITIES]


# --- service handlers that act on the controller ---------------------------------


class _ServiceHass:
    def __init__(self, entries):
        self.registered = {}
        self.bus = types.SimpleNamespace(
            async_fire=lambda name, payload: self.fired.append((name, payload))
        )
        self.fired = []
        self.updated = []
        self.services = types.SimpleNamespace(
            async_register=lambda domain, name, handler, schema=None: self.registered.__setitem__(
                name, handler
            )
        )
        self.config_entries = types.SimpleNamespace(
            async_entries=lambda _d: list(entries),
            async_update_entry=lambda entry, options: self.updated.append(options),
        )


class _Controller:
    def __init__(self, report=None):
        self.forced = []
        self.paused = []
        self._report = report or {}

    async def async_force_charge_for(self, duration_s, current_a):
        self.forced.append((duration_s, current_a))

    async def async_pause_for(self, duration_s):
        self.paused.append(duration_s)

    async def async_profile_assistant_report(self):
        return self._report

    def async_dry_run(self):
        return {"decision": "would_start"}


def _service_setup(monkeypatch, controller):
    import tuya_ev_charger as pkg
    from homeassistant.components import persistent_notification

    notes = []
    monkeypatch.setattr(
        persistent_notification, "async_create", lambda **kw: notes.append(kw), raising=False
    )
    entry = _entry(options={"keep": 1})
    entry.runtime_data = types.SimpleNamespace(
        solar_surplus_controller=controller, coordinator=None, vehicle_tracker=None
    )
    hass = _ServiceHass([entry])
    pkg._register_services(hass)
    return hass, entry, notes


def _call(**data):
    return types.SimpleNamespace(data=data)


def test_force_charge_converts_minutes_and_passes_the_current(monkeypatch):
    controller = _Controller()
    hass, _, _ = _service_setup(monkeypatch, controller)

    asyncio.run(hass.registered["force_charge_for"](_call(duration_minutes=30, current_a=16)))
    asyncio.run(hass.registered["force_charge_for"](_call(duration_minutes=5)))

    assert controller.forced == [(1800, 16), (300, None)]


def test_pause_surplus_converts_minutes(monkeypatch):
    controller = _Controller()
    hass, _, _ = _service_setup(monkeypatch, controller)

    asyncio.run(hass.registered["pause_surplus"](_call(duration_minutes=10)))

    assert controller.paused == [600]


def test_the_profile_assistant_only_applies_a_suggestion_when_asked(monkeypatch):
    from tuya_ev_charger.const import CHARGER_PROFILES

    suggested = sorted(CHARGER_PROFILES)[0]
    controller = _Controller(report={"suggested_profile": suggested.upper()})
    hass, _, notes = _service_setup(monkeypatch, controller)

    asyncio.run(hass.registered["profile_assistant"](_call(apply=False)))
    assert hass.updated == []

    asyncio.run(hass.registered["profile_assistant"](_call(apply=True)))
    assert hass.updated[0]["charger_profile"] == suggested
    assert hass.updated[0]["keep"] == 1
    assert notes  # the user is shown what was suggested either way


def test_the_dry_run_fires_an_event_and_shows_a_notification(monkeypatch):
    hass, _, notes = _service_setup(monkeypatch, _Controller())

    asyncio.run(hass.registered["dry_run_surplus"](_call()))

    ((name, payload),) = hass.fired
    assert name == "tuya_ev_charger_dry_run_surplus"
    assert payload["decision"] == "would_start"
    assert payload["entry_id"] == "e1"
    assert notes
