"""The client's connection management and its defensive parsing.

`test_client_commands.py` covers the write path; this covers the rest of what
`TuyaEVChargerClient` does around it: building and replacing the socket, telling
the three failure modes apart (nothing there / port refuses / payload will not
decrypt), the range checks on writes, and what happens to a malformed status
payload. tinytuya and the socket module are replaced by recorders.

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import asyncio
import types
from typing import ClassVar

import pytest


class _Device:
    """Stands in for tinytuya.Device and records how it was built and used."""

    instances: ClassVar[list[_Device]] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = 0
        self.config: dict = {}
        self.status_result = {"dps": {"109": "IDLE"}}
        _Device.instances.append(self)

    def set_socketTimeout(self, value):
        self.config["timeout"] = value

    def set_socketRetryLimit(self, value):
        self.config["retry"] = value

    def set_socketRetryDelay(self, value):
        self.config["delay"] = value

    def close(self):
        self.closed += 1

    def status(self):
        result = self.status_result
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(autouse=True)
def _fake_tinytuya(monkeypatch):
    import tuya_ev_charger.tuya_ev_charger as tem

    _Device.instances = []
    monkeypatch.setattr(tem.tinytuya, "Device", _Device)
    return tem


def _client(**kwargs):
    from tuya_ev_charger.tuya_ev_charger import TuyaEVChargerClient

    return TuyaEVChargerClient("dev", "1.2.3.4", "key", "3.5", **kwargs)


# --- building and replacing the connection -------------------------------------


def test_connect_builds_a_device_with_fail_fast_socket_settings():
    from tuya_ev_charger.tuya_ev_charger import (
        SOCKET_RETRY_DELAY_S,
        SOCKET_RETRY_LIMIT,
        SOCKET_TIMEOUT_S,
    )

    client = _client()

    asyncio.run(client.async_connect())

    (device,) = _Device.instances
    assert device.kwargs == {
        "dev_id": "dev",
        "address": "1.2.3.4",
        "local_key": "key",
        "version": "3.5",
    }
    assert device.config == {
        "timeout": SOCKET_TIMEOUT_S,
        "retry": SOCKET_RETRY_LIMIT,
        "delay": SOCKET_RETRY_DELAY_S,
    }


def test_reconnecting_closes_the_previous_socket_first():
    """The charger has one slot; a lingering socket would lock our own next one out."""
    client = _client()

    asyncio.run(client.async_connect())
    asyncio.run(client.async_connect())

    first, second = _Device.instances
    assert first.closed == 1
    assert second.closed == 0


def test_a_new_address_reconnects_there():
    client = _client()
    asyncio.run(client.async_connect())

    asyncio.run(client.async_update_host("9.9.9.9"))

    assert client.host == "9.9.9.9"
    assert _Device.instances[-1].kwargs["address"] == "9.9.9.9"


def test_a_rotated_key_reconnects_with_it():
    client = _client()
    asyncio.run(client.async_connect())

    asyncio.run(client.async_update_local_key("newkey"))

    assert client.local_key == "newkey"
    assert _Device.instances[-1].kwargs["local_key"] == "newkey"


def test_close_before_connect_is_harmless():
    asyncio.run(_client().async_close())


def test_using_the_client_before_connecting_is_a_clear_error():
    with pytest.raises(RuntimeError, match="async_connect"):
        asyncio.run(_client().async_get_raw_dps())


def test_the_identity_the_client_was_built_with_is_exposed():
    client = _client()

    assert (client.device_id, client.host, client.local_key) == ("dev", "1.2.3.4", "key")
    assert client.dp_profile


# --- telling the failure modes apart -------------------------------------------


class _Socket:
    outcome: object = None

    def __init__(self, *_a, **_kw):
        self.closed = False

    def settimeout(self, _value):
        pass

    def connect(self, _address):
        if isinstance(_Socket.outcome, Exception):
            raise _Socket.outcome

    def close(self):
        self.closed = True


@pytest.fixture
def _socket(monkeypatch, _fake_tinytuya):
    # Replace the module reference the client holds, not `socket.socket` itself:
    # asyncio uses the real one for its own event loop.
    monkeypatch.setattr(
        _fake_tinytuya,
        "socket",
        types.SimpleNamespace(socket=_Socket, AF_INET=2, SOCK_STREAM=1),
    )
    return _Socket


@pytest.mark.parametrize(
    ("outcome", "fault"),
    [
        (None, "ok"),
        (ConnectionRefusedError(), "refused"),
        (TimeoutError(), "unreachable"),
        (OSError("no route"), "unreachable"),
    ],
)
def test_the_port_probe_classifies_what_the_socket_does(_socket, outcome, fault):
    from tuya_ev_charger.const import ConnectionFault

    _socket.outcome = outcome

    assert asyncio.run(_client()._async_probe_port()) == ConnectionFault(fault)


def test_tcp_reachability_is_true_only_when_the_port_accepts(_socket):
    _socket.outcome = None
    assert asyncio.run(_client().async_tcp_reachable()) is True

    _socket.outcome = ConnectionRefusedError()
    assert asyncio.run(_client().async_tcp_reachable()) is False


def test_a_refused_port_is_reported_without_reading_the_payload(_socket):
    from tuya_ev_charger.const import ConnectionFault

    _socket.outcome = ConnectionRefusedError()

    assert asyncio.run(_client().async_classify_fault()) == ConnectionFault.REFUSED
    assert _Device.instances == []


def test_an_answering_port_with_a_readable_payload_is_not_a_fault(_socket):
    from tuya_ev_charger.const import ConnectionFault

    _socket.outcome = None

    assert asyncio.run(_client().async_classify_fault()) == ConnectionFault.OK


def test_an_answering_port_whose_payload_will_not_decrypt_points_at_the_key(_socket):
    from tuya_ev_charger.const import ConnectionFault

    _socket.outcome = None
    client = _client()
    original = _Device.__init__

    def _bad(self, **kw):
        original(self, **kw)
        self.status_result = {"Error": "Check device key or version", "Err": "914"}

    _Device.__init__ = _bad
    try:
        assert asyncio.run(client.async_classify_fault()) == ConnectionFault.UNDECRYPTABLE
    finally:
        _Device.__init__ = original


# --- probing another address ---------------------------------------------------


def _probe(result):
    original = _Device.__init__

    def _init(self, **kw):
        original(self, **kw)
        self.status_result = result

    _Device.__init__ = _init
    return original


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"dps": {"109": "IDLE"}}, True),
        ({"dps": {}}, False),
        ({"Error": "nope"}, False),
        ({"dps": "not-a-dict"}, False),
        ("not-a-dict", False),
        (OSError("boom"), False),
    ],
)
def test_a_probe_succeeds_only_on_a_real_decrypted_status(result, expected):
    original = _probe(result)
    try:
        assert asyncio.run(_client().async_probe_host("5.5.5.5")) is expected
    finally:
        _Device.__init__ = original

    (device,) = _Device.instances
    assert device.kwargs["address"] == "5.5.5.5"
    # Never leave the probe holding the charger's single local connection.
    assert device.closed == 1


def test_a_probe_leaves_the_live_connection_alone():
    client = _client()
    asyncio.run(client.async_connect())
    live = _Device.instances[0]

    asyncio.run(client.async_probe_host("5.5.5.5"))

    assert live.closed == 0
    assert client.host == "1.2.3.4"


# --- malformed payloads --------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [None, "garbage", ["dps"], {"Error": "x"}, {"dps": "not-a-dict"}, {"dps": ["list"]}],
)
def test_a_malformed_status_payload_reads_as_no_data(payload):
    client = _client()
    asyncio.run(client.async_connect())
    _Device.instances[0].status_result = payload

    assert asyncio.run(client.async_get_raw_dps()) is None
    assert asyncio.run(client.async_get_metrics()) is None


def test_a_status_without_a_dps_key_is_an_empty_reading():
    client = _client()
    asyncio.run(client.async_connect())
    _Device.instances[0].status_result = {}

    assert asyncio.run(client.async_get_raw_dps()) == {}


# --- range checks on writes ----------------------------------------------------


@pytest.mark.parametrize("amperage", [0, 5, 33, 100])
def test_a_current_outside_the_supported_range_is_rejected_before_any_write(amperage):
    client = _client()

    with pytest.raises(ValueError, match="out of supported range"):
        asyncio.run(client.async_set_charge_current(amperage))


def test_the_chargers_own_hardware_limit_caps_the_range():
    client = _client()

    with pytest.raises(ValueError, match="6-16A"):
        asyncio.run(client.async_set_charge_current(20, max_current=16))


def test_an_unknown_plug_in_action_is_rejected():
    with pytest.raises(ValueError, match="Unsupported plug-in action"):
        asyncio.run(_client().async_set_plug_in_action("explode"))


def test_every_known_plug_in_action_maps_to_a_write(monkeypatch):
    from tuya_ev_charger.charger_metrics import PLUG_IN_ACTION_MAP

    client = _client()
    sent = []

    async def _send(dp_id, value, verify=True):
        sent.append(value)
        return True

    client._async_send_command = _send

    for name in PLUG_IN_ACTION_MAP.values():
        assert asyncio.run(client.async_set_plug_in_action(name)) is True

    assert sent == list(PLUG_IN_ACTION_MAP)


def test_a_reboot_tries_each_payload_shape_until_one_is_accepted():
    client = _client()
    sent = []

    async def _send(dp_id, value, verify=True):
        sent.append((value, verify))
        return value == "1"

    client._async_send_command = _send

    assert asyncio.run(client.async_reboot()) is True
    assert sent == [(True, False), (1, False), ("1", False)]


def test_a_reboot_that_nothing_accepts_fails():
    client = _client()

    async def _send(dp_id, value, verify=True):
        return False

    client._async_send_command = _send

    assert asyncio.run(client.async_reboot()) is False


# --- read-back verification ----------------------------------------------------


@pytest.fixture
def _no_wait(monkeypatch, _fake_tinytuya):
    monkeypatch.setattr(_fake_tinytuya, "COMMAND_VERIFY_DELAY_S", 0)


def test_a_dp_that_reads_back_a_different_value_is_a_failed_write(_no_wait):
    client = _client()
    asyncio.run(client.async_connect())
    _Device.instances[0].status_result = {"dps": {"140": False}}

    async def _run():
        async with client._io_lock:
            return await client._async_verify_command("140", True)

    assert asyncio.run(_run()) is False


def test_a_dp_the_charger_never_reports_is_assumed_applied(_no_wait):
    client = _client()
    asyncio.run(client.async_connect())
    _Device.instances[0].status_result = {"dps": {"109": "IDLE"}}

    async def _run():
        async with client._io_lock:
            return await client._async_verify_command("140", True)

    assert asyncio.run(_run()) is None


# --- the custom DP mapping -----------------------------------------------------


def test_a_valid_custom_profile_is_used():
    from tuya_ev_charger.dp_profile import resolve_profile

    name, profile = resolve_profile("custom_json", '{"do_charge": "200"}')

    assert name == "custom_json"
    assert profile.do_charge == "200"


@pytest.mark.parametrize(
    "raw", ["", "not json", "[1, 2]", '{"do_charge": null}', '{"do_charge": ""}']
)
def test_an_unusable_custom_profile_falls_back_to_the_default(raw):
    from tuya_ev_charger.const import DEFAULT_CHARGER_PROFILE
    from tuya_ev_charger.dp_profile import resolve_profile

    name, _ = resolve_profile("custom_json", raw)

    assert name == DEFAULT_CHARGER_PROFILE


def test_an_unknown_profile_name_falls_back_to_the_default():
    from tuya_ev_charger.const import DEFAULT_CHARGER_PROFILE
    from tuya_ev_charger.dp_profile import resolve_profile

    assert resolve_profile("nope", "")[0] == DEFAULT_CHARGER_PROFILE


# --- decoding helpers ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[6, 8, 8, 10]", (6, 8, 10)),
        ([16, "x", 6], (6, 16)),
        ("not json", None),
        ("{}", None),
        ([], None),
        ("[]", None),
        (5, None),
        (None, None),
    ],
)
def test_the_allowed_current_list_is_cleaned_and_sorted(raw, expected):
    from tuya_ev_charger.charger_metrics import _parse_int_list

    assert _parse_int_list(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [({"a": 1}, {"a": 1}), ('{"a": 1}', {"a": 1}), ("[1]", {}), ("nope", {}), (5, {}), (None, {})],
)
def test_a_json_object_dp_is_decoded_or_empty(raw, expected):
    from tuya_ev_charger.charger_metrics import _parse_json_object

    assert _parse_json_object(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (0, False),
        (2, True),
        ("ON", True),
        (" off ", False),
        ("maybe", None),
        (None, None),
    ],
)
def test_booleans_are_read_liberally_but_never_guessed(raw, expected):
    from tuya_ev_charger.option_values import coerce_optional_bool

    assert coerce_optional_bool(raw) is expected


def test_text_and_json_text_coercion():
    from tuya_ev_charger.charger_metrics import _coerce_optional_json_text, _coerce_optional_text

    assert _coerce_optional_text(None) is None
    assert _coerce_optional_text("  ") is None
    assert _coerce_optional_text(5) == "5"
    assert _coerce_optional_json_text(None) is None
    assert _coerce_optional_json_text("  ") is None
    assert _coerce_optional_json_text(" x ") == "x"
    assert _coerce_optional_json_text({"a": 1}) == '{"a":1}'
    assert _coerce_optional_json_text({1, 2}) is not None  # not JSON-serialisable: still text


def test_a_numeric_coercion_never_raises():
    from tuya_ev_charger.charger_metrics import _coerce_float, _deciseconds, _tenths

    assert _coerce_float("x") == 0.0
    assert _tenths(None) is None
    assert _tenths("52") == 5.2
    assert _deciseconds(None) is None
    assert _deciseconds("9420") == 942


@pytest.mark.parametrize(
    ("received", "expected", "matches"),
    [
        (200, 300, False),
        ("300", 300, True),
        (True, True, True),
        ("off", False, True),
        ("on", False, False),
        (None, True, False),
        (" X ", "x", False),
        (" x ", "x", True),
    ],
)
def test_a_read_back_matches_on_the_type_that_was_written(received, expected, matches):
    from tuya_ev_charger.charger_metrics import values_match

    assert values_match(received, expected) is matches
