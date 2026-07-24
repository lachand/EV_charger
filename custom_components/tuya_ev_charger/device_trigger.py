"""Device triggers, so the automation editor offers charging events directly.

Without these, writing "when the car finishes charging" means knowing that the
`status` sensor exists, that its value is `charged` rather than `full` or
`complete`, and that "unplugged mid-charge" is the transition `charging → idle`
rather than a state of its own. All of that is internal vocabulary.

Every trigger here is a state trigger on the `status` sensor; the work is
naming them and delegating.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import state as state_trigger
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_FOR,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

# Trigger type -> (from_state, to_state). None means "any".
#
# `unplugged_while_charging` is the one that justifies this module: it is a
# transition, not a state, so it cannot be expressed as a simple state trigger
# by someone who has not read the source.
TRIGGER_TRANSITIONS: dict[str, tuple[str | None, str]] = {
    "charge_started": (None, "charging"),
    "charge_complete": (None, "charged"),
    "fault": (None, "fault"),
    "plugged_in": (None, "plugged_in"),
    "unplugged_while_charging": ("charging", "idle"),
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TRANSITIONS),
        vol.Optional(CONF_ENTITY_ID): cv.entity_id,
        vol.Optional(CONF_FOR): cv.positive_time_period_dict,
    }
)


def _status_entity_id(hass: HomeAssistant, device_id: str) -> str | None:
    """The device's `status` sensor, which every trigger watches.

    Matched on the unique_id suffix rather than the entity_id, which the user is
    free to rename. `evcc_status` also ends in `_status`, hence the second test.
    """
    registry = er.async_get(hass)
    for entry in er.async_entries_for_device(registry, device_id):
        if entry.domain != "sensor" or entry.platform != DOMAIN:
            continue
        unique_id = entry.unique_id or ""
        if unique_id.endswith("_status") and not unique_id.endswith("_evcc_status"):
            return entry.entity_id
    return None


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    entity_id = _status_entity_id(hass, device_id)
    if entity_id is None:
        return []
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_ENTITY_ID: entity_id,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in TRIGGER_TRANSITIONS
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    from_state, to_state = TRIGGER_TRANSITIONS[config[CONF_TYPE]]

    entity_id = config.get(CONF_ENTITY_ID) or _status_entity_id(hass, config[CONF_DEVICE_ID])
    state_config: dict[str, Any] = {
        state_trigger.CONF_PLATFORM: "state",
        state_trigger.CONF_ENTITY_ID: entity_id,
        state_trigger.CONF_TO: to_state,
    }
    if from_state is not None:
        state_config[state_trigger.CONF_FROM] = from_state
    if CONF_FOR in config:
        state_config[CONF_FOR] = config[CONF_FOR]

    state_config = await state_trigger.async_validate_trigger_config(hass, state_config)
    return await state_trigger.async_attach_trigger(
        hass, state_config, action, trigger_info, platform_type="device"
    )
