"""Local rules for socially aware proactive UI behavior.

The rules consume already-known application state.  They do not monitor the
keyboard, capture the screen, call an AI provider, or manipulate Tk widgets.
The application remains responsible for gathering state and applying a
decision on the Tk owner thread.
"""

from __future__ import annotations

import math
import threading
import time as time_module
from collections import deque
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Callable


MAX_PRESENCE_QUEUE_SIZE = 1024
MAX_MESSAGE_CHARS = 1000
DEFAULT_QUEUE_TTL_SECONDS = 300.0


class PresenceUrgency(str, Enum):
    NONURGENT = "nonurgent"
    IMPORTANT = "important"
    DANGEROUS = "dangerous"


@dataclass(frozen=True)
class PresenceState:
    """Non-invasive state supplied by existing application components."""

    presentation_mode: bool = False
    fullscreen_active: bool = False
    active_game: bool = False
    rapid_typing: bool = False
    user_idle: bool = False
    user_recently_active: bool = False
    quiet_hours: bool | None = None
    media_playing: bool = False
    repeated_dismissals: bool = False
    agetha_minimized: bool = False
    agetha_sleeping: bool = False
    shutdown_in_progress: bool = False
    dangerous_condition: bool = False


@dataclass(frozen=True)
class PresenceDecision:
    allow_popup: bool
    allow_voice: bool
    allow_focus_request: bool
    allow_window_motion: bool
    queue_nonurgent: bool
    reason: str
    retry_after_seconds: float | None = None


@dataclass(frozen=True)
class QueuedPresenceMessage:
    """A bounded local summary waiting for a less intrusive moment."""

    message: str
    created_at: datetime
    expires_at: datetime
    urgency: PresenceUrgency = PresenceUrgency.NONURGENT
    dedup_key: str | None = None


@dataclass(frozen=True)
class _QueuedRecord:
    message: QueuedPresenceMessage
    expires_monotonic: float


def _bounded_delay(value: float | int, *, maximum: float = 86_400.0) -> float:
    delay = float(value)
    if not math.isfinite(delay):
        raise ValueError("delay must be finite")
    return min(maximum, max(0.0, delay))


def _parse_clock_time(value: str | int | time | None) -> time | None:
    if value is None or value == "":
        return None
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0, tzinfo=None)
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value <= 23:
            return time(hour=value)
        raise ValueError("quiet-hour integer must be from 0 through 23")
    raw = str(value).strip()
    pieces = raw.split(":")
    if len(pieces) not in (1, 2):
        raise ValueError("quiet-hour value must be HH or HH:MM")
    try:
        hour = int(pieces[0])
        minute = int(pieces[1]) if len(pieces) == 2 else 0
    except ValueError as exc:
        raise ValueError("quiet-hour value must be numeric") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("quiet-hour value is out of range")
    return time(hour=hour, minute=minute)


def _time_in_window(value: time, start: time | None, end: time | None) -> bool:
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= value < end
    return value >= start or value < end


def _seconds_until_window_end(now: datetime, end: time | None) -> float | None:
    if end is None:
        return None
    candidate = now.replace(
        hour=end.hour,
        minute=end.minute,
        second=0,
        microsecond=0,
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return max(0.0, (candidate - now).total_seconds())


def _decision(
    *,
    popup: bool,
    voice: bool,
    focus: bool,
    motion: bool,
    queue: bool,
    reason: str,
    retry: float | None = None,
) -> PresenceDecision:
    return PresenceDecision(
        allow_popup=popup,
        allow_voice=voice,
        allow_focus_request=focus,
        allow_window_motion=motion,
        queue_nonurgent=queue,
        reason=reason,
        retry_after_seconds=None if retry is None else max(0.0, float(retry)),
    )


def decide_presence(
    state: PresenceState,
    *,
    urgency: PresenceUrgency = PresenceUrgency.NONURGENT,
    fullscreen_silent: bool = True,
    quiet_hours_active: bool = False,
    rapid_typing_cooldown_active: bool = False,
    dismissal_cooldown_active: bool = False,
    rapid_retry_after_seconds: float | None = None,
    dismissal_retry_after_seconds: float | None = None,
    quiet_retry_after_seconds: float | None = None,
) -> PresenceDecision:
    """Pure precedence-ordered etiquette decision.

    A dangerous condition may bypass ordinary backoff so a calm visual warning
    can appear.  It still never steals focus, moves the window, or speaks over
    presentation/fullscreen state.  Shutdown remains absolute.
    """

    if not isinstance(state, PresenceState):
        raise TypeError("state must be a PresenceState")
    urgency = PresenceUrgency(urgency)
    dangerous = state.dangerous_condition or urgency is PresenceUrgency.DANGEROUS

    if state.shutdown_in_progress:
        return _decision(
            popup=False,
            voice=False,
            focus=False,
            motion=False,
            queue=False,
            reason="shutdown in progress",
        )

    if state.presentation_mode:
        return _decision(
            popup=False,
            voice=False,
            focus=False,
            motion=False,
            queue=not dangerous,
            reason="presentation mode",
        )

    if state.active_game or (state.fullscreen_active and fullscreen_silent):
        return _decision(
            popup=False,
            voice=False,
            focus=False,
            motion=False,
            queue=not dangerous,
            reason="active game" if state.active_game else "fullscreen active",
        )

    if dangerous:
        return _decision(
            popup=True,
            voice=False,
            focus=False,
            motion=False,
            queue=False,
            reason="calm dangerous-condition warning",
        )

    if state.rapid_typing or rapid_typing_cooldown_active:
        return _decision(
            popup=False,
            voice=False,
            focus=False,
            motion=False,
            queue=True,
            reason="rapid typing cooldown",
            retry=rapid_retry_after_seconds,
        )

    if state.repeated_dismissals or dismissal_cooldown_active:
        return _decision(
            popup=False,
            voice=False,
            focus=False,
            motion=False,
            queue=True,
            reason="dismissal cooldown",
            retry=dismissal_retry_after_seconds,
        )

    if state.agetha_sleeping:
        return _decision(
            popup=False,
            voice=False,
            focus=False,
            motion=False,
            queue=True,
            reason="Agetha is sleeping",
        )

    if state.agetha_minimized:
        return _decision(
            popup=False,
            voice=False,
            focus=False,
            motion=False,
            queue=True,
            reason="Agetha is minimized",
        )

    if quiet_hours_active:
        return _decision(
            popup=True,
            voice=False,
            focus=False,
            motion=False,
            queue=False,
            reason="quiet hours",
            retry=quiet_retry_after_seconds,
        )

    if state.media_playing:
        return _decision(
            popup=True,
            voice=False,
            focus=False,
            motion=False,
            queue=False,
            reason="media playback",
        )

    if state.fullscreen_active:
        return _decision(
            popup=True,
            voice=False,
            focus=False,
            motion=False,
            queue=False,
            reason="fullscreen motion suppression",
        )

    if state.user_recently_active:
        return _decision(
            popup=True,
            voice=False,
            focus=False,
            motion=False,
            queue=False,
            reason="user recently became active",
        )

    if state.user_idle:
        return _decision(
            popup=True,
            voice=True,
            focus=False,
            motion=True,
            queue=False,
            reason="user idle",
        )

    return _decision(
        popup=True,
        voice=True,
        focus=False,
        motion=True,
        queue=False,
        reason="normal presence",
    )


class PresenceEtiquette:
    """Stateful cooldown and bounded-queue owner around the pure rules."""

    def __init__(
        self,
        *,
        quiet_hours_start: str | int | time | None = None,
        quiet_hours_end: str | int | time | None = None,
        dismiss_cooldown_seconds: float = 900.0,
        rapid_typing_cooldown_seconds: float = 30.0,
        fullscreen_silent: bool = True,
        max_queue_size: int = 64,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if not 1 <= int(max_queue_size) <= MAX_PRESENCE_QUEUE_SIZE:
            raise ValueError(
                f"max_queue_size must be between 1 and {MAX_PRESENCE_QUEUE_SIZE}"
            )
        self._quiet_start = _parse_clock_time(quiet_hours_start)
        self._quiet_end = _parse_clock_time(quiet_hours_end)
        if (self._quiet_start is None) != (self._quiet_end is None):
            raise ValueError("quiet-hours start and end must both be configured")
        self._dismiss_cooldown = _bounded_delay(dismiss_cooldown_seconds)
        self._rapid_cooldown = _bounded_delay(rapid_typing_cooldown_seconds)
        self._fullscreen_silent = bool(fullscreen_silent)
        self._max_queue_size = int(max_queue_size)
        self._clock = clock or datetime.now
        self._monotonic = monotonic or time_module.monotonic
        self._lock = threading.RLock()
        self._queue: deque[_QueuedRecord] = deque()
        self._queued_keys: set[str] = set()
        self._dismissals: deque[float] = deque()
        self._dismiss_until = 0.0
        self._rapid_until = 0.0
        self._shutdown = False

    @property
    def is_shutdown(self) -> bool:
        with self._lock:
            return self._shutdown

    def _monotonic_now(self) -> float:
        value = float(self._monotonic())
        if not math.isfinite(value):
            raise ValueError("monotonic clock returned a non-finite value")
        return value

    def _wall_now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise TypeError("clock must return a datetime")
        return value

    def quiet_hours_active(self, now: datetime | None = None) -> bool:
        current = now or self._wall_now()
        return _time_in_window(current.time(), self._quiet_start, self._quiet_end)

    def _cooldown_remaining(self, until: float, now: float) -> float | None:
        return max(0.0, until - now) if until > now else None

    def record_dismissal(self) -> bool:
        """Record a user dismissal; two within the window activate backoff."""

        with self._lock:
            if self._shutdown:
                return False
            now = self._monotonic_now()
            cutoff = now - self._dismiss_cooldown
            while self._dismissals and self._dismissals[0] <= cutoff:
                self._dismissals.popleft()
            self._dismissals.append(now)
            while len(self._dismissals) > 2:
                self._dismissals.popleft()
            if len(self._dismissals) >= 2:
                self._dismiss_until = now + self._dismiss_cooldown
                return True
            return False

    note_dismissal = record_dismissal

    def reset_dismissals(self) -> None:
        with self._lock:
            self._dismissals.clear()
            self._dismiss_until = 0.0

    def note_rapid_typing(self) -> None:
        with self._lock:
            if not self._shutdown:
                self._rapid_until = max(
                    self._rapid_until,
                    self._monotonic_now() + self._rapid_cooldown,
                )

    def decide(
        self,
        state: PresenceState,
        *,
        urgency: PresenceUrgency = PresenceUrgency.NONURGENT,
    ) -> PresenceDecision:
        with self._lock:
            if self._shutdown:
                return decide_presence(PresenceState(shutdown_in_progress=True))
            monotonic_now = self._monotonic_now()
            if state.rapid_typing and not self._shutdown:
                self._rapid_until = max(
                    self._rapid_until,
                    monotonic_now + self._rapid_cooldown,
                )
            rapid_remaining = self._cooldown_remaining(
                self._rapid_until, monotonic_now
            )
            dismiss_remaining = self._cooldown_remaining(
                self._dismiss_until, monotonic_now
            )
            wall_now = self._wall_now()
            quiet = (
                bool(state.quiet_hours)
                if state.quiet_hours is not None
                else self.quiet_hours_active(wall_now)
            )
            quiet_retry = (
                _seconds_until_window_end(wall_now, self._quiet_end)
                if quiet
                else None
            )
            return decide_presence(
                state,
                urgency=urgency,
                fullscreen_silent=self._fullscreen_silent,
                quiet_hours_active=quiet,
                rapid_typing_cooldown_active=rapid_remaining is not None,
                dismissal_cooldown_active=dismiss_remaining is not None,
                rapid_retry_after_seconds=rapid_remaining,
                dismissal_retry_after_seconds=dismiss_remaining,
                quiet_retry_after_seconds=quiet_retry,
            )

    @staticmethod
    def _bounded_message(value: object) -> str:
        text = str(value)
        if not text:
            raise ValueError("message must not be empty")
        if len(text) > MAX_MESSAGE_CHARS:
            raise ValueError(f"message exceeds {MAX_MESSAGE_CHARS} characters")
        return text

    @staticmethod
    def _bounded_key(value: object | None) -> str | None:
        if value is None:
            return None
        key = str(value)
        if not key or "\n" in key or "\r" in key or len(key) > 160:
            raise ValueError("dedup_key must be a non-empty bounded line")
        return key

    def _purge_expired_locked(self, monotonic_now: float) -> None:
        if not self._queue:
            return
        kept: deque[_QueuedRecord] = deque()
        keys: set[str] = set()
        for record in self._queue:
            if record.expires_monotonic <= monotonic_now:
                continue
            kept.append(record)
            if record.message.dedup_key is not None:
                keys.add(record.message.dedup_key)
        self._queue = kept
        self._queued_keys = keys

    def queue_message(
        self,
        message: object,
        *,
        ttl_seconds: float = DEFAULT_QUEUE_TTL_SECONDS,
        urgency: PresenceUrgency = PresenceUrgency.NONURGENT,
        dedup_key: object | None = None,
    ) -> bool:
        """Queue local UI text only; this method has no provider side effect."""

        text = self._bounded_message(message)
        ttl = _bounded_delay(ttl_seconds)
        if ttl <= 0.0:
            return False
        urgency = PresenceUrgency(urgency)
        key = self._bounded_key(dedup_key)
        with self._lock:
            if self._shutdown:
                return False
            monotonic_now = self._monotonic_now()
            self._purge_expired_locked(monotonic_now)
            if key is not None and key in self._queued_keys:
                return False
            if len(self._queue) >= self._max_queue_size:
                removed = self._queue.popleft()
                if removed.message.dedup_key is not None:
                    self._queued_keys.discard(removed.message.dedup_key)
            wall_now = self._wall_now()
            queued = QueuedPresenceMessage(
                message=text,
                created_at=wall_now,
                expires_at=wall_now + timedelta(seconds=ttl),
                urgency=urgency,
                dedup_key=key,
            )
            self._queue.append(
                _QueuedRecord(queued, monotonic_now + ttl)
            )
            if key is not None:
                self._queued_keys.add(key)
            return True

    def pending_messages(self) -> tuple[QueuedPresenceMessage, ...]:
        with self._lock:
            if self._shutdown:
                return ()
            self._purge_expired_locked(self._monotonic_now())
            return tuple(record.message for record in self._queue)

    def drain_ready(
        self,
        state: PresenceState,
        *,
        limit: int | None = None,
    ) -> tuple[QueuedPresenceMessage, ...]:
        """Drain only when current etiquette allows a nonurgent popup."""

        with self._lock:
            if self._shutdown or state.shutdown_in_progress:
                self._queue.clear()
                self._queued_keys.clear()
                return ()
            self._purge_expired_locked(self._monotonic_now())
            decision = self.decide(state, urgency=PresenceUrgency.NONURGENT)
            if decision.queue_nonurgent or not decision.allow_popup:
                return ()
            if limit is None:
                count = len(self._queue)
            else:
                count = int(limit)
                if count < 0:
                    raise ValueError("limit must not be negative")
                count = min(count, len(self._queue))
            drained: list[QueuedPresenceMessage] = []
            for _ in range(count):
                record = self._queue.popleft()
                if record.message.dedup_key is not None:
                    self._queued_keys.discard(record.message.dedup_key)
                drained.append(record.message)
            return tuple(drained)

    def clear_queue(self) -> None:
        with self._lock:
            self._queue.clear()
            self._queued_keys.clear()

    def shutdown(self) -> None:
        """Idempotently drop nonessential reactions and queued content."""

        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            self._queue.clear()
            self._queued_keys.clear()
            self._dismissals.clear()
            self._dismiss_until = 0.0
            self._rapid_until = 0.0


__all__ = [
    "PresenceDecision",
    "PresenceEtiquette",
    "PresenceState",
    "PresenceUrgency",
    "QueuedPresenceMessage",
    "decide_presence",
]
