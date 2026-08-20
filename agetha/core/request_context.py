"""Typed request origins for trusted application events and ordinary user text."""

from __future__ import annotations

from enum import Enum
from typing import Literal, get_args


RequestOrigin = Literal[
    "user",
    "touch",
    "file_drop",
    "reminder",
    "ambient",
    "tool_result",
    "terminal_sentinel",
]

REQUEST_ORIGINS = frozenset(get_args(RequestOrigin))
INTERNAL_ORIGINS = REQUEST_ORIGINS - {"user", "ambient"}


class AmbientRelevance(str, Enum):
    """Presentation metadata for conservative ambient responses."""

    MUNDANE = "mundane"
    INTERESTING = "interesting"
    IMPORTANT = "important"


def normalize_ambient_relevance(value: object) -> AmbientRelevance:
    """Fail closed without changing request origin or authority."""
    candidate = str(value or "").strip().casefold()
    try:
        return AmbientRelevance(candidate)
    except ValueError:
        return AmbientRelevance.MUNDANE


def normalize_request_origin(value: object, *, default: RequestOrigin = "user") -> RequestOrigin:
    """Return a known origin without granting authority to message text."""
    candidate = str(value or "").strip().lower()
    if candidate in REQUEST_ORIGINS:
        return candidate  # type: ignore[return-value]
    return default


def render_request_message(origin: RequestOrigin, text: str) -> str:
    """Label programmatic events while leaving ordinary user text untouched."""
    content = str(text or "").strip()
    if origin == "user":
        return content
    if origin == "ambient":
        return ""
    return f"[internal event: {origin}]\n{content}" if content else f"[internal event: {origin}]"


def request_profile_for_origin(origin: RequestOrigin) -> str:
    if origin == "tool_result":
        return "tool_continuation"
    if origin == "ambient":
        return "fast_ambient"
    if origin in INTERNAL_ORIGINS:
        return "fast_command"
    return "fast_user"
