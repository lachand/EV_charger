"""Reading the world for the surplus controller: sensors, tariff signals, battery.

Split out of ``solar_surplus.py``. Everything here reads Home Assistant state and
the user's settings and turns it into a value for the decision; nothing here
decides or writes to the charger. The rules are deliberately not uniform -- a
safety veto fails closed, a tariff signal fails open, a power cap simply does not
apply -- and each method says which it is.

The settings come through a getter, not a copy: the controller replaces its
settings when an option changes in place, and the reader must see the new ones.
Behaviour is unchanged by the move.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .charge_gates import battery_hysteresis
from .charge_planner import is_within_windows, parse_windows
from .option_values import coerce_optional_bool
from .preemption import parse_reservations
from .surplus_settings import SolarSurplusSettings

LOGGER = logging.getLogger(__name__)


class SurplusReader:
    def __init__(
        self,
        hass: HomeAssistant,
        get_settings: Callable[[], SolarSurplusSettings],
    ) -> None:
        self._hass = hass
        self._get_settings = get_settings
        # The battery gate's memory between readings (see `is_battery_ready`).
        self._battery_soc_hysteresis_enabled: bool | None = None

    @property
    def _settings(self) -> SolarSurplusSettings:
        return self._get_settings()

    def tracked_sensor_entities(self) -> list[str]:
        sensor_entities: list[str] = []
        for entity_id in (
            self._settings.grid_sensor_entity_id,
            self._settings.total_load_sensor_entity_id,
            self._settings.curtailment_sensor_entity_id,
            self._settings.battery_soc_sensor_entity_id,
            self._settings.battery_net_discharge_sensor_entity_id,
            self._settings.forecast_sensor_entity_id,
            self._settings.external_charge_allowed_sensor_entity_id,
            self._settings.off_peak_sensor_entity_id,
            self._settings.critical_peak_sensor_entity_id,
            # The announcing entities matter most of all: reacting to them the
            # instant they switch is the entire point of a reservation.
            *parse_reservations(self._settings.load_reservations),
        ):
            if entity_id and entity_id not in sensor_entities:
                sensor_entities.append(entity_id)
        return sensor_entities

    def read_grid_power_w(self) -> float | None:
        value = self.read_sensor_power_w(self._settings.grid_sensor_entity_id)
        if value is None:
            return None
        if self._settings.grid_sensor_inverted:
            return -value
        return value

    def read_curtailment_power_w(self) -> float:
        if not self._settings.curtailment_sensor_entity_id:
            return 0.0
        value = self.read_sensor_power_w(self._settings.curtailment_sensor_entity_id)
        if value is None:
            return 0.0
        if self._settings.curtailment_sensor_inverted:
            value = -value
        return max(0.0, value)

    def read_battery_net_discharge_w(self) -> float | None:
        if not self._settings.battery_net_discharge_sensor_entity_id:
            return None
        value = self.read_sensor_power_w(self._settings.battery_net_discharge_sensor_entity_id)
        if value is None:
            return None
        if self._settings.battery_net_discharge_sensor_inverted:
            value = -value
        return max(0.0, value)

    def battery_discharge_over_limit_w(self) -> float:
        measured_net_discharge_w = self.read_battery_net_discharge_w()
        if measured_net_discharge_w is None:
            return 0.0
        return max(0.0, measured_net_discharge_w - self.allowed_battery_discharge_w())

    def allowed_battery_discharge_w(self) -> float:
        if not self._settings.allow_battery_discharge_for_ev:
            return 0.0
        return max(0.0, float(self._settings.max_battery_discharge_for_ev_w))

    def is_battery_ready(self) -> bool:
        if not self._settings.battery_soc_sensor_entity_id:
            return True

        soc = self.read_sensor_numeric(self._settings.battery_soc_sensor_entity_id)
        if soc is None:
            return False

        self._battery_soc_hysteresis_enabled = battery_hysteresis(
            soc,
            high=float(self._settings.battery_soc_high_threshold_pct),
            low=float(self._settings.battery_soc_low_threshold_pct),
            enabled=self._battery_soc_hysteresis_enabled,
        )
        return self._battery_soc_hysteresis_enabled

    def read_external_charge_allowed(self) -> bool:
        """Whether a configured external condition currently permits charging.

        True when nothing is configured -- same discipline as every other
        optional sensor here, this feature must never be why a charge fails
        to start for someone who has not set it up. When configured, a
        missing or unparsable reading fails *closed*, unlike the power
        sensors below where a gap just means "no cap applied": what this
        guards is a safety veto (e.g. an inverter's on-grid status), and a
        gap in the reading is exactly when that protection matters most --
        the same precedent `is_battery_ready` already sets above.
        """
        entity_id = self._settings.external_charge_allowed_sensor_entity_id
        if not entity_id:
            return True
        state = self._hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return False
        value = coerce_optional_bool(state.state)
        if value is None:
            return False
        return (not value) if self._settings.external_charge_allowed_sensor_inverted else value

    def resolve_off_peak_now(self) -> bool | None:
        """Whether it is off-peak right now, from whichever source is configured.

        None means no tariff restriction is configured at all, so `plan_charge`
        arbitrates nothing and charging is unrestricted -- same discipline as
        every other optional feature here.

        Once an off-peak sensor entity is set it is the sole source of truth:
        windows stop gating (they remain usable as the cost-split fallback, see
        `consume_session_off_peak_split`). Unlike `read_external_charge_allowed`,
        this fails *open* on an unavailable or unparsable reading rather than
        falling back to windows -- a silent second source would make "why is it
        (not) charging" depend on sensor availability, and windows are only
        skipped here in the first place because malformed ones are already
        ignored rather than fatal (`parse_windows`).
        """
        entity_id = self._settings.off_peak_sensor_entity_id
        if entity_id:
            state = self._hass.states.get(entity_id)
            if state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                value = coerce_optional_bool(state.state)
                if value is not None:
                    return (not value) if self._settings.off_peak_sensor_inverted else value
            return None

        windows = parse_windows(self._settings.off_peak_windows)
        if not windows:
            return None
        return is_within_windows(dt_util.now().time(), windows)

    def resolve_critical_peak_now(self, is_off_peak: bool | None) -> bool:
        """Whether right now is worth treating as exceptionally expensive.

        Only matters during peak hours -- off-peak pricing is untouched by
        this signal in any tariff scheme this is meant to model, so a
        critical reading during an off-peak window must not block the
        battery-floor grid charge (#43's tier). Source-agnostic on purpose:
        point this at a template built from a Tempo colour sensor, a
        day-ahead spot-price threshold, or any other signal -- the
        integration does not need to know which. Fails to False (the pre-B10
        behaviour) if unconfigured or the sensor is unavailable: a stale
        reading should not become a new way to surprise someone relying on
        their departure deadline.
        """
        entity_id = self._settings.critical_peak_sensor_entity_id
        if not entity_id or is_off_peak is not False:
            return False
        state = self._hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return False
        value = coerce_optional_bool(state.state)
        if value is None:
            return False
        return (not value) if self._settings.critical_peak_sensor_inverted else value

    def read_sensor_power_w(self, entity_id: str) -> float | None:
        if not entity_id:
            return None
        value = self.read_sensor_numeric(entity_id)
        if value is None:
            return None
        state = self._hass.states.get(entity_id)
        if state is None:
            return None
        unit = str(state.attributes.get("unit_of_measurement", "")).strip().lower()
        if unit == "kw":
            return value * 1000.0
        return value

    def read_sensor_numeric(self, entity_id: str) -> float | None:
        state = self._hass.states.get(entity_id)
        if state is None:
            return None
        raw = state.state
        if raw in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
            return None
        try:
            return float(str(raw).replace(",", "."))
        except ValueError:
            LOGGER.debug(
                "Unable to parse numeric sensor '%s' value '%s'.",
                entity_id,
                raw,
            )
            return None
