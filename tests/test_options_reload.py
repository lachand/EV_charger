"""#36: a surplus knob change must not reload the whole integration.

A reload tears down the charger's single local connection for a couple of
seconds, and a `switch.charging_session` write that lands in that window is
lost. `_async_update_listener` now applies a change confined to the surplus
runtime options in place, and only falls back to a full reload for anything
else. Every import is inside a function: the integration is only importable
once the session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import asyncio
import types

import pytest


class _Hass:
    def __init__(self):
        self.reloaded: list[str] = []
        self.scheduled: list = []

        async def _reload(entry_id):
            self.reloaded.append(entry_id)

        self.config_entries = types.SimpleNamespace(async_reload=_reload)
        self.states = types.SimpleNamespace(get=lambda _entity_id: None)

    def add_job(self, _target, *args):
        self.scheduled.append(args[0] if args else None)


class _Controller:
    def __init__(self):
        self.applied = 0

    async def async_apply_settings(self):
        self.applied += 1

    def config_problems(self):
        return []


class _Coordinator:
    def __init__(self):
        self.notified = 0

    def async_update_listeners(self):
        self.notified += 1


def _runtime(options, *, controller=None, coordinator=None):
    from tuya_ev_charger import TuyaEVChargerRuntimeData

    return TuyaEVChargerRuntimeData(
        client=object(),
        coordinator=coordinator or _Coordinator(),
        solar_surplus_controller=controller or _Controller(),
        options_snapshot=dict(options),
    )


def _entry(options, runtime_data=None):
    return types.SimpleNamespace(
        options=dict(options),
        entry_id="e1",
        title="test",
        runtime_data=runtime_data,
    )


@pytest.fixture(autouse=True)
def _stub_config_problems(monkeypatch):
    import tuya_ev_charger as integration

    calls: list = []
    monkeypatch.setattr(integration, "async_sync_config_problems", lambda *a: calls.append(a))
    return calls


_BASE = {"surplus_mode_enabled": False, "scan_interval": 30}


def test_only_surplus_knobs_change_applies_in_place():
    import tuya_ev_charger as integration

    rd = _runtime(_BASE)
    entry = _entry({**_BASE, "surplus_mode_enabled": True}, runtime_data=rd)
    hass = _Hass()

    asyncio.run(integration._async_update_listener(hass, entry))

    assert hass.reloaded == []
    assert rd.solar_surplus_controller.applied == 1
    assert rd.coordinator.notified == 1
    assert rd.options_snapshot["surplus_mode_enabled"] is True


def test_a_non_surplus_change_reloads():
    import tuya_ev_charger as integration

    rd = _runtime(_BASE)
    entry = _entry({**_BASE, "scan_interval": 10}, runtime_data=rd)
    hass = _Hass()

    asyncio.run(integration._async_update_listener(hass, entry))

    assert hass.reloaded == ["e1"]
    assert rd.solar_surplus_controller.applied == 0


def test_a_mixed_change_reloads():
    import tuya_ev_charger as integration

    rd = _runtime(_BASE)
    entry = _entry({"surplus_mode_enabled": True, "scan_interval": 10}, runtime_data=rd)
    hass = _Hass()

    asyncio.run(integration._async_update_listener(hass, entry))

    assert hass.reloaded == ["e1"]
    assert rd.solar_surplus_controller.applied == 0


def test_no_runtime_data_reloads():
    import tuya_ev_charger as integration

    hass = _Hass()
    asyncio.run(integration._async_update_listener(hass, _entry(_BASE, runtime_data=None)))
    assert hass.reloaded == ["e1"]


def test_apply_settings_rereads_the_snapshot():
    from tuya_ev_charger import solar_surplus

    hass = _Hass()
    entry = types.SimpleNamespace(
        options={"surplus_mode_enabled": False, "surplus_sensor_entity_id": "sensor.grid"},
        title="t",
        entry_id="e",
    )
    controller = solar_surplus.SolarSurplusController(
        hass=hass,
        entry=entry,
        client=object(),
        coordinator=types.SimpleNamespace(data=None),
    )
    assert controller._settings.mode_enabled is False

    entry.options = {**entry.options, "surplus_mode_enabled": True}
    asyncio.run(controller.async_apply_settings())

    assert controller._settings.mode_enabled is True
    assert "options_updated" in hass.scheduled
