"""Central, deterministic capability policy for Compact and Full profiles.

This module is deliberately provider-neutral and side-effect free.  It turns a
snapshot of local settings into effective capability decisions; callers must
still enforce their existing safety and confirmation boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading
from types import MappingProxyType
from typing import Callable, Mapping, TypeVar


_EffectResult = TypeVar("_EffectResult")


class CapabilityProfile(str, Enum):
    COMPACT = "compact"
    FULL = "full"


class Capability(str, Enum):
    CHAT = "chat"
    MEMORY = "memory"
    EMOTION_PERSONALITY = "emotion_personality"
    WEB_RAG = "web_rag"
    READ_ONLY_CONTINUATION = "read_only_continuation"
    TERMINAL_SENTINEL = "terminal_sentinel"
    PROCESS_AWARENESS = "process_awareness"
    COMPUTER_USE = "computer_use"
    COMPUTER_PLANNER = "computer_planner"
    RECOVERY_PLANNER = "recovery_planner"
    OS_TYPING = "os_typing"
    APP_CONTROL = "app_control"
    BACKGROUND_SENSING = "background_sensing"
    ADVANCED_OS_INTEGRATION = "advanced_os_integration"
    ADVANCED_UI = "advanced_ui"


class DecisionReason(str, Enum):
    ALLOWED = "allowed"
    COMPACT_MODE = "disabled_by_compact_mode"
    FEATURE_DISABLED = "disabled_by_feature_gate"


_CORE_CAPABILITIES = frozenset({
    Capability.CHAT,
    Capability.MEMORY,
    Capability.EMOTION_PERSONALITY,
})

_COMPACT_ALLOWED = _CORE_CAPABILITIES | frozenset({
    Capability.WEB_RAG,
    Capability.READ_ONLY_CONTINUATION,
})

_FEATURE_GATES: Mapping[Capability, tuple[str, ...]] = MappingProxyType({
    Capability.WEB_RAG: ("ENABLE_WEB_RAG",),
    Capability.READ_ONLY_CONTINUATION: ("ENABLE_AGENT_CONTINUATION",),
    Capability.TERMINAL_SENTINEL: ("ENABLE_TERMINAL_SENTINEL",),
    Capability.PROCESS_AWARENESS: ("ENABLE_PROCESS_AWARENESS",),
    Capability.COMPUTER_USE: ("ENABLE_COMPUTER_USE", "ENABLE_COMMAND_EXECUTION"),
    Capability.COMPUTER_PLANNER: ("ENABLE_COMPUTER_USE", "ENABLE_COMMAND_EXECUTION"),
    Capability.RECOVERY_PLANNER: ("ENABLE_COMPUTER_USE", "ENABLE_COMMAND_EXECUTION"),
    Capability.OS_TYPING: ("ENABLE_UNICODE_TYPING", "ENABLE_COMMAND_EXECUTION"),
    Capability.APP_CONTROL: ("ENABLE_COMMAND_EXECUTION",),
    Capability.BACKGROUND_SENSING: ("ENABLE_AMBIENT_POLLS",),
    Capability.ADVANCED_OS_INTEGRATION: ("ENABLE_COMMAND_EXECUTION",),
})

_COMMAND_CAPABILITIES: Mapping[str, Capability] = MappingProxyType({
    # Core passive presentation.
    "idle": Capability.CHAT,
    "speak": Capability.CHAT,
    "wake_user": Capability.CHAT,
    "popup": Capability.CHAT,
    "change_mood": Capability.EMOTION_PERSONALITY,
    "view_emotions": Capability.EMOTION_PERSONALITY,
    "clear_emotions": Capability.EMOTION_PERSONALITY,
    "view_memory": Capability.MEMORY,
    "search_memory": Capability.MEMORY,
    "clear_memory": Capability.MEMORY,
    "view_dreams": Capability.MEMORY,
    "list_tasks": Capability.MEMORY,
    "add_task": Capability.MEMORY,
    "complete_task": Capability.MEMORY,
    "read_notepad": Capability.MEMORY,
    "search_web": Capability.WEB_RAG,
    "fetch_webpage": Capability.WEB_RAG,
    "read_document": Capability.READ_ONLY_CONTINUATION,
    "read_file": Capability.READ_ONLY_CONTINUATION,
    "list_dir": Capability.READ_ONLY_CONTINUATION,
    "list_directory": Capability.READ_ONLY_CONTINUATION,
    "system_info": Capability.READ_ONLY_CONTINUATION,
    "recycle_bin_status": Capability.READ_ONLY_CONTINUATION,
    # Full-only effects and observation.
    "computer_use": Capability.COMPUTER_USE,
    "type_text": Capability.OS_TYPING,
    "monitor_process": Capability.PROCESS_AWARENESS,
    "get_active_app": Capability.PROCESS_AWARENESS,
    "list_running_apps": Capability.PROCESS_AWARENESS,
    "open_app": Capability.APP_CONTROL,
    "force_close": Capability.APP_CONTROL,
    "target_window_move": Capability.APP_CONTROL,
    "target_window_resize": Capability.APP_CONTROL,
    "target_window_close": Capability.APP_CONTROL,
})

_ADVANCED_OS_COMMANDS = frozenset({
    "request_path", "create_folder", "create_file", "delete_file",
    "rename_file", "write_file", "set_clipboard", "copy_to_clipboard",
    "get_clipboard", "take_screenshot", "show_notification", "run_command",
    "open_file", "open_folder", "show_dialog", "open_browser", "open_url",
    "set_volume", "set_wallpaper", "search_files", "lock_screen", "shutdown",
    "restart", "set_reminder", "set_autostart", "open_settings", "set_theme",
    "request_screen_read", "analyze_screen_deep",
})


def capability_for_command(command: object) -> Capability:
    """Classify one model/handler command at the central policy boundary."""
    name = str(command or "").strip().casefold()
    if name in _COMMAND_CAPABILITIES:
        return _COMMAND_CAPABILITIES[name]
    if name in _ADVANCED_OS_COMMANDS:
        return Capability.ADVANCED_OS_INTEGRATION
    # Unknown commands already fail before dispatch.  Classifying them as an
    # advanced integration keeps any future direct caller fail-closed.
    return Capability.ADVANCED_OS_INTEGRATION


@dataclass(frozen=True, slots=True)
class CapabilityDecision:
    capability: Capability
    profile: CapabilityProfile
    allowed: bool
    reason: DecisionReason
    failed_gate: str = ""


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    profile: CapabilityProfile
    generation: int
    transitioning: bool


@dataclass(frozen=True, slots=True)
class CapabilityAuthorization:
    capability: Capability
    generation: int


class CapabilityPolicy:
    """Immutable effective capability snapshot.

    A fresh settings snapshot should be installed when a mode transition or a
    settings reload completes.  The policy never starts services or performs an
    effect by itself.
    """

    def __init__(
        self,
        profile: CapabilityProfile,
        feature_gates: Mapping[str, bool] | None = None,
    ) -> None:
        self._profile = CapabilityProfile(profile)
        self._feature_gates = MappingProxyType(dict(feature_gates or {}))

    @classmethod
    def from_settings(cls, settings: object) -> "CapabilityPolicy":
        profile = (
            CapabilityProfile.COMPACT
            if bool(getattr(settings, "compact_mode", True))
            else CapabilityProfile.FULL
        )
        names = {name for gates in _FEATURE_GATES.values() for name in gates}
        values: dict[str, bool] = {}
        for name in names:
            attribute = name.casefold()
            try:
                values[name] = bool(getattr(settings, attribute))
            except (AttributeError, TypeError, ValueError):
                bool_reader = getattr(settings, "bool", None)
                values[name] = bool_reader(name, False) if callable(bool_reader) else False
        return cls(profile, values)

    @property
    def profile(self) -> CapabilityProfile:
        return self._profile

    @property
    def is_compact(self) -> bool:
        return self._profile is CapabilityProfile.COMPACT

    def decision(self, capability: Capability) -> CapabilityDecision:
        capability = Capability(capability)
        if self.is_compact and capability not in _COMPACT_ALLOWED:
            return CapabilityDecision(
                capability, self._profile, False, DecisionReason.COMPACT_MODE,
            )
        for gate in _FEATURE_GATES.get(capability, ()):
            if not self._feature_gates.get(gate, False):
                return CapabilityDecision(
                    capability,
                    self._profile,
                    False,
                    DecisionReason.FEATURE_DISABLED,
                    failed_gate=gate,
                )
        return CapabilityDecision(
            capability, self._profile, True, DecisionReason.ALLOWED,
        )

    def is_allowed(self, capability: Capability) -> bool:
        return self.decision(capability).allowed


def policy_from_settings(settings: object) -> CapabilityPolicy:
    """Compatibility-friendly functional constructor."""
    return CapabilityPolicy.from_settings(settings)


class CapabilityController:
    """Thread-safe owner of the current policy and transition generation.

    Beginning a transition increments the generation before any cleanup work.
    While transitioning, every Full-only capability fails closed.  Effectful
    callers may retain an authorization token and revalidate it immediately
    before their actual OS effect.
    """

    def __init__(self, policy: CapabilityPolicy) -> None:
        self._lock = threading.RLock()
        self._policy = policy
        self._generation = 0
        self._transitioning = False

    def snapshot(self) -> CapabilitySnapshot:
        with self._lock:
            return CapabilitySnapshot(
                self._policy.profile,
                self._generation,
                self._transitioning,
            )

    def decision(self, capability: Capability) -> CapabilityDecision:
        with self._lock:
            decision = self._policy.decision(capability)
            if self._transitioning and capability not in _COMPACT_ALLOWED:
                return CapabilityDecision(
                    Capability(capability),
                    CapabilityProfile.COMPACT,
                    False,
                    DecisionReason.COMPACT_MODE,
                )
            return decision

    def is_allowed(self, capability: Capability) -> bool:
        return self.decision(capability).allowed

    def authorize(
        self, capability: Capability,
    ) -> CapabilityAuthorization | None:
        with self._lock:
            if not self.decision(capability).allowed:
                return None
            return CapabilityAuthorization(Capability(capability), self._generation)

    def is_authorized(self, token: object) -> bool:
        if not isinstance(token, CapabilityAuthorization):
            return False
        with self._lock:
            return (
                token.generation == self._generation
                and not self._transitioning
                and self._policy.is_allowed(token.capability)
            )

    def perform_authorized(
        self,
        token: object,
        effect: Callable[[], _EffectResult],
    ) -> tuple[bool, _EffectResult | None]:
        """Run one primitive only while its exact authorization stays current.

        The controller lock covers both the generation/policy check and the
        primitive invocation.  A transition therefore orders strictly before
        the effect (which is rejected) or after it (so the transition has not
        begun yet); there is no check-to-effect window.  Effect exceptions are
        intentionally left to the owning platform boundary.
        """

        if not isinstance(token, CapabilityAuthorization):
            return False, None
        with self._lock:
            if not self.is_authorized(token):
                return False, None
            return True, effect()

    def begin_full_transition(self) -> int:
        with self._lock:
            self._generation += 1
            self._transitioning = True
            return self._generation

    def begin_compact_transition(self) -> int:
        with self._lock:
            self._generation += 1
            self._transitioning = True
            # Replace first: cleanup and persistence happen behind this outer
            # fail-closed profile rather than the previously active Full one.
            self._policy = CapabilityPolicy(CapabilityProfile.COMPACT)
            return self._generation

    def cancel_transition(self, compact_policy: CapabilityPolicy | None = None) -> int:
        with self._lock:
            self._generation += 1
            self._transitioning = False
            if (
                compact_policy is not None
                and compact_policy.profile is CapabilityProfile.COMPACT
            ):
                self._policy = compact_policy
            elif self._policy.profile is not CapabilityProfile.COMPACT:
                self._policy = CapabilityPolicy(CapabilityProfile.COMPACT)
            return self._generation

    def commit_full(self, policy: CapabilityPolicy, generation: int) -> bool:
        if policy.profile is not CapabilityProfile.FULL:
            return False
        return self._commit(policy, generation)

    def commit_compact(self, policy: CapabilityPolicy, generation: int) -> bool:
        if policy.profile is not CapabilityProfile.COMPACT:
            return False
        return self._commit(policy, generation)

    def _commit(self, policy: CapabilityPolicy, generation: int) -> bool:
        with self._lock:
            if int(generation) != self._generation:
                return False
            self._policy = policy
            self._transitioning = False
            return True


__all__ = [
    "Capability",
    "CapabilityAuthorization",
    "CapabilityController",
    "CapabilityDecision",
    "CapabilityPolicy",
    "CapabilityProfile",
    "CapabilitySnapshot",
    "DecisionReason",
    "capability_for_command",
    "policy_from_settings",
]
