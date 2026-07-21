from __future__ import annotations

from .const import ALLOWED_CURRENTS, PAUSE_CURRENT_RANGE
from .tuya_ev_charger import EVMetrics


def allowed_currents(data: EVMetrics | None) -> tuple[int, ...]:
    min_current = min(ALLOWED_CURRENTS)
    max_current = max(ALLOWED_CURRENTS)

    if data is not None and data.max_current_cfg is not None:
        max_current = min(max_current, data.max_current_cfg)

    preset_options: tuple[int, ...] = tuple(range(min_current, max_current + 1))
    if data is not None and data.adjust_current_options:
        filtered = tuple(
            sorted(
                {
                    value
                    for value in data.adjust_current_options
                    if min_current <= value <= max_current
                }
            )
        )
        if filtered:
            preset_options = filtered

    # PAUSE_CURRENT_RANGE (0-5A) is always offered in addition to whatever
    # the device reports/allows above the normal preset floor - it's used to
    # stop a vehicle auto-starting a charge, not as a "real" charge rate.
    return tuple(sorted(set(PAUSE_CURRENT_RANGE) | set(preset_options)))
