"""Small, local-only foundation for typed proactive observations.

Publishing an :class:`Observation` only records a bounded local fact.  It does
not call an AI provider, write memory, show UI, or authorize a command.  Those
choices deliberately remain separate and must be made by their owning layer.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Callable, Mapping

from agetha.platform.screen_monitoring import redact_sensitive_text


MAX_QUEUE_SIZE = 4096
MAX_SUMMARY_CHARS = 512
MAX_SOURCE_CHARS = 80
MAX_DEDUP_KEY_CHARS = 160
MAX_METADATA_ITEMS = 32
MAX_METADATA_KEY_CHARS = 80
MAX_METADATA_TEXT_CHARS = 512

_FORBIDDEN_METADATA_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "document_content",
    "full_document",
    "password",
    "provider_token",
    "raw_ocr",
    "secret",
)


class ObservationKind(str, Enum):
    """Locally observable event categories shared by proactive features."""

    USER_BECAME_ACTIVE = "user_became_active"
    USER_BECAME_IDLE = "user_became_idle"
    APP_FOCUSED = "app_focused"
    APP_UNFOCUSED = "app_unfocused"
    ERROR_PATTERN_DETECTED = "error_pattern_detected"
    TASK_DUE = "task_due"
    BATTERY_LOW = "battery_low"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    FILE_DROPPED = "file_dropped"
    PRESENTATION_MODE = "presentation_mode"
    FULLSCREEN_ACTIVE = "fullscreen_active"
    RAPID_TYPING = "rapid_typing"


class Sensitivity(str, Enum):
    """Coarse privacy label; payload contents must still be minimized."""

    PUBLIC = "public"
    INTERNAL = "internal"
    PRIVATE = "private"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


class ObservationUse(str, Enum):
    """Downstream uses kept distinct from publication itself."""

    LOCAL_REACTION = "local_reaction"
    NOTIFICATION = "notification"
    PROVIDER_CONTEXT = "provider_context"
    MEMORY = "memory"
    GUARDED_ACTION = "guarded_action"


class PublishStatus(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    EXPIRED = "expired"
    SHUTDOWN = "shutdown"


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _one_line(value: object, *, field_name: str, maximum: int, empty: bool = False) -> str:
    text = str(value)
    if not empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    if "\n" in text or "\r" in text:
        raise ValueError(f"{field_name} must be one line")
    if len(text) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} characters")
    return text


def _freeze_metadata_value(value: object, *, depth: int = 0) -> object:
    """Copy common containers into immutable equivalents.

    Metadata is intentionally small and structured.  Rejecting arbitrary
    mutable objects prevents a frozen Observation from changing underneath a
    consumer after publication.
    """

    if depth > 4:
        raise ValueError("metadata nesting is too deep")
    if value is None or isinstance(value, (bool, int, datetime, Enum)):
        return value
    if isinstance(value, bytes):
        if len(value) > MAX_METADATA_TEXT_CHARS:
            raise ValueError(
                f"metadata bytes exceed {MAX_METADATA_TEXT_CHARS} bytes"
            )
        decoded = value.decode("utf-8", errors="replace")
        redacted = redact_sensitive_text(decoded)
        return redacted.encode("utf-8") if redacted != decoded else value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("metadata numbers must be finite")
        return value
    if isinstance(value, str):
        if len(value) > MAX_METADATA_TEXT_CHARS:
            raise ValueError(
                f"metadata text exceeds {MAX_METADATA_TEXT_CHARS} characters"
            )
        return redact_sensitive_text(value)
    if isinstance(value, Mapping):
        if len(value) > MAX_METADATA_ITEMS:
            raise ValueError("metadata mapping has too many items")
        copied: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            key = _validated_metadata_key(raw_key)
            copied[key] = _freeze_metadata_value(raw_value, depth=depth + 1)
        return MappingProxyType(copied)
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_METADATA_ITEMS:
            raise ValueError("metadata sequence has too many items")
        return tuple(_freeze_metadata_value(item, depth=depth + 1) for item in value)
    if isinstance(value, (set, frozenset)):
        if len(value) > MAX_METADATA_ITEMS:
            raise ValueError("metadata set has too many items")
        return frozenset(
            _freeze_metadata_value(item, depth=depth + 1) for item in value
        )
    raise TypeError(f"unsupported metadata value type: {type(value).__name__}")


def _validated_metadata_key(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("metadata keys must be strings")
    key = _one_line(
        value,
        field_name="metadata key",
        maximum=MAX_METADATA_KEY_CHARS,
    )
    lowered = key.casefold()
    if any(part in lowered for part in _FORBIDDEN_METADATA_KEY_PARTS):
        raise ValueError("metadata contains a forbidden sensitive field")
    return key


def _freeze_metadata(metadata: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(metadata, Mapping):
        raise TypeError("metadata must be a mapping")
    if len(metadata) > MAX_METADATA_ITEMS:
        raise ValueError("metadata has too many items")
    safe: dict[str, object] = {}
    for raw_key, value in metadata.items():
        key = _validated_metadata_key(raw_key)
        safe[key] = _freeze_metadata_value(value)
    return MappingProxyType(safe)


@dataclass(frozen=True)
class Observation:
    """Immutable, minimized fact produced by a local observer."""

    kind: ObservationKind
    source: str
    summary: str
    confidence: float
    sensitivity: Sensitivity
    created_at: datetime
    expires_at: datetime | None = None
    local_only: bool = True
    dedup_key: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    request_origin: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ObservationKind):
            object.__setattr__(self, "kind", ObservationKind(self.kind))
        if not isinstance(self.sensitivity, Sensitivity):
            object.__setattr__(self, "sensitivity", Sensitivity(self.sensitivity))
        object.__setattr__(
            self,
            "source",
            _one_line(
                redact_sensitive_text(self.source),
                field_name="source",
                maximum=MAX_SOURCE_CHARS,
            ),
        )
        object.__setattr__(
            self,
            "summary",
            _one_line(
                redact_sensitive_text(self.summary),
                field_name="summary",
                maximum=MAX_SUMMARY_CHARS,
            ),
        )
        try:
            confidence = float(self.confidence)
        except (TypeError, ValueError) as exc:
            raise TypeError("confidence must be numeric") from exc
        if not math.isfinite(confidence):
            confidence = 0.0
        object.__setattr__(self, "confidence", min(1.0, max(0.0, confidence)))
        created = _aware_utc(self.created_at, field_name="created_at")
        object.__setattr__(self, "created_at", created)
        if self.expires_at is not None:
            object.__setattr__(
                self,
                "expires_at",
                _aware_utc(self.expires_at, field_name="expires_at"),
            )
        object.__setattr__(self, "local_only", bool(self.local_only))
        if self.dedup_key is not None:
            object.__setattr__(
                self,
                "dedup_key",
                _one_line(
                    redact_sensitive_text(self.dedup_key),
                    field_name="dedup_key",
                    maximum=MAX_DEDUP_KEY_CHARS,
                ),
            )
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))
        if self.request_origin is not None:
            object.__setattr__(
                self,
                "request_origin",
                _one_line(
                    self.request_origin,
                    field_name="request_origin",
                    maximum=40,
                ),
            )

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at is not None and self.expires_at <= _aware_utc(
            now, field_name="now"
        )


@dataclass(frozen=True)
class PublishResult:
    accepted: bool
    status: PublishStatus
    queue_size: int
    dropped_oldest: bool = False

    def __bool__(self) -> bool:
        return self.accepted


@dataclass(frozen=True)
class ObservationEligibility:
    """Local policy result only; it performs none of the represented uses."""

    local_reaction: bool
    notification: bool
    provider_context: bool
    memory: bool
    guarded_action: bool
    reason: str

    def allows(self, use: ObservationUse) -> bool:
        return {
            ObservationUse.LOCAL_REACTION: self.local_reaction,
            ObservationUse.NOTIFICATION: self.notification,
            ObservationUse.PROVIDER_CONTEXT: self.provider_context,
            ObservationUse.MEMORY: self.memory,
            ObservationUse.GUARDED_ACTION: self.guarded_action,
        }[ObservationUse(use)]


def eligibility_for(
    observation: Observation,
    *,
    now: datetime | None = None,
    provider_authorized: bool = False,
    memory_authorized: bool = False,
) -> ObservationEligibility:
    """Return downstream eligibility without causing any downstream action.

    Provider and memory use remain false unless a separate owning workflow has
    explicitly authorized that use.  A guarded action is *always* false here:
    an observation can inform a later user request, but can never authorize an
    OS command by itself.
    """

    if now is not None and observation.is_expired(now):
        return ObservationEligibility(False, False, False, False, False, "expired")

    sensitivity = observation.sensitivity
    highly_sensitive = sensitivity in {Sensitivity.SENSITIVE, Sensitivity.RESTRICTED}
    provider_safe = sensitivity in {Sensitivity.PUBLIC, Sensitivity.INTERNAL}
    memory_safe = sensitivity is Sensitivity.PUBLIC
    return ObservationEligibility(
        local_reaction=True,
        notification=not highly_sensitive,
        provider_context=(
            bool(provider_authorized)
            and not observation.local_only
            and provider_safe
        ),
        memory=bool(memory_authorized) and memory_safe,
        guarded_action=False,
        reason="separate authorization required",
    )


class ObservationBus:
    """Application-owned, bounded, thread-safe FIFO observation queue."""

    def __init__(
        self,
        *,
        max_size: int = 128,
        dedup_window_seconds: float = 30.0,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if not 1 <= int(max_size) <= MAX_QUEUE_SIZE:
            raise ValueError(f"max_size must be between 1 and {MAX_QUEUE_SIZE}")
        if not math.isfinite(float(dedup_window_seconds)):
            raise ValueError("dedup_window_seconds must be finite")
        self._max_size = int(max_size)
        self._dedup_window_seconds = max(0.0, float(dedup_window_seconds))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._queue: deque[Observation] = deque()
        self._dedup_until: dict[str, float] = {}
        self._lock = threading.RLock()
        self._shutdown = False

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def is_shutdown(self) -> bool:
        with self._lock:
            return self._shutdown

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired_locked()
            return len(self._queue)

    def _now(self) -> datetime:
        return _aware_utc(self._clock(), field_name="clock result")

    def _dedup_identity(self, observation: Observation) -> str | None:
        if observation.dedup_key is None:
            return None
        return "\0".join(
            (observation.kind.value, observation.source, observation.dedup_key)
        )

    def _purge_dedup_locked(self, monotonic_now: float) -> None:
        expired = [
            key for key, until in self._dedup_until.items() if until <= monotonic_now
        ]
        for key in expired:
            del self._dedup_until[key]

    def _purge_expired_locked(self) -> None:
        if self._queue:
            now = self._now()
            self._queue = deque(item for item in self._queue if not item.is_expired(now))
        if self._dedup_until:
            self._purge_dedup_locked(float(self._monotonic()))

    def publish(self, observation: Observation) -> PublishResult:
        if not isinstance(observation, Observation):
            raise TypeError("publish expects an Observation")
        with self._lock:
            if self._shutdown:
                return PublishResult(False, PublishStatus.SHUTDOWN, 0)
            self._purge_expired_locked()
            if observation.is_expired(self._now()):
                return PublishResult(False, PublishStatus.EXPIRED, len(self._queue))

            monotonic_now = float(self._monotonic())
            identity = self._dedup_identity(observation)
            if identity is not None and self._dedup_until.get(identity, -math.inf) > monotonic_now:
                return PublishResult(False, PublishStatus.DUPLICATE, len(self._queue))

            dropped_oldest = len(self._queue) >= self._max_size
            if dropped_oldest:
                self._queue.popleft()
            self._queue.append(observation)
            if identity is not None and self._dedup_window_seconds > 0.0:
                if (
                    identity not in self._dedup_until
                    and len(self._dedup_until) >= self._max_size
                ):
                    oldest = min(self._dedup_until, key=self._dedup_until.__getitem__)
                    del self._dedup_until[oldest]
                self._dedup_until[identity] = monotonic_now + self._dedup_window_seconds
            return PublishResult(
                True,
                PublishStatus.ACCEPTED,
                len(self._queue),
                dropped_oldest,
            )

    @staticmethod
    def _validated_limit(limit: int | None, available: int) -> int:
        if limit is None:
            return available
        count = int(limit)
        if count < 0:
            raise ValueError("limit must not be negative")
        return min(count, available)

    def peek(self, limit: int | None = None) -> tuple[Observation, ...]:
        with self._lock:
            self._purge_expired_locked()
            count = self._validated_limit(limit, len(self._queue))
            return tuple(list(self._queue)[:count])

    def drain(self, limit: int | None = None) -> tuple[Observation, ...]:
        with self._lock:
            self._purge_expired_locked()
            count = self._validated_limit(limit, len(self._queue))
            return tuple(self._queue.popleft() for _ in range(count))

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()
            self._dedup_until.clear()

    def shutdown(self) -> None:
        """Idempotently reject future publications and release queued data."""

        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            self._queue.clear()
            self._dedup_until.clear()


__all__ = [
    "Observation",
    "ObservationBus",
    "ObservationEligibility",
    "ObservationKind",
    "ObservationUse",
    "PublishResult",
    "PublishStatus",
    "Sensitivity",
    "eligibility_for",
]
