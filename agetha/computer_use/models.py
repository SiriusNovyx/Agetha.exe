"""Immutable, privacy-minimized records for Computer Use Lite.

The records in this module deliberately contain no platform or provider code.
Model output is parsed into one strict :class:`ComputerAction` before any
policy or executor sees it.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Sequence

from agetha.platform.process_awareness import ProcessIdentity, identities_match


MAX_TEXT_CHARS = 240
MAX_CONTROLS = 96
MAX_HOTKEY_KEYS = 4
MAX_RECENT_ACTIONS = 4

_CONTROL_ID_RE = re.compile(r"^(?:acc|ocr):[1-9][0-9]{0,3}$")
_PAYLOAD_REF_RE = re.compile(r"^(?:payload:)?user_text_[1-9][0-9]{0,3}$")
_OBSERVATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:bearer|token|password|passwd|api[_ -]?key)\s*[:=]\s*\S+"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)


def safe_text(value: object, *, maximum: int = MAX_TEXT_CHARS) -> str:
    """Return a bounded one-line string with obvious credentials removed."""

    text = " ".join(str(value or "").replace("\x00", " ").split())
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text[:maximum]


def clamp_confidence(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return min(1.0, max(0.0, number))


def normalize_process_name(value: object) -> str:
    text = safe_text(value, maximum=120).replace("/", "\\")
    return text.rsplit("\\", 1)[-1].casefold()


def process_identities_match(expected: ProcessIdentity, current: ProcessIdentity) -> bool:
    """Computer effects require PID, basename, and both creation timestamps."""

    return identities_match(expected, current, strict=True)


def normalize_payload_ref(value: object) -> str:
    if not isinstance(value, str) or not _PAYLOAD_REF_RE.fullmatch(value):
        raise ValueError("invalid payload reference")
    return value.removeprefix("payload:")


class ControlSource(str, Enum):
    ACCESSIBILITY = "accessibility"
    OCR = "ocr"


class ComputerActionKind(str, Enum):
    OBSERVE_AGAIN = "observe_again"
    MOVE_POINTER = "move_pointer"
    CLICK_CONTROL = "click_control"
    CLICK_POINT = "click_point"
    DOUBLE_CLICK_CONTROL = "double_click_control"
    SCROLL = "scroll"
    TYPE_PAYLOAD = "type_payload"
    KEYPRESS = "keypress"
    HOTKEY = "hotkey"
    WAIT = "wait"
    FOCUS_WINDOW = "focus_window"
    FINISH = "finish"
    BLOCKED = "blocked"


class PolicyDisposition(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REOBSERVE = "reobserve"
    HANDOFF = "handoff"


class PolicyCode(str, Enum):
    ALLOWED = "allowed"
    FEATURE_DISABLED = "feature_disabled"
    USER_AUTHORITY_REQUIRED = "user_authority_required"
    SESSION_MISMATCH = "session_mismatch"
    CANCELLED = "cancelled"
    SHUTDOWN = "shutdown"
    EXPIRED = "expired"
    STEP_LIMIT = "step_limit"
    TARGET_CHANGED = "target_changed"
    TARGET_UNAUTHORIZED = "target_unauthorized"
    TARGET_NOT_FOREGROUND = "target_not_foreground"
    OUT_OF_BOUNDS = "out_of_bounds"
    CONTROL_NOT_FOUND = "control_not_found"
    LOW_CONFIDENCE = "low_confidence"
    SENSITIVE_HANDOFF = "sensitive_handoff"
    PAYLOAD_UNAUTHORIZED = "payload_unauthorized"
    TYPING_NOT_AUTHORIZED = "typing_not_authorized"
    KEY_NOT_ALLOWED = "key_not_allowed"
    SUBMIT_NOT_AUTHORIZED = "submit_not_authorized"
    HOTKEY_NOT_ALLOWED = "hotkey_not_allowed"
    FOCUS_NOT_AUTHORIZED = "focus_not_authorized"
    FOCUS_RESTRICTED = "focus_restricted"
    INVALID_ACTION = "invalid_action"


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    FINISHED = "finished"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    SHUTDOWN = "shutdown"
    TARGET_CHANGED = "target_changed"
    POLICY_DENIED = "policy_denied"
    FAILED = "failed"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    FAILED = "failed"
    TARGET_CHANGED = "target_changed"
    CANCELLED = "cancelled"


class SessionState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (self.left, self.top, self.width, self.height)
        ):
            raise TypeError("rectangle coordinates and dimensions must be integers")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("rectangle dimensions must be positive")

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)

    def contains_point(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def contains_rect(self, other: "Rect") -> bool:
        return (
            self.left <= other.left
            and self.top <= other.top
            and other.right <= self.right
            and other.bottom <= self.bottom
        )


@dataclass(frozen=True, slots=True)
class WindowIdentity:
    hwnd: int
    process: ProcessIdentity
    bounds: Rect
    title: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.hwnd, bool) or not isinstance(self.hwnd, int) or self.hwnd <= 0:
            raise ValueError("window handle must be a positive integer")
        object.__setattr__(self, "title", safe_text(self.title))

    def matches(self, other: "WindowIdentity", *, require_same_bounds: bool = True) -> bool:
        return (
            isinstance(other, WindowIdentity)
            and self.hwnd == other.hwnd
            and process_identities_match(self.process, other.process)
            and (not require_same_bounds or self.bounds == other.bounds)
        )


@dataclass(frozen=True, slots=True)
class ObservedControl:
    control_id: str
    source: ControlSource
    label: str
    bounds: Rect
    confidence: float
    role: str = ""
    state: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        if not _CONTROL_ID_RE.fullmatch(self.control_id):
            raise ValueError("invalid temporary control identifier")
        if not isinstance(self.source, ControlSource):
            object.__setattr__(self, "source", ControlSource(self.source))
        object.__setattr__(self, "label", safe_text(self.label))
        object.__setattr__(self, "role", safe_text(self.role, maximum=80))
        object.__setattr__(self, "state", safe_text(self.state, maximum=80))
        object.__setattr__(self, "confidence", clamp_confidence(self.confidence))


@dataclass(frozen=True, slots=True)
class ComputerObservation:
    observation_id: str
    target: WindowIdentity | None
    foreground: bool
    screen_bounds: Rect
    cursor: tuple[int, int]
    controls: tuple[ObservedControl, ...] = ()
    previous_result: str = ""
    process_alive: bool = True
    captured_at: float = 0.0
    accessibility_available: bool = False

    def __post_init__(self) -> None:
        if not _OBSERVATION_ID_RE.fullmatch(self.observation_id):
            raise ValueError("invalid observation identifier")
        if len(self.cursor) != 2:
            raise ValueError("cursor must contain x and y")
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in self.cursor
        ):
            raise TypeError("cursor coordinates must be integers")
        cursor = (self.cursor[0], self.cursor[1])
        object.__setattr__(self, "cursor", cursor)
        controls = tuple(self.controls)
        if len(controls) > MAX_CONTROLS:
            raise ValueError("observation has too many controls")
        if len({control.control_id for control in controls}) != len(controls):
            raise ValueError("control identifiers must be unique per observation")
        object.__setattr__(self, "controls", controls)
        object.__setattr__(self, "previous_result", safe_text(self.previous_result))
        captured = float(self.captured_at)
        if not math.isfinite(captured) or captured < 0:
            raise ValueError("capture time must be finite and non-negative")
        object.__setattr__(self, "captured_at", captured)

    def control(self, control_id: str | None) -> ObservedControl | None:
        if not control_id:
            return None
        return next((item for item in self.controls if item.control_id == control_id), None)


_COMMON_ACTION_FIELDS = frozenset(
    {"action", "observation_id", "expected_result", "reason", "confidence"}
)
_ACTION_FIELDS: Mapping[ComputerActionKind, frozenset[str]] = MappingProxyType(
    {
        ComputerActionKind.OBSERVE_AGAIN: frozenset(),
        ComputerActionKind.MOVE_POINTER: frozenset({"x", "y"}),
        ComputerActionKind.CLICK_CONTROL: frozenset({"target_id"}),
        ComputerActionKind.CLICK_POINT: frozenset({"x", "y"}),
        ComputerActionKind.DOUBLE_CLICK_CONTROL: frozenset({"target_id"}),
        ComputerActionKind.SCROLL: frozenset({"amount", "x", "y"}),
        ComputerActionKind.TYPE_PAYLOAD: frozenset({"payload_ref"}),
        ComputerActionKind.KEYPRESS: frozenset({"key"}),
        ComputerActionKind.HOTKEY: frozenset({"keys"}),
        ComputerActionKind.WAIT: frozenset({"amount"}),
        ComputerActionKind.FOCUS_WINDOW: frozenset(),
        ComputerActionKind.FINISH: frozenset(),
        ComputerActionKind.BLOCKED: frozenset(),
    }
)


@dataclass(frozen=True, slots=True)
class ComputerAction:
    action: ComputerActionKind
    observation_id: str
    target_id: str | None = None
    x: int | None = None
    y: int | None = None
    amount: int | None = None
    key: str | None = None
    keys: tuple[str, ...] = ()
    payload_ref: str | None = None
    expected_result: str = ""
    reason: str = ""
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.action, ComputerActionKind):
            object.__setattr__(self, "action", ComputerActionKind(self.action))
        if not _OBSERVATION_ID_RE.fullmatch(self.observation_id):
            raise ValueError("invalid action observation identifier")
        if self.target_id is not None and not _CONTROL_ID_RE.fullmatch(self.target_id):
            raise ValueError("invalid target control identifier")
        if self.payload_ref is not None:
            object.__setattr__(self, "payload_ref", normalize_payload_ref(self.payload_ref))
        for field_name in ("x", "y", "amount"):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int)
            ):
                raise TypeError(f"{field_name} must be an integer")
        normalized_keys = tuple(safe_text(item, maximum=32).casefold() for item in self.keys)
        if any(not item for item in normalized_keys) or len(normalized_keys) > MAX_HOTKEY_KEYS:
            raise ValueError("invalid hotkey sequence")
        object.__setattr__(self, "keys", normalized_keys)
        if self.key is not None:
            key = safe_text(self.key, maximum=32).casefold()
            if not key:
                raise ValueError("key must not be empty")
            object.__setattr__(self, "key", key)
        object.__setattr__(self, "expected_result", safe_text(self.expected_result))
        object.__setattr__(self, "reason", safe_text(self.reason))
        object.__setattr__(self, "confidence", clamp_confidence(self.confidence))
        self._validate_shape()

    @classmethod
    def parse(cls, raw: str | bytes | Mapping[str, object]) -> "ComputerAction":
        """Parse exactly one action and reject every unknown executable field."""

        data: object
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="strict")
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("planner response is not valid JSON") from exc
        else:
            data = raw
        if not isinstance(data, Mapping):
            raise ValueError("planner response must be one JSON object")
        if not isinstance(data.get("action"), str):
            raise ValueError("planner response requires an action string")
        try:
            kind = ComputerActionKind(data["action"])
        except ValueError as exc:
            raise ValueError("planner returned an unsupported action") from exc

        allowed = _COMMON_ACTION_FIELDS | _ACTION_FIELDS[kind]
        unknown = set(data) - allowed
        if unknown:
            raise ValueError("planner response contains unsupported fields")
        observation_id = data.get("observation_id")
        if not isinstance(observation_id, str):
            raise ValueError("planner response requires observation_id")

        def integer(name: str, *, required: bool = False) -> int | None:
            value = data.get(name)
            if value is None and not required:
                return None
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
            return value

        target_id = data.get("target_id")
        if target_id is not None and not isinstance(target_id, str):
            raise ValueError("target_id must be a string")
        payload_ref = data.get("payload_ref")
        if payload_ref is not None and not isinstance(payload_ref, str):
            raise ValueError("payload_ref must be a string")
        key = data.get("key")
        if key is not None and not isinstance(key, str):
            raise ValueError("key must be a string")
        keys_value = data.get("keys", ())
        if isinstance(keys_value, (str, bytes)) or not isinstance(keys_value, Sequence):
            raise ValueError("keys must be an array")
        if any(not isinstance(item, str) for item in keys_value):
            raise ValueError("hotkey keys must be strings")
        confidence = data.get("confidence", 0.0)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("confidence must be a number")

        action = cls(
            action=kind,
            observation_id=observation_id,
            target_id=target_id,
            x=integer("x"),
            y=integer("y"),
            amount=integer("amount"),
            key=key,
            keys=tuple(keys_value),
            payload_ref=payload_ref,
            expected_result=_string_field(data, "expected_result"),
            reason=_string_field(data, "reason"),
            confidence=confidence,
        )
        return action

    def _validate_shape(self) -> None:
        kind = self.action
        provided: set[str] = set()
        if self.target_id is not None:
            provided.add("target_id")
        if self.x is not None:
            provided.add("x")
        if self.y is not None:
            provided.add("y")
        if self.amount is not None:
            provided.add("amount")
        if self.key is not None:
            provided.add("key")
        if self.keys:
            provided.add("keys")
        if self.payload_ref is not None:
            provided.add("payload_ref")
        if provided - _ACTION_FIELDS[kind]:
            raise ValueError(f"{kind.value} contains fields for another action")
        if kind in {ComputerActionKind.MOVE_POINTER, ComputerActionKind.CLICK_POINT}:
            if self.x is None or self.y is None:
                raise ValueError(f"{kind.value} requires x and y")
        elif kind in {ComputerActionKind.CLICK_CONTROL, ComputerActionKind.DOUBLE_CLICK_CONTROL}:
            if self.target_id is None:
                raise ValueError(f"{kind.value} requires target_id")
        elif kind is ComputerActionKind.SCROLL:
            if self.amount is None or self.amount == 0 or abs(self.amount) > 10_000:
                raise ValueError("scroll requires a bounded non-zero amount")
            if (self.x is None) != (self.y is None):
                raise ValueError("scroll coordinates require both x and y")
        elif kind is ComputerActionKind.TYPE_PAYLOAD:
            if self.payload_ref is None:
                raise ValueError("type_payload requires payload_ref")
        elif kind is ComputerActionKind.KEYPRESS:
            if self.key is None:
                raise ValueError("keypress requires key")
        elif kind is ComputerActionKind.HOTKEY:
            if len(self.keys) < 2:
                raise ValueError("hotkey requires at least two keys")
        elif kind is ComputerActionKind.WAIT:
            if self.amount is None or not 1 <= self.amount <= 10_000:
                raise ValueError("wait amount must be 1..10000 milliseconds")


def _string_field(data: Mapping[str, object], name: str) -> str:
    value = data.get(name, "")
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    disposition: PolicyDisposition
    code: PolicyCode
    safe_reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.disposition is PolicyDisposition.ALLOW


@dataclass(frozen=True, slots=True)
class LiveTargetState:
    target: WindowIdentity | None
    is_window: bool
    foreground: bool
    authorized: bool


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    status: ExecutionStatus
    action: ComputerActionKind
    safe_reason: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status in {ExecutionStatus.SUCCESS, ExecutionStatus.FINISHED}


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: VerificationStatus
    safe_reason: str = ""


@dataclass(frozen=True, slots=True)
class PlannerRequest:
    request_id: str
    session_id: str
    generation: int
    observation_id: str
    step: int
    goal: str
    payload_refs: tuple[str, ...]
    recent_actions: tuple[str, ...]
    failure_reason: str
    allowed_actions: tuple[str, ...]
    recovery: bool = False

    def __post_init__(self) -> None:
        if not _OBSERVATION_ID_RE.fullmatch(self.request_id):
            raise ValueError("invalid request identifier")
        if not _SESSION_ID_RE.fullmatch(self.session_id):
            raise ValueError("invalid session identifier")
        if not _OBSERVATION_ID_RE.fullmatch(self.observation_id):
            raise ValueError("invalid observation identifier")
        if self.generation <= 0 or self.step < 0:
            raise ValueError("invalid planner request generation or step")
        object.__setattr__(self, "goal", safe_text(self.goal, maximum=600))
        refs = tuple(normalize_payload_ref(item) for item in self.payload_refs)
        object.__setattr__(self, "payload_refs", refs)
        object.__setattr__(
            self,
            "recent_actions",
            tuple(safe_text(item) for item in self.recent_actions[-MAX_RECENT_ACTIONS:]),
        )
        object.__setattr__(self, "failure_reason", safe_text(self.failure_reason))
        object.__setattr__(self, "allowed_actions", tuple(self.allowed_actions))

    def as_payload(self, observation: ComputerObservation) -> Mapping[str, object]:
        """Build the complete, compact payload for a structured planner call."""

        controls = tuple(
            {
                "id": control.control_id,
                "source": control.source.value,
                "label": control.label,
                "bounds": (
                    control.bounds.left,
                    control.bounds.top,
                    control.bounds.width,
                    control.bounds.height,
                ),
                "confidence": control.confidence,
                "role": control.role,
                "state": control.state,
            }
            for control in observation.controls
        )
        target_name = observation.target.process.name if observation.target else "unavailable"
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "generation": self.generation,
            "observation_id": self.observation_id,
            "step": self.step,
            "goal": self.goal,
            "target_process": target_name,
            "foreground": observation.foreground,
            "controls": controls,
            "previous": observation.previous_result,
            "payload_refs": self.payload_refs,
            "recent_actions": self.recent_actions,
            "failure_reason": self.failure_reason,
            "allowed_actions": self.allowed_actions,
            "recovery": self.recovery,
        }


@dataclass(frozen=True, slots=True)
class PlannedAction:
    action: ComputerAction
    request_id: str
    session_id: str
    generation: int
    observation_id: str
    recovery: bool


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    state: SessionState
    session_id: str = ""
    generation: int = 0
    step: int = 0
    max_steps: int = 0
    target_process: str = ""
    last_action: str = ""
    last_result: str = ""
    recovery_calls: int = 0


@dataclass(frozen=True, slots=True)
class SessionOutcome:
    state: SessionState
    session_id: str
    steps: int
    recovery_calls: int
    safe_reason: str = ""


@dataclass(frozen=True, slots=True)
class ComputerUseAuditEvent:
    """Bounded effect metadata; it can never contain OCR or payload values."""

    session_id: str
    step: int
    action: str
    target_process: str
    policy_result: str
    result: str
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", safe_text(self.session_id, maximum=80))
        object.__setattr__(self, "step", max(0, int(self.step)))
        object.__setattr__(self, "action", safe_text(self.action, maximum=40))
        object.__setattr__(
            self,
            "target_process",
            normalize_process_name(self.target_process)[:120],
        )
        object.__setattr__(
            self, "policy_result", safe_text(self.policy_result, maximum=80),
        )
        object.__setattr__(self, "result", safe_text(self.result, maximum=80))
        object.__setattr__(self, "confidence", clamp_confidence(self.confidence))


def immutable_payload_names(payloads: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(sorted(normalize_payload_ref(item) for item in payloads))
