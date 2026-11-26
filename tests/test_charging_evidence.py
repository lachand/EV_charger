"""The "energy is rising" guard: when it arms, and above all when it must not."""

from __future__ import annotations

import json

import pytest

# charging_evidence.HOLD_S, spelled out: the package is only importable from
# inside a test (conftest puts it on the path from a fixture).
HOLD_S = 600.0


class _Feed:
    """One guard fed successive polls, four and a half minutes apart."""

    def __init__(self):
        from tuya_ev_charger.charging_evidence import ChargingEvidence
        from tuya_ev_charger.dp_profile import resolve_profile

        _, self.dp = resolve_profile("depow_v2", "")
        self.guard = ChargingEvidence()
        self.now = 1000.0

    def poll(self, energy_tenths, state="IDLEINS", do_charge=False, after=270.0):
        from tuya_ev_charger.charger_metrics import decode_metrics

        self.now += after
        metrics = {"L1": [2300, 60, 14]}
        if energy_tenths is not None:
            metrics["e"] = energy_tenths
        dps = {
            self.dp.work_state_debug: state,
            self.dp.do_charge: do_charge,
            self.dp.metrics: json.dumps(metrics),
        }
        raw = decode_metrics(dps, self.dp)
        return raw, self.guard.apply(raw, dps, self.dp, self.now)


def _armed():
    feed = _Feed()
    for energy in (0, 1, 2):
        _, seen = feed.poll(energy)
    assert seen.status == "charging"
    return feed


def test_nothing_changes_before_the_second_rise():
    feed = _Feed()
    for energy in (0, 1, 1):
        raw, seen = feed.poll(energy)
        assert seen is raw


def test_two_rises_rewrite_the_whole_charging_state():
    feed = _Feed()
    feed.poll(0)
    feed.poll(1)
    _, seen = feed.poll(2)
    assert seen.status == "charging"
    assert seen.do_charge is True
    assert seen.current_l1 == 6.0
    assert seen.power_l1 == seen.total_power == 1.38
    assert seen.phases["L1"].current == 6.0
    # The raw state string stays: it is what a bug report needs to show.
    assert seen.work_state_debug == "IDLEINS"


def test_it_holds_between_two_rises():
    feed = _armed()
    _, seen = feed.poll(2, after=HOLD_S - 1)
    assert seen.status == "charging"


def test_it_lets_go_when_the_energy_stops_rising():
    feed = _armed()
    raw, seen = feed.poll(2, after=HOLD_S)
    assert seen is raw
    # And one later step is not enough to arm it again.
    raw, seen = feed.poll(3)
    assert seen is raw


def test_two_rises_far_apart_are_not_evidence():
    feed = _Feed()
    feed.poll(0)
    feed.poll(1)
    raw, seen = feed.poll(2, after=HOLD_S)
    assert seen is raw


def test_a_counter_reset_disarms_it():
    feed = _armed()
    raw, seen = feed.poll(0, after=30)
    assert seen is raw


def test_an_unplugged_sleeping_or_faulted_charger_disarms_it():
    for state in ("IDLE", "SLEEP", "ERRORPAUSE"):
        feed = _armed()
        raw, seen = feed.poll(3, state=state)
        assert seen is raw, state


def test_a_normal_charge_passes_through_and_disarms_it():
    feed = _armed()
    raw, seen = feed.poll(3, state="WORKING")
    assert seen is raw
    raw, seen = feed.poll(3)
    assert seen is raw


def test_a_charge_flagged_only_by_dp_140_passes_through():
    feed = _Feed()
    for energy in (0, 1, 2):
        raw, seen = feed.poll(energy, do_charge=True)
        assert seen is raw


def test_the_last_step_of_a_session_does_not_revive_it():
    feed = _Feed()
    for energy in (0, 1, 2):
        feed.poll(energy, state="WORKING", do_charge=True)
    for energy in (3, 3, 3):
        raw, seen = feed.poll(energy, state="PAUSE")
        assert seen is raw


def test_a_poll_without_an_energy_reading_keeps_the_baseline():
    feed = _Feed()
    for energy in (0, None, 1, None):
        feed.poll(energy, after=60)
    _, seen = feed.poll(2, after=60)
    assert seen.status == "charging"


def test_it_logs_once_when_it_arms(caplog):
    feed = _armed()
    feed.poll(3)
    assert caplog.text.count("treating as charging") == 1


def test_rises_expire_even_while_polls_keep_coming():
    feed = _Feed()
    feed.poll(0)
    feed.poll(1)
    feed.poll(1, after=HOLD_S / 2)
    raw, seen = feed.poll(2, after=HOLD_S / 2)
    assert seen is raw


def test_a_change_of_raw_state_disarms_it():
    """The charger reacted, and DP 102 L1 may be frozen at its last value."""
    feed = _armed()
    raw, seen = feed.poll(2, state="PAUSE", after=30)
    assert seen is raw
    # A charge that goes on regardless proves itself again, with two new rises.
    raw, seen = feed.poll(3, state="PAUSE")
    assert seen is raw
    _, seen = feed.poll(4, state="PAUSE")
    assert seen.status == "charging"


def test_energy_gained_during_a_gap_in_polling_is_not_a_rise():
    """A lost link or a connection lent to the app: nobody knows when it rose."""
    feed = _Feed()
    feed.poll(10)
    feed.poll(50, state="PAUSE", after=3600)
    raw, seen = feed.poll(51, state="PAUSE", after=60)
    assert seen is raw


def _client(monkeypatch, profile, write_reply=None, **kwargs):
    """A real client on a charger that always denies the charge it is metering."""
    import asyncio

    from tuya_ev_charger import tuya_ev_charger as client_module

    energy = iter(range(100))

    class _Device:
        def __init__(self, *args, **kwargs):
            pass

        def status(self):
            metrics = {"L1": [2300, 60, 14], "e": next(energy)}
            return {"dps": {"109": "IDLEINS", "140": False, "102": json.dumps(metrics)}}

        def set_value(self, *_):
            return write_reply

        def close(self):
            pass

        def set_socketTimeout(self, *_):
            pass

        set_socketRetryLimit = set_socketRetryDelay = set_socketTimeout

    monkeypatch.setattr(client_module.tinytuya, "Device", _Device)
    monkeypatch.setattr(client_module, "COMMAND_VERIFY_DELAY_S", 0)
    client = client_module.TuyaEVChargerClient("id", "192.0.2.1", "key", "3.4", profile, **kwargs)
    asyncio.run(client.async_connect())
    return client


def _statuses(client, polls):
    import asyncio

    return [asyncio.run(client.async_get_metrics()).status for _ in range(polls)]


def test_an_accepted_stop_makes_the_charger_prove_itself_again(monkeypatch):
    """Held as charging, a stop the charger obeyed would swallow the restart:
    the switch writes nothing when it already reads the wanted state."""
    import asyncio

    client = _client(monkeypatch, "depow_v2")
    assert _statuses(client, 3)[-1] == "charging"
    assert asyncio.run(client.async_set_charge_enabled(False)) is True
    assert _statuses(client, 1) == ["plugged_in"]
    # Ignored by the charger: the energy keeps rising and says so again.
    assert _statuses(client, 1) == ["charging"]


def test_a_refused_command_leaves_the_guard_armed(monkeypatch):
    import asyncio

    client = _client(monkeypatch, "depow_v2", write_reply={"Error": "offline"})
    assert _statuses(client, 3)[-1] == "charging"
    assert asyncio.run(client.async_set_charge_enabled(False)) is False
    assert _statuses(client, 1) == ["charging"]


@pytest.mark.parametrize("profile", ["depow_v2", "generic_v1"])
def test_the_guard_covers_the_built_in_dp102_profiles(monkeypatch, profile):
    assert _statuses(_client(monkeypatch, profile), 3)[-1] == "charging"


def test_a_custom_mapping_is_left_alone(monkeypatch):
    client = _client(
        monkeypatch,
        "custom_json",
        charger_profile_json='{"work_state_debug": "109"}',
    )
    assert client.dp_profile == "custom_json"
    assert _statuses(client, 3) == ["plugged_in"] * 3
