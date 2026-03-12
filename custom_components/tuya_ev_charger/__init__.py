from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_PROTOCOL_VERSION,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    PLATFORMS,
)
from .coordinator import TuyaEVChargerDataUpdateCoordinator
from .solar_surplus import SolarSurplusController
from .tuya_ev_charger import TuyaEVChargerClient

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class TuyaEVChargerRuntimeData:
    client: TuyaEVChargerClient
    coordinator: TuyaEVChargerDataUpdateCoordinator
    solar_surplus_controller: SolarSurplusController | None = None


def _scan_interval_seconds(entry: ConfigEntry) -> int:
    try:
        configured_value = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS))
    except (TypeError, ValueError):
        configured_value = DEFAULT_SCAN_INTERVAL_SECONDS
    return max(MIN_SCAN_INTERVAL_SECONDS, min(MAX_SCAN_INTERVAL_SECONDS, configured_value))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = TuyaEVChargerClient(
        device_id=entry.data[CONF_DEVICE_ID],
        host=entry.data[CONF_HOST],
        local_key=entry.data[CONF_LOCAL_KEY],
        protocol_version=entry.data[CONF_PROTOCOL_VERSION],
    )

    try:
        await client.async_connect()
    except Exception as err:
        raise ConfigEntryNotReady(
            f"Unable to initialize charger client for {entry.title}: {err}"
        ) from err

    coordinator = TuyaEVChargerDataUpdateCoordinator(
        hass=hass,
        client=client,
        update_interval=timedelta(seconds=_scan_interval_seconds(entry)),
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(
            f"Unable to fetch initial charger state for {entry.title}: {err}"
        ) from err

    runtime_data = TuyaEVChargerRuntimeData(client=client, coordinator=coordinator)
    runtime_data.solar_surplus_controller = SolarSurplusController(
        hass=hass,
        entry=entry,
        client=client,
        coordinator=coordinator,
    )
    await runtime_data.solar_surplus_controller.async_start()
    entry.runtime_data = runtime_data
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    LOGGER.debug("Tuya EV charger integration initialized: %s", entry.title)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime_data: TuyaEVChargerRuntimeData | None = entry.runtime_data
    if runtime_data is not None and runtime_data.solar_surplus_controller is not None:
        await runtime_data.solar_surplus_controller.async_shutdown()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
