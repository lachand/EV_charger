"""The current ceilings that protect the installation, and what they reserve.

Two independent caps (the house's subscribed limit, and the inverter's rating) plus
the power held back for appliances that have announced themselves. Split out of
``solar_surplus.py``; behaviour is unchanged.

The clock is injected rather than imported: the controller's tests drive time by
patching the controller module's ``monotonic``, and the reservation expiry must
follow the same clock.
"""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.core import HomeAssistant

from .charger_metrics import EVMetrics
from .preemption import ReservationTracker, headroom_with_reservations, parse_reservations
from .surplus_decision import cap_to_available_power, headroom_for_car_w
from .surplus_metrics import ev_power_w, line_voltage
from .surplus_reader import SurplusReader
from .surplus_settings import SolarSurplusSettings


class ProtectionCaps:
    def __init__(
        self,
        hass: HomeAssistant,
        get_settings: Callable[[], SolarSurplusSettings],
        inputs: SurplusReader,
        clock: Callable[[], float],
    ) -> None:
        self._hass = hass
        self._get_settings = get_settings
        self._inputs = inputs
        self._clock = clock
        # Built once per controller and kept across in-place settings changes: this
        # tracker holds the announcement clocks, which must not restart on a reload.
        self._reservations = ReservationTracker()

    @property
    def _settings(self) -> SolarSurplusSettings:
        return self._get_settings()

    def protection_cap(
        self,
        data: EVMetrics,
        available_currents: tuple[int, ...],
    ) -> tuple[int | None, str | None]:
        """The tighter of the load-balancing and inverter caps.

        Returns the binding cap and which limit produced it (``load_limit`` or
        ``inverter_limit``), for the decision reason. ``(None, None)`` when
        neither is configured or neither has a usable reading — a cap computed
        from a missing measurement would either stop a healthy charge or fail to
        protect, both worse than not capping.
        """
        candidates = (
            ("load_limit", self.load_limit_current(data, available_currents)),
            ("inverter_limit", self.inverter_limit_current(data, available_currents)),
        )
        binding_source: str | None = None
        binding_cap: int | None = None
        for source, cap in candidates:
            if cap is None:
                continue
            if binding_cap is None or cap < binding_cap:
                binding_cap, binding_source = cap, source
        return binding_cap, binding_source

    def load_limit_current(
        self,
        data: EVMetrics,
        available_currents: tuple[int, ...],
    ) -> int | None:
        """Highest current that keeps the house under its subscribed limit.

        Returns None when load balancing is off or the grid reading is missing:
        capping blind would be worse than not capping, since a stale or absent
        measurement would either stop a healthy charge or fail to protect.
        """
        limit_w = self._settings.max_house_power_w
        if limit_w <= 0:
            return None
        grid_power_w = self._inputs.read_grid_power_w()
        if grid_power_w is None:
            return None

        headroom_w = headroom_for_car_w(
            grid_power_w=grid_power_w,
            ev_power_w=ev_power_w(data),
            house_limit_w=float(limit_w),
        )
        return self.cap_current(available_currents, headroom_w, data)

    def inverter_limit_current(
        self,
        data: EVMetrics,
        available_currents: tuple[int, ...],
    ) -> int | None:
        """Highest current that keeps total inverter output under its rating.

        The measurement point is what distinguishes this from load balancing.
        Load balancing reads the grid meter; on a hybrid inverter with the house
        on its backup output, the battery covers a sudden household draw so the
        grid meter stays near zero while the inverter is being overloaded past
        its rating. This must therefore read *total* load — household plus car —
        not the grid.

        Announced-but-not-yet-measured loads are subtracted on top. A cap can
        only react as fast as its sensor, and a hob is +2 kW in under a second,
        so waiting for the measurement means reacting after the overload. The
        reservation expires once the sensor has had time to catch up, which is
        what stops the same appliance being counted twice.

        Returns None, like load balancing, when disabled or the reading is
        missing: a cap off a stale total-load figure is worse than none.
        """
        limit_w = self._settings.max_inverter_power_w
        if limit_w <= 0:
            return None
        total_load_w = self._inputs.read_sensor_power_w(self._settings.total_load_sensor_entity_id)
        if total_load_w is None:
            return None

        headroom_w = headroom_with_reservations(
            limit_w=float(limit_w),
            measured_load_w=total_load_w,
            ev_power_w=ev_power_w(data),
            reserved_w=self.reserved_power_w(),
        )
        return self.cap_current(available_currents, headroom_w, data)

    def cap_current(
        self,
        available_currents: tuple[int, ...],
        headroom_w: float,
        data: EVMetrics,
    ) -> int | None:
        return cap_to_available_power(
            available_currents,
            headroom_w,
            line_voltage=line_voltage(data),
            phases=self._settings.installation_phases,
        )

    def reserved_power_w(self) -> float:
        """Watts held back for appliances that have announced themselves."""
        table = parse_reservations(self._settings.load_reservations)
        if not table:
            return 0.0
        now = self._clock()
        states = {
            entity_id: (state.state if (state := self._hass.states.get(entity_id)) else None)
            for entity_id in table
        }
        self._reservations.observe(table, states, now)
        return self._reservations.reserved_w(table, now)
