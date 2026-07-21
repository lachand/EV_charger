from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import device_registry as dr, selector
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .const import (
    CHARGER_PROFILES,
    CLOUD_REGIONS,
    CONF_CHARGER_PROFILE,
    CONF_CHARGER_PROFILE_JSON,
    CONF_CLOUD_API_KEY,
    CONF_CLOUD_API_SECRET,
    CONF_CLOUD_REGION,
    CONF_CONTINUOUS_CURRENT,
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MAC,
    CONF_PROTOCOL_VERSION,
    CONF_SCAN_INTERVAL,
    CONF_SURPLUS_ALLOW_BATTERY_DISCHARGE_FOR_EV,
    CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_ENTITY_ID,
    CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_INVERTED,
    CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
    CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
    CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
    CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    CONF_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
    CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
    CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    CONF_SURPLUS_MODE_ENABLED,
    CONF_SURPLUS_SENSOR_ENTITY_ID,
    CONF_SURPLUS_SENSOR_INVERTED,
    CONF_SURPLUS_START_THRESHOLD_W,
    CONF_SURPLUS_STOP_THRESHOLD_W,
    CONF_VEHICLES,
    DEFAULT_CHARGER_PROFILE,
    DEFAULT_CHARGER_PROFILE_JSON,
    DEFAULT_CLOUD_REGION,
    DEFAULT_CONTINUOUS_CURRENT,
    DEFAULT_NAME,
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_SURPLUS_ALLOW_BATTERY_DISCHARGE_FOR_EV,
    DEFAULT_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_INVERTED,
    DEFAULT_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
    DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
    DEFAULT_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    DEFAULT_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
    DEFAULT_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    DEFAULT_SURPLUS_MODE_ENABLED,
    DEFAULT_SURPLUS_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_SENSOR_INVERTED,
    DEFAULT_SURPLUS_START_THRESHOLD_W,
    DEFAULT_SURPLUS_STOP_THRESHOLD_W,
    DEFAULT_VEHICLES,
    DOMAIN,
    MAX_SCAN_INTERVAL_SECONDS,
    MAX_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MAX_SURPLUS_THRESHOLD_W,
    MIN_SCAN_INTERVAL_SECONDS,
    MIN_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MIN_SURPLUS_THRESHOLD_W,
    SUPPORTED_PROTOCOL_VERSIONS,
)
from .cloud import TuyaCloudError, async_fetch_devices
from .discovery import async_scan_devices_by_id
from .tuya_ev_charger import TuyaEVChargerClient

LOGGER = logging.getLogger(__name__)


class CannotConnectError(Exception):
    """Raised when the charger cannot be reached."""


def _build_credentials_schema(
    prefill: Mapping[str, Any] | None = None,
) -> vol.Schema:
    prefill = prefill or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=prefill.get(CONF_HOST, "")): str,
            vol.Required(
                CONF_DEVICE_ID,
                default=prefill.get(CONF_DEVICE_ID, ""),
            ): str,
            vol.Required(CONF_LOCAL_KEY, default=prefill.get(CONF_LOCAL_KEY, "")): str,
            vol.Required(
                CONF_PROTOCOL_VERSION,
                default=prefill.get(CONF_PROTOCOL_VERSION, DEFAULT_PROTOCOL_VERSION),
            ): vol.In(SUPPORTED_PROTOCOL_VERSIONS),
            vol.Required(
                CONF_CHARGER_PROFILE,
                default=prefill.get(CONF_CHARGER_PROFILE, DEFAULT_CHARGER_PROFILE),
            ): vol.In(CHARGER_PROFILES),
        }
    )


def _format_scan_mac(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or ":" not in text:
        return None
    return dr.format_mac(text)


async def _async_validate_input(
    hass: HomeAssistant,
    data: Mapping[str, Any],
) -> dict[str, str]:
    _ = hass
    client = TuyaEVChargerClient(
        device_id=str(data[CONF_DEVICE_ID]),
        host=str(data[CONF_HOST]),
        local_key=str(data[CONF_LOCAL_KEY]),
        protocol_version=str(data[CONF_PROTOCOL_VERSION]),
        charger_profile=str(data.get(CONF_CHARGER_PROFILE, DEFAULT_CHARGER_PROFILE)),
        charger_profile_json=str(data.get(CONF_CHARGER_PROFILE_JSON, "")),
    )
    await client.async_connect()
    metrics = await client.async_get_metrics()
    if metrics is None:
        raise CannotConnectError
    return {"title": f"{DEFAULT_NAME} ({data[CONF_HOST]})"}


class TuyaEVChargerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, dict] = {}
        self._prefill: dict[str, Any] = {}
        self._device_meta: dict[str, Any] = {}
        self._cloud_devices: dict[str, dict] = {}
        self._cloud_credentials: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> TuyaEVChargerOptionsFlow:
        return TuyaEVChargerOptionsFlow(config_entry)

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        if user_input is not None:
            mode = user_input["mode"]
            if mode == "scan":
                return await self.async_step_scan()
            if mode == "cloud":
                return await self.async_step_cloud()
            return await self.async_step_credentials()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("mode", default="scan"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value="scan", label="Scan network"),
                                selector.SelectOptionDict(
                                    value="cloud",
                                    label="Fetch credentials from Tuya Cloud",
                                ),
                                selector.SelectOptionDict(value="manual", label="Enter manually"),
                            ],
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_scan(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        if user_input is not None:
            selected = user_input["device"]
            if selected == "__manual__":
                self._prefill = {}
                self._device_meta = {}
            else:
                info = self._discovered.get(selected, {})
                self._prefill = {
                    CONF_HOST: info.get("ip", ""),
                    CONF_DEVICE_ID: selected,
                    CONF_PROTOCOL_VERSION: str(info.get("version", DEFAULT_PROTOCOL_VERSION)),
                }
                mac = _format_scan_mac(info.get("mac"))
                self._device_meta = {CONF_MAC: mac} if mac else {}
            return await self.async_step_credentials()

        self._discovered = await async_scan_devices_by_id(self.hass)

        if not self._discovered:
            self._prefill = {}
            return await self.async_step_credentials(errors={"base": "no_devices_found"})

        options = [
            selector.SelectOptionDict(
                value=dev_id,
                label=f"{dev_id}  —  {info['ip']}  (v{info.get('version', '?')})",
            )
            for dev_id, info in self._discovered.items()
        ] + [selector.SelectOptionDict(value="__manual__", label="Enter manually")]

        return self.async_show_form(
            step_id="scan",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_cloud(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Ask for Tuya IoT credentials and list the account's devices."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = str(user_input[CONF_CLOUD_API_KEY]).strip()
            api_secret = str(user_input[CONF_CLOUD_API_SECRET]).strip()
            region = str(user_input[CONF_CLOUD_REGION]).strip()
            hint_device_id = str(user_input.get(CONF_DEVICE_ID, "")).strip()
            try:
                devices = await async_fetch_devices(
                    self.hass, region, api_key, api_secret, hint_device_id or None
                )
            except TuyaCloudError as err:
                LOGGER.debug("Tuya Cloud lookup failed: %s", err)
                errors["base"] = "cloud_auth_failed"
            else:
                if not devices:
                    errors["base"] = "cloud_no_devices"
                else:
                    self._cloud_devices = {str(d["id"]): d for d in devices}
                    self._cloud_credentials = {
                        CONF_CLOUD_API_KEY: api_key,
                        CONF_CLOUD_API_SECRET: api_secret,
                        CONF_CLOUD_REGION: region,
                    }
                    return await self.async_step_cloud_device()

        return self.async_show_form(
            step_id="cloud",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CLOUD_API_KEY,
                        default=(user_input or {}).get(CONF_CLOUD_API_KEY, ""),
                    ): str,
                    vol.Required(
                        CONF_CLOUD_API_SECRET,
                        default=(user_input or {}).get(CONF_CLOUD_API_SECRET, ""),
                    ): str,
                    vol.Required(
                        CONF_CLOUD_REGION,
                        default=(user_input or {}).get(
                            CONF_CLOUD_REGION, DEFAULT_CLOUD_REGION
                        ),
                    ): vol.In(CLOUD_REGIONS),
                    vol.Optional(
                        CONF_DEVICE_ID,
                        default=(user_input or {}).get(CONF_DEVICE_ID, ""),
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_cloud_device(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Pick the charger among the cloud devices and locate it on the LAN."""
        if user_input is not None:
            device_id = user_input["device"]
            device = self._cloud_devices.get(device_id, {})
            local_key = str(device.get("key", "") or "").strip()

            # The cloud knows the credentials; the LAN scan knows the current IP.
            discovered = await async_scan_devices_by_id(self.hass)
            info = discovered.get(device_id, {})

            self._prefill = {
                CONF_HOST: str(info.get("ip", "") or device.get("ip", "") or ""),
                CONF_DEVICE_ID: device_id,
                CONF_LOCAL_KEY: local_key,
                CONF_PROTOCOL_VERSION: str(
                    info.get("version", device.get("version", DEFAULT_PROTOCOL_VERSION))
                ),
            }
            self._device_meta = dict(self._cloud_credentials)
            mac = _format_scan_mac(info.get("mac") or device.get("mac"))
            if mac:
                self._device_meta[CONF_MAC] = mac
            return await self.async_step_credentials()

        options = [
            selector.SelectOptionDict(
                value=dev_id,
                label=f"{device.get('name') or dev_id} — {dev_id}",
            )
            for dev_id, device in self._cloud_devices.items()
        ]
        return self.async_show_form(
            step_id="cloud_device",
            data_schema=vol.Schema(
                {
                    vol.Required("device"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                }
            ),
        )

    async def async_step_credentials(
        self,
        user_input: dict[str, Any] | None = None,
        errors: dict[str, str] | None = None,
    ) -> FlowResult:
        errors = errors or {}
        if user_input is not None:
            await self.async_set_unique_id(str(user_input[CONF_DEVICE_ID]))
            self._abort_if_unique_id_configured()
            try:
                info = await _async_validate_input(self.hass, user_input)
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except Exception:
                LOGGER.exception("Unexpected error while validating charger config.")
                errors["base"] = "unknown"
            else:
                entry_data = {**user_input, **self._device_meta}
                return self.async_create_entry(title=info["title"], data=entry_data)

        return self.async_show_form(
            step_id="credentials",
            data_schema=_build_credentials_schema(user_input or self._prefill),
            errors=errors,
        )

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> FlowResult:
        """Auto-update a charger's IP when its DHCP lease changes.

        Triggered (via ``registered_devices`` in the manifest) when a device this
        integration registered gets a new lease. We match the announced MAC to the
        owning config entry and update its host in place.
        """
        mac = dr.format_mac(discovery_info.macaddress)
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(
            connections={(dr.CONNECTION_NETWORK_MAC, mac)}
        )
        if device is None:
            return self.async_abort(reason="not_tuya_ev_charger")

        for entry_id in device.config_entries:
            entry = self.hass.config_entries.async_get_entry(entry_id)
            if entry is None or entry.domain != DOMAIN:
                continue
            unique_id = entry.unique_id or str(entry.data.get(CONF_DEVICE_ID, ""))
            if not unique_id:
                continue
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured(
                updates={CONF_HOST: discovery_info.ip}
            )

        return self.async_abort(reason="not_tuya_ev_charger")


class TuyaEVChargerOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        if user_input is not None:
            cleaned_input = dict(self._config_entry.options)
            cleaned_input.update(user_input)
            _normalize_optional_entity_value(cleaned_input, CONF_SURPLUS_SENSOR_ENTITY_ID)
            _normalize_optional_entity_value(
                cleaned_input,
                CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
            )
            _normalize_optional_entity_value(
                cleaned_input,
                CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
            )
            _normalize_optional_entity_value(
                cleaned_input,
                CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_ENTITY_ID,
            )
            _normalize_optional_entity_value(
                cleaned_input,
                CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
            )
            _normalize_text_value(
                cleaned_input,
                CONF_CHARGER_PROFILE_JSON,
                DEFAULT_CHARGER_PROFILE_JSON,
            )
            _normalize_text_value(cleaned_input, CONF_VEHICLES, DEFAULT_VEHICLES)
            _normalize_surplus_options(cleaned_input)
            return self.async_create_entry(data=cleaned_input)

        options = self._config_entry.options

        current_scan_interval = _option_int(
            options,
            CONF_SCAN_INTERVAL,
            DEFAULT_SCAN_INTERVAL_SECONDS,
            MIN_SCAN_INTERVAL_SECONDS,
            MAX_SCAN_INTERVAL_SECONDS,
        )
        charger_profile_json = _option_text(
            options,
            CONF_CHARGER_PROFILE_JSON,
            str(
                self._config_entry.data.get(
                    CONF_CHARGER_PROFILE_JSON,
                    DEFAULT_CHARGER_PROFILE_JSON,
                )
            ),
        )

        high_threshold = _option_int(
            options,
            CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
            _legacy_high_threshold_default(options),
            MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
            MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        )
        low_threshold = _option_int(
            options,
            CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
            min(DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT, high_threshold),
            MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
            MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        )
        if low_threshold >= high_threshold:
            low_threshold = max(MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, high_threshold - 1)
        max_battery_discharge = _option_int(
            options,
            CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
            DEFAULT_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
            MIN_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
            MAX_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        )
        start_threshold_w = _option_int(
            options,
            CONF_SURPLUS_START_THRESHOLD_W,
            DEFAULT_SURPLUS_START_THRESHOLD_W,
            MIN_SURPLUS_THRESHOLD_W,
            MAX_SURPLUS_THRESHOLD_W,
        )
        stop_threshold_w = _option_int(
            options,
            CONF_SURPLUS_STOP_THRESHOLD_W,
            DEFAULT_SURPLUS_STOP_THRESHOLD_W,
            MIN_SURPLUS_THRESHOLD_W,
            MAX_SURPLUS_THRESHOLD_W,
        )
        if stop_threshold_w > start_threshold_w:
            stop_threshold_w = start_threshold_w

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current_scan_interval): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SCAN_INTERVAL_SECONDS,
                            max=MAX_SCAN_INTERVAL_SECONDS,
                        ),
                    ),
                    vol.Required(
                        CONF_CHARGER_PROFILE,
                        default=_option_choice(
                            options,
                            CONF_CHARGER_PROFILE,
                            str(
                                self._config_entry.data.get(
                                    CONF_CHARGER_PROFILE,
                                    DEFAULT_CHARGER_PROFILE,
                                )
                            ),
                            CHARGER_PROFILES,
                        ),
                    ): vol.In(CHARGER_PROFILES),
                    vol.Optional(
                        CONF_CHARGER_PROFILE_JSON,
                        default=charger_profile_json,
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            multiline=True,
                        )
                    ),
                    vol.Required(
                        CONF_CONTINUOUS_CURRENT,
                        default=_option_bool(
                            options,
                            CONF_CONTINUOUS_CURRENT,
                            DEFAULT_CONTINUOUS_CURRENT,
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_VEHICLES,
                        default=_option_text(
                            options, CONF_VEHICLES, DEFAULT_VEHICLES
                        ),
                    ): str,
                    vol.Required(
                        CONF_SURPLUS_MODE_ENABLED,
                        default=_option_bool(
                            options,
                            CONF_SURPLUS_MODE_ENABLED,
                            DEFAULT_SURPLUS_MODE_ENABLED,
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_SURPLUS_SENSOR_ENTITY_ID,
                        default=_option_entity(
                            options,
                            CONF_SURPLUS_SENSOR_ENTITY_ID,
                            DEFAULT_SURPLUS_SENSOR_ENTITY_ID,
                        ),
                    ): _sensor_selector(),
                    vol.Required(
                        CONF_SURPLUS_SENSOR_INVERTED,
                        default=_option_bool(
                            options,
                            CONF_SURPLUS_SENSOR_INVERTED,
                            DEFAULT_SURPLUS_SENSOR_INVERTED,
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
                        default=_option_entity(
                            options,
                            CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
                            DEFAULT_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
                        ),
                    ): _sensor_selector(),
                    vol.Required(
                        CONF_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
                        default=_option_bool(
                            options,
                            CONF_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
                            DEFAULT_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
                        default=_option_entity(
                            options,
                            CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
                            DEFAULT_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
                        ),
                    ): _sensor_selector(),
                    vol.Required(
                        CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
                        default=high_threshold,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
                            max=MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
                        default=low_threshold,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
                            max=MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
                        ),
                    ),
                    vol.Optional(
                        CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_ENTITY_ID,
                        default=_option_entity(
                            options,
                            CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_ENTITY_ID,
                            DEFAULT_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_ENTITY_ID,
                        ),
                    ): _sensor_selector(),
                    vol.Required(
                        CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_INVERTED,
                        default=_option_bool(
                            options,
                            CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_INVERTED,
                            DEFAULT_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_INVERTED,
                        ),
                    ): bool,
                    vol.Required(
                        CONF_SURPLUS_ALLOW_BATTERY_DISCHARGE_FOR_EV,
                        default=_option_bool(
                            options,
                            CONF_SURPLUS_ALLOW_BATTERY_DISCHARGE_FOR_EV,
                            DEFAULT_SURPLUS_ALLOW_BATTERY_DISCHARGE_FOR_EV,
                        ),
                    ): bool,
                    vol.Required(
                        CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
                        default=max_battery_discharge,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
                            max=MAX_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_START_THRESHOLD_W,
                        default=start_threshold_w,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_THRESHOLD_W,
                            max=MAX_SURPLUS_THRESHOLD_W,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_STOP_THRESHOLD_W,
                        default=stop_threshold_w,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_THRESHOLD_W,
                            max=MAX_SURPLUS_THRESHOLD_W,
                        ),
                    ),
                    vol.Optional(
                        CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
                        default=_option_entity(
                            options,
                            CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
                            DEFAULT_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
                        ),
                    ): _sensor_selector(),
                }
            ),
        )


def _legacy_high_threshold_default(options: Mapping[str, Any]) -> int:
    return _option_int(
        options,
        CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        DEFAULT_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
        MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    )


def _option_int(
    options: Mapping[str, Any],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(options.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _option_bool(options: Mapping[str, Any], key: str, default: bool) -> bool:
    value = options.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "on", "yes"}:
            return True
        if lowered in {"0", "false", "off", "no"}:
            return False
    return bool(value)


def _option_choice(
    options: Mapping[str, Any],
    key: str,
    default: str,
    choices: tuple[str, ...],
) -> str:
    value = str(options.get(key, default)).strip().lower()
    if value in choices:
        return value
    return default


def _option_entity(
    options: Mapping[str, Any],
    key: str,
    default: str,
) -> str | None:
    value = options.get(key, default)
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "none":
        return None
    return text


def _option_text(options: Mapping[str, Any], key: str, default: str) -> str:
    value = options.get(key, default)
    if value is None:
        return default
    return str(value).strip()


def _sensor_selector() -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            domain=["sensor"],
            multiple=False,
        )
    )


def _normalize_optional_entity_value(data: dict[str, Any], key: str) -> None:
    value = data.get(key)
    if value is None:
        data[key] = ""
        return
    text = str(value).strip()
    if not text or text.lower() == "none":
        data[key] = ""
        return
    data[key] = text


def _normalize_text_value(data: dict[str, Any], key: str, default: str) -> None:
    value = data.get(key, default)
    if value is None:
        data[key] = default
        return
    text = str(value).strip()
    data[key] = text if text else default


def _normalize_surplus_options(data: dict[str, Any]) -> None:
    try:
        high = int(data.get(CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT, DEFAULT_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT))
    except (TypeError, ValueError):
        high = DEFAULT_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT
    try:
        low = int(data.get(CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT, DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT))
    except (TypeError, ValueError):
        low = DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT

    high = max(MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, min(MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, high))
    low = max(MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, min(MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, low))

    if high <= MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT:
        high = MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT + 1
    if low >= high:
        low = max(MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, high - 1)

    try:
        start_threshold_w = int(data.get(CONF_SURPLUS_START_THRESHOLD_W, DEFAULT_SURPLUS_START_THRESHOLD_W))
    except (TypeError, ValueError):
        start_threshold_w = DEFAULT_SURPLUS_START_THRESHOLD_W
    try:
        stop_threshold_w = int(data.get(CONF_SURPLUS_STOP_THRESHOLD_W, DEFAULT_SURPLUS_STOP_THRESHOLD_W))
    except (TypeError, ValueError):
        stop_threshold_w = DEFAULT_SURPLUS_STOP_THRESHOLD_W
    try:
        max_battery_discharge_w = int(
            data.get(
                CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
                DEFAULT_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
            )
        )
    except (TypeError, ValueError):
        max_battery_discharge_w = DEFAULT_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W

    start_threshold_w = max(MIN_SURPLUS_THRESHOLD_W, min(MAX_SURPLUS_THRESHOLD_W, start_threshold_w))
    stop_threshold_w = max(MIN_SURPLUS_THRESHOLD_W, min(MAX_SURPLUS_THRESHOLD_W, stop_threshold_w))
    if stop_threshold_w > start_threshold_w:
        stop_threshold_w = start_threshold_w
    max_battery_discharge_w = max(
        MIN_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        min(MAX_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W, max_battery_discharge_w),
    )

    data[CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT] = high
    data[CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT] = low
    data[CONF_SURPLUS_START_THRESHOLD_W] = start_threshold_w
    data[CONF_SURPLUS_STOP_THRESHOLD_W] = stop_threshold_w
    data[CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W] = max_battery_discharge_w
