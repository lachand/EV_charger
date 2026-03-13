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
    CHARGER_PROFILES,
    CONF_CHARGER_PROFILE,
    CONF_CHARGER_PROFILE_JSON,
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_PROTOCOL_VERSION,
    CONF_SCAN_INTERVAL,
    CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
    CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
    CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
    CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    CONF_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
    CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
    CONF_SURPLUS_MODE_ENABLED,
    CONF_SURPLUS_SENSOR_ENTITY_ID,
    CONF_SURPLUS_SENSOR_INVERTED,
    DEFAULT_CHARGER_PROFILE,
    DEFAULT_CHARGER_PROFILE_JSON,
    DEFAULT_NAME,
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
    DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
    DEFAULT_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    DEFAULT_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
    DEFAULT_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_MODE_ENABLED,
    DEFAULT_SURPLUS_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_SENSOR_INVERTED,
    DOMAIN,
    MAX_SCAN_INTERVAL_SECONDS,
    MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MIN_SCAN_INTERVAL_SECONDS,
    MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
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
                CONF_DEVICE_ID,
                default=user_input.get(CONF_DEVICE_ID, ""),
            ): str,
            vol.Required(CONF_LOCAL_KEY, default=user_input.get(CONF_LOCAL_KEY, "")): str,
            vol.Required(
                CONF_PROTOCOL_VERSION,
                default=user_input.get(CONF_PROTOCOL_VERSION, DEFAULT_PROTOCOL_VERSION),
            ): vol.In(SUPPORTED_PROTOCOL_VERSIONS),
            vol.Required(
                CONF_CHARGER_PROFILE,
                default=user_input.get(CONF_CHARGER_PROFILE, DEFAULT_CHARGER_PROFILE),
            ): vol.In(CHARGER_PROFILES),
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
            cleaned_input = dict(user_input)
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
                CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
            )
            _normalize_text_value(
                cleaned_input,
                CONF_CHARGER_PROFILE_JSON,
                DEFAULT_CHARGER_PROFILE_JSON,
            )
            _normalize_soc_thresholds(cleaned_input)
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


def _normalize_soc_thresholds(data: dict[str, Any]) -> None:
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

    data[CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT] = high
    data[CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT] = low
