from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .cloud import TuyaCloudError, async_fetch_devices
from .const import (
    CHARGER_PROFILES,
    CLOUD_REGIONS,
    CONF_CHARGER_PROFILE,
    CONF_CHARGER_PROFILE_JSON,
    CONF_CLOUD_API_KEY,
    CONF_CLOUD_API_SECRET,
    CONF_CLOUD_REGION,
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MAC,
    CONF_PROTOCOL_VERSION,
    CONFIG_ENTRY_VERSION,
    DEFAULT_CHARGER_PROFILE,
    DEFAULT_CLOUD_REGION,
    DEFAULT_NAME,
    DEFAULT_PROTOCOL_VERSION,
    DOMAIN,
    SUPPORTED_PROTOCOL_VERSIONS,
    ConnectionFault,
)
from .discovery import async_scan_devices_by_id
from .options_flow import TuyaEVChargerOptionsFlow
from .tuya_ev_charger import (
    TuyaEVChargerClient,
)

LOGGER = logging.getLogger(__name__)


class CannotConnectError(Exception):
    """Raised when nothing answers at the charger's address."""


class ConnectionRefusedByChargerError(Exception):
    """Raised when the charger is present but rejects the control port.

    A Tuya charger accepts a single local connection, so this almost always
    means another client is holding it — not that the credentials are wrong.
    """


class InvalidCredentialsError(Exception):
    """Raised when the charger answers but its replies will not decrypt."""


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
        # "Cannot connect" blames the credentials, which is wrong for two of the
        # three failure modes, so say which one it actually is.
        fault = await client.async_classify_fault()
        if fault == ConnectionFault.REFUSED:
            raise ConnectionRefusedByChargerError
        if fault == ConnectionFault.UNDECRYPTABLE:
            raise InvalidCredentialsError
        raise CannotConnectError
    # No IP in the title: it is a DHCP address the integration relocates on its
    # own, so baking it in would make the title lie after the first move.
    return {"title": DEFAULT_NAME}


class TuyaEVChargerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = CONFIG_ENTRY_VERSION

    def __init__(self) -> None:
        self._discovered: dict[str, dict[str, Any]] = {}
        self._prefill: dict[str, Any] = {}
        self._device_meta: dict[str, Any] = {}
        self._cloud_devices: dict[str, dict[str, Any]] = {}
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
    ) -> config_entries.ConfigFlowResult:
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
    ) -> config_entries.ConfigFlowResult:
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
    ) -> config_entries.ConfigFlowResult:
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
                        default=(user_input or {}).get(CONF_CLOUD_REGION, DEFAULT_CLOUD_REGION),
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
    ) -> config_entries.ConfigFlowResult:
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
    ) -> config_entries.ConfigFlowResult:
        errors = errors or {}
        if user_input is not None:
            # The charger's identity is its Tuya device id (gwId), set at the factory
            # and stable across power cycles, re-pairing and a new IP. The MAC is
            # deliberately not used: it can change if the Wi-Fi module is replaced,
            # and changing an id orphans every entity's history. See entity.py.
            await self.async_set_unique_id(str(user_input[CONF_DEVICE_ID]))
            self._abort_if_unique_id_configured()
            try:
                info = await _async_validate_input(self.hass, user_input)
            except ConnectionRefusedByChargerError:
                errors["base"] = "connection_refused"
            except InvalidCredentialsError:
                errors["base"] = "invalid_credentials"
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

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> config_entries.ConfigFlowResult:
        """Entered when the charger stops accepting our credentials.

        Raised by the coordinator when the control port answers but nothing
        decrypts, which means the local_key was rotated by a re-pairing.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            candidate = {**entry.data, **user_input}
            try:
                await _async_validate_input(self.hass, candidate)
            except ConnectionRefusedByChargerError:
                errors["base"] = "connection_refused"
            except InvalidCredentialsError:
                errors["base"] = "invalid_credentials"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except Exception:
                LOGGER.exception("Unexpected error while validating charger config.")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(entry, data=candidate)

        # Only the key is asked for: the address and identity have not changed,
        # and re-typing them would be a chance to get them wrong.
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LOCAL_KEY,
                        default=(user_input or {}).get(CONF_LOCAL_KEY, ""),
                    ): str,
                }
            ),
            description_placeholders={"host": str(entry.data.get(CONF_HOST, ""))},
            errors=errors,
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Change the address or credentials of an existing charger.

        Without this, fixing a wrong device_id or a rotated local_key means
        deleting and re-adding the integration, which throws away the entity
        history and energy statistics.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await _async_validate_input(self.hass, user_input)
            except ConnectionRefusedByChargerError:
                errors["base"] = "connection_refused"
            except InvalidCredentialsError:
                errors["base"] = "invalid_credentials"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except Exception:
                LOGGER.exception("Unexpected error while validating charger config.")
                errors["base"] = "unknown"
            else:
                # Keep everything we are not asking about (cloud credentials,
                # learned MAC) and only overwrite the submitted fields.
                return self.async_update_reload_and_abort(entry, data={**entry.data, **user_input})

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_build_credentials_schema(user_input or entry.data),
            errors=errors,
        )

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> config_entries.ConfigFlowResult:
        """Auto-update a charger's IP when its DHCP lease changes.

        Triggered (via ``registered_devices`` in the manifest) when a device this
        integration registered gets a new lease. We match the announced MAC to the
        owning config entry and update its host in place.
        """
        mac = dr.format_mac(discovery_info.macaddress)
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(connections={(dr.CONNECTION_NETWORK_MAC, mac)})
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
            self._abort_if_unique_id_configured(updates={CONF_HOST: discovery_info.ip})

        return self.async_abort(reason="not_tuya_ev_charger")
