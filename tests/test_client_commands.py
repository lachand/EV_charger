"""The command path: `TuyaEVChargerClient._async_send_command` and read-back verify.

Field reports #38 and #39 ("Command rejected for DP 140: None", the charge switch
falling straight back to off) came from this path treating tinytuya's *bare ACK*
(`set_value` returning `None`) as a rejection. A `None` is the normal reply to a
write-only DP on protocol 3.4/3.5; only a dict carrying an `"Error"` key is a real
failure. These tests pin that distinction, the read-back short-circuit, and the
single-connection lock.

The integration is only importable once the session-scoped conftest fixture has
put `custom_components` on the path, so every import here is inside a function.
"""

from __future__ import annotations

import asyncio

import pytest


class _FakeDevice:
    """Stands in for tinytuya.Device. `set_value` / `status` are sync (run in a thread)."""

    def __init__(self, *, set_result=None, status_result=None):
        self._set_result = set_result
        self._status_result = status_result if status_result is not None else {"dps": {}}
        self.set_calls: list[tuple[str, object]] = []
        self.status_calls = 0

    def set_value(self, dp_id, value):
        self.set_calls.append((dp_id, value))
        if callable(self._set_result):
            return self._set_result(dp_id, value)
        return self._set_result

    def status(self):
        self.status_calls += 1
        result = self._status_result
        return result(self.status_calls) if callable(result) else result

    def close(self):  # pragma: no cover - only the unload path calls this
        pass


def _client(device: _FakeDevice):
    from tuya_ev_charger.tuya_ev_charger import TuyaEVChargerClient

    client = TuyaEVChargerClient("dev", "1.2.3.4", "key", "3.5")
    client._device = device
    return client


@pytest.fixture(autouse=True)
def _no_verify_sleep(monkeypatch):
    import tuya_ev_charger.tuya_ev_charger as tem

    monkeypatch.setattr(tem, "COMMAND_VERIFY_DELAY_S", 0)


# --- a bare ACK is not a rejection ---------------------------------------------


def test_none_from_set_value_is_accepted_when_the_dp_is_not_echoed():
    """The depow 3.5 kW never reports DP 140, so a bare ACK is all we get."""
    device = _FakeDevice(set_result=None, status_result={"dps": {"109": "WORKING"}})
    client = _client(device)

    assert asyncio.run(client.async_set_charge_enabled(True)) is True
    assert device.set_calls == [("140", True)]


def test_error_dict_is_a_genuine_failure():
    device = _FakeDevice(
        set_result={"Error": "Network Error: Unable to Connect", "Err": "901", "Payload": None}
    )
    client = _client(device)

    assert asyncio.run(client.async_set_charge_enabled(True)) is False


def test_an_echoed_match_succeeds():
    device = _FakeDevice(set_result={"dps": {"150": 10}}, status_result={"dps": {"150": 10}})
    client = _client(device)

    assert asyncio.run(client.async_set_charge_current(10)) is True


def test_an_echoed_mismatch_fails():
    """DP present in every read but never the value we wrote (140 stuck at False)."""
    device = _FakeDevice(set_result={"dps": {"140": False}}, status_result={"dps": {"140": False}})
    client = _client(device)

    assert asyncio.run(client.async_set_charge_enabled(True)) is False


def test_a_numeric_dp_is_verified_by_value_not_truthiness():
    """DP 101 = 200 must not "match" a read-back of 300 just because both are truthy."""
    device = _FakeDevice(set_result=None, status_result={"dps": {"101": 300}})
    client = _client(device)

    assert asyncio.run(client.async_set_work_state(200)) is False
    assert asyncio.run(client.async_set_work_state(300)) is True


# --- read-back does not burn the whole retry budget on a DP that never appears --


def test_verify_stops_after_two_reads_without_the_dp():
    device = _FakeDevice(set_result=None, status_result={"dps": {"101": 200}})
    client = _client(device)

    assert asyncio.run(client.async_set_charge_enabled(True)) is True
    assert device.status_calls == 2  # not COMMAND_VERIFY_RETRIES


# --- reboot (verify=False) ----------------------------------------------------


def test_reboot_sends_one_command_when_the_charger_just_acks():
    device = _FakeDevice(set_result=None)
    client = _client(device)

    assert asyncio.run(client.async_reboot()) is True
    assert device.set_calls == [("142", True)]


def test_reboot_falls_back_through_payloads_only_on_a_real_error():
    device = _FakeDevice(set_result={"Error": "Timeout Waiting for Device", "Err": "902"})
    client = _client(device)

    assert asyncio.run(client.async_reboot()) is False
    assert [v for _, v in device.set_calls] == [True, 1, "1"]


# --- the single-connection lock ---------------------------------------------


def test_a_command_waits_for_an_in_flight_read():
    device = _FakeDevice(set_result=None, status_result={"dps": {}})
    client = _client(device)

    async def _run():
        async with client._io_lock:
            task = asyncio.create_task(client.async_set_charge_enabled(True))
            await asyncio.sleep(0.02)
            assert not task.done()  # blocked on the lock the poll would hold
        return await task

    assert asyncio.run(_run()) is True
