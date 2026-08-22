"""
command_handlers.py — Command pattern dispatch for Agetha.
Each handler receives (app, response, ctx) and returns True if it handled the command.
"""

from __future__ import annotations

import os
import platform
import re
import shlex
import shutil
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from agetha.commands.command_guard import CommandGuard
from agetha.commands.specs import (
    DispatchKind,
    get_command_spec,
    validate_handler_bindings,
)
from agetha.commands.handlers.registry import HANDLERS, HandlerFn, register
from agetha.commands.handlers.support import (
    CAPABILITY_AUTHORIZATION as _CAPABILITY_AUTHORIZATION,
    DispatchCtx,
    call_app_ui_sync as _call_app_ui_sync,
    command_result_ok as _command_result_ok,
    finish_verified_command as _finish_verified_command,
    perform_authorized_effect as _perform_authorized_effect,
    schedule_app_ui as _schedule_app_ui,
    start_app_worker as _start_app_worker,
)
from agetha.commands.handlers.memory_presentation import (
    _format_notepad_context,
    handle_add_task,
    handle_change_mood,
    handle_clear_emotions,
    handle_clear_memory,
    handle_complete_task,
    handle_glitch_overlay,
    handle_list_tasks,
    handle_play_virus_trivia,
    handle_read_notepad,
    handle_search_memory,
    handle_view_dreams,
    handle_view_emotions,
    handle_view_memory,
)
from agetha.commands.handlers.web_context import handle_fetch_webpage, handle_search_web
from agetha.commands.handlers.files import (
    handle_clipboard_set,
    handle_create_file,
    handle_create_folder,
    handle_delete_file,
    handle_list_dir,
    handle_notification,
    handle_open_file,
    handle_open_folder,
    handle_play_sound,
    handle_rename_file,
    handle_request_path,
    handle_run_command,
    handle_screenshot,
    handle_write_file,
)
from agetha.commands.handlers.system import (
    handle_lock_screen,
    handle_open_settings,
    handle_recycle_bin_status,
    handle_restart,
    handle_search_files,
    handle_set_autostart,
    handle_set_theme,
    handle_set_volume,
    handle_set_wallpaper,
    handle_shutdown,
)
from agetha.core.request_context import (
    AmbientRelevance,
    REQUEST_ORIGINS,
    RequestOrigin,
    normalize_ambient_relevance,
    normalize_request_origin,
)
from agetha.core.capabilities import (
    CapabilityAuthorization,
    CapabilityController,
    CapabilityPolicy,
    DecisionReason,
    capability_for_command,
)
from agetha.core.context_dependencies import ContextKind, ContextRequest
from agetha.commands.system_commands import (
    copy_to_clipboard, get_clipboard, lock_screen, open_folder, open_url,
    restart_system, screenshot_path, search_files, set_reminder, set_volume,
    set_wallpaper, show_notification, shutdown_system, system_info,
)
from agetha.platform.screen_monitoring import redact_sensitive_text
from agetha.platform.unicode_typing import (
    ClipboardSnapshot,
    NativeSendResult,
    TypingPreview,
    TypingTarget,
    build_typing_preview,
    capture_intended_target,
    default_dependencies,
    parse_mode,
    parse_speed,
    type_unicode_text,
)
from agetha.platform.window_control import (
    close_window,
    is_self_process_target,
    is_self_window_target,
    kill_process_by_hwnd,
    kill_process_by_name,
    move_window,
    resize_window,
    resolve_target_hwnd,
)
from agetha.utils import IS_LINUX, IS_WINDOWS, WINDOW_W, WINDOW_H, logger
from agetha.app_config import get_settings

_WINDOW_COMMANDS = frozenset({
    "target_window_move", "target_window_resize", "target_window_close", "force_close",
})

# Inspection-only commands: do not apply command_approved emotion events.
_EMOTION_READONLY_COMMANDS = frozenset({
    "view_emotions", "view_memory", "view_dreams", "list_tasks",
    "search_memory", "recycle_bin_status", "read_notepad",
    "get_active_app", "list_running_apps", "monitor_process",
})

# Identity-only marker: model JSON cannot forge approval by naming a field.
_TYPE_TEXT_GUARD_APPROVAL = object()
if TYPE_CHECKING:
    from main import CompanionApp


def _capability_decision(app: "CompanionApp", command: str, settings: object):
    """Read the app-owned live controller, with a safe settings fallback."""
    capability = capability_for_command(command)
    controller = getattr(app, "_capabilities", None)
    decision = getattr(controller, "decision", None)
    # MagicMock-heavy legacy test doubles expose arbitrary callable attributes;
    # only the concrete controller is an authority source.
    if isinstance(controller, CapabilityController) and callable(decision):
        try:
            return decision(capability)
        except Exception as exc:
            logger.warning("Capability controller failed closed: %s", type(exc).__name__)
    return CapabilityPolicy.from_settings(settings).decision(capability)


def _capability_effect_allowed(
    app: "CompanionApp", command: str, settings: object | None = None,
) -> bool:
    try:
        resolved = settings if settings is not None else get_settings()
        return bool(_capability_decision(app, command, resolved).allowed)
    except Exception:
        return False


def _authorize_capability(app: "CompanionApp", command: str, settings: object):
    controller = getattr(app, "_capabilities", None)
    authorize = getattr(controller, "authorize", None)
    if isinstance(controller, CapabilityController) and callable(authorize):
        try:
            return authorize(capability_for_command(command))
        except Exception:
            return None
    decision = CapabilityPolicy.from_settings(settings).decision(
        capability_for_command(command),
    )
    return True if decision.allowed else None


def _authorization_is_current(app: "CompanionApp", authorization: object) -> bool:
    if authorization is True:
        return True
    if not isinstance(authorization, CapabilityAuthorization):
        return False
    controller = getattr(app, "_capabilities", None)
    checker = getattr(controller, "is_authorized", None)
    try:
        return bool(
            isinstance(controller, CapabilityController)
            and callable(checker)
            and checker(authorization)
        )
    except Exception:
        return False


def _bind_capability_effect_boundaries(
    app: "CompanionApp",
    authorization: object,
    dependencies: object,
) -> None:
    """Bind normal Unicode platform primitives to one exact generation."""

    controller = getattr(app, "_capabilities", None)
    if (
        not isinstance(controller, CapabilityController)
        or not isinstance(authorization, CapabilityAuthorization)
    ):
        return

    _bind_typing_effect_runner(
        dependencies,
        lambda effect: controller.perform_authorized(authorization, effect),
    )


def _bind_typing_effect_runner(
    dependencies: object,
    effect_runner: Callable[
        [Callable[[], object]], tuple[bool, object | None]
    ],
) -> None:
    """Gate only short platform primitives, never Guard or preview dialogs."""

    denied_values = {
        "get_focused_target": None,
        "send_native_unicode": NativeSendResult(False, 0, 0),
        "read_clipboard": ClipboardSnapshot(False, None),
        "write_clipboard": False,
        "send_paste_shortcut": False,
        "activate_target": False,
    }
    for name, denied in denied_values.items():
        callback = getattr(dependencies, name, None)
        if not callable(callback):
            continue

        def _bound(*args, _callback=callback, _denied=denied, **kwargs):
            try:
                performed, value = effect_runner(
                    lambda: _callback(*args, **kwargs),
                )
            except Exception:
                performed, value = False, None
            return value if performed else _denied

        setattr(dependencies, name, _bound)


def _deny_capability(app: "CompanionApp", decision) -> None:
    compact = getattr(decision, "reason", None) is DecisionReason.COMPACT_MODE
    message = (
        "This action is unavailable while Compact Mode is on."
        if compact else "This action is disabled in settings."
    )
    logger.info("Command blocked by capability policy: %s", getattr(decision, "reason", ""))
    app._show_op_error(message)
    app._speak_and_continue(
        [{"text": message, "pause": 0.0}], "neutral", False,
    )


def guarded_launch_application(
    app: "CompanionApp",
    command: tuple[str, ...],
    *,
    guard_approved: bool = False,
    launcher: Callable[[tuple[str, ...]], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    capability_authorization: object | None = None,
) -> bool:
    """Canonical shell-free app launch used by handlers and Computer Use.

    Computer Use calls this with ``guard_approved=False`` so ``open_app`` keeps
    its own Caution confirmation. The normal handler has already passed the
    same dispatch guard and uses ``guard_approved=True`` to avoid a duplicate.
    """

    if (
        not command
        or len(command) > 4
        or any(
            not isinstance(item, str)
            or not item
            or "\x00" in item
            or len(item) > 1024
            for item in command
        )
    ):
        return False
    def _cancelled() -> bool:
        try:
            return bool(cancel_check is not None and cancel_check())
        except Exception:
            return True

    settings = get_settings()
    authorization = capability_authorization
    if authorization is None:
        authorization = _authorize_capability(app, "open_app", settings)
    if not _authorization_is_current(app, authorization):
        return False

    if _cancelled():
        return False
    if not _authorization_is_current(app, authorization):
        return False
    response = {"app": command[0], "app_name": command[0]}
    if not guard_approved:
        if cancel_check is None:
            approved = app._guard.check("open_app", response)
        else:
            approved = app._guard.check(
                "open_app",
                response,
                cancel_check=cancel_check,
            )
        if not approved:
            return False
    # A confirmation dialog may remain open while STOP is pressed.  Recheck
    # the session-owned cancellation signal after the dialog returns and at
    # the last possible point before the launch effect.
    if _cancelled():
        return False

    def _default_launch(argv: tuple[str, ...]) -> None:
        if IS_WINDOWS and len(argv) == 1:
            try:
                os.startfile(argv[0])
                return
            except OSError:
                pass
        subprocess.Popen(list(argv))

    def _launch() -> bool:
        if _cancelled():
            return False
        (launcher or _default_launch)(tuple(command))
        return True

    try:
        if _cancelled() or not _authorization_is_current(app, authorization):
            return False
        performed, launched = _perform_authorized_effect(
            app, authorization, _launch,
        )
        return bool(performed and launched)
    except Exception as exc:
        logger.warning("Application launch failed safely: %s", type(exc).__name__)
        return False


def _typing_target_from_screen(app: "CompanionApp", platform_name: str) -> TypingTarget | None:
    """Reuse ScreenReader's validated pre-dialog external target when possible."""
    screen = getattr(app, "_screen", None)
    if screen is None:
        return None
    try:
        info = screen.preserve_external_target()
    except Exception as exc:
        logger.warning("Unicode target preservation failed: %s", type(exc).__name__)
        return None
    if not isinstance(info, dict) or info.get("hwnd") is None:
        return None
    try:
        handle = int(info["hwnd"])
        process_id = info.get("process_id")
        pid_text = "" if process_id is None else str(int(process_id))
    except (KeyError, TypeError, ValueError):
        return None
    platform_key = str(platform_name or "").casefold()
    prefix = "win" if platform_key == "windows" else "x11" if platform_key == "linux" else "window"
    own_handle = getattr(screen, "_own_hwnd", None)
    return TypingTarget(
        stable_id=f"{prefix}:{handle}:{pid_text}",
        title=str(info.get("title", ""))[:512],
        process_name=str(info.get("process_name", ""))[:260],
        window_handle=handle,
        is_own_window=own_handle is not None and handle == own_handle,
    )


def _prepare_unicode_typing(app: "CompanionApp", response: dict, settings) -> bool:
    """Validate gates and capture the target before any owned confirmation UI."""
    for key in (
        "_typing_dependencies", "_typing_target", "_typing_preview",
        "_typing_preview_approved", "_typing_guard_token",
    ):
        response.pop(key, None)
    if not settings.enable_command_execution:
        app._show_op_error("Command execution is disabled in config.")
        app._speak_and_continue(
            [{"text": "ปิดการพิมพ์ผ่านคำสั่งไว้ใน config", "pause": 0.0}],
            "neutral",
            False,
        )
        return False
    if not settings.enable_unicode_typing:
        app._show_op_error("Unicode typing is disabled in config.")
        app._speak_and_continue(
            [{"text": "ปิด Unicode typing ไว้ใน config", "pause": 0.0}],
            "neutral",
            False,
        )
        return False

    text = response.get("text", "")
    if not isinstance(text, str):
        text = str(text or "")
        response["text"] = text
    if not text:
        app._show_op_error("No text was supplied.")
        return False
    try:
        mode = parse_mode(response.get("mode", settings.unicode_typing_mode))
        speed = parse_speed(response.get("speed", "normal"))
    except (TypeError, ValueError):
        app._show_op_error("Unknown Unicode typing mode or speed.")
        return False
    response["mode"] = mode.value
    response["speed"] = speed.value
    raw_restore = response.get(
        "restore_clipboard", settings.unicode_typing_restore_clipboard,
    )
    response["restore_clipboard"] = (
        raw_restore if isinstance(raw_restore, bool)
        else str(raw_restore).strip().casefold() in {"1", "yes", "true", "on"}
    )

    dependencies = default_dependencies()
    target = _typing_target_from_screen(app, dependencies.platform_name)
    if target is None:
        target = capture_intended_target(dependencies)
    preview = build_typing_preview(
        text,
        target,
        mode=mode,
        platform_name=dependencies.platform_name,
        session_type=dependencies.session_type,
        preview_threshold=settings.unicode_typing_preview_threshold,
    )
    if "restricted-target" in preview.reasons:
        app._show_op_error("Typing into this protected or elevated target is refused.")
        app._speak_and_continue(
            [{"text": "เป้าหมายนี้เสี่ยงเกินไป ฉันไม่พิมพ์ให้", "pause": 0.0}],
            "neutral",
            False,
        )
        return False
    response["_typing_dependencies"] = dependencies
    response["_typing_target"] = target
    response["_typing_preview"] = preview
    return True


def _confirm_typing_preview(
    app: "CompanionApp",
    preview: TypingPreview,
    text: str,
    *,
    preview_only: bool,
    cancel_check: Callable[[], bool] | None = None,
    timeout_seconds: float = 120.0,
) -> bool:
    """Request the Win95 preview without touching Tk from this worker."""
    from agetha.ui.typing_preview import open_typing_preview

    if "potentially-sensitive-text" in preview.reasons:
        content = f"[Sensitive content hidden — {len(text)} characters]"
    else:
        content = redact_sensitive_text(text)
        if len(content) > 600:
            content = content[:599] + "…"
    done = threading.Event()
    invalidated = threading.Event()
    approved = [False]
    dialog = [None]

    def _cancelled() -> bool:
        try:
            return bool(cancel_check is not None and cancel_check())
        except Exception:
            return True

    def _decision(value: bool) -> None:
        if not invalidated.is_set() and not _cancelled():
            approved[0] = bool(value)
        done.set()

    def _open() -> None:
        if invalidated.is_set() or _cancelled() or getattr(app, "_closing", False):
            done.set()
            return
        try:
            opened = open_typing_preview(
                app.root,
                preview,
                content_preview=content,
                on_decision=_decision,
                preview_only=preview_only,
            )
            dialog[0] = opened
            if invalidated.is_set() or _cancelled():
                opened.close()
        except Exception as exc:
            logger.warning("Typing preview failed: %s", type(exc).__name__)
            done.set()

    if _schedule_app_ui(app, _open) is None:
        return False
    deadline = time.monotonic() + max(1.0, min(float(timeout_seconds), 300.0))
    while not done.wait(0.1):
        if (
            _cancelled()
            or getattr(app, "_closing", False)
            or time.monotonic() >= deadline
        ):
            invalidated.set()
            opened = dialog[0]
            if opened is not None:
                _schedule_app_ui(app, opened.close)
            return False
    return approved[0] and not invalidated.is_set() and not _cancelled()


def guarded_type_for_computer_use(
    app: "CompanionApp",
    text: str,
    locked_target: object,
    cancel_event: threading.Event,
    *,
    validate_locked_target: Callable[[bool], bool] | None = None,
    effect_runner: Callable[
        [Callable[[], object]], tuple[bool, object | None]
    ] | None = None,
) -> bool:
    """Synchronously reuse Unicode preflight, Guard, preview, and target checks."""
    def _target_is_valid(require_foreground: bool) -> bool:
        if (
            cancel_event.is_set()
            or bool(getattr(app, "_closing", False))
            or not callable(validate_locked_target)
        ):
            return False
        if not _capability_effect_allowed(app, "type_text"):
            return False
        try:
            return bool(validate_locked_target(require_foreground))
        except Exception:
            return False

    settings = get_settings()
    response: dict[str, object] = {
        "command": "type_text",
        "text": text,
        "mode": settings.unicode_typing_mode,
        "speed": "normal",
        "restore_clipboard": settings.unicode_typing_restore_clipboard,
        "segments": [],
    }
    if cancel_event.is_set() or not _prepare_unicode_typing(app, response, settings):
        return False
    captured = response.get("_typing_target")
    expected_hwnd = getattr(locked_target, "hwnd", None)
    expected_process = getattr(locked_target, "process", None)
    expected_pid = getattr(expected_process, "pid", None)
    expected_name = Path(str(getattr(expected_process, "name", ""))).name.casefold()
    captured_name = (
        Path(captured.process_name).name.casefold()
        if isinstance(captured, TypingTarget)
        else ""
    )
    expected_stable_id = f"win:{expected_hwnd}:{expected_pid}"
    if (
        not isinstance(captured, TypingTarget)
        or captured.window_handle != expected_hwnd
        or captured.stable_id.casefold() != expected_stable_id.casefold()
        or not expected_name
        or captured_name != expected_name
        or not _target_is_valid(False)
    ):
        logger.warning("Computer Use Unicode target did not match the locked window")
        return False
    if not app._guard.check(
        "type_text",
        response,
        cancel_check=cancel_event.is_set,
    ):
        return False
    if cancel_event.is_set() or not _target_is_valid(False):
        return False
    preview = response.get("_typing_preview")
    if not isinstance(preview, TypingPreview):
        return False
    preview_approved = False
    if preview.reasons:
        if not _confirm_typing_preview(
            app,
            preview,
            text,
            preview_only=False,
            cancel_check=cancel_event.is_set,
        ):
            return False
        preview_approved = True
        if not _target_is_valid(False):
            return False
    dependencies = response.get("_typing_dependencies")
    if dependencies is None or cancel_event.is_set() or not _target_is_valid(False):
        return False
    dependencies.cancel_requested = lambda: (
        cancel_event.is_set()
        or bool(getattr(app, "_cancel_event", None) and app._cancel_event.is_set())
    )
    dependencies.shutdown_requested = lambda: bool(getattr(app, "_closing", False))
    dependencies.effect_authorized = lambda: _target_is_valid(True)
    original_activate = dependencies.activate_target
    if original_activate is not None:
        dependencies.activate_target = lambda target: bool(
            _target_is_valid(False)
            and original_activate(target)
            and _target_is_valid(True)
        )
    if effect_runner is not None:
        _bind_typing_effect_runner(dependencies, effect_runner)
    try:
        result = type_unicode_text(
            text,
            mode=str(response.get("mode", settings.unicode_typing_mode)),
            speed=str(response.get("speed", "normal")),
            restore_clipboard=bool(response.get("restore_clipboard", True)),
            preview_approved=preview_approved,
            intended_target=captured,
            dependencies=dependencies,
            own_window_handles=tuple(
                handle
                for handle in (
                    getattr(getattr(app, "_screen", None), "_own_hwnd", None),
                )
                if isinstance(handle, int)
            ),
            preview_threshold=settings.unicode_typing_preview_threshold,
            paced_delay_ms=settings.unicode_typing_delay_ms,
        )
    except Exception:
        return False
    return bool(result.success and not cancel_event.is_set())


def _deep_ocr_focused_only(response: dict) -> bool:
    raw = response.get("focused_only", True)
    return (
        raw if isinstance(raw, bool)
        else str(raw).strip().lower() not in {"0", "no", "false", "off"}
    )


def _block_recursive_deep_ocr(response: dict | None) -> dict | None:
    if not response or response.get("command") != "analyze_screen_deep":
        return response
    logger.warning("Blocked recursive analyze_screen_deep follow-up")
    safe = dict(response)
    safe["command"] = "speak" if safe.get("segments") else "idle"
    safe.pop("focused_only", None)
    safe.pop("prompt", None)
    return safe


def dispatch(
    app: "CompanionApp",
    response: dict,
    user_message: str | None = None,
    *,
    origin: RequestOrigin | None = None,
) -> None:
    """Route a parsed AI response to the appropriate handler."""
    resolved_origin = normalize_request_origin(
        origin,
        default="ambient" if user_message is None else "user",
    )
    if not isinstance(response, dict):
        logger.warning("Blocked malformed AI response before command dispatch")
        _schedule_app_ui(app, lambda: app._set_state(app.STATE_IDLE))
        app._reschedule_screen_poll()
        return
    command = response.get("command", "idle")
    spec = get_command_spec(command)
    if not isinstance(command, str) or spec is None:
        logger.warning("Blocked unknown AI command before Command Guard")
        _schedule_app_ui(app, lambda: app._set_state(app.STATE_IDLE))
        app._reschedule_screen_poll()
        return
    ctx = DispatchCtx(
        user_message=user_message,
        origin=resolved_origin,
        mood=response.get("mood", "neutral"),
        segments=response.get("segments", []),
        shutdown_requested=bool(response.get("shutdown", False)),
    )

    if response.get("groq_exhausted"):
        _schedule_app_ui(app, lambda: app._subtitle.show_message(
            "You reached your limit with your Groq keys", "#ff4444"))
        _schedule_app_ui(app, lambda: app._set_state(app.STATE_IDLE))
        app._reschedule_screen_poll()
        return

    # Defense in depth: an ambient model turn must not even open a confirmation
    # dialog for deep OCR, let alone capture or transmit a screenshot.
    if command == "analyze_screen_deep" and resolved_origin != "user":
        logger.info("analyze_screen_deep ignored during an ambient turn")
        _schedule_app_ui(app, app._reschedule_screen_poll)
        return

    if command == "analyze_screen_deep" and _deep_ocr_focused_only(response):
        response["_deep_capture_target"] = None
        if app._screen:
            try:
                response["_deep_capture_target"] = (
                    app._screen.preserve_external_target()
                )
            except Exception as exc:
                logger.warning(f"Could not preserve deep-OCR target: {exc}")

    # Sentinel Explain is explicit analysis, never authority for actions,
    # focus-stealing popup UI, or application shutdown.  Provider output is
    # constrained to a passive subtitle response regardless of model fields.
    if resolved_origin == "terminal_sentinel":
        response = dict(response)
        ctx.shutdown_requested = False
        if command not in {"idle", "speak"}:
            logger.info("Blocked command from Terminal Sentinel explanation: %s", command)
            command = "speak" if ctx.segments else "idle"
            response["command"] = command
        response["shutdown"] = False
        response.pop("popup", None)

    # A tool result is untrusted context, never a fresh grant of user
    # authority.  Bounded read-only chaining is owned by ContinuationEngine
    # before dispatch; a bare/legacy tool_result may only finish passively.
    if resolved_origin == "tool_result":
        response = dict(response)
        ctx.shutdown_requested = False
        if command not in {"idle", "speak"}:
            logger.info("Blocked command from bare tool result: %s", command)
            command = "speak" if ctx.segments else "idle"
            response["command"] = command
        response["shutdown"] = False
        response.pop("popup", None)

    ambient_relevance = normalize_ambient_relevance(
        response.get("ambient_relevance"),
    )
    if resolved_origin == "ambient":
        # Presentation metadata never upgrades ambient authority. Ambient model
        # output is passive even if a provider returns a protected command.
        if command not in {"idle", "speak"} or ambient_relevance is AmbientRelevance.MUNDANE:
            command = "idle"
            response = {
                "command": "idle",
                "mood": response.get("mood", "neutral"),
                "segments": [],
                "shutdown": False,
                "ambient_relevance": ambient_relevance.value,
            }
            ctx.segments = []
            ctx.shutdown_requested = False

    settings = get_settings()
    capability_decision = _capability_decision(app, command, settings)
    if not capability_decision.allowed:
        _deny_capability(app, capability_decision)
        return
    capability_authorization = _authorize_capability(app, command, settings)
    if capability_authorization is None:
        _deny_capability(app, capability_decision)
        return
    response[_CAPABILITY_AUTHORIZATION] = capability_authorization

    # CommandSpec can narrow authority that survived the central origin policy;
    # it never upgrades ambient, tool-result, or Sentinel authority. Keep this
    # after capability denial to preserve the established Compact-mode ordering.
    spec = get_command_spec(command)
    if spec is None or resolved_origin not in spec.allowed_origins:
        logger.info(
            "Command ignored for request origin: command=%s origin=%s",
            command,
            resolved_origin,
        )
        if command == "computer_use":
            app._speak_and_continue(
                [{"text": "Computer Use needs a direct request from you.", "pause": 0.0}],
                "neutral",
                False,
            )
        else:
            _schedule_app_ui(app, lambda: app._set_state(app.STATE_IDLE))
            app._reschedule_screen_poll()
        return

    if command == "computer_use":
        if not settings.enable_computer_use or not settings.enable_command_execution:
            logger.info("Computer Use blocked by its local feature gate")
            app._speak_and_continue(
                [{"text": "Computer Use is off in settings.", "pause": 0.0}],
                "neutral",
                False,
            )
            return
    if command == "type_text" and not _prepare_unicode_typing(app, response, settings):
        return

    response_presence = None
    if resolved_origin == "ambient":
        presence_method = getattr(type(app), "_presence_decision", None)
        if callable(presence_method):
            try:
                urgency = (
                    "important"
                    if ambient_relevance is AmbientRelevance.IMPORTANT
                    else "nonurgent"
                )
                try:
                    response_presence = presence_method(app, urgency=urgency)
                except TypeError:
                    response_presence = presence_method(app)
            except (AttributeError, TypeError, ValueError) as exc:
                logger.warning(
                    "Presence decision failed closed for response UI: %s",
                    type(exc).__name__,
                )
    ambient_presence = response_presence
    if resolved_origin == "ambient":
        if (
            ambient_presence is not None
            and command in {"speak", "wake_user", "popup"}
            and (
                not bool(getattr(ambient_presence, "allow_popup", False))
                or bool(getattr(ambient_presence, "queue_nonurgent", False))
            )
        ):
            queued_text = " ".join(
                str(item.get("text", "")).strip()
                for item in ctx.segments
                if isinstance(item, dict) and item.get("text")
            )[:800]
            presence_owner = getattr(app, "_presence", None)
            if queued_text and presence_owner is not None:
                try:
                    presence_owner.queue_message(queued_text, ttl_seconds=300)
                except (TypeError, ValueError, RuntimeError) as exc:
                    logger.warning("Ambient presence queue rejected a message: %s", type(exc).__name__)
            _schedule_app_ui(app, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
            app._reschedule_screen_poll()
            return

    if resolved_origin == "ambient" and ctx.mood in app._ATTENTION_MOODS:
        app._maybe_snap_to_center(ctx.mood)
    elif resolved_origin != "terminal_sentinel" and hasattr(app, "_play_response_motion"):
        app._play_response_motion(ctx.mood)

    if command in _WINDOW_COMMANDS and not get_settings().enable_window_control:
        logger.info(f"Blocked (ENABLE_WINDOW_CONTROL=no): {command}")
        denied = [{"text": "Window control is off in config.", "pause": 0.0}]
        app._speak_and_continue(denied, "neutral", False)
        return

    _dry_run_skip = frozenset({
        "idle", "speak", "wake_user", "change_mood", "view_memory",
        "search_memory", "search_web", "fetch_webpage",
    })
    if settings.dry_run_mode and command not in _dry_run_skip:
        details = app._guard.describe(command, response)
        proceed = app._guard.check_dry_run(command, details)
        if not proceed:
            _schedule_app_ui(app, lambda: app._subtitle.show_message("Dry run — skipped.", "#ffaa00"))
            if hasattr(app, "flash_error_gif"):
                try:
                    app.flash_error_gif()
                except Exception:
                    pass
            denied = [{"text": "Dry run. I won't.", "pause": 0.0}]
            app._speak_and_continue(denied, "angry", False)
            return
        _schedule_app_ui(app, lambda: app._subtitle.show_message(f"DRY RUN OK: {command}", "#ffaa00"))

    if command not in ("idle", "speak", "wake_user") and not app._guard.check(command, response):
        logger.info(f"User denied command: {command}")
        if hasattr(app, "flash_error_gif"):
            try:
                app.flash_error_gif()
            except Exception:
                pass
        # Emotion: a safety denial is mild disappointment only — never betrayal.
        try:
            from agetha.core.emotion_engine import note
            note("command_declined", summary=f"user declined command {command}")
        except Exception as exc:
            logger.debug("Command-declined emotion update failed: %s", type(exc).__name__)
        denied = [{"text": "Fine. I won't.", "pause": 0.0}]
        app._speak_and_continue(denied, "angry", False)
        return

    if command == "type_text":
        preview = response.get("_typing_preview")
        if not isinstance(preview, TypingPreview):
            app._show_op_error("Typing target preview is unavailable.")
            return
        if preview.reasons:
            preview_only = response.get("mode") == "preview"
            if not _confirm_typing_preview(
                app,
                preview,
                response.get("text", ""),
                preview_only=preview_only,
            ):
                logger.info("Unicode typing preview was cancelled or unavailable")
                app._speak_and_continue(
                    [{"text": "ยกเลิกแล้ว ฉันยังไม่ได้พิมพ์", "pause": 0.0}],
                    "neutral",
                    False,
                )
                return
            response["_typing_preview_approved"] = True
        response["_typing_guard_token"] = _TYPE_TEXT_GUARD_APPROVAL

    if command not in ("idle", "speak", "wake_user"):
        try:
            from agetha.core.companion_stats import update_stats
            update_stats("command")
        except Exception:
            pass
        if command not in _EMOTION_READONLY_COMMANDS:
            try:
                from agetha.core.emotion_engine import note
                note("command_approved", summary=f"user approved command {command}")
            except Exception:
                pass

    ambient_voice_blocked = (
        resolved_origin == "ambient"
        and ambient_presence is not None
        and not bool(getattr(ambient_presence, "allow_voice", False))
    )
    if not ambient_voice_blocked and app._try_short_mood_speak(command, ctx):
        return

    handler = (
        HANDLERS.get(spec.handler_key)
        if spec.dispatch_kind is DispatchKind.HANDLER
        else None
    )
    if handler:
        # Guard/preview/UI waits can overlap a live Full→Compact downgrade.
        # Recheck the app-owned generation/profile before entering any handler;
        # effectful engines recheck again at their immediate OS boundary.
        latest_settings = get_settings()
        latest_decision = _capability_decision(app, command, latest_settings)
        if (
            not latest_decision.allowed
            or not _authorization_is_current(
                app, response.get(_CAPABILITY_AUTHORIZATION),
            )
        ):
            _deny_capability(app, latest_decision)
            return
        if handler(app, response, ctx):
            return

    popup = response.get("popup")
    if popup and isinstance(popup, list) and popup:
        from main import AgethaPopup
        _schedule_app_ui(app, lambda: AgethaPopup(app.root, popup, ctx.mood))
        _schedule_app_ui(app, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
        app._reschedule_screen_poll()
        return

    if command in ("wake_user", "speak") and ctx.segments:
        if response_presence is None and resolved_origin != "terminal_sentinel":
            app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
        else:
            app._speak_and_continue(
                ctx.segments,
                ctx.mood,
                ctx.shutdown_requested,
                allow_audio=(
                    resolved_origin != "terminal_sentinel"
                    and bool(getattr(response_presence, "allow_voice", False))
                ),
            )
    else:
        app._persistent_mood = None
        if command == "idle" and not ctx.segments:
            _schedule_app_ui(app, lambda: app._subtitle.clear())
        _schedule_app_ui(app, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
        app._reschedule_screen_poll()


# ── Handlers ──────────────────────────────────────────────────────────────────

@register("show_error_gif")
def handle_show_error_gif(app, response, ctx):
    from main import ASSETS, GifPlayer
    path = response.get("path", "") or str(ASSETS / "error.gif")
    def _show() -> None:
        try:
            gif_path = Path(path)
            if not gif_path.exists():
                gif_path = ASSETS / "error.gif"
            player = GifPlayer(app._gif_label, str(gif_path), app.root.after)
            if app._current_gif_player:
                app._current_gif_player.stop()
            app._current_gif_player = player
            player.play()
            app._set_state(app.STATE_IDLE, "neutral")
        except Exception as exc:
            logger.error("show_error_gif failed safely: %s", type(exc).__name__)

    _schedule_app_ui(app, _show)
    return True


@register("move_window")
def handle_move_window(app, response, ctx):
    x, y = response.get("x"), response.get("y")
    direction = str(response.get("direction", "")).lower()
    capability_authorization = response.get(_CAPABILITY_AUTHORIZATION)

    def _go() -> None:
        try:
            sw, sh = app.root.winfo_screenwidth(), app.root.winfo_screenheight()
            ww = app.root.winfo_width() or WINDOW_W
            wh = app.root.winfo_height() or WINDOW_H
            if x is not None and y is not None:
                nx, ny = int(x), int(y)
            else:
                curx, cury = app.root.winfo_x(), app.root.winfo_y()
                dirs = {
                    "left": (10, cury), "right": (max(0, sw - ww - 10), cury),
                    "up": (curx, 10), "down": (curx, max(0, sh - wh - 50)),
                    "center": (max(0, (sw - ww) // 2), max(0, (sh - wh) // 2)),
                }
                nx, ny = dirs.get(direction, (10, cury))
            app.animate_geometry(
                nx,
                ny,
                effect_runner=lambda effect: _perform_authorized_effect(
                    app, capability_authorization, effect,
                ),
            )
        except Exception as exc:
            logger.error("move_window failed safely: %s", type(exc).__name__)

    _schedule_app_ui(app, _go)
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True









@register("get_clipboard")
def handle_get_clipboard(app, response, ctx):
    content = _call_app_ui_sync(app, lambda: get_clipboard(app.root))
    content = str(content or "[clipboard unavailable]")

    def _requery():
        _schedule_app_ui(app, lambda: app._set_state(app.STATE_THINKING))
        follow = app._ai_query(
            "",
            doc_content=f"Clipboard contents:\n{content[:500]}",
            request_profile="fast_tool_result",
        )
        if follow:
            dispatch(app, follow, ctx.user_message, origin="tool_result")

    _start_app_worker(app, _requery, "clipboard-requery")
    return True






@register("read_document")
def handle_read_document(app, response, ctx):
    doc_path = response.get("path", "").strip()
    doc_content = app._ai.read_document(doc_path) if app._ai and doc_path else "[no path]"

    def _requery():
        follow = app._ai_query(
            "", doc_content=doc_content, request_profile="fast_tool_result",
        )
        if follow:
            dispatch(app, follow, ctx.user_message, origin="tool_result")

    _start_app_worker(app, _requery, "document-requery")
    return True


@register("read_file")
def handle_read_file(app, response, ctx):
    return handle_read_document(app, response, ctx)





@register("monitor_process")
def handle_monitor_process(app, response, ctx):
    process_name = response.get("process_name", "").strip()
    if not process_name or not app._ai:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
        return True

    _schedule_app_ui(app, lambda: app._subtitle.show_message(f"Checking {process_name}…", "#888888"))

    def _check():
        running = app._ai.monitor_process(process_name)
        status = "running" if running else "not running"
        color = "#44cc66" if running else "#ffaa00"
        _schedule_app_ui(app, lambda: app._subtitle.show_message(f"{process_name}: {status}", color))
        follow = app._ai_query(
            f"[SYSTEM] Process '{process_name}' is {status}.",
            request_profile="fast_command",
        )
        if follow:
            dispatch(app, follow, ctx.user_message, origin="tool_result")

    _start_app_worker(app, _check, "process-monitor")
    return True


@register("get_active_app")
def handle_get_active_app(app, response, ctx):
    awareness = getattr(app, "_process_awareness", None)
    if awareness is None:
        detail = "Process awareness is unavailable."
    else:
        try:
            snapshot = awareness.snapshot("foreground_only")
            detail = awareness.provider_context(snapshot) or "Foreground application unavailable."
        except Exception:
            detail = "Foreground application unavailable."
    app._show_op_success(detail[:140])
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("list_running_apps")
def handle_list_running_apps(app, response, ctx):
    awareness = getattr(app, "_process_awareness", None)
    if awareness is None:
        detail = "Process awareness is unavailable."
    else:
        try:
            snapshot = awareness.snapshot("visible_apps")
            detail = awareness.provider_context(snapshot) or "No visible applications found."
        except Exception:
            detail = "Visible application list unavailable."
    app._show_op_success(detail[:140])
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("play_emotion_sound")
def handle_emotion_sound(app, response, ctx):
    emotion = response.get("emotion", "angry").strip()
    _start_app_worker(app, lambda: app._play_emotion_sound(emotion), "emotion-sound")
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("show_dialog")
def handle_show_dialog(app, response, ctx):
    dlg_type = response.get("dialog_type", "info").strip().lower()
    dlg_title = response.get("title", "Agetha").strip()
    dlg_msg = response.get("message", "").strip()

    def _show():
        guard = CommandGuard(app.root)
        if dlg_type == "yesno":
            ok = guard._native_confirm(dlg_title, dlg_msg, "info", "yesno", default_no=True)
            if ok and app._ai:
                def _requery_yes():
                    follow = app._ai_query(
                        f"[SYSTEM] User answered YES to: {dlg_msg[:120]}",
                        request_profile="fast_command",
                    )
                    if follow:
                        dispatch(app, follow, ctx.user_message, origin="tool_result")
                _start_app_worker(app, _requery_yes, "dialog-requery")
        elif dlg_type == "warning":
            guard._native_confirm(dlg_title, dlg_msg, "warning", "okcancel", False)
        elif dlg_type == "error":
            guard._native_confirm(dlg_title, dlg_msg, "error", "okcancel", False)
        else:
            guard._native_confirm(dlg_title, dlg_msg, "info", "okcancel", False)

    _schedule_app_ui(app, _show)
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("snap_to_center")
def handle_snap_to_center(app, response, ctx):
    def _snap():
        sw, sh = app.root.winfo_screenwidth(), app.root.winfo_screenheight()
        nx = (sw - WINDOW_W) // 2
        ny = (sh - WINDOW_H) // 2

        def _after():
            try:
                app.root.attributes("-topmost", True)
            except Exception:
                pass
            app.root.lift()

        app.animate_geometry(nx, ny, on_done=_after)

    _schedule_app_ui(app, _snap)
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("target_window_move")
def handle_target_move(app, response, ctx):
    _target_window_op(app, response, ctx, move=True)
    return True


@register("target_window_resize")
def handle_target_resize(app, response, ctx):
    _target_window_op(app, response, ctx, move=False)
    return True


@register("target_window_close")
def handle_target_close(app, response, ctx):
    capability_authorization = response.pop(_CAPABILITY_AUTHORIZATION, None)
    target_app = response.get("target_app", "").strip()
    if not target_app:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
        return True
    if is_self_window_target(target_app):
        fail = [
            {"text": "I'm not closing myself.", "pause": 0.5},
            {"text": "Try a real app window.", "pause": 0.0},
        ]
        app._speak_and_continue(fail, ctx.mood, ctx.shutdown_requested)
        return True

    def _close():
        exclude = _self_hwnd(app)
        picker = _window_picker(app)
        if IS_WINDOWS:
            hwnd, title = resolve_target_hwnd(
                target_app, exclude_hwnd=exclude, picker=picker,
            )
            if not hwnd:
                _finish_target_op(
                    app,
                    ctx,
                    False,
                    f"Window not found: {target_app}",
                    capability_authorization=capability_authorization,
                )
                return
            performed, result = _perform_authorized_effect(
                app,
                capability_authorization,
                lambda: close_window(hwnd),
            )
            if not performed:
                logger.info("Window close cancelled after capability invalidation")
                return
            ok, msg = result
            if ok:
                msg = f"Close sent to: {title}"
            elif title:
                msg = f"{title}: {msg}"
            _finish_target_op(
                app,
                ctx,
                ok,
                msg,
                capability_authorization=capability_authorization,
            )
            return
        if IS_LINUX:
            performed, wmctrl_result = _perform_authorized_effect(
                app,
                capability_authorization,
                lambda: subprocess.run(
                    ["wmctrl", "-c", target_app], timeout=3, capture_output=True,
                ),
            )
            if not performed:
                logger.info("Window close fallback cancelled after capability invalidation")
                return
            if wmctrl_result.returncode == 0:
                _finish_target_op(
                    app,
                    ctx,
                    True,
                    "wmctrl closed window",
                    capability_authorization=capability_authorization,
                )
                return
            detail = getattr(wmctrl_result, "stderr", b"") or getattr(
                wmctrl_result, "stdout", b"",
            )
            if isinstance(detail, bytes):
                detail = detail.decode(errors="replace")
            _finish_target_op(
                app,
                ctx,
                False,
                str(detail).strip() or "wmctrl failed",
                capability_authorization=capability_authorization,
            )
            return
        _finish_target_op(
            app,
            ctx,
            False,
            "Not supported on this platform",
            capability_authorization=capability_authorization,
        )

    _start_app_worker(app, _close, "window-close")
    return True


@register("open_app")
def handle_open_app(app, response, ctx):
    app_name = response.get("app", "").strip() or response.get("app_name", "").strip()
    if app_name:
        command = (
            ("open", app_name)
            if platform.system() == "Darwin"
            else (app_name,)
        )
        if not guarded_launch_application(
            app,
            command,
            guard_approved=True,
            capability_authorization=response.get(_CAPABILITY_AUTHORIZATION),
        ):
            app._show_op_error("Launch failed.")
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    _schedule_app_ui(app, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
    app._reschedule_screen_poll()
    return True


@register("computer_use")
def handle_computer_use(app, response, ctx):
    """Delegate one Guard-approved direct request to the app-owned session."""
    if ctx.origin != "user" or not ctx.user_message:
        logger.info("Computer Use handler rejected a non-user activation")
        app._speak_and_continue(
            [{"text": "Computer Use needs a direct request from you.", "pause": 0.0}],
            "neutral",
            False,
        )
        return True
    starter = getattr(app, "_start_computer_use_request", None)
    if not callable(starter):
        app._speak_and_continue(
            [{"text": "Computer Use is unavailable right now.", "pause": 0.0}],
            "neutral",
            False,
        )
        return True
    starter(ctx.user_message, response)
    return True


@register("force_close")
def handle_force_close(app, response, ctx):
    capability_authorization = response.pop(_CAPABILITY_AUTHORIZATION, None)
    target = CommandGuard._process_target(response)
    if target and is_self_process_target(target):
        fail = [
            {"text": "I'm not killing myself.", "pause": 0.5},
            {"text": "Nice try.", "pause": 0.0},
        ]
        app._speak_and_continue(fail, ctx.mood, ctx.shutdown_requested)
        return True
    if target:
        def _kill():
            exclude = _self_hwnd(app)
            picker = _window_picker(app)
            if IS_WINDOWS:
                hwnd, title = resolve_target_hwnd(
                    target, exclude_hwnd=exclude, picker=picker,
                )
                if hwnd:
                    performed, result = _perform_authorized_effect(
                        app,
                        capability_authorization,
                        lambda: kill_process_by_hwnd(hwnd),
                    )
                    if not performed:
                        logger.info("Force close cancelled after capability invalidation")
                        return
                    ok, msg = result
                    if ok:
                        msg = f"Killed process for: {title}"
                    elif title:
                        msg = f"{title}: {msg}"
                else:
                    ok, msg = False, f"Window not found: {target}"
            else:
                ok, msg = False, f"Window not found: {target}"
            if not ok:
                performed, result = _perform_authorized_effect(
                    app,
                    capability_authorization,
                    lambda: kill_process_by_name(target),
                )
                if not performed:
                    logger.info("Force close fallback cancelled after capability invalidation")
                    return
                ok, msg = result
            if not ok:
                _finish_target_op(
                    app,
                    ctx,
                    False,
                    msg or "Process not found.",
                    capability_authorization=capability_authorization,
                )
                return
            _finish_target_op(
                app,
                ctx,
                ok,
                msg,
                capability_authorization=capability_authorization,
            )

        _start_app_worker(app, _kill, "force-close")
        return True
    segs = ctx.segments or [{"text": "Gone.", "pause": 0.0}]
    app._speak_and_continue(segs, ctx.mood, ctx.shutdown_requested)
    return True


@register("open_browser")
def handle_open_browser(app, response, ctx):
    url = response.get("url", "").strip()
    search = response.get("search", "").strip()
    if not url and search:
        engines = {
            "google": "https://www.google.com/search?q=",
            "duckduckgo": "https://duckduckgo.com/?q=",
            "bing": "https://www.bing.com/search?q=",
        }
        url = engines.get(response.get("engine", "google"), engines["google"]) + urllib.parse.quote_plus(search)
    if url:
        webbrowser.open(url)
    _schedule_app_ui(app, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
    app._reschedule_screen_poll()
    return True


@register("open_url")
def handle_open_url(app, response, ctx):
    open_url(response.get("url", "").strip())
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("system_info")
def handle_system_info(app, response, ctx):
    info = system_info()

    def _requery():
        follow = app._ai_query(
            "",
            doc_content=f"System info:\n{info}",
            request_profile="fast_tool_result",
        )
        if follow:
            dispatch(app, follow, ctx.user_message, origin="tool_result")

    _start_app_worker(app, _requery, "system-info-requery")
    return True





@register("type_text")
def handle_type_text(app, response, ctx):
    settings = get_settings()
    approved = response.pop("_typing_guard_token", None) is _TYPE_TEXT_GUARD_APPROVAL
    capability_authorization = response.pop(_CAPABILITY_AUTHORIZATION, None)
    if (
        not approved
        or not settings.enable_command_execution
        or not settings.enable_unicode_typing
    ):
        logger.warning("Direct or disabled Unicode typing handler call was blocked")
        app._show_op_error("Unicode typing was blocked by command policy.")
        app._speak_and_continue(
            [{"text": "คำสั่งพิมพ์ไม่ผ่านการยืนยัน ฉันหยุดไว้ก่อน", "pause": 0.0}],
            "neutral",
            False,
        )
        return True

    dependencies = response.pop("_typing_dependencies", None)
    target = response.pop("_typing_target", None)
    preview = response.pop("_typing_preview", None)
    preview_approved = bool(response.pop("_typing_preview_approved", False))
    if dependencies is None or not isinstance(preview, TypingPreview):
        app._show_op_error("Unicode typing preflight state is unavailable.")
        return True

    operation_cancel = threading.Event()
    previous_cancel = getattr(app, "_typing_cancel_event", None)
    if previous_cancel is not None:
        try:
            previous_cancel.set()
        except Exception as exc:
            logger.debug("Previous Unicode typing cancellation failed: %s", type(exc).__name__)
    app._typing_cancel_event = operation_cancel
    operation_lock = getattr(app, "_typing_operation_lock", None)
    if operation_lock is None:
        operation_lock = threading.Lock()
        app._typing_operation_lock = operation_lock
    dependencies.cancel_requested = lambda: (
        operation_cancel.is_set()
        or bool(getattr(app, "_cancel_event", None) and app._cancel_event.is_set())
    )
    dependencies.shutdown_requested = lambda: bool(getattr(app, "_closing", False))
    dependencies.effect_authorized = lambda: _authorization_is_current(
        app, capability_authorization,
    )
    _bind_capability_effect_boundaries(
        app, capability_authorization, dependencies,
    )

    text = response.get("text", "")
    mode = response.get("mode", settings.unicode_typing_mode)
    speed = response.get("speed", "normal")
    restore_clipboard = bool(response.get(
        "restore_clipboard", settings.unicode_typing_restore_clipboard,
    ))

    def _run() -> None:
        with operation_lock:
            result = type_unicode_text(
                text,
                mode=mode,
                speed=speed,
                restore_clipboard=restore_clipboard,
                preview_approved=preview_approved,
                intended_target=target if isinstance(target, TypingTarget) else None,
                dependencies=dependencies,
                own_window_handles=tuple(
                    handle for handle in (getattr(getattr(app, "_screen", None), "_own_hwnd", None),)
                    if isinstance(handle, int)
                ),
                preview_threshold=settings.unicode_typing_preview_threshold,
                paced_delay_ms=settings.unicode_typing_delay_ms,
            )
        logger.info(
            "Unicode typing finished: method=%s success=%s requested=%d sent=%d restored=%s",
            result.method,
            result.success,
            result.characters_requested,
            result.characters_sent,
            result.clipboard_restored,
        )
        if getattr(app, "_typing_cancel_event", None) is operation_cancel:
            app._typing_cancel_event = None
        if getattr(app, "_closing", False):
            return
        if result.success and result.method == "preview":
            app._show_op_success(result.message)
            app._speak_and_continue(
                [{"text": "ดูตัวอย่างแล้ว ยังไม่ได้พิมพ์", "pause": 0.0}],
                "neutral",
                False,
            )
            return
        if result.success:
            app._show_op_success(result.message)
            app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
            return
        app._show_op_error(result.message)
        message = "พิมพ์ไม่ได้ ลองใหม่ได้"
        if result.method == "clipboard-copy-only":
            message = "พิมพ์อัตโนมัติไม่ได้ คัดลอกไว้แล้ว กด Ctrl+V ได้เลย"
        elif "focused window changed" in result.message.casefold():
            message = "หน้าต่างเปลี่ยน ฉันหยุดก่อน"
        app._speak_and_continue(
            [{"text": message, "pause": 0.0}],
            "neutral",
            False,
        )

    _start_app_worker(app, _run, name="unicode-typing")
    return True





@register("set_reminder")
def handle_set_reminder(app, response, ctx):
    capability_authorization = response.pop(_CAPABILITY_AUTHORIZATION, None)
    try:
        seconds = int(response.get("seconds", 300))
    except (TypeError, ValueError):
        seconds = 300
    text = response.get("reminder_text", "Reminder").strip()

    def _authorized() -> bool:
        return _authorization_is_current(app, capability_authorization)

    def _remind(msg):
        if not _authorized():
            logger.info("Reminder cancelled after capability invalidation")
            return

        def _show_reminder() -> None:
            if _authorized():
                app._subtitle.show_message(msg, "#ff6600")

        _schedule_app_ui(app, _show_reminder)
        if not _authorized():
            return
        if app._ai:
            if not _authorized():
                return
            follow = app._ai_query(
                msg,
                request_profile="fast_command",
                origin="reminder",
                result_is_current=_authorized,
            )
            if follow and _authorized():
                dispatch(app, follow, None, origin="reminder")

    set_reminder(
        seconds,
        text,
        _remind,
        cancel_check=lambda: not _authorized(),
    )
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True








# ── v5.0.0 — safe Windows integration ────────────────────────────────────────






@register("request_screen_read")
def handle_request_screen_read(app, response, ctx):
    requester = getattr(app, "_request_read_only_context_dependency", None)
    if callable(requester):
        requester(
            ContextRequest(ContextKind.SCREEN),
            ctx.user_message or "",
            origin=ctx.origin,
        )
    return True


@register("analyze_screen_deep")
def handle_analyze_screen_deep(app, response, ctx):
    """Run optional deep OCR only after a direct user-triggered AI turn."""
    ctx_origin = getattr(ctx, "origin", None)
    if ctx_origin not in REQUEST_ORIGINS:
        ctx_origin = "user" if ctx.user_message else "ambient"
    if not ctx.user_message or ctx_origin != "user":
        logger.info("analyze_screen_deep ignored without an explicit user request")
        _schedule_app_ui(app, lambda: app._subtitle.show_message(
            "Deep OCR only runs after you ask for it.", "#ff6600",
        ))
        _schedule_app_ui(app, app._reschedule_screen_poll)
        return True
    if not app._screen:
        _schedule_app_ui(app, lambda: app._subtitle.show_message(
            "Screen capture is unavailable.", "#ff4444",
        ))
        _schedule_app_ui(app, app._reschedule_screen_poll)
        return True

    focused_only = _deep_ocr_focused_only(response)
    prompt = str(response.get("prompt", "") or "<image>document parsing.")[:2000]
    if ctx.segments:
        first = str(ctx.segments[0].get("text", "Analyzing…"))[:120]
        _schedule_app_ui(app, lambda text=first: app._subtitle.show_message(text, "#888888"))

    def _analyze_and_requery():
        from agetha.platform.ocr_backends.base import format_deep_ocr_for_prompt

        result = app._screen.capture_deep_text(
            focused_only=focused_only,
            prompt=prompt,
            capture_target=response.get("_deep_capture_target"),
            require_target=(
                focused_only and "_deep_capture_target" in response
            ),
        )
        if not result.ok:
            message = result.text[:300]
            _schedule_app_ui(app, lambda text=message: app._subtitle.show_message(text, "#ff6600"))
            _schedule_app_ui(app, lambda: app._set_state(app.STATE_IDLE, "neutral"))
            _schedule_app_ui(app, app._reschedule_screen_poll)
            return

        wrapped = format_deep_ocr_for_prompt(
            result,
            max_chars=get_settings().deep_ocr_max_output_chars,
        )
        wrapped = app._screen.redact_for_external_context(wrapped)
        follow = app._ai_query(
            ctx.user_message or "",
            screen_context="",
            doc_content=wrapped,
            reserved_ai_slot=True,
            request_profile="deep_analysis",
        )
        follow = _block_recursive_deep_ocr(follow)
        if follow:
            app._dispatch_response(follow, ctx.user_message, origin="tool_result")
        else:
            _schedule_app_ui(app, app._reschedule_screen_poll)

    app._defer_exclusive_ai_operation(_analyze_and_requery)
    return True


def _self_hwnd(app) -> int | None:
    caller = getattr(type(app), "_call_ui_sync", None)
    if callable(caller) and threading.current_thread() is not threading.main_thread():
        value = caller(app, lambda: int(app.root.winfo_id()))
        return int(value) if isinstance(value, int) else None
    try:
        return int(app.root.winfo_id())
    except Exception:
        return None


def _window_picker(app):
    return lambda matches: app.pick_window_sync(matches)


def _finish_target_op(
    app,
    ctx,
    ok: bool,
    msg: str,
    *,
    capability_authorization: object,
) -> None:
    """Speak only after window op finishes — avoids 'Watch this' when nothing moved."""
    if not _authorization_is_current(app, capability_authorization):
        return

    def _schedule_if_current(callback: Callable[[], object]) -> None:
        def _deliver() -> None:
            if _authorization_is_current(app, capability_authorization):
                callback()

        _schedule_app_ui(app, _deliver)

    if ok:
        logger.info(msg)
        _schedule_if_current(lambda: app._show_op_success(msg))
        _schedule_if_current(lambda: app._speak_and_continue(
            ctx.segments, ctx.mood, ctx.shutdown_requested,
        ))
    else:
        logger.warning(f"target_window op failed: {msg}")
        _schedule_if_current(lambda: app._show_op_error(msg))
        fail_segs = [
            {"text": "It's not there.", "pause": 0.5},
            {"text": "No window matched that name.", "pause": 0.0},
        ]
        _schedule_if_current(lambda: app._speak_and_continue(
            fail_segs, ctx.mood, False,
        ))


def _redirect_self_move(app, response, ctx, capability_authorization: object) -> bool:
    """target_window_* aimed at Agetha → move her own window instead."""
    target_app = response.get("target_app", "").strip()
    if not is_self_window_target(target_app):
        return False
    try:
        tx = int(response.get("x", 0) or 0)
        ty = int(response.get("y", 0) or 0)
    except (TypeError, ValueError):
        tx, ty = 0, 0

    def _go():
        if not _authorization_is_current(app, capability_authorization):
            logger.info("Self window move cancelled after capability invalidation")
            return
        app.animate_geometry(
            tx,
            ty,
            effect_runner=lambda effect: _perform_authorized_effect(
                app, capability_authorization, effect,
            ),
        )
        if not _authorization_is_current(app, capability_authorization):
            return
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)

    _schedule_app_ui(app, _go)
    return True


def _target_window_op(app, response, ctx, move: bool):
    capability_authorization = response.pop(_CAPABILITY_AUTHORIZATION, None)
    target_app = response.get("target_app", "").strip()
    if not target_app:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
        return
    if _redirect_self_move(app, response, ctx, capability_authorization):
        return

    try:
        tx, ty = int(response.get("x", 0) or 0), int(response.get("y", 0) or 0)
        tw = int(response.get("width", 800) or 800)
        th = int(response.get("height", 600) or 600)
    except (TypeError, ValueError):
        tx = ty = 0
        tw, th = 800, 600

    def _do():
        exclude = _self_hwnd(app)
        picker = _window_picker(app)
        if IS_WINDOWS:
            hwnd, title = resolve_target_hwnd(
                target_app, exclude_hwnd=exclude, picker=picker,
            )
            if not hwnd:
                _finish_target_op(
                    app,
                    ctx,
                    False,
                    f"Window not found: {target_app}",
                    capability_authorization=capability_authorization,
                )
                return
            if move:
                result = move_window(
                    hwnd,
                    tx,
                    ty,
                    effect_runner=lambda effect: _perform_authorized_effect(
                        app, capability_authorization, effect,
                    ),
                )
            else:
                result = resize_window(
                    hwnd,
                    tx,
                    ty,
                    tw,
                    th,
                    effect_runner=lambda effect: _perform_authorized_effect(
                        app, capability_authorization, effect,
                    ),
                )
            if not _authorization_is_current(app, capability_authorization):
                logger.info("Target window operation cancelled after capability invalidation")
                return
            ok, msg = result
            if ok:
                msg = f"{'Moved' if move else 'Resized'} window: {title}"
            elif title:
                msg = f"{title}: {msg}"
            _finish_target_op(
                app,
                ctx,
                ok,
                msg,
                capability_authorization=capability_authorization,
            )
            return
        if IS_LINUX:
            if move:
                command = ["wmctrl", "-r", target_app, "-e", f"0,{tx},{ty},-1,-1"]
            else:
                command = ["wmctrl", "-r", target_app, "-e", f"0,{tx},{ty},{tw},{th}"]
            performed, _result = _perform_authorized_effect(
                app,
                capability_authorization,
                lambda: subprocess.run(command, timeout=3),
            )
            if not performed:
                logger.info("Target window operation cancelled after capability invalidation")
                return
            _finish_target_op(
                app,
                ctx,
                True,
                "wmctrl sent",
                capability_authorization=capability_authorization,
            )
            return
        _finish_target_op(
            app,
            ctx,
            False,
            "Not supported on this platform",
            capability_authorization=capability_authorization,
        )

    _start_app_worker(app, _do, "target-window-operation")


# Import-time integration invariant: missing, extra, and contradictory handler
# bindings fail deterministically instead of becoming dispatch-time surprises.
validate_handler_bindings(HANDLERS)
