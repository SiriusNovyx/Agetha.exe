"""Canonical static metadata for model-facing Agetha commands.

This module describes what a command is. It does not implement commands and it
does not replace capability state, CommandGuard, confirmation, target checks,
or effect-time authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping

from agetha.core.capabilities import Capability
from agetha.core.request_context import REQUEST_ORIGINS, RequestOrigin


class RiskTier(str, Enum):
    SAFE = "safe"
    CAUTION = "caution"
    DANGER = "danger"


class DispatchKind(str, Enum):
    CORE = "core"
    HANDLER = "handler"


ALL_REQUEST_ORIGINS: frozenset[RequestOrigin] = frozenset(REQUEST_ORIGINS)
TRUSTED_EVENT_ORIGINS: frozenset[RequestOrigin] = frozenset({
    "user",
    "touch",
    "file_drop",
    "reminder",
})
DIRECT_USER_ORIGIN: frozenset[RequestOrigin] = frozenset({"user"})

_EXECUTION_CAPABILITIES = frozenset({
    Capability.COMPUTER_USE,
    Capability.OS_TYPING,
    Capability.APP_CONTROL,
    Capability.ADVANCED_OS_INTEGRATION,
})


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """Invocation-independent command policy facts."""

    name: str
    base_risk: RiskTier
    capability: Capability
    allowed_origins: frozenset[RequestOrigin]
    dispatch_kind: DispatchKind
    handler_key: str | None
    feature_gates: tuple[str, ...] = ()

    @property
    def requires_execution(self) -> bool:
        """Whether the outer capability requires command execution support."""
        return self.capability in _EXECUTION_CAPABILITIES


def build_command_specs(specs: Iterable[CommandSpec]) -> Mapping[str, CommandSpec]:
    """Validate and index specifications without permitting silent overwrite."""
    indexed: dict[str, CommandSpec] = {}
    for spec in specs:
        name = spec.name
        if not name or name != name.strip().casefold():
            raise ValueError(f"command name must be canonical: {name!r}")
        if name in indexed:
            raise ValueError(f"duplicate command specification: {name}")
        if not isinstance(spec.base_risk, RiskTier):
            raise ValueError(f"invalid base risk: {name}")
        if not isinstance(spec.capability, Capability):
            raise ValueError(f"invalid capability: {name}")
        if not isinstance(spec.dispatch_kind, DispatchKind):
            raise ValueError(f"invalid dispatch kind: {name}")
        if not spec.allowed_origins:
            raise ValueError(f"command must allow at least one origin: {name}")
        if not spec.allowed_origins <= ALL_REQUEST_ORIGINS:
            raise ValueError(f"command declares an unknown origin: {name}")
        if len(spec.feature_gates) != len(set(spec.feature_gates)):
            raise ValueError(f"command declares a duplicate feature gate: {name}")
        if any(not gate.startswith("ENABLE_") for gate in spec.feature_gates):
            raise ValueError(f"command declares an invalid feature gate: {name}")
        if spec.dispatch_kind is DispatchKind.CORE:
            if spec.handler_key is not None:
                raise ValueError(f"core command cannot declare a handler: {name}")
        elif spec.handler_key != name:
            if not spec.handler_key:
                raise ValueError(f"handler command must declare a handler: {name}")
            raise ValueError(f"handler key must match command name: {name}")
        indexed[name] = spec
    return MappingProxyType(indexed)


def _core(
    name: str,
    risk: RiskTier,
    capability: Capability,
    *,
    origins: frozenset[RequestOrigin] = TRUSTED_EVENT_ORIGINS,
) -> CommandSpec:
    return CommandSpec(
        name=name,
        base_risk=risk,
        capability=capability,
        allowed_origins=origins,
        dispatch_kind=DispatchKind.CORE,
        handler_key=None,
    )


def _handler(
    name: str,
    risk: RiskTier,
    capability: Capability,
    *,
    origins: frozenset[RequestOrigin] = TRUSTED_EVENT_ORIGINS,
    feature_gates: tuple[str, ...] = (),
) -> CommandSpec:
    return CommandSpec(
        name=name,
        base_risk=risk,
        capability=capability,
        allowed_origins=origins,
        dispatch_kind=DispatchKind.HANDLER,
        handler_key=name,
        feature_gates=feature_gates,
    )


_S = RiskTier.SAFE
_C = RiskTier.CAUTION
_D = RiskTier.DANGER

_COMMAND_SPEC_DEFINITIONS = (
    _handler("add_task", _S, Capability.MEMORY, feature_gates=("ENABLE_TASKS",)),
    _handler(
        "analyze_screen_deep",
        _C,
        Capability.ADVANCED_OS_INTEGRATION,
        origins=DIRECT_USER_ORIGIN,
    ),
    _handler("change_mood", _S, Capability.EMOTION_PERSONALITY),
    _handler(
        "clear_emotions",
        _C,
        Capability.EMOTION_PERSONALITY,
        feature_gates=("ENABLE_EMOTION_ENGINE",),
    ),
    _handler("clear_memory", _C, Capability.MEMORY),
    _handler("complete_task", _S, Capability.MEMORY, feature_gates=("ENABLE_TASKS",)),
    _handler(
        "computer_use",
        _C,
        Capability.COMPUTER_USE,
        origins=DIRECT_USER_ORIGIN,
        feature_gates=("ENABLE_COMPUTER_USE",),
    ),
    _handler("copy_to_clipboard", _C, Capability.ADVANCED_OS_INTEGRATION),
    _handler("create_file", _D, Capability.ADVANCED_OS_INTEGRATION),
    _handler("create_folder", _D, Capability.ADVANCED_OS_INTEGRATION),
    _handler("delete_file", _D, Capability.ADVANCED_OS_INTEGRATION),
    _handler("fetch_webpage", _C, Capability.WEB_RAG),
    _handler(
        "force_close",
        _D,
        Capability.APP_CONTROL,
        feature_gates=("ENABLE_WINDOW_CONTROL",),
    ),
    _handler("get_active_app", _S, Capability.PROCESS_AWARENESS),
    _handler("get_clipboard", _S, Capability.ADVANCED_OS_INTEGRATION),
    _handler(
        "glitch_overlay",
        _S,
        Capability.ADVANCED_OS_INTEGRATION,
        feature_gates=("ENABLE_GLITCH_EFFECTS",),
    ),
    _core("idle", _S, Capability.CHAT, origins=ALL_REQUEST_ORIGINS),
    _handler("list_dir", _C, Capability.READ_ONLY_CONTINUATION),
    _handler("list_directory", _C, Capability.READ_ONLY_CONTINUATION),
    _handler("list_running_apps", _S, Capability.PROCESS_AWARENESS),
    _handler("list_tasks", _S, Capability.MEMORY, feature_gates=("ENABLE_TASKS",)),
    _handler("lock_screen", _D, Capability.ADVANCED_OS_INTEGRATION),
    _handler("monitor_process", _S, Capability.PROCESS_AWARENESS),
    _handler("move_window", _S, Capability.ADVANCED_OS_INTEGRATION),
    _handler("open_app", _C, Capability.APP_CONTROL),
    _handler("open_browser", _S, Capability.ADVANCED_OS_INTEGRATION),
    _handler("open_file", _C, Capability.ADVANCED_OS_INTEGRATION),
    _handler("open_folder", _S, Capability.ADVANCED_OS_INTEGRATION),
    _handler("open_settings", _C, Capability.ADVANCED_OS_INTEGRATION),
    _handler("open_url", _S, Capability.ADVANCED_OS_INTEGRATION),
    _handler("play_emotion_sound", _S, Capability.ADVANCED_OS_INTEGRATION),
    _handler("play_sound", _C, Capability.ADVANCED_OS_INTEGRATION),
    _handler("play_virus_trivia", _S, Capability.ADVANCED_OS_INTEGRATION),
    _core("popup", _S, Capability.CHAT),
    _handler("read_document", _S, Capability.READ_ONLY_CONTINUATION),
    _handler("read_file", _C, Capability.READ_ONLY_CONTINUATION),
    _handler("read_notepad", _S, Capability.MEMORY),
    _handler("recycle_bin_status", _S, Capability.READ_ONLY_CONTINUATION),
    _handler("rename_file", _D, Capability.ADVANCED_OS_INTEGRATION),
    _handler("request_path", _S, Capability.ADVANCED_OS_INTEGRATION),
    _handler("request_screen_read", _S, Capability.ADVANCED_OS_INTEGRATION),
    _handler("restart", _D, Capability.ADVANCED_OS_INTEGRATION),
    _handler("run_command", _D, Capability.ADVANCED_OS_INTEGRATION),
    _handler("search_files", _C, Capability.ADVANCED_OS_INTEGRATION),
    _handler(
        "search_memory",
        _S,
        Capability.MEMORY,
        feature_gates=("ENABLE_LONGTERM_MEMORY",),
    ),
    _handler("search_web", _C, Capability.WEB_RAG),
    _handler(
        "set_autostart",
        _D,
        Capability.ADVANCED_OS_INTEGRATION,
        feature_gates=("ENABLE_AUTOSTART_CONTROL",),
    ),
    _handler("set_clipboard", _C, Capability.ADVANCED_OS_INTEGRATION),
    _handler("set_reminder", _S, Capability.ADVANCED_OS_INTEGRATION),
    _handler(
        "set_theme",
        _D,
        Capability.ADVANCED_OS_INTEGRATION,
        feature_gates=("ENABLE_THEME_CONTROL",),
    ),
    _handler("set_volume", _C, Capability.ADVANCED_OS_INTEGRATION),
    _handler("set_wallpaper", _C, Capability.ADVANCED_OS_INTEGRATION),
    _handler("show_dialog", _C, Capability.ADVANCED_OS_INTEGRATION),
    _handler("show_error_gif", _S, Capability.ADVANCED_OS_INTEGRATION),
    _handler("show_notification", _S, Capability.ADVANCED_OS_INTEGRATION),
    _handler("shutdown", _D, Capability.ADVANCED_OS_INTEGRATION),
    _handler("snap_to_center", _S, Capability.ADVANCED_OS_INTEGRATION),
    _core("speak", _S, Capability.CHAT, origins=ALL_REQUEST_ORIGINS),
    _handler("system_info", _S, Capability.READ_ONLY_CONTINUATION),
    _handler("take_screenshot", _S, Capability.ADVANCED_OS_INTEGRATION),
    _handler(
        "target_window_close",
        _C,
        Capability.APP_CONTROL,
        feature_gates=("ENABLE_WINDOW_CONTROL",),
    ),
    _handler(
        "target_window_move",
        _C,
        Capability.APP_CONTROL,
        feature_gates=("ENABLE_WINDOW_CONTROL",),
    ),
    _handler(
        "target_window_resize",
        _C,
        Capability.APP_CONTROL,
        feature_gates=("ENABLE_WINDOW_CONTROL",),
    ),
    _handler(
        "type_text",
        _C,
        Capability.OS_TYPING,
        feature_gates=("ENABLE_UNICODE_TYPING",),
    ),
    _handler("view_dreams", _S, Capability.MEMORY, feature_gates=("ENABLE_DREAMS",)),
    _handler(
        "view_emotions",
        _S,
        Capability.EMOTION_PERSONALITY,
        feature_gates=("ENABLE_EMOTION_ENGINE",),
    ),
    _handler("view_memory", _S, Capability.MEMORY),
    _core("wake_user", _S, Capability.CHAT),
    _handler("write_file", _D, Capability.ADVANCED_OS_INTEGRATION),
)

COMMAND_SPECS: Mapping[str, CommandSpec] = build_command_specs(
    _COMMAND_SPEC_DEFINITIONS,
)
COMMAND_NAMES = frozenset(COMMAND_SPECS)
BASE_RISK_BY_COMMAND: Mapping[str, str] = MappingProxyType({
    name: spec.base_risk.value
    for name, spec in COMMAND_SPECS.items()
})
CAPABILITY_BY_COMMAND: Mapping[str, Capability] = MappingProxyType({
    name: spec.capability
    for name, spec in COMMAND_SPECS.items()
})


def get_command_spec(command: object) -> CommandSpec | None:
    """Return a known specification without inventing policy for unknown input."""
    name = str(command or "").strip().casefold()
    return COMMAND_SPECS.get(name)


def validate_handler_bindings(handlers: Mapping[str, object]) -> None:
    """Require an exact bidirectional match with handler-backed specifications."""
    expected = {
        spec.handler_key
        for spec in COMMAND_SPECS.values()
        if spec.dispatch_kind is DispatchKind.HANDLER
    }
    actual = set(handlers)
    unexpected = sorted(actual - expected)
    if unexpected:
        raise ValueError(f"handler has no handler-backed CommandSpec: {unexpected[0]}")
    missing = sorted(expected - actual)
    if missing:
        raise ValueError(f"CommandSpec handler is not registered: {missing[0]}")


__all__ = [
    "ALL_REQUEST_ORIGINS",
    "BASE_RISK_BY_COMMAND",
    "CAPABILITY_BY_COMMAND",
    "COMMAND_NAMES",
    "COMMAND_SPECS",
    "DIRECT_USER_ORIGIN",
    "TRUSTED_EVENT_ORIGINS",
    "CommandSpec",
    "DispatchKind",
    "RiskTier",
    "build_command_specs",
    "get_command_spec",
    "validate_handler_bindings",
]
