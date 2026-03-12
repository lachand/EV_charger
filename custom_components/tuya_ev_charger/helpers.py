from __future__ import annotations

from .const import ALLOWED_CURRENTS
from .tuya_ev_charger import EVMetrics


def allowed_currents(data: EVMetrics | None) -> tuple[int, ...]:
    min_current = min(ALLOWED_CURRENTS)
    max_current = max(ALLOWED_CURRENTS)

    if data is not None and data.max_current_cfg is not None:
        max_current = min(max_current, data.max_current_cfg)

    if data is not None and data.adjust_current_options:
        options = tuple(
            sorted(
                {
                    value
                    for value in data.adjust_current_options
                    if min_current <= value <= max_current
                }
            )
        )
        if options:
            return options

    return tuple(range(min_current, max_current + 1))
