"""The coordinator's poll loop: what it does when the charger stops answering.

This is the integration's recovery logic -- relocate after a DHCP change, fetch a
rotated local_key, tell the user *why* it is failing -- and it only runs when
something is wrong, so it is the part a happy-path test never reaches. Each test
builds a coordinator without Home Assistant (`__new__` plus the attributes the
method under test reads) and fakes the collaborators at the module seams the
coordinator itself uses.

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import asyncio
import types
from datetime import timedelta

import pytest


class _Clock:
    def __init__(self, now=10_000.0):
        self.now = now

    def __call__(self):
        return self.now


class _Client:
    """The client surface the coordinator touches; every call is recorded."""

    def __init__(self, *, host="192.168.1.10", local_key="oldkey"):
        self.host = host
        self.local_key = local_key
        self.metrics_results: list = []
        self.fault = None
        self.tcp_reachable = True
        self.probe_hosts: set[str] = set()
        self.calls: list[tuple] = []

    async def async_get_metrics(self):
        self.calls.append(("get_metrics",))
        result = self.metrics_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def async_classify_fault(self):
        self.calls.append(("classify",))
        return self.fault

    async def async_tcp_reachable(self):
        return self.tcp_reachable

    async def async_probe_host(self, host):
        self.calls.append(("probe", host))
        return host in self.probe_hosts

    async def async_update_host(self, host):
        self.calls.append(("update_host", host))
        self.host = host

    async def async_update_local_key(self, key):
        self.calls.append(("update_key", key))
        self.local_key = key


def _coordinator(monkeypatch, *, entry_data=None, options=None, client=None, data="last"):
    from tuya_ev_charger import coordinator as module

    coord = module.TuyaEVChargerDataUpdateCoordinator.__new__(
        module.TuyaEVChargerDataUpdateCoordinator
    )
    clock = _Clock()
    coord.clock = clock
    coord.hass = types.SimpleNamespace(loop=types.SimpleNamespace(time=clock))
    coord.client = client or _Client()
    coord.entry = types.SimpleNamespace(
        entry_id="e1", title="charger", data=entry_data or {}, options=options or {}
    )
    coord.data = data
    coord.vehicle_tracker = None
    coord.session_history = None
    coord.vehicle_curves = None
    coord.solar_surplus_controller = None
    coord._session_log_primed = False
    coord._last_rediscovery_at = 0.0
    coord._last_key_refresh_at = 0.0
    coord._last_fault = None
    coord._last_fault_at = 0.0
    coord._polls_ok = 0
    coord._polls_failed = 0
    coord._consecutive_failures = 0
    coord._last_success_at = None
    coord._last_failure_at = None
    coord._relocations = 0
    coord._key_refreshes = 0
    coord._relocating = None
    coord._base_interval_s = 30
    coord._release_until = None
    coord.regulating = False
    coord.update_interval = timedelta(seconds=30)
    coord.last_discovery = None
    coord.new_local_key = None

    coord.issues = []
    monkeypatch.setattr(
        module,
        "async_raise",
        lambda _hass, entry_id, kind, **kw: coord.issues.append(("raise", kind, kw)),
    )
    monkeypatch.setattr(
        module, "async_clear", lambda _hass, entry_id, kind: coord.issues.append(("clear", kind))
    )
    return coord


GWID = "bf1234567890abcdefxyz1"


def _metrics(**overrides):
    base = {
        "status": "idle",
        "session_energy_kwh": 0.0,
        "last_session_duration_s": None,
        "last_session_energy_kwh": None,
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


# --- _async_update_data: the recovery ladder ------------------------------------


def test_a_good_poll_returns_the_metrics_and_counts_as_a_success(monkeypatch):
    coord = _coordinator(monkeypatch)
    metrics = _metrics()
    coord.client.metrics_results = [metrics]

    assert asyncio.run(coord._async_update_data()) is metrics
    assert coord._polls_ok == 1
    assert coord.client.calls == [("get_metrics",)]


def test_a_poll_failure_after_relocation_succeeds_on_the_retry(monkeypatch):
    coord = _coordinator(monkeypatch)
    metrics = _metrics()
    coord.client.metrics_results = [None, metrics]

    async def _relocated():
        return True

    coord._async_try_rediscover_host = _relocated

    assert asyncio.run(coord._async_update_data()) is metrics
    assert coord._polls_failed == 0


def test_a_poll_failure_after_a_key_refresh_succeeds_on_the_retry(monkeypatch):
    coord = _coordinator(monkeypatch)
    metrics = _metrics()
    coord.client.metrics_results = [None, metrics]

    async def _no():
        return False

    async def _refreshed():
        return True

    coord._async_try_rediscover_host = _no
    coord._async_try_refresh_local_key = _refreshed

    assert asyncio.run(coord._async_update_data()) is metrics


def _failing(monkeypatch, *, fault):
    coord = _coordinator(monkeypatch)
    coord.client.metrics_results = [None]
    coord.client.fault = fault

    async def _no():
        return False

    coord._async_try_rediscover_host = _no
    coord._async_try_refresh_local_key = _no
    return coord


def test_a_failed_poll_raises_update_failed_and_counts_it(monkeypatch):
    from tuya_ev_charger.const import ConnectionFault
    from tuya_ev_charger.coordinator import UpdateFailed

    coord = _failing(monkeypatch, fault=ConnectionFault.UNREACHABLE)

    with pytest.raises(UpdateFailed, match="nothing answers"):
        asyncio.run(coord._async_update_data())

    assert coord._polls_failed == 1
    assert coord._consecutive_failures == 1
    assert coord._last_failure_at is not None


def test_repeated_undecryptable_polls_ask_for_reauthentication(monkeypatch):
    from tuya_ev_charger.const import UNDECRYPTABLE_FAILURES_BEFORE_REAUTH, ConnectionFault
    from tuya_ev_charger.coordinator import ConfigEntryAuthFailed

    coord = _failing(monkeypatch, fault=ConnectionFault.UNDECRYPTABLE)
    coord._consecutive_failures = UNDECRYPTABLE_FAILURES_BEFORE_REAUTH - 1

    with pytest.raises(ConfigEntryAuthFailed, match="cannot be decrypted"):
        asyncio.run(coord._async_update_data())


def test_a_released_connection_skips_the_poll_and_keeps_the_last_value(monkeypatch):
    coord = _coordinator(monkeypatch, data="held")
    coord._release_until = coord.clock.now + 600

    assert asyncio.run(coord._async_update_data()) == "held"
    assert coord.client.calls == []


# --- _async_failure_message -----------------------------------------------------


@pytest.mark.parametrize(
    ("fault", "fragment", "issue"),
    [
        (None, "no telemetry received", None),
        ("refused", "refused the connection", ("raise", "connection_refused")),
        ("undecryptable", "cannot be decrypted", ("clear", "connection_refused")),
        ("unreachable", "nothing answers", ("clear", "connection_refused")),
    ],
)
def test_the_failure_message_says_why_and_manages_the_repair(monkeypatch, fault, fragment, issue):
    from tuya_ev_charger.const import ConnectionFault
    from tuya_ev_charger.repairs import ISSUE_CONNECTION_REFUSED

    coord = _coordinator(monkeypatch)
    coord.client.fault = ConnectionFault(fault) if fault else None

    message = asyncio.run(coord._async_failure_message())

    assert fragment in message
    assert "192.168.1.10" in message
    if issue is None:
        assert coord.issues == []
    else:
        kind = ISSUE_CONNECTION_REFUSED
        assert [(i[0], i[1]) for i in coord.issues] == [(issue[0], kind)]


def test_a_refused_connection_raises_the_repair_with_the_host(monkeypatch):
    from tuya_ev_charger.const import ConnectionFault

    coord = _coordinator(monkeypatch)
    coord.client.fault = ConnectionFault.REFUSED

    asyncio.run(coord._async_failure_message())

    assert coord.issues[0][2] == {"translation_placeholders": {"host": "192.168.1.10"}}


def test_a_failed_diagnosis_falls_back_to_the_cached_verdict(monkeypatch):
    from tuya_ev_charger.const import ConnectionFault

    coord = _coordinator(monkeypatch)
    coord._last_fault = ConnectionFault.UNREACHABLE
    coord._last_fault_at = coord.clock.now - 10_000  # past the cooldown

    async def _boom():
        raise OSError("probe failed")

    coord.client.async_classify_fault = _boom

    assert asyncio.run(coord._async_classify_fault_throttled()) == ConnectionFault.UNREACHABLE


# --- relocation -----------------------------------------------------------------


def _scan(monkeypatch, result):
    from tuya_ev_charger import coordinator as module

    calls = []

    async def _scanner(_hass, scantime, wantids):
        calls.append((scantime, wantids))
        return result

    monkeypatch.setattr(module, "async_scan_devices_by_id", _scanner)
    return calls


def test_a_charger_that_moved_is_followed_to_its_new_address(monkeypatch):
    coord = _coordinator(monkeypatch, entry_data={"device_id": GWID})
    calls = _scan(monkeypatch, {GWID: {"ip": "192.168.1.99"}})

    assert asyncio.run(coord._async_relocate()) is True

    assert coord.client.host == "192.168.1.99"
    assert coord._relocations == 1
    assert coord.last_discovery == {"ip": "192.168.1.99"}
    # The scan targets our charger so a neighbour's broadcast cannot end it early.
    assert calls[0][1] == [GWID]


def test_a_charger_still_at_the_same_address_is_not_a_relocation(monkeypatch):
    coord = _coordinator(monkeypatch, entry_data={"device_id": GWID})
    _scan(monkeypatch, {GWID: {"ip": "192.168.1.10"}})

    assert asyncio.run(coord._async_relocate()) is False
    assert coord._relocations == 0


def test_a_charger_not_heard_does_not_get_other_devices_probed_with_its_key(monkeypatch):
    coord = _coordinator(monkeypatch, entry_data={"device_id": GWID})
    _scan(monkeypatch, {"someone_else": {"ip": "192.168.1.55"}})

    assert asyncio.run(coord._async_relocate()) is False
    assert not [c for c in coord.client.calls if c[0] == "probe"]


def test_an_empty_scan_finds_nothing(monkeypatch):
    coord = _coordinator(monkeypatch, entry_data={"device_id": GWID})
    _scan(monkeypatch, {})

    assert asyncio.run(coord._async_relocate()) is False


def test_a_legacy_entry_without_a_gwid_is_identified_by_a_live_read(monkeypatch):
    """An old scan saved the IP as the device_id; only a decrypting read can tell."""
    coord = _coordinator(monkeypatch, entry_data={"device_id": "192.168.1.10"})
    coord.client.probe_hosts = {"192.168.1.77"}
    _scan(
        monkeypatch,
        {"a": {"ip": "192.168.1.55"}, "b": {"ip": "192.168.1.77"}, "c": {"ip": "192.168.1.10"}},
    )

    assert asyncio.run(coord._async_relocate()) is True

    assert coord.client.host == "192.168.1.77"
    assert coord.last_discovery == {"ip": "192.168.1.77"}
    # The current (failing) host is never probed again.
    assert ("probe", "192.168.1.10") not in coord.client.calls


def test_a_legacy_entry_with_no_matching_device_gives_up(monkeypatch):
    coord = _coordinator(monkeypatch, entry_data={"device_id": "192.168.1.10"})
    _scan(monkeypatch, {"a": {"ip": "192.168.1.55"}})

    assert asyncio.run(coord._async_relocate()) is False


def test_rediscovery_is_throttled(monkeypatch):
    coord = _coordinator(monkeypatch, data=None)
    calls = _scan(monkeypatch, {})

    asyncio.run(coord._async_try_rediscover_host())
    asyncio.run(coord._async_try_rediscover_host())

    assert len(calls) == 1


def test_a_routine_poll_relocates_in_the_background_not_inline(monkeypatch):
    coord = _coordinator(monkeypatch, data="have-data")
    scheduled = []
    coord._schedule_relocation = lambda: scheduled.append(1)

    assert asyncio.run(coord._async_try_rediscover_host()) is False
    assert scheduled == [1]


def test_the_background_relocation_refreshes_straight_away_when_it_moves(monkeypatch):
    coord = _coordinator(monkeypatch)
    created = []
    refreshed = []

    def _create(_hass, coro, name=None):
        created.append((coro, name))
        return types.SimpleNamespace(done=lambda: False)

    coord.entry.async_create_background_task = _create

    async def _relocate():
        return True

    async def _refresh():
        refreshed.append(1)

    coord._async_relocate = _relocate
    coord.async_request_refresh = _refresh

    coord._schedule_relocation()
    assert created[0][1] == "tuya_ev_charger_relocate"
    asyncio.run(created[0][0])

    assert refreshed == [1]


def test_a_second_relocation_is_not_started_while_one_runs(monkeypatch):
    coord = _coordinator(monkeypatch)
    coord._relocating = types.SimpleNamespace(done=lambda: False)
    coord.entry.async_create_background_task = lambda *a, **k: pytest.fail("started twice")

    coord._schedule_relocation()


def test_a_background_relocation_that_raises_is_swallowed(monkeypatch):
    coord = _coordinator(monkeypatch)
    created = []
    coord.entry.async_create_background_task = lambda _h, coro, name=None: created.append(coro)

    async def _boom():
        raise OSError("scan failed")

    coord._async_relocate = _boom

    coord._schedule_relocation()
    asyncio.run(created[0])  # must not raise


def test_the_candidate_helpers_skip_the_failing_host_and_blanks():
    from tuya_ev_charger.coordinator import TuyaEVChargerDataUpdateCoordinator as C

    candidates = {
        "a": {"ip": "192.168.1.1"},
        "b": {"ip": " "},
        "c": {"ip": "192.168.1.2"},
        "d": {"ip": "192.168.1.1"},
    }

    assert C._other_candidate_hosts(candidates, "192.168.1.2") == ["192.168.1.1"]
    assert C._discovery_for_host(candidates, "192.168.1.2") == {"ip": "192.168.1.2"}
    # Unknown host: only the address is known, so that is all the record holds.
    assert C._discovery_for_host(candidates, "10.0.0.1") == {"ip": "10.0.0.1"}


# --- local_key refresh ----------------------------------------------------------

CLOUD = {
    "device_id": GWID,
    "cloud_api_key": "k",
    "cloud_api_secret": "s",
    "cloud_region": "eu",
}


def _cloud(monkeypatch, *, key=None, error=None):
    from tuya_ev_charger import coordinator as module

    calls = []

    async def _fetch(_hass, region, api_key, api_secret, device_id):
        calls.append((region, api_key, api_secret, device_id))
        if error:
            raise error
        return key

    monkeypatch.setattr(module, "async_fetch_local_key", _fetch)
    return calls


def test_without_cloud_credentials_the_key_refresh_is_a_no_op(monkeypatch):
    coord = _coordinator(monkeypatch, entry_data={"device_id": GWID})
    calls = _cloud(monkeypatch, key="new")

    assert asyncio.run(coord._async_try_refresh_local_key()) is False
    assert calls == []


def test_a_closed_port_is_a_network_problem_not_a_key_problem(monkeypatch):
    coord = _coordinator(monkeypatch, entry_data=CLOUD)
    coord.client.tcp_reachable = False
    calls = _cloud(monkeypatch, key="new")

    assert asyncio.run(coord._async_try_refresh_local_key()) is False
    assert calls == []


def test_a_rotated_key_is_adopted(monkeypatch):
    coord = _coordinator(monkeypatch, entry_data=CLOUD)
    calls = _cloud(monkeypatch, key="newkey")

    assert asyncio.run(coord._async_try_refresh_local_key()) is True

    assert coord.client.local_key == "newkey"
    assert coord.new_local_key == "newkey"
    assert coord._key_refreshes == 1
    assert calls == [("eu", "k", "s", GWID)]


@pytest.mark.parametrize("returned", [None, "oldkey"])
def test_an_unchanged_or_missing_key_is_not_adopted(monkeypatch, returned):
    coord = _coordinator(monkeypatch, entry_data=CLOUD)
    _cloud(monkeypatch, key=returned)

    assert asyncio.run(coord._async_try_refresh_local_key()) is False
    assert coord._key_refreshes == 0


def test_a_cloud_error_is_logged_not_raised(monkeypatch):
    from tuya_ev_charger.cloud import TuyaCloudError

    coord = _coordinator(monkeypatch, entry_data=CLOUD)
    _cloud(monkeypatch, error=TuyaCloudError("quota"))

    assert asyncio.run(coord._async_try_refresh_local_key()) is False


def test_the_cloud_is_not_asked_again_inside_the_cooldown(monkeypatch):
    coord = _coordinator(monkeypatch, entry_data=CLOUD)
    calls = _cloud(monkeypatch, key=None)

    asyncio.run(coord._async_try_refresh_local_key())
    asyncio.run(coord._async_try_refresh_local_key())

    assert len(calls) == 1


# --- bookkeeping that must never break the poll ---------------------------------


class _History:
    def __init__(self, *, new=True, fail=False, sessions=()):
        self._new = new
        self._fail = fail
        self.sessions = list(sessions)
        self.recorded = []
        self.seen = []

    def is_new_session(self, duration_s, energy_kwh):
        return self._new

    async def async_record(self, record):
        if self._fail:
            raise OSError("disk full")
        self.recorded.append(record)

    async def async_note_seen(self, duration_s, energy_kwh):
        self.seen.append((duration_s, energy_kwh))


def _finished(**kw):
    return _metrics(last_session_duration_s=3600, last_session_energy_kwh=7.2, **kw)


def test_the_first_sighting_of_a_stored_session_is_noted_not_logged(monkeypatch):
    """The charger's stored session may be weeks old; logging it would invent one."""
    coord = _coordinator(monkeypatch)
    coord.session_history = _History()

    asyncio.run(coord._async_log_completed_session(_finished()))

    assert coord.session_history.recorded == []
    assert coord.session_history.seen == [(3600, 7.2)]
    assert coord._session_log_primed is True


def test_a_later_new_session_is_recorded_with_its_cost_and_vehicle(monkeypatch):
    coord = _coordinator(
        monkeypatch,
        options={"off_peak_price": 0.1, "peak_price": 0.2, "off_peak_windows": ""},
    )
    coord.session_history = _History()
    coord._session_log_primed = True
    coord.vehicle_tracker = types.SimpleNamespace(active_vehicle="Zoe")

    asyncio.run(coord._async_log_completed_session(_finished()))

    (record,) = coord.session_history.recorded
    assert record.energy_kwh == 7.2
    assert record.duration_s == 3600
    assert record.vehicle == "Zoe"


def test_a_known_session_is_not_logged_twice(monkeypatch):
    coord = _coordinator(monkeypatch)
    coord.session_history = _History(new=False)
    coord._session_log_primed = True

    asyncio.run(coord._async_log_completed_session(_finished()))

    assert coord.session_history.recorded == []


def test_an_incomplete_session_reading_is_ignored(monkeypatch):
    coord = _coordinator(monkeypatch)
    coord.session_history = _History()

    asyncio.run(coord._async_log_completed_session(_metrics()))

    assert coord.session_history.seen == []


def test_a_failing_history_never_fails_the_poll(monkeypatch):
    coord = _coordinator(monkeypatch)
    coord.session_history = _History(fail=True)
    coord._session_log_primed = True

    asyncio.run(coord._async_log_completed_session(_finished()))  # must not raise

    assert coord._session_log_primed is True


def test_a_session_record_prefers_the_controllers_live_off_peak_split(monkeypatch):
    from tuya_ev_charger.session_costing import SessionSplit

    split = SessionSplit(off_peak_minutes=40, peak_minutes=20)
    coord = _coordinator(monkeypatch, options={"off_peak_price": 0.1, "peak_price": 0.3})
    coord.solar_surplus_controller = types.SimpleNamespace(session_off_peak_split=lambda: split)

    record = coord._build_session_record(_finished(), 3600, 6.0)

    assert (record.off_peak_minutes, record.peak_minutes) == (40, 20)
    # Two thirds of 6 kWh at 0.10, one third at 0.30.
    assert record.cost == pytest.approx(4.0 * 0.10 + 2.0 * 0.30)


def test_without_a_live_split_the_windows_are_reconstructed(monkeypatch):
    coord = _coordinator(monkeypatch, options={"off_peak_windows": ""})
    coord.solar_surplus_controller = types.SimpleNamespace(session_off_peak_split=lambda: None)

    record = coord._build_session_record(_finished(), 3600, 6.0)

    assert record.off_peak_minutes + record.peak_minutes == 60


def test_anomaly_detection_syncs_the_repairs(monkeypatch):
    from tuya_ev_charger import coordinator as module

    coord = _coordinator(monkeypatch)
    coord.session_history = _History(sessions=[{"energy_kwh": 5.0}])
    synced = []
    monkeypatch.setattr(module, "detect_anomalies", lambda *a, **k: [])
    monkeypatch.setattr(
        module, "async_sync_session_anomalies", lambda _h, entry_id, values: synced.append(values)
    )

    coord._async_check_session_anomalies()

    assert synced == [[]]


def test_anomaly_detection_without_history_does_nothing(monkeypatch):
    coord = _coordinator(monkeypatch)

    coord._async_check_session_anomalies()  # must not raise


def test_vehicle_energy_is_routed_to_the_tracker_and_its_failures_swallowed(monkeypatch):
    coord = _coordinator(monkeypatch)
    seen = []

    class _Tracker:
        async def async_process_counter(self, value):
            seen.append(value)

    coord.vehicle_tracker = _Tracker()
    asyncio.run(coord._async_track_vehicle_energy(_metrics(session_energy_kwh=3.5)))
    assert seen == [3.5]

    class _Broken:
        async def async_process_counter(self, value):
            raise ValueError("bad counter")

    coord.vehicle_tracker = _Broken()
    asyncio.run(coord._async_track_vehicle_energy(_metrics()))  # must not raise


def test_a_raising_client_reads_as_a_failed_poll(monkeypatch):
    coord = _coordinator(monkeypatch)
    coord.client.metrics_results = [OSError("socket")]

    assert asyncio.run(coord._async_fetch_metrics()) is None


def test_metrics_flush_the_curve_store_after_a_poll(monkeypatch):
    coord = _coordinator(monkeypatch)
    flushed = []

    class _Curves:
        async def async_flush(self, now):
            flushed.append(now)

    coord.vehicle_curves = _Curves()
    asyncio.run(coord._async_on_metrics(_metrics()))

    assert flushed == [coord.clock.now]


# --- the poll interval and the health report ------------------------------------


def test_the_interval_follows_what_the_charger_is_doing(monkeypatch):
    from tuya_ev_charger.polling import poll_interval_s

    coord = _coordinator(monkeypatch)
    coord.regulating = True

    coord._async_retune_interval(_metrics(status="charging"))

    assert coord.update_interval == timedelta(
        seconds=poll_interval_s(base_interval_s=30, status="charging", regulating=True)
    )


def test_the_connection_health_report(monkeypatch):
    coord = _coordinator(monkeypatch)
    coord._polls_ok = 3
    coord._polls_failed = 1

    health = coord.connection_health

    assert health["success_rate_pct"] == 75.0
    assert health["host"] == "192.168.1.10"
    assert health["poll_interval_s"] == 30
    assert health["last_fault"] is None


def test_the_health_report_has_no_rate_before_the_first_poll(monkeypatch):
    coord = _coordinator(monkeypatch)

    assert coord.connection_health["success_rate_pct"] is None


def test_option_helpers_tolerate_missing_and_bad_values():
    from tuya_ev_charger.option_values import option_float, option_text

    assert option_text({"k": "x"}, "k", "") == "x"
    assert option_text({}, "k", "") == ""
    assert option_float({"k": "1.5"}, "k") == 1.5
    # A price typed with a stray comma must not break a poll: it reads as zero.
    assert option_float({"k": "1,5"}, "k") == 0.0
    assert option_float({}, "k") == 0.0
    assert option_float({"k": None}, "k") == 0.0
    assert option_float({"k": ""}, "k") == 0.0


def test_a_session_under_a_negative_tariff_is_recorded_with_a_negative_cost(monkeypatch):
    """The coordinator reads the price with its sign, so the record can show a gain."""
    coord = _coordinator(
        monkeypatch, options={"off_peak_price": -0.05, "peak_price": 0, "off_peak_windows": ""}
    )
    from tuya_ev_charger.session_costing import SessionSplit

    coord.solar_surplus_controller = types.SimpleNamespace(
        session_off_peak_split=lambda: SessionSplit(off_peak_minutes=60, peak_minutes=0)
    )

    record = coord._build_session_record(_finished(), 3600, 10.0)

    assert record.cost == pytest.approx(-0.5)


# --- the real constructor -------------------------------------------------------------


def test_a_new_coordinator_starts_clean(monkeypatch):
    """Every other test builds one with `__new__`; this is the only one that runs the
    constructor, so it is where a forgotten or mis-typed initial value would show."""
    from tuya_ev_charger.coordinator import TuyaEVChargerDataUpdateCoordinator

    client = _Client()
    entry = types.SimpleNamespace(entry_id="e1", title="charger", data={}, options={})

    coord = TuyaEVChargerDataUpdateCoordinator(
        hass=types.SimpleNamespace(),
        client=client,
        entry=entry,
        update_interval=timedelta(seconds=45),
    )

    assert coord.client is client
    assert coord.entry is entry
    assert coord._base_interval_s == 45
    assert (coord._polls_ok, coord._polls_failed, coord._consecutive_failures) == (0, 0, 0)
    assert (coord._relocations, coord._key_refreshes) == (0, 0)
    assert coord._release_until is None
    assert coord.regulating is False
    assert coord._last_fault is None
    assert coord.last_discovery is None
    assert coord.new_local_key is None
    assert coord._session_log_primed is False
    for attached_later in (
        coord.vehicle_tracker,
        coord.session_history,
        coord.vehicle_curves,
        coord.solar_surplus_controller,
    ):
        assert attached_later is None
