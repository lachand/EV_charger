from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import (
    ALLOWED_CURRENTS,
    CONF_CONTINUOUS_CURRENT,
    CONF_MAX_CHARGE_CURRENT_A,
    CONF_MIN_CHARGE_CURRENT_A,
    DEFAULT_CONTINUOUS_CURRENT,
    DEFAULT_MAX_CHARGE_CURRENT_A,
    DEFAULT_MIN_CHARGE_CURRENT_A,
)
from .tuya_ev_charger import EVMetrics


def allowed_currents(data: EVMetrics | None, options: Mapping[str, Any] | None = None) -> tuple[int, ...]:
    min_current = min(ALLOWED_CURRENTS)
    max_current = max(ALLOWED_CURRENTS)

    if data is not None and data.max_current_cfg is not None:
        max_current = min(max_current, data.max_current_cfg)

    is_continuous = DEFAULT_CONTINUOUS_CURRENT
    if options is not None:
        is_continuous = bool(options.get(CONF_CONTINUOUS_CURRENT, DEFAULT_CONTINUOUS_CURRENT))

    if is_continuous:
        if data is not None and data.adjust_current_options:
            min_current = min(data.adjust_current_options)
            max_current = max(data.adjust_current_options)

        if data is not None and data.max_current_cfg is not None:
            max_current = min(max_current, data.max_current_cfg)

        return _apply_installation_limits(
            tuple(range(min_current, max_current + 1)), options
        )

    if data is not None and data.adjust_current_options:
        opts = tuple(
            sorted(
                {
                    value
                    for value in data.adjust_current_options
                    if min_current <= value <= max_current
                }
            )
        )
        if opts:
            return _apply_installation_limits(opts, options)

    return _apply_installation_limits(tuple(range(min_current, max_current + 1)), options)


def _apply_installation_limits(
    currents: tuple[int, ...],
    options: Mapping[str, Any] | None,
) -> tuple[int, ...]:
    """Narrow the offered currents to what the wiring can actually carry.

    A charger's rating is not its circuit's rating: a 32 A unit on a 25 A breaker
    must never be offered 32 A, by the number entity or by surplus regulation.
    Exceeding it does not fail cleanly — the breaker trips after a long session,
    which is hard to connect back to a cause.

    Applied last, and to every branch, so no later step can widen the range
    again. A limit that would leave nothing offered keeps the single lowest
    current rather than returning an empty tuple, which would read as "this
    charger reports no currents" and disable the entity entirely.
    """
    if not currents or options is None:
        return currents

    ceiling = _positive_int(options.get(CONF_MAX_CHARGE_CURRENT_A), DEFAULT_MAX_CHARGE_CURRENT_A)
    floor = _positive_int(options.get(CONF_MIN_CHARGE_CURRENT_A), DEFAULT_MIN_CHARGE_CURRENT_A)

    limited = currents
    if ceiling > 0:
        limited = tuple(value for value in limited if value <= ceiling) or (min(currents),)
    if floor > 0:
        limited = tuple(value for value in limited if value >= floor) or (max(limited),)
    return limited


def _positive_int(value: Any, default: int) -> int:
    """0 (the default) means "no limit"; anything unparseable means the same."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
