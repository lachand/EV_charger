"""The `feyree_v1` profile: Feyree portable chargers with scalar per-phase DPs (#98).

Product id ebdm8ax35pgodi2s (11 kW three-phase, 16 A). The payloads below are the
real reads from the #98 report: voltages/currents in tenths (DP 102-107), total
power in tenths of a kW (DP 109, quantised), session energy in tenths of a kWh
(DP 112), temperature in tenths of a degree (DP 110), max current as a string
(DP 113 "Max16A"), setpoint in amperes (DP 114), command enum (DP 124).

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import asyncio

import pytest

# #98, charging a Kia PV5 at a 16 A setpoint.
CHARGING = {
    "10": 0,
    "101": "charing",
    "102": 2360,
    "103": 2348,
    "104": 2352,
    "105": 147,
    "106": 147,
    "107": 147,
    "109": 103,
    "110": 345,
    "112": 30,
    "113": "Max16A",
    "114": 16,
    "115": 16,
    "118": 0,
    "124": "OpenCharging",
}


def _profile():
    from tuya_ev_charger.dp_profile import DP_PROFILE_MAP

    return DP_PROFILE_MAP["feyree_v1"]


def _decode(dps):
    from tuya_ev_charger.charger_metrics import decode_feyree_metrics

    return decode_feyree_metrics(dps, _profile())


def test_a_charging_read_is_decoded_with_the_feyree_scales():
    m = _decode(CHARGING)

    assert m.status == "charging"
    assert m.work_state_debug == "WORKING"
    assert m.voltage_l1 == pytest.approx(236.0)
    assert m.current_l1 == pytest.approx(14.7)
    # Three phases wired: all three carry V and A.
    assert set(m.phases) == {"L1", "L2", "L3"}
    assert m.phases["L2"].voltage == pytest.approx(234.8)
    assert m.phases["L3"].current == pytest.approx(14.7)
    # Power is derived from V x A per phase and summed, not read from the
    # quantised DP 109 (103 -> 10.3 kW): 706.0 V x 14.7 A = 10.378 kW.
    assert m.phases["L1"].power == pytest.approx(236.0 * 14.7 / 1000, abs=1e-3)
    assert m.total_power == pytest.approx(10.378, abs=1e-3)
    assert m.power_l1 == m.phases["L1"].power
    assert m.session_energy_kwh == pytest.approx(3.0)
    assert m.temperature == pytest.approx(34.5)
    assert m.do_charge is True
    assert m.current_target == 16
    assert m.max_current_cfg == 16
    assert m.alarm is None


def test_power_and_current_read_zero_once_the_car_is_unplugged():
    """Like the other firmwares, the last current/power reading lingers."""
    m = _decode({**CHARGING, "101": "no_connet", "124": "WaitOperation"})

    assert m.status == "idle"
    assert m.work_state_debug == "IDLE"
    assert m.current_l1 == 0.0
    assert m.power_l1 == 0.0
    assert m.total_power == 0.0
    assert all(p.current == 0.0 and p.power == 0.0 for p in m.phases.values())
    # Voltage and the session counter are still meaningful.
    assert m.voltage_l1 == pytest.approx(236.0)
    assert m.session_energy_kwh == pytest.approx(3.0)
    assert m.do_charge is None  # WaitOperation is the transient idle value


@pytest.mark.parametrize(
    ("raw", "debug", "status"),
    [
        ("no_connet", "IDLE", "idle"),
        ("connect", "IDLEINS", "plugged_in"),
        ("charing", "WORKING", "charging"),
        ("wait_rfid", "WAIT", "waiting"),
        ("wait_charing", "WAIT", "waiting"),
        ("finish", "STOP", "charged"),
        ("error", "ERRORPAUSE", "fault"),
    ],
)
def test_feyree_status_strings_map_onto_the_existing_vocabulary(raw, debug, status):
    m = _decode({**CHARGING, "101": raw})

    assert m.work_state_debug == debug
    assert m.status == status


def test_an_unmapped_status_is_unknown_and_not_charging():
    m = _decode({**CHARGING, "101": "something_new"})

    assert m.work_state_debug == "UNKNOWN"
    assert m.status is None
    assert m.total_power == 0.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("OpenCharging", True), ("CloseCharging", False), ("WaitOperation", None)],
)
def test_do_charge_reads_the_dp124_command_enum(raw, expected):
    assert _decode({**CHARGING, "124": raw}).do_charge is expected


def test_do_charge_is_unknown_without_dp_124():
    dps = {k: v for k, v in CHARGING.items() if k != "124"}
    assert _decode(dps).do_charge is None


@pytest.mark.parametrize(("raw", "expected"), [("Max16A", 16), ("Max32A", 32), ("Max", None)])
def test_max_current_is_parsed_from_the_dp113_string(raw, expected):
    assert _decode({**CHARGING, "113": raw}).max_current_cfg == expected


def test_max_current_is_none_without_dp_113():
    dps = {k: v for k, v in CHARGING.items() if k != "113"}
    assert _decode(dps).max_current_cfg is None


def test_a_single_phase_model_omits_unwired_phases():
    m = _decode({**CHARGING, "103": 0, "104": 0, "106": 0, "107": 0})

    assert set(m.phases) == {"L1"}
    assert m.total_power == pytest.approx(236.0 * 14.7 / 1000, abs=1e-3)


def test_fault_bits_are_named():
    assert _decode({**CHARGING, "10": 1}).alarm == "ov_cr"
    assert _decode({**CHARGING, "10": 0}).alarm is None


def test_a_read_without_metering_dps_has_no_phases():
    dps = {k: v for k, v in CHARGING.items() if k not in {"102", "103", "104", "105", "106", "107"}}
    m = _decode(dps)

    assert m.phases == {}
    assert m.voltage_l1 == 0.0
    assert m.total_power == 0.0
    assert m.status == "charging"  # the DP 101 string still drives the state


def test_the_assistant_recognises_the_feyree_layout():
    from tuya_ev_charger.profile_assistant import suggest_profile

    assert suggest_profile(CHARGING) == "feyree_v1"
    assert suggest_profile({**CHARGING, "101": "no_connet"}) == "feyree_v1"


def test_the_assistant_keeps_the_other_layouts_on_their_own_profiles():
    from tuya_ev_charger.profile_assistant import suggest_profile

    depow = {"102": "{}", "106": "{}", "140": True, "150": 10, "109": "IDLE", "101": 200}
    assert suggest_profile(depow) == "depow_v2"
    qccdz = {"4": 6, "13": "controlpi_12v", "18": True}
    assert suggest_profile(qccdz) == "qccdz_v1"
    assert suggest_profile({"1": True}) == "generic_v1"


class _FakeDevice:
    def __init__(self, dps):
        self._dps = dict(dps)
        self.set_calls: list[tuple[str, object]] = []

    def status(self):
        return {"dps": self._dps}

    def set_value(self, dp_id, value):
        self.set_calls.append((dp_id, value))
        self._dps[dp_id] = value  # echo it back, so read-back verification matches
        return {"dps": {dp_id: value}}


def _client(dps):
    from tuya_ev_charger.tuya_ev_charger import TuyaEVChargerClient

    client = TuyaEVChargerClient("dev", "1.2.3.4", "key", "3.5", charger_profile="feyree_v1")
    client._device = _FakeDevice(dps)
    return client


def test_the_client_decodes_with_the_feyree_decoder():
    metrics = asyncio.run(_client(CHARGING).async_get_metrics())

    assert metrics is not None
    assert metrics.status == "charging"
    assert metrics.current_l1 == pytest.approx(14.7)
    assert metrics.total_power == pytest.approx(10.378, abs=1e-3)


@pytest.mark.parametrize(("enabled", "raw"), [(True, "OpenCharging"), (False, "CloseCharging")])
def test_start_and_stop_go_through_the_dp124_command_enum(enabled, raw):
    client = _client(CHARGING)

    assert asyncio.run(client.async_set_charge_enabled(enabled)) is True
    assert client._device.set_calls == [("124", raw)]


def test_the_setpoint_goes_to_dp114():
    client = _client(CHARGING)

    assert asyncio.run(client.async_set_charge_current(10)) is True
    assert client._device.set_calls == [("114", 10)]
