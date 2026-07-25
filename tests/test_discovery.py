"""The UDP discovery scan.

Its one job beyond wrapping tinytuya is filtering: a scan result without an IP is
unusable, and returning it would send the recovery logic to probe a bad address.
The `wantids`/`byID` behaviour is what a past bug turned on (probing a neighbour
device), so the call shape is pinned too.
"""

from __future__ import annotations

import asyncio


def _patch_scanner(monkeypatch, result=None, *, raises=None):
    from tuya_ev_charger import discovery

    captured = {}

    def _devices(**kwargs):
        captured.update(kwargs)
        if raises is not None:
            raise raises
        return result if result is not None else {}

    monkeypatch.setattr(discovery.scanner, "devices", _devices, raising=False)
    return captured


def _scan(monkeypatch, result=None, *, raises=None, wantids=None):
    captured = _patch_scanner(monkeypatch, result, raises=raises)

    class _Hass:
        async def async_add_executor_job(self, func, *args):
            return func(*args)

    from tuya_ev_charger.discovery import async_scan_devices_by_id

    found = asyncio.run(async_scan_devices_by_id(_Hass(), 5, wantids))
    return found, captured


def test_devices_without_an_ip_are_dropped(monkeypatch):
    """A result with no IP would send recovery to probe nowhere."""
    result = {
        "bf23": {"ip": "192.168.1.5", "gwId": "bf23"},
        "orphan": {"gwId": "orphan"},  # no ip
        "notdict": "garbage",
    }
    found, _ = _scan(monkeypatch, result)
    assert set(found) == {"bf23"}


def test_the_scan_keys_by_device_id_and_waits_for_our_charger(monkeypatch):
    """byID keys the result by gwId (an earlier bug keyed by IP), and wantids is
    what makes it wait for our charger rather than the first to announce."""
    _, captured = _scan(monkeypatch, {}, wantids=["bf23"])
    assert captured["byID"] is True
    assert captured["wantids"] == ["bf23"]


def test_a_scan_failure_returns_empty_rather_than_raising(monkeypatch):
    """A failed scan must not take the poll loop down with it."""
    found, _ = _scan(monkeypatch, raises=OSError("network down"))
    assert found == {}
