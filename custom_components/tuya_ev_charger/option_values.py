"""Reading loosely typed values: stored options and raw charger payloads.

Options come back from storage as whatever the form or a script last wrote, and a
charger reports the same fact as a bool, an int or a string depending on the
firmware. These helpers turn either into the type the caller needs and never
raise, so one malformed value cannot take down a poll or a form.

Only readers that every module agreed on live here. The ones that remain local
(`_option_choice` in the options form, `_option_installation_phases` in the surplus
settings) have a single user and a rule of their own.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_TRUE_WORDS = frozenset({"1", "true", "on", "yes"})
_FALSE_WORDS = frozenset({"0", "false", "off", "no"})


def option_int(
    options: Mapping[str, Any],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """An integer option clamped to [minimum, maximum]; ``default`` if unreadable."""
    try:
        value = int(options.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def option_bool(options: Mapping[str, Any], key: str, default: bool) -> bool:
    """A boolean option, accepting the usual spellings of yes and no.

    Anything else falls back to Python truthiness, so an unrecognised string is
    true. That is long-standing behaviour, kept as it was.
    """
    value = options.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_WORDS:
            return True
        if lowered in _FALSE_WORDS:
            return False
    return bool(value)


def option_float(options: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    """A float option, sign preserved; ``default`` if absent or unreadable.

    Deliberately not clamped: a tariff price can be negative (paid to consume at
    certain hours), and a clamp here once turned a stored negative price into 0.
    """
    try:
        return float(options.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def option_text(options: Mapping[str, Any], key: str, default: str) -> str:
    """A text option with surrounding whitespace removed; ``default`` if None."""
    value = options.get(key, default)
    if value is None:
        return default
    return str(value).strip()


def clean_optional_text(value: Any) -> str:
    """Text with blanks and the literal "none" read as unset: always a string.

    An entity picker that was cleared can come back as ``None``, as an empty
    string, or as the string "None" depending on how the form was submitted; all
    of them mean "no entity". Callers that want ``None`` for unset use
    ``clean_optional_text(...) or None``.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "none":
        return ""
    return text


def coerce_optional_int(value: Any) -> int | None:
    """``value`` as an int, or None when it is not one."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def coerce_optional_bool(value: Any) -> bool | None:
    """``value`` as a bool, or None when it does not clearly say yes or no.

    Narrower than ``option_bool`` on purpose: this reads a sensor or a DP, where
    "unknown" must stay distinguishable from "off", and it does not accept "yes".
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "on"}:
            return True
        if lowered in {"false", "0", "off"}:
            return False
    return None
