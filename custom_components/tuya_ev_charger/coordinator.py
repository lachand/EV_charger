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
)
from .discovery import async_find_device_by_id
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
        # have changed. Relocate it by device_id and retry once before failing.
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
        """Relocate the charger by device_id when its IP changed.

        Only the in-memory client host is updated, so recovery is immediate and
        does not trigger a config-entry reload; the new IP is persisted to the
        entry lazily on the next setup. Throttled so a genuinely offline charger
        does not trigger a scan on every poll.
        """
        now = self.hass.loop.time()
        if now - self._last_rediscovery_at < REDISCOVERY_COOLDOWN_SECONDS:
            return False
        self._last_rediscovery_at = now

        device_id = str(self.entry.data.get(CONF_DEVICE_ID, "")).strip()
        if not device_id:
            return False

        info = await async_find_device_by_id(self.hass, device_id)
        if info is None:
            return False
        self.last_discovery = info

        new_host = str(info.get("ip", "")).strip()
        if not new_host or new_host == self.client.host:
            return False

        LOGGER.info(
            "Charger %s moved from %s to %s; updating host.",
            device_id,
            self.client.host,
            new_host,
        )
        await self.client.async_update_host(new_host)
        return True
