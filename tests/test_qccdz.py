"""The `qccdz_v1` profile: Tuya's standard EV charger layout with scalar DPs (#46).

dé portable chargers on firmware 1.1.25+ report no DP 102 blob. Power, current and
voltage come as plain integers in tenths (DP 48/49/50), the state in Tuya's
standard vocabulary (DP 38 `evse_state`, DP 13 pilot state). The payloads below are
real reads: the charging/unplugged pair from the #46 report (1.1.25), and an idle
read from a 1.1.27-EU unit that has not charged since boot, so it omits DP 48-50.

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import asyncio

import pytest

# #46, charging at a 6 A setpoint.
CHARGING = {
    "4": 6,
    "10": 0,
    "13": "controlpi_6v_pwm",
    "18": True,
    "23": "1.1.25",
    "24": 37,
    "25": 10,
    "35": "charge",
    "36": True,
    "38": "plugged_in_charging",
    "42": False,
    "48": 13,
    "49": 59,
    "50": 2326,
    "54": "1790698557",
}

# #46, right after unplugging: DP 48/49 still hold the last reading.
UNPLUGGED = {
    **CHARGING,
    "13": "controlpi_12v",
    "24": 39,
    "25": 19,
    "35": "disabled",
    "38": "not_plugged_in",
    "48": 14,
    "49": 60,
    "50": 2335,
}

# 1.1.27-EU, idle since boot.
IDLE_SINCE_BOOT = {
    "4": 10,
    "10": 0,
    "13": "controlpi_12v",
    "18": True,
    "23": "1.1.27-EU",
    "24": 24,
    "36": True,
    "38": "not_plugged_in",
    "42": False,
}


def _profile():
    from tuya_ev_charger.dp_profile import DP_PROFILE_MAP

    return DP_PROFILE_MAP["qccdz_v1"]


def _decode(dps):
    from tuya_ev_charger.charger_metrics import decode_qccdz_metrics

    return decode_qccdz_metrics(dps, _profile())


def test_a_charging_read_is_decoded_with_the_thing_model_scales():
    m = _decode(CHARGING)

    assert m.status == "charging"
    assert m.work_state_debug == "WORKING"
    assert m.voltage_l1 == pytest.approx(232.6)
    assert m.current_l1 == pytest.approx(5.9)
    # Derived from V x A like the DP 102 path; DP 48 itself is quantised.
    assert m.power_l1 == pytest.approx(232.6 * 5.9 / 1000, abs=1e-3)
    assert m.total_power == m.power_l1
    assert m.phases["L1"].raw_power == pytest.approx(1.3)
    assert m.session_energy_kwh == pytest.approx(0.10)
    assert m.temperature == pytest.approx(37.0)
    assert m.do_charge is True
    assert m.current_target == 6
    assert m.max_current_cfg == 16
    assert m.charger_info == {"sw_version": "1.1.25"}
    assert m.alarm is None


def test_power_and_current_read_zero_once_the_car_is_unplugged():
    """DP 48/49 keep the last reading (1.4 kW / 6.0 A) with no car attached."""
    m = _decode(UNPLUGGED)

    assert m.status == "idle"
    assert m.current_l1 == 0.0
    assert m.power_l1 == 0.0
    assert m.total_power == 0.0
    # Voltage and the session counter are still meaningful.
    assert m.voltage_l1 == pytest.approx(233.5)
    assert m.session_energy_kwh == pytest.approx(0.19)


def test_a_read_without_the_metering_dps_has_no_phases():
    m = _decode(IDLE_SINCE_BOOT)

    assert m.phases == {}
    assert m.voltage_l1 == 0.0
    assert m.total_power == 0.0
    assert m.session_energy_kwh is None
    assert m.status == "idle"
    assert m.charger_info == {"sw_version": "1.1.27-EU"}


@pytest.mark.parametrize(
    ("evse_state", "work_state_debug", "status"),
    [
        ("not_plugged_in", "IDLE", "idle"),
        ("plugged_in_no_demand", "IDLEINS", "plugged_in"),
        ("plugged_in_demand", "WAIT", "waiting"),
        ("plugged_in_charging", "WORKING", "charging"),
        ("session_ending", "STOP", "charged"),
        ("fault", "ERRORPAUSE", "fault"),
    ],
)
def test_evse_state_maps_onto_the_existing_status_vocabulary(evse_state, work_state_debug, status):
    m = _decode({**IDLE_SINCE_BOOT, "38": evse_state})

    assert m.work_state_debug == work_state_debug
    assert m.status == status


def test_an_unmapped_evse_state_is_kept_but_has_no_status():
    m = _decode({**IDLE_SINCE_BOOT, "38": "plugged_in_discharging"})

    assert m.work_state_debug == "PLUGGED_IN_DISCHARGING"
    assert m.status is None


def test_the_pilot_state_stands_in_when_dp_38_is_missing():
    dps = {key: value for key, value in CHARGING.items() if key != "38"}

    m = _decode(dps)

    assert m.status == "charging"
    assert m.current_l1 == pytest.approx(5.9)


def test_no_state_dp_at_all_reads_as_unknown_and_not_charging():
    dps = {key: value for key, value in CHARGING.items() if key not in {"13", "38"}}

    m = _decode(dps)

    assert m.work_state_debug == "UNKNOWN"
    assert m.status is None
    assert m.current_l1 == 0.0


def test_fault_bits_are_named():
    assert _decode({**IDLE_SINCE_BOOT, "10": 0}).alarm is None
    # bit 0 = ov_cr, bit 6 = earth_fault, bit 16 = high_temp
    m = _decode({**IDLE_SINCE_BOOT, "10": (1 << 0) | (1 << 6) | (1 << 16)})
    assert m.alarm == "ov_cr,earth_fault,high_temp"


def test_a_fault_bit_beyond_the_known_labels_is_still_reported():
    m = _decode({**IDLE_SINCE_BOOT, "10": 1 << 17})

    assert m.alarm == "unknown_0x20000"


def test_the_assistant_recognises_the_qccdz_layout():
    from tuya_ev_charger.profile_assistant import suggest_profile

    assert suggest_profile(CHARGING) == "qccdz_v1"
    assert suggest_profile(IDLE_SINCE_BOOT) == "qccdz_v1"


def test_the_assistant_still_picks_depow_v2_for_its_layout():
    from tuya_ev_charger.profile_assistant import suggest_profile

    depow = {"102": "{}", "106": "{}", "140": True, "150": 10, "109": "IDLE"}
    assert suggest_profile(depow) == "depow_v2"
    assert suggest_profile({"1": True}) == "generic_v1"


class _FakeDevice:
    def __init__(self, dps):
        self._dps = dps
        self.set_calls: list[tuple[str, object]] = []

    def status(self):
        return {"dps": self._dps}

    def set_value(self, dp_id, value):
        self.set_calls.append((dp_id, value))
        return None


def _client(dps):
    from tuya_ev_charger.tuya_ev_charger import TuyaEVChargerClient

    client = TuyaEVChargerClient("dev", "1.2.3.4", "key", "3.5", charger_profile="qccdz_v1")
    client._device = _FakeDevice(dps)
    return client


def test_the_client_decodes_with_the_qccdz_decoder():
    metrics = asyncio.run(_client(CHARGING).async_get_metrics())

    assert metrics is not None
    assert metrics.status == "charging"
    assert metrics.current_l1 == pytest.approx(5.9)


def test_the_client_writes_the_qccdz_command_dps():
    client = _client(CHARGING)

    assert client.has_dp("current_target")
    assert client.has_dp("do_charge")


@pytest.mark.parametrize(("enabled", "raw"), [(True, "charge"), (False, "disabled")])
def test_start_and_stop_go_through_charge_discharge_not_the_switch(enabled, raw):
    # The app stops a session with DP 35; DP 18 off leaves the car charging.
    client = _client({**CHARGING, "35": raw})

    assert asyncio.run(client.async_set_charge_enabled(enabled)) is True
    assert client._device.set_calls == [("35", raw)]


@pytest.mark.parametrize(
    ("raw", "expected"), [("charge", True), ("disabled", False), ("discharge", False)]
)
def test_do_charge_reads_charge_discharge(raw, expected):
    assert _decode({**CHARGING, "18": True, "35": raw}).do_charge is expected


def test_do_charge_is_unknown_without_dp_35():
    assert _decode(IDLE_SINCE_BOOT).do_charge is None


def test_a_command_with_no_dp_on_this_firmware_is_refused_without_writing():
    client = _client(CHARGING)

    assert not client.has_dp("reboot")
    assert asyncio.run(client.async_reboot()) is False
    assert asyncio.run(client.async_set_work_state(200)) is False
    assert client._device.set_calls == []


def test_entities_backed_by_a_missing_dp_are_listed_for_disabling():
    from tuya_ev_charger.entity_cleanup import missing_dp_entity_keys

    client = _client(CHARGING)

    assert missing_dp_entity_keys(client.has_dp) == {
        "reboot_charger",
        "ready_to_charge",
        "schedule_enabled",
        "schedule_start",
        "schedule_end",
    }


def test_depow_v2_keeps_every_dp_backed_entity():
    from tuya_ev_charger.entity_cleanup import missing_dp_entity_keys
    from tuya_ev_charger.tuya_ev_charger import TuyaEVChargerClient

    client = TuyaEVChargerClient("dev", "1.2.3.4", "key", "3.5", charger_profile="depow_v2")

    assert missing_dp_entity_keys(client.has_dp) == set()


def test_the_schedule_is_not_written_on_a_firmware_without_it():
    client = _client(CHARGING)

    assert asyncio.run(client.async_set_schedule(True, "22:00", "06:00")) is False
    assert client._device.set_calls == []


def test_the_current_ceiling_defaults_to_16_a():
    assert _decode(CHARGING).max_current_cfg == 16


def test_a_configured_ceiling_replaces_the_16_a_default():
    """A 32 A unit on this layout must be able to go past 16 A (#57)."""
    from tuya_ev_charger.charger_metrics import decode_qccdz_metrics

    assert decode_qccdz_metrics(CHARGING, _profile(), 32).max_current_cfg == 32
    assert decode_qccdz_metrics(CHARGING, _profile(), 10).max_current_cfg == 10


def test_the_client_hands_its_ceiling_to_the_decoder():
    from tuya_ev_charger.tuya_ev_charger import TuyaEVChargerClient

    client = TuyaEVChargerClient(
        "dev", "1.2.3.4", "key", "3.5", charger_profile="qccdz_v1", max_current_a=32
    )
    client._device = _FakeDevice(CHARGING)

    assert asyncio.run(client.async_get_metrics()).max_current_cfg == 32
    assert asyncio.run(_client(CHARGING).async_get_metrics()).max_current_cfg == 16


def test_the_offered_currents_follow_the_configured_ceiling():
    from tuya_ev_charger.helpers import allowed_currents

    wide = _decode_with(32)
    assert max(allowed_currents(wide, {})) == 32
    assert max(allowed_currents(_decode(CHARGING), {})) == 16


def _decode_with(ceiling):
    from tuya_ev_charger.charger_metrics import decode_qccdz_metrics

    return decode_qccdz_metrics(CHARGING, _profile(), ceiling)
