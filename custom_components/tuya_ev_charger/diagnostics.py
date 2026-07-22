from __future__ import annotations

import json
import pathlib
from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import TuyaEVChargerRuntimeData
from .const import (
    CONF_CLOUD_API_KEY,
    CONF_CLOUD_API_SECRET,
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MAC,
    CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_ENTITY_ID,
    CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
    CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
    CONF_SURPLUS_SENSOR_ENTITY_ID,
)

# Diagnostics are routinely attached to public GitHub issues, so everything that
# identifies or grants access to the charger or the user's Tuya account must be
# redacted. The cloud credentials in particular are account-wide, not just
# device-scoped.
TO_REDACT = {
    CONF_HOST,
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MAC,
    CONF_CLOUD_API_KEY,
    CONF_CLOUD_API_SECRET,
    # A discovery record carries the charger's identity under tinytuya's own
    # names, which do not match the config keys above.
    "ip",
    "gwId",
    "key",
    "serial",
    "serial_number",
    "sn",
}


def _integration_version() -> str | None:
    """Read the version from the manifest.

    Reports invariably omit it, and half the answers depend on it.
    """
    try:
        manifest = json.loads(
            (pathlib.Path(__file__).parent / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    return manifest.get("version")


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    runtime_data: TuyaEVChargerRuntimeData | None = getattr(entry, "runtime_data", None)
    coordinator_data: dict[str, Any] | None = None
    profile: str | None = None
    raw_dps: dict[str, Any] | None = None
    # Connection health and the discovery result are the two things asked for by
    # hand in every connection report (#5, #7); they belong in the dump rather
    # than in a follow-up comment.
    connection: dict[str, Any] | None = None
    if runtime_data is not None:
        connection = runtime_data.coordinator.connection_health
    if runtime_data is not None and runtime_data.coordinator.data is not None:
        coordinator_data = asdict(runtime_data.coordinator.data)
        profile = runtime_data.client.dp_profile
        raw_dps = await runtime_data.client.async_get_raw_dps()

    sensors: dict[str, Any] = {}
    for option_key in (
        CONF_SURPLUS_SENSOR_ENTITY_ID,
        CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
        CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
        CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_ENTITY_ID,
        CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
    ):
        entity_id = str(entry.options.get(option_key, "")).strip()
        if not entity_id:
            continue
        state = hass.states.get(entity_id)
        if state is None:
            sensors[option_key] = {"entity_id": entity_id, "state": None}
            continue
        sensors[option_key] = {
            "entity_id": entity_id,
            "state": state.state,
            "unit_of_measurement": state.attributes.get("unit_of_measurement"),
            "device_class": state.attributes.get("device_class"),
            "state_class": state.attributes.get("state_class"),
        }

    payload: dict[str, Any] = {
        "entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "integration": {"version": _integration_version()},
        "client": {"dp_profile": profile},
        "connection": connection,
        "coordinator_data": coordinator_data,
        "raw_dps": raw_dps,
        "configured_surplus_sensors": sensors,
    }
    return async_redact_data(payload, TO_REDACT)
