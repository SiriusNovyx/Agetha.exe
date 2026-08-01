"""Provider-bound privacy policy shared by OCR, files, and tool results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agetha.platform.screen_monitoring import redact_sensitive_text


@dataclass(frozen=True)
class PreparedExternalContext:
    text: str
    allowed: bool
    redacted: bool
    source: str
    reason: str = ""


def prepare_external_context(
    value: object,
    *,
    source: str,
    max_chars: int = 4000,
    allowed: bool = True,
    reason: str = "",
    redactor: Callable[[str], str] | None = None,
) -> PreparedExternalContext:
    """Redact and bound untrusted data before any provider can receive it."""
    raw = str(value or "")
    if not allowed:
        return PreparedExternalContext("", False, bool(raw), source, reason or "withheld")

    safe = redact_sensitive_text(raw)
    if redactor is not None:
        try:
            safe = redactor(safe)
        except Exception:
            return PreparedExternalContext("", False, bool(raw), source, "redaction_failed")

    limit = max(0, min(int(max_chars), 50_000))
    if len(safe) > limit:
        safe = safe[: max(0, limit - 1)] + ("…" if limit else "")
    return PreparedExternalContext(
        safe,
        True,
        safe != raw,
        source,
        reason,
    )
