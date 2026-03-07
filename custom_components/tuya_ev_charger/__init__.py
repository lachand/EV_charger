from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_PROTOCOL_VERSION,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import TuyaEVChargerDataUpdateCoordinator
from .tuya_ev_charger import TuyaEVChargerClient

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class TuyaEVChargerRuntimeData:
    client: TuyaEVChargerClient
    coordinator: TuyaEVChargerDataUpdateCoordinator


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

    coordinator = TuyaEVChargerDataUpdateCoordinator(hass=hass, client=client)

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(
            f"Unable to fetch initial charger state for {entry.title}: {err}"
        ) from err

    runtime_data = TuyaEVChargerRuntimeData(client=client, coordinator=coordinator)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime_data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    LOGGER.debug("Tuya EV charger integration initialized: %s", entry.title)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
