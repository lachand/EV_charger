"""The button platform: ready-to-charge (DP 101) and reboot.

DP 101 is the write still unvalidated on hardware, so its behaviour is pinned
here at least at the software level: it writes the ready state and surfaces a
failure rather than swallowing it.
"""

from __future__ import annotations

import asyncio
import types

import pytest


class _Client:
    def __init__(self, *, work_ok=True, reboot_ok=True):
        self.calls: list[str] = []
        self._work_ok = work_ok
        self._reboot_ok = reboot_ok

    async def async_set_work_state(self, state):
        self.calls.append(f"work:{state}")
        return self._work_ok

    async def async_reboot(self):
        self.calls.append("reboot")
        return self._reboot_ok


def _button(cls, *, client=None, controller=None):
    entity = cls.__new__(cls)
    refreshes: list[int] = []

    async def _refresh():
        refreshes.append(1)

    entity.coordinator = types.SimpleNamespace(data=None, async_request_refresh=_refresh)
    entity._runtime_data = types.SimpleNamespace(
        client=client or _Client(), solar_surplus_controller=controller
    )
    entity.refreshes = refreshes
    return entity


def test_ready_to_charge_writes_the_ready_state():
    from tuya_ev_charger.button import TuyaEVChargerReadyToChargeButton as B
    from tuya_ev_charger.const import WORK_STATE_READY_TO_CHARGE

    client = _Client()
    asyncio.run(_button(B, client=client).async_press())
    assert client.calls == [f"work:{WORK_STATE_READY_TO_CHARGE}"]


def test_ready_to_charge_failure_raises():
    from homeassistant.exceptions import HomeAssistantError
    from tuya_ev_charger.button import TuyaEVChargerReadyToChargeButton as B

    with pytest.raises(HomeAssistantError, match="ready_to_charge_failed"):
        asyncio.run(_button(B, client=_Client(work_ok=False)).async_press())


def test_a_successful_ready_press_refreshes():
    from tuya_ev_charger.button import TuyaEVChargerReadyToChargeButton as B

    button = _button(B)
    asyncio.run(button.async_press())
    assert button.refreshes == [1]


def test_reboot_failure_raises_before_the_wait(monkeypatch):
    from homeassistant.exceptions import HomeAssistantError
    from tuya_ev_charger.button import TuyaEVChargerRebootButton as B

    button = _button(B, client=_Client(reboot_ok=False))
    with pytest.raises(HomeAssistantError, match="reboot_failed"):
        asyncio.run(button.async_press())
    assert button.refreshes == []


class _Controller:
    def __init__(self):
        self.rearmed = 0

    def rearm_start_watch(self):
        self.rearmed += 1


@pytest.mark.parametrize("name", ["TuyaEVChargerReadyToChargeButton", "TuyaEVChargerRebootButton"])
def test_both_presses_let_regulation_send_the_start_again(monkeypatch, name):
    """A press is how a user wakes a car that ignored the start; regulation must
    then be allowed to ask again, or the woken car has nothing to answer."""
    from tuya_ev_charger import button

    async def _no_wait(_seconds):
        return None

    monkeypatch.setattr(button.asyncio, "sleep", _no_wait)
    controller = _Controller()
    asyncio.run(_button(getattr(button, name), controller=controller).async_press())
    assert controller.rearmed == 1


def test_a_press_without_surplus_regulation_still_works():
    from tuya_ev_charger.button import TuyaEVChargerReadyToChargeButton as B

    client = _Client()
    asyncio.run(_button(B, client=client, controller=None).async_press())
    assert client.calls
