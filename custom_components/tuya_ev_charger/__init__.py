from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.device_registry import format_mac

from .const import (
    CHARGER_PROFILES,
    CONF_CHARGER_PROFILE,
    CONF_CHARGER_PROFILE_JSON,
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MAC,
    CONF_PROTOCOL_VERSION,
    CONF_SCAN_INTERVAL,
    DEFAULT_CHARGER_PROFILE,
    DEFAULT_CHARGER_PROFILE_JSON,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    PLATFORMS,
)
from .coordinator import TuyaEVChargerDataUpdateCoordinator
from .tuya_ev_charger import TuyaEVChargerClient

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class TuyaEVChargerRuntimeData:
    client: TuyaEVChargerClient
    coordinator: TuyaEVChargerDataUpdateCoordinator


def _scan_interval_seconds(entry: ConfigEntry) -> int:
    try:
        configured_value = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SECONDS))
    except (TypeError, ValueError):
        configured_value = DEFAULT_SCAN_INTERVAL_SECONDS
    return max(MIN_SCAN_INTERVAL_SECONDS, min(MAX_SCAN_INTERVAL_SECONDS, configured_value))


def _charger_profile(entry: ConfigEntry) -> str:
    configured_value = entry.options.get(
        CONF_CHARGER_PROFILE,
        entry.data.get(CONF_CHARGER_PROFILE, DEFAULT_CHARGER_PROFILE),
    )
    normalized = str(configured_value).strip().lower()
    if normalized in CHARGER_PROFILES:
        return normalized
    return DEFAULT_CHARGER_PROFILE


def _charger_profile_json(entry: ConfigEntry) -> str:
    configured_value = entry.options.get(
        CONF_CHARGER_PROFILE_JSON,
        entry.data.get(CONF_CHARGER_PROFILE_JSON, DEFAULT_CHARGER_PROFILE_JSON),
    )
    return str(configured_value or "").strip()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = TuyaEVChargerClient(
        device_id=entry.data[CONF_DEVICE_ID],
        host=entry.data[CONF_HOST],
        local_key=entry.data[CONF_LOCAL_KEY],
        protocol_version=entry.data[CONF_PROTOCOL_VERSION],
        charger_profile=_charger_profile(entry),
        charger_profile_json=_charger_profile_json(entry),
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
        entry=entry,
        update_interval=timedelta(seconds=_scan_interval_seconds(entry)),
    )

    try:
        # The coordinator relocates the charger by device_id if its DHCP IP
        # changed, so the first refresh can succeed even from a stale host.
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(
            f"Unable to fetch initial charger state for {entry.title}: {err}"
        ) from err

    # Persist any relocated IP and learn the MAC (used by DHCP auto-discovery).
    # Done before the update listener is registered so it does not trigger a reload.
    await _async_reconcile_network_info(hass, entry, coordinator)

    runtime_data = TuyaEVChargerRuntimeData(client=client, coordinator=coordinator)
    entry.runtime_data = runtime_data
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    LOGGER.debug("Tuya EV charger integration initialized: %s", entry.title)
    return True


async def _async_reconcile_network_info(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: TuyaEVChargerDataUpdateCoordinator,
) -> None:
    """Sync the config entry with the charger's real network identity.

    Persists a host relocated in-memory by the coordinator and, when a relocation
    scan happened, records the MAC so Home Assistant DHCP discovery can auto-update
    the IP later. Uses only data the coordinator already gathered (no extra scan),
    and must run before the update listener is registered to avoid an entry reload.
    """
    updates: dict[str, str] = {}

    live_host = coordinator.client.host
    if live_host and live_host != entry.data.get(CONF_HOST):
        updates[CONF_HOST] = live_host

    if not _normalized_mac(entry.data.get(CONF_MAC)) and coordinator.last_discovery:
        mac = _normalized_mac(coordinator.last_discovery.get("mac"))
        if mac:
            updates[CONF_MAC] = mac

    if updates:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, **updates}
        )


def _normalized_mac(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or ":" not in text:
        return None
    return format_mac(text)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
