"""Detecting the settings that fail silently.

Most misconfigurations here do not raise anything. A protection limit with no
sensor to read simply never engages; a malformed off-peak window is skipped by
design; a departure time with no energy target is inert. In every case the user
gets a feature that quietly does nothing, and no way to tell that from a feature
that decided to do nothing.

Pure, like the other decision modules: values in, findings out.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ConfigProblem(StrEnum):
    """Each value is also the repair issue's translation key."""

    # A protection limit is set but has no sensor, so it never engages.
    LOAD_LIMIT_WITHOUT_SENSOR = "load_limit_without_sensor"
    INVERTER_LIMIT_WITHOUT_SENSOR = "inverter_limit_without_sensor"
    # Surplus mode is on with nothing to measure.
    SURPLUS_WITHOUT_SENSOR = "surplus_without_sensor"
    # Every window was rejected, so the schedule is not restricting anything.
    OFF_PEAK_WINDOWS_MALFORMED = "off_peak_windows_malformed"
    # A deadline with no target cannot be planned for.
    DEPARTURE_WITHOUT_ENERGY = "departure_without_energy"
    # The grid sensor appears to report the opposite sign to the one expected.
    GRID_SENSOR_SIGN_INVERTED = "grid_sensor_sign_inverted"


@dataclass(slots=True, frozen=True)
class DiagnosisInputs:
    """The settings a static check can judge without any measurement."""

    surplus_mode_enabled: bool = False
    grid_sensor_entity_id: str = ""
    max_house_power_w: int = 0
    max_inverter_power_w: int = 0
    total_load_sensor_entity_id: str = ""
    off_peak_windows_raw: str = ""
    off_peak_windows_parsed: int = 0
    departure_time: str = ""
    departure_energy_kwh: int = 0


def static_problems(inputs: DiagnosisInputs) -> list[ConfigProblem]:
    """Settings that cannot work as the user probably intends.

    Deliberately conservative: only cases where a feature was switched on and is
    guaranteed to do nothing. Anything merely unusual is left alone -- a repair
    notice for a working setup is worse than none.
    """
    problems: list[ConfigProblem] = []

    if inputs.surplus_mode_enabled and not inputs.grid_sensor_entity_id:
        problems.append(ConfigProblem.SURPLUS_WITHOUT_SENSOR)

    # Load balancing reads the grid meter; the inverter cap reads total load.
    # Either without its sensor is a protection the user believes they have.
    if inputs.max_house_power_w > 0 and not inputs.grid_sensor_entity_id:
        problems.append(ConfigProblem.LOAD_LIMIT_WITHOUT_SENSOR)
    if inputs.max_inverter_power_w > 0 and not inputs.total_load_sensor_entity_id:
        problems.append(ConfigProblem.INVERTER_LIMIT_WITHOUT_SENSOR)

    # Something was typed and none of it parsed, so nothing is restricted.
    if inputs.off_peak_windows_raw.strip() and inputs.off_peak_windows_parsed == 0:
        problems.append(ConfigProblem.OFF_PEAK_WINDOWS_MALFORMED)

    if inputs.departure_time.strip() and inputs.departure_energy_kwh <= 0:
        problems.append(ConfigProblem.DEPARTURE_WITHOUT_ENERGY)

    return problems


# How much the car's draw must change before a sample is worth anything: below
# this the grid reading moves for a hundred other reasons.
MIN_EV_POWER_DELTA_W = 800.0
# The grid must move by at least this share of the car's change for the sample to
# be attributable to the car at all.
MIN_ATTRIBUTABLE_SHARE = 0.5
# Consecutive contradicting samples before saying anything. A cloud passing at
# the same instant as a current change can fake one; three in a row is not luck.
REQUIRED_SAMPLES = 3


@dataclass(slots=True)
class GridSignDetector:
    """Watches whether the grid reading moves the right way with the car.

    When the car's draw rises, an import-positive grid meter must rise too. If it
    consistently falls instead, the sign convention is inverted -- which makes
    surplus regulation chase its own tail and load balancing compute a budget
    larger than the whole supply.

    Only near-certain evidence counts. Sign errors are permanent, so waiting for
    a third confirmation costs nothing, whereas telling somebody their working
    setup is misconfigured costs their trust.
    """

    last_ev_power_w: float | None = None
    last_grid_power_w: float | None = None
    contradictions: int = 0

    def observe(self, *, ev_power_w: float, grid_power_w: float) -> bool:
        """Feed one reading. True once the sign looks inverted."""
        previous_ev = self.last_ev_power_w
        previous_grid = self.last_grid_power_w
        self.last_ev_power_w = ev_power_w
        self.last_grid_power_w = grid_power_w

        if previous_ev is None or previous_grid is None:
            return self.inverted

        ev_delta = ev_power_w - previous_ev
        if abs(ev_delta) < MIN_EV_POWER_DELTA_W:
            return self.inverted

        grid_delta = grid_power_w - previous_grid
        # The grid barely moved: solar or another load absorbed the change, so
        # this sample says nothing about the sign either way.
        if abs(grid_delta) < abs(ev_delta) * MIN_ATTRIBUTABLE_SHARE:
            return self.inverted

        if (ev_delta > 0) == (grid_delta > 0):
            # Moved together, as an import-positive meter should. Any earlier
            # suspicion was noise.
            self.contradictions = 0
        else:
            self.contradictions += 1

        return self.inverted

    @property
    def inverted(self) -> bool:
        return self.contradictions >= REQUIRED_SAMPLES

    def reset(self) -> None:
        self.last_ev_power_w = None
        self.last_grid_power_w = None
        self.contradictions = 0
