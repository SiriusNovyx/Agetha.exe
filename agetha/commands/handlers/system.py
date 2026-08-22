"""System settings and guarded operating-system handlers."""

from agetha.app_config import get_settings
from agetha.commands.system_commands import (
    lock_screen,
    restart_system,
    search_files,
    set_volume,
    set_wallpaper,
    shutdown_system,
)
from agetha.utils import logger

from .registry import register
from .support import (
    finish_verified_command as _finish_verified_command,
    schedule_app_ui as _schedule_app_ui,
)


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
    _schedule_app_ui(app, lambda: AgethaPopup(app.root, lines[:12], ctx.mood))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("lock_screen")
def handle_lock_screen(app, response, ctx):
    _finish_verified_command(app, ctx, lock_screen())
    return True


@register("shutdown")
def handle_shutdown(app, response, ctx):
    try:
        delay = int(response.get("delay", 60))
    except (TypeError, ValueError):
        delay = 60
    _finish_verified_command(app, ctx, shutdown_system(delay))
    return True


@register("restart")
def handle_restart(app, response, ctx):
    try:
        delay = int(response.get("delay", 60))
    except (TypeError, ValueError):
        delay = 60
    _finish_verified_command(app, ctx, restart_system(delay))
    return True


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
        _schedule_app_ui(app, lambda: (app._show_op_success(msg) if ok else app._show_op_error(msg)))
    except Exception as exc:
        logger.warning(f"set_autostart failed: {exc}")
        _schedule_app_ui(app, lambda: app._show_op_error(f"Startup change failed: {exc}"))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("open_settings")
def handle_open_settings(app, response, ctx):
    try:
        from agetha.platform.win_integration import open_settings
        page = response.get("page", "home") or "home"
        ok, msg = open_settings(page)
        if ok:
            _schedule_app_ui(app, lambda: app._show_op_success(msg))
        else:
            _schedule_app_ui(app, lambda: app._show_op_error(msg))
    except Exception as exc:
        logger.warning(f"open_settings failed: {exc}")
        _schedule_app_ui(app, lambda: app._show_op_error(f"Settings launch failed: {exc}"))
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
            _schedule_app_ui(app, lambda: app._show_op_success(msg))
        else:
            _schedule_app_ui(app, lambda: app._show_op_error(msg))
    except Exception as exc:
        logger.warning(f"set_theme failed: {exc}")
        _schedule_app_ui(app, lambda: app._show_op_error(f"Theme change failed: {exc}"))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True


@register("recycle_bin_status")
def handle_recycle_bin_status(app, response, ctx):
    try:
        from agetha.platform.win_integration import recycle_bin_status
        ok, msg, _info = recycle_bin_status()
        if ok:
            _schedule_app_ui(app, lambda: app._show_op_success(msg))
        else:
            _schedule_app_ui(app, lambda: app._show_op_error(msg))
    except Exception as exc:
        logger.warning(f"recycle_bin_status failed: {exc}")
        _schedule_app_ui(app, lambda: app._show_op_error(f"Recycle Bin query failed: {exc}"))
    app._speak_and_continue(ctx.segments, ctx.mood, ctx.shutdown_requested)
    return True
