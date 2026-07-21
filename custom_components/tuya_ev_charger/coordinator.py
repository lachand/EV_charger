from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .cloud import TuyaCloudError, async_fetch_local_key
from .const import (
    CONF_CLOUD_API_KEY,
    CONF_CLOUD_API_SECRET,
    CONF_CLOUD_REGION,
    CONF_DEVICE_ID,
    DEFAULT_CLOUD_REGION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOCAL_KEY_REFRESH_COOLDOWN_SECONDS,
    REDISCOVERY_COOLDOWN_SECONDS,
    REDISCOVERY_SCAN_SECONDS,
)
from .discovery import async_scan_devices_by_id
from .tuya_ev_charger import EVMetrics, TuyaEVChargerClient

LOGGER = logging.getLogger(__name__)


class TuyaEVChargerDataUpdateCoordinator(DataUpdateCoordinator[EVMetrics]):
    def __init__(
        self,
        hass: HomeAssistant,
        client: TuyaEVChargerClient,
        entry: ConfigEntry,
        update_interval: timedelta = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.client = client
        self.entry = entry
        self.last_discovery: dict | None = None
        self.new_local_key: str | None = None
        self._last_rediscovery_at: float = 0.0
        self._last_key_refresh_at: float = 0.0

    async def _async_update_data(self) -> EVMetrics:
        metrics = await self._async_fetch_metrics()
        if metrics is not None:
            return metrics

        # Communication failed: after a power cycle the charger's DHCP IP may
        # have changed. Relocate it (scan + live read) and retry once before failing.
        if await self._async_try_rediscover_host():
            metrics = await self._async_fetch_metrics()
            if metrics is not None:
                return metrics

        # Still failing while the control port answers: the payload does not
        # decrypt, which means the local_key was rotated by a re-pairing.
        if await self._async_try_refresh_local_key():
            metrics = await self._async_fetch_metrics()
            if metrics is not None:
                return metrics

        raise UpdateFailed(
            f"Charger unreachable at {self.client.host} (no telemetry received)."
        )

    async def _async_fetch_metrics(self) -> EVMetrics | None:
        try:
            return await self.client.async_get_metrics()
        except Exception as err:  # noqa: BLE001 - surfaced as UpdateFailed upstream
            LOGGER.debug("Charger poll failed: %s", err)
            return None

    async def _async_try_rediscover_host(self) -> bool:
        """Relocate the charger when its IP changed.

        Scans the LAN for Tuya broadcasts. If our device_id is announced, its IP
        is trusted directly (definitive identity). Otherwise — a stale or wrong
        stored device_id — we fall back to probing candidates with our local_key
        (a real status/voltage read) and adopt the one that answers. Only the
        in-memory client host is updated (immediate recovery, no config-entry
        reload); the new IP is persisted lazily on the next setup. Throttled so
        an offline charger does not scan on every poll.
        """
        now = self.hass.loop.time()
        if now - self._last_rediscovery_at < REDISCOVERY_COOLDOWN_SECONDS:
            return False
        self._last_rediscovery_at = now

        candidates = await async_scan_devices_by_id(
            self.hass, scantime=REDISCOVERY_SCAN_SECONDS
        )
        if not candidates:
            LOGGER.debug("Re-discovery scan found no Tuya devices on the network.")
            return False

        device_id = str(self.entry.data.get(CONF_DEVICE_ID, "")).strip()
        current_host = self.client.host

        # Strong path: our device_id is broadcasting -> trust the advertised IP.
        mine = candidates.get(device_id) if device_id else None
        if mine and mine.get("ip"):
            new_host = str(mine["ip"]).strip()
            if new_host and new_host != current_host:
                LOGGER.info(
                    "Charger %s found at %s (was %s) via broadcast; updating host.",
                    device_id,
                    new_host,
                    current_host,
                )
                self.last_discovery = mine
                await self.client.async_update_host(new_host)
                return True
            # Already at the advertised IP: relocation cannot fix the failure
            # (the charger is refusing/ignoring our reads at this address).
            LOGGER.debug(
                "Charger %s still advertises %s; connection issue is not an IP change.",
                device_id,
                current_host,
            )
            return False

        # Fallback: device_id not broadcasting (stale/wrong id). Identify our
        # charger by a live read with our local_key.
        for host in self._other_candidate_hosts(candidates, current_host):
            if not await self.client.async_probe_host(host):
                continue
            LOGGER.info(
                "Charger confirmed at %s (was %s) via live telemetry read.",
                host,
                current_host,
            )
            self.last_discovery = self._discovery_for_host(candidates, host)
            await self.client.async_update_host(host)
            return True

        LOGGER.debug(
            "Re-discovery found %d device(s) but none matched our device_id or local_key.",
            len(candidates),
        )
        return False

    async def _async_try_refresh_local_key(self) -> bool:
        """Re-download the local_key from the Tuya Cloud after a re-pairing.

        Only attempted when the charger's control port *does* answer: a reachable
        port with unreadable payloads points at a rotated key rather than a
        network problem. Requires cloud credentials to have been configured;
        without them this is a no-op. Heavily throttled (cloud API quotas).
        """
        data = self.entry.data
        api_key = str(data.get(CONF_CLOUD_API_KEY, "")).strip()
        api_secret = str(data.get(CONF_CLOUD_API_SECRET, "")).strip()
        device_id = str(data.get(CONF_DEVICE_ID, "")).strip()
        if not (api_key and api_secret and device_id):
            return False

        now = self.hass.loop.time()
        if now - self._last_key_refresh_at < LOCAL_KEY_REFRESH_COOLDOWN_SECONDS:
            return False

        if not await self.client.async_tcp_reachable():
            # Port is down: this is a connectivity problem, not a key problem.
            return False
        self._last_key_refresh_at = now

        region = str(data.get(CONF_CLOUD_REGION, DEFAULT_CLOUD_REGION)).strip() or DEFAULT_CLOUD_REGION
        try:
            new_key = await async_fetch_local_key(
                self.hass, region, api_key, api_secret, device_id
            )
        except TuyaCloudError as err:
            LOGGER.warning("Could not refresh local_key from Tuya Cloud: %s", err)
            return False

        if not new_key or new_key == self.client.local_key:
            LOGGER.debug("Tuya Cloud returned no new local_key for %s.", device_id)
            return False

        LOGGER.info(
            "local_key for charger %s changed (re-pairing); adopting the new key.",
            device_id,
        )
        await self.client.async_update_local_key(new_key)
        self.new_local_key = new_key
        return True

    @staticmethod
    def _other_candidate_hosts(candidates: dict[str, dict], current_host: str) -> list[str]:
        """Discovered IPs to probe, excluding the already-failed current host."""
        hosts: list[str] = []
        for info in candidates.values():
            host = str(info.get("ip", "")).strip()
            if host and host != current_host and host not in hosts:
                hosts.append(host)
        return hosts

    @staticmethod
    def _discovery_for_host(candidates: dict[str, dict], host: str) -> dict:
        for info in candidates.values():
            if str(info.get("ip", "")).strip() == host:
                return info
        return {"ip": host}
