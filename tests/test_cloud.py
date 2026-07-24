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
