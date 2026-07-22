"""Optional Tuya Cloud helpers.

Used strictly to obtain device *credentials* — never telemetry. Two purposes:

* config flow: list the user's devices so device_id/local_key can be filled in
  automatically instead of being copied by hand;
* runtime: re-download the local_key after the charger is re-paired, since
  re-pairing rotates the key and silently breaks local control.

Every call here performs network I/O (``tinytuya.Cloud`` requests a token in its
constructor), so all of it must run in an executor.
"""

from __future__ import annotations

import logging
from typing import Any

import tinytuya  # type: ignore
from homeassistant.core import HomeAssistant

LOGGER = logging.getLogger(__name__)


class TuyaCloudError(Exception):
    """Raised when the Tuya Cloud API cannot be queried."""


def _sync_fetch_devices(
    region: str,
    api_key: str,
    api_secret: str,
    device_id: str | None,
) -> list[dict[str, Any]]:
    """Blocking Tuya Cloud device listing — run in an executor.

    Credentials are always passed explicitly so ``tinytuya`` never falls back to
    reading its ``tinytuya.json`` config file from the working directory.
    """
    try:
        cloud = tinytuya.Cloud(
            apiRegion=region,
            apiKey=api_key,
            apiSecret=api_secret,
            apiDeviceID=device_id or None,
        )
    except Exception as err:
        raise TuyaCloudError(f"Tuya Cloud authentication failed: {err}") from err

    if getattr(cloud, "error", None):
        raise TuyaCloudError(f"Tuya Cloud authentication failed: {cloud.error}")

    try:
        devices: Any = cloud.getdevices()
    except Exception as err:
        raise TuyaCloudError(f"Tuya Cloud device listing failed: {err}") from err

    # getdevices() returns a list normally, or an error_json dict on failure.
    if isinstance(devices, dict):
        raise TuyaCloudError(f"Tuya Cloud returned an error: {devices}")
    if not isinstance(devices, list):
        raise TuyaCloudError("Unexpected Tuya Cloud device list payload.")

    return [dev for dev in devices if isinstance(dev, dict) and dev.get("id")]


async def async_fetch_devices(
    hass: HomeAssistant,
    region: str,
    api_key: str,
    api_secret: str,
    device_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return the user's Tuya devices (each with its ``key`` = local_key)."""
    return await hass.async_add_executor_job(
        _sync_fetch_devices, region, api_key, api_secret, device_id
    )


async def async_fetch_local_key(
    hass: HomeAssistant,
    region: str,
    api_key: str,
    api_secret: str,
    device_id: str,
) -> str | None:
    """Return the current local_key for ``device_id``, or None if not found."""
    devices = await async_fetch_devices(
        hass, region, api_key, api_secret, device_id
    )
    for device in devices:
        if str(device.get("id", "")).strip() == str(device_id).strip():
            key = str(device.get("key", "") or "").strip()
            return key or None
    return None
