from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import TuyaEVChargerRuntimeData
from .const import CONF_DEVICE_ID, CONF_LOCAL_KEY

TO_REDACT = {
    CONF_HOST,
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    "serial",
    "serial_number",
    "sn",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    _ = hass
    runtime_data: TuyaEVChargerRuntimeData | None = getattr(entry, "runtime_data", None)
    coordinator_data: dict[str, Any] | None = None
    profile: str | None = None
    raw_dps: dict[str, Any] | None = None
    if runtime_data is not None and runtime_data.coordinator.data is not None:
        coordinator_data = asdict(runtime_data.coordinator.data)
        profile = runtime_data.client.dp_profile
        raw_dps = await runtime_data.client.async_get_raw_dps()

    payload: dict[str, Any] = {
        "entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "client": {"dp_profile": profile},
        "coordinator_data": coordinator_data,
        "raw_dps": raw_dps,
    }
    return async_redact_data(payload, TO_REDACT)
