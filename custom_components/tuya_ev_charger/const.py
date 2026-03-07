from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "tuya_ev_charger"

CONF_DEVICE_ID = "device_id"
CONF_LOCAL_KEY = "local_key"
CONF_PROTOCOL_VERSION = "protocol_version"

DEFAULT_NAME = "Tuya EV Charger"
DEFAULT_PROTOCOL_VERSION = "3.5"
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("3.3", "3.4", "3.5")
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)

PLATFORMS: tuple[Platform, ...] = (
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SWITCH,
)

ALLOWED_CURRENTS: tuple[int, ...] = (6, 8, 10, 13, 16)

DP_WORK_STATE = "109"
DP_METRICS = "102"
DP_DO_CHARGE = "140"
DP_CURRENT_TARGET = "150"
DP_MAX_CURRENT_CFG = "152"
