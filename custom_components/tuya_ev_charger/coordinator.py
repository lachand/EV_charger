from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_DEVICE_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
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
        self._last_rediscovery_at: float = 0.0

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
        """Relocate the charger when its IP changed, confirming by a live read.

        Scans the LAN for Tuya devices, then probes candidate IPs with our own
        local_key (a real status/voltage read). The device that answers is our
        charger, whatever its advertised MAC or device_id. Candidates advertising
        our device_id are tried first. Only the in-memory client host is updated
        (immediate recovery, no config-entry reload); the new IP is persisted
        lazily on the next setup. Throttled so an offline charger does not scan
        on every poll.
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

        for host in self._ordered_candidate_hosts(candidates, device_id, current_host):
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
            "Re-discovery probed %d candidate(s) but none answered our local_key.",
            len(candidates),
        )
        return False

    @staticmethod
    def _ordered_candidate_hosts(
        candidates: dict[str, dict],
        device_id: str,
        current_host: str,
    ) -> list[str]:
        """Candidate IPs to probe: our device_id's IP first, then the rest.

        The current (already-failed) host is skipped since the normal poll just
        tried it.
        """
        ordered: list[str] = []
        mine = candidates.get(device_id) if device_id else None
        if mine and mine.get("ip"):
            ordered.append(str(mine["ip"]))
        for info in candidates.values():
            host = str(info.get("ip", "")).strip()
            if host and host not in ordered:
                ordered.append(host)
        return [host for host in ordered if host and host != current_host]

    @staticmethod
    def _discovery_for_host(candidates: dict[str, dict], host: str) -> dict:
        for info in candidates.values():
            if str(info.get("ip", "")).strip() == host:
                return info
        return {"ip": host}
