"""Narrow, dependency-injected Notepad bootstrap for Full Mode consent.

This module has no provider, planner, Computer Use, web, OCR, clipboard, shell,
or Python-helper path.  It can request exactly one fixed executable and can
send exactly one compiled warning after a fresh strict target validation.
Platform integration supplies the effect adapters; tests use inert fakes.
"""

from __future__ import annotations

import math
import ntpath
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, TypeAlias


NOTEPAD_COMMAND = ("notepad.exe",)
CONSENT_DEMO_MESSAGE = (
    "ARE YOU REALLY SURE YOU WANT TO CONTINUE THIS?\n\n"
    "Agetha Full Mode enables advanced OS integration.\n\n"
    "Safety restrictions will remain enabled.\n\n"
    "Return to Agetha to make the final decision."
)
DEFAULT_CONSENT_DEMO_TIMEOUT_SECONDS = 5.0
MAX_CONSENT_DEMO_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class ConsentDemoProcess:
    pid: int
    process_name: str
    created_at: float


@dataclass(frozen=True, slots=True)
class ConsentDemoTarget:
    pid: int
    process_name: str
    created_at: float
    hwnd: int
    bounds: tuple[int, int, int, int]
    foreground_hwnd: int | None
    process_alive: bool
    window_valid: bool


class ConsentDemoStatus(str, Enum):
    TYPED = "typed"
    LAUNCH_FAILED = "launch_failed"
    TARGET_UNAVAILABLE = "target_unavailable"
    TARGET_CHANGED = "target_changed"
    PROCESS_EXITED = "process_exited"
    CANCELLED = "cancelled"
    SHUTDOWN = "shutdown"
    TIMED_OUT = "timed_out"
    TYPE_FAILED = "type_failed"


@dataclass(frozen=True, slots=True)
class ConsentDemoResult:
    status: ConsentDemoStatus
    safe_reason: str

    @property
    def typed(self) -> bool:
        return self.status is ConsentDemoStatus.TYPED


AbortCheck: TypeAlias = Callable[[], bool]
LaunchAdapter: TypeAlias = Callable[
    [tuple[str, ...]], ConsentDemoProcess | None
]
TargetWaitAdapter: TypeAlias = Callable[
    [ConsentDemoProcess, float, AbortCheck], ConsentDemoTarget | None
]
TargetValidator: TypeAlias = Callable[
    [ConsentDemoTarget], ConsentDemoTarget | None
]
StaticTyper: TypeAlias = Callable[[ConsentDemoTarget], bool]
StaticTextSender: TypeAlias = Callable[[ConsentDemoTarget, str], bool]
Clock: TypeAlias = Callable[[], float]


class FixedConsentTyper:
    """Expose a no-text effect API while keeping the built-in warning local."""

    def __init__(
        self,
        *,
        send_static: StaticTextSender,
        authorized: Callable[[ConsentDemoTarget], bool],
    ) -> None:
        self._send_static = send_static
        self._authorized = authorized

    def __call__(self, target: ConsentDemoTarget) -> bool:
        try:
            if self._authorized(target) is not True:
                return False
            return self._send_static(target, CONSENT_DEMO_MESSAGE) is True
        except Exception:
            return False


class FullModeConsentDemo:
    """Run the fixed, bounded consent presentation without authorizing Full."""

    def __init__(
        self,
        *,
        launcher: LaunchAdapter,
        target_wait: TargetWaitAdapter,
        validator: TargetValidator,
        type_static: StaticTyper,
        cancel_requested: AbortCheck,
        shutdown_requested: AbortCheck,
        clock: Clock = time.monotonic,
        timeout_seconds: float = DEFAULT_CONSENT_DEMO_TIMEOUT_SECONDS,
    ) -> None:
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0.0:
            raise ValueError("consent demo timeout must be finite and positive")
        self._launcher = launcher
        self._target_wait = target_wait
        self._validator = validator
        self._type_static = type_static
        self._cancel_requested = cancel_requested
        self._shutdown_requested = shutdown_requested
        self._clock = clock
        self._timeout_seconds = min(
            timeout,
            MAX_CONSENT_DEMO_TIMEOUT_SECONDS,
        )

    def run_full_mode_consent_demo(self) -> ConsentDemoResult:
        stopped = self._stop_result()
        if stopped is not None:
            return stopped

        started = self._read_clock()
        if started is None:
            return self._result(
                ConsentDemoStatus.TIMED_OUT,
                "Consent demo clock was unavailable.",
            )
        deadline = started + self._timeout_seconds

        try:
            launched = self._launcher(NOTEPAD_COMMAND)
        except Exception:
            launched = None
        if launched is None:
            return self._result(
                ConsentDemoStatus.LAUNCH_FAILED,
                "Notepad could not be launched.",
            )
        if not _valid_process(launched):
            return self._result(
                ConsentDemoStatus.TARGET_CHANGED,
                "The launched process was not the fixed Notepad target.",
            )

        stopped = self._stop_result()
        if stopped is not None:
            return stopped
        remaining = self._remaining(deadline)
        if remaining is None or remaining <= 0.0:
            return self._timed_out()

        def should_abort() -> bool:
            return self._stop_result() is not None or self._deadline_passed(deadline)

        try:
            expected = self._target_wait(launched, remaining, should_abort)
        except Exception:
            expected = None

        stopped = self._stop_result()
        if stopped is not None:
            return stopped
        if self._deadline_passed(deadline):
            return self._timed_out()
        if expected is None:
            return self._result(
                ConsentDemoStatus.TARGET_UNAVAILABLE,
                "The Notepad window could not be identified.",
            )
        invalid = _target_problem(expected, launched)
        if invalid is not None:
            return invalid

        stopped = self._stop_result()
        if stopped is not None:
            return stopped
        try:
            live = self._validator(expected)
        except Exception:
            live = None

        stopped = self._stop_result()
        if stopped is not None:
            return stopped
        if self._deadline_passed(deadline):
            return self._timed_out()
        if live is None:
            return self._result(
                ConsentDemoStatus.TARGET_CHANGED,
                "The Notepad target could not be revalidated.",
            )
        invalid = _target_problem(live, launched)
        if invalid is not None:
            return invalid
        if not _same_locked_target(expected, live):
            return self._result(
                ConsentDemoStatus.TARGET_CHANGED,
                "The Notepad target changed before consent typing.",
            )

        # This is the immediate effect boundary.  The cancel predicate should
        # include the consent-flow generation/state check owned by the caller.
        stopped = self._stop_result()
        if stopped is not None:
            return stopped
        if self._deadline_passed(deadline):
            return self._timed_out()
        try:
            typed = self._type_static(live)
        except Exception:
            typed = False
        if typed is not True:
            return self._result(
                ConsentDemoStatus.TYPE_FAILED,
                "The fixed consent warning could not be typed.",
            )
        return self._result(
            ConsentDemoStatus.TYPED,
            "The fixed consent warning was typed into validated Notepad.",
        )

    def _stop_result(self) -> ConsentDemoResult | None:
        if _safe_check(self._shutdown_requested):
            return self._result(
                ConsentDemoStatus.SHUTDOWN,
                "Consent demo stopped during shutdown.",
            )
        if _safe_check(self._cancel_requested):
            return self._result(
                ConsentDemoStatus.CANCELLED,
                "Consent demo was cancelled.",
            )
        return None

    def _read_clock(self) -> float | None:
        try:
            value = float(self._clock())
        except Exception:
            return None
        return value if math.isfinite(value) else None

    def _remaining(self, deadline: float) -> float | None:
        now = self._read_clock()
        if now is None:
            return None
        return max(0.0, min(self._timeout_seconds, deadline - now))

    def _deadline_passed(self, deadline: float) -> bool:
        remaining = self._remaining(deadline)
        return remaining is None or remaining <= 0.0

    def _timed_out(self) -> ConsentDemoResult:
        return self._result(
            ConsentDemoStatus.TIMED_OUT,
            "Consent demo timed out.",
        )

    @staticmethod
    def _result(
        status: ConsentDemoStatus,
        reason: str,
    ) -> ConsentDemoResult:
        return ConsentDemoResult(status=status, safe_reason=reason)


def _valid_process(value: object) -> bool:
    if not isinstance(value, ConsentDemoProcess):
        return False
    if isinstance(value.pid, bool) or not isinstance(value.pid, int) or value.pid <= 0:
        return False
    if _process_basename(value.process_name) != NOTEPAD_COMMAND[0]:
        return False
    return _valid_creation_time(value.created_at)


def _target_problem(
    target: object,
    launched: ConsentDemoProcess,
) -> ConsentDemoResult | None:
    if not isinstance(target, ConsentDemoTarget):
        return ConsentDemoResult(
            ConsentDemoStatus.TARGET_CHANGED,
            "The Notepad target identity was invalid.",
        )
    if target.process_alive is not True:
        return ConsentDemoResult(
            ConsentDemoStatus.PROCESS_EXITED,
            "The Notepad process exited before consent typing.",
        )
    identity_matches = bool(
        not isinstance(target.pid, bool)
        and isinstance(target.pid, int)
        and target.pid > 0
        and target.pid == launched.pid
        and _process_basename(target.process_name) == NOTEPAD_COMMAND[0]
        and _valid_creation_time(target.created_at)
        and target.created_at == launched.created_at
    )
    valid_hwnd = bool(
        not isinstance(target.hwnd, bool)
        and isinstance(target.hwnd, int)
        and target.hwnd > 0
    )
    valid_bounds = _valid_bounds(target.bounds)
    foreground = bool(
        not isinstance(target.foreground_hwnd, bool)
        and isinstance(target.foreground_hwnd, int)
        and target.foreground_hwnd == target.hwnd
    )
    if not (
        identity_matches
        and valid_hwnd
        and valid_bounds
        and foreground
        and target.window_valid is True
    ):
        return ConsentDemoResult(
            ConsentDemoStatus.TARGET_CHANGED,
            "The Notepad process or window target changed.",
        )
    return None


def _same_locked_target(
    expected: ConsentDemoTarget,
    live: ConsentDemoTarget,
) -> bool:
    return bool(
        expected.pid == live.pid
        and _process_basename(expected.process_name)
        == _process_basename(live.process_name)
        and expected.created_at == live.created_at
        and expected.hwnd == live.hwnd
        and expected.bounds == live.bounds
        and expected.foreground_hwnd == live.foreground_hwnd
    )


def _valid_bounds(value: object) -> bool:
    if not isinstance(value, tuple) or len(value) != 4:
        return False
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        return False
    _left, _top, width, height = value
    return width > 0 and height > 0


def _valid_creation_time(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value)) and float(value) >= 0.0


def _process_basename(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return ntpath.basename(value.strip()).casefold()


def _safe_check(check: AbortCheck) -> bool:
    try:
        return bool(check())
    except Exception:
        # A broken lifecycle channel cannot authorize an external effect.
        return True
