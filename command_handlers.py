"""
command_handlers.py — Command pattern dispatch for Agetha.
Each handler receives (app, response, ctx) and returns True if it handled the command.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from command_guard import CommandGuard
from system_commands import (
    copy_to_clipboard, get_clipboard, lock_screen, open_folder, open_url,
    restart_system, screenshot_path, search_files, set_reminder, set_volume,
    set_wallpaper, show_notification, shutdown_system, system_info, type_text,
)
from utils import IS_LINUX, IS_WINDOWS, WINDOW_W, WINDOW_H, logger

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

    if not user_message and ctx.mood in app._ATTENTION_MOODS:
        app._maybe_snap_to_center(ctx.mood)

    if command not in ("idle", "speak", "wake_user") and not app._guard.check(command, response):
        logger.info(f"User denied command: {command}")
        denied = [{"text": "Fine. I won't.", "pause": 0.0}]
        app._speak_and_continue(denied, "neutral", False)
        return

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
        app.root.geometry(f"+{nx}+{ny}")
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
    show_notification(response.get("title", "Agetha").strip(), response.get("message", "").strip())
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("run_command")
def handle_run_command(app, response, ctx):
    cmd_str = response.get("cmd", "").strip()
    if cmd_str:
        try:
            r = subprocess.run(
                cmd_str, shell=bool(response.get("shell", True)),
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

    def _check():
        running = app._ai.monitor_process(process_name)
        status = "running" if running else "not running"
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
                follow = app._ai_query(f"[SYSTEM] User answered YES to: {dlg_msg[:120]}")
                if follow:
                    dispatch(app, follow, ctx.user_message)
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
        app.root.geometry(f"+{(sw - WINDOW_W) // 2}+{(sh - WINDOW_H) // 2}")
        try:
            app.root.attributes("-topmost", True)
        except Exception:
            pass
        app.root.lift()
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

    def _close():
        if IS_WINDOWS:
            from main import _find_window_hwnd
            import ctypes
            hwnd = _find_window_hwnd(target_app)
            if hwnd:
                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            else:
                app._show_op_error(f"Window not found: {target_app}")
        elif IS_LINUX:
            subprocess.run(["wmctrl", "-c", target_app], timeout=3)

    threading.Thread(target=_close, daemon=True).start()
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
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
    app.root.after(0, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
    app._reschedule_screen_poll()
    return True


@register("force_close")
def handle_force_close(app, response, ctx):
    target = (
        response.get("app", "") or response.get("process", "") or response.get("name", "")
    ).strip()
    if target:
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/IM", os.path.basename(target), "/F"], capture_output=True, check=False)
            else:
                subprocess.run(["pkill", "-f", target], check=False)
        except Exception as exc:
            app._show_op_error(f"Force close failed: {exc}")
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
        url = engines.get(response.get("engine", "google"), engines["google"]) + search.replace(" ", "+")
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
    try:
        from memory_system import clear_episodic
        clear_episodic()
    except ImportError:
        pass
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("request_screen_read")
def handle_request_screen_read(app, response, ctx):
    if not app._screen:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
        return True
    screen_text = app._screen.capture_text()
    app._last_screen_text = screen_text

    def _requery():
        follow = app._ai_query("", screen_context=screen_text, user_message=ctx.user_message or "")
        if follow:
            dispatch(app, follow, ctx.user_message)

    threading.Thread(target=_requery, daemon=True).start()
    return True


def _target_window_op(app, response, ctx, move: bool):
    target_app = response.get("target_app", "").strip()
    if not target_app:
        app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
        return

    try:
        tx, ty = int(response.get("x", 0) or 0), int(response.get("y", 0) or 0)
        tw = int(response.get("width", 800) or 800)
        th = int(response.get("height", 600) or 600)
    except (TypeError, ValueError):
        tx = ty = 0
        tw, th = 800, 600

    def _do():
        if IS_WINDOWS:
            from main import _find_window_hwnd
            import ctypes
            hwnd = _find_window_hwnd(target_app)
            if not hwnd:
                fail = [{"text": "It's not here.", "pause": 0.4}, {"text": "Where did it go.", "pause": 0.0}]
                app.root.after(0, lambda: app._subtitle.speak(fail))
                return
            if move:
                ctypes.windll.user32.SetWindowPos(hwnd, 0, tx, ty, 0, 0, 0x0001 | 0x0004)
            else:
                ctypes.windll.user32.MoveWindow(hwnd, tx, ty, tw, th, True)
        elif IS_LINUX:
            if move:
                subprocess.run(["wmctrl", "-r", target_app, "-e", f"0,{tx},{ty},-1,-1"], timeout=3)
            else:
                subprocess.run(["wmctrl", "-r", target_app, "-e", f"0,{tx},{ty},{tw},{th}"], timeout=3)

    threading.Thread(target=_do, daemon=True).start()
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
