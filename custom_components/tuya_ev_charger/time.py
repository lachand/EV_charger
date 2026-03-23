from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TuyaEVChargerRuntimeData
from .const import (
    CARD_ROLE_INDEX,
    CARD_ROLE_SCHEDULE_END,
    CARD_ROLE_SCHEDULE_START,
)
from .entity import TuyaEVChargerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _ = hass
    runtime_data: TuyaEVChargerRuntimeData = entry.runtime_data
    async_add_entities(
        [
            TuyaEVChargerScheduleStartTime(entry, runtime_data),
            TuyaEVChargerScheduleEndTime(entry, runtime_data),
        ]
    )


def _parse_time(value: str | None) -> time | None:
    if not value:
        return None
    try:
        h, m = value.split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return None


def _format_time(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


class TuyaEVChargerScheduleStartTime(TuyaEVChargerEntity, TimeEntity):
    _attr_translation_key = "schedule_start"
    _attr_icon = "mdi:clock-start"

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(
            entry=entry,
            runtime_data=runtime_data,
            card_role=CARD_ROLE_SCHEDULE_START,
            card_index=CARD_ROLE_INDEX[CARD_ROLE_SCHEDULE_START],
        )
        self._attr_unique_id = f"{runtime_data.client.device_id}_schedule_start"

    @property
    def native_value(self) -> time | None:
        data = self.coordinator.data
        return _parse_time(data.schedule_start if data else None)

    async def async_set_value(self, value: time) -> None:
        data = self.coordinator.data
        enabled = bool(data and data.schedule_enabled)
        end = (data.schedule_end if data else None) or "00:00"
        if not await self._runtime_data.client.async_set_schedule(enabled, _format_time(value), end):
            raise HomeAssistantError("Unable to update schedule start time.")
        await self.coordinator.async_request_refresh()


class TuyaEVChargerScheduleEndTime(TuyaEVChargerEntity, TimeEntity):
    _attr_translation_key = "schedule_end"
    _attr_icon = "mdi:clock-end"

    def __init__(self, entry: ConfigEntry, runtime_data: TuyaEVChargerRuntimeData) -> None:
        super().__init__(
            entry=entry,
            runtime_data=runtime_data,
            card_role=CARD_ROLE_SCHEDULE_END,
            card_index=CARD_ROLE_INDEX[CARD_ROLE_SCHEDULE_END],
        )
        self._attr_unique_id = f"{runtime_data.client.device_id}_schedule_end"

    @property
    def native_value(self) -> time | None:
        data = self.coordinator.data
        return _parse_time(data.schedule_end if data else None)

    async def async_set_value(self, value: time) -> None:
        data = self.coordinator.data
        enabled = bool(data and data.schedule_enabled)
        start = (data.schedule_start if data else None) or "00:00"
        if not await self._runtime_data.client.async_set_schedule(enabled, start, _format_time(value)):
            raise HomeAssistantError("Unable to update schedule end time.")
        await self.coordinator.async_request_refresh()
