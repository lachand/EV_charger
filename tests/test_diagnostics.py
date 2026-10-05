"""Diagnostics are pasted into public GitHub issues, so what leaves must be safe.

The stub `async_redact_data` in conftest is the identity, which would let a missing
redaction pass. These tests swap in a real recursive redactor, so a secret that is
placed in the payload under a key `TO_REDACT` does not cover shows up in the output.

Every import is inside a function: the integration is only importable once the
session-scoped conftest fixture has set the path up.
"""

from __future__ import annotations

import asyncio
import json
import types
from dataclasses import dataclass

import pytest


def _redact(data, keys):
    if isinstance(data, dict):
        return {k: "**REDACTED**" if k in keys else _redact(v, keys) for k, v in data.items()}
    if isinstance(data, list):
        return [_redact(item, keys) for item in data]
    return data


@dataclass
class _Metrics:
    status: str = "charging"
    charger_info: dict | None = None


class _Client:
    dp_profile = "generic_v1"

    async def async_get_raw_dps(self):
        return {"109": "WORKING", "ip": "192.168.1.50"}


def _entry(options=None, runtime=True):
    entry = types.SimpleNamespace(
        entry_id="e1",
        title="Garage",
        data={
            "host": "192.168.1.50",
            "device_id": "bf123secret",
            "local_key": "SUPERSECRETKEY",
            "mac": "aa:bb:cc:dd:ee:ff",
            "cloud_api_key": "CLOUDKEY",
            "cloud_api_secret": "CLOUDSECRET",
            "protocol_version": "3.5",
        },
        options=options or {},
    )
    if runtime:
        coordinator = types.SimpleNamespace(
            data=_Metrics(charger_info={"serial": "SN-12345", "model": "X"}),
            connection_health={"host": "192.168.1.50", "last_discovery": {"gwId": "bf123secret"}},
        )
        entry.runtime_data = types.SimpleNamespace(client=_Client(), coordinator=coordinator)
    else:
        entry.runtime_data = None
    return entry


def _hass(states=None):
    return types.SimpleNamespace(
        states=types.SimpleNamespace(get=lambda eid: (states or {}).get(eid))
    )


@pytest.fixture(autouse=True)
def _real_redaction(monkeypatch):
    from tuya_ev_charger import diagnostics

    monkeypatch.setattr(diagnostics, "async_redact_data", _redact)


def _dump(entry, hass=None):
    from tuya_ev_charger.diagnostics import async_get_config_entry_diagnostics

    return asyncio.run(async_get_config_entry_diagnostics(hass or _hass(), entry))


def test_no_secret_survives_into_the_dump():
    text = json.dumps(_dump(_entry()))

    for secret in (
        "SUPERSECRETKEY",
        "CLOUDKEY",
        "CLOUDSECRET",
        "bf123secret",
        "aa:bb:cc:dd:ee:ff",
        "192.168.1.50",
        "SN-12345",
    ):
        assert secret not in text, f"{secret} would be published"


def test_what_is_useful_for_a_bug_report_is_still_there():
    dump = _dump(_entry())

    assert dump["entry"]["title"] == "Garage"
    assert dump["entry"]["data"]["protocol_version"] == "3.5"
    assert dump["client"] == {"dp_profile": "generic_v1"}
    assert dump["coordinator_data"]["status"] == "charging"
    assert dump["raw_dps"]["109"] == "WORKING"
    assert dump["connection"] is not None


def test_the_integration_version_comes_from_the_manifest():
    import json as _json
    import pathlib

    import tuya_ev_charger

    manifest = _json.loads(
        (pathlib.Path(tuya_ev_charger.__file__).parent / "manifest.json").read_text()
    )

    assert _dump(_entry())["integration"]["version"] == manifest["version"]


def test_an_entry_that_failed_to_load_still_produces_a_dump():
    dump = _dump(_entry(runtime=False))

    assert dump["coordinator_data"] is None
    assert dump["raw_dps"] is None
    assert dump["connection"] is None
    assert dump["client"] == {"dp_profile": None}


def test_configured_sensors_are_reported_with_their_state_and_unit():
    state = types.SimpleNamespace(
        state="-1500",
        attributes={
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
        },
    )
    entry = _entry(options={"surplus_sensor_entity_id": "sensor.grid"})

    sensors = _dump(entry, _hass({"sensor.grid": state}))["configured_surplus_sensors"]

    assert sensors["surplus_sensor_entity_id"] == {
        "entity_id": "sensor.grid",
        "state": "-1500",
        "unit_of_measurement": "W",
        "device_class": "power",
        "state_class": "measurement",
    }


def test_a_configured_sensor_that_does_not_exist_is_reported_as_such():
    entry = _entry(options={"surplus_sensor_entity_id": "sensor.gone"})

    sensors = _dump(entry)["configured_surplus_sensors"]

    assert sensors["surplus_sensor_entity_id"] == {"entity_id": "sensor.gone", "state": None}


def test_blank_sensor_options_are_skipped():
    entry = _entry(options={"surplus_sensor_entity_id": "  "})

    assert _dump(entry)["configured_surplus_sensors"] == {}
