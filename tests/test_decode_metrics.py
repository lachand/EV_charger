"""Decoding one status read into readings, with no client and no I/O.

`decode_metrics` is the pure core of `TuyaEVChargerClient.async_get_metrics`. The
cases here are the ones that bit real chargers: firmwares disagree on types, omit
DPs, keep reporting the last power after a session ends, and a model with DP 140 can
be charging while its operating-state string says something unmapped (#35).

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import json

import pytest


def _profile():
    from tuya_ev_charger.dp_profile import DP_PROFILE_MAP

    return DP_PROFILE_MAP["depow_v2"]


def _decode(dps, profile=None):
    from tuya_ev_charger.charger_metrics import decode_metrics

    return decode_metrics(dps, profile or _profile())


def _dps(profile=None, **fields):
    """A payload keyed by the profile's DP numbers, built from field names."""
    p = profile or _profile()
    return {getattr(p, name): value for name, value in fields.items()}


L1_CHARGING = json.dumps({"L1": [2270, 87, 19], "e": 52, "d": 9420, "t": 305})


def test_a_charging_read_is_decoded_end_to_end():
    m = _decode(
        _dps(
            metrics=L1_CHARGING,
            work_state_debug="WORKING",
            do_charge=True,
            current_target=10,
            max_current_cfg=32,
        )
    )

    assert m.voltage_l1 == pytest.approx(227.0)
    assert m.current_l1 == pytest.approx(8.7)
    assert m.power_l1 == pytest.approx(1.975)  # derived from V x A, not the quantised raw value
    assert m.total_power == pytest.approx(1.975)
    assert m.session_energy_kwh == pytest.approx(5.2)
    assert m.session_duration_s == 942
    assert m.temperature == pytest.approx(30.5)
    assert m.status == "charging"
    assert m.do_charge is True
    assert m.current_target == 10
    assert m.max_current_cfg == 32


def test_an_empty_read_decodes_to_unknowns_not_an_error():
    m = _decode({})

    assert m.work_state_debug == "UNKNOWN"
    assert m.status is None
    assert m.phases == {}
    assert m.voltage_l1 == 0.0
    assert m.total_power == 0.0
    assert m.temperature == 0.0
    for unknown in (
        m.session_energy_kwh,
        m.session_duration_s,
        m.last_session_energy_kwh,
        m.work_state,
        m.do_charge,
        m.current_target,
        m.max_current_cfg,
        m.nfc_enabled,
        m.plug_in_action,
        m.adjust_current_options,
        m.product_variant,
        m.selftest,
        m.alarm,
    ):
        assert unknown is None
    assert m.charger_info == {}
    assert m.schedule_enabled is False


@pytest.mark.parametrize("garbage", ["{not json", "[1, 2]", 5, None])
def test_a_metrics_blob_that_cannot_be_read_gives_no_phases(garbage):
    m = _decode(_dps(metrics=garbage, work_state_debug="WORKING"))

    assert m.phases == {}
    assert m.total_power == 0.0


def test_a_session_that_has_ended_reads_as_zero_current_and_power():
    """The charger keeps echoing the last reading after a session ends."""
    m = _decode(_dps(metrics=L1_CHARGING, work_state_debug="IDLE", do_charge=False))

    assert m.voltage_l1 == pytest.approx(227.0)  # voltage is still real
    assert m.current_l1 == 0.0
    assert m.power_l1 == 0.0
    assert m.total_power == 0.0


def test_a_model_reporting_dp140_counts_as_charging_even_with_an_unmapped_state():
    """#35: DP 109 says something we do not map, but DP 140 says it is charging."""
    m = _decode(_dps(metrics=L1_CHARGING, work_state_debug="SOMETHINGNEW", do_charge=True))

    assert m.current_l1 == pytest.approx(8.7)
    assert m.status is None  # the state string is unmapped; the reading is still live


def test_the_operating_state_is_normalised_before_it_is_mapped():
    m = _decode(_dps(work_state_debug="  working "))

    assert m.work_state_debug == "WORKING"
    assert m.status == "charging"


def test_an_unwired_phase_is_left_out_rather_than_shown_as_zero():
    blob = json.dumps({"L1": [2270, 87, 19], "L2": [0, 0, 0], "L3": [2280, 50, 11]})
    m = _decode(_dps(metrics=blob, work_state_debug="WORKING"))

    assert set(m.phases) == {"L1", "L3"}
    # kW from V x A per phase: 227.0 x 8.7 + 228.0 x 5.0 = 1.975 + 1.14
    assert m.total_power == pytest.approx(3.115)


def test_a_short_phase_array_is_ignored():
    m = _decode(_dps(metrics=json.dumps({"L1": [2270, 87]}), work_state_debug="WORKING"))

    assert m.phases == {}


def test_the_last_completed_session_comes_from_its_own_dp():
    history = json.dumps({"c": 123, "d": 7200})
    m = _decode(_dps(charge_history=history))

    assert m.last_session_energy_kwh == pytest.approx(12.3)
    assert m.last_session_duration_s == 7200  # seconds here, tenths of a second in DP 102


@pytest.mark.parametrize(
    ("raw", "expected"), [(0, "prompt"), (1, "charge"), (2, "idle"), (9, None)]
)
def test_the_plug_in_action_is_named_or_absent(raw, expected):
    assert _decode(_dps(plug_in_action=raw)).plug_in_action == expected


def test_the_allowed_current_list_is_sorted_and_deduplicated():
    assert _decode(_dps(adjust_current="[16, 6, 6, 10]")).adjust_current_options == (6, 10, 16)


def test_the_schedule_is_decoded_from_its_fixed_dp():
    from tuya_ev_charger.const import DP_SCHEDULE

    on = _decode({DP_SCHEDULE: json.dumps({"m": 2, "ss": "22:00", "se": "06:00"})})
    off = _decode({DP_SCHEDULE: json.dumps({"m": 0, "ss": "22:00", "se": "06:00"})})

    assert on.schedule_enabled is True
    assert (on.schedule_start, on.schedule_end) == ("22:00", "06:00")
    assert off.schedule_enabled is False


def test_the_info_blob_and_the_alarm_are_kept_for_diagnostics():
    m = _decode(_dps(charger_info='{"model": "X1"}', alarm={"code": 3}, selftest=" ok "))

    assert m.charger_info == {"model": "X1"}
    assert m.alarm == '{"code":3}'
    assert m.selftest == "ok"


def test_a_flag_arrives_as_a_bool_an_int_or_a_string():
    assert _decode(_dps(do_charge=True)).do_charge is True
    assert _decode(_dps(do_charge=0)).do_charge is False
    assert _decode(_dps(do_charge="ON")).do_charge is True
    assert _decode(_dps(do_charge="maybe")).do_charge is None


def test_another_profile_reads_its_own_dp_numbers():
    from tuya_ev_charger.dp_profile import DP_PROFILE_MAP

    generic = DP_PROFILE_MAP["generic_v1"]
    m = _decode(_dps(generic, current_target=16, work_state_debug="WORKING"), generic)

    assert m.current_target == 16
    assert m.status == "charging"


def test_the_client_decodes_what_it_reads(monkeypatch):
    """The method keeps the locked read; the decoding is the pure function's job."""
    import asyncio

    from tuya_ev_charger.tuya_ev_charger import TuyaEVChargerClient

    client = TuyaEVChargerClient("dev", "1.2.3.4", "key", "3.5")

    async def _payload():
        return _dps(client._dp, current_target=12)

    client._async_get_dps_payload = _payload

    assert asyncio.run(client.async_get_metrics()).current_target == 12

    async def _none():
        return None

    client._async_get_dps_payload = _none
    assert asyncio.run(client.async_get_metrics()) is None
