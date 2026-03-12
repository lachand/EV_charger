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
    CONF_SURPLUS_ADJUST_COOLDOWN_S,
    CONF_SURPLUS_ADJUST_DOWN_COOLDOWN_S,
    CONF_SURPLUS_ADJUST_UP_COOLDOWN_S,
    CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
    CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    CONF_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
    CONF_SURPLUS_DEPARTURE_MODE_ENABLED,
    CONF_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
    CONF_SURPLUS_DEPARTURE_TIME,
    CONF_SURPLUS_DRY_RUN_CONTINUOUS_ENABLED,
    CONF_SURPLUS_FORECAST_DROP_GUARD_W,
    CONF_SURPLUS_FORECAST_MODE_ENABLED,
    CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
    CONF_SURPLUS_FORECAST_SMOOTHING_S,
    CONF_SURPLUS_FORECAST_WEIGHT_PCT,
    CONF_SURPLUS_LINE_VOLTAGE,
    CONF_SURPLUS_MAX_SESSION_DURATION_MIN,
    CONF_SURPLUS_MAX_SESSION_END_TIME,
    CONF_SURPLUS_MAX_SESSION_ENERGY_KWH,
    CONF_SURPLUS_MIN_RUN_TIME_S,
    CONF_SURPLUS_MODE,
    CONF_SURPLUS_MODE_ENABLED,
    CONF_SURPLUS_SENSOR_ENTITY_ID,
    CONF_SURPLUS_SENSOR_INVERTED,
    CONF_SURPLUS_START_DELAY_S,
    CONF_SURPLUS_START_THRESHOLD_W,
    CONF_SURPLUS_STOP_DELAY_S,
    CONF_SURPLUS_STOP_THRESHOLD_W,
    CONF_SURPLUS_TARGET_OFFSET_W,
    CONF_SURPLUS_RAMP_STEP_A,
    CONF_TARIFF_ALLOWED_VALUES,
    CONF_TARIFF_MAX_PRICE_EUR_KWH,
    CONF_TARIFF_MODE,
    CONF_TARIFF_PRICE_SENSOR_ENTITY_ID,
    CONF_TARIFF_SENSOR_ENTITY_ID,
    DEFAULT_CHARGER_PROFILE,
    DEFAULT_CHARGER_PROFILE_JSON,
    DEFAULT_SURPLUS_ADJUST_COOLDOWN_S,
    DEFAULT_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    DEFAULT_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
    DEFAULT_SURPLUS_DEPARTURE_MODE_ENABLED,
    DEFAULT_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
    DEFAULT_SURPLUS_DEPARTURE_TIME,
    DEFAULT_SURPLUS_DRY_RUN_CONTINUOUS_ENABLED,
    DEFAULT_SURPLUS_FORECAST_DROP_GUARD_W,
    DEFAULT_SURPLUS_FORECAST_MODE_ENABLED,
    DEFAULT_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_FORECAST_SMOOTHING_S,
    DEFAULT_SURPLUS_FORECAST_WEIGHT_PCT,
    DEFAULT_SURPLUS_LINE_VOLTAGE,
    DEFAULT_SURPLUS_MAX_SESSION_DURATION_MIN,
    DEFAULT_SURPLUS_MAX_SESSION_END_TIME,
    DEFAULT_SURPLUS_MAX_SESSION_ENERGY_KWH,
    DEFAULT_SURPLUS_MIN_RUN_TIME_S,
    DEFAULT_SURPLUS_MODE,
    DEFAULT_SURPLUS_MODE_ENABLED,
    DEFAULT_SURPLUS_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_SENSOR_INVERTED,
    DEFAULT_SURPLUS_START_DELAY_S,
    DEFAULT_SURPLUS_START_THRESHOLD_W,
    DEFAULT_SURPLUS_STOP_DELAY_S,
    DEFAULT_SURPLUS_STOP_THRESHOLD_W,
    DEFAULT_SURPLUS_TARGET_OFFSET_W,
    DEFAULT_SURPLUS_RAMP_STEP_A,
    DEFAULT_TARIFF_ALLOWED_VALUES,
    DEFAULT_TARIFF_MAX_PRICE_EUR_KWH,
    DEFAULT_TARIFF_MODE,
    DEFAULT_TARIFF_PRICE_SENSOR_ENTITY_ID,
    DEFAULT_TARIFF_SENSOR_ENTITY_ID,
    DEFAULT_NAME,
    DEFAULT_PROTOCOL_VERSION,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    MAX_SURPLUS_DELAY_S,
    MAX_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
    MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MAX_SURPLUS_LINE_VOLTAGE,
    MAX_SURPLUS_FORECAST_DROP_GUARD_W,
    MAX_SURPLUS_FORECAST_WEIGHT_PCT,
    MAX_SURPLUS_RAMP_STEP_A,
    MAX_SURPLUS_SESSION_DURATION_MIN,
    MAX_SURPLUS_SESSION_ENERGY_KWH,
    MAX_SURPLUS_TARGET_OFFSET_W,
    MAX_SURPLUS_THRESHOLD_W,
    MAX_TARIFF_PRICE_EUR_KWH,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SURPLUS_DELAY_S,
    MIN_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
    MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MIN_SURPLUS_LINE_VOLTAGE,
    MIN_SURPLUS_FORECAST_DROP_GUARD_W,
    MIN_SURPLUS_FORECAST_WEIGHT_PCT,
    MIN_SURPLUS_RAMP_STEP_A,
    MIN_SURPLUS_SESSION_DURATION_MIN,
    MIN_SURPLUS_SESSION_ENERGY_KWH,
    MIN_SURPLUS_TARGET_OFFSET_W,
    MIN_SURPLUS_THRESHOLD_W,
    MIN_TARIFF_PRICE_EUR_KWH,
    MIN_SCAN_INTERVAL_SECONDS,
    SURPLUS_MODES,
    TARIFF_MODES,
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
            _normalize_optional_entity_value(
                cleaned_input,
                CONF_SURPLUS_SENSOR_ENTITY_ID,
            )
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
            _normalize_optional_entity_value(
                cleaned_input,
                CONF_TARIFF_SENSOR_ENTITY_ID,
            )
            _normalize_optional_entity_value(
                cleaned_input,
                CONF_TARIFF_PRICE_SENSOR_ENTITY_ID,
            )
            _normalize_text_value(
                cleaned_input,
                CONF_TARIFF_ALLOWED_VALUES,
                DEFAULT_TARIFF_ALLOWED_VALUES,
            )
            _normalize_text_value(
                cleaned_input,
                CONF_CHARGER_PROFILE_JSON,
                DEFAULT_CHARGER_PROFILE_JSON,
            )
            _normalize_end_time_value(cleaned_input, CONF_SURPLUS_MAX_SESSION_END_TIME)
            _normalize_end_time_value(cleaned_input, CONF_SURPLUS_DEPARTURE_TIME)
            return self.async_create_entry(data=cleaned_input)

        options = self._config_entry.options
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
        surplus_adjust_up_cooldown = _option_int(
            options,
            CONF_SURPLUS_ADJUST_UP_COOLDOWN_S,
            surplus_adjust_cooldown,
            MIN_SURPLUS_DELAY_S,
            MAX_SURPLUS_DELAY_S,
        )
        surplus_adjust_down_cooldown = _option_int(
            options,
            CONF_SURPLUS_ADJUST_DOWN_COOLDOWN_S,
            surplus_adjust_cooldown,
            MIN_SURPLUS_DELAY_S,
            MAX_SURPLUS_DELAY_S,
        )
        surplus_ramp_step_a = _option_int(
            options,
            CONF_SURPLUS_RAMP_STEP_A,
            DEFAULT_SURPLUS_RAMP_STEP_A,
            MIN_SURPLUS_RAMP_STEP_A,
            MAX_SURPLUS_RAMP_STEP_A,
        )
        surplus_forecast_weight_pct = _option_int(
            options,
            CONF_SURPLUS_FORECAST_WEIGHT_PCT,
            DEFAULT_SURPLUS_FORECAST_WEIGHT_PCT,
            MIN_SURPLUS_FORECAST_WEIGHT_PCT,
            MAX_SURPLUS_FORECAST_WEIGHT_PCT,
        )
        surplus_forecast_smoothing_s = _option_int(
            options,
            CONF_SURPLUS_FORECAST_SMOOTHING_S,
            DEFAULT_SURPLUS_FORECAST_SMOOTHING_S,
            MIN_SURPLUS_DELAY_S,
            MAX_SURPLUS_DELAY_S,
        )
        surplus_forecast_drop_guard_w = _option_int(
            options,
            CONF_SURPLUS_FORECAST_DROP_GUARD_W,
            DEFAULT_SURPLUS_FORECAST_DROP_GUARD_W,
            MIN_SURPLUS_FORECAST_DROP_GUARD_W,
            MAX_SURPLUS_FORECAST_DROP_GUARD_W,
        )
        surplus_battery_soc_threshold = _option_int(
            options,
            CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
            DEFAULT_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
            MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
            MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        )
        surplus_min_run_time = _option_int(
            options,
            CONF_SURPLUS_MIN_RUN_TIME_S,
            DEFAULT_SURPLUS_MIN_RUN_TIME_S,
            MIN_SURPLUS_DELAY_S,
            MAX_SURPLUS_DELAY_S,
        )
        surplus_max_session_duration_min = _option_int(
            options,
            CONF_SURPLUS_MAX_SESSION_DURATION_MIN,
            DEFAULT_SURPLUS_MAX_SESSION_DURATION_MIN,
            MIN_SURPLUS_SESSION_DURATION_MIN,
            MAX_SURPLUS_SESSION_DURATION_MIN,
        )
        surplus_max_session_energy_kwh = _option_float(
            options,
            CONF_SURPLUS_MAX_SESSION_ENERGY_KWH,
            DEFAULT_SURPLUS_MAX_SESSION_ENERGY_KWH,
            MIN_SURPLUS_SESSION_ENERGY_KWH,
            MAX_SURPLUS_SESSION_ENERGY_KWH,
        )
        surplus_max_session_end_time = _option_end_time(
            options,
            CONF_SURPLUS_MAX_SESSION_END_TIME,
            DEFAULT_SURPLUS_MAX_SESSION_END_TIME,
        )
        surplus_departure_time = _option_end_time(
            options,
            CONF_SURPLUS_DEPARTURE_TIME,
            DEFAULT_SURPLUS_DEPARTURE_TIME,
        )
        surplus_departure_target_energy_kwh = _option_float(
            options,
            CONF_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
            DEFAULT_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
            MIN_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
            MAX_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
        )
        surplus_mode = _option_choice(
            options,
            CONF_SURPLUS_MODE,
            DEFAULT_SURPLUS_MODE,
            SURPLUS_MODES,
        )
        tariff_mode = _option_choice(
            options,
            CONF_TARIFF_MODE,
            DEFAULT_TARIFF_MODE,
            TARIFF_MODES,
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
                    vol.Required(
                        CONF_TARIFF_MODE,
                        default=tariff_mode,
                    ): vol.In(TARIFF_MODES),
                    vol.Optional(
                        CONF_SURPLUS_SENSOR_ENTITY_ID,
                        default=_option_entity(
                            options,
                            CONF_SURPLUS_SENSOR_ENTITY_ID,
                            DEFAULT_SURPLUS_SENSOR_ENTITY_ID,
                        ),
                    ): _optional_sensor_selector(),
                    vol.Optional(
                        CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
                        default=_option_entity(
                            options,
                            CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
                            DEFAULT_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
                        ),
                    ): _optional_sensor_selector(),
                    vol.Optional(
                        CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
                        default=_option_entity(
                            options,
                            CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
                            DEFAULT_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
                        ),
                    ): _optional_sensor_selector(),
                    vol.Required(
                        CONF_SURPLUS_FORECAST_MODE_ENABLED,
                        default=_option_bool(
                            options,
                            CONF_SURPLUS_FORECAST_MODE_ENABLED,
                            DEFAULT_SURPLUS_FORECAST_MODE_ENABLED,
                        ),
                    ): bool,
                    vol.Required(
                        CONF_SURPLUS_DRY_RUN_CONTINUOUS_ENABLED,
                        default=_option_bool(
                            options,
                            CONF_SURPLUS_DRY_RUN_CONTINUOUS_ENABLED,
                            DEFAULT_SURPLUS_DRY_RUN_CONTINUOUS_ENABLED,
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
                        default=_option_entity(
                            options,
                            CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
                            DEFAULT_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
                        ),
                    ): _optional_sensor_selector(),
                    vol.Optional(
                        CONF_TARIFF_SENSOR_ENTITY_ID,
                        default=_option_entity(
                            options,
                            CONF_TARIFF_SENSOR_ENTITY_ID,
                            DEFAULT_TARIFF_SENSOR_ENTITY_ID,
                        ),
                    ): _optional_sensor_selector(),
                    vol.Optional(
                        CONF_TARIFF_PRICE_SENSOR_ENTITY_ID,
                        default=_option_entity(
                            options,
                            CONF_TARIFF_PRICE_SENSOR_ENTITY_ID,
                            DEFAULT_TARIFF_PRICE_SENSOR_ENTITY_ID,
                        ),
                    ): _optional_sensor_selector(),
                    vol.Optional(
                        CONF_TARIFF_ALLOWED_VALUES,
                        default=_option_text(
                            options,
                            CONF_TARIFF_ALLOWED_VALUES,
                            DEFAULT_TARIFF_ALLOWED_VALUES,
                        ),
                    ): str,
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
                        CONF_SURPLUS_ADJUST_UP_COOLDOWN_S,
                        default=surplus_adjust_up_cooldown,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_DELAY_S,
                            max=MAX_SURPLUS_DELAY_S,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_ADJUST_DOWN_COOLDOWN_S,
                        default=surplus_adjust_down_cooldown,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_DELAY_S,
                            max=MAX_SURPLUS_DELAY_S,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_RAMP_STEP_A,
                        default=surplus_ramp_step_a,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_RAMP_STEP_A,
                            max=MAX_SURPLUS_RAMP_STEP_A,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_FORECAST_WEIGHT_PCT,
                        default=surplus_forecast_weight_pct,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_FORECAST_WEIGHT_PCT,
                            max=MAX_SURPLUS_FORECAST_WEIGHT_PCT,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_FORECAST_SMOOTHING_S,
                        default=surplus_forecast_smoothing_s,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_DELAY_S,
                            max=MAX_SURPLUS_DELAY_S,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_FORECAST_DROP_GUARD_W,
                        default=surplus_forecast_drop_guard_w,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_FORECAST_DROP_GUARD_W,
                            max=MAX_SURPLUS_FORECAST_DROP_GUARD_W,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_MIN_RUN_TIME_S,
                        default=surplus_min_run_time,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_DELAY_S,
                            max=MAX_SURPLUS_DELAY_S,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_MAX_SESSION_DURATION_MIN,
                        default=surplus_max_session_duration_min,
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_SURPLUS_SESSION_DURATION_MIN,
                            max=MAX_SURPLUS_SESSION_DURATION_MIN,
                        ),
                    ),
                    vol.Required(
                        CONF_SURPLUS_MAX_SESSION_ENERGY_KWH,
                        default=surplus_max_session_energy_kwh,
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(
                            min=MIN_SURPLUS_SESSION_ENERGY_KWH,
                            max=MAX_SURPLUS_SESSION_ENERGY_KWH,
                        ),
                    ),
                    vol.Optional(
                        CONF_SURPLUS_MAX_SESSION_END_TIME,
                        default=surplus_max_session_end_time,
                    ): str,
                    vol.Required(
                        CONF_SURPLUS_DEPARTURE_MODE_ENABLED,
                        default=_option_bool(
                            options,
                            CONF_SURPLUS_DEPARTURE_MODE_ENABLED,
                            DEFAULT_SURPLUS_DEPARTURE_MODE_ENABLED,
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_SURPLUS_DEPARTURE_TIME,
                        default=surplus_departure_time,
                    ): str,
                    vol.Required(
                        CONF_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
                        default=surplus_departure_target_energy_kwh,
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(
                            min=MIN_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
                            max=MAX_SURPLUS_DEPARTURE_TARGET_ENERGY_KWH,
                        ),
                    ),
                    vol.Required(
                        CONF_TARIFF_MAX_PRICE_EUR_KWH,
                        default=_option_float(
                            options,
                            CONF_TARIFF_MAX_PRICE_EUR_KWH,
                            DEFAULT_TARIFF_MAX_PRICE_EUR_KWH,
                            MIN_TARIFF_PRICE_EUR_KWH,
                            MAX_TARIFF_PRICE_EUR_KWH,
                        ),
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(
                            min=MIN_TARIFF_PRICE_EUR_KWH,
                            max=MAX_TARIFF_PRICE_EUR_KWH,
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


def _option_float(
    options: Mapping[str, Any],
    key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(options.get(key, default))
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


def _option_end_time(options: Mapping[str, Any], key: str, default: str) -> str:
    text = _option_text(options, key, default)
    if not text:
        return ""
    try:
        return str(vol.Match(r"^([01]?\d|2[0-3]):[0-5]\d$")(text))
    except vol.Invalid:
        return ""


def _sensor_selector() -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            domain=["sensor"],
            multiple=False,
        )
    )


def _optional_sensor_selector() -> vol.Any:
    return vol.Any(None, "", _sensor_selector())


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


def _normalize_end_time_value(data: dict[str, Any], key: str) -> None:
    value = data.get(key, "")
    text = str(value).strip() if value is not None else ""
    if not text:
        data[key] = ""
        return
    try:
        data[key] = str(vol.Match(r"^([01]?\d|2[0-3]):[0-5]\d$")(text))
    except vol.Invalid:
        data[key] = ""
