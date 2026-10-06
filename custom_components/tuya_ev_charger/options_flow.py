"""The options form: its description, and the flow that edits and stores it.

Split out of ``config_flow.py`` so the setup steps and the 30-odd options each
fit on a screen. Behaviour is unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    CHARGER_PROFILE_CUSTOM_JSON,
    CHARGER_PROFILES,
    CONF_CAP_ONLY_REGULATION,
    CONF_CHARGER_PROFILE,
    CONF_CHARGER_PROFILE_JSON,
    CONF_CONTINUOUS_CURRENT,
    CONF_CRITICAL_PEAK_SENSOR_ENTITY_ID,
    CONF_CRITICAL_PEAK_SENSOR_INVERTED,
    CONF_DEPARTURE_ENERGY_KWH,
    CONF_DEPARTURE_TIME,
    CONF_EXTERNAL_CHARGE_ALLOWED_SENSOR_ENTITY_ID,
    CONF_EXTERNAL_CHARGE_ALLOWED_SENSOR_INVERTED,
    CONF_INSTALLATION_PHASES,
    CONF_MAX_CHARGE_CURRENT_A,
    CONF_MAX_HOUSE_POWER_W,
    CONF_MAX_INVERTER_POWER_W,
    CONF_MIN_CHARGE_CURRENT_A,
    CONF_OFF_PEAK_PRICE,
    CONF_OFF_PEAK_SENSOR_ENTITY_ID,
    CONF_OFF_PEAK_SENSOR_INVERTED,
    CONF_OFF_PEAK_WINDOWS,
    CONF_PEAK_PRICE,
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
    CONF_TOTAL_LOAD_SENSOR_ENTITY_ID,
    CONF_VEHICLES,
    DEFAULT_CAP_ONLY_REGULATION,
    DEFAULT_CHARGER_PROFILE,
    DEFAULT_CHARGER_PROFILE_JSON,
    DEFAULT_CONTINUOUS_CURRENT,
    DEFAULT_CRITICAL_PEAK_SENSOR_ENTITY_ID,
    DEFAULT_CRITICAL_PEAK_SENSOR_INVERTED,
    DEFAULT_DEPARTURE_ENERGY_KWH,
    DEFAULT_DEPARTURE_TIME,
    DEFAULT_EXTERNAL_CHARGE_ALLOWED_SENSOR_ENTITY_ID,
    DEFAULT_EXTERNAL_CHARGE_ALLOWED_SENSOR_INVERTED,
    DEFAULT_INSTALLATION_PHASES,
    DEFAULT_MAX_CHARGE_CURRENT_A,
    DEFAULT_MAX_HOUSE_POWER_W,
    DEFAULT_MAX_INVERTER_POWER_W,
    DEFAULT_MIN_CHARGE_CURRENT_A,
    DEFAULT_OFF_PEAK_PRICE,
    DEFAULT_OFF_PEAK_SENSOR_ENTITY_ID,
    DEFAULT_OFF_PEAK_SENSOR_INVERTED,
    DEFAULT_OFF_PEAK_WINDOWS,
    DEFAULT_PEAK_PRICE,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_SURPLUS_ALLOW_BATTERY_DISCHARGE_FOR_EV,
    DEFAULT_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_INVERTED,
    DEFAULT_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
    DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
    DEFAULT_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
    DEFAULT_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    DEFAULT_SURPLUS_MODE_ENABLED,
    DEFAULT_SURPLUS_SENSOR_ENTITY_ID,
    DEFAULT_SURPLUS_SENSOR_INVERTED,
    DEFAULT_SURPLUS_START_THRESHOLD_W,
    DEFAULT_SURPLUS_STOP_THRESHOLD_W,
    DEFAULT_TOTAL_LOAD_SENSOR_ENTITY_ID,
    DEFAULT_VEHICLES,
    INSTALLATION_PHASE_CHOICES,
    MAX_CHARGE_CURRENT_LIMIT_A,
    MAX_DEPARTURE_ENERGY_KWH,
    MAX_MAX_HOUSE_POWER_W,
    MAX_SCAN_INTERVAL_SECONDS,
    MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MAX_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    MAX_SURPLUS_THRESHOLD_W,
    MIN_CHARGE_CURRENT_LIMIT_A,
    MIN_DEPARTURE_ENERGY_KWH,
    MIN_MAX_HOUSE_POWER_W,
    MIN_SCAN_INTERVAL_SECONDS,
    MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    MIN_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
    MIN_SURPLUS_THRESHOLD_W,
)
from .dp_profile import known_dp_profile_fields, validate_custom_dp_profile
from .option_values import (
    clean_optional_text,
    option_bool,
    option_float,
    option_int,
    option_text,
)

LOGGER = logging.getLogger(__name__)


# Optional entity pickers in the options form. They must not carry a voluptuous
# default: an inserted `None` fails EntitySelector validation, and wrapping the
# selector to tolerate it breaks the schema serialisation the frontend needs.
OPTIONAL_ENTITY_OPTIONS: tuple[str, ...] = (
    CONF_SURPLUS_SENSOR_ENTITY_ID,
    CONF_TOTAL_LOAD_SENSOR_ENTITY_ID,
    CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
    CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
    CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_ENTITY_ID,
    CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
    CONF_EXTERNAL_CHARGE_ALLOWED_SENSOR_ENTITY_ID,
    CONF_OFF_PEAK_SENSOR_ENTITY_ID,
)

# Optional free-text fields (kind "text"/"multiline"). Same omission behaviour
# as the entity pickers above, and the same #30 bug when it isn't handled: the
# frontend drops a blanked field's key from its section's payload, so an
# absent key means "cleared" and must not fall back to the stored value.
OPTIONAL_TEXT_OPTIONS: tuple[str, ...] = (
    CONF_OFF_PEAK_WINDOWS,
    CONF_DEPARTURE_TIME,
    CONF_VEHICLES,
    CONF_CHARGER_PROFILE_JSON,
)


# The options form is 32 fields; ungrouped, it is a wall. Sections are cosmetic
# only -- values are flattened back before storage, so nothing downstream changes
# and no migration is needed.
SECTION_DEVICE = "device"
SECTION_CURRENT = "current"
SECTION_VEHICLES = "vehicles"
SECTION_PROTECTION = "protection"
SECTION_TARIFF = "tariff"
SECTION_SURPLUS = "surplus"
SECTION_BATTERY = "battery"

# Collapsed by default are the ones most installs never touch. The protections
# stay open: a limit nobody notices is a limit nobody sets.
_COLLAPSED_SECTIONS = frozenset({SECTION_TARIFF, SECTION_SURPLUS, SECTION_BATTERY})

_SECTION_ORDER: tuple[str, ...] = (
    SECTION_DEVICE,
    SECTION_CURRENT,
    SECTION_PROTECTION,
    SECTION_VEHICLES,
    SECTION_TARIFF,
    SECTION_SURPLUS,
    SECTION_BATTERY,
)


@dataclass(frozen=True, slots=True)
class _Opt:
    """One row of the options form.

    The surplus form is twenty fields following three repeating shapes, so it is
    described rather than written out: the order here is the order on screen.
    """

    key: str
    kind: str  # bool | entity | int | price | text | choice | multiline
    default: Any = None
    minimum: int = 0
    maximum: int = 0
    choices: tuple[str, ...] = ()
    # Which collapsible group the field belongs to on screen. Purely
    # presentational: the stored options stay flat, see `_flatten_sections`.
    section: str = SECTION_DEVICE


_OPTIONS_FORM: tuple[_Opt, ...] = (
    _Opt(
        CONF_SCAN_INTERVAL,
        "int",
        DEFAULT_SCAN_INTERVAL_SECONDS,
        MIN_SCAN_INTERVAL_SECONDS,
        MAX_SCAN_INTERVAL_SECONDS,
    ),
    _Opt(CONF_CHARGER_PROFILE, "choice", DEFAULT_CHARGER_PROFILE, choices=CHARGER_PROFILES),
    _Opt(CONF_CHARGER_PROFILE_JSON, "multiline", DEFAULT_CHARGER_PROFILE_JSON),
    _Opt(CONF_CONTINUOUS_CURRENT, "bool", DEFAULT_CONTINUOUS_CURRENT, section=SECTION_CURRENT),
    _Opt(
        CONF_MAX_CHARGE_CURRENT_A,
        "int",
        DEFAULT_MAX_CHARGE_CURRENT_A,
        MIN_CHARGE_CURRENT_LIMIT_A,
        MAX_CHARGE_CURRENT_LIMIT_A,
        section=SECTION_CURRENT,
    ),
    _Opt(
        CONF_MIN_CHARGE_CURRENT_A,
        "int",
        DEFAULT_MIN_CHARGE_CURRENT_A,
        MIN_CHARGE_CURRENT_LIMIT_A,
        MAX_CHARGE_CURRENT_LIMIT_A,
        section=SECTION_CURRENT,
    ),
    _Opt(
        CONF_INSTALLATION_PHASES,
        "choice",
        DEFAULT_INSTALLATION_PHASES,
        choices=INSTALLATION_PHASE_CHOICES,
        section=SECTION_CURRENT,
    ),
    _Opt(CONF_VEHICLES, "text", DEFAULT_VEHICLES, section=SECTION_VEHICLES),
    _Opt(
        CONF_MAX_HOUSE_POWER_W,
        "int",
        DEFAULT_MAX_HOUSE_POWER_W,
        MIN_MAX_HOUSE_POWER_W,
        MAX_MAX_HOUSE_POWER_W,
        section=SECTION_PROTECTION,
    ),
    _Opt(
        CONF_MAX_INVERTER_POWER_W,
        "int",
        DEFAULT_MAX_INVERTER_POWER_W,
        MIN_MAX_HOUSE_POWER_W,
        MAX_MAX_HOUSE_POWER_W,
        section=SECTION_PROTECTION,
    ),
    _Opt(
        CONF_CAP_ONLY_REGULATION,
        "bool",
        DEFAULT_CAP_ONLY_REGULATION,
        section=SECTION_PROTECTION,
    ),
    _Opt(
        CONF_TOTAL_LOAD_SENSOR_ENTITY_ID,
        "entity",
        DEFAULT_TOTAL_LOAD_SENSOR_ENTITY_ID,
        section=SECTION_PROTECTION,
    ),
    _Opt(
        CONF_EXTERNAL_CHARGE_ALLOWED_SENSOR_ENTITY_ID,
        "boolean_entity",
        DEFAULT_EXTERNAL_CHARGE_ALLOWED_SENSOR_ENTITY_ID,
        section=SECTION_PROTECTION,
    ),
    _Opt(
        CONF_EXTERNAL_CHARGE_ALLOWED_SENSOR_INVERTED,
        "bool",
        DEFAULT_EXTERNAL_CHARGE_ALLOWED_SENSOR_INVERTED,
        section=SECTION_PROTECTION,
    ),
    _Opt(CONF_OFF_PEAK_WINDOWS, "text", DEFAULT_OFF_PEAK_WINDOWS, section=SECTION_TARIFF),
    _Opt(
        CONF_OFF_PEAK_SENSOR_ENTITY_ID,
        "boolean_entity",
        DEFAULT_OFF_PEAK_SENSOR_ENTITY_ID,
        section=SECTION_TARIFF,
    ),
    _Opt(
        CONF_OFF_PEAK_SENSOR_INVERTED,
        "bool",
        DEFAULT_OFF_PEAK_SENSOR_INVERTED,
        section=SECTION_TARIFF,
    ),
    _Opt(
        CONF_CRITICAL_PEAK_SENSOR_ENTITY_ID,
        "boolean_entity",
        DEFAULT_CRITICAL_PEAK_SENSOR_ENTITY_ID,
        section=SECTION_TARIFF,
    ),
    _Opt(
        CONF_CRITICAL_PEAK_SENSOR_INVERTED,
        "bool",
        DEFAULT_CRITICAL_PEAK_SENSOR_INVERTED,
        section=SECTION_TARIFF,
    ),
    _Opt(CONF_DEPARTURE_TIME, "text", DEFAULT_DEPARTURE_TIME, section=SECTION_TARIFF),
    _Opt(
        CONF_DEPARTURE_ENERGY_KWH,
        "int",
        DEFAULT_DEPARTURE_ENERGY_KWH,
        MIN_DEPARTURE_ENERGY_KWH,
        MAX_DEPARTURE_ENERGY_KWH,
        section=SECTION_TARIFF,
    ),
    _Opt(CONF_OFF_PEAK_PRICE, "price", DEFAULT_OFF_PEAK_PRICE, section=SECTION_TARIFF),
    _Opt(CONF_PEAK_PRICE, "price", DEFAULT_PEAK_PRICE, section=SECTION_TARIFF),
    _Opt(CONF_SURPLUS_MODE_ENABLED, "bool", DEFAULT_SURPLUS_MODE_ENABLED, section=SECTION_SURPLUS),
    _Opt(
        CONF_SURPLUS_SENSOR_ENTITY_ID,
        "entity",
        DEFAULT_SURPLUS_SENSOR_ENTITY_ID,
        section=SECTION_SURPLUS,
    ),
    _Opt(
        CONF_SURPLUS_SENSOR_INVERTED,
        "bool",
        DEFAULT_SURPLUS_SENSOR_INVERTED,
        section=SECTION_SURPLUS,
    ),
    _Opt(
        CONF_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
        "entity",
        DEFAULT_SURPLUS_CURTAILMENT_SENSOR_ENTITY_ID,
        section=SECTION_SURPLUS,
    ),
    _Opt(
        CONF_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
        "bool",
        DEFAULT_SURPLUS_CURTAILMENT_SENSOR_INVERTED,
        section=SECTION_SURPLUS,
    ),
    _Opt(
        CONF_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
        "entity",
        DEFAULT_SURPLUS_BATTERY_SOC_SENSOR_ENTITY_ID,
        section=SECTION_BATTERY,
    ),
    _Opt(
        CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
        "int",
        DEFAULT_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
        MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        section=SECTION_BATTERY,
    ),
    _Opt(
        CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
        "int",
        DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
        MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        section=SECTION_BATTERY,
    ),
    _Opt(
        CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_ENTITY_ID,
        "entity",
        DEFAULT_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_ENTITY_ID,
        section=SECTION_BATTERY,
    ),
    _Opt(
        CONF_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_INVERTED,
        "bool",
        DEFAULT_SURPLUS_BATTERY_NET_DISCHARGE_SENSOR_INVERTED,
        section=SECTION_BATTERY,
    ),
    _Opt(
        CONF_SURPLUS_ALLOW_BATTERY_DISCHARGE_FOR_EV,
        "bool",
        DEFAULT_SURPLUS_ALLOW_BATTERY_DISCHARGE_FOR_EV,
        section=SECTION_BATTERY,
    ),
    _Opt(
        CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        "int",
        DEFAULT_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        MIN_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        MAX_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        section=SECTION_BATTERY,
    ),
    _Opt(
        CONF_SURPLUS_START_THRESHOLD_W,
        "int",
        DEFAULT_SURPLUS_START_THRESHOLD_W,
        MIN_SURPLUS_THRESHOLD_W,
        MAX_SURPLUS_THRESHOLD_W,
        section=SECTION_SURPLUS,
    ),
    _Opt(
        CONF_SURPLUS_STOP_THRESHOLD_W,
        "int",
        DEFAULT_SURPLUS_STOP_THRESHOLD_W,
        MIN_SURPLUS_THRESHOLD_W,
        MAX_SURPLUS_THRESHOLD_W,
        section=SECTION_SURPLUS,
    ),
    _Opt(
        CONF_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
        "entity",
        DEFAULT_SURPLUS_FORECAST_SENSOR_ENTITY_ID,
        section=SECTION_SURPLUS,
    ),
)


class TuyaEVChargerOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        # Why the custom DP mapping was rejected, shown next to the field.
        self._profile_json_problem: str | None = None

    def _build_options_schema(
        self,
        options: Mapping[str, Any],
        computed: Mapping[str, Any],
    ) -> vol.Schema:
        """Turn the options table into a schema of collapsible sections.

        Grouping is presentation only: `section` nests the submitted values, and
        `_flatten_sections` undoes that before anything is stored, so the options
        keep the flat shape every reader expects and no migration is needed.
        """
        schema: dict[Any, Any] = {}
        for name in _SECTION_ORDER:
            rows = tuple(opt for opt in _OPTIONS_FORM if opt.section == name)
            if not rows:
                continue
            schema[vol.Required(name)] = section(
                self._build_section_schema(rows, options, computed),
                {"collapsed": name in _COLLAPSED_SECTIONS},
            )
        return vol.Schema(schema)

    def _build_section_schema(
        self,
        options_form: tuple[_Opt, ...],
        options: Mapping[str, Any],
        computed: Mapping[str, Any],
    ) -> vol.Schema:
        """One section's fields.

        ``computed`` carries the values that cannot come straight from storage,
        such as thresholds clamped against each other.
        """
        fields: dict[Any, Any] = {}
        for opt in options_form:
            if opt.kind in ("entity", "boolean_entity"):
                # No voluptuous default here: an inserted None fails the entity
                # selector, and wrapping the selector to tolerate it breaks the
                # schema serialisation the frontend needs.
                current = _option_entity(options, opt.key, opt.default)
                picker = (
                    _boolean_sensor_selector()
                    if opt.kind == "boolean_entity"
                    else _sensor_selector()
                )
                fields[vol.Optional(opt.key, description={"suggested_value": current})] = picker
                continue

            default = computed.get(opt.key)
            if opt.kind == "bool":
                if default is None:
                    default = option_bool(options, opt.key, opt.default)
                fields[vol.Required(opt.key, default=default)] = bool
            elif opt.kind == "int":
                if default is None:
                    default = option_int(options, opt.key, opt.default, opt.minimum, opt.maximum)
                fields[vol.Required(opt.key, default=default)] = vol.All(
                    vol.Coerce(int), vol.Range(min=opt.minimum, max=opt.maximum)
                )
            elif opt.kind == "choice":
                if default is None:
                    default = _option_choice(
                        options,
                        opt.key,
                        str(self._config_entry.data.get(opt.key, opt.default)),
                        opt.choices,
                    )
                fields[vol.Required(opt.key, default=default)] = vol.In(opt.choices)
            elif opt.kind == "price":
                # Prices are small floats, so the int path would round a 0.16
                # tariff to 0 and silently report every session as free. The range
                # is symmetric: a negative price (paid to consume) is a real tariff.
                if default is None:
                    default = option_float(options, opt.key, opt.default)
                fields[vol.Required(opt.key, default=default)] = vol.All(
                    vol.Coerce(float), vol.Range(min=-100, max=100)
                )
            elif opt.kind == "multiline":
                # No voluptuous default here either -- same reasoning as the
                # entity branch above, and the same #30 bug otherwise: a
                # default would let HA-core's own schema validation silently
                # refill a field the frontend omitted because it was cleared.
                if default is None:
                    default = option_text(options, opt.key, opt.default)
                fields[vol.Optional(opt.key, description={"suggested_value": default})] = (
                    selector.TextSelector(selector.TextSelectorConfig(multiline=True))
                )
            else:  # text
                if default is None:
                    default = option_text(options, opt.key, opt.default)
                fields[vol.Optional(opt.key, description={"suggested_value": default})] = str
        return vol.Schema(fields)

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            flat_input = _flatten_sections(user_input)
            cleaned_input = dict(self._config_entry.options)
            cleaned_input.update(flat_input)
            # Optional entity pickers are omitted from their section's dict when
            # left empty, so an absent key means "cleared" and must not fall back
            # to the previously stored value. Must check the flattened dict: the
            # raw user_input's top-level keys are section names, never field
            # names, so checking it here always looked "absent" and wiped every
            # entity-selector pick on every save.
            for key in OPTIONAL_ENTITY_OPTIONS:
                if key not in flat_input:
                    cleaned_input[key] = ""
            # Same omission, same fix, for the free-text fields (#30): without
            # this, the schema change above only stops HA-core from refilling
            # the stale default -- `cleaned_input` still starts from
            # `self._config_entry.options`, so an absent key must still be
            # cleared explicitly here.
            for key in OPTIONAL_TEXT_OPTIONS:
                if key not in flat_input:
                    cleaned_input[key] = ""
            _normalize_optional_entity_value(cleaned_input, CONF_SURPLUS_SENSOR_ENTITY_ID)
            _normalize_optional_entity_value(cleaned_input, CONF_TOTAL_LOAD_SENSOR_ENTITY_ID)
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
            _normalize_optional_entity_value(
                cleaned_input,
                CONF_EXTERNAL_CHARGE_ALLOWED_SENSOR_ENTITY_ID,
            )
            _normalize_optional_entity_value(
                cleaned_input,
                CONF_OFF_PEAK_SENSOR_ENTITY_ID,
            )
            _normalize_optional_entity_value(
                cleaned_input,
                CONF_CRITICAL_PEAK_SENSOR_ENTITY_ID,
            )
            _normalize_text_value(
                cleaned_input,
                CONF_CHARGER_PROFILE_JSON,
                DEFAULT_CHARGER_PROFILE_JSON,
            )
            _normalize_text_value(cleaned_input, CONF_VEHICLES, DEFAULT_VEHICLES)
            _normalize_text_value(cleaned_input, CONF_OFF_PEAK_WINDOWS, DEFAULT_OFF_PEAK_WINDOWS)
            _normalize_text_value(cleaned_input, CONF_DEPARTURE_TIME, DEFAULT_DEPARTURE_TIME)
            _normalize_surplus_options(cleaned_input)

            # A bad custom mapping is otherwise accepted, logged, and silently
            # replaced by the default profile: the form closes, the charger
            # reports nothing, and the mapping looks applied.
            if cleaned_input.get(CONF_CHARGER_PROFILE) == CHARGER_PROFILE_CUSTOM_JSON:
                problem = validate_custom_dp_profile(
                    cleaned_input.get(CONF_CHARGER_PROFILE_JSON, "")
                )
                if problem is not None:
                    self._profile_json_problem = problem
                    return await self._async_show_options_form(
                        cleaned_input, errors={CONF_CHARGER_PROFILE_JSON: "invalid_dp_profile"}
                    )

            self._profile_json_problem = None
            return self.async_create_entry(data=cleaned_input)

        return await self._async_show_options_form(self._config_entry.options)

    async def _async_show_options_form(
        self,
        options: Mapping[str, Any],
        errors: dict[str, str] | None = None,
    ) -> config_entries.ConfigFlowResult:

        current_scan_interval = option_int(
            options,
            CONF_SCAN_INTERVAL,
            DEFAULT_SCAN_INTERVAL_SECONDS,
            MIN_SCAN_INTERVAL_SECONDS,
            MAX_SCAN_INTERVAL_SECONDS,
        )
        charger_profile_json = option_text(
            options,
            CONF_CHARGER_PROFILE_JSON,
            str(
                self._config_entry.data.get(
                    CONF_CHARGER_PROFILE_JSON,
                    DEFAULT_CHARGER_PROFILE_JSON,
                )
            ),
        )

        high_threshold = option_int(
            options,
            CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
            _legacy_high_threshold_default(options),
            MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
            MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        )
        low_threshold = option_int(
            options,
            CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
            min(DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT, high_threshold),
            MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
            MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        )
        if low_threshold >= high_threshold:
            low_threshold = max(MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, high_threshold - 1)
        max_battery_discharge = option_int(
            options,
            CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
            DEFAULT_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
            MIN_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
            MAX_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W,
        )
        start_threshold_w = option_int(
            options,
            CONF_SURPLUS_START_THRESHOLD_W,
            DEFAULT_SURPLUS_START_THRESHOLD_W,
            MIN_SURPLUS_THRESHOLD_W,
            MAX_SURPLUS_THRESHOLD_W,
        )
        stop_threshold_w = option_int(
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
            data_schema=self._build_options_schema(
                options,
                computed={
                    CONF_SCAN_INTERVAL: current_scan_interval,
                    CONF_CHARGER_PROFILE_JSON: charger_profile_json,
                    CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT: high_threshold,
                    CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT: low_threshold,
                    CONF_SURPLUS_MAX_BATTERY_DISCHARGE_FOR_EV_W: max_battery_discharge,
                    CONF_SURPLUS_START_THRESHOLD_W: start_threshold_w,
                    CONF_SURPLUS_STOP_THRESHOLD_W: stop_threshold_w,
                },
            ),
            errors=errors or {},
            description_placeholders={
                "dp_profile_problem": self._profile_json_problem or "",
                "dp_profile_fields": ", ".join(known_dp_profile_fields()),
            },
        )


def _flatten_sections(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Undo the nesting that collapsible sections introduce.

    Sections are a display grouping, so the stored options must not inherit their
    shape: every reader -- settings, diagnostics, existing installations -- expects
    flat keys, and nesting them would need a migration for no benefit.
    """
    flat: dict[str, Any] = {}
    for key, value in user_input.items():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    return flat


def _legacy_high_threshold_default(options: Mapping[str, Any]) -> int:
    return option_int(
        options,
        CONF_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        DEFAULT_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
        MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
        MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT,
    )


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
    return clean_optional_text(options.get(key, default)) or None


def _sensor_selector() -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            domain=["sensor"],
            multiple=False,
        )
    )


def _boolean_sensor_selector() -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            domain=["binary_sensor", "input_boolean"],
            multiple=False,
        )
    )


def _normalize_optional_entity_value(data: dict[str, Any], key: str) -> None:
    data[key] = clean_optional_text(data.get(key))


def _normalize_text_value(data: dict[str, Any], key: str, default: str) -> None:
    value = data.get(key, default)
    if value is None:
        data[key] = default
        return
    text = str(value).strip()
    data[key] = text if text else default


def _normalize_surplus_options(data: dict[str, Any]) -> None:
    try:
        high = int(
            data.get(
                CONF_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
                DEFAULT_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT,
            )
        )
    except (TypeError, ValueError):
        high = DEFAULT_SURPLUS_BATTERY_SOC_HIGH_THRESHOLD_PCT
    try:
        low = int(
            data.get(
                CONF_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
                DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT,
            )
        )
    except (TypeError, ValueError):
        low = DEFAULT_SURPLUS_BATTERY_SOC_LOW_THRESHOLD_PCT

    high = max(
        MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, min(MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, high)
    )
    low = max(
        MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, min(MAX_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, low)
    )

    if high <= MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT:
        high = MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT + 1
    if low >= high:
        low = max(MIN_SURPLUS_BATTERY_SOC_THRESHOLD_PCT, high - 1)

    try:
        start_threshold_w = int(
            data.get(CONF_SURPLUS_START_THRESHOLD_W, DEFAULT_SURPLUS_START_THRESHOLD_W)
        )
    except (TypeError, ValueError):
        start_threshold_w = DEFAULT_SURPLUS_START_THRESHOLD_W
    try:
        stop_threshold_w = int(
            data.get(CONF_SURPLUS_STOP_THRESHOLD_W, DEFAULT_SURPLUS_STOP_THRESHOLD_W)
        )
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

    start_threshold_w = max(
        MIN_SURPLUS_THRESHOLD_W, min(MAX_SURPLUS_THRESHOLD_W, start_threshold_w)
    )
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
