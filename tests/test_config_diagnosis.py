"""Detecting settings that fail silently.

The risk here runs the other way from most tests: a false positive tells someone
their working setup is broken. So the checks are deliberately narrow, and these
tests spend as much effort on what must *not* be reported as on what must.
"""

from __future__ import annotations

import pytest


def _inputs(**kwargs):
    from tuya_ev_charger.config_diagnosis import DiagnosisInputs

    return DiagnosisInputs(**kwargs)


def _problems(**kwargs):
    from tuya_ev_charger.config_diagnosis import static_problems

    return static_problems(_inputs(**kwargs))


# --- protections that cannot engage ---------------------------------------


def test_a_load_limit_without_a_grid_sensor_is_reported():
    """The user believes the breaker is protected; nothing reads the meter."""
    from tuya_ev_charger.config_diagnosis import ConfigProblem

    assert ConfigProblem.LOAD_LIMIT_WITHOUT_SENSOR in _problems(max_house_power_w=9200)


def test_an_inverter_limit_without_a_total_load_sensor_is_reported():
    from tuya_ev_charger.config_diagnosis import ConfigProblem

    assert ConfigProblem.INVERTER_LIMIT_WITHOUT_SENSOR in _problems(max_inverter_power_w=5500)


def test_the_inverter_limit_needs_its_own_sensor_not_the_grid_one():
    """The whole point of the inverter cap is that the grid meter cannot see the
    overload; a grid sensor does not satisfy it."""
    from tuya_ev_charger.config_diagnosis import ConfigProblem

    problems = _problems(max_inverter_power_w=5500, grid_sensor_entity_id="sensor.grid")
    assert ConfigProblem.INVERTER_LIMIT_WITHOUT_SENSOR in problems


def test_surplus_mode_without_a_sensor_is_reported():
    from tuya_ev_charger.config_diagnosis import ConfigProblem

    assert ConfigProblem.SURPLUS_WITHOUT_SENSOR in _problems(surplus_mode_enabled=True)


# --- schedules that do nothing --------------------------------------------


def test_windows_that_all_failed_to_parse_are_reported():
    """Malformed windows are skipped by design, which is invisible."""
    from tuya_ev_charger.config_diagnosis import ConfigProblem

    problems = _problems(off_peak_windows_raw="22h-6h", off_peak_windows_parsed=0)
    assert ConfigProblem.OFF_PEAK_WINDOWS_MALFORMED in problems


def test_windows_that_parsed_are_not_reported():
    from tuya_ev_charger.config_diagnosis import ConfigProblem

    problems = _problems(off_peak_windows_raw="22:00-06:00", off_peak_windows_parsed=1)
    assert ConfigProblem.OFF_PEAK_WINDOWS_MALFORMED not in problems


def test_a_departure_time_without_an_energy_target_is_reported():
    from tuya_ev_charger.config_diagnosis import ConfigProblem

    assert ConfigProblem.DEPARTURE_WITHOUT_ENERGY in _problems(departure_time="07:00")


# --- silence is the default ------------------------------------------------


def test_an_empty_configuration_reports_nothing():
    """Everything off is a perfectly good setup, not a problem."""
    assert _problems() == []


def test_a_complete_configuration_reports_nothing():
    assert (
        _problems(
            surplus_mode_enabled=True,
            grid_sensor_entity_id="sensor.grid",
            max_house_power_w=9200,
            max_inverter_power_w=5500,
            total_load_sensor_entity_id="sensor.total_load",
            off_peak_windows_raw="22:00-06:00",
            off_peak_windows_parsed=1,
            departure_time="07:00",
            departure_energy_kwh=20,
        )
        == []
    )


def test_a_limit_left_at_zero_is_not_a_problem():
    """0 means "off", so no sensor is needed and nothing should be said."""
    assert _problems(max_house_power_w=0, max_inverter_power_w=0) == []


# --- inverted grid sensor --------------------------------------------------


def _detector():
    from tuya_ev_charger.config_diagnosis import GridSignDetector

    return GridSignDetector()


def _feed(detector, samples):
    for ev, grid in samples:
        detector.observe(ev_power_w=ev, grid_power_w=grid)
    return detector.inverted


def test_a_correctly_signed_sensor_is_never_flagged():
    """Car draw up, import up: exactly what an import-positive meter does."""
    detector = _detector()
    assert not _feed(detector, [(0, 500), (2300, 2800), (4600, 5100), (2300, 2800), (0, 500)])


def test_an_inverted_sensor_is_flagged_after_enough_evidence():
    """Car draw up while the meter goes *down*, repeatedly."""
    from tuya_ev_charger.config_diagnosis import REQUIRED_SAMPLES

    detector = _detector()
    samples = [(0, -500)]
    for index in range(REQUIRED_SAMPLES):
        # Each step adds 2 kW of car draw and the reading falls by 2 kW.
        samples.append((2000 * (index + 1), -500 - 2000 * (index + 1)))

    assert _feed(detector, samples)


def test_one_contradicting_sample_is_not_enough():
    """A cloud passing exactly as the current changes can fake a single sample."""
    detector = _detector()
    assert not _feed(detector, [(0, -500), (3000, -3500)])


def test_a_correct_sample_clears_earlier_suspicion():
    """Suspicion must not accumulate across unrelated moments."""
    from tuya_ev_charger.config_diagnosis import REQUIRED_SAMPLES

    detector = _detector()
    _feed(detector, [(0, -500), (3000, -3500)])  # one contradiction
    assert detector.contradictions == 1

    _feed(detector, [(0, -6500)])  # car drops 3 kW, grid drops too: consistent
    assert detector.contradictions == 0

    # And the full count is required again from scratch.
    samples = [(0, 0)]
    for index in range(REQUIRED_SAMPLES - 1):
        samples.append((2000 * (index + 1), -2000 * (index + 1)))
    assert not _feed(detector, samples)


def test_small_current_changes_are_ignored():
    """A 1 A step is ~230 W, far too small to attribute a grid movement to."""
    detector = _detector()
    assert not _feed(detector, [(2300, 0), (2530, -300), (2760, -600), (2990, -900)])
    assert detector.contradictions == 0


def test_a_grid_reading_that_barely_moves_says_nothing():
    """Solar or another load absorbed the change, so the sample is uninformative
    rather than evidence either way."""
    detector = _detector()
    _feed(detector, [(0, 500), (4000, 400), (0, 500), (4000, 400)])
    assert detector.contradictions == 0


@pytest.mark.parametrize("first", [(0.0, 0.0)])
def test_the_first_sample_can_never_decide(first):
    detector = _detector()
    detector.observe(ev_power_w=first[0], grid_power_w=first[1])
    assert detector.inverted is False


def test_reset_forgets_everything():
    detector = _detector()
    _feed(detector, [(0, -500), (3000, -3500)])
    detector.reset()
    assert detector.contradictions == 0
    assert detector.last_ev_power_w is None
