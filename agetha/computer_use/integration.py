"""Application composition helpers for opt-in Computer Use Lite.

The core session remains deterministic and injected.  This module composes
that core with Agetha's existing AI, process, screen, typing, and provider-slot
owners without importing Tk or starting any effect at import time.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import Callable

from .activation import LocalActivation
from .ai_bridge import StructuredAIClient
from .executor import ComputerExecutor, ExecutorDependencies
from .models import (
    ComputerUseAuditEvent,
    LiveTargetState,
    SessionSnapshot,
    WindowIdentity,
    normalize_process_name,
)
from .observer import ComputerObserver, UnavailableAccessibilityProvider
from .planner import ComputerPlanner
from .policy import ComputerUsePolicy
from .runtime import (
    ExactWindowFocus,
    LazyPyAutoGUIInput,
    LockedTargetScreenSource,
    LockedTargetValidator,
    build_executor_dependencies,
    running_application_to_window_identity,
    runtime_platform_status,
)
from .session import ComputerUseManager
from .verifier import ComputerVerifier


@dataclass(frozen=True, slots=True)
class ComputerUseRuntimeBundle:
    manager: ComputerUseManager | None
    runtime_status: str
    focus_window: Callable[[WindowIdentity], bool] | None = None

    @property
    def available(self) -> bool:
        return self.manager is not None and self.runtime_status == "available_windows"


@dataclass(frozen=True, slots=True)
class TargetSelection:
    target: WindowIdentity | None
    status: str
    launched: bool = False


def build_runtime_bundle(
    *,
    ai_engine: object,
    screen_reader: object,
    process_awareness: object,
    planner_route: str,
    planner_model: str,
    reserve_provider: Callable[[], object | None],
    release_provider: Callable[[object], None],
    guarded_type: Callable[
        [str, WindowIdentity, threading.Event, Callable[[bool], bool]],
        bool,
    ],
    feature_gate: Callable[[], bool],
    is_shutdown: Callable[[], bool],
    effect_runner: Callable[
        [Callable[[], object]], tuple[bool, object | None]
    ] | None = None,
    focus_allowed: Callable[[], bool] = lambda: True,
    status_sink: Callable[[SessionSnapshot], None] | None = None,
    audit_sink: Callable[[ComputerUseAuditEvent], None] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    platform_name: str | None = None,
) -> ComputerUseRuntimeBundle:
    """Build the real runtime only where strict Windows locking is available."""

    platform_status = runtime_platform_status(platform_name=platform_name)
    if platform_status != "available_windows":
        return ComputerUseRuntimeBundle(None, platform_status)
    if ai_engine is None or screen_reader is None or process_awareness is None:
        return ComputerUseRuntimeBundle(None, "unavailable_dependencies")

    validator = LockedTargetValidator(
        process_awareness,
        screen_reader,
        platform_name=platform_name,
    )
    if not validator.available:
        return ComputerUseRuntimeBundle(None, validator.status)
    inputs = LazyPyAutoGUIInput(platform_name=platform_name)
    source = LockedTargetScreenSource(
        screen_reader,
        process_awareness,
        validator=validator,
        input_adapter=inputs,
        monotonic=monotonic,
        platform_name=platform_name,
    )
    if not source.available:
        return ComputerUseRuntimeBundle(None, source.status)

    def _guarded_type_with_lock(
        text: str,
        target: WindowIdentity,
        cancel_event: threading.Event,
    ) -> bool:
        def _validate_locked_target(require_foreground: bool) -> bool:
            if cancel_event.is_set():
                return False
            try:
                if not feature_gate() or is_shutdown():
                    return False
                state = validator.validate(
                    target,
                    require_foreground=require_foreground,
                )
            except Exception:
                return False
            return bool(
                state.is_window
                and state.authorized
                and state.target is not None
                and target.matches(state.target, require_same_bounds=True)
                and (not require_foreground or state.foreground)
            )

        return bool(
            guarded_type(
                text,
                target,
                cancel_event,
                _validate_locked_target,
            )
        )

    cheap = StructuredAIClient(
        ai_engine,
        route=planner_route,
        model=planner_model,
        max_tokens=480,
        reserve=reserve_provider,
        release=release_provider,
    )
    recovery = StructuredAIClient(
        ai_engine,
        route="primary",
        model="",
        max_tokens=720,
        reserve=reserve_provider,
        release=release_provider,
    )
    base_dependencies = build_executor_dependencies(
        process_awareness,
        screen_reader,
        guarded_type=_guarded_type_with_lock,
        validator=validator,
        input_adapter=inputs,
        is_shutdown=is_shutdown,
        monotonic=monotonic,
        platform_name=platform_name,
    )
    dependencies = _gate_effect_dependencies(
        base_dependencies,
        feature_gate=feature_gate,
        effect_runner=effect_runner,
        is_shutdown=is_shutdown,
        focus_allowed=focus_allowed,
    )
    manager = ComputerUseManager(
        observer=ComputerObserver(
            source,
            accessibility=UnavailableAccessibilityProvider(),
            monotonic=monotonic,
        ),
        planner=ComputerPlanner(cheap, recovery_client=recovery),
        policy=ComputerUsePolicy(),
        executor=ComputerExecutor(dependencies),
        verifier=ComputerVerifier(),
        monotonic=monotonic,
        status_sink=status_sink,
        audit_sink=audit_sink,
    )
    return ComputerUseRuntimeBundle(
        manager,
        "available_windows",
        focus_window=dependencies.focus_window,
    )


def _gate_effect_dependencies(
    dependencies: ExecutorDependencies,
    *,
    feature_gate: Callable[[], bool],
    is_shutdown: Callable[[], bool],
    effect_runner: Callable[
        [Callable[[], object]], tuple[bool, object | None]
    ] | None = None,
    focus_allowed: Callable[[], bool] = lambda: True,
) -> ExecutorDependencies:
    """Recheck global feature/command gates immediately before each effect."""

    def allowed() -> bool:
        try:
            return bool(feature_gate()) and not bool(is_shutdown())
        except Exception:
            return False

    def validate(target: WindowIdentity, foreground: bool) -> LiveTargetState:
        if not allowed():
            return LiveTargetState(None, False, False, False)
        return dependencies.validate_target(target, foreground)

    def run_effect(callback: Callable[[], object]) -> tuple[bool, object | None]:
        if effect_runner is None:
            if not allowed():
                return False, None
            return True, callback()
        try:
            return effect_runner(lambda: callback() if allowed() else False)
        except Exception:
            return False, None

    def effect(callback):
        def gated(*args):
            performed, result = run_effect(lambda: callback(*args))
            return bool(performed and result)

        return gated

    def focus(target: WindowIdentity) -> bool:
        try:
            current_focus_allowed = bool(focus_allowed())
        except Exception:
            current_focus_allowed = False
        performed, result = run_effect(
            lambda: bool(
                current_focus_allowed
                and dependencies.focus_window(target)
            ),
        )
        return bool(performed and result)

    return replace(
        dependencies,
        validate_target=validate,
        move_pointer=effect(dependencies.move_pointer),
        click=effect(dependencies.click),
        double_click=effect(dependencies.double_click),
        scroll=effect(dependencies.scroll),
        keypress=effect(dependencies.keypress),
        hotkey=effect(dependencies.hotkey),
        focus_window=focus,
        # Guard and preview may synchronously wait for user input. They must not
        # run while the capability controller holds its transition lock; the
        # typing adapter gates only the eventual platform primitives.
        guarded_type=dependencies.guarded_type,
        is_shutdown=is_shutdown,
    )


def select_initial_target(
    process_awareness: object,
    activation: LocalActivation,
    cancel_event: threading.Event,
    *,
    focus_window: Callable[[WindowIdentity], bool] | None = None,
    launcher: Callable[[tuple[str, ...]], bool] | None = None,
    timeout_seconds: float = 8.0,
    monotonic: Callable[[], float] = time.monotonic,
    wait: Callable[[float], None] = time.sleep,
) -> TargetSelection:
    """Discover/launch only the exact app named by the direct user, then lock it."""

    requested = activation.requested_app
    launch = launcher
    deadline = monotonic() + max(0.1, min(float(timeout_seconds), 30.0))
    launched = False
    focus_attempted: set[tuple[int, int]] = set()

    while not cancel_event.is_set() and monotonic() < deadline:
        snapshot_method = getattr(process_awareness, "snapshot", None)
        if not callable(snapshot_method):
            return TargetSelection(None, "process awareness unavailable", launched)
        try:
            snapshot = snapshot_method()
        except Exception:
            return TargetSelection(None, "process snapshot failed", launched)
        if cancel_event.is_set():
            return TargetSelection(None, "cancelled", launched)
        foreground = getattr(snapshot, "foreground", None)
        visible = tuple(getattr(snapshot, "visible_apps", ()) or ())
        applications = tuple(
            item for item in (foreground, *visible) if item is not None
        )
        selected = _matching_application(applications, requested)
        if selected is not None and not bool(getattr(selected, "sensitive", False)):
            target = running_application_to_window_identity(selected)
            if target is not None and target.process.created_at is not None:
                if cancel_event.is_set():
                    return TargetSelection(None, "cancelled", launched)
                is_foreground = bool(getattr(selected, "foreground", False))
                if is_foreground:
                    return TargetSelection(target, "target locked", launched)
                key = (target.hwnd, target.process.pid)
                if focus_window is not None and key not in focus_attempted:
                    focus_attempted.add(key)
                    if cancel_event.is_set():
                        return TargetSelection(None, "cancelled", launched)
                    if focus_window(target):
                        if cancel_event.is_set():
                            return TargetSelection(None, "cancelled", launched)
                        continue

        if requested is not None and not launched and requested.launch_command:
            if cancel_event.is_set():
                return TargetSelection(None, "cancelled", launched)
            if launch is None:
                return TargetSelection(None, "guarded launcher unavailable", launched)
            if not launch(requested.launch_command):
                return TargetSelection(None, "application launch failed", True)
            launched = True
            if cancel_event.is_set():
                return TargetSelection(None, "cancelled", launched)
        elif requested is None:
            return TargetSelection(None, "foreground target unavailable", launched)
        wait(min(0.1, max(0.0, deadline - monotonic())))

    return TargetSelection(
        None,
        "cancelled" if cancel_event.is_set() else "target did not become available",
        launched,
    )


def _matching_application(applications: tuple[object, ...], requested) -> object | None:
    if requested is None:
        return next(
            (item for item in applications if bool(getattr(item, "foreground", False))),
            None,
        )
    wanted = normalize_process_name(requested.process_name)
    matches = [
        item
        for item in applications
        if normalize_process_name(getattr(getattr(item, "identity", None), "name", ""))
        == wanted
    ]
    return next(
        (item for item in matches if bool(getattr(item, "foreground", False))),
        matches[0] if matches else None,
    )


__all__ = [
    "ComputerUseRuntimeBundle",
    "TargetSelection",
    "build_runtime_bundle",
    "select_initial_target",
]
