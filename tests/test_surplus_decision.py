"""The surplus arithmetic, which had no coverage at all before it was extracted.

`solar_surplus.py` is the most intricate module in the integration and was the
only one with zero tests, precisely because its maths was tangled with sensor
reading and a state machine.
"""

from __future__ import annotations

import pytest


def _inputs(**kwargs):
    from tuya_ev_charger.surplus_decision import SurplusInputs

    base = {"grid_power_w": 0.0, "ev_power_w": 0.0}
    base.update(kwargs)
    return SurplusInputs(**base)


# --- raw surplus ----------------------------------------------------------


def test_exporting_to_the_grid_is_surplus():
    from tuya_ev_charger.surplus_decision import raw_surplus_w

    # Grid power is negative when exporting: 2 kW going out is 2 kW available.
    assert raw_surplus_w(_inputs(grid_power_w=-2000.0), battery_ready=True) == 2000.0


def test_the_car_s_own_draw_is_added_back():
    """Otherwise regulation chases its own tail.

    Charging at 3 kW with the grid balanced means the solar is producing exactly
    what the car takes, so the surplus is 3 kW -- not zero.
    """
    from tuya_ev_charger.surplus_decision import raw_surplus_w

    surplus = raw_surplus_w(
        _inputs(grid_power_w=0.0, ev_power_w=3000.0), battery_ready=True
    )
    assert surplus == 3000.0


def test_importing_from_the_grid_is_a_deficit():
    from tuya_ev_charger.surplus_decision import raw_surplus_w

    assert raw_surplus_w(_inputs(grid_power_w=1500.0), battery_ready=True) == -1500.0


def test_curtailed_production_counts_only_once_the_battery_is_ready():
    """Below the battery threshold the inverter would rather charge the battery."""
    from tuya_ev_charger.surplus_decision import raw_surplus_w

    inputs = _inputs(grid_power_w=-500.0, curtailed_power_w=2000.0)
    assert raw_surplus_w(inputs, battery_ready=True) == 2500.0
    assert raw_surplus_w(inputs, battery_ready=False) == 500.0


def test_battery_discharge_beyond_budget_is_not_surplus():
    from tuya_ev_charger.surplus_decision import raw_surplus_w

    inputs = _inputs(grid_power_w=-3000.0, battery_discharge_over_limit_w=1000.0)
    assert raw_surplus_w(inputs, battery_ready=True) == 2000.0


# --- surplus to amperes ---------------------------------------------------


LADDER = (6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16)


@pytest.mark.parametrize(
    ("surplus_w", "expected"),
    [
        (0.0, 0),
        (-500.0, 0),      # a deficit never charges
        (1000.0, 0),      # below the 6 A minimum
        (1380.0, 6),      # exactly 6 A at 230 V
        (2300.0, 10),
        (2400.0, 10),     # rounds down: never import to top up
        (3680.0, 16),
        (10_000.0, 16),   # capped by what the charger offers
    ],
)
def test_current_supported_by_surplus(surplus_w, expected):
    from tuya_ev_charger.surplus_decision import current_supported_by

    assert current_supported_by(surplus_w, LADDER) == expected


def test_current_is_zero_without_a_usable_voltage():
    from tuya_ev_charger.surplus_decision import current_supported_by

    assert current_supported_by(5000.0, LADDER, line_voltage=0) == 0


# --- ramping --------------------------------------------------------------


def test_ramp_moves_one_rung_at_a_time():
    """Cars dislike large jumps; some abort the session outright."""
    from tuya_ev_charger.surplus_decision import ramp_towards

    assert ramp_towards(6, 16, LADDER) == 7
    assert ramp_towards(16, 6, LADDER) == 15


def test_ramp_never_overshoots():
    from tuya_ev_charger.surplus_decision import ramp_towards

    assert ramp_towards(6, 7, LADDER, step=5) == 7
    assert ramp_towards(16, 15, LADDER, step=5) == 15


def test_ramp_at_target_is_a_no_op():
    from tuya_ev_charger.surplus_decision import ramp_towards

    assert ramp_towards(10, 10, LADDER) == 10


def test_ramp_snaps_a_setpoint_that_left_the_ladder():
    """The charger can report a value the current options no longer contain."""
    from tuya_ev_charger.surplus_decision import ramp_towards

    assert ramp_towards(20, 6, (6, 8, 10)) == 8


def test_ramp_without_a_ladder_changes_nothing():
    from tuya_ev_charger.surplus_decision import ramp_towards

    assert ramp_towards(10, 16, ()) == 10


# --- forecast smoothing ---------------------------------------------------


def test_without_a_forecast_the_measurement_passes_through():
    from tuya_ev_charger.surplus_decision import ForecastState, apply_forecast

    result = apply_forecast(
        raw_w=1500.0, forecast_w=None, now=100.0, state=ForecastState(),
        weight_pct=35, smoothing_s=180, drop_guard_w=500.0,
    )
    assert result.effective_surplus_w == 1500.0
    assert result.state.ema_w == 1500.0


def test_forecast_is_blended_by_weight():
    from tuya_ev_charger.surplus_decision import ForecastState, apply_forecast

    result = apply_forecast(
        raw_w=1000.0, forecast_w=2000.0, now=100.0, state=ForecastState(),
        weight_pct=50, smoothing_s=180, drop_guard_w=0.0,
    )
    assert result.effective_surplus_w == pytest.approx(1500.0)


def test_a_passing_cloud_does_not_collapse_the_surplus():
    """The drop guard is the whole point: ride out a transient, do not stop."""
    from tuya_ev_charger.surplus_decision import ForecastState, apply_forecast

    settled = ForecastState(ema_w=3000.0, last_sample_ts=100.0)
    result = apply_forecast(
        raw_w=200.0,        # cloud
        forecast_w=200.0,
        now=130.0,          # only 30 s later, so the average barely moves
        state=settled,
        weight_pct=35, smoothing_s=180, drop_guard_w=500.0,
    )
    # Held near the smoothed value rather than following the dip down to 200 W.
    assert result.effective_surplus_w > 2000.0


def test_a_sustained_drop_is_eventually_followed():
    """The guard must delay a stop, not prevent one."""
    from tuya_ev_charger.surplus_decision import ForecastState, apply_forecast

    state = ForecastState(ema_w=3000.0, last_sample_ts=0.0)
    now = 0.0
    for _ in range(20):
        now += 60.0
        result = apply_forecast(
            raw_w=200.0, forecast_w=200.0, now=now, state=state,
            weight_pct=35, smoothing_s=180, drop_guard_w=500.0,
        )
        state = result.state
    assert result.effective_surplus_w < 800.0


# --- load-balancing helper ------------------------------------------------


def test_power_budget_caps_the_current():
    from tuya_ev_charger.surplus_decision import cap_to_available_power

    assert cap_to_available_power(LADDER, 2300.0) == 10
    # No headroom means stop, and a negative budget must not wrap around.
    assert cap_to_available_power(LADDER, 0.0) == 0
    assert cap_to_available_power(LADDER, -1000.0) == 0


# --- load balancing -------------------------------------------------------


def test_headroom_removes_the_car_from_the_house_reading():
    """The car's draw is already inside the grid measurement.

    A 6 kVA house importing 5 kW of which the car takes 3 kW is really using
    2 kW without it, so 4 kW remain available.
    """
    from tuya_ev_charger.surplus_decision import headroom_for_car_w

    headroom = headroom_for_car_w(
        grid_power_w=5000.0, ev_power_w=3000.0, house_limit_w=6000.0
    )
    assert headroom == pytest.approx(4000.0)


def test_headroom_is_negative_when_the_house_alone_is_over():
    """Oven plus hob can exceed the subscription with no car at all."""
    from tuya_ev_charger.surplus_decision import headroom_for_car_w

    headroom = headroom_for_car_w(
        grid_power_w=7000.0, ev_power_w=0.0, house_limit_w=6000.0
    )
    assert headroom == pytest.approx(-1000.0)


def test_exporting_leaves_the_full_subscription_available():
    from tuya_ev_charger.surplus_decision import headroom_for_car_w

    headroom = headroom_for_car_w(
        grid_power_w=-2000.0, ev_power_w=0.0, house_limit_w=6000.0
    )
    assert headroom == pytest.approx(8000.0)


def test_load_balancing_caps_the_current():
    from tuya_ev_charger.surplus_decision import cap_to_available_power, headroom_for_car_w

    # 6 kVA house, oven drawing 3.5 kW, car currently pulling 3.7 kW at 16 A.
    headroom = headroom_for_car_w(
        grid_power_w=7200.0, ev_power_w=3700.0, house_limit_w=6000.0
    )
    assert cap_to_available_power(LADDER, headroom) == 10  # 2.5 kW left -> 10 A


def test_no_headroom_means_stop_not_minimum():
    """Below the lowest offered current the answer is zero, and the caller stops."""
    from tuya_ev_charger.surplus_decision import cap_to_available_power, headroom_for_car_w

    headroom = headroom_for_car_w(
        grid_power_w=6500.0, ev_power_w=0.0, house_limit_w=6000.0
    )
    assert cap_to_available_power(LADDER, headroom) == 0
