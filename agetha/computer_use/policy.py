"""Pure, deterministic safety policy for Computer Use Lite actions."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .models import (
    ComputerAction,
    ComputerActionKind,
    ComputerObservation,
    PolicyCode,
    PolicyDecision,
    PolicyDisposition,
    WindowIdentity,
    normalize_process_name,
    safe_text,
)


_SAFE_KEYS = frozenset(
    {
        "backspace",
        "down",
        "end",
        "escape",
        "home",
        "left",
        "pagedown",
        "pageup",
        "right",
        "space",
        "tab",
        "up",
    }
)
_SUBMIT_KEYS = frozenset({"enter", "return"})
_SAFE_HOTKEYS = frozenset(
    {
        ("ctrl", "a"),
        ("ctrl", "c"),
        ("ctrl", "f"),
        ("ctrl", "z"),
        ("ctrl", "shift", "z"),
        ("shift", "tab"),
    }
)
_SENSITIVE_TERMS = (
    "password",
    "passwd",
    "passkey",
    "credential",
    "pin",
    "2fa",
    "mfa",
    "recovery code",
    "api key",
    "captcha",
    "credit card",
    "debit card",
    "banking",
    "bank account",
    "payment",
    "password manager",
    "secure desktop",
    "user account control",
    "uac",
    "administrator",
    "elevated",
    "admin privileges",
    "security software",
    "windows security",
    "bitwarden",
    "keepass",
    "1password",
    "lastpass",
    "dashlane",
    "proton pass",
    "credential manager",
    "authenticator",
)

_HIGH_IMPACT_TERMS = (
    "delete",
    "erase",
    "wipe",
    "factory reset",
    "reset this pc",
    "reset pc",
    "format drive",
    "uninstall",
    "remove this app",
    "remove app",
    "installer",
    "install",
    "setup",
    "install software",
    "run as administrator",
    "administrator privileges",
    "elevate privileges",
    "change permissions",
    "permission settings",
    "change account",
    "account settings",
    "manage account",
    "security settings",
    "disable security",
    "turn off protection",
    "change password",
    "delete account",
    "bank transfer",
    "wire transfer",
    "transfer funds",
    "transfer money",
    "send money",
    "pay now",
    "buy now",
    "subscribe",
    "purchase",
    "checkout",
    "confirm order",
    "place order",
    "order confirmation",
)


@dataclass(frozen=True, slots=True)
class PolicyContext:
    enabled: bool
    explicit_user_activation: bool
    request_origin: str
    session_id: str
    expected_session_id: str
    generation: int
    expected_generation: int
    now: float
    deadline: float
    step: int
    max_steps: int
    cancelled: bool
    shutdown: bool
    expected_target: WindowIdentity | None
    allowed_processes: frozenset[str] = field(default_factory=frozenset)
    payload_refs: frozenset[str] = field(default_factory=frozenset)
    confidence_min: float = 0.65
    typing_authorized: bool = False
    submit_authorized: bool = False
    focus_authorized: bool = False
    presentation_restricted: bool = False
    fullscreen_restricted: bool = False
    goal_summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "allowed_processes",
            frozenset(normalize_process_name(item) for item in self.allowed_processes),
        )
        object.__setattr__(self, "payload_refs", frozenset(self.payload_refs))
        object.__setattr__(self, "goal_summary", safe_text(self.goal_summary, maximum=600))
        confidence = float(self.confidence_min)
        if not math.isfinite(confidence):
            confidence = 1.0
        object.__setattr__(self, "confidence_min", min(1.0, max(0.0, confidence)))


class ComputerUsePolicy:
    """Validate one planner action without performing any side effect."""

    def preflight_observation(
        self,
        observation: ComputerObservation,
        context: PolicyContext,
    ) -> PolicyDecision:
        """Reject sensitive observations before they cross to a planner."""

        probe = ComputerAction(
            ComputerActionKind.FINISH,
            observation.observation_id,
            confidence=1.0,
        )
        basic = self._session_gate(probe, observation, context)
        if basic is not None:
            return basic
        target = observation.target
        assert target is not None
        visible_text = " ".join(
            part
            for control in observation.controls
            for part in (control.label, control.role, control.state)
            if part
        )
        if self._is_sensitive(target, visible_text):
            return self._handoff(
                PolicyCode.SENSITIVE_HANDOFF,
                "sensitive target requires user handoff",
            )
        if self._is_high_impact(context.goal_summary) or self._is_high_impact(visible_text):
            return self._handoff(
                PolicyCode.SENSITIVE_HANDOFF,
                "high-impact action requires user handoff",
            )
        return self._allow()

    def evaluate(
        self,
        action: ComputerAction,
        observation: ComputerObservation,
        context: PolicyContext,
    ) -> PolicyDecision:
        preflight = self.preflight_observation(observation, context)
        if not preflight.allowed:
            return preflight

        if action.action in {
            ComputerActionKind.OBSERVE_AGAIN,
            ComputerActionKind.WAIT,
            ComputerActionKind.BLOCKED,
        }:
            return self._allow()

        target = observation.target
        assert target is not None
        sensitive_control = observation.control(action.target_id)
        # Computer Use Lite deliberately does not automate destructive,
        # installation, privilege/account/security, or financial-transfer
        # workflows.  Planner prose is untrusted, so this gate uses only the
        # user-authorized goal and the currently selected observed control.
        selected_text = sensitive_control.label if sensitive_control else ""
        if action.action in {
            ComputerActionKind.CLICK_CONTROL,
            ComputerActionKind.DOUBLE_CLICK_CONTROL,
        } and self._is_high_impact(selected_text):
            return self._handoff(
                PolicyCode.SENSITIVE_HANDOFF,
                "high-impact action requires user handoff",
            )

        if action.action is ComputerActionKind.FINISH:
            return self._allow()

        if action.confidence < context.confidence_min:
            return self._reobserve(PolicyCode.LOW_CONFIDENCE, "action confidence is below threshold")

        if action.action is ComputerActionKind.FOCUS_WINDOW:
            if not context.focus_authorized:
                return self._deny(PolicyCode.FOCUS_NOT_AUTHORIZED, "focus was not authorized")
            if context.presentation_restricted or context.fullscreen_restricted:
                return self._deny(PolicyCode.FOCUS_RESTRICTED, "focus is restricted by current presence state")
            return self._allow()

        if not observation.foreground:
            return self._reobserve(PolicyCode.TARGET_NOT_FOREGROUND, "authorized target is no longer foreground")

        if action.action in {
            ComputerActionKind.CLICK_CONTROL,
            ComputerActionKind.DOUBLE_CLICK_CONTROL,
        }:
            control = observation.control(action.target_id)
            if control is None or not control.enabled:
                return self._reobserve(PolicyCode.CONTROL_NOT_FOUND, "temporary control is absent or disabled")
            if not target.bounds.contains_rect(control.bounds) or not observation.screen_bounds.contains_rect(control.bounds):
                return self._deny(PolicyCode.OUT_OF_BOUNDS, "control bounds are outside the locked target")
            if min(action.confidence, control.confidence) < context.confidence_min:
                return self._reobserve(PolicyCode.LOW_CONFIDENCE, "control confidence is below threshold")
            return self._allow()

        if action.action in {ComputerActionKind.MOVE_POINTER, ComputerActionKind.CLICK_POINT}:
            if action.x is None or action.y is None:
                return self._deny(PolicyCode.INVALID_ACTION, "coordinates are missing")
            if not target.bounds.contains_point(action.x, action.y) or not observation.screen_bounds.contains_point(action.x, action.y):
                return self._deny(PolicyCode.OUT_OF_BOUNDS, "point is outside the locked target")
            if action.confidence < context.confidence_min:
                return self._reobserve(PolicyCode.LOW_CONFIDENCE, "coordinate confidence is below threshold")
            return self._allow()

        if action.action is ComputerActionKind.SCROLL:
            if action.amount is None or action.amount == 0:
                return self._deny(PolicyCode.INVALID_ACTION, "scroll amount is missing")
            if action.x is not None and action.y is not None:
                if (
                    not target.bounds.contains_point(action.x, action.y)
                    or not observation.screen_bounds.contains_point(action.x, action.y)
                ):
                    return self._deny(PolicyCode.OUT_OF_BOUNDS, "scroll point is outside the locked target")
            return self._allow()

        if action.action is ComputerActionKind.TYPE_PAYLOAD:
            if not context.typing_authorized:
                return self._deny(PolicyCode.TYPING_NOT_AUTHORIZED, "guarded typing was not authorized")
            if action.payload_ref not in context.payload_refs:
                return self._deny(PolicyCode.PAYLOAD_UNAUTHORIZED, "payload reference is not session-authorized")
            return self._allow()

        if action.action is ComputerActionKind.KEYPRESS:
            if action.key in _SUBMIT_KEYS:
                if context.submit_authorized:
                    return self._allow()
                return self._handoff(PolicyCode.SUBMIT_NOT_AUTHORIZED, "submit key requires separate authorization")
            if action.key not in _SAFE_KEYS:
                return self._deny(PolicyCode.KEY_NOT_ALLOWED, "key is not allowlisted")
            return self._allow()

        if action.action is ComputerActionKind.HOTKEY:
            if action.keys not in _SAFE_HOTKEYS:
                return self._deny(PolicyCode.HOTKEY_NOT_ALLOWED, "hotkey is not allowlisted")
            return self._allow()

        return self._deny(PolicyCode.INVALID_ACTION, "action is not supported")

    def _session_gate(
        self,
        action: ComputerAction,
        observation: ComputerObservation,
        context: PolicyContext,
    ) -> PolicyDecision | None:
        if not context.enabled:
            return self._deny(PolicyCode.FEATURE_DISABLED, "computer use is disabled")
        if not context.explicit_user_activation or context.request_origin != "user":
            return self._deny(PolicyCode.USER_AUTHORITY_REQUIRED, "explicit user authority is required")
        if context.session_id != context.expected_session_id or context.generation != context.expected_generation:
            return self._deny(PolicyCode.SESSION_MISMATCH, "session generation is stale")
        if context.shutdown:
            return self._deny(PolicyCode.SHUTDOWN, "application is shutting down")
        if context.cancelled:
            return self._deny(PolicyCode.CANCELLED, "session was cancelled")
        if context.now >= context.deadline:
            return self._deny(PolicyCode.EXPIRED, "session deadline expired")
        if context.step >= context.max_steps:
            return self._deny(PolicyCode.STEP_LIMIT, "session step limit reached")
        if action.observation_id != observation.observation_id:
            return self._reobserve(PolicyCode.TARGET_CHANGED, "planner action references a stale observation")

        expected = context.expected_target
        actual = observation.target
        if expected is None or actual is None or not observation.process_alive:
            return self._reobserve(PolicyCode.TARGET_CHANGED, "locked target is unavailable")
        if not expected.matches(actual, require_same_bounds=True):
            return self._reobserve(PolicyCode.TARGET_CHANGED, "locked target identity or bounds changed")
        if not observation.screen_bounds.contains_rect(actual.bounds):
            return self._deny(PolicyCode.OUT_OF_BOUNDS, "target window lies outside screen bounds")
        if not context.allowed_processes or normalize_process_name(actual.process.name) not in context.allowed_processes:
            return self._deny(PolicyCode.TARGET_UNAUTHORIZED, "target process is outside the session allowlist")
        return None

    @staticmethod
    def _is_sensitive(target: WindowIdentity, control_text: str) -> bool:
        haystack = " ".join(
            (target.process.name, target.title, control_text)
        ).casefold()
        for term in _SENSITIVE_TERMS:
            if term in {"pin", "2fa", "mfa", "uac"}:
                words = haystack.replace("-", " ").replace("_", " ").split()
                if term in words:
                    return True
            elif term in haystack:
                return True
        return False

    @staticmethod
    def _is_high_impact(text: str) -> bool:
        haystack = str(text or "").casefold()
        return any(term in haystack for term in _HIGH_IMPACT_TERMS)

    @staticmethod
    def _allow() -> PolicyDecision:
        return PolicyDecision(PolicyDisposition.ALLOW, PolicyCode.ALLOWED, "allowed")

    @staticmethod
    def _deny(code: PolicyCode, reason: str) -> PolicyDecision:
        return PolicyDecision(PolicyDisposition.DENY, code, reason)

    @staticmethod
    def _reobserve(code: PolicyCode, reason: str) -> PolicyDecision:
        return PolicyDecision(PolicyDisposition.REOBSERVE, code, reason)

    @staticmethod
    def _handoff(code: PolicyCode, reason: str) -> PolicyDecision:
        return PolicyDecision(PolicyDisposition.HANDOFF, code, reason)
