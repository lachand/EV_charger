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
    CONF_CHARGER_PROFILE,
    CONF_CHARGER_PROFILE_JSON,
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MAC,
    CONF_PROTOCOL_VERSION,
    CONF_SCAN_INTERVAL,
    DEFAULT_CHARGER_PROFILE,
    DEFAULT_CHARGER_PROFILE_JSON,
    DEFAULT_NAME,
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    SUPPORTED_PROTOCOL_VERSIONS,
)
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
            if user_input["mode"] == "scan":
                return await self.async_step_scan()
            return await self.async_step_credentials()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("mode", default="scan"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value="scan", label="Scan network"),
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
            _normalize_text_value(
                cleaned_input,
                CONF_CHARGER_PROFILE_JSON,
                DEFAULT_CHARGER_PROFILE_JSON,
            )
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
                }
            ),
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


def _option_text(options: Mapping[str, Any], key: str, default: str) -> str:
    value = options.get(key, default)
    if value is None:
        return default
    return str(value).strip()


def _normalize_text_value(data: dict[str, Any], key: str, default: str) -> None:
    value = data.get(key, default)
    if value is None:
        data[key] = default
        return
    text = str(value).strip()
    data[key] = text if text else default
