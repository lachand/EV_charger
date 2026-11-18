"""Persistent-connection mode: mimicking the Tuya app's held connection (#35).

Some Wi-Fi modules (dé wallbox 7.4 kW V2, fw 7.x) only refresh the metrics DP
for a persistent subscriber; a plain poll-per-connection reads a ~10-minute-old
cache. With `persistent_connection` on, the client keeps the socket open, runs a
listener thread that collects the pushed DP reports (tinytuya merges them into
the device status cache) and heartbeats the module, and polls read that cache.

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import asyncio
import queue
import time

import pytest

BASE_DPS = {"102": "{}", "106": "{}", "109": "IDLE", "140": False, "150": 16}
PUSHED_DPS = {"109": "WORKING", "150": 16, "102": '{"L1":[2300,32,7],"t":280,"p":7,"d":100,"e":1}'}


class _FakeDevice:
    """Stands in for tinytuya.Device: a push queue, a heartbeat counter, a cache."""

    def __init__(self, dps):
        self._dps = dict(dps)
        self._last_status: dict = {}
        self._have_status = False
        self.socket_persistent = False
        self.socket_timeout = None
        self.pushes: queue.Queue = queue.Queue()
        self.heartbeats = 0
        self.status_calls = 0
        self.set_calls: list[tuple[str, object]] = []
        self.closed = False

    # -- configuration no-ops -------------------------------------------------
    def set_socketRetryLimit(self, _n):
        pass

    def set_socketRetryDelay(self, _s):
        pass

    def set_socketPersistent(self, persist):
        self.socket_persistent = persist

    def set_socketTimeout(self, s):
        self.socket_timeout = s

    # -- reads ----------------------------------------------------------------
    def status(self):
        self.status_calls += 1
        self._have_status = True
        self._last_status = dict(self._dps)
        return {"dps": dict(self._dps)}

    def receive(self):
        """One pushed DP report; None on read timeout."""
        try:
            dps = self.pushes.get_nowait()
        except queue.Empty:
            return None
        self._have_status = True
        self._last_status.update(dps)  # tinytuya merges pushes into the cache
        return {"dps": dict(dps)}

    def cached_status(self, nowait=False):
        if not (self._have_status and self.socket_persistent and self._last_status):
            return None if nowait else self.status()
        return {"dps": dict(self._last_status)}

    # -- writes / lifecycle ---------------------------------------------------
    def heartbeat(self, nowait=True):
        self.heartbeats += 1
        return None

    def set_value(self, dp_id, value):
        self.set_calls.append((dp_id, value))
        self._dps[str(dp_id)] = value
        self._last_status[str(dp_id)] = value
        return {"dps": {str(dp_id): value}}

    def close(self):
        self.closed = True


def _client(monkeypatch, persistent, dps=BASE_DPS):
    from tuya_ev_charger import tuya_ev_charger as tem

    fake = _FakeDevice(dps)
    monkeypatch.setattr(tem.tinytuya, "Device", lambda **kwargs: fake)
    monkeypatch.setattr(tem, "LISTEN_HEARTBEAT_INTERVAL_S", 0.05)
    monkeypatch.setattr(tem, "LISTEN_RECEIVE_TIMEOUT_S", 0.05)
    client = tem.TuyaEVChargerClient(
        "dev", "1.2.3.4", "key", "3.5", persistent_connection=persistent
    )
    return client, fake


def test_persistent_mode_holds_the_socket_and_heartbeats(monkeypatch):
    client, fake = _client(monkeypatch, persistent=True)
    asyncio.run(client.async_connect())

    assert fake.socket_persistent is True
    assert fake.socket_timeout == 0.05
    assert client._listen_thread is not None and client._listen_thread.is_alive()

    time.sleep(0.3)
    asyncio.run(client.async_close())
    assert fake.heartbeats >= 2  # interval monkeypatched to 0.05s
    assert fake.closed is True


def test_polls_read_the_push_cache_not_status(monkeypatch):
    client, fake = _client(monkeypatch, persistent=True)
    asyncio.run(client.async_connect())

    # First poll: no cache yet, so one full status() seeds it.
    first = asyncio.run(client.async_get_metrics())
    assert first is not None and first.status == "idle"
    assert fake.status_calls == 1

    # A pushed DP report lands in the cache via the listener...
    fake.pushes.put(PUSHED_DPS)
    time.sleep(0.3)

    # ...and the next poll reads it without another status() request.
    second = asyncio.run(client.async_get_metrics())
    assert second is not None and second.status == "charging"
    assert second.current_l1 == pytest.approx(3.2)
    assert fake.status_calls == 1

    asyncio.run(client.async_close())


def test_commands_still_work_and_close_stops_the_listener(monkeypatch):
    client, fake = _client(monkeypatch, persistent=True)
    asyncio.run(client.async_connect())

    assert asyncio.run(client.async_set_charge_enabled(True)) is True
    assert fake.set_calls == [("140", True)]

    asyncio.run(client.async_close())
    assert client._listen_thread is None
    beats_after_close = fake.heartbeats
    time.sleep(0.2)
    assert fake.heartbeats == beats_after_close  # listener really stopped


def test_non_persistent_mode_keeps_polling_status(monkeypatch):
    client, fake = _client(monkeypatch, persistent=False)
    asyncio.run(client.async_connect())

    assert client._listen_thread is None
    assert fake.socket_persistent is False
    asyncio.run(client.async_get_metrics())
    asyncio.run(client.async_get_metrics())
    assert fake.status_calls == 2
    assert fake.heartbeats == 0
    asyncio.run(client.async_close())


def test_a_dead_socket_makes_the_next_poll_reconnect(monkeypatch):
    client, fake = _client(monkeypatch, persistent=True)
    asyncio.run(client.async_connect())
    time.sleep(0.1)

    def _boom():
        raise OSError("connection reset")

    fake.receive = _boom  # type: ignore[method-assign]
    time.sleep(0.3)
    assert client._device is None and client._needs_reconnect  # listener dropped it

    # Socket healthy again: the next read rebuilds the connection.
    monkeypatch.setattr(fake, "receive", lambda: None)
    monkeypatch.setattr(fake, "status", lambda: {"dps": dict(BASE_DPS)})
    dps = asyncio.run(client.async_get_raw_dps())
    assert dps == BASE_DPS
    assert client._device is fake  # rebuilt via the patched tinytuya.Device
    asyncio.run(client.async_close())
