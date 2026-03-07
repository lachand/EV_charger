from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .tuya_ev_charger import EVMetrics, TuyaEVChargerClient

LOGGER = logging.getLogger(__name__)


class TuyaEVChargerDataUpdateCoordinator(DataUpdateCoordinator[EVMetrics]):
    def __init__(self, hass: HomeAssistant, client: TuyaEVChargerClient) -> None:
        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> EVMetrics:
        try:
            metrics = await self.client.async_get_metrics()
        except Exception as err:
            raise UpdateFailed(f"Communication error with charger: {err}") from err

        if metrics is None:
            raise UpdateFailed("No telemetry payload received from charger.")

        return metrics
