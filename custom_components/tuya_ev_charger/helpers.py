from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import (
    ALLOWED_CURRENTS,
    CONF_CONTINUOUS_CURRENT,
    DEFAULT_CONTINUOUS_CURRENT,
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

        return tuple(range(min_current, max_current + 1))

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
            return opts

    return tuple(range(min_current, max_current + 1))



