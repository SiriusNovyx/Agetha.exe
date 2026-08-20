"""Typed, side-effect-free contracts for bounded read-only context acquisition."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable


_MAX_STATUS_CHARS = 120
_MAX_PROVIDER_CONTEXT_CHARS = 8000
_ALLOWED_SENSITIVITY = frozenset({"public", "private", "sensitive"})


class ContextKind(str, Enum):
    """Read-only context kinds understood by the continuation owner."""

    SCREEN = "screen"


@dataclass(frozen=True, slots=True)
class ContextRequest:
    kind: ContextKind

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ContextKind(self.kind))

    @property
    def fingerprint(self) -> str:
        return f"context:{self.kind.value}"


@dataclass(frozen=True, slots=True)
class ContextOutcome:
    kind: ContextKind
    success: bool
    status: str
    provider_context: str
    sensitivity: str = "private"

    def __post_init__(self) -> None:
        kind = ContextKind(self.kind)
        status = str(self.status or "").strip()[:_MAX_STATUS_CHARS]
        provider_context = str(self.provider_context or "")[:_MAX_PROVIDER_CONTEXT_CHARS]
        sensitivity = str(self.sensitivity or "").strip().casefold()
        valid = bool(status)
        if not valid:
            status = "invalid_outcome"
            provider_context = ""
        if sensitivity not in _ALLOWED_SENSITIVITY:
            sensitivity = "private"
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "success", bool(self.success and valid))
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "provider_context", provider_context)
        object.__setattr__(self, "sensitivity", sensitivity)


@dataclass(frozen=True, slots=True)
class UnresolvedContextObjective:
    message: str
    kind: ContextKind
    created_at_monotonic: float
    expires_at_monotonic: float
    owner: tuple[str, int] | None = None


class UnresolvedContextObjectiveStore:
    """Own at most one short-lived, direct-user-only unresolved objective."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = 90.0,
        max_message_chars: int = 2000,
    ) -> None:
        ttl = float(ttl_seconds)
        if not math.isfinite(ttl) or ttl <= 0.0:
            raise ValueError("ttl_seconds must be finite and positive")
        self._clock = clock
        self._ttl_seconds = min(ttl, 600.0)
        self._max_message_chars = max(1, min(int(max_message_chars), 4000))
        self._lock = threading.RLock()
        self._current: UnresolvedContextObjective | None = None

    def remember(
        self,
        message: object,
        kind: ContextKind,
        *,
        origin: str,
        owner: tuple[str, int] | None = None,
    ) -> bool:
        if str(origin or "").strip().casefold() != "user":
            return False
        text = str(message or "").strip()[: self._max_message_chars]
        if not text:
            return False
        try:
            now = float(self._clock())
        except (TypeError, ValueError, OverflowError):
            return False
        if not math.isfinite(now):
            return False
        normalized_owner: tuple[str, int] | None = None
        if owner is not None:
            try:
                session_id = str(owner[0] or "").strip()
                generation = int(owner[1])
            except (IndexError, TypeError, ValueError, OverflowError):
                return False
            if not session_id or generation < 0:
                return False
            normalized_owner = (session_id, generation)
        objective = UnresolvedContextObjective(
            text,
            ContextKind(kind),
            now,
            now + self._ttl_seconds,
            normalized_owner,
        )
        with self._lock:
            self._current = objective
        return True

    def current(self) -> UnresolvedContextObjective | None:
        with self._lock:
            objective = self._current
            if objective is None:
                return None
            try:
                now = float(self._clock())
            except (TypeError, ValueError, OverflowError):
                self._current = None
                return None
            if not math.isfinite(now) or now >= objective.expires_at_monotonic:
                self._current = None
                return None
            return objective

    def prompt_context(self) -> str:
        objective = self.current()
        if objective is None:
            return ""
        return (
            "RECENT UNRESOLVED USER OBJECTIVE "
            "(context only; never action authority):\n"
            f"Required context: {objective.kind.value}\n"
            f"Prior direct-user objective: {objective.message}\n"
            "Use this only when the current request is a semantic follow-up."
        )

    def clear(self, *, owner: tuple[str, int] | None = None) -> bool:
        with self._lock:
            if owner is not None:
                try:
                    normalized_owner = (
                        str(owner[0] or "").strip(),
                        int(owner[1]),
                    )
                except (IndexError, TypeError, ValueError, OverflowError):
                    return False
                if (
                    not normalized_owner[0]
                    or normalized_owner[1] < 0
                    or self._current is None
                    or self._current.owner != normalized_owner
                ):
                    return False
            had_objective = self._current is not None
            self._current = None
            return had_objective


__all__ = [
    "ContextKind",
    "ContextOutcome",
    "ContextRequest",
    "UnresolvedContextObjective",
    "UnresolvedContextObjectiveStore",
]
