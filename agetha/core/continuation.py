"""Bounded, authority-preserving orchestration for multi-message AI turns.

The continuation engine is deliberately independent from providers, command
handlers, and Tk.  It owns one logical user session and returns immutable
decisions for the application layer to execute.  Tool results are observations
inside an existing session; they can never create user authority or start a
session of their own.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from .context_dependencies import ContextOutcome, ContextRequest


class ContinuationState(str, Enum):
    """Externally visible state of one continuation session."""

    AWAITING_MODEL = "awaiting_model"
    AWAITING_STATUS = "awaiting_status"
    AWAITING_TOOL = "awaiting_tool"
    AWAITING_CONTEXT = "awaiting_context"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    STOPPED = "stopped"


class DecisionKind(str, Enum):
    """Action the application layer should take next."""

    STARTED = "started"
    STATUS = "status"
    RUN_TOOL = "run_tool"
    RUN_CONTEXT = "run_context"
    CALL_PROVIDER = "call_provider"
    FINAL = "final"
    STOPPED = "stopped"
    BLOCKED = "blocked"
    IGNORED = "ignored"


@dataclass(frozen=True)
class MessageSegment:
    """Immutable user-visible message segment."""

    text: str
    pause: float = 0.0


@dataclass(frozen=True, order=True)
class AuthorizedResource:
    """Exact, session-scoped capability for a read-only resource."""

    kind: str
    value: str

    def __post_init__(self) -> None:
        kind, value = _canonical_resource(self.kind, self.value)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "value", value)


@dataclass(frozen=True)
class ToolRequest:
    """Normalized read-only tool request emitted by the model."""

    command: str
    arguments_json: str
    fingerprint: str

    def arguments(self) -> dict[str, object]:
        """Return a new mutable copy suitable for an existing handler."""

        value = json.loads(self.arguments_json)
        return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class ToolOutcome:
    """Bounded input returned by a read-only tool implementation."""

    tool: str
    success: bool
    summary: str
    provider_context: str
    sensitivity: str = "public"
    continuation_allowed: bool = True
    discovered_resources: tuple[AuthorizedResource, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool", str(self.tool or "").strip().lower())
        object.__setattr__(self, "success", bool(self.success))
        object.__setattr__(self, "summary", str(self.summary or ""))
        object.__setattr__(self, "provider_context", str(self.provider_context or ""))
        object.__setattr__(
            self,
            "sensitivity",
            str(self.sensitivity or "public").strip().lower(),
        )
        object.__setattr__(self, "continuation_allowed", bool(self.continuation_allowed))
        object.__setattr__(
            self,
            "discovered_resources",
            _normalize_resources(self.discovered_resources),
        )


@dataclass(frozen=True)
class ContinuationSnapshot:
    """Immutable inspection view; it cannot be used to mutate session state."""

    session_id: str
    generation: int
    original_user_message: str
    authority_origin: str
    step: int
    max_steps: int
    started_at_monotonic: float
    deadline_monotonic: float
    state: ContinuationState
    cancelled: bool
    history: tuple[ToolOutcome, ...]
    context_history: tuple[ContextOutcome, ...]
    authorized_resources: tuple[AuthorizedResource, ...]
    discovered_resources: tuple[AuthorizedResource, ...]
    sensitive_context_seen: bool
    allow_sensitive_outbound: bool


@dataclass(frozen=True)
class ContinuationDecision:
    """Immutable result of a state transition."""

    kind: DecisionKind
    reason: str
    snapshot: ContinuationSnapshot | None = None
    messages: tuple[MessageSegment, ...] = ()
    tool_request: ToolRequest | None = None
    context_request: ContextRequest | None = None
    provider_context: str = ""
    outcome: ToolOutcome | ContextOutcome | None = None

    @property
    def session_id(self) -> str:
        return self.snapshot.session_id if self.snapshot is not None else ""

    @property
    def generation(self) -> int:
        return self.snapshot.generation if self.snapshot is not None else 0

    @property
    def final_message(self) -> str:
        return " ".join(segment.text for segment in self.messages if segment.text).strip()


# Only these commands may be selected automatically after a direct user goal.
# ``speak`` and ``idle`` are terminal responses rather than tools.
AUTOMATIC_READ_ONLY_COMMANDS = frozenset({
    "search_web",
    "fetch_webpage",
    "search_memory",
    "view_memory",
    "read_document",
    "read_file",
    "list_dir",
    "list_directory",
    "read_notepad",
    "list_tasks",
    "view_dreams",
    "view_emotions",
    "system_info",
    "recycle_bin_status",
    "monitor_process",
    "get_active_app",
    "list_running_apps",
})

_FINAL_COMMANDS = frozenset({"speak", "idle"})
_OUTBOUND_COMMANDS = frozenset({"search_web", "fetch_webpage"})
_SENSITIVE_CONTEXT = frozenset({
    "private", "sensitive", "confidential", "secret", "credential", "credentials",
})
_NEVER_CONTINUE_SENSITIVITY = frozenset({"secret", "credential", "credentials"})
_MAX_RESOURCES = 128
_MAX_DISCOVERED_PER_OUTCOME = 32
_MAX_ARGUMENT_CHARS = 4096

_TOOL_ARGUMENT_FIELDS: dict[str, tuple[str, ...]] = {
    "search_web": ("query", "limit"),
    "fetch_webpage": ("url",),
    "search_memory": ("query", "limit"),
    "view_memory": ("limit",),
    "read_document": ("path",),
    "read_file": ("path",),
    "list_dir": ("path",),
    "list_directory": ("path",),
    "read_notepad": (),
    "list_tasks": (),
    "view_dreams": ("limit",),
    "view_emotions": ("limit",),
    "system_info": (),
    "recycle_bin_status": (),
    "monitor_process": ("process_name",),
    "get_active_app": (),
    "list_running_apps": (),
}

_REQUIRED_ARGUMENTS: dict[str, str] = {
    "search_web": "query",
    "fetch_webpage": "url",
    "search_memory": "query",
    "read_document": "path",
    "read_file": "path",
    "list_dir": "path",
    "list_directory": "path",
    "monitor_process": "process_name",
}

_RESOURCE_ARGUMENTS: dict[str, tuple[str, str]] = {
    "fetch_webpage": ("url", "url"),
    "read_document": ("path", "path"),
    "read_file": ("path", "path"),
    "list_dir": ("path", "path"),
    "list_directory": ("path", "path"),
    "monitor_process": ("process_name", "process"),
}


@dataclass
class _Session:
    session_id: str
    generation: int
    original_user_message: str
    authority_origin: str
    step: int
    max_steps: int
    started_at_monotonic: float
    deadline_monotonic: float
    state: ContinuationState
    cancel_event: threading.Event
    history: list[ToolOutcome] = field(default_factory=list)
    context_history: list[ContextOutcome] = field(default_factory=list)
    authorized_resources: set[AuthorizedResource] = field(default_factory=set)
    discovered_resources: set[AuthorizedResource] = field(default_factory=set)
    seen_fingerprints: set[str] = field(default_factory=set)
    seen_context_fingerprints: set[str] = field(default_factory=set)
    pending_tool: ToolRequest | None = None
    pending_context: ContextRequest | None = None
    expected_model_origin: str = "user"
    sensitive_context_seen: bool = False
    allow_sensitive_outbound: bool = False


class ContinuationEngine:
    """Thread-safe owner of at most one bounded continuation session."""

    def __init__(
        self,
        *,
        max_steps: int = 6,
        max_duration_sec: float = 120.0,
        max_tool_result_chars: int = 8000,
        max_history: int = 6,
        clock: Callable[[], float] = time.monotonic,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._max_steps = max(1, min(_coerce_int(max_steps, 6), 100))
        self._max_duration_sec = max(
            0.001, min(_coerce_float(max_duration_sec, 120.0), 3600.0),
        )
        self._max_tool_result_chars = max(
            1, min(_coerce_int(max_tool_result_chars, 8000), 50_512),
        )
        self._max_history = max(1, min(_coerce_int(max_history, 6), 64))
        self._clock = clock
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._lock = threading.RLock()
        self._generation = 0
        self._active: _Session | None = None
        self._last_snapshot: ContinuationSnapshot | None = None
        self._shutdown = False

    def start(
        self,
        original_user_message: str,
        *,
        authority_origin: str,
        authorized_resources: Iterable[AuthorizedResource] = (),
        allow_sensitive_outbound: bool = False,
    ) -> ContinuationDecision:
        """Start a new session only from direct user authority.

        A successful start preempts the previous session.  A denied internal
        origin does not disturb an already-active user session.
        """

        with self._lock:
            if self._shutdown:
                return ContinuationDecision(DecisionKind.BLOCKED, "shutdown")
            if str(authority_origin or "").strip().lower() != "user":
                return ContinuationDecision(
                    DecisionKind.BLOCKED,
                    "direct_user_authority_required",
                    snapshot=self._snapshot_locked(self._active) if self._active else None,
                )
            message = str(original_user_message or "")
            if not message.strip():
                return ContinuationDecision(
                    DecisionKind.BLOCKED,
                    "empty_user_goal",
                    snapshot=self._snapshot_locked(self._active) if self._active else None,
                )

            preempted = self._active is not None
            if self._active is not None:
                self._active.cancel_event.set()
                self._active.state = ContinuationState.CANCELLED
                self._last_snapshot = self._snapshot_locked(self._active)

            self._generation += 1
            started = float(self._clock())
            resources = set(
                _normalize_resources(authorized_resources, limit=_MAX_RESOURCES),
            )
            session = _Session(
                session_id=str(self._id_factory()),
                generation=self._generation,
                original_user_message=message,
                authority_origin="user",
                step=0,
                max_steps=self._max_steps,
                started_at_monotonic=started,
                deadline_monotonic=started + self._max_duration_sec,
                state=ContinuationState.AWAITING_MODEL,
                cancel_event=threading.Event(),
                authorized_resources=resources,
                allow_sensitive_outbound=bool(allow_sensitive_outbound),
            )
            self._active = session
            return ContinuationDecision(
                DecisionKind.STARTED,
                "started_preempting_previous" if preempted else "started",
                snapshot=self._snapshot_locked(session),
            )

    def accept_initial_model_response(
        self,
        session_id: str,
        generation: int,
        response: Mapping[str, object],
    ) -> ContinuationDecision:
        """Accept the provider result directly attributable to the user goal."""

        return self.accept_model_response(
            session_id,
            generation,
            response,
            request_origin="user",
        )

    def accept_continuation_model_response(
        self,
        session_id: str,
        generation: int,
        response: Mapping[str, object],
    ) -> ContinuationDecision:
        """Accept a provider result after a matching read-only ToolOutcome."""

        return self.accept_model_response(
            session_id,
            generation,
            response,
            request_origin="tool_result",
        )

    def accept_context_request(
        self,
        session_id: str,
        generation: int,
        request: ContextRequest,
        *,
        request_origin: str,
    ) -> ContinuationDecision:
        """Accept one typed read-only dependency from the expected model turn."""

        with self._lock:
            session, rejected = self._validate_locked(session_id, generation)
            if rejected is not None:
                return rejected
            assert session is not None
            if session.state is not ContinuationState.AWAITING_MODEL:
                return self._ignored_locked(session, "unexpected_model_response_state")
            origin = str(request_origin or "").strip().casefold()
            if origin != session.expected_model_origin:
                return self._ignored_locked(session, "unexpected_model_response_origin")
            if session.authority_origin != "user" or not isinstance(request, ContextRequest):
                return self._stop_locked(
                    session,
                    "direct_user_context_authority_required",
                    blocked=True,
                )
            if session.step >= session.max_steps:
                return self._stop_locked(session, "max_steps_reached")
            if request.fingerprint in session.seen_context_fingerprints:
                return self._stop_locked(session, "repeated_context_dependency")

            session.step += 1
            session.seen_context_fingerprints.add(request.fingerprint)
            session.pending_context = request
            session.pending_tool = None
            session.state = ContinuationState.AWAITING_CONTEXT
            return ContinuationDecision(
                DecisionKind.RUN_CONTEXT,
                "run_read_only_context",
                snapshot=self._snapshot_locked(session),
                context_request=request,
            )

    def accept_context_outcome(
        self,
        session_id: str,
        generation: int,
        outcome: ContextOutcome,
    ) -> ContinuationDecision:
        """Record one bounded dependency result and continue the same goal."""

        with self._lock:
            session, rejected = self._validate_locked(session_id, generation)
            if rejected is not None:
                return rejected
            assert session is not None
            if session.state is not ContinuationState.AWAITING_CONTEXT:
                return self._ignored_locked(session, "unexpected_context_outcome_state")
            pending = session.pending_context
            if (
                pending is None
                or not isinstance(outcome, ContextOutcome)
                or outcome.kind != pending.kind
            ):
                return self._stop_locked(
                    session,
                    "context_outcome_mismatch",
                    blocked=True,
                )

            safe_outcome = ContextOutcome(
                outcome.kind,
                outcome.success,
                _truncate(outcome.status, 120),
                _truncate(outcome.provider_context, self._max_tool_result_chars),
                sensitivity=outcome.sensitivity,
            )
            session.context_history.append(safe_outcome)
            if len(session.context_history) > self._max_history:
                session.context_history = session.context_history[-self._max_history:]
            if safe_outcome.sensitivity in _SENSITIVE_CONTEXT:
                session.sensitive_context_seen = True
            session.pending_context = None
            session.expected_model_origin = "tool_result"
            session.state = ContinuationState.AWAITING_MODEL
            return ContinuationDecision(
                DecisionKind.CALL_PROVIDER,
                "continue_with_context_outcome",
                snapshot=self._snapshot_locked(session),
                provider_context=safe_outcome.provider_context,
                outcome=safe_outcome,
            )

    def accept_model_response(
        self,
        session_id: str,
        generation: int,
        response: Mapping[str, object],
        *,
        request_origin: str,
    ) -> ContinuationDecision:
        """Validate a model response and return the next non-recursive action."""

        with self._lock:
            session, rejected = self._validate_locked(session_id, generation)
            if rejected is not None:
                return rejected
            assert session is not None
            if session.state is not ContinuationState.AWAITING_MODEL:
                return self._ignored_locked(session, "unexpected_model_response_state")

            origin = str(request_origin or "").strip().lower()
            if origin != session.expected_model_origin:
                # A bare tool_result can neither start nor borrow a user turn.
                return self._ignored_locked(session, "unexpected_model_response_origin")
            if not isinstance(response, Mapping):
                return self._stop_locked(session, "malformed_model_response", blocked=True)

            command = str(response.get("command", "") or "").strip().lower()
            if _truthy(response.get("shutdown", False)):
                return self._stop_locked(session, "shutdown_not_allowed", blocked=True)

            messages = self._normalize_segments(response.get("segments", ()))
            if command in _FINAL_COMMANDS:
                session.state = ContinuationState.COMPLETED
                snapshot = self._snapshot_locked(session)
                self._last_snapshot = snapshot
                self._active = None
                return ContinuationDecision(
                    DecisionKind.FINAL,
                    "final_response",
                    snapshot=snapshot,
                    messages=messages,
                )

            if command not in AUTOMATIC_READ_ONLY_COMMANDS:
                return self._stop_locked(
                    session,
                    "state_changing_or_unknown_command",
                    blocked=True,
                )
            if session.step >= session.max_steps:
                return self._stop_locked(session, "max_steps_reached")
            if (
                command in _OUTBOUND_COMMANDS
                and session.sensitive_context_seen
                and not session.allow_sensitive_outbound
            ):
                return self._stop_locked(
                    session,
                    "sensitive_context_cannot_cross_to_web",
                    blocked=True,
                )

            request, error = self._build_tool_request(command, response)
            if error:
                return self._stop_locked(session, error, blocked=True)
            assert request is not None
            if request.fingerprint in session.seen_fingerprints:
                return self._stop_locked(session, "repeated_tool_cycle", blocked=True)
            resource_error = self._check_resource_locked(session, request)
            if resource_error:
                return self._stop_locked(session, resource_error, blocked=True)

            session.step += 1
            session.seen_fingerprints.add(request.fingerprint)
            session.pending_tool = request
            if messages:
                session.state = ContinuationState.AWAITING_STATUS
                return ContinuationDecision(
                    DecisionKind.STATUS,
                    "status_before_tool",
                    snapshot=self._snapshot_locked(session),
                    messages=messages,
                )

            session.state = ContinuationState.AWAITING_TOOL
            return ContinuationDecision(
                DecisionKind.RUN_TOOL,
                "run_read_only_tool",
                snapshot=self._snapshot_locked(session),
                tool_request=request,
            )

    def status_finished(self, session_id: str, generation: int) -> ContinuationDecision:
        """Advance a matching STATUS message to its already-validated tool."""

        with self._lock:
            session, rejected = self._validate_locked(session_id, generation)
            if rejected is not None:
                return rejected
            assert session is not None
            if session.state is not ContinuationState.AWAITING_STATUS:
                return self._ignored_locked(session, "unexpected_status_completion")
            if session.pending_tool is None:
                return self._stop_locked(session, "missing_pending_tool", blocked=True)
            session.state = ContinuationState.AWAITING_TOOL
            return ContinuationDecision(
                DecisionKind.RUN_TOOL,
                "run_read_only_tool",
                snapshot=self._snapshot_locked(session),
                tool_request=session.pending_tool,
            )

    def accept_tool_outcome(
        self,
        session_id: str,
        generation: int,
        outcome: ToolOutcome,
    ) -> ContinuationDecision:
        """Record a matching tool observation and request the next provider turn."""

        with self._lock:
            session, rejected = self._validate_locked(session_id, generation)
            if rejected is not None:
                return rejected
            assert session is not None
            if session.state is not ContinuationState.AWAITING_TOOL:
                return self._ignored_locked(session, "unexpected_tool_outcome_state")
            pending = session.pending_tool
            if pending is None:
                return self._stop_locked(session, "missing_pending_tool", blocked=True)
            if not isinstance(outcome, ToolOutcome):
                return self._stop_locked(session, "malformed_tool_outcome", blocked=True)
            if outcome.tool != pending.command:
                return self._stop_locked(session, "tool_outcome_mismatch", blocked=True)

            safe_outcome = ToolOutcome(
                tool=outcome.tool,
                success=outcome.success,
                summary=_truncate(outcome.summary, min(512, self._max_tool_result_chars)),
                provider_context=_truncate(
                    outcome.provider_context or outcome.summary,
                    self._max_tool_result_chars,
                ),
                sensitivity=outcome.sensitivity,
                continuation_allowed=outcome.continuation_allowed,
                discovered_resources=tuple(
                    outcome.discovered_resources[:_MAX_DISCOVERED_PER_OUTCOME]
                ),
            )
            session.history.append(safe_outcome)
            if len(session.history) > self._max_history:
                session.history = session.history[-self._max_history:]

            if safe_outcome.sensitivity in _SENSITIVE_CONTEXT:
                session.sensitive_context_seen = True
            if (
                safe_outcome.success
                and safe_outcome.tool == "search_web"
                and safe_outcome.discovered_resources
            ):
                for item in safe_outcome.discovered_resources:
                    if item.kind == "url" and len(session.discovered_resources) < _MAX_RESOURCES:
                        session.discovered_resources.add(item)

            if not safe_outcome.continuation_allowed:
                return self._stop_locked(
                    session,
                    "tool_outcome_disallows_continuation",
                    outcome=safe_outcome,
                )
            if safe_outcome.sensitivity in _NEVER_CONTINUE_SENSITIVITY:
                return self._stop_locked(
                    session,
                    "tool_outcome_too_sensitive",
                    blocked=True,
                    outcome=safe_outcome,
                )

            session.pending_tool = None
            session.expected_model_origin = "tool_result"
            session.state = ContinuationState.AWAITING_MODEL
            return ContinuationDecision(
                DecisionKind.CALL_PROVIDER,
                "continue_with_tool_outcome",
                snapshot=self._snapshot_locked(session),
                provider_context=safe_outcome.provider_context,
                outcome=safe_outcome,
            )

    def provider_failed(
        self,
        session_id: str,
        generation: int,
        reason: str = "provider_error",
    ) -> ContinuationDecision:
        """Close a matching session after a provider failure."""

        with self._lock:
            session, rejected = self._validate_locked(session_id, generation)
            if rejected is not None:
                return rejected
            assert session is not None
            return self._stop_locked(session, str(reason or "provider_error")[:120])

    def cancel_active(self, reason: str = "cancelled") -> ContinuationDecision:
        """Cancel the current session and invalidate all outstanding callbacks."""

        with self._lock:
            if self._active is None:
                return ContinuationDecision(DecisionKind.IGNORED, "no_active_session")
            session = self._active
            session.cancel_event.set()
            session.state = ContinuationState.CANCELLED
            snapshot = self._snapshot_locked(session)
            self._last_snapshot = snapshot
            self._active = None
            return ContinuationDecision(
                DecisionKind.STOPPED,
                str(reason or "cancelled")[:120],
                snapshot=snapshot,
            )

    def shutdown(self) -> ContinuationDecision:
        """Idempotently stop the owner and reject all future sessions."""

        with self._lock:
            if self._shutdown:
                return ContinuationDecision(
                    DecisionKind.IGNORED,
                    "already_shutdown",
                    snapshot=self._last_snapshot,
                )
            self._shutdown = True
            if self._active is None:
                return ContinuationDecision(DecisionKind.STOPPED, "shutdown")
            session = self._active
            session.cancel_event.set()
            session.state = ContinuationState.CANCELLED
            snapshot = self._snapshot_locked(session)
            self._last_snapshot = snapshot
            self._active = None
            return ContinuationDecision(
                DecisionKind.STOPPED,
                "shutdown",
                snapshot=snapshot,
            )

    def active_snapshot(self) -> ContinuationSnapshot | None:
        with self._lock:
            return self._snapshot_locked(self._active) if self._active is not None else None

    def last_snapshot(self) -> ContinuationSnapshot | None:
        with self._lock:
            return self._last_snapshot

    def is_current(self, session_id: str, generation: int) -> bool:
        """Return whether a callback still owns the active generation."""

        with self._lock:
            session = self._active
            return bool(
                not self._shutdown
                and session is not None
                and session.session_id == session_id
                and session.generation == generation
                and not session.cancel_event.is_set()
                and float(self._clock()) < session.deadline_monotonic
            )

    def cancel_requested(self, session_id: str, generation: int) -> bool:
        """Cancellation callback suitable for provider/tool worker adapters."""

        return not self.is_current(session_id, generation)

    def _validate_locked(
        self,
        session_id: str,
        generation: int,
    ) -> tuple[_Session | None, ContinuationDecision | None]:
        if self._shutdown:
            return None, ContinuationDecision(DecisionKind.IGNORED, "shutdown")
        session = self._active
        if session is None:
            return None, ContinuationDecision(DecisionKind.IGNORED, "no_active_session")
        if session.session_id != session_id or session.generation != generation:
            return None, self._ignored_locked(session, "stale_session_callback")
        if session.cancel_event.is_set():
            return None, self._stop_locked(session, "cancelled")
        if float(self._clock()) >= session.deadline_monotonic:
            return None, self._stop_locked(session, "deadline_exceeded")
        return session, None

    def _build_tool_request(
        self,
        command: str,
        response: Mapping[str, object],
    ) -> tuple[ToolRequest | None, str]:
        fields = _TOOL_ARGUMENT_FIELDS[command]
        arguments: dict[str, object] = {}
        for name in fields:
            if name not in response:
                continue
            value = _normalize_argument(name, response.get(name))
            if value not in (None, ""):
                arguments[name] = value
        required = _REQUIRED_ARGUMENTS.get(command)
        if required and arguments.get(required) in (None, ""):
            return None, f"missing_required_argument:{required}"
        arguments_json = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = f"{command}:{arguments_json}"
        return ToolRequest(command, arguments_json, fingerprint), ""

    @staticmethod
    def _check_resource_locked(session: _Session, request: ToolRequest) -> str:
        requirement = _RESOURCE_ARGUMENTS.get(request.command)
        if requirement is None:
            return ""
        field_name, resource_kind = requirement
        raw_value = request.arguments().get(field_name, "")
        resource = AuthorizedResource(resource_kind, str(raw_value))
        allowed = session.authorized_resources | session.discovered_resources
        if resource not in allowed:
            return f"resource_not_authorized:{resource_kind}"
        return ""

    def _normalize_segments(self, raw_segments: object) -> tuple[MessageSegment, ...]:
        if not isinstance(raw_segments, (list, tuple)):
            return ()
        remaining = self._max_tool_result_chars
        segments: list[MessageSegment] = []
        for raw in raw_segments[:16]:
            if remaining <= 0:
                break
            if not isinstance(raw, Mapping):
                continue
            text = str(raw.get("text", "") or "").strip()
            if not text:
                continue
            text = _truncate(text, remaining)
            remaining -= len(text)
            pause = max(0.0, min(_coerce_float(raw.get("pause", 0.0), 0.0), 1.2))
            segments.append(MessageSegment(text, pause))
        return tuple(segments)

    def _stop_locked(
        self,
        session: _Session,
        reason: str,
        *,
        blocked: bool = False,
        outcome: ToolOutcome | ContextOutcome | None = None,
    ) -> ContinuationDecision:
        session.cancel_event.set()
        session.state = ContinuationState.STOPPED
        snapshot = self._snapshot_locked(session)
        self._last_snapshot = snapshot
        if self._active is session:
            self._active = None
        return ContinuationDecision(
            DecisionKind.BLOCKED if blocked else DecisionKind.STOPPED,
            str(reason or "stopped")[:160],
            snapshot=snapshot,
            outcome=outcome,
        )

    def _ignored_locked(self, session: _Session, reason: str) -> ContinuationDecision:
        return ContinuationDecision(
            DecisionKind.IGNORED,
            reason,
            snapshot=self._snapshot_locked(session),
        )

    @staticmethod
    def _snapshot_locked(session: _Session | None) -> ContinuationSnapshot | None:
        if session is None:
            return None
        return ContinuationSnapshot(
            session_id=session.session_id,
            generation=session.generation,
            original_user_message=session.original_user_message,
            authority_origin=session.authority_origin,
            step=session.step,
            max_steps=session.max_steps,
            started_at_monotonic=session.started_at_monotonic,
            deadline_monotonic=session.deadline_monotonic,
            state=session.state,
            cancelled=session.cancel_event.is_set(),
            history=tuple(session.history),
            context_history=tuple(session.context_history),
            authorized_resources=tuple(sorted(session.authorized_resources)),
            discovered_resources=tuple(sorted(session.discovered_resources)),
            sensitive_context_seen=session.sensitive_context_seen,
            allow_sensitive_outbound=session.allow_sensitive_outbound,
        )


def _canonical_resource(kind: object, value: object) -> tuple[str, str]:
    normalized_kind = str(kind or "").strip().lower()
    normalized_value = str(value or "").strip()[:_MAX_ARGUMENT_CHARS]
    if normalized_kind == "url":
        try:
            parsed = urlsplit(normalized_value)
            scheme = parsed.scheme.lower()
            host = (parsed.hostname or "").lower()
            if host:
                netloc = host
                if parsed.port is not None:
                    netloc = f"{host}:{parsed.port}"
                path = parsed.path or "/"
                normalized_value = urlunsplit(
                    (scheme, netloc, path, parsed.query, ""),
                )
        except ValueError:
            pass
    elif normalized_kind == "process":
        normalized_value = normalized_value.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    elif normalized_kind == "path":
        normalized_value = normalized_value.replace("\\", "/")
        if len(normalized_value) > 3:
            normalized_value = normalized_value.rstrip("/")
        if len(normalized_value) >= 2 and normalized_value[1] == ":":
            normalized_value = normalized_value.casefold()
    return normalized_kind, normalized_value


def _normalize_resources(
    resources: Iterable[AuthorizedResource],
    *,
    limit: int = _MAX_DISCOVERED_PER_OUTCOME,
) -> tuple[AuthorizedResource, ...]:
    normalized: list[AuthorizedResource] = []
    for item in tuple(resources or ())[: max(0, int(limit))]:
        if isinstance(item, AuthorizedResource):
            candidate = item
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            candidate = AuthorizedResource(str(item[0]), str(item[1]))
        else:
            continue
        if candidate.kind and candidate.value:
            normalized.append(candidate)
    return tuple(normalized)


def _normalize_argument(name: str, value: object) -> object:
    if value is None:
        return None
    if name == "limit":
        return max(1, min(_coerce_int(value, 5), 100))
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    return str(value)[:_MAX_ARGUMENT_CHARS].strip()


def _truncate(value: object, limit: int) -> str:
    text = str(value or "")
    safe_limit = max(0, int(limit))
    if len(text) <= safe_limit:
        return text
    if safe_limit <= 0:
        return ""
    if safe_limit == 1:
        return "…"
    return text[: safe_limit - 1].rstrip() + "…"


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "yes", "true", "on"}


def _coerce_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _coerce_float(value: object, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    if result != result or result in (float("inf"), float("-inf")):
        return float(default)
    return result
