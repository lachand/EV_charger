"""The select platform: plug-in action, active vehicle, surplus profile.

The vehicle select has a subtle rule -- fall back to the first car when the stored
one was renamed -- and the profile select rewrites the config entry, so both are
worth pinning.
"""

from __future__ import annotations

import asyncio
import types

import pytest


def _make(cls, **fields):
    entity = cls.__new__(cls)
    for key, value in fields.items():
        setattr(entity, key, value)
    return entity


# --- plug-in action --------------------------------------------------------


class _Client:
    def __init__(self, *, ok=True):
        self.calls: list[str] = []
        self._ok = ok

    async def async_set_plug_in_action(self, option):
        self.calls.append(option)
        return self._ok


def _plug_in(*, plug_in_action, client=None):
    from tuya_ev_charger.select import TuyaEVChargerPlugInActionSelect as S

    refreshes: list[int] = []

    async def _refresh():
        refreshes.append(1)

    data = types.SimpleNamespace(plug_in_action=plug_in_action)
    entity = _make(
        S,
        coordinator=types.SimpleNamespace(data=data, async_request_refresh=_refresh),
        _runtime_data=types.SimpleNamespace(client=client or _Client()),
    )
    entity.refreshes = refreshes
    return entity


def test_plug_in_action_is_unavailable_when_the_firmware_omits_it(monkeypatch):
    entity = _plug_in(plug_in_action=None)
    # available chains to super().available, which needs the coordinator; the
    # DP-present half is what this platform adds.
    assert entity.coordinator.data.plug_in_action is None
    assert entity.current_option is None


def test_selecting_a_new_plug_in_action_writes(monkeypatch):
    client = _Client()
    entity = _plug_in(plug_in_action="prompt", client=client)
    asyncio.run(entity.async_select_option("charge"))
    assert client.calls == ["charge"]
    assert entity.refreshes == [1]


def test_selecting_the_current_plug_in_action_writes_nothing():
    client = _Client()
    entity = _plug_in(plug_in_action="charge", client=client)
    asyncio.run(entity.async_select_option("charge"))
    assert client.calls == []


def test_a_failed_plug_in_write_raises():
    from tuya_ev_charger.select import HomeAssistantError

    entity = _plug_in(plug_in_action="prompt", client=_Client(ok=False))
    with pytest.raises(HomeAssistantError):
        asyncio.run(entity.async_select_option("charge"))


# --- active vehicle --------------------------------------------------------


def _vehicle(*, options, active):
    from tuya_ev_charger.select import TuyaEVChargerVehicleSelect as S

    tracker_sets: list[str] = []

    async def _set_active(name):
        tracker_sets.append(name)

    tracker = types.SimpleNamespace(active_vehicle=active, async_set_active_vehicle=_set_active)
    entity = _make(
        S,
        _entry=types.SimpleNamespace(options={"vehicles": ", ".join(options)}),
        _runtime_data=types.SimpleNamespace(vehicle_tracker=tracker),
        async_write_ha_state=lambda: None,
    )
    entity.tracker_sets = tracker_sets
    return entity


def test_the_active_vehicle_reflects_the_tracker():
    entity = _vehicle(options=["Zoe", "Kangoo"], active="Kangoo")
    assert entity.current_option == "Kangoo"


def test_a_renamed_stored_vehicle_falls_back_to_the_first():
    """The stored car was renamed out of the list; the select must not show a
    value that is no longer an option."""
    entity = _vehicle(options=["Zoe", "Kangoo"], active="OldName")
    assert entity.current_option == "Zoe"


def test_selecting_an_unknown_vehicle_raises():
    from tuya_ev_charger.select import HomeAssistantError

    entity = _vehicle(options=["Zoe"], active="Zoe")
    with pytest.raises(HomeAssistantError, match="Unknown vehicle"):
        asyncio.run(entity.async_select_option("Tesla"))


def test_selecting_a_known_vehicle_updates_the_tracker():
    entity = _vehicle(options=["Zoe", "Kangoo"], active="Zoe")
    asyncio.run(entity.async_select_option("Kangoo"))
    assert entity.tracker_sets == ["Kangoo"]


# --- surplus profile -------------------------------------------------------


def _profile(*, current):
    from tuya_ev_charger.select import TuyaEVChargerSurplusProfileSelect as S

    updates: list[dict] = []
    entry = types.SimpleNamespace(options={"surplus_profile": current})
    hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(
            async_update_entry=lambda e, options: updates.append(options)
        )
    )
    entity = _make(S, _entry=entry, hass=hass, async_write_ha_state=lambda: None)
    entity.updates = updates
    return entity


def test_an_unsupported_profile_raises():
    from tuya_ev_charger.select import HomeAssistantError

    with pytest.raises(HomeAssistantError, match="Unsupported"):
        asyncio.run(_profile(current="balanced").async_select_option("turbo"))


def test_selecting_the_current_profile_does_not_rewrite_the_entry():
    entity = _profile(current="balanced")
    asyncio.run(entity.async_select_option("balanced"))
    assert entity.updates == []


def test_selecting_a_new_profile_rewrites_the_entry_with_its_preset():
    entity = _profile(current="balanced")
    asyncio.run(entity.async_select_option("eco"))
    assert len(entity.updates) == 1
    written = entity.updates[0]
    assert written["surplus_profile"] == "eco"
    # The preset's thresholds must have been applied, not just the name.
    assert "surplus_start_threshold_w" in written
