"""DP decoding, checked against payloads captured from real hardware.

Every scale here was measured on a charger, not read from documentation: the
vendor documents none of these sub-fields, and two earlier guesses (power in W,
DP 105 as a lifetime meter) turned out wrong.
"""

from __future__ import annotations

import asyncio

import pytest


def _metrics(dps: dict):
    from tuya_ev_charger.tuya_ev_charger import TuyaEVChargerClient

    client = TuyaEVChargerClient("dev", "1.2.3.4", "key", "3.5")

    async def _run():
        async def _payload():
            return dps

        client._async_get_dps_payload = _payload  # type: ignore[method-assign]
        return await client.async_get_metrics()

    return asyncio.run(_run())


def test_phase_decoding_matches_hardware(charging_dps):
    data = _metrics(charging_dps)
    assert data.voltage_l1 == 227.0
    assert data.current_l1 == 8.7
    # Derived from V x A, so finer than the 0.1 kW the charger reports.
    assert data.power_l1 == pytest.approx(1.975, abs=0.01)
    assert data.phases["L1"].raw_power == pytest.approx(1.9, abs=0.01)


def test_unwired_phases_are_omitted(charging_dps):
    """Single-phase models report L2/L3 as zeros; they must not read as 0 V."""
    assert sorted(_metrics(charging_dps).phases) == ["L1"]


def test_three_phase_is_summed():
    dps = {
        "102": '{"L1":[2350,160,37],"L2":[2350,160,37],"L3":[2350,160,37],'
        '"t":290,"p":111,"d":0,"e":0}',
        "109": "WORKING",
    }
    data = _metrics(dps)
    assert sorted(data.phases) == ["L1", "L2", "L3"]
    assert data.total_power == pytest.approx(11.28, abs=0.05)


@pytest.mark.parametrize("state", ["IDLEINS", "IDLE", "STOP", "PAUSE", "SLEEP"])
def test_power_resets_when_not_charging(charging_dps, state):
    """The charger keeps echoing its last power reading once a session ends."""
    data = _metrics({**charging_dps, "109": state})
    assert data.power_l1 == 0.0
    assert data.total_power == 0.0
    # Voltage is still reported, so this is a deliberate reset, not missing data.
    assert data.voltage_l1 == 227.0


@pytest.mark.parametrize("state", ["IDLEINS", "IDLE", "STOP", "PAUSE", "SLEEP"])
def test_current_resets_when_not_charging(charging_dps, state):
    """Some firmwares also keep echoing the last current reading (issue #35)."""
    data = _metrics({**charging_dps, "109": state})
    assert data.current_l1 == 0.0


def test_session_counters(charging_dps):
    """DP 102 holds the running session: e in 0.1 kWh, d in 0.1 s."""
    data = _metrics(charging_dps)
    assert data.session_energy_kwh == 5.2
    assert data.session_duration_s == 9459
    # 2h37 at ~1.975 kW is ~5.2 kWh, which is what pins both scales.
    assert data.session_energy_kwh == pytest.approx(
        data.total_power * data.session_duration_s / 3600, rel=0.05
    )


def test_last_session_is_a_frozen_record(charging_dps):
    """DP 105 describes the previous session, not a lifetime total."""
    data = _metrics(charging_dps)
    assert data.last_session_energy_kwh == 1.3
    assert data.last_session_duration_s == 1801  # plain seconds here, not 0.1 s


def test_temperature_is_tenths(charging_dps):
    assert _metrics(charging_dps).temperature == 51.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SLEEP", "sleep"),
        ("IDLE", "idle"),
        ("IDLEINS", "plugged_in"),
        ("WORKING", "charging"),
        ("WAIT", "waiting"),
        ("ERRORPAUSE", "fault"),
        ("PAUSE", "paused"),
        ("STOP", "charged"),
    ],
)
def test_status_enum(charging_dps, raw, expected):
    assert _metrics({**charging_dps, "109": raw}).status == expected


def test_unknown_status_is_none(charging_dps):
    """An unmapped firmware state must not break the enum sensor."""
    assert _metrics({**charging_dps, "109": "SOMETHING_NEW"}).status is None


def test_plug_in_action_absent_on_firmwares_without_dp154(charging_dps):
    assert _metrics(charging_dps).plug_in_action is None


@pytest.mark.parametrize(("raw", "expected"), [(0, "prompt"), (1, "charge"), (2, "idle")])
def test_plug_in_action(charging_dps, raw, expected):
    assert _metrics({**charging_dps, "154": raw}).plug_in_action == expected
