"""The config flow, step by step: setup paths, failures, reauth, reconfigure, DHCP.

The flow's own logic is real; Home Assistant's flow plumbing (`async_show_form`,
`async_create_entry`, the unique-id bookkeeping) is replaced by a small recorder so
a step's outcome can be read straight off the result. The network is faked at the
two seams the flow itself uses: `_async_validate_input` for the steps, and the
client for `_async_validate_input` itself.

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import asyncio
import types

import pytest


class _Aborted(Exception):
    def __init__(self, reason, updates=None):
        super().__init__(reason)
        self.reason = reason
        self.updates = updates


def _flow(monkeypatch, *, configured=(), entry=None):
    """A real flow whose Home Assistant plumbing records what it is asked to do."""
    from tuya_ev_charger import config_flow as cf

    flow = cf.TuyaEVChargerConfigFlow()
    flow.hass = types.SimpleNamespace()
    flow.unique_id = None

    async def _set_unique_id(unique_id):
        flow.unique_id = unique_id

    def _abort_if_configured(updates=None):
        if flow.unique_id in configured:
            raise _Aborted("already_configured", updates)

    flow.async_set_unique_id = _set_unique_id
    flow._abort_if_unique_id_configured = _abort_if_configured
    flow.async_show_form = lambda **kw: {"type": "form", **kw}
    flow.async_create_entry = lambda **kw: {"type": "create_entry", **kw}
    flow.async_abort = lambda **kw: {"type": "abort", **kw}
    flow.async_update_reload_and_abort = lambda target, data: {
        "type": "update_reload_and_abort",
        "entry": target,
        "data": data,
    }
    flow._get_reauth_entry = lambda: entry
    flow._get_reconfigure_entry = lambda: entry
    return flow


def _validate_raising(monkeypatch, error=None):
    """Make `_async_validate_input` succeed, or raise `error`."""
    from tuya_ev_charger import config_flow as cf

    calls: list[dict] = []

    async def _validate(_hass, data):
        calls.append(dict(data))
        if error is not None:
            raise error
        return {"title": "Tuya EV Charger Local"}

    monkeypatch.setattr(cf, "_async_validate_input", _validate)
    return calls


CREDENTIALS = {
    "host": "192.168.1.50",
    "device_id": "bf123",
    "local_key": "secret",
    "protocol_version": "3.5",
    "charger_profile": "generic_v1",
}


# --- the first screen routes to the right path ---------------------------------


@pytest.mark.parametrize(
    ("mode", "expected_step"),
    [("scan", "scan"), ("cloud", "cloud"), ("manual", "credentials")],
)
def test_the_first_screen_routes_by_mode(monkeypatch, mode, expected_step):
    from tuya_ev_charger import config_flow as cf

    async def _one_device(_hass, **_kw):
        return {"bf123": {"ip": "192.168.1.50"}}

    monkeypatch.setattr(cf, "async_scan_devices_by_id", _one_device)
    flow = _flow(monkeypatch)

    result = asyncio.run(flow.async_step_user({"mode": mode}))

    assert result["step_id"] == expected_step


def test_the_first_screen_is_a_form_until_a_mode_is_chosen(monkeypatch):
    flow = _flow(monkeypatch)

    assert asyncio.run(flow.async_step_user())["step_id"] == "user"


# --- scan ----------------------------------------------------------------------


def test_scan_with_nothing_found_falls_back_to_manual_entry_with_a_hint(monkeypatch):
    from tuya_ev_charger import config_flow as cf

    async def _none(_hass, **_kw):
        return {}

    monkeypatch.setattr(cf, "async_scan_devices_by_id", _none)
    flow = _flow(monkeypatch)

    result = asyncio.run(flow.async_step_scan())

    assert result["step_id"] == "credentials"
    assert result["errors"] == {"base": "no_devices_found"}


def test_scan_lists_what_it_found_and_prefills_the_chosen_device(monkeypatch):
    from tuya_ev_charger import config_flow as cf

    async def _found(_hass, **_kw):
        return {"bf123": {"ip": "192.168.1.50", "version": "3.4", "mac": "AA:BB:CC:DD:EE:FF"}}

    monkeypatch.setattr(cf, "async_scan_devices_by_id", _found)
    flow = _flow(monkeypatch)

    listing = asyncio.run(flow.async_step_scan())
    assert listing["step_id"] == "scan"

    result = asyncio.run(flow.async_step_scan({"device": "bf123"}))

    assert result["step_id"] == "credentials"
    assert flow._prefill == {
        "host": "192.168.1.50",
        "device_id": "bf123",
        "protocol_version": "3.4",
    }
    assert flow._device_meta == {"mac": "aa:bb:cc:dd:ee:ff"}


def test_choosing_manual_in_the_scan_list_clears_any_prefill(monkeypatch):
    flow = _flow(monkeypatch)
    flow._prefill = {"host": "stale"}

    result = asyncio.run(flow.async_step_scan({"device": "__manual__"}))

    assert result["step_id"] == "credentials"
    assert flow._prefill == {}


# --- credentials: create the entry, or say why not ----------------------------


def test_valid_credentials_create_the_entry_with_the_discovered_mac(monkeypatch):
    calls = _validate_raising(monkeypatch)
    flow = _flow(monkeypatch)
    flow._device_meta = {"mac": "aa:bb:cc:dd:ee:ff"}

    result = asyncio.run(flow.async_step_credentials(dict(CREDENTIALS)))

    assert result["type"] == "create_entry"
    assert result["title"] == "Tuya EV Charger Local"
    assert result["data"] == {**CREDENTIALS, "mac": "aa:bb:cc:dd:ee:ff"}
    assert flow.unique_id == "bf123"
    assert len(calls) == 1


def test_a_charger_already_configured_aborts_before_any_network_call(monkeypatch):
    calls = _validate_raising(monkeypatch)
    flow = _flow(monkeypatch, configured={"bf123"})

    with pytest.raises(_Aborted) as excinfo:
        asyncio.run(flow.async_step_credentials(dict(CREDENTIALS)))

    assert excinfo.value.reason == "already_configured"
    assert calls == []


@pytest.mark.parametrize(
    ("error_name", "expected"),
    [
        ("CannotConnectError", "cannot_connect"),
        ("InvalidCredentialsError", "invalid_credentials"),
        ("ConnectionRefusedByChargerError", "connection_refused"),
    ],
)
def test_each_connection_failure_gets_its_own_message(monkeypatch, error_name, expected):
    from tuya_ev_charger import config_flow as cf

    _validate_raising(monkeypatch, getattr(cf, error_name)())
    flow = _flow(monkeypatch)

    result = asyncio.run(flow.async_step_credentials(dict(CREDENTIALS)))

    assert result["type"] == "form"
    assert result["step_id"] == "credentials"
    assert result["errors"] == {"base": expected}


def test_an_unexpected_failure_is_reported_as_unknown_not_raised(monkeypatch):
    _validate_raising(monkeypatch, ValueError("boom"))
    flow = _flow(monkeypatch)

    result = asyncio.run(flow.async_step_credentials(dict(CREDENTIALS)))

    assert result["errors"] == {"base": "unknown"}


# --- _async_validate_input: what a failed read means ---------------------------


class _Client:
    def __init__(self, *, metrics, fault=None, **_kw):
        self._metrics = metrics
        self._fault = fault

    async def async_connect(self):
        pass

    async def async_get_metrics(self):
        return self._metrics

    async def async_classify_fault(self):
        return self._fault


def _patch_client(monkeypatch, *, metrics, fault=None):
    from tuya_ev_charger import config_flow as cf

    monkeypatch.setattr(
        cf, "TuyaEVChargerClient", lambda **kw: _Client(metrics=metrics, fault=fault, **kw)
    )
    return cf


def test_a_successful_read_yields_the_title_without_an_ip(monkeypatch):
    cf = _patch_client(monkeypatch, metrics=object())

    info = asyncio.run(cf._async_validate_input(None, CREDENTIALS))

    assert info == {"title": cf.DEFAULT_NAME}
    assert "192.168" not in info["title"]


@pytest.mark.parametrize(
    ("fault", "error_name"),
    [
        ("refused", "ConnectionRefusedByChargerError"),
        ("undecryptable", "InvalidCredentialsError"),
        ("unreachable", "CannotConnectError"),
        ("ok", "CannotConnectError"),
    ],
)
def test_a_failed_read_is_classified_so_the_user_blames_the_right_thing(
    monkeypatch, fault, error_name
):
    from tuya_ev_charger.const import ConnectionFault

    cf = _patch_client(monkeypatch, metrics=None, fault=ConnectionFault(fault))

    with pytest.raises(getattr(cf, error_name)):
        asyncio.run(cf._async_validate_input(None, CREDENTIALS))


# --- cloud ---------------------------------------------------------------------

CLOUD_FORM = {
    "cloud_api_key": " key ",
    "cloud_api_secret": " secret ",
    "cloud_region": "eu",
}


def test_cloud_credentials_are_trimmed_and_the_account_devices_listed(monkeypatch):
    from tuya_ev_charger import config_flow as cf

    seen = {}

    async def _fetch(_hass, region, key, secret, device_id):
        seen.update(region=region, key=key, secret=secret, device_id=device_id)
        return [{"id": "bf123", "name": "Garage", "key": "abc"}]

    monkeypatch.setattr(cf, "async_fetch_devices", _fetch)
    flow = _flow(monkeypatch)

    result = asyncio.run(flow.async_step_cloud(dict(CLOUD_FORM)))

    assert seen == {"region": "eu", "key": "key", "secret": "secret", "device_id": None}
    assert result["step_id"] == "cloud_device"


def test_a_cloud_authentication_failure_is_shown_on_the_form(monkeypatch):
    from tuya_ev_charger import config_flow as cf

    async def _fail(*_a, **_kw):
        raise cf.TuyaCloudError("nope")

    monkeypatch.setattr(cf, "async_fetch_devices", _fail)
    flow = _flow(monkeypatch)

    result = asyncio.run(flow.async_step_cloud(dict(CLOUD_FORM)))

    assert result["step_id"] == "cloud"
    assert result["errors"] == {"base": "cloud_auth_failed"}


def test_an_empty_cloud_account_is_reported(monkeypatch):
    from tuya_ev_charger import config_flow as cf

    async def _empty(*_a, **_kw):
        return []

    monkeypatch.setattr(cf, "async_fetch_devices", _empty)
    flow = _flow(monkeypatch)

    result = asyncio.run(flow.async_step_cloud(dict(CLOUD_FORM)))

    assert result["errors"] == {"base": "cloud_no_devices"}


def test_choosing_a_cloud_device_takes_the_key_from_the_cloud_and_the_ip_from_the_lan(
    monkeypatch,
):
    from tuya_ev_charger import config_flow as cf

    async def _lan(_hass, **_kw):
        return {"bf123": {"ip": "10.0.0.7", "version": "3.5", "mac": "AA:BB:CC:00:11:22"}}

    monkeypatch.setattr(cf, "async_scan_devices_by_id", _lan)
    flow = _flow(monkeypatch)
    flow._cloud_devices = {"bf123": {"id": "bf123", "key": "cloudkey", "ip": "1.1.1.1"}}
    flow._cloud_credentials = {"cloud_api_key": "k", "cloud_api_secret": "s", "cloud_region": "eu"}

    result = asyncio.run(flow.async_step_cloud_device({"device": "bf123"}))

    assert result["step_id"] == "credentials"
    assert flow._prefill["host"] == "10.0.0.7"
    assert flow._prefill["local_key"] == "cloudkey"
    # The cloud credentials ride along so a later key rotation can refetch them.
    assert flow._device_meta["cloud_api_key"] == "k"
    assert flow._device_meta["mac"] == "aa:bb:cc:00:11:22"


# --- reauth --------------------------------------------------------------------


def _entry():
    return types.SimpleNamespace(
        data=dict(CREDENTIALS),
        options={},
        entry_id="e1",
        unique_id="bf123",
        domain="tuya_ev_charger",
    )


def test_reauth_asks_only_for_the_local_key(monkeypatch):
    flow = _flow(monkeypatch, entry=_entry())

    result = asyncio.run(flow.async_step_reauth(_entry().data))

    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"] == {"host": "192.168.1.50"}


def test_reauth_with_a_working_key_updates_the_entry_and_keeps_the_rest(monkeypatch):
    calls = _validate_raising(monkeypatch)
    entry = _entry()
    flow = _flow(monkeypatch, entry=entry)

    result = asyncio.run(flow.async_step_reauth_confirm({"local_key": "newkey"}))

    assert result["type"] == "update_reload_and_abort"
    assert result["data"] == {**CREDENTIALS, "local_key": "newkey"}
    assert calls[0]["local_key"] == "newkey"


def test_reauth_with_a_wrong_key_stays_on_the_form(monkeypatch):
    from tuya_ev_charger import config_flow as cf

    _validate_raising(monkeypatch, cf.InvalidCredentialsError())
    flow = _flow(monkeypatch, entry=_entry())

    result = asyncio.run(flow.async_step_reauth_confirm({"local_key": "still-wrong"}))

    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_credentials"}


# --- reconfigure ---------------------------------------------------------------


def test_reconfigure_overwrites_the_submitted_fields_and_keeps_the_others(monkeypatch):
    _validate_raising(monkeypatch)
    entry = _entry()
    entry.data["mac"] = "aa:bb:cc:dd:ee:ff"
    flow = _flow(monkeypatch, entry=entry)

    result = asyncio.run(flow.async_step_reconfigure({**CREDENTIALS, "host": "192.168.1.99"}))

    assert result["type"] == "update_reload_and_abort"
    assert result["data"]["host"] == "192.168.1.99"
    assert result["data"]["mac"] == "aa:bb:cc:dd:ee:ff"


def test_reconfigure_does_not_save_what_it_could_not_validate(monkeypatch):
    from tuya_ev_charger import config_flow as cf

    _validate_raising(monkeypatch, cf.CannotConnectError())
    flow = _flow(monkeypatch, entry=_entry())

    result = asyncio.run(flow.async_step_reconfigure(dict(CREDENTIALS)))

    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}


# --- DHCP ----------------------------------------------------------------------


class _DeviceRegistry:
    def __init__(self, device):
        self._device = device

    def async_get_device(self, connections):
        return self._device


def _dhcp(monkeypatch, *, device, entries):
    from tuya_ev_charger import config_flow as cf

    monkeypatch.setattr(cf.dr, "async_get", lambda _hass: _DeviceRegistry(device))
    flow = _flow(monkeypatch, configured={"bf123"})
    flow.hass = types.SimpleNamespace(
        config_entries=types.SimpleNamespace(async_get_entry=lambda entry_id: entries.get(entry_id))
    )
    return flow


def _lease(ip="192.168.1.77"):
    return types.SimpleNamespace(macaddress="AABBCCDDEEFF", ip=ip)


def test_a_new_lease_for_a_known_charger_updates_its_host(monkeypatch):
    device = types.SimpleNamespace(config_entries={"e1"})
    flow = _dhcp(monkeypatch, device=device, entries={"e1": _entry()})

    with pytest.raises(_Aborted) as excinfo:
        asyncio.run(flow.async_step_dhcp(_lease()))

    assert excinfo.value.reason == "already_configured"
    assert excinfo.value.updates == {"host": "192.168.1.77"}


def test_a_lease_for_an_unknown_device_is_not_ours(monkeypatch):
    flow = _dhcp(monkeypatch, device=None, entries={})

    result = asyncio.run(flow.async_step_dhcp(_lease()))

    assert result == {"type": "abort", "reason": "not_tuya_ev_charger"}


def test_a_lease_for_a_device_of_another_integration_is_ignored(monkeypatch):
    other = _entry()
    other.domain = "something_else"
    device = types.SimpleNamespace(config_entries={"e9"})
    flow = _dhcp(monkeypatch, device=device, entries={"e9": other})

    result = asyncio.run(flow.async_step_dhcp(_lease()))

    assert result["reason"] == "not_tuya_ev_charger"


# --- the remaining branches: unusable MACs, failures on reauth and reconfigure --------


@pytest.mark.parametrize("raw", [None, "", "   ", "AABBCCDDEEFF", 12345])
def test_a_scan_mac_without_separators_is_not_trusted(raw):
    from tuya_ev_charger.config_flow import _format_scan_mac

    assert _format_scan_mac(raw) is None


def test_a_scan_mac_is_normalised():
    from tuya_ev_charger.config_flow import _format_scan_mac

    assert _format_scan_mac(" AA:BB:CC:DD:EE:FF ") == "aa:bb:cc:dd:ee:ff"


def test_the_options_flow_is_built_for_the_entry_it_edits():
    from tuya_ev_charger.config_flow import TuyaEVChargerConfigFlow
    from tuya_ev_charger.options_flow import TuyaEVChargerOptionsFlow

    entry = _entry()

    flow = TuyaEVChargerConfigFlow.async_get_options_flow(entry)

    assert isinstance(flow, TuyaEVChargerOptionsFlow)
    assert flow._config_entry is entry


@pytest.mark.parametrize(
    ("error_name", "expected"),
    [
        ("CannotConnectError", "cannot_connect"),
        ("ConnectionRefusedByChargerError", "connection_refused"),
    ],
)
def test_reauth_names_each_failure(monkeypatch, error_name, expected):
    from tuya_ev_charger import config_flow as cf

    _validate_raising(monkeypatch, getattr(cf, error_name)())
    flow = _flow(monkeypatch, entry=_entry())

    result = asyncio.run(flow.async_step_reauth_confirm({"local_key": "k"}))

    assert result["errors"] == {"base": expected}


def test_reauth_reports_an_unexpected_failure_as_unknown(monkeypatch):
    _validate_raising(monkeypatch, ValueError("boom"))
    flow = _flow(monkeypatch, entry=_entry())

    result = asyncio.run(flow.async_step_reauth_confirm({"local_key": "k"}))

    assert result["errors"] == {"base": "unknown"}


@pytest.mark.parametrize(
    ("error_name", "expected"),
    [
        ("InvalidCredentialsError", "invalid_credentials"),
        ("ConnectionRefusedByChargerError", "connection_refused"),
    ],
)
def test_reconfigure_names_each_failure(monkeypatch, error_name, expected):
    from tuya_ev_charger import config_flow as cf

    _validate_raising(monkeypatch, getattr(cf, error_name)())
    flow = _flow(monkeypatch, entry=_entry())

    result = asyncio.run(flow.async_step_reconfigure(dict(CREDENTIALS)))

    assert result["errors"] == {"base": expected}


def test_reconfigure_reports_an_unexpected_failure_as_unknown(monkeypatch):
    _validate_raising(monkeypatch, ValueError("boom"))
    flow = _flow(monkeypatch, entry=_entry())

    result = asyncio.run(flow.async_step_reconfigure(dict(CREDENTIALS)))

    assert result["errors"] == {"base": "unknown"}


def test_a_lease_for_a_device_whose_entry_has_no_identity_is_skipped(monkeypatch):
    """An entry with neither a unique id nor a device id cannot be matched to a lease."""
    nameless = _entry()
    nameless.unique_id = None
    nameless.data = {}
    device = types.SimpleNamespace(config_entries={"e1"})
    flow = _dhcp(monkeypatch, device=device, entries={"e1": nameless})

    result = asyncio.run(flow.async_step_dhcp(_lease()))

    assert result["reason"] == "not_tuya_ev_charger"
