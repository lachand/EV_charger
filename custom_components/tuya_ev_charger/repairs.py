"""Actionable repair issues surfaced in Settings > Repairs.

A failed poll used to produce a single opaque "charger unreachable" message, but
the three causes need completely different responses from the user, and only one
of them is something the integration can fix on its own.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

ISSUE_CONNECTION_REFUSED = "connection_refused"
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
