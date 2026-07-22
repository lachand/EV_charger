"""Tidying of entities that already exist in the registry.

`entity_registry_enabled_default` only applies the first time an entity is
registered, so it never helps an existing install. Disabling those afterwards is
possible, but Home Assistant records who *disabled* an entity and has no
"enabled by user" flag — an integration that re-applied its policy on every
start would silently undo the user's own choices.

So we write our own marker in the registry entry options: an entity is touched
at most once, ever. Anything the user does afterwards stands.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, ENTITY_OPTION_AUTO_DISABLED

LOGGER = logging.getLogger(__name__)


def _already_handled(entry: er.RegistryEntry) -> bool:
    return bool((entry.options.get(DOMAIN) or {}).get(ENTITY_OPTION_AUTO_DISABLED))


async def async_disable_entities(
    hass: HomeAssistant,
    entry_id: str,
    unique_id_suffixes: set[str],
    *,
    reason: str,
) -> int:
    """Disable the given entities once, and remember that we did.

    Returns how many were actually changed. Entities the user disabled
    themselves, or that we have already handled, are left untouched.
    """
    if not unique_id_suffixes:
        return 0

    registry = er.async_get(hass)
    disabled = 0

    for entity in list(er.async_entries_for_config_entry(registry, entry_id)):
        matched = next(
            (key for key in unique_id_suffixes if entity.unique_id.endswith(f"_{key}")),
            None,
        )
        if matched is None or _already_handled(entity):
            continue
        # A deliberate user choice always wins.
        if entity.disabled_by is er.RegistryEntryDisabler.USER:
            continue

        options = dict(entity.options.get(DOMAIN) or {})
        options[ENTITY_OPTION_AUTO_DISABLED] = True
        registry.async_update_entity_options(entity.entity_id, DOMAIN, options)
        if entity.disabled_by is None:
            registry.async_update_entity(
                entity.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )
            disabled += 1
            LOGGER.debug("Disabled %s (%s)", entity.entity_id, matched)

    if disabled:
        LOGGER.info("Disabled %d entities that %s.", disabled, reason)
    return disabled


def unavailable_capability_keys(metrics) -> set[str]:
    """Entity keys this charger can never populate, so they read unavailable.

    Only capabilities the hardware genuinely lacks are listed: a phase the model
    does not wire, or a DP the firmware never reports. Everything else stays for
    the user to decide on.
    """
    keys: set[str] = set()
    if metrics is None:
        return keys

    for phase in ("L2", "L3"):
        if phase not in metrics.phases:
            suffix = phase.lower()
            keys.update({f"voltage_{suffix}", f"current_{suffix}", f"power_{suffix}"})

    if metrics.plug_in_action is None:
        keys.add("plug_in_action")
    if metrics.nfc_enabled is None:
        keys.add("nfc_enabled")

    return keys
