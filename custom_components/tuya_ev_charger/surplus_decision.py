"""Pure surplus arithmetic, with no Home Assistant dependency.

`solar_surplus.py` mixes sensor reading, a state machine, decision-making and
ramping in one 1 200-line class, which is why it had no tests at all: exercising
the maths meant standing up half of Home Assistant.

Everything here takes plain values and returns plain values. The controller
keeps the I/O and the state; this module owns the arithmetic, so it can be
tested directly — and so the tariff, departure and load-balancing features can
plug into the same place rather than growing the controller further.
"""

from __future__ import annotations

from dataclasses import dataclass

# Home Assistant reports power in watts; the charger is commanded in amperes,
# and this is the assumed line voltage for that conversion.
DEFAULT_LINE_VOLTAGE_V = 230


@dataclass(slots=True, frozen=True)
class SurplusInputs:
    """Everything the surplus arithmetic needs, already read from sensors."""

    # Positive when importing from the grid, negative when exporting.
    grid_power_w: float
    # What the charger is currently drawing, added back to reconstruct the
    # surplus that would exist if the car were not charging.
    ev_power_w: float
    # Production the inverter is holding back; only meaningful in zero-injection
    # setups, and only trustworthy while the battery is charged enough.
    curtailed_power_w: float = 0.0
    # Battery discharge beyond the budget allowed for the car, which must not be
    # counted as surplus.
    battery_discharge_over_limit_w: float = 0.0


def raw_surplus_w(inputs: SurplusInputs, *, battery_ready: bool) -> float:
    """Surplus available before any forecast smoothing.

    Grid power is positive on import, so adding the car's own draw back gives
    the surplus that would exist if it stopped charging — otherwise regulation
    would chase its own tail.
    """
    surplus = inputs.ev_power_w - inputs.grid_power_w

    # Curtailed production is only real headroom once the battery is happy;
    # below that threshold the inverter would rather charge the battery.
    if battery_ready:
        surplus += inputs.curtailed_power_w

    # Discharging the house battery into the car is not surplus.
    if inputs.battery_discharge_over_limit_w > 0:
        surplus -= inputs.battery_discharge_over_limit_w

    return surplus


@dataclass(slots=True, frozen=True)
class ForecastState:
    """Rolling state of the forecast smoother, carried between evaluations."""

    ema_w: float | None = None
    last_sample_ts: float | None = None


@dataclass(slots=True, frozen=True)
class ForecastResult:
    effective_surplus_w: float
    state: ForecastState


def apply_forecast(
    *,
    raw_w: float,
    forecast_w: float | None,
    now: float,
    state: ForecastState,
    weight_pct: int,
    smoothing_s: int,
    drop_guard_w: float,
) -> ForecastResult:
    """Blend a solar forecast into the measured surplus.

    The forecast is an anti-drop guard, not a source of truth: it keeps a
    passing cloud from stopping a charge that is about to be viable again. With
    no forecast sensor the measurement passes through untouched.
    """
    if forecast_w is None:
        return ForecastResult(raw_w, ForecastState(ema_w=raw_w, last_sample_ts=now))

    weight = max(0.0, min(1.0, weight_pct / 100.0))
    blended = (raw_w * (1.0 - weight)) + (forecast_w * weight)

    ema = state.ema_w
    if ema is None:
        ema = blended
    else:
        elapsed = max(0.0, now - state.last_sample_ts) if state.last_sample_ts else 0.0
        # Time-based smoothing, so an irregular poll interval does not change
        # how fast the average moves.
        alpha = min(1.0, elapsed / max(1.0, float(smoothing_s))) if elapsed > 0 else 0.0
        ema = ema + (blended - ema) * alpha

    # Never fall more than the guard below the smoothed value: that is what
    # rides out a brief drop instead of stopping the session.
    effective = max(blended, ema - float(drop_guard_w))
    return ForecastResult(effective, ForecastState(ema_w=ema, last_sample_ts=now))


def current_supported_by(
    surplus_w: float,
    available_currents: tuple[int, ...],
    *,
    line_voltage: int = DEFAULT_LINE_VOLTAGE_V,
) -> int:
    """Highest offered current the surplus can actually sustain.

    Rounds down: drawing more than the surplus would import from the grid, which
    is the one thing surplus mode exists to avoid.
    """
    if line_voltage <= 0 or surplus_w <= 0:
        return 0
    affordable = int(surplus_w // line_voltage)
    candidates = [current for current in available_currents if current <= affordable]
    return max(candidates) if candidates else 0


def ramp_towards(
    current: int,
    target: int,
    available_currents: tuple[int, ...],
    *,
    step: int = 1,
) -> int:
    """Move one step of the offered ladder towards the target.

    Cars dislike large current jumps, and some abort the session outright, so
    changes are walked rather than applied at once.
    """
    if target == current:
        return current

    ladder = tuple(sorted(set(available_currents)))
    if not ladder:
        return current

    steps = max(1, step)
    # Snap both ends onto the ladder: the charger may report a setpoint the
    # current options no longer contain, e.g. after its maximum changed.
    if current not in ladder:
        current = min(ladder, key=lambda candidate: abs(candidate - current))
    if target not in ladder:
        target = min(ladder, key=lambda candidate: abs(candidate - target))

    here = ladder.index(current)
    there = ladder.index(target)
    if there > here:
        return ladder[min(here + steps, there)]
    return ladder[max(here - steps, there)]


def headroom_for_car_w(
    *,
    grid_power_w: float,
    ev_power_w: float,
    house_limit_w: float,
) -> float:
    """Power the car may draw without pushing the house past its limit.

    The car's own draw is already part of the grid reading, so it is removed
    first to get what the rest of the house is using; the remainder of the
    subscription is what is left for charging. Can be negative when the house
    alone already exceeds the limit.
    """
    house_without_car_w = grid_power_w - ev_power_w
    return house_limit_w - house_without_car_w


def cap_to_available_power(
    available_currents: tuple[int, ...],
    headroom_w: float,
    *,
    line_voltage: int = DEFAULT_LINE_VOLTAGE_V,
) -> int:
    """Largest offered current that fits within a power budget.

    Used by load balancing to stay under the main breaker: unlike
    ``current_supported_by`` a zero budget means "stop", not "no surplus".
    """
    return current_supported_by(
        max(0.0, headroom_w), available_currents, line_voltage=line_voltage
    )
