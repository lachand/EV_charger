from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "tuya_ev_charger"

ATTR_CHARGER_TOKEN = "tuya_ev_charger_token"
ATTR_CHARGER_ENTRY_ID = "tuya_ev_charger_entry_id"
ATTR_CHARGER_DEVICE_ID = "tuya_ev_charger_device_id"
ATTR_CARD_ROLE = "tuya_ev_charger_card_role"
ATTR_CARD_INDEX = "tuya_ev_charger_card_index"

CARD_ROLE_POWER = "power"
CARD_ROLE_CURRENT = "current"
CARD_ROLE_CHARGE_CURRENT = "charge_current"
CARD_ROLE_CHARGE_SESSION = "charge_session"
CARD_ROLE_VOLTAGE = "voltage"
CARD_ROLE_TEMPERATURE = "temperature"
CARD_ROLE_WORK_STATE = "work_state"
CARD_ROLE_SELFTEST = "selftest"
CARD_ROLE_ALARM = "alarm"
CARD_ROLE_REBOOT = "reboot"
CARD_ROLE_SCHEDULE_ENABLED = "schedule_enabled"
CARD_ROLE_SCHEDULE_START = "schedule_start"
CARD_ROLE_SCHEDULE_END = "schedule_end"

CARD_ROLE_INDEX: dict[str, int] = {
    CARD_ROLE_POWER: 10,
    CARD_ROLE_CURRENT: 20,
    CARD_ROLE_CHARGE_CURRENT: 30,
    CARD_ROLE_CHARGE_SESSION: 40,
    CARD_ROLE_VOLTAGE: 130,
    CARD_ROLE_TEMPERATURE: 140,
    CARD_ROLE_WORK_STATE: 150,
    CARD_ROLE_SELFTEST: 160,
    CARD_ROLE_ALARM: 170,
    CARD_ROLE_REBOOT: 180,
    CARD_ROLE_SCHEDULE_ENABLED: 210,
    CARD_ROLE_SCHEDULE_START: 220,
    CARD_ROLE_SCHEDULE_END: 230,
}

CONF_CHARGER_PROFILE = "charger_profile"
CONF_CHARGER_PROFILE_JSON = "charger_profile_json"
CONF_DEVICE_ID = "device_id"
CONF_LOCAL_KEY = "local_key"
CONF_MAC = "mac"
CONF_PROTOCOL_VERSION = "protocol_version"
CONF_SCAN_INTERVAL = "scan_interval"

CHARGER_PROFILE_DEPOW_V2 = "depow_v2"
CHARGER_PROFILE_GENERIC_V1 = "generic_v1"
CHARGER_PROFILE_CUSTOM_JSON = "custom_json"
CHARGER_PROFILES: tuple[str, ...] = (
    CHARGER_PROFILE_CUSTOM_JSON,
    CHARGER_PROFILE_DEPOW_V2,
    CHARGER_PROFILE_GENERIC_V1,
)

DEFAULT_NAME = "Tuya EV Charger"
DEFAULT_PROTOCOL_VERSION = "3.5"
DEFAULT_CHARGER_PROFILE = CHARGER_PROFILE_DEPOW_V2
DEFAULT_CHARGER_PROFILE_JSON = ""
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("3.3", "3.4", "3.5")
DEFAULT_SCAN_INTERVAL_SECONDS = 30
MIN_SCAN_INTERVAL_SECONDS = 5
MAX_SCAN_INTERVAL_SECONDS = 300
DEFAULT_SCAN_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS)

# Automatic IP re-discovery. The charger's DHCP IP changes on power cycle. We
# listen for Tuya UDP broadcasts, then confirm the right device by a live read
# with our local_key. REDISCOVERY_SCAN_SECONDS is the UDP listen window;
# REDISCOVERY_COOLDOWN_SECONDS throttles how often the coordinator rescans while
# the charger stays unreachable (e.g. simply unplugged).
REDISCOVERY_SCAN_SECONDS = 6
REDISCOVERY_COOLDOWN_SECONDS = 120

PLATFORMS: tuple[Platform, ...] = (
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.TIME,
)

ALLOWED_CURRENTS: tuple[int, ...] = (6, 8, 10, 13, 16)

DP_WORK_STATE = "101"
DP_CHARGER_INFO = "106"
DP_METRICS = "102"
DP_SCHEDULE = "151"
DP_SELFTEST = "103"
DP_ALARM = "104"
DP_CHARGE_HISTORY = "105"
DP_ADJUST_CURRENT = "107"
DP_DOWNCOUNTER = "108"
DP_WORK_STATE_DEBUG = "109"
DP_DO_CHARGE = "140"
DP_DO_RESET = "141"
DP_CURRENT_TARGET = "150"
DP_MAX_CURRENT_CFG = "152"
DP_SOCKET_CFG = "154"
DP_NFC_CFG = "155"
DP_EARCH_FREE_CFG = "156"
DP_PRODUCT_VARIANT = "157"
DP_REBOOT = "142"
DP_HEARTBEAT = "188"
DP_NUM = "189"
