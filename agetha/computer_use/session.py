"""Bounded observe-plan-policy-act-verify session ownership.

The manager is deliberately synchronous: the application remains the owner of
worker creation.  Cancellation is thread-safe and immediately invalidates the
active generation, so a provider response that arrives later cannot execute.
"""

from __future__ import annotations

import threading
import time
import unicodedata
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Callable, Mapping

from .executor import ComputerExecutor
from .models import (
    ComputerActionKind,
    ComputerUseAuditEvent,
    ExecutionStatus,
    PolicyCode,
    PolicyDisposition,
    SessionOutcome,
    SessionSnapshot,
    SessionState,
    VerificationStatus,
    WindowIdentity,
    immutable_payload_names,
    normalize_payload_ref,
    normalize_process_name,
    safe_text,
)
from .observer import ComputerObserver
from .planner import ComputerPlanner, PlannerCancelled, PlannerProtocolError
from .policy import ComputerUsePolicy, PolicyContext
from .verifier import ComputerVerifier


class SessionAlreadyActive(RuntimeError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class ComputerUseSessionSpec:
    """User-authorized immutable inputs; exact payloads remain local only."""

    goal: str
    initial_target: WindowIdentity
    allowed_processes: frozenset[str] = field(default_factory=frozenset)
    payloads: Mapping[str, str] = field(default_factory=dict)
    enabled: bool = False
    explicit_user_activation: bool = False
    request_origin: str = "user"
    max_steps: int = 30
    timeout_seconds: float = 120.0
    confidence_min: float = 0.65
    recovery_after_failures: int = 2
    max_recovery_calls: int = 2
    typing_authorized: bool = False
    submit_authorized: bool = False
    focus_authorized: bool = False
    presentation_restricted: bool = False
    fullscreen_restricted: bool = False

    def __post_init__(self) -> None:
        goal = str(self.goal)
        if not goal or len(goal) > 4_000:
            raise ValueError("goal must contain 1..4000 characters")
        if not 1 <= self.max_steps <= 1_000:
            raise ValueError("max_steps must be 1..1000")
        timeout = float(self.timeout_seconds)
        if not 0 < timeout <= 3_600:
            raise ValueError("timeout_seconds must be 0..3600")
        if not 1 <= self.recovery_after_failures <= self.max_steps:
            raise ValueError("recovery_after_failures is outside the step budget")
        if not 0 <= self.max_recovery_calls <= self.max_steps:
            raise ValueError("max_recovery_calls is outside the step budget")

        copied: dict[str, str] = {}
        for raw_ref, value in self.payloads.items():
            ref = normalize_payload_ref(raw_ref)
            if ref in copied:
                raise ValueError("duplicate normalized payload reference")
            if not isinstance(value, str):
                raise TypeError("payload values must be exact strings")
            copied[ref] = value
        object.__setattr__(self, "payloads", MappingProxyType(copied))

        allowed = frozenset(normalize_process_name(item) for item in self.allowed_processes)
        if not allowed:
            allowed = frozenset({normalize_process_name(self.initial_target.process.name)})
        object.__setattr__(self, "allowed_processes", allowed)
        object.__setattr__(self, "confidence_min", min(1.0, max(0.0, float(self.confidence_min))))
        object.__setattr__(self, "timeout_seconds", timeout)

    def __repr__(self) -> str:
        refs = ",".join(immutable_payload_names(self.payloads))
        return (
            "ComputerUseSessionSpec("
            f"target={self.initial_target.process.name!r}, payload_refs=({refs}), "
            f"enabled={self.enabled!r}, origin={self.request_origin!r})"
        )


class _PayloadVault:
    def __init__(self, payloads: Mapping[str, str]) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, str] = dict(payloads)

    @property
    def refs(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._values))

    def snapshot(self) -> Mapping[str, str]:
        with self._lock:
            return MappingProxyType(dict(self._values))

    def planner_goal(self, goal: str) -> str:
        with self._lock:
            replacements = sorted(self._values.items(), key=lambda item: len(item[1]), reverse=True)
        result = goal
        for ref, exact in replacements:
            if exact:
                result = result.replace(exact, f"payload:{ref}")
        return safe_text(result, maximum=600)

    def planner_observation(self, observation):
        """Return a planner-only copy with every exact payload echo removed."""

        with self._lock:
            items = tuple(self._values.items())
        replacements: list[tuple[str, str]] = []
        for ref, exact in items:
            if not exact:
                continue
            variants = {
                exact,
                unicodedata.normalize("NFC", exact),
                unicodedata.normalize("NFD", exact),
                unicodedata.normalize("NFKC", exact),
                unicodedata.normalize("NFKD", exact),
            }
            replacements.extend(
                (variant, f"payload:{ref}")
                for variant in variants
                if variant
            )
        replacements.sort(key=lambda item: len(item[0]), reverse=True)

        def scrub(value: str, *, maximum: int = 2000) -> str:
            result = str(value or "")
            for exact, placeholder in replacements:
                result = result.replace(exact, placeholder)
            return safe_text(result, maximum=maximum)

        controls = tuple(
            replace(
                control,
                label=scrub(control.label),
                role=scrub(control.role, maximum=80),
                state=scrub(control.state, maximum=80),
            )
            for control in observation.controls
        )
        return replace(
            observation,
            controls=controls,
            previous_result=scrub(observation.previous_result),
        )

    def clear(self) -> None:
        with self._lock:
            self._values.clear()

    def __repr__(self) -> str:
        return f"_PayloadVault(refs={self.refs!r})"


class _ComputerUseSession:
    def __init__(
        self,
        *,
        session_id: str,
        generation: int,
        spec: ComputerUseSessionSpec,
        observer: ComputerObserver,
        planner: ComputerPlanner,
        policy: ComputerUsePolicy,
        executor: ComputerExecutor,
        verifier: ComputerVerifier,
        cancel_event: threading.Event,
        monotonic: Callable[[], float],
        is_current: Callable[[str, int], bool],
        is_shutdown: Callable[[], bool],
        publish_snapshot: Callable[[SessionSnapshot], None],
        audit_sink: Callable[[ComputerUseAuditEvent], None] | None,
    ) -> None:
        self.session_id = session_id
        self.generation = generation
        self._spec = spec
        self._observer = observer
        self._planner = planner
        self._policy = policy
        self._executor = executor
        self._verifier = verifier
        self._cancel = cancel_event
        self._monotonic = monotonic
        self._is_current = is_current
        self._is_shutdown = is_shutdown
        self._publish_snapshot = publish_snapshot
        self._audit_sink = audit_sink
        self._vault = _PayloadVault(spec.payloads)
        self._started = monotonic()
        self._deadline = self._started + spec.timeout_seconds
        self._step = 0
        self._recovery_calls = 0
        self._failures = 0
        self._recent_actions: list[str] = []
        self._last_action = ""
        self._last_result = "starting"

    def run(self) -> SessionOutcome:
        current_observation = None
        previous_result = ""
        self._publish(SessionState.RUNNING)
        try:
            while self._step < self._spec.max_steps:
                stopped = self._stop_outcome()
                if stopped is not None:
                    return stopped

                if current_observation is None:
                    try:
                        current_observation = self._observer.observe(
                            self._spec.initial_target,
                            previous_result=previous_result,
                        )
                    except Exception:
                        return self._outcome(SessionState.FAILED, "observation failed")
                if not self._observation_matches_lock(current_observation):
                    return self._outcome(SessionState.BLOCKED, "target changed during observation")

                preflight = self._policy.preflight_observation(
                    current_observation,
                    self._policy_context(step=self._step),
                )
                if preflight.disposition is not PolicyDisposition.ALLOW:
                    self._last_result = preflight.code.value
                    if preflight.code is PolicyCode.CANCELLED:
                        return self._outcome(SessionState.CANCELLED, preflight.safe_reason)
                    if preflight.code is PolicyCode.SHUTDOWN:
                        return self._outcome(SessionState.SHUTDOWN, preflight.safe_reason)
                    return self._outcome(SessionState.BLOCKED, preflight.safe_reason)

                use_recovery = self._should_recover()
                if use_recovery:
                    # Count attempted provider calls, including failures/timeouts.
                    self._recovery_calls += 1
                try:
                    planner_observation = self._vault.planner_observation(
                        current_observation,
                    )
                    planned = self._planner.plan(
                        session_id=self.session_id,
                        generation=self.generation,
                        step=self._step,
                        goal=self._vault.planner_goal(self._spec.goal),
                        observation=planner_observation,
                        payload_refs=self._vault.refs,
                        recent_actions=tuple(self._recent_actions),
                        failure_reason=previous_result,
                        recovery=use_recovery,
                        cancel_event=self._cancel,
                        is_current=self._is_current,
                    )
                except PlannerCancelled:
                    return self._stop_outcome() or self._outcome(SessionState.CANCELLED, "planner result was discarded")
                except PlannerProtocolError:
                    self._step += 1
                    self._failures += 1
                    previous_result = "planner returned an invalid action"
                    self._last_result = previous_result
                    self._publish(SessionState.RUNNING)
                    if use_recovery or not self._can_recover_after_failure():
                        return self._outcome(SessionState.BLOCKED, previous_result)
                    current_observation = None
                    continue
                except Exception:
                    self._step += 1
                    self._failures += 1
                    previous_result = "planner request failed"
                    self._last_result = previous_result
                    self._publish(SessionState.RUNNING)
                    if use_recovery or not self._can_recover_after_failure():
                        return self._outcome(SessionState.FAILED, previous_result)
                    current_observation = None
                    continue

                if not self._is_current(self.session_id, self.generation) or self._cancel.is_set():
                    return self._stop_outcome() or self._outcome(SessionState.CANCELLED, "late action was discarded")

                action = planned.action
                self._step += 1
                self._last_action = action.action.value
                self._publish(SessionState.RUNNING)

                if action.confidence < self._spec.confidence_min:
                    self._failures += 1
                    previous_result = "planner confidence was below threshold"
                    self._last_result = previous_result
                    self._remember(action.action.value, "low_confidence")
                    self._publish(SessionState.RUNNING)
                    self._audit(action, PolicyCode.LOW_CONFIDENCE.value, "blocked")
                    if use_recovery or not self._can_recover_after_failure():
                        return self._outcome(SessionState.BLOCKED, previous_result)
                    current_observation = None
                    continue

                if action.action is ComputerActionKind.BLOCKED and not use_recovery and self._can_recover_after_failure(force=True):
                    self._failures = max(self._failures + 1, self._spec.recovery_after_failures)
                    previous_result = "cheap planner requested handoff"
                    self._last_result = previous_result
                    self._remember(action.action.value, "recovery_pending")
                    current_observation = None
                    continue

                context = self._policy_context(step=self._step - 1)
                decision = self._policy.evaluate(action, current_observation, context)
                if decision.disposition is not PolicyDisposition.ALLOW:
                    previous_result = decision.safe_reason
                    self._last_result = decision.code.value
                    self._remember(action.action.value, decision.code.value)
                    self._publish(SessionState.RUNNING)
                    self._audit(action, decision.code.value, "blocked")
                    if decision.disposition is PolicyDisposition.REOBSERVE and decision.code in {
                        PolicyCode.CONTROL_NOT_FOUND,
                        PolicyCode.LOW_CONFIDENCE,
                    }:
                        self._failures += 1
                        current_observation = None
                        continue
                    return self._outcome(SessionState.BLOCKED, decision.safe_reason)

                execution = self._executor.execute(
                    action,
                    current_observation,
                    decision,
                    payloads=self._vault.snapshot(),
                    cancel_event=self._cancel,
                    deadline=self._deadline,
                )
                self._last_result = execution.status.value
                self._audit(action, decision.code.value, execution.status.value)
                self._remember(action.action.value, execution.status.value)
                self._publish(SessionState.RUNNING)

                if execution.status is ExecutionStatus.FINISHED:
                    return self._outcome(SessionState.COMPLETED, "goal marked complete")
                if execution.status is ExecutionStatus.BLOCKED:
                    return self._outcome(SessionState.BLOCKED, execution.safe_reason)
                if execution.status is ExecutionStatus.CANCELLED:
                    return self._outcome(SessionState.CANCELLED, execution.safe_reason)
                if execution.status is ExecutionStatus.SHUTDOWN:
                    return self._outcome(SessionState.SHUTDOWN, execution.safe_reason)
                if execution.status is ExecutionStatus.EXPIRED:
                    return self._outcome(SessionState.BLOCKED, execution.safe_reason)
                if execution.status is ExecutionStatus.TARGET_CHANGED:
                    return self._outcome(SessionState.BLOCKED, execution.safe_reason)
                if execution.status is not ExecutionStatus.SUCCESS:
                    self._failures += 1
                    previous_result = execution.safe_reason
                    if use_recovery or not self._can_recover_after_failure():
                        return self._outcome(SessionState.FAILED, execution.safe_reason)
                    current_observation = None
                    continue

                stopped = self._stop_outcome()
                if stopped is not None:
                    return stopped
                try:
                    after = self._observer.observe(
                        self._spec.initial_target,
                        previous_result=execution.safe_reason,
                    )
                except Exception:
                    return self._outcome(SessionState.FAILED, "observation failed after action")
                verification = self._verifier.verify(
                    current_observation,
                    after,
                    action,
                    execution,
                )
                self._last_result = verification.status.value
                self._publish(SessionState.RUNNING)
                if verification.status is VerificationStatus.TARGET_CHANGED:
                    return self._outcome(SessionState.BLOCKED, verification.safe_reason)
                if verification.status is VerificationStatus.CANCELLED:
                    return self._outcome(SessionState.CANCELLED, verification.safe_reason)
                if verification.status is VerificationStatus.VERIFIED:
                    self._failures = 0
                else:
                    self._failures += 1
                previous_result = verification.safe_reason
                current_observation = after

            return self._outcome(SessionState.BLOCKED, "session step limit reached")
        finally:
            self._vault.clear()

    def _observation_matches_lock(self, observation: object) -> bool:
        target = getattr(observation, "target", None)
        alive = bool(getattr(observation, "process_alive", False))
        return (
            alive
            and target is not None
            and self._spec.initial_target.matches(target, require_same_bounds=True)
            and normalize_process_name(target.process.name) in self._spec.allowed_processes
        )

    def _policy_context(self, *, step: int) -> PolicyContext:
        return PolicyContext(
            enabled=self._spec.enabled,
            explicit_user_activation=self._spec.explicit_user_activation,
            request_origin=self._spec.request_origin,
            session_id=self.session_id,
            expected_session_id=self.session_id,
            generation=self.generation,
            expected_generation=self.generation if self._is_current(self.session_id, self.generation) else -1,
            now=self._monotonic(),
            deadline=self._deadline,
            step=step,
            max_steps=self._spec.max_steps,
            cancelled=self._cancel.is_set(),
            shutdown=self._is_shutdown(),
            expected_target=self._spec.initial_target,
            allowed_processes=self._spec.allowed_processes,
            payload_refs=frozenset(self._vault.refs),
            confidence_min=self._spec.confidence_min,
            typing_authorized=self._spec.typing_authorized,
            submit_authorized=self._spec.submit_authorized,
            focus_authorized=self._spec.focus_authorized,
            presentation_restricted=self._spec.presentation_restricted,
            fullscreen_restricted=self._spec.fullscreen_restricted,
            goal_summary=self._vault.planner_goal(self._spec.goal),
        )

    def _should_recover(self) -> bool:
        return (
            self._failures >= self._spec.recovery_after_failures
            and self._recovery_calls < self._spec.max_recovery_calls
            and self._planner.recovery_available
        )

    def _can_recover_after_failure(self, *, force: bool = False) -> bool:
        if not self._planner.recovery_available or self._recovery_calls >= self._spec.max_recovery_calls:
            return False
        return force or self._failures < self._spec.max_steps

    def _stop_outcome(self) -> SessionOutcome | None:
        if self._is_shutdown():
            return self._outcome(SessionState.SHUTDOWN, "application is shutting down")
        if self._cancel.is_set() or not self._is_current(self.session_id, self.generation):
            return self._outcome(SessionState.CANCELLED, "session was cancelled")
        if self._monotonic() >= self._deadline:
            return self._outcome(SessionState.BLOCKED, "session deadline expired")
        return None

    def _remember(self, action: str, result: str) -> None:
        self._recent_actions.append(f"{safe_text(action, maximum=40)}:{safe_text(result, maximum=80)}")
        del self._recent_actions[:-4]

    def _audit(self, action, policy_result: str, result: str) -> None:
        if self._audit_sink is None:
            return
        try:
            self._audit_sink(ComputerUseAuditEvent(
                session_id=self.session_id,
                step=self._step,
                action=action.action.value,
                target_process=self._spec.initial_target.process.name,
                policy_result=policy_result,
                result=result,
                confidence=action.confidence,
            ))
        except Exception:
            return

    def _publish(self, state: SessionState) -> None:
        self._publish_snapshot(
            SessionSnapshot(
                state=state,
                session_id=self.session_id,
                generation=self.generation,
                step=self._step,
                max_steps=self._spec.max_steps,
                target_process=self._spec.initial_target.process.name,
                last_action=self._last_action,
                last_result=self._last_result,
                recovery_calls=self._recovery_calls,
            )
        )

    def _outcome(self, state: SessionState, reason: str) -> SessionOutcome:
        self._last_result = safe_text(reason)
        self._publish(state)
        return SessionOutcome(
            state=state,
            session_id=self.session_id,
            steps=self._step,
            recovery_calls=self._recovery_calls,
            safe_reason=self._last_result,
        )


class ComputerUseManager:
    """Own exactly one session generation and expose immediate thread-safe STOP."""

    def __init__(
        self,
        *,
        observer: ComputerObserver,
        planner: ComputerPlanner,
        policy: ComputerUsePolicy,
        executor: ComputerExecutor,
        verifier: ComputerVerifier,
        monotonic: Callable[[], float] = time.monotonic,
        status_sink: Callable[[SessionSnapshot], None] | None = None,
        audit_sink: Callable[[ComputerUseAuditEvent], None] | None = None,
        session_id_factory: Callable[[int], str] | None = None,
    ) -> None:
        self._observer = observer
        self._planner = planner
        self._policy = policy
        self._executor = executor
        self._verifier = verifier
        self._monotonic = monotonic
        self._status_sink = status_sink
        self._audit_sink = audit_sink
        self._session_id_factory = session_id_factory or (lambda generation: f"computer:{generation}")
        self._lock = threading.RLock()
        self._generation = 0
        self._active_session_id = ""
        self._active_cancel: threading.Event | None = None
        self._shutdown = False
        self._snapshot = SessionSnapshot(SessionState.IDLE)

    def run(
        self,
        spec: ComputerUseSessionSpec,
        *,
        cancel_event: threading.Event | None = None,
    ) -> SessionOutcome:
        with self._lock:
            if self._shutdown:
                return SessionOutcome(SessionState.SHUTDOWN, "", 0, 0, "application is shutting down")
            if self._active_cancel is not None:
                raise SessionAlreadyActive("a Computer Use session is already active")
            self._generation += 1
            generation = self._generation
            session_id = self._session_id_factory(generation)

            denied = self._activation_denial(spec)
            if denied:
                outcome = SessionOutcome(SessionState.BLOCKED, session_id, 0, 0, denied)
                self._snapshot = SessionSnapshot(
                    state=SessionState.BLOCKED,
                    session_id=session_id,
                    generation=generation,
                    max_steps=spec.max_steps,
                    target_process=spec.initial_target.process.name,
                    last_result=denied,
                )
                snapshot = self._snapshot
            elif cancel_event is not None and cancel_event.is_set():
                outcome = SessionOutcome(
                    SessionState.CANCELLED,
                    session_id,
                    0,
                    0,
                    "session was cancelled before start",
                )
                self._snapshot = SessionSnapshot(
                    state=SessionState.CANCELLED,
                    session_id=session_id,
                    generation=generation,
                    max_steps=spec.max_steps,
                    target_process=spec.initial_target.process.name,
                    last_result="cancelled",
                )
                snapshot = self._snapshot
            else:
                session_cancel = cancel_event or threading.Event()
                self._active_session_id = session_id
                self._active_cancel = session_cancel
                snapshot = SessionSnapshot(
                    state=SessionState.RUNNING,
                    session_id=session_id,
                    generation=generation,
                    max_steps=spec.max_steps,
                    target_process=spec.initial_target.process.name,
                    last_result="starting",
                )
                self._snapshot = snapshot
                outcome = None
        self._emit(snapshot)
        if outcome is not None:
            return outcome

        session = _ComputerUseSession(
            session_id=session_id,
            generation=generation,
            spec=spec,
            observer=self._observer,
            planner=self._planner,
            policy=self._policy,
            executor=self._executor,
            verifier=self._verifier,
            cancel_event=session_cancel,
            monotonic=self._monotonic,
            is_current=self.is_current,
            is_shutdown=self.is_shutdown,
            publish_snapshot=self._set_snapshot,
            audit_sink=self._audit_sink,
        )
        try:
            return session.run()
        finally:
            with self._lock:
                if self._active_session_id == session_id:
                    self._active_session_id = ""
                    self._active_cancel = None

    def cancel_active(self, reason: str = "stop") -> bool:
        del reason  # Never retain arbitrary UI text in status/audit state.
        with self._lock:
            if self._active_cancel is None:
                return False
            self._generation += 1
            self._active_cancel.set()
            self._snapshot = SessionSnapshot(
                state=SessionState.CANCELLED,
                session_id=self._active_session_id,
                generation=self._generation,
                step=self._snapshot.step,
                max_steps=self._snapshot.max_steps,
                target_process=self._snapshot.target_process,
                last_action=self._snapshot.last_action,
                last_result="cancelled",
                recovery_calls=self._snapshot.recovery_calls,
            )
            snapshot = self._snapshot
        self._emit(snapshot)
        return True

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            self._generation += 1
            if self._active_cancel is not None:
                self._active_cancel.set()
            self._snapshot = SessionSnapshot(
                state=SessionState.SHUTDOWN,
                session_id=self._active_session_id,
                generation=self._generation,
                step=self._snapshot.step,
                max_steps=self._snapshot.max_steps,
                target_process=self._snapshot.target_process,
                last_action=self._snapshot.last_action,
                last_result="shutdown",
                recovery_calls=self._snapshot.recovery_calls,
            )
            snapshot = self._snapshot
        self._emit(snapshot)

    def snapshot(self) -> SessionSnapshot:
        with self._lock:
            return self._snapshot

    def is_current(self, session_id: str, generation: int) -> bool:
        with self._lock:
            return (
                not self._shutdown
                and self._active_cancel is not None
                and not self._active_cancel.is_set()
                and self._active_session_id == session_id
                and self._generation == generation
            )

    def is_shutdown(self) -> bool:
        with self._lock:
            return self._shutdown

    def _activation_denial(self, spec: ComputerUseSessionSpec) -> str:
        if not spec.enabled:
            return "computer use is disabled"
        if not spec.explicit_user_activation or spec.request_origin != "user":
            return "explicit user activation is required"
        target_name = normalize_process_name(spec.initial_target.process.name)
        if target_name not in spec.allowed_processes:
            return "initial target is not authorized"
        return ""

    def _set_snapshot(self, snapshot: SessionSnapshot) -> None:
        with self._lock:
            if snapshot.session_id != self._active_session_id:
                return
            if snapshot.generation != self._generation:
                return
            self._snapshot = snapshot
        self._emit(snapshot)

    def _emit(self, snapshot: SessionSnapshot) -> None:
        if self._status_sink is None:
            return
        try:
            self._status_sink(snapshot)
        except Exception:
            # Status is observational only and cannot own session safety.
            return
