from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_PROTOCOL_VERSION,
    CONF_SCAN_INTERVAL,
    CONF_SURPLUS_ADJUST_COOLDOWN_S,
    CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
    CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    CONF_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
    CONF_SURPLUS_LINE_VOLTAGE,
    CONF_SURPLUS_MODE,
    CONF_SURPLUS_MODE_ENABLED,
    CONF_SURPLUS_SENSOR_ENTITY_ID,
    CONF_SURPLUS_SENSOR_INVERTED,
    CONF_SURPLUS_START_DELAY_S,
    CONF_SURPLUS_START_THRESHOLD_W,
    CONF_SURPLUS_STOP_DELAY_S,
    CONF_SURPLUS_STOP_THRESHOLD_W,
    CONF_SURPLUS_TARGET_OFFSET_W,
    DEFAULT_SURPLUS_ADJUST_COOLDOWN_S,
    DEFAULT_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    DEFAULT_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
    DEFAULT_SURPLUS_LINE_VOLTAGE,
    DEFAULT_SURPLUS_MODE,
    DEFAULT_SURPLUS_MODE_ENABLED,
    DEFAULT_SURPLUS_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_SENSOR_INVERTED,
    DEFAULT_SURPLUS_START_DELAY_S,
    DEFAULT_SURPLUS_START_THRESHOLD_W,
    DEFAULT_SURPLUS_STOP_DELAY_S,
    DEFAULT_SURPLUS_STOP_THRESHOLD_W,
    DEFAULT_SURPLUS_TARGET_OFFSET_W,
    DEFAULT_NAME,
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    MAX_SURPLUS_DELAY_S,
    MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MAX_SURPLUS_LINE_VOLTAGE,
    MAX_SURPLUS_TARGET_OFFSET_W,
    MAX_SURPLUS_THRESHOLD_W,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SURPLUS_DELAY_S,
    MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MIN_SURPLUS_LINE_VOLTAGE,
    MIN_SURPLUS_TARGET_OFFSET_W,
    MIN_SURPLUS_THRESHOLD_W,
    MIN_SCAN_INTERVAL_SECONDS,
    SURPLUS_MODES,
    SUPPORTED_PROTOCOL_VERSIONS,
)
from .tuya_ev_charger import TuyaEVChargerClient

LOGGER = logging.getLogger(__name__)


class CannotConnectError(Exception):
    """Raised when the charger cannot be reached."""


def _build_user_schema(
    user_input: Mapping[str, Any] | None = None,
) -> vol.Schema:
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=user_input.get(CONF_HOST, "")): str,
            vol.Required(
                CONF_DEVICE_ID, default=user_input.get(CONF_DEVICE_ID, "")
            ): str,
            vol.Required(CONF_LOCAL_KEY, default=user_input.get(CONF_LOCAL_KEY, "")): str,
            vol.Required(
                CONF_PROTOCOL_VERSION,
                default=user_input.get(CONF_PROTOCOL_VERSION, DEFAULT_PROTOCOL_VERSION),
            ): vol.In(SUPPORTED_PROTOCOL_VERSIONS),
        }
    )


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
    )
    await client.async_connect()
    metrics = await client.async_get_metrics()
    if metrics is None:
        raise CannotConnectError
    return {"title": f"{DEFAULT_NAME} ({data[CONF_HOST]})"}


class TuyaEVChargerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

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
        errors: dict[str, str] = {}
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
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_build_user_schema(user_input),
            errors=errors,
        )


class TuyaEVChargerOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self._config_entry.options
        current_scan_interval = _option_int(
            options,
            CONF_SCAN_INTERVAL,
            DEFAULT_SCAN_INTERVAL_SECONDS,
            MIN_SCAN_INTERVAL_SECONDS,
            MAX_SCAN_INTERVAL_SECONDS,
        )
        surplus_line_voltage = _option_int(
            options,
            CONF_SURPLUS_LINE_VOLTAGE,
            DEFAULT_SURPLUS_LINE_VOLTAGE,
            MIN_SURPLUS_LINE_VOLTAGE,
            MAX_SURPLUS_LINE_VOLTAGE,
        )
        surplus_start_threshold = _option_int(
            options,
            CONF_SURPLUS_START_THRESHOLD_W,
            DEFAULT_SURPLUS_START_THRESHOLD_W,
            MIN_SURPLUS_THRESHOLD_W,
            MAX_SURPLUS_THRESHOLD_W,
        )
        surplus_stop_threshold = _option_int(
            options,
            CONF_SURPLUS_STOP_THRESHOLD_W,
            DEFAULT_SURPLUS_STOP_THRESHOLD_W,
            MIN_SURPLUS_THRESHOLD_W,
            MAX_SURPLUS_THRESHOLD_W,
        )
        if surplus_start_threshold <= surplus_stop_threshold:
            if surplus_stop_threshold >= MAX_SURPLUS_THRESHOLD_W:
                surplus_stop_threshold = MAX_SURPLUS_THRESHOLD_W - 1
            surplus_start_threshold = surplus_stop_threshold + 1
        surplus_target_offset = _option_int(
            options,
            CONF_SURPLUS_TARGET_OFFSET_W,
            DEFAULT_SURPLUS_TARGET_OFFSET_W,
            MIN_SURPLUS_TARGET_OFFSET_W,
            MAX_SURPLUS_TARGET_OFFSET_W,
        )
        surplus_start_delay = _option_int(
            options,
            CONF_SURPLUS_START_DELAY_S,
            DEFAULT_SURPLUS_START_DELAY_S,
            MIN_SURPLUS_DELAY_S,
            MAX_SURPLUS_DELAY_S,
        )
        surplus_stop_delay = _option_int(
            options,
            CONF_SURPLUS_STOP_DELAY_S,
            DEFAULT_SURPLUS_STOP_DELAY_S,
            MIN_SURPLUS_DELAY_S,
            MAX_SURPLUS_DELAY_S,
        )
        surplus_adjust_cooldown = _option_int(
            options,
            CONF_SURPLUS_ADJUST_COOLDOWN_S,
            DEFAULT_SURPLUS_ADJUST_COOLDOWN_S,
            MIN_SURPLUS_DELAY_S,
            MAX_SURPLUS_DELAY_S,
        )
        surplus_battery_soc_threshold = _option_int(
            options,
            CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
            DEFAULT_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
            MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
            MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        )
        surplus_mode = _option_choice(
            options,
            CONF_SURPLUS_MODE,
            DEFAULT_SURPLUS_MODE,
            SURPLUS_MODES,
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
                        CONF_SURPLUS_MODE_ENABLED,
                        default=_option_bool(
                            options,
                            CONF_SURPLUS_MODE_ENABLED,
                            DEFAULT_SURPLUS_MODE_ENABLED,
                        ),
                    ): bool,
                    vol.Required(
                        CONF_SURPLUS_MODE,
                        default=surplus_mode,
                    ): vol.In(SURPLUS_MODES),
                    vol.Optional(
                        CONF_SURPLUS_SENSOR_ENTITY_ID,
                        default=_option_entity(
                            options,
                            CONF_SURPLUS_SENSOR_ENTITY_ID,
                            DEFAULT_SURPLUS_SENSOR_ENTITY_ID,
                        ),
                    ): _sensor_selector(),
                    vol.Optional(
                        CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
                        default=_option_entity(
                            options,
                            CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
                            DEFAULT_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
                        ),
                    ): _sensor_selector(),
                    vol.Optional(
                        CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
                        default=_option_entity(
                            options,
                            CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
                            DEFAULT_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
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
                    vol.Required(
                        CONF_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
                        default=_option_bool(
                            options,
                            CONF_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
                            DEFAULT_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
                        ),
                    ): bool,
                    vol.Required(
                        CONF_SURPLUS_LINE_VOLTAGE,
                        default=surplus_line_voltage,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_LINE_VOLTAGE,
                            max=MAX_SURPLUS_LINE_VOLTAGE,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
                        default=surplus_battery_soc_threshold,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
                            max=MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_START_THRESHOLD_W,
                        default=surplus_start_threshold,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_THRESHOLD_W,
                            max=MAX_SURPLUS_THRESHOLD_W,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_STOP_THRESHOLD_W,
                        default=surplus_stop_threshold,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_THRESHOLD_W,
                            max=MAX_SURPLUS_THRESHOLD_W,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_TARGET_OFFSET_W,
                        default=surplus_target_offset,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_TARGET_OFFSET_W,
                            max=MAX_SURPLUS_TARGET_OFFSET_W,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_START_DELAY_S,
                        default=surplus_start_delay,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_DELAY_S,
                            max=MAX_SURPLUS_DELAY_S,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_STOP_DELAY_S,
                        default=surplus_stop_delay,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_DELAY_S,
                            max=MAX_SURPLUS_DELAY_S,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_ADJUST_COOLDOWN_S,
                        default=surplus_adjust_cooldown,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_DELAY_S,
                            max=MAX_SURPLUS_DELAY_S,
                        ),
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
    return text or None


def _sensor_selector() -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            domain=["sensor"],
            multiple=False,
        )
    )
