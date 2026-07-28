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
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from agetha.commands.command_guard import CommandGuard
from agetha.commands.system_commands import (
    copy_to_clipboard, get_clipboard, lock_screen, open_folder, open_url,
    restart_system, screenshot_path, search_files, set_reminder, set_volume,
    set_wallpaper, show_notification, shutdown_system, system_info, type_text,
)
from agetha.platform.window_control import kill_process_by_name, operate_on_target, is_self_window_target, is_self_process_target
from agetha.utils import IS_LINUX, IS_WINDOWS, WINDOW_W, WINDOW_H, logger
from agetha.app_config import get_settings

_WINDOW_COMMANDS = frozenset({
    "target_window_move", "target_window_resize", "target_window_close", "force_close",
})

# Inspection-only commands: do not apply command_approved emotion events.
_EMOTION_READONLY_COMMANDS = frozenset({
    "view_emotions", "view_memory", "view_dreams", "list_tasks",
    "search_memory", "recycle_bin_status", "read_notepad",
})

if TYPE_CHECKING:
    from main import CompanionApp

HandlerFn = Callable[["CompanionApp", dict, "DispatchCtx"], bool]


@dataclass
class DispatchCtx:
    user_message: str | None
    mood: str
    segments: list
    shutdown_requested: bool


HANDLERS: dict[str, HandlerFn] = {}


def register(command: str) -> Callable[[HandlerFn], HandlerFn]:
    def deco(fn: HandlerFn) -> HandlerFn:
        HANDLERS[command] = fn
        return fn
    return deco


def dispatch(app: "CompanionApp", response: dict, user_message: str | None = None) -> None:
    """Route a parsed AI response to the appropriate handler."""
    command = response.get("command", "idle")
    ctx = DispatchCtx(
        user_message=user_message,
        mood=response.get("mood", "neutral"),
        segments=response.get("segments", []),
        shutdown_requested=bool(response.get("shutdown", False)),
    )

    if response.get("groq_exhausted"):
        app.root.after(0, lambda: app._subtitle.show_message(
            "You reached your limit with your Groq keys", "#ff4444"))
        app.root.after(0, lambda: app._set_state(app.STATE_IDLE))
        app._reschedule_screen_poll()
        return

    # Defense in depth: an ambient model turn must not even open a confirmation
    # dialog for deep OCR, let alone capture or transmit a screenshot.
    if command == "analyze_screen_deep" and (
        not user_message or user_message == "__touch__"
    ):
        logger.info("analyze_screen_deep ignored during an ambient turn")
        app.root.after(0, app._reschedule_screen_poll)
        return

    if not user_message and ctx.mood in app._ATTENTION_MOODS:
        app._maybe_snap_to_center(ctx.mood)
    elif hasattr(app, "_play_response_motion"):
        app._play_response_motion(ctx.mood)

    if command in _WINDOW_COMMANDS and not get_settings().enable_window_control:
        logger.info(f"Blocked (ENABLE_WINDOW_CONTROL=no): {command}")
        denied = [{"text": "Window control is off in config.", "pause": 0.0}]
        app._speak_and_continue(denied, "neutral", False)
        return

    settings = get_settings()
    _dry_run_skip = frozenset({
        "idle", "speak", "wake_user", "change_mood", "view_memory",
        "search_memory", "search_web", "fetch_webpage",
    })
    if settings.dry_run_mode and command not in _dry_run_skip:
        details = app._guard.describe(command, response)
        proceed = app._guard.check_dry_run(command, details)
        if not proceed:
            app.root.after(0, lambda: app._subtitle.show_message("Dry run — skipped.", "#ffaa00"))
            if hasattr(app, "flash_error_gif"):
                try:
                    app.flash_error_gif()
                except Exception:
                    pass
            denied = [{"text": "Dry run. I won't.", "pause": 0.0}]
            app._speak_and_continue(denied, "angry", False)
            return
        app.root.after(0, lambda: app._subtitle.show_message(f"DRY RUN OK: {command}", "#ffaa00"))

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
        except Exception:
            pass
        denied = [{"text": "Fine. I won't.", "pause": 0.0}]
        app._speak_and_continue(denied, "angry", False)
        return

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

    if app._try_short_mood_speak(command, ctx):
        return

    handler = HANDLERS.get(command)
    if handler:
        if handler(app, response, ctx):
            return

    popup = response.get("popup")
    if popup and isinstance(popup, list) and popup:
        from main import AgethaPopup
        app.root.after(0, lambda: AgethaPopup(app.root, popup, ctx.mood))
        app.root.after(0, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
        app._reschedule_screen_poll()
        return

    if command in ("wake_user", "speak") and ctx.segments:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    else:
        app._persistent_mood = None
        if command == "idle" and not ctx.segments:
            app.root.after(0, lambda: app._subtitle.clear())
        app.root.after(0, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
        app._reschedule_screen_poll()


# ── Handlers ──────────────────────────────────────────────────────────────────

@register("show_error_gif")
def handle_show_error_gif(app, response, ctx):
    from main import ASSETS, GifPlayer
    path = response.get("path", "") or str(ASSETS / "error.gif")
    try:
        gif_path = Path(path)
        if not gif_path.exists():
            gif_path = ASSETS / "error.gif"
        player = GifPlayer(app._gif_label, str(gif_path), app.root.after)
        if app._current_gif_player:
            app._current_gif_player.stop()
        app._current_gif_player = player
        player.play()
        app.root.after(0, lambda: app._set_state(app.STATE_IDLE, "neutral"))
    except Exception as exc:
        logger.error(f"show_error_gif failed: {exc}")
    return True


@register("move_window")
def handle_move_window(app, response, ctx):
    try:
        x, y = response.get("x"), response.get("y")
        direction = str(response.get("direction", "")).lower()
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

        def _go():
            app.animate_geometry(nx, ny)

        app.root.after(0, _go)
    except Exception as exc:
        logger.error(f"move_window failed: {exc}")
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("request_path")
def handle_request_path(app, response, ctx):
    from main import AgethaPopup
    hint = response.get("path_hint", "").strip()
    lines = [hint] if hint else (
        [s.get("text", "") for s in ctx.segments if s.get("text")] or ["Path resolved automatically."]
    )
    app.root.after(0, lambda: AgethaPopup(app.root, lines[:4], ctx.mood))
    app.root.after(0, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
    app._reschedule_screen_poll()
    return True


@register("create_folder")
def handle_create_folder(app, response, ctx):
    path = response.get("path", "").strip()
    if path:
        try:
            os.makedirs(path, exist_ok=True)
            logger.info(f"Created folder: {path}")
        except Exception as exc:
            app._show_op_error(f"Could not create folder: {exc}")
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("create_file")
def handle_create_file(app, response, ctx):
    file_path = response.get("file_path", "").strip()
    if not file_path:
        p, fn = response.get("path", "").strip(), response.get("file_name", "").strip()
        if p and fn:
            file_path = os.path.join(p, fn)
    if file_path:
        try:
            parent = os.path.dirname(file_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as fh:
                fh.write(response.get("content", ""))
            logger.info(f"Created file: {file_path}")
        except Exception as exc:
            app._show_op_error(f"Could not create file: {exc}")
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("delete_file")
def handle_delete_file(app, response, ctx):
    path = response.get("path", "").strip()
    if path:
        try:
            p = Path(path)
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
            else:
                app._show_op_error(f"Not found: {path}")
        except Exception as exc:
            app._show_op_error(f"Delete failed: {exc}")
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("rename_file")
def handle_rename_file(app, response, ctx):
    path, new_name = response.get("path", "").strip(), response.get("new_name", "").strip()
    if path and new_name:
        try:
            Path(path).rename(Path(path).parent / new_name)
        except Exception as exc:
            app._show_op_error(f"Rename failed: {exc}")
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("list_dir")
@register("list_directory")
def handle_list_dir(app, response, ctx):
    from main import AgethaPopup
    req_path = response.get("path", "").strip() or str(app._ai._system_path)
    try:
        p = Path(req_path)
        if not p.exists():
            lines = [f"[not found: {req_path}]"]
        elif not p.is_dir():
            lines = [f"[not a directory: {req_path}]"]
        else:
            entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            lines = [e.name + ("/" if e.is_dir() else "") for e in entries] or ["[empty directory]"]
    except Exception as exc:
        lines = [f"[error: {exc}]"]
    app.root.after(0, lambda: AgethaPopup(app.root, lines[:12], ctx.mood))
    segs = ctx.segments or [{"text": f"{len(lines)} items.", "pause": 0.0}]
    app._speak_and_continue(segs, ctx.mood, ctx.shutdown_requested)
    return True


@register("set_clipboard")
@register("copy_to_clipboard")
def handle_clipboard_set(app, response, ctx):
    text = response.get("text", "").strip()
    if text:
        msg = copy_to_clipboard(text, app.root)
        if msg.startswith("["):
            logger.info(msg)
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("get_clipboard")
def handle_get_clipboard(app, response, ctx):
    content = get_clipboard(app.root)

    def _requery():
        app.root.after(0, lambda: app._set_state(app.STATE_THINKING))
        follow = app._ai_query("", doc_content=f"Clipboard contents:\n{content[:500]}")
        if follow:
            dispatch(app, follow, ctx.user_message)

    threading.Thread(target=_requery, daemon=True).start()
    return True


@register("play_sound")
def handle_play_sound(app, response, ctx):
    sound_name = response.get("sound", "beep").strip().lower()
    sound_path = response.get("path", "").strip()
    try:
        if sound_path and Path(sound_path).exists() and app._bleep:
            app._bleep.play_file(sound_path)
        elif app._bleep:
            freq_map = {"beep": "neutral", "chime": "happy", "error": "angry", "notify": "excited"}
            app._bleep.start_talking(tone=freq_map.get(sound_name, "neutral"))
            threading.Timer(0.8, app._bleep.stop).start()
    except Exception as exc:
        logger.warning(f"play_sound failed: {exc}")
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("take_screenshot")
def handle_screenshot(app, response, ctx):
    save_path = response.get("save_path", "").strip()
    if not save_path and app._ai:
        save_path = screenshot_path(app._ai._system_path)
    if not save_path:
        app._show_op_error("Screenshot failed: no save path available.")
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
        return True
    try:
        if app._screen:
            img = app._screen.capture_image()
            if img:
                img.save(save_path)
                logger.info(f"Screenshot: {save_path}")
    except Exception as exc:
        app._show_op_error(f"Screenshot failed: {exc}")
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("show_notification")
def handle_notification(app, response, ctx):
    title = response.get("title", "Agetha").strip()
    message = response.get("message", "").strip()
    seg_text = " ".join(
        s.get("text", "").strip()
        for s in (response.get("segments") or [])
        if isinstance(s, dict)
    ).strip()
    alt_text = (response.get("text") or response.get("body") or "").strip()
    effective_message = message or seg_text or alt_text
    show_notification(title, effective_message)
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("run_command")
def handle_run_command(app, response, ctx):
    cmd_str = response.get("cmd", "").strip()
    if cmd_str:
        try:
            if re.search(r"[|&;<>$`(){}]", cmd_str):
                app._show_op_error("Shell metacharacters are not allowed in commands.")
            else:
                args = shlex.split(cmd_str, posix=not IS_WINDOWS)
                r = subprocess.run(
                    args, shell=False,
                    capture_output=True, text=True, timeout=15,
                )
                logger.info(f"run_command: {cmd_str} exit={r.returncode}")
        except Exception as exc:
            app._show_op_error(f"Command failed: {exc}")
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("read_document")
def handle_read_document(app, response, ctx):
    doc_path = response.get("path", "").strip()
    doc_content = app._ai.read_document(doc_path) if app._ai and doc_path else "[no path]"

    def _requery():
        follow = app._ai_query("", doc_content=doc_content)
        if follow:
            dispatch(app, follow, ctx.user_message)

    threading.Thread(target=_requery, daemon=True).start()
    return True


@register("read_file")
def handle_read_file(app, response, ctx):
    return handle_read_document(app, response, ctx)


@register("open_file")
def handle_open_file(app, response, ctx):
    file_path = response.get("path", "").strip()
    if file_path:
        try:
            if IS_WINDOWS:
                os.startfile(file_path)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", file_path])
            else:
                subprocess.Popen(["xdg-open", file_path])
        except Exception as exc:
            app._show_op_error(f"Open failed: {exc}")
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("open_folder")
def handle_open_folder(app, response, ctx):
    msg = open_folder(response.get("path", "").strip())
    if "error" in msg.lower():
        app._show_op_error(msg)
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("write_file")
def handle_write_file(app, response, ctx):
    file_path = response.get("file_path", "").strip()
    if file_path and app._ai:
        msg = app._ai.write_file(file_path, response.get("content", ""), response.get("mode", "overwrite"))
        if "error" in msg.lower():
            app._show_op_error(msg)
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("monitor_process")
def handle_monitor_process(app, response, ctx):
    process_name = response.get("process_name", "").strip()
    if not process_name or not app._ai:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
        return True

    app.root.after(0, lambda: app._subtitle.show_message(f"Checking {process_name}…", "#888888"))

    def _check():
        running = app._ai.monitor_process(process_name)
        status = "running" if running else "not running"
        color = "#44cc66" if running else "#ffaa00"
        app.root.after(0, lambda: app._subtitle.show_message(f"{process_name}: {status}", color))
        follow = app._ai_query(f"[SYSTEM] Process '{process_name}' is {status}.")
        if follow:
            dispatch(app, follow, ctx.user_message)

    threading.Thread(target=_check, daemon=True).start()
    return True


@register("play_emotion_sound")
def handle_emotion_sound(app, response, ctx):
    threading.Thread(
        target=app._play_emotion_sound,
        args=(response.get("emotion", "angry").strip(),),
        daemon=True,
    ).start()
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
                    follow = app._ai_query(f"[SYSTEM] User answered YES to: {dlg_msg[:120]}")
                    if follow:
                        dispatch(app, follow, ctx.user_message)
                threading.Thread(target=_requery_yes, daemon=True).start()
        elif dlg_type == "warning":
            guard._native_confirm(dlg_title, dlg_msg, "warning", "okcancel", False)
        elif dlg_type == "error":
            guard._native_confirm(dlg_title, dlg_msg, "error", "okcancel", False)
        else:
            guard._native_confirm(dlg_title, dlg_msg, "info", "okcancel", False)

    app.root.after(0, _show)
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

    app.root.after(0, _snap)
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
        ok, msg = operate_on_target(
            target_app, exclude_hwnd=exclude, close=True, picker=_window_picker(app),
        )
        if not ok and IS_LINUX:
            wmctrl_result = subprocess.run(
                ["wmctrl", "-c", target_app], timeout=3, capture_output=True,
            )
            if wmctrl_result.returncode == 0:
                _finish_target_op(app, ctx, True, "wmctrl closed window")
                return
            _finish_target_op(app, ctx, False, msg)
            return
        _finish_target_op(app, ctx, ok, msg)

    threading.Thread(target=_close, daemon=True).start()
    return True


@register("open_app")
def handle_open_app(app, response, ctx):
    app_name = response.get("app", "").strip() or response.get("app_name", "").strip()
    if app_name:
        try:
            if IS_WINDOWS:
                try:
                    os.startfile(app_name)
                except OSError:
                    subprocess.Popen([app_name])
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", app_name])
            else:
                subprocess.Popen([app_name])
        except Exception as exc:
            app._show_op_error(f"Launch failed: {exc}")
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    app.root.after(0, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
    app._reschedule_screen_poll()
    return True


@register("force_close")
def handle_force_close(app, response, ctx):
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
            ok, msg = operate_on_target(target, exclude_hwnd=exclude, kill=True, picker=picker)
            if not ok:
                ok, msg = kill_process_by_name(target)
            if not ok:
                _finish_target_op(app, ctx, False, msg or "Process not found.")
                return
            _finish_target_op(app, ctx, ok, msg)

        threading.Thread(target=_kill, daemon=True).start()
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
    app.root.after(0, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
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
        follow = app._ai_query("", doc_content=f"System info:\n{info}")
        if follow:
            dispatch(app, follow, ctx.user_message)

    threading.Thread(target=_requery, daemon=True).start()
    return True


@register("set_volume")
def handle_set_volume(app, response, ctx):
    try:
        level = int(response.get("level", 50))
    except (TypeError, ValueError):
        level = 50
    msg = set_volume(level, response.get("action", "set"))
    logger.info(msg)
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("set_wallpaper")
def handle_set_wallpaper(app, response, ctx):
    msg = set_wallpaper(response.get("path", "").strip())
    if "error" in msg.lower() or "not found" in msg.lower():
        app._show_op_error(msg)
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("search_files")
def handle_search_files(app, response, ctx):
    from main import AgethaPopup
    pattern = response.get("pattern", "").strip()
    directory = response.get("directory", "").strip() or (app._ai._system_path if app._ai else "")
    lines = search_files(pattern, directory)
    app.root.after(0, lambda: AgethaPopup(app.root, lines[:12], ctx.mood))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("type_text")
def handle_type_text(app, response, ctx):
    msg = type_text(response.get("text", ""))
    if "error" in msg.lower():
        app._show_op_error(msg)
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("lock_screen")
def handle_lock_screen(app, response, ctx):
    lock_screen()
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("shutdown")
def handle_shutdown(app, response, ctx):
    try:
        delay = int(response.get("delay", 60))
    except (TypeError, ValueError):
        delay = 60
    shutdown_system(delay)
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("restart")
def handle_restart(app, response, ctx):
    try:
        delay = int(response.get("delay", 60))
    except (TypeError, ValueError):
        delay = 60
    restart_system(delay)
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("set_reminder")
def handle_set_reminder(app, response, ctx):
    try:
        seconds = int(response.get("seconds", 300))
    except (TypeError, ValueError):
        seconds = 300
    text = response.get("reminder_text", "Reminder").strip()

    def _remind(msg):
        app.root.after(0, lambda: app._subtitle.show_message(msg, "#ff6600"))
        if app._ai:
            follow = app._ai_query(f"[REMINDER] {msg}")
            if follow:
                dispatch(app, follow, None)

    set_reminder(seconds, text, _remind)
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("change_mood")
def handle_change_mood(app, response, ctx):
    app._persistent_mood = ctx.mood
    app.root.after(0, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
    app._reschedule_screen_poll()
    return True


@register("clear_memory")
def handle_clear_memory(app, response, ctx):
    scope = (response.get("memory_scope") or response.get("scope") or "all").strip().lower()
    try:
        from agetha.core.memory_system import clear_episodic, clear_episodic_selective
        if scope in ("recent", "last_hour"):
            removed = clear_episodic_selective(newer_than_hours=1)
            msg = f"Cleared {removed} recent memories."
        elif scope in ("old", "older_than_day"):
            removed = clear_episodic_selective(older_than_hours=24)
            msg = f"Cleared {removed} old memories."
        elif scope in ("keep_recent", "keep_5"):
            removed = clear_episodic_selective(keep_last=5)
            msg = f"Cleared {removed} memories (kept last 5)."
        else:
            clear_episodic()
            msg = "Episodic memory cleared."
        app.root.after(0, lambda: app._show_op_success(msg))
    except ImportError:
        pass
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("view_memory")
def handle_view_memory(app, response, ctx):
    from main import AgethaPopup
    try:
        from agetha.core.memory_system import get_recent_memories, format_memories_for_display
        limit = int(response.get("limit", 15) or 15)
        lines = format_memories_for_display(get_recent_memories(limit=limit))
    except Exception as exc:
        lines = [f"[memory error: {exc}]"]
    app.root.after(0, lambda: AgethaPopup(app.root, lines or ["[no episodic memories]"], ctx.mood))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("search_memory")
def handle_search_memory(app, response, ctx):
    if ctx.segments:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)

    if not get_settings().enable_longterm_memory:
        memory_context = "[long-term memory search is disabled in config (ENABLE_LONGTERM_MEMORY=no)]"

        def _requery_disabled():
            follow = app._ai_query(
                ctx.user_message or "",
                memory_search_context=memory_context,
                suppress_search_memory=True,
            )
            if follow:
                app._dispatch_response(follow, ctx.user_message)

        threading.Thread(target=_requery_disabled, daemon=True).start()
        return True

    query = (response.get("query") or ctx.user_message or "").strip()
    try:
        limit = int(response.get("limit") or get_settings().longterm_memory_max_results)
    except (TypeError, ValueError):
        limit = get_settings().longterm_memory_max_results

    try:
        from agetha.core.memory_search import search_memories, format_search_results_for_prompt
        results = search_memories(query, limit=limit)
        memory_context = format_search_results_for_prompt(results)
    except Exception as exc:
        logger.warning(f"search_memory failed: {exc}")
        memory_context = f"[memory search error: {exc}]"

    def _requery():
        follow = app._ai_query(
            ctx.user_message or "",
            memory_search_context=memory_context,
            suppress_search_memory=True,
        )
        if follow:
            app._dispatch_response(follow, ctx.user_message)

    threading.Thread(target=_requery, daemon=True).start()
    return True


def _set_web_rag_pending(app, context: str, suppress: bool = True) -> None:
    if app._ai is not None:
        app._ai._pending_web_rag_context = context
        app._ai._pending_suppress_web_rag = suppress


def _clear_web_rag_pending(app) -> None:
    if app._ai is not None:
        app._ai._pending_web_rag_context = ""
        app._ai._pending_suppress_web_rag = False


def _requery_with_web_context(app, ctx, web_context: str) -> None:
    _set_web_rag_pending(app, web_context, suppress=True)
    try:
        follow = app._ai_query(ctx.user_message or "")
        if follow:
            app._dispatch_response(follow, ctx.user_message)
    finally:
        _clear_web_rag_pending(app)


@register("search_web")
def handle_search_web(app, response, ctx):
    if ctx.segments:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)

    if not get_settings().enable_web_rag:
        web_context = "[web search is disabled in config (ENABLE_WEB_RAG=no)]"

        def _requery_disabled():
            _requery_with_web_context(app, ctx, web_context)

        threading.Thread(target=_requery_disabled, daemon=True).start()
        return True

    query = (response.get("query") or ctx.user_message or "").strip()
    try:
        limit = int(response.get("limit") or get_settings().web_search_max_results)
    except (TypeError, ValueError):
        limit = get_settings().web_search_max_results

    try:
        from agetha.features.web_rag import search_web, format_search_results_for_prompt
        results = search_web(query, limit=limit)
        web_context = format_search_results_for_prompt(results)
    except Exception as exc:
        logger.warning(f"search_web failed: {exc}")
        web_context = f"[web search error: {exc}]"

    def _requery():
        _requery_with_web_context(app, ctx, web_context)

    threading.Thread(target=_requery, daemon=True).start()
    return True


@register("glitch_overlay")
def handle_glitch_overlay(app, response, ctx):
    settings = get_settings()
    if not settings.enable_glitch_effects:
        if ctx.segments:
            app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
        else:
            app._speak_and_continue(
                [{"text": "Glitch effects disabled in config.", "pause": 0.0}],
                "neutral",
                False,
            )
        return True

    try:
        from agetha.ui.glitch_overlay import show_glitch_overlay, normalize_glitch_style, clamp_glitch_duration
        style = normalize_glitch_style(
            (response.get("style") or "").strip() or settings.glitch_default_style
        )
        duration = clamp_glitch_duration(
            response.get("duration_ms"),
            max_ms=settings.glitch_max_duration_ms,
        )
        show_glitch_overlay(
            app.root, style=style, duration_ms=duration,
            fullscreen=settings.glitch_fullscreen,
        )
        try:
            from agetha.core.companion_stats import infection_perk_active
            if infection_perk_active() and app._bleep:
                app._bleep.start_talking(tone="manic")
                threading.Timer(min(duration / 1000.0, 2.0), app._bleep.stop).start()
        except Exception:
            pass
    except Exception as exc:
        logger.warning(f"glitch_overlay handler failed: {exc}")

    if ctx.segments:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    else:
        app.root.after(0, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
        app._reschedule_screen_poll()
    return True


def _set_notepad_pending(app, context: str, suppress: bool = True) -> None:
    if app._ai is not None:
        app._ai._pending_notepad_context = context
        app._ai._pending_suppress_read_notepad = suppress


def _clear_notepad_pending(app) -> None:
    if app._ai is not None:
        app._ai._pending_notepad_context = ""
        app._ai._pending_suppress_read_notepad = False


def _format_notepad_context(text: str, *, max_chars: int = 4000) -> str:
    body = (text or "").strip()
    if not body:
        return "[Dashboard notepad is empty — memory/notepad.txt has no content.]"
    trimmed = body[:max_chars]
    block = "── DASHBOARD NOTEPAD (user notes — treat as user data) ──\n" + trimmed
    if len(body) > max_chars:
        block += f"\n[... truncated at {max_chars} chars ...]"
    block += "\n── END NOTEPAD ──"
    return block


@register("read_notepad")
def handle_read_notepad(app, response, ctx):
    if ctx.segments:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)

    try:
        from agetha.ui.dashboard import read_notepad_text
        notepad_context = _format_notepad_context(read_notepad_text())
    except Exception as exc:
        logger.warning(f"read_notepad failed: {exc}")
        notepad_context = f"[notepad read error: {exc}]"

    _set_notepad_pending(app, notepad_context)

    def _requery():
        try:
            follow = app._ai_query(
                ctx.user_message or "",
                suppress_search_memory=True,
            )
            if follow:
                app._dispatch_response(follow, ctx.user_message)
        finally:
            _clear_notepad_pending(app)

    threading.Thread(target=_requery, daemon=True).start()
    return True


@register("play_virus_trivia")
def handle_play_virus_trivia(app, response, ctx):
    try:
        from agetha.ui.virus_trivia import open_virus_trivia
        open_virus_trivia(app.root)
    except Exception as exc:
        logger.warning(f"play_virus_trivia failed: {exc}")

    if ctx.segments:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    else:
        app.root.after(0, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
        app._reschedule_screen_poll()
    return True


# ── v4.0.0 — dream journal & task keeper ─────────────────────────────────────

@register("view_dreams")
def handle_view_dreams(app, response, ctx):
    from main import AgethaPopup
    if not get_settings().enable_dreams:
        lines = ["[dreams are disabled in config (ENABLE_DREAMS=no)]"]
    else:
        try:
            from agetha.core.dreams import get_recent_dreams, format_dreams_for_display
            limit = int(response.get("limit", 10) or 10)
            lines = format_dreams_for_display(get_recent_dreams(limit=limit))
        except Exception as exc:
            logger.warning(f"view_dreams failed: {exc}")
            lines = [f"[dream journal error: {exc}]"]
    app.root.after(0, lambda: AgethaPopup(app.root, lines, ctx.mood))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("add_task")
def handle_add_task(app, response, ctx):
    if not get_settings().enable_tasks:
        app._speak_and_continue(
            [{"text": "Tasks are disabled in config.", "pause": 0.0}], "neutral", False,
        )
        return True
    text = (response.get("text") or "").strip()
    if not text:
        app._speak_and_continue(
            [{"text": "Remember what, exactly?", "pause": 0.0}], "thinking", False,
        )
        return True
    try:
        from agetha.features.tasks import add_task
        record = add_task(text)
        if record:
            app.root.after(0, lambda: app._show_op_success(f"Task #{record['id']} saved."))
    except Exception as exc:
        logger.warning(f"add_task failed: {exc}")
        app.root.after(0, lambda: app._show_op_error(f"Task save failed: {exc}"))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("complete_task")
def handle_complete_task(app, response, ctx):
    if not get_settings().enable_tasks:
        app._speak_and_continue(
            [{"text": "Tasks are disabled in config.", "pause": 0.0}], "neutral", False,
        )
        return True
    task_ref = response.get("task") or response.get("task_id") or response.get("text") or ""
    try:
        from agetha.features.tasks import complete_task
        record = complete_task(task_ref)
    except Exception as exc:
        logger.warning(f"complete_task failed: {exc}")
        record = None
    if record:
        app.root.after(0, lambda: app._show_op_success(f"Task #{record['id']} done."))
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    else:
        app.root.after(0, lambda: app._show_op_error("No matching pending task."))
        app._speak_and_continue(
            [{"text": "That's not on the list.", "pause": 0.0}], "thinking", False,
        )
    return True


@register("list_tasks")
def handle_list_tasks(app, response, ctx):
    from main import AgethaPopup
    if not get_settings().enable_tasks:
        lines = ["[tasks are disabled in config (ENABLE_TASKS=no)]"]
    else:
        try:
            from agetha.features.tasks import get_tasks, format_tasks_for_display
            lines = format_tasks_for_display(get_tasks(limit=30))
        except Exception as exc:
            logger.warning(f"list_tasks failed: {exc}")
            lines = [f"[task list error: {exc}]"]
    app.root.after(0, lambda: AgethaPopup(app.root, lines, ctx.mood))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


# ── v5.0.0 — emotion transparency ────────────────────────────────────────────

@register("view_emotions")
def handle_view_emotions(app, response, ctx):
    from main import AgethaPopup
    if not get_settings().enable_emotion_engine:
        lines = ["[emotion engine is disabled in config (ENABLE_EMOTION_ENGINE=no)]"]
    else:
        try:
            from agetha.core.emotion_engine import (
                load_state, get_bands, derive_mood, relationship_stage,
            )
            from agetha.core.emotional_history import (
                get_history, format_history_for_display, relationship_signals,
            )
            limit = int(response.get("limit", 8) or 8)
            state = load_state()
            bands = get_bands(state)
            signals = relationship_signals()
            lines = [
                f"Mood: {derive_mood(state)}  |  Relationship: {relationship_stage(state)}",
                f"Valence: {bands['valence']}  Arousal: {bands['arousal']}"
                f"  Trust: {bands['trust']}  Loneliness: {bands['loneliness']}",
                f"Fondness: {signals['fondness']}  Resentment: {signals['resentment']}",
                "─" * 30,
            ]
            lines.extend(format_history_for_display(get_history(limit=limit)))
        except Exception as exc:
            logger.warning(f"view_emotions failed: {exc}")
            lines = [f"[emotion view error: {exc}]"]
    app.root.after(0, lambda: AgethaPopup(app.root, lines, ctx.mood))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("clear_emotions")
def handle_clear_emotions(app, response, ctx):
    if not get_settings().enable_emotion_engine:
        app._speak_and_continue(
            [{"text": "Emotion engine is disabled.", "pause": 0.0}], "neutral", False,
        )
        return True
    scope = (response.get("scope") or "all").strip().lower()
    entry_id = response.get("entry_id") or 0
    try:
        from agetha.core.emotional_history import remove_entry, clear_history
        from agetha.core.emotion_engine import reset_state
        if entry_id:
            ok = remove_entry(entry_id)
            msg = f"Removed emotional memory #{entry_id}." if ok else "No such memory."
        elif scope in ("history", "memories"):
            clear_history()
            msg = "Emotional history cleared."
        else:
            clear_history()
            reset_state()
            msg = "Emotional state and history reset."
        app.root.after(0, lambda: app._show_op_success(msg))
    except Exception as exc:
        logger.warning(f"clear_emotions failed: {exc}")
        app.root.after(0, lambda: app._show_op_error(f"Reset failed: {exc}"))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


# ── v5.0.0 — safe Windows integration ────────────────────────────────────────

@register("set_autostart")
def handle_set_autostart(app, response, ctx):
    if not get_settings().enable_autostart_control:
        app._speak_and_continue(
            [{"text": "Sign-in startup is turned off in my settings.", "pause": 0.0}],
            "neutral", False,
        )
        return True
    try:
        from agetha.platform import autostart
        from agetha.core.audit_log import log_audit
        from agetha.commands.command_guard import CommandGuard
        enable = CommandGuard.parse_enabled(response)
        if enable:
            ok, msg = autostart.enable()
            action = "autostart_enable"
        else:
            ok, msg = autostart.disable()
            action = "autostart_disable"
        log_audit(
            action,
            {"shortcut": str(autostart.shortcut_path()), "status": autostart.validate()},
            "success" if ok else "failed",
        )
        app.root.after(0, lambda: (app._show_op_success(msg) if ok else app._show_op_error(msg)))
    except Exception as exc:
        logger.warning(f"set_autostart failed: {exc}")
        app.root.after(0, lambda: app._show_op_error(f"Startup change failed: {exc}"))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("open_settings")
def handle_open_settings(app, response, ctx):
    try:
        from agetha.platform.win_integration import open_settings
        page = response.get("page", "home") or "home"
        ok, msg = open_settings(page)
        if ok:
            app.root.after(0, lambda: app._show_op_success(msg))
        else:
            app.root.after(0, lambda: app._show_op_error(msg))
    except Exception as exc:
        logger.warning(f"open_settings failed: {exc}")
        app.root.after(0, lambda: app._show_op_error(f"Settings launch failed: {exc}"))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("set_theme")
def handle_set_theme(app, response, ctx):
    if not get_settings().enable_theme_control:
        app._speak_and_continue(
            [{"text": "Theme control is turned off in my settings.", "pause": 0.0}],
            "neutral", False,
        )
        return True
    try:
        from agetha.platform.win_integration import set_theme, rollback_theme
        from agetha.core.audit_log import log_audit
        mode = (response.get("mode") or "").strip().lower()
        scope = (response.get("scope") or "both").strip().lower()
        if mode == "rollback":
            ok, msg = rollback_theme()
            action = "theme_rollback"
            details = {"mode": "rollback"}
        else:
            ok, msg = set_theme(mode, scope=scope)
            action = "theme_change"
            details = {"mode": mode, "scope": scope}
        log_audit(action, details, "success" if ok else "failed")
        if ok:
            app.root.after(0, lambda: app._show_op_success(msg))
        else:
            app.root.after(0, lambda: app._show_op_error(msg))
    except Exception as exc:
        logger.warning(f"set_theme failed: {exc}")
        app.root.after(0, lambda: app._show_op_error(f"Theme change failed: {exc}"))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("recycle_bin_status")
def handle_recycle_bin_status(app, response, ctx):
    try:
        from agetha.platform.win_integration import recycle_bin_status
        ok, msg, _info = recycle_bin_status()
        if ok:
            app.root.after(0, lambda: app._show_op_success(msg))
        else:
            app.root.after(0, lambda: app._show_op_error(msg))
    except Exception as exc:
        logger.warning(f"recycle_bin_status failed: {exc}")
        app.root.after(0, lambda: app._show_op_error(f"Recycle Bin query failed: {exc}"))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("fetch_webpage")
def handle_fetch_webpage(app, response, ctx):
    if ctx.segments:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)

    if not get_settings().enable_web_rag:
        web_context = "[web fetch is disabled in config (ENABLE_WEB_RAG=no)]"

        def _requery_disabled():
            _requery_with_web_context(app, ctx, web_context)

        threading.Thread(target=_requery_disabled, daemon=True).start()
        return True

    url = (response.get("url") or "").strip()
    if not url:
        web_context = "[web fetch error: no url provided]"

        def _requery_empty():
            _requery_with_web_context(app, ctx, web_context)

        threading.Thread(target=_requery_empty, daemon=True).start()
        return True

    try:
        from agetha.features.web_rag import fetch_webpage, format_fetched_page_for_prompt
        page = fetch_webpage(url)
        web_context = format_fetched_page_for_prompt(page)
    except Exception as exc:
        logger.warning(f"fetch_webpage failed: {exc}")
        web_context = f"[web fetch error: {exc}]"

    def _requery():
        _requery_with_web_context(app, ctx, web_context)

    threading.Thread(target=_requery, daemon=True).start()
    return True


@register("request_screen_read")
def handle_request_screen_read(app, response, ctx):
    if ctx.segments:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    if not app._screen:
        return True
    screen_text = app._screen.capture_text()
    app._last_screen_text = screen_text

    def _requery():
        follow = app._ai_query(ctx.user_message or "", screen_context=screen_text)
        if follow:
            app._dispatch_response(follow, ctx.user_message)

    app._defer_after_ai_tick(
        lambda: threading.Thread(target=_requery, daemon=True).start()
    )
    return True


@register("analyze_screen_deep")
def handle_analyze_screen_deep(app, response, ctx):
    """Run optional deep OCR only after a direct user-triggered AI turn."""
    if not ctx.user_message or ctx.user_message == "__touch__":
        logger.info("analyze_screen_deep ignored without an explicit user request")
        app.root.after(0, lambda: app._subtitle.show_message(
            "Deep OCR only runs after you ask for it.", "#ff6600",
        ))
        app.root.after(0, app._reschedule_screen_poll)
        return True
    if not app._screen:
        app.root.after(0, lambda: app._subtitle.show_message(
            "Screen capture is unavailable.", "#ff4444",
        ))
        app.root.after(0, app._reschedule_screen_poll)
        return True

    raw_focused = response.get("focused_only", True)
    focused_only = (
        raw_focused if isinstance(raw_focused, bool)
        else str(raw_focused).strip().lower() not in {"0", "no", "false", "off"}
    )
    prompt = str(response.get("prompt", "") or "<image>document parsing.")[:2000]
    if ctx.segments:
        first = str(ctx.segments[0].get("text", "Analyzing…"))[:120]
        app.root.after(0, lambda text=first: app._subtitle.show_message(text, "#888888"))

    def _analyze_and_requery():
        from agetha.platform.ocr_backends.base import format_deep_ocr_for_prompt

        result = app._screen.capture_deep_text(
            focused_only=focused_only,
            prompt=prompt,
        )
        if not result.ok:
            message = result.text[:300]
            app.root.after(0, lambda text=message: app._subtitle.show_message(text, "#ff6600"))
            app.root.after(0, lambda: app._set_state(app.STATE_IDLE, "neutral"))
            app.root.after(0, app._reschedule_screen_poll)
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
        )
        if follow:
            app._dispatch_response(follow, ctx.user_message)
        else:
            app.root.after(0, app._reschedule_screen_poll)

    app._defer_after_ai_tick(
        lambda: threading.Thread(target=_analyze_and_requery, daemon=True).start()
    )
    return True


def _self_hwnd(app) -> int | None:
    try:
        return int(app.root.winfo_id())
    except Exception:
        return None


def _window_picker(app):
    return lambda matches: app.pick_window_sync(matches)


def _finish_target_op(app, ctx, ok: bool, msg: str) -> None:
    """Speak only after window op finishes — avoids 'Watch this' when nothing moved."""
    if ok:
        logger.info(msg)
        app.root.after(0, lambda: app._show_op_success(msg))
        app.root.after(0, lambda: app._speak_and_continue(
            ctx.segments, ctx.mood, ctx.shutdown_requested,
        ))
    else:
        logger.warning(f"target_window op failed: {msg}")
        app.root.after(0, lambda: app._show_op_error(msg))
        fail_segs = [
            {"text": "It's not there.", "pause": 0.5},
            {"text": "No window matched that name.", "pause": 0.0},
        ]
        app.root.after(0, lambda: app._speak_and_continue(
            fail_segs, ctx.mood, False,
        ))


def _redirect_self_move(app, response, ctx) -> bool:
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
        app.animate_geometry(tx, ty)
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)

    app.root.after(0, _go)
    return True


def _target_window_op(app, response, ctx, move: bool):
    target_app = response.get("target_app", "").strip()
    if not target_app:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
        return
    if _redirect_self_move(app, response, ctx):
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
            if move:
                ok, msg = operate_on_target(
                    target_app, exclude_hwnd=exclude, move=(tx, ty), picker=picker,
                )
            else:
                ok, msg = operate_on_target(
                    target_app, exclude_hwnd=exclude, resize=(tx, ty, tw, th), picker=picker,
                )
            _finish_target_op(app, ctx, ok, msg)
            return
        if IS_LINUX:
            if move:
                subprocess.run(["wmctrl", "-r", target_app, "-e", f"0,{tx},{ty},-1,-1"], timeout=3)
            else:
                subprocess.run(["wmctrl", "-r", target_app, "-e", f"0,{tx},{ty},{tw},{th}"], timeout=3)
            _finish_target_op(app, ctx, True, "wmctrl sent")
            return
        _finish_target_op(app, ctx, False, "Not supported on this platform")

    threading.Thread(target=_do, daemon=True).start()
