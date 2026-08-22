"""Filesystem and local OS utility handlers."""

import os
import platform
import re
import shlex
import shutil
import subprocess
import threading
from pathlib import Path

from agetha.commands.system_commands import (
    copy_to_clipboard,
    open_folder,
    screenshot_path,
    show_notification,
)
from agetha.utils import IS_WINDOWS, logger

from .registry import register
from .support import (
    CAPABILITY_AUTHORIZATION as _CAPABILITY_AUTHORIZATION,
    call_app_ui_sync as _call_app_ui_sync,
    finish_verified_command as _finish_verified_command,
    perform_authorized_effect as _perform_authorized_effect,
    schedule_app_ui as _schedule_app_ui,
)


@register("request_path")
def handle_request_path(app, response, ctx):
    from main import AgethaPopup
    hint = response.get("path_hint", "").strip()
    lines = [hint] if hint else (
        [s.get("text", "") for s in ctx.segments if s.get("text")] or ["Path resolved automatically."]
    )
    _schedule_app_ui(app, lambda: AgethaPopup(app.root, lines[:4], ctx.mood))
    _schedule_app_ui(app, lambda: app._set_state(app.STATE_IDLE, ctx.mood))
    app._reschedule_screen_poll()
    return True


@register("create_folder")
def handle_create_folder(app, response, ctx):
    path = response.get("path", "").strip()
    result = "[no path]"
    if path:
        try:
            performed, _value = _perform_authorized_effect(
                app,
                response.get(_CAPABILITY_AUTHORIZATION),
                lambda: os.makedirs(path, exist_ok=True),
            )
            if performed:
                logger.info("Created folder: name=%s", Path(path).name)
                result = "[folder created]"
            else:
                result = "[create folder blocked: capability changed]"
        except Exception as exc:
            result = f"[create folder error: {type(exc).__name__}]"
    _finish_verified_command(app, ctx, result)
    return True


@register("create_file")
def handle_create_file(app, response, ctx):
    file_path = response.get("file_path", "").strip()
    if not file_path:
        p, fn = response.get("path", "").strip(), response.get("file_name", "").strip()
        if p and fn:
            file_path = os.path.join(p, fn)
    result = "[no path]"
    if file_path:
        try:
            def _create() -> None:
                parent = os.path.dirname(file_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(file_path, "w", encoding="utf-8") as fh:
                    fh.write(response.get("content", ""))

            performed, _value = _perform_authorized_effect(
                app, response.get(_CAPABILITY_AUTHORIZATION), _create,
            )
            if performed:
                logger.info("Created file: name=%s", Path(file_path).name)
                result = "[file created]"
            else:
                result = "[create file blocked: capability changed]"
        except Exception as exc:
            result = f"[create file error: {type(exc).__name__}]"
    _finish_verified_command(app, ctx, result)
    return True


@register("delete_file")
def handle_delete_file(app, response, ctx):
    path = response.get("path", "").strip()
    result = "[no path]"
    if path:
        try:
            def _delete() -> str:
                p = Path(path)
                if p.is_dir():
                    shutil.rmtree(p)
                    return "[folder deleted]"
                if p.exists():
                    p.unlink()
                    return "[file deleted]"
                return "[delete error: not found]"

            performed, value = _perform_authorized_effect(
                app, response.get(_CAPABILITY_AUTHORIZATION), _delete,
            )
            result = (
                str(value)
                if performed else "[delete blocked: capability changed]"
            )
        except Exception as exc:
            result = f"[delete error: {type(exc).__name__}]"
    _finish_verified_command(app, ctx, result)
    return True


@register("rename_file")
def handle_rename_file(app, response, ctx):
    path, new_name = response.get("path", "").strip(), response.get("new_name", "").strip()
    result = "[missing path or name]"
    if path and new_name:
        try:
            performed, _value = _perform_authorized_effect(
                app,
                response.get(_CAPABILITY_AUTHORIZATION),
                lambda: Path(path).rename(Path(path).parent / new_name),
            )
            result = (
                "[file renamed]"
                if performed else "[rename blocked: capability changed]"
            )
        except Exception as exc:
            result = f"[rename error: {type(exc).__name__}]"
    _finish_verified_command(app, ctx, result)
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
    _schedule_app_ui(app, lambda: AgethaPopup(app.root, lines[:12], ctx.mood))
    segs = ctx.segments or [{"text": f"{len(lines)} items.", "pause": 0.0}]
    app._speak_and_continue(segs, ctx.mood, ctx.shutdown_requested)
    return True


@register("set_clipboard")
@register("copy_to_clipboard")
def handle_clipboard_set(app, response, ctx):
    text = response.get("text", "").strip()
    if text:
        msg = _call_app_ui_sync(app, lambda: copy_to_clipboard(text, app.root))
        if isinstance(msg, str) and msg.startswith("["):
            logger.info(msg)
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
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
    result = "[no command]"
    if cmd_str:
        try:
            if re.search(r"[|&;<>$`(){}]", cmd_str):
                result = "[command error: shell metacharacters are not allowed]"
            else:
                args = shlex.split(cmd_str, posix=not IS_WINDOWS)
                r = subprocess.run(
                    args, shell=False,
                    capture_output=True, text=True, timeout=15,
                )
                logger.info("run_command completed: exit=%s", r.returncode)
                result = (
                    "[command completed]" if r.returncode == 0
                    else f"[command error: exit {r.returncode}]"
                )
        except Exception as exc:
            result = f"[command error: {type(exc).__name__}]"
    _finish_verified_command(app, ctx, result)
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
    msg = "[no path]"
    if file_path and app._ai:
        performed, value = _perform_authorized_effect(
            app,
            response.get(_CAPABILITY_AUTHORIZATION),
            lambda: app._ai.write_file(
                file_path,
                response.get("content", ""),
                response.get("mode", "overwrite"),
            ),
        )
        msg = (
            str(value)
            if performed else "[write blocked: capability changed]"
        )
    _finish_verified_command(app, ctx, msg)
    return True
