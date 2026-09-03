"""The switch platform — every one of these writes to the charger.

A DP write makes the charger beep, and controllers like evcc re-assert the same
state on a timer, so the write-skip when the value already matches is the
behaviour that matters most. None of it had tests.
"""

from __future__ import annotations

import asyncio
import types

import pytest


class _Client:
    def __init__(self, *, ok=True):
        self.calls: list[tuple[str, object]] = []
        self._ok = ok

    async def async_set_charge_enabled(self, value):
        self.calls.append(("charge", value))
        return self._ok

    async def async_set_nfc_enabled(self, value):
        self.calls.append(("nfc", value))
        return self._ok


def _switch(cls, *, data=None, client=None, **fields):
    entity = cls.__new__(cls)
    refreshes: list[int] = []

    async def _refresh():
        refreshes.append(1)

    entity.coordinator = types.SimpleNamespace(data=data, async_request_refresh=_refresh)
    entity._runtime_data = types.SimpleNamespace(client=client or _Client())
    entity.refreshes = refreshes
    # __new__ skips __init__; the optimistic-state fields the real __init__ sets.
    entity._pending_charge = None
    entity._pending_since = 0.0
    for key, value in fields.items():
        setattr(entity, key, value)
    return entity


def _metrics(**kwargs):
    base = {"do_charge": None, "work_state_debug": "IDLE", "nfc_enabled": None}
    base.update(kwargs)
    return types.SimpleNamespace(**base)


# --- charge session --------------------------------------------------------


def test_charge_session_reads_the_do_charge_dp():
    from tuya_ev_charger.switch import TuyaEVChargerChargeSessionSwitch as S

    assert _switch(S, data=_metrics(do_charge=True)).is_on is True
    assert _switch(S, data=_metrics(do_charge=False)).is_on is False


def test_charge_session_falls_back_to_the_operating_state():
    """The depow 3.5 kW has no DP 140, so is_on must read the work state."""
    from tuya_ev_charger.switch import TuyaEVChargerChargeSessionSwitch as S

    on = _switch(S, data=_metrics(do_charge=None, work_state_debug="WORKING"))
    assert on.is_on is True


def test_turning_on_writes_and_refreshes():
    from tuya_ev_charger.switch import TuyaEVChargerChargeSessionSwitch as S

    client = _Client()
    switch = _switch(S, data=_metrics(do_charge=False), client=client)
    asyncio.run(switch.async_turn_on())
    assert client.calls == [("charge", True)]
    assert switch.refreshes == [1]


def test_turning_on_when_already_on_writes_nothing():
    """The write-skip: the charger would only beep."""
    from tuya_ev_charger.switch import TuyaEVChargerChargeSessionSwitch as S

    client = _Client()
    switch = _switch(S, data=_metrics(do_charge=True), client=client)
    asyncio.run(switch.async_turn_on())
    assert client.calls == []


def test_a_failed_charge_write_raises():
    from tuya_ev_charger.switch import HomeAssistantError
    from tuya_ev_charger.switch import TuyaEVChargerChargeSessionSwitch as S

    switch = _switch(S, data=_metrics(do_charge=False), client=_Client(ok=False))
    with pytest.raises(HomeAssistantError, match="start charging"):
        asyncio.run(switch.async_turn_on())


# --- optimistic state on a charger without DP 140 (the switch used to flick off)


def test_switch_holds_on_while_the_charger_walks_up_to_working():
    """No DP 140: after turn_on the charger steps PAUSE -> IDLEINS -> WORKING."""
    from tuya_ev_charger.switch import TuyaEVChargerChargeSessionSwitch as S

    switch = _switch(S, data=_metrics(do_charge=None, work_state_debug="PAUSE"))
    asyncio.run(switch.async_turn_on())

    # Still transitioning -- the raw state is not WORKING yet, but the switch holds on.
    switch.coordinator.data = _metrics(do_charge=None, work_state_debug="IDLEINS")
    assert switch.is_on is True

    # Charger reaches WORKING: optimism clears, still on, from the real state now.
    switch.coordinator.data = _metrics(do_charge=None, work_state_debug="WORKING")
    assert switch.is_on is True
    assert switch._pending_charge is None


def test_switch_gives_up_the_pending_on_when_no_cable():
    from tuya_ev_charger.switch import TuyaEVChargerChargeSessionSwitch as S

    switch = _switch(S, data=_metrics(do_charge=None, work_state_debug="IDLEINS"))
    asyncio.run(switch.async_turn_on())
    switch.coordinator.data = _metrics(do_charge=None, work_state_debug="IDLE")
    assert switch.is_on is False
    assert switch._pending_charge is None


def test_pending_state_times_out(monkeypatch):
    import tuya_ev_charger.switch as sw

    monkeypatch.setattr(sw, "monotonic", lambda: 10_000.0)
    switch = _switch(sw.TuyaEVChargerChargeSessionSwitch, data=_metrics(do_charge=None))
    asyncio.run(switch.async_turn_on())
    switch.coordinator.data = _metrics(do_charge=None, work_state_debug="IDLEINS")
    assert switch.is_on is True  # within the window

    monkeypatch.setattr(sw, "monotonic", lambda: 10_000.0 + sw._PENDING_TIMEOUT_S + 1)
    assert switch.is_on is False
    assert switch._pending_charge is None


def test_dp140_charger_reconciles_on_the_next_poll():
    """With DP 140 the echo is immediate, so the pending state clears at once."""
    from tuya_ev_charger.switch import TuyaEVChargerChargeSessionSwitch as S

    switch = _switch(S, data=_metrics(do_charge=False))
    asyncio.run(switch.async_turn_on())
    switch.coordinator.data = _metrics(do_charge=True)
    assert switch.is_on is True
    assert switch._pending_charge is None


# --- NFC -------------------------------------------------------------------


def test_nfc_write_skip_and_write():
    from tuya_ev_charger.switch import TuyaEVChargerNfcSwitch as S

    client = _Client()
    already = _switch(S, data=_metrics(nfc_enabled=True), client=client)
    asyncio.run(already.async_turn_on())
    assert client.calls == []

    change = _switch(S, data=_metrics(nfc_enabled=False), client=client)
    asyncio.run(change.async_turn_on())
    assert client.calls == [("nfc", True)]


def test_a_switch_reads_false_before_the_first_poll():
    from tuya_ev_charger.switch import TuyaEVChargerChargeSessionSwitch as S

    assert _switch(S, data=None).is_on is False
