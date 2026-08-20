"""The sole injected effect boundary for Computer Use Lite."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Mapping

from .models import (
    ComputerAction,
    ComputerActionKind,
    ComputerObservation,
    ExecutionResult,
    ExecutionStatus,
    LiveTargetState,
    PolicyCode,
    PolicyDecision,
    PolicyDisposition,
    WindowIdentity,
    normalize_payload_ref,
)


@dataclass(frozen=True, slots=True)
class ExecutorDependencies:
    """All platform effects are mandatory injections; no real input is implicit."""

    validate_target: Callable[[WindowIdentity, bool], LiveTargetState]
    move_pointer: Callable[[int, int], bool]
    click: Callable[[int, int], bool]
    double_click: Callable[[int, int], bool]
    scroll: Callable[[int, int | None, int | None], bool]
    keypress: Callable[[str], bool]
    hotkey: Callable[[tuple[str, ...]], bool]
    focus_window: Callable[[WindowIdentity], bool]
    guarded_type: Callable[[str, WindowIdentity, threading.Event], bool]
    wait: Callable[[float, threading.Event], bool] = lambda seconds, event: not event.wait(seconds)
    is_shutdown: Callable[[], bool] = lambda: False
    monotonic: Callable[[], float] = time.monotonic


class ComputerExecutor:
    """Resolve one validated action and perform at most one injected effect."""

    def __init__(self, dependencies: ExecutorDependencies) -> None:
        self._deps = dependencies

    def execute(
        self,
        action: ComputerAction,
        observation: ComputerObservation,
        decision: PolicyDecision,
        *,
        payloads: Mapping[str, str],
        cancel_event: threading.Event,
        deadline: float,
    ) -> ExecutionResult:
        if action.observation_id != observation.observation_id:
            return ExecutionResult(
                ExecutionStatus.TARGET_CHANGED,
                action.action,
                "action references a stale observation",
            )
        if not decision.allowed:
            status = (
                ExecutionStatus.TARGET_CHANGED
                if decision.code in {
                    PolicyCode.TARGET_CHANGED,
                    PolicyCode.TARGET_NOT_FOREGROUND,
                }
                else ExecutionStatus.POLICY_DENIED
            )
            return ExecutionResult(status, action.action, decision.safe_reason)
        stop = self._stop_result(action, cancel_event, deadline)
        if stop is not None:
            return stop

        kind = action.action
        if kind is ComputerActionKind.OBSERVE_AGAIN:
            return ExecutionResult(ExecutionStatus.SUCCESS, kind, "fresh observation requested")
        if kind is ComputerActionKind.FINISH:
            return ExecutionResult(ExecutionStatus.FINISHED, kind, "planner marked goal complete")
        if kind is ComputerActionKind.BLOCKED:
            return ExecutionResult(ExecutionStatus.BLOCKED, kind, "planner requested user handoff")
        if kind is ComputerActionKind.WAIT:
            return self._wait(action, cancel_event, deadline)

        expected = observation.target
        if expected is None:
            return ExecutionResult(ExecutionStatus.TARGET_CHANGED, kind, "locked target is unavailable")
        require_foreground = kind is not ComputerActionKind.FOCUS_WINDOW
        invalid = self._validate_live_target(
            action,
            expected,
            require_foreground=require_foreground,
            cancel_event=cancel_event,
            deadline=deadline,
        )
        if invalid is not None:
            return invalid
        # Validation callbacks may take long enough for STOP/shutdown/expiry to
        # arrive.  Recheck at the last possible point before the injected effect.
        stop = self._stop_result(action, cancel_event, deadline)
        if stop is not None:
            return stop

        try:
            success = self._perform_effect(
                action,
                observation,
                expected,
                payloads,
                cancel_event,
            )
        except Exception:
            # Exception text can contain window titles or typed content.
            return ExecutionResult(ExecutionStatus.FAILED, kind, "effect callback failed")

        stop = self._stop_result(action, cancel_event, deadline)
        if stop is not None:
            return stop
        if not success:
            if kind is ComputerActionKind.TYPE_PAYLOAD:
                invalid = self._validate_live_target(
                    action,
                    expected,
                    require_foreground=True,
                    cancel_event=cancel_event,
                    deadline=deadline,
                )
                if invalid is not None:
                    return invalid
            return ExecutionResult(ExecutionStatus.FAILED, kind, "effect callback reported failure")
        return ExecutionResult(ExecutionStatus.SUCCESS, kind, "effect completed")

    def _perform_effect(
        self,
        action: ComputerAction,
        observation: ComputerObservation,
        target: WindowIdentity,
        payloads: Mapping[str, str],
        cancel_event: threading.Event,
    ) -> bool:
        kind = action.action
        if kind is ComputerActionKind.MOVE_POINTER:
            assert action.x is not None and action.y is not None
            return bool(self._deps.move_pointer(action.x, action.y))
        if kind is ComputerActionKind.CLICK_POINT:
            assert action.x is not None and action.y is not None
            return bool(self._deps.click(action.x, action.y))
        if kind in {
            ComputerActionKind.CLICK_CONTROL,
            ComputerActionKind.DOUBLE_CLICK_CONTROL,
        }:
            control = observation.control(action.target_id)
            if control is None:
                return False
            x, y = control.bounds.center
            if kind is ComputerActionKind.CLICK_CONTROL:
                return bool(self._deps.click(x, y))
            return bool(self._deps.double_click(x, y))
        if kind is ComputerActionKind.SCROLL:
            assert action.amount is not None
            # PyAutoGUI scrolls wherever the ambient pointer happens to be when
            # coordinates are omitted.  That would escape the locked target.
            # Resolve the optional planner coordinates locally to the immutable
            # target centre instead.
            x, y = (
                (action.x, action.y)
                if action.x is not None and action.y is not None
                else target.bounds.center
            )
            return bool(self._deps.scroll(action.amount, x, y))
        if kind is ComputerActionKind.KEYPRESS:
            assert action.key is not None
            return bool(self._deps.keypress(action.key))
        if kind is ComputerActionKind.HOTKEY:
            return bool(self._deps.hotkey(action.keys))
        if kind is ComputerActionKind.FOCUS_WINDOW:
            return bool(self._deps.focus_window(target))
        if kind is ComputerActionKind.TYPE_PAYLOAD:
            assert action.payload_ref is not None
            payload = payloads.get(normalize_payload_ref(action.payload_ref))
            if payload is None:
                return False
            return bool(self._deps.guarded_type(payload, target, cancel_event))
        return False

    def _validate_live_target(
        self,
        action: ComputerAction,
        expected: WindowIdentity,
        *,
        require_foreground: bool,
        cancel_event: threading.Event,
        deadline: float,
    ) -> ExecutionResult | None:
        stop = self._stop_result(action, cancel_event, deadline)
        if stop is not None:
            return stop
        try:
            state = self._deps.validate_target(expected, require_foreground)
        except Exception:
            return ExecutionResult(
                ExecutionStatus.TARGET_CHANGED,
                action.action,
                "live target validation failed",
            )
        if (
            not state.is_window
            or not state.authorized
            or state.target is None
            or not expected.matches(state.target, require_same_bounds=True)
            or (require_foreground and not state.foreground)
        ):
            return ExecutionResult(
                ExecutionStatus.TARGET_CHANGED,
                action.action,
                "locked process, window, focus, bounds, or authorization changed",
            )
        return None

    def _wait(
        self,
        action: ComputerAction,
        cancel_event: threading.Event,
        deadline: float,
    ) -> ExecutionResult:
        assert action.amount is not None
        seconds = min(action.amount / 1000.0, max(0.0, deadline - self._deps.monotonic()))
        if seconds <= 0:
            return ExecutionResult(ExecutionStatus.EXPIRED, action.action, "session deadline expired")
        try:
            completed = bool(self._deps.wait(seconds, cancel_event))
        except Exception:
            return ExecutionResult(ExecutionStatus.FAILED, action.action, "wait callback failed")
        stop = self._stop_result(action, cancel_event, deadline)
        if stop is not None:
            return stop
        return ExecutionResult(
            ExecutionStatus.SUCCESS if completed else ExecutionStatus.FAILED,
            action.action,
            "wait completed" if completed else "wait callback reported failure",
        )

    def _stop_result(
        self,
        action: ComputerAction,
        cancel_event: threading.Event,
        deadline: float,
    ) -> ExecutionResult | None:
        if self._deps.is_shutdown():
            return ExecutionResult(ExecutionStatus.SHUTDOWN, action.action, "application is shutting down")
        if cancel_event.is_set():
            return ExecutionResult(ExecutionStatus.CANCELLED, action.action, "session was cancelled")
        if self._deps.monotonic() >= deadline:
            return ExecutionResult(ExecutionStatus.EXPIRED, action.action, "session deadline expired")
        return None
