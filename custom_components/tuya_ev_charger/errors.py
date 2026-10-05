"""User-facing errors, raised as translation keys instead of English sentences.

The wording lives in the ``exceptions`` section of ``strings.json`` so it is
translated like the rest of the UI. ``ServiceValidationError`` is for input the
caller got wrong; ``HomeAssistantError`` is for the integration or charger
failing to do something valid.
"""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import DOMAIN


def charger_error(key: str, **placeholders: object) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={name: str(value) for name, value in placeholders.items()},
    )


def validation_error(key: str, **placeholders: object) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={name: str(value) for name, value in placeholders.items()},
    )
