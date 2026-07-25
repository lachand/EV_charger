"""Actionable repair issues surfaced in Settings > Repairs.

A failed poll used to produce a single opaque "charger unreachable" message, but
the three causes need completely different responses from the user, and only one
of them is something the integration can fix on its own.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from .const import ADVANCED_ENTITY_KEYS, DOMAIN

ISSUE_CONNECTION_REFUSED = "connection_refused"
ISSUE_TIDY_ENTITIES = "tidy_entities"
ISSUE_LEGACY_DEVICE_ID = "legacy_device_id"
ISSUE_CLOUD_AUTH_FAILED = "cloud_auth_failed"


def _issue_id(entry_id: str, kind: str) -> str:
    return f"{kind}_{entry_id}"


def async_raise(
    hass: HomeAssistant,
    entry_id: str,
    kind: str,
    *,
    translation_placeholders: dict[str, str] | None = None,
) -> None:
    """Create (or refresh) a repair issue for this config entry."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(entry_id, kind),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=kind,
        translation_placeholders=translation_placeholders,
    )


def async_clear(hass: HomeAssistant, entry_id: str, kind: str) -> None:
    """Remove a repair issue once its cause is gone."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry_id, kind))


def async_sync_config_problems(
    hass: HomeAssistant,
    entry_id: str,
    problems: list[str],
) -> None:
    """Raise a repair per detected problem, and clear the ones now resolved.

    Cleared as well as raised, so fixing the setting makes the notice disappear
    on its own rather than lingering until a restart.
    """
    from .config_diagnosis import ConfigProblem

    active = set(problems)
    for problem in ConfigProblem:
        if problem.value in active:
            async_raise(hass, entry_id, problem.value)
        else:
            async_clear(hass, entry_id, problem.value)


def async_sync_session_anomalies(
    hass: HomeAssistant,
    entry_id: str,
    anomalies: list[str],
) -> None:
    """Raise a repair per detected charge anomaly, and clear the resolved ones.

    Cleared as well as raised, so a charger that recovers -- a reseated cable, a
    cooled connector -- drops its notice on the next healthy session rather than
    keeping a stale alarm.
    """
    from .session_anomaly import SessionAnomaly

    active = set(anomalies)
    for anomaly in SessionAnomaly:
        if anomaly.value in active:
            async_raise(hass, entry_id, anomaly.value)
        else:
            async_clear(hass, entry_id, anomaly.value)


def async_offer_entity_cleanup(hass: HomeAssistant, entry_id: str, count: int) -> None:
    """Offer to hide advanced entities an existing install already registered.

    Fixable rather than automatic: these entities do work, and Home Assistant
    cannot tell an entity the user deliberately kept from one that is merely
    enabled by default. Disabling them silently would break someone's dashboard,
    so the user confirms.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(entry_id, ISSUE_TIDY_ENTITIES),
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_TIDY_ENTITIES,
        translation_placeholders={"count": str(count)},
        data={"entry_id": entry_id},
    )


class TidyEntitiesFlow(RepairsFlow):
    """Disables the advanced entities, once the user has accepted."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            from .entity_cleanup import async_disable_entities

            disabled = await async_disable_entities(
                self.hass,
                self._entry_id,
                set(ADVANCED_ENTITY_KEYS),
                reason="are advanced or diagnostic",
            )
            return self.async_create_entry(title="", data={"disabled": disabled})

        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Home Assistant entry point for a fixable issue."""
    if issue_id.startswith(ISSUE_TIDY_ENTITIES) and data:
        return TidyEntitiesFlow(entry_id=str(data["entry_id"]))
    return ConfirmRepairFlow()
