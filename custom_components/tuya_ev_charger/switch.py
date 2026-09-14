from __future__ import annotations

from collections.abc import Callable
from time import monotonic

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TuyaEVChargerRuntimeData
from .const import (
    ALLOWED_CURRENTS,
    CARD_ROLE_CHARGE_SESSION,
    CARD_ROLE_FORCE_CHARGE,
    CARD_ROLE_INDEX,
    CARD_ROLE_SCHEDULE_ENABLED,
    CARD_ROLE_SURPLUS_MODE,
    CONF_SURPLUS_MODE_ENABLED,
    DEFAULT_SURPLUS_MODE_ENABLED,
)
from .entity import TuyaEVChargerEntity
from .tuya_ev_charger import WORK_STATE_CHARGING

PARALLEL_UPDATES = 1  # The charger accepts one local connection; writes are serialised.

# A charger without DP 140 reports only its operating state, and after a start
# command it steps PAUSE -> IDLEINS -> WORKING over several seconds. Hold the
# commanded state until the charger's own state agrees, so the switch does not
# flick back off for those seconds right after the user turns it on.
_PENDING_TIMEOUT_S = 90.0
# Operating states (raw DP 109) that mean a pending "on" will not arrive -- no
# cable, a fault, or a finished session. Stop waiting and show the real state.
_NOT_STARTING_STATES = frozenset({"SLEEP", "IDLE", "STOP", "ERRORPAUSE"})

# Same ceiling as SERVICE_FORCE_CHARGE_SCHEMA's duration_minutes (__init__.py):
# a safety cap in case the switch is left on, not a target duration -- turning
# it off is the real way to end a forced session.
_FORCE_CHARGE_MAX_DURATION_S = 24 * 60 * 60


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime_data: TuyaEVChargerRuntimeData = entry.runtime_data
    async_add_entities(
        [
            TuyaEVChargerChargeSessionSwitch(entry, runtime_data),
            TuyaEVChargerForceChargeSwitch(entry, runtime_data),
            TuyaEVChargerNfcSwitch(entry, runtime_data),
            TuyaEVChargerSurplusModeSwitch(entry, runtime_data),
            TuyaEVChargerScheduleSwitch(entry, runtime_data),
        ]
    )


class TuyaEVChargerChargeSessionSwitch(TuyaEVChargerEntity, SwitchEntity):
    _attr_translation_key = "charge_session"

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(
            entry=entry,
            runtime_data=runtime_data,
            card_role=CARD_ROLE_CHARGE_SESSION,
            card_index=CARD_ROLE_INDEX[CARD_ROLE_CHARGE_SESSION],
        )
        self._attr_unique_id = f"{runtime_data.client.device_id}_charge_session"
        self._pending_charge: bool | None = None
        self._pending_since: float = 0.0

    @property
    def _reported_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None:
            return None
        if data.do_charge is not None:
            return data.do_charge
        # Models that do not expose the do_charge DP (e.g. the depow 3.5kW has
        # no DP 140) only report the operating state.
        return data.work_state_debug == WORK_STATE_CHARGING

    @property
    def is_on(self) -> bool:
        """The charger's reported state, or the commanded one while it catches up.

        A charger with DP 140 echoes the change on the next poll; one without it
        walks PAUSE -> IDLEINS -> WORKING first, and reporting the raw operating
        state there flicks the switch back off for those seconds right after the
        user turned it on. Hold the commanded state until they agree, a
        no-cable/fault state rules it out, or it times out.
        """
        reported = self._reported_on
        pending = self._pending_charge
        if pending is None:
            return bool(reported)

        data = self.coordinator.data
        stalled = bool(
            pending and data is not None and data.work_state_debug in _NOT_STARTING_STATES
        )
        if reported == pending or stalled or monotonic() - self._pending_since > _PENDING_TIMEOUT_S:
            self._pending_charge = None
            return bool(reported)
        return pending

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._async_set_charging(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._async_set_charging(False)

    async def _async_set_charging(self, enabled: bool) -> None:
        # Writing the DP makes the charger beep even when nothing changes, and
        # controllers such as evcc re-assert the same state on a timer.
        data = self.coordinator.data
        if data is not None and data.do_charge is not None and data.do_charge == enabled:
            return
        if not await self._runtime_data.client.async_set_charge_enabled(enabled):
            raise HomeAssistantError(
                "Unable to start charging session."
                if enabled
                else "Unable to stop charging session."
            )
        self._pending_charge = enabled
        self._pending_since = monotonic()
        await self.coordinator.async_request_refresh()


class TuyaEVChargerForceChargeSwitch(TuyaEVChargerEntity, SwitchEntity):
    """A more discoverable front for the `force_charge_for` service (#22, #36).

    On starts a forced session at the highest current the installation,
    inverter and charger caps allow (the same "as fast as you can" clamp
    `_gate_force_charge` already does); off cancels it. Scheduling (off-peak
    windows, surplus mode) is bypassed, the physical caps are not -- exactly
    what the service already guarantees, just without a Developer Tools trip.
    """

    _attr_translation_key = "force_charge"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(
            entry=entry,
            runtime_data=runtime_data,
            card_role=CARD_ROLE_FORCE_CHARGE,
            card_index=CARD_ROLE_INDEX[CARD_ROLE_FORCE_CHARGE],
        )
        self._attr_unique_id = f"{runtime_data.client.device_id}_force_charge"
        self._unsub_listener: Callable[[], None] | None = None

    @property
    def is_on(self) -> bool:
        controller = self._runtime_data.solar_surplus_controller
        if controller is None:
            return False
        return controller.snapshot.force_charge_active

    async def async_turn_on(self, **kwargs: object) -> None:
        controller = self._runtime_data.solar_surplus_controller
        if controller is None:
            raise HomeAssistantError("Solar surplus controller is unavailable.")
        await controller.async_force_charge_for(_FORCE_CHARGE_MAX_DURATION_S, max(ALLOWED_CURRENTS))

    async def async_turn_off(self, **kwargs: object) -> None:
        controller = self._runtime_data.solar_surplus_controller
        if controller is None:
            return
        await controller.async_force_charge_for(0)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        controller = self._runtime_data.solar_surplus_controller
        if controller is None:
            return

        @callback
        def _handle_update() -> None:
            self.async_write_ha_state()

        self._unsub_listener = controller.async_add_update_listener(_handle_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_listener is not None:
            self._unsub_listener()
            self._unsub_listener = None
        await super().async_will_remove_from_hass()


class TuyaEVChargerNfcSwitch(TuyaEVChargerEntity, SwitchEntity):
    _attr_translation_key = "nfc_enabled"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(entry=entry, runtime_data=runtime_data)
        self._attr_unique_id = f"{runtime_data.client.device_id}_nfc_enabled"

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data
        if data is None or data.nfc_enabled is None:
            return False
        return data.nfc_enabled

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._async_set_nfc(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._async_set_nfc(False)

    async def _async_set_nfc(self, enabled: bool) -> None:
        data = self.coordinator.data
        if data is not None and data.nfc_enabled is not None and data.nfc_enabled == enabled:
            return
        if not await self._runtime_data.client.async_set_nfc_enabled(enabled):
            raise HomeAssistantError(
                "Unable to enable NFC." if enabled else "Unable to disable NFC."
            )
        await self.coordinator.async_request_refresh()


class TuyaEVChargerSurplusModeSwitch(TuyaEVChargerEntity, SwitchEntity):
    _attr_translation_key = "surplus_mode"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(
            entry=entry,
            runtime_data=runtime_data,
            card_role=CARD_ROLE_SURPLUS_MODE,
            card_index=CARD_ROLE_INDEX[CARD_ROLE_SURPLUS_MODE],
        )
        self._attr_unique_id = f"{runtime_data.client.device_id}_surplus_mode"

    @property
    def is_on(self) -> bool:
        return bool(
            self._entry.options.get(
                CONF_SURPLUS_MODE_ENABLED,
                DEFAULT_SURPLUS_MODE_ENABLED,
            )
        )

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._async_set_mode(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._async_set_mode(False)

    async def _async_set_mode(self, enabled: bool) -> None:
        if enabled == self.is_on:
            return
        new_options = dict(self._entry.options)
        new_options[CONF_SURPLUS_MODE_ENABLED] = enabled
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        self.async_write_ha_state()


class TuyaEVChargerScheduleSwitch(TuyaEVChargerEntity, SwitchEntity):
    _attr_translation_key = "schedule_enabled"

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(
            entry=entry,
            runtime_data=runtime_data,
            card_role=CARD_ROLE_SCHEDULE_ENABLED,
            card_index=CARD_ROLE_INDEX[CARD_ROLE_SCHEDULE_ENABLED],
        )
        self._attr_unique_id = f"{runtime_data.client.device_id}_schedule_enabled"

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data
        return bool(data and data.schedule_enabled)

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._async_set(False)

    async def _async_set(self, enabled: bool) -> None:
        data = self.coordinator.data
        start = (data.schedule_start if data else None) or "00:00"
        end = (data.schedule_end if data else None) or "00:00"
        if not await self._runtime_data.client.async_set_schedule(enabled, start, end):
            raise HomeAssistantError("Unable to update charging schedule.")
        await self.coordinator.async_request_refresh()
