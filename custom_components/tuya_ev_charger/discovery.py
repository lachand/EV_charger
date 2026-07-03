"""Local UDP discovery helpers for Tuya EV chargers.

Tuya devices broadcast their identity (``gwId`` / device_id) and current LAN IP
over UDP (ports 6666/6667). The device_id and local_key are stable across power
cycles; only the DHCP-assigned IP changes. We therefore relocate a charger after
its IP changes by listening for that broadcast and matching on device_id — no
IP-range brute force and no state-changing command required.
"""

from __future__ import annotations

import logging
from typing import Any

import tinytuya  # type: ignore
from tinytuya import scanner  # type: ignore

from homeassistant.core import HomeAssistant

from .const import REDISCOVERY_SCAN_SECONDS

LOGGER = logging.getLogger(__name__)


def _sync_scan_devices_by_id(
    scantime: int,
    wantids: list[str] | None,
) -> dict[str, dict[str, Any]]:
    """Blocking tinytuya UDP scan keyed by device_id (gwId).

    Must be run in an executor. When ``wantids`` is provided the scan stops as
    soon as those device IDs are seen, so it usually returns well before
    ``scantime`` elapses and only waits the full window when the device is absent.
    """
    try:
        devices = scanner.devices(
            verbose=False,
            scantime=scantime,
            color=False,
            poll=False,
            byID=True,
            wantids=wantids,
        )
    except Exception:  # noqa: BLE001 - discovery is best-effort
        LOGGER.debug("Tuya UDP discovery scan failed.", exc_info=True)
        return {}

    return {
        dev_id: info
        for dev_id, info in devices.items()
        if isinstance(info, dict) and info.get("ip")
    }


async def async_scan_devices_by_id(
    hass: HomeAssistant,
    scantime: int = tinytuya.SCANTIME,
) -> dict[str, dict[str, Any]]:
    """Return all discovered Tuya devices keyed by device_id (gwId)."""
    return await hass.async_add_executor_job(_sync_scan_devices_by_id, scantime, None)


async def async_find_device_by_id(
    hass: HomeAssistant,
    device_id: str,
    scantime: int = REDISCOVERY_SCAN_SECONDS,
) -> dict[str, Any] | None:
    """Locate a single Tuya device by its device_id.

    Returns the raw discovery info (``ip``, ``mac``, ``version``, ...) or ``None``
    when the device does not announce itself within ``scantime`` seconds.
    """
    device_id = str(device_id).strip()
    if not device_id:
        return None
    devices = await hass.async_add_executor_job(
        _sync_scan_devices_by_id, scantime, [device_id]
    )
    info = devices.get(device_id)
    return info if isinstance(info, dict) else None
