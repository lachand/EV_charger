"""Tuya Cloud credential lookup.

Its error handling is what matters: `tinytuya.Cloud` does not raise on bad
credentials, it sets `.error` on the instance and then returns an error *dict*
from `getdevices()` instead of a list. Both were confirmed against the real
library. Missing either turns a clear "check your Access Secret" into a crash.
"""

from __future__ import annotations

import asyncio

import pytest


class _FakeCloud:
    """Mimics tinytuya.Cloud, including its habit of not raising."""

    def __init__(self, *, error=None, devices=None, raises=None):
        self._error = error
        self._devices = devices
        self._raises = raises
        if raises is not None:
            raise raises

    @property
    def error(self):
        return self._error

    def getdevices(self, *args, **kwargs):
        return self._devices


def _patch_cloud(monkeypatch, **kwargs):
    from tuya_ev_charger import cloud

    monkeypatch.setattr(cloud.tinytuya, "Cloud", lambda **_kw: _FakeCloud(**kwargs), raising=False)


def _fetch(**kwargs):
    from tuya_ev_charger.cloud import _sync_fetch_devices

    return _sync_fetch_devices("eu", "key", "secret", kwargs.pop("device_id", None))


def test_bad_credentials_become_a_clear_error(monkeypatch):
    """The constructor does not raise; it sets .error and carries on."""
    from tuya_ev_charger.cloud import TuyaCloudError

    _patch_cloud(
        monkeypatch,
        error={"Error": "Unable to Get Cloud Token", "Err": "911"},
    )
    with pytest.raises(TuyaCloudError, match="authentication failed"):
        _fetch()


def test_an_error_dict_is_not_mistaken_for_a_device_list(monkeypatch):
    """getdevices() returns a dict on failure, where a list is expected."""
    from tuya_ev_charger.cloud import TuyaCloudError

    _patch_cloud(monkeypatch, devices={"Error": "Permission denied", "Err": "1106"})
    with pytest.raises(TuyaCloudError, match="returned an error"):
        _fetch()


def test_a_raising_constructor_is_wrapped(monkeypatch):
    from tuya_ev_charger.cloud import TuyaCloudError

    _patch_cloud(monkeypatch, raises=TypeError("Tuya Cloud Key and Secret required"))
    with pytest.raises(TuyaCloudError, match="authentication failed"):
        _fetch()


def test_devices_without_an_id_are_dropped(monkeypatch):
    _patch_cloud(
        monkeypatch,
        devices=[
            {"id": "bf23", "key": "abc", "name": "Charger"},
            {"key": "orphan"},  # no id: unusable
            "not a dict",
        ],
    )
    assert _fetch() == [{"id": "bf23", "key": "abc", "name": "Charger"}]


def test_local_key_lookup_matches_on_device_id(monkeypatch):
    from tuya_ev_charger import cloud

    _patch_cloud(
        monkeypatch,
        devices=[
            {"id": "other", "key": "wrong"},
            {"id": "bf23", "key": "right"},
        ],
    )

    class _Hass:
        async def async_add_executor_job(self, func, *args):
            return func(*args)

    found = asyncio.run(cloud.async_fetch_local_key(_Hass(), "eu", "key", "secret", "bf23"))
    assert found == "right"

    missing = asyncio.run(cloud.async_fetch_local_key(_Hass(), "eu", "key", "secret", "absent"))
    assert missing is None


def test_a_hung_cloud_call_becomes_a_cloud_error(monkeypatch):
    """The executor job cannot be cancelled, but the caller must not wait on it."""
    import time

    from tuya_ev_charger import cloud

    monkeypatch.setattr(cloud, "CLOUD_TIMEOUT_S", 0.05)

    class _Hass:
        async def async_add_executor_job(self, func, *args):
            return await asyncio.to_thread(lambda: time.sleep(0.3))

    with pytest.raises(cloud.TuyaCloudError, match="in time"):
        asyncio.run(cloud.async_fetch_devices(_Hass(), "eu", "key", "secret"))


# --- the local_key lookup --------------------------------------------------------------


def _fetch_key(monkeypatch, devices, device_id="bf123"):
    from tuya_ev_charger import cloud

    async def _devices(_hass, region, key, secret, wanted):
        return devices

    monkeypatch.setattr(cloud, "async_fetch_devices", _devices)
    return asyncio.run(cloud.async_fetch_local_key(None, "eu", "k", "s", device_id))


def test_the_local_key_of_the_matching_device_is_returned(monkeypatch):
    devices = [{"id": "other", "key": "nope"}, {"id": " bf123 ", "key": " thekey "}]

    assert _fetch_key(monkeypatch, devices) == "thekey"


def test_a_device_the_account_does_not_have_gives_no_key(monkeypatch):
    assert _fetch_key(monkeypatch, [{"id": "other", "key": "nope"}]) is None


def test_a_device_with_a_blank_or_missing_key_gives_no_key(monkeypatch):
    assert _fetch_key(monkeypatch, [{"id": "bf123", "key": "  "}]) is None
    assert _fetch_key(monkeypatch, [{"id": "bf123"}]) is None


def test_a_listing_that_raises_becomes_a_cloud_error(monkeypatch):
    from tuya_ev_charger.cloud import TuyaCloudError

    class _Cloud:
        error = None

        def getdevices(self, *a, **kw):
            raise OSError("network down")

    from tuya_ev_charger import cloud

    monkeypatch.setattr(cloud.tinytuya, "Cloud", lambda **_kw: _Cloud(), raising=False)

    with pytest.raises(TuyaCloudError, match="listing failed"):
        cloud._sync_fetch_devices("eu", "k", "s", None)


def test_a_payload_that_is_neither_a_list_nor_an_error_dict_is_refused(monkeypatch):
    from tuya_ev_charger import cloud
    from tuya_ev_charger.cloud import TuyaCloudError

    class _Cloud:
        error = None

        def getdevices(self, *a, **kw):
            return "garbage"

    monkeypatch.setattr(cloud.tinytuya, "Cloud", lambda **_kw: _Cloud(), raising=False)

    with pytest.raises(TuyaCloudError, match="Unexpected"):
        cloud._sync_fetch_devices("eu", "k", "s", None)


def test_devices_without_an_id_or_that_are_not_dicts_are_dropped(monkeypatch):
    from tuya_ev_charger import cloud

    class _Cloud:
        error = None

        def getdevices(self, *a, **kw):
            return [{"id": "a"}, {"name": "no id"}, "text", {"id": ""}]

    monkeypatch.setattr(cloud.tinytuya, "Cloud", lambda **_kw: _Cloud(), raising=False)

    assert cloud._sync_fetch_devices("eu", "k", "s", None) == [{"id": "a"}]
