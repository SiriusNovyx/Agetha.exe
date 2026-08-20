"""
command_guard.py — 3-tier command confirmation with native OS dialogs.

Tiers:
  SAFE    — execute immediately
  CAUTION — info icon, OK/Cancel
  DANGER  — warning icon, Yes/No (default No)

force_close: auto-allow common user apps; confirm protected/system processes.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from agetha.utils import IS_WINDOWS, apply_window_icon, logger, native_message_box
from agetha.app_config import get_settings
from agetha.platform.window_control import is_self_process_target

# Windows MessageBox flags
_MB_OKCANCEL = 0x00000001
_MB_YESNO = 0x00000004
_MB_ICONINFORMATION = 0x00000040
_MB_ICONWARNING = 0x00000030
_MB_ICONERROR = 0x00000010
_MB_DEFBUTTON2 = 0x00000100
_MB_TOPMOST = 0x00040000
_MB_SETFOREGROUND = 0x00010000


class CommandGuard:
    """Intercepts AI commands; shows native confirmation dialogs by danger tier."""

    SAFE = "safe"
    CAUTION = "caution"
    DANGER = "danger"

    # Processes that must never be killed without explicit user confirmation
    PROTECTED_PROCESSES = frozenset({
        "explorer.exe", "svchost.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
        "lsass.exe", "services.exe", "smss.exe", "system", "registry",
        "dwm.exe", "fontdrvhost.exe", "sihost.exe", "taskhostw.exe",
        "runtimebroker.exe", "searchindexer.exe", "spoolsv.exe",
        "systemd", "init", "kernel", "kthreadd", "ksoftirqd",
        "agetha.exe", "python.exe", "pythonw.exe",
    })

    TIER_MAP: dict[str, str] = {
        "speak": SAFE, "idle": SAFE, "change_mood": SAFE,
        "change_animation_speed": SAFE, "open_url": SAFE, "open_browser": SAFE,
        "system_info": SAFE, "take_screenshot": SAFE, "set_reminder": SAFE,
        "show_notification": SAFE, "snap_to_center": SAFE, "move_window": SAFE,
        "request_screen_read": SAFE, "request_path": SAFE, "show_error_gif": SAFE,
        "analyze_screen_deep": CAUTION,
        "wake_user": SAFE, "play_emotion_sound": SAFE, "monitor_process": SAFE,
        "get_active_app": SAFE, "list_running_apps": SAFE,
        "view_memory": SAFE, "search_memory": SAFE,
        "glitch_overlay": SAFE,
        "read_notepad": SAFE, "play_virus_trivia": SAFE,
        # v4.0.0 — dreams & tasks live only in the app's memory/ folder
        "view_dreams": SAFE, "add_task": SAFE, "complete_task": SAFE,
        "list_tasks": SAFE,
        # v5.0.0 — emotion transparency (memory/ only). Reset is confirmed.
        "view_emotions": SAFE, "clear_emotions": CAUTION,
        # v5.0.0 — sign-in startup: changes a visible Startup-folder shortcut.
        "set_autostart": DANGER,
        # v5.0.0 — safe Windows integration
        "open_settings": CAUTION,        # allowlisted ms-settings pages only
        "set_theme": DANGER,             # HKCU registry write (reversible)
        "recycle_bin_status": SAFE,      # aggregate read only
        "search_web": CAUTION, "fetch_webpage": CAUTION,
        "computer_use": CAUTION,
        "read_document": SAFE, "get_clipboard": SAFE, "open_folder": SAFE,
        "clear_memory": CAUTION,
        "read_file": CAUTION, "open_file": CAUTION, "open_app": CAUTION,
        "copy_to_clipboard": CAUTION, "set_clipboard": CAUTION,
        "search_files": CAUTION, "play_sound": CAUTION, "set_volume": CAUTION,
        "set_wallpaper": CAUTION, "type_text": CAUTION,
        "list_dir": CAUTION, "list_directory": CAUTION, "show_dialog": CAUTION,
        "run_command": DANGER, "delete_file": DANGER, "force_close": DANGER,
        "create_file": DANGER, "write_file": DANGER, "rename_file": DANGER,
        "create_folder": DANGER, "lock_screen": DANGER,
        "shutdown": DANGER, "restart": DANGER,
        "target_window_move": CAUTION, "target_window_resize": CAUTION,
        "target_window_close": CAUTION,
    }

    TIER_TITLES = {
        CAUTION: "Agetha — Confirm Action",
        DANGER: "Agetha — Dangerous Action",
    }

    def __init__(
        self,
        root=None,
        *,
        schedule_ui: Callable[[Callable[[], None]], object | None] | None = None,
        owner_thread_id: int | None = None,
    ):
        self._root = root
        self._settings = get_settings()
        self._schedule_ui = schedule_ui
        self._owner_thread_id = (
            threading.get_ident() if owner_thread_id is None else int(owner_thread_id)
        )

    def set_root(self, root) -> None:
        self._root = root

    def describe(self, command: str, response: dict) -> str:
        """Human-readable summary of a command (for dry-run UI)."""
        return self._format_details(command, response)

    def check_dry_run(
        self,
        command: str,
        details: str,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> bool:
        """Dry-run gate: ask user to approve executing a suggested command."""
        title = "Agetha — Dry Run"
        message = (
            f"DRY RUN MODE — execute this command?\n\n"
            f"{details}\n\n"
            f"Command: {command}"
        )
        return self._confirm_on_ui(
            lambda: self._native_confirm(
                title,
                message,
                "warning",
                "yesno",
                default_no=True,
            ),
            label="dry_run",
            cancel_check=cancel_check,
        )

    def check(
        self,
        command: str,
        response: dict,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> bool:
        """Return True if the command may proceed. Thread-safe (marshals to Tk main thread)."""
        if not self._settings.enable_command_confirmations:
            return True
        tier = self._resolve_tier(command, response)
        if tier == self.SAFE:
            return True

        return self._confirm_on_ui(
            lambda: self._show_dialog(command, response, tier),
            label=command,
            cancel_check=cancel_check,
        )

    def _confirm_on_ui(
        self,
        callback: Callable[[], bool],
        *,
        label: str,
        cancel_check: Callable[[], bool] | None,
        timeout_seconds: float = 120.0,
    ) -> bool:
        """Run a modal confirmation only on its UI owner and fail closed late."""

        def _cancelled() -> bool:
            try:
                return bool(cancel_check is not None and cancel_check())
            except Exception:
                return True

        if _cancelled():
            return False
        owner = getattr(self, "_owner_thread_id", None)
        if self._root is None or (
            owner is not None and threading.get_ident() == owner
        ):
            try:
                return bool(callback()) if not _cancelled() else False
            except Exception as exc:
                logger.error(
                    "CommandGuard dialog failed safely: %s",
                    type(exc).__name__,
                )
                return False

        scheduler = getattr(self, "_schedule_ui", None)
        if not callable(scheduler):
            logger.warning("CommandGuard UI scheduler unavailable for %s", label)
            return False

        result: list[bool | None] = [None]
        done = threading.Event()
        invalidated = threading.Event()

        def _on_owner() -> None:
            if invalidated.is_set() or _cancelled():
                done.set()
                return
            try:
                value = bool(callback())
                if not invalidated.is_set() and not _cancelled():
                    result[0] = value
            except Exception as exc:
                logger.error(
                    "CommandGuard dialog failed safely: %s",
                    type(exc).__name__,
                )
                result[0] = False
            finally:
                done.set()

        try:
            scheduled = scheduler(_on_owner)
        except Exception:
            scheduled = None
        if scheduled is None:
            return False
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        while not done.wait(0.05):
            if _cancelled() or time.monotonic() >= deadline:
                invalidated.set()
                logger.warning("CommandGuard confirmation stopped for %s", label)
                return False
        return bool(result[0]) if not invalidated.is_set() else False

    def _resolve_tier(self, command: str, response: dict) -> str:
        if command == "force_close":
            target = self._process_target(response)
            if target and is_self_process_target(target):
                return self.DANGER
            if not self._settings.force_close_auto_allow:
                return self.DANGER
            if target and not self._is_protected_process(target):
                logger.info(f"force_close auto-allowed (user app): {target}")
                return self.SAFE
        return self.TIER_MAP.get(command, self.DANGER)

    @staticmethod
    def _process_target(response: dict) -> str:
        return (
            response.get("app", "")
            or response.get("process", "")
            or response.get("name", "")
            or             response.get("process_name", "")
            or response.get("target_app", "")
        ).strip()

    def _is_protected_process(self, target: str) -> bool:
        name = target.lower().replace("\\", "/").split("/")[-1]
        if not name.endswith(".exe") and IS_WINDOWS:
            name = f"{name}.exe"
        protected = self._settings.protected_processes()
        return name in protected or name.replace(".exe", "") in protected

    def _owner_hwnd(self) -> int:
        if self._root is None:
            return 0
        try:
            return int(self._root.winfo_id())
        except Exception:
            return 0

    def _show_dialog(self, command: str, response: dict, tier: str) -> bool:
        details = self._format_details(command, response)
        title = self.TIER_TITLES.get(tier, "Agetha — Confirm")
        owner = self._owner_hwnd()

        if tier == self.CAUTION:
            body = (
                f"Agetha wants to perform this action:\n\n"
                f"Command: {command}\n\n"
                f"{details}\n\n"
                "Allow this action?"
            )
            return self._native_confirm(
                title, body,
                style="info",
                buttons="okcancel",
                default_no=False,
                owner_hwnd=owner,
            )

        body = (
            f"⚠ DANGEROUS ACTION ⚠\n\n"
            f"Command: {command}\n\n"
            f"{details}\n\n"
            "This could modify your system or data.\n"
            "Are you sure you want to allow this?"
        )
        return self._native_confirm(
            title, body,
            style="warning",
            buttons="yesno",
            default_no=True,
            owner_hwnd=owner,
        )

    @staticmethod
    def _native_confirm(
        title: str,
        message: str,
        style: str = "warning",
        buttons: str = "yesno",
        default_no: bool = True,
        owner_hwnd: int = 0,
    ) -> bool:
        """Native OS dialog. Returns True if user confirms."""
        if IS_WINDOWS:
            try:
                flags = _MB_TOPMOST | _MB_SETFOREGROUND
                if style == "info":
                    flags |= _MB_ICONINFORMATION
                elif style == "error":
                    flags |= _MB_ICONERROR
                else:
                    flags |= _MB_ICONWARNING

                if buttons == "okcancel":
                    flags |= _MB_OKCANCEL
                    if default_no:
                        flags |= _MB_DEFBUTTON2
                    result = native_message_box(title, message, flags, owner_hwnd=owner_hwnd)
                    return result == 1  # IDOK
                flags |= _MB_YESNO
                if default_no:
                    flags |= _MB_DEFBUTTON2
                result = native_message_box(title, message, flags, owner_hwnd=owner_hwnd)
                return result == 6  # IDYES
            except Exception as exc:
                logger.warning(f"MessageBoxW failed: {exc}")

        return CommandGuard._tk_fallback(title, message, style, buttons, default_no)

    @staticmethod
    def _tk_fallback(title, message, style, buttons, default_no) -> bool:
        try:
            import tkinter as tk
            from tkinter import messagebox as mb
            root = tk.Tk()
            root.withdraw()
            apply_window_icon(root)
            try:
                root.attributes("-topmost", True)
            except Exception:
                pass
            if buttons == "okcancel":
                ok = mb.askokcancel(title, message, icon="info", parent=root)
            elif style == "error":
                mb.showerror(title, message, parent=root)
                ok = False
            else:
                ok = mb.askyesno(title, message, icon="warning", default="no" if default_no else "yes", parent=root)
            root.destroy()
            return bool(ok)
        except Exception as exc:
            logger.error(f"Tk fallback dialog failed: {exc}")
            return False

    @staticmethod
    def parse_enabled(response: dict) -> bool:
        """Interpret the set_autostart 'enabled' flag (default True)."""
        val = response.get("enabled", True)
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in ("1", "yes", "true", "on")

    @staticmethod
    def _describe_theme(response: dict) -> str:
        mode = (response.get("mode") or "").strip().lower()
        scope = (response.get("scope") or "both").strip().lower()
        if mode == "rollback":
            return ("Roll back the last Windows theme change "
                    "(restore the previously saved registry values).")
        try:
            from agetha.platform.win_integration import describe_theme_change
            return describe_theme_change(mode, scope=scope)
        except Exception:
            return (
                f"Switch Windows to {mode or '?'} mode "
                f"(scope: {scope or 'both'}, current user, reversible)."
            )

    @staticmethod
    def _describe_autostart(response: dict) -> str:
        try:
            from agetha.platform import autostart
            return autostart.describe_change(
                "enable" if CommandGuard.parse_enabled(response) else "disable"
            )
        except Exception:
            action = "enable" if CommandGuard.parse_enabled(response) else "disable"
            return f"{action} 'Start Agetha when I sign in' (Startup-folder shortcut)."

    @staticmethod
    def _describe_type_text(response: dict) -> str:
        """Describe Unicode entry without echoing the potentially sensitive text."""
        preview = response.get("_typing_preview")
        text = response.get("text", "")
        character_count = len(text) if isinstance(text, str) else len(str(text or ""))
        line_count = (text.count("\n") + 1) if isinstance(text, str) and text else 0

        def field(name: str, fallback: object) -> object:
            if preview is None:
                return fallback
            if isinstance(preview, dict):
                return preview.get(name, fallback)
            return getattr(preview, name, fallback)

        target_application = str(field("target_application", "unknown target"))[:64]
        target_title = str(field("target_window_title", "unavailable"))[:80]
        method = str(field("method", response.get("mode", "auto")))[:48]
        character_count = field("character_count", character_count)
        line_count = field("line_count", line_count)
        clipboard = bool(field("clipboard_fallback_may_be_used", True))
        reversible = bool(field("reversible", False))
        return (
            "Enter exact Unicode text (content hidden).\n"
            f"Target application: {target_application}\n"
            f"Target window: {target_title}\n"
            f"Characters: {character_count}; lines: {line_count}\n"
            f"Method: {method}\n"
            f"Clipboard fallback: {'may be used' if clipboard else 'not planned'}\n"
            f"Reversible: {'yes' if reversible else 'no'}\n"
            "No Enter, Return, or Tab key will be pressed."
        )

    def _format_details(self, command: str, response: dict) -> str:
        formatters: dict[str, Callable[[dict], str]] = {
            "run_command": lambda r: (
                f"Agetha wants to run a shell command:\n\n  {r.get('cmd', '???')}\n\n"
                "This executes directly on your system."
            ),
            "delete_file": lambda r: (
                f"Agetha wants to DELETE:\n\n  {r.get('path', '???')}\n\n"
                "This permanently removes the file or folder."
            ),
            "force_close": lambda r: (
                f"Agetha wants to KILL process:\n\n  {CommandGuard._process_target(r) or '???'}\n\n"
                "This will force-close the application."
            ),
            "create_file": lambda r: (
                f"Agetha wants to CREATE a file:\n\n  {r.get('file_path', '') or r.get('path', '???')}"
            ),
            "write_file": lambda r: (
                f"Agetha wants to WRITE to file:\n\n  {r.get('file_path', '???')}\n"
                f"Mode: {r.get('mode', 'overwrite')}"
            ),
            "rename_file": lambda r: (
                f"Agetha wants to RENAME:\n\n  {r.get('path', '???')}\n  → {r.get('new_name', '???')}"
            ),
            "create_folder": lambda r: f"Agetha wants to CREATE a folder:\n\n  {r.get('path', '???')}",
            "lock_screen": lambda r: "Agetha wants to LOCK your computer screen.",
            "shutdown": lambda r: (
                f"Agetha wants to SHUT DOWN your computer.\n\nDelay: {r.get('delay', 60)} seconds\n"
                "All unsaved work will be lost!"
            ),
            "restart": lambda r: (
                f"Agetha wants to RESTART your computer.\n\nDelay: {r.get('delay', 60)} seconds\n"
                "All unsaved work will be lost!"
            ),
            "target_window_move": lambda r: (
                f"Move window '{r.get('target_app', '???')}' to ({r.get('x', '?')}, {r.get('y', '?')})"
            ),
            "target_window_resize": lambda r: (
                f"Resize window '{r.get('target_app', '???')}' to "
                f"{r.get('width', '?')}×{r.get('height', '?')}"
            ),
            "target_window_close": lambda r: f"Close window: {r.get('target_app', '???')}",
            "clear_memory": lambda r: (
                f"Clear episodic memory"
                f" (scope: {r.get('memory_scope', 'all') or 'all'})."
                f" Soul.md is kept."
            ),
            "clear_emotions": lambda r: (
                f"Reset Agetha's emotional state/history"
                f" (scope: {r.get('scope', 'all') or 'all'}"
                + (f", entry #{r.get('entry_id')}" if r.get('entry_id') else "")
                + "). This cannot be undone."
            ),
            "set_autostart": lambda r: CommandGuard._describe_autostart(r),
            "open_settings": lambda r: (
                f"Open the Windows Settings page: {r.get('page', 'home') or 'home'}"
                f" (allowlisted ms-settings page; nothing else is launched)."
            ),
            "set_theme": lambda r: CommandGuard._describe_theme(r),
            "search_web": lambda r: (
                f"Search the web for:\n\n  {(r.get('query', '???'))[:200]}"
            ),
            "fetch_webpage": lambda r: (
                f"Fetch webpage content from:\n\n  {(r.get('url', '???'))[:500]}"
            ),
            "analyze_screen_deep": lambda r: (
                "Send a screenshot of the "
                + ("focused window" if CommandGuard.parse_enabled({"enabled": r.get("focused_only", True)}) else "full screen")
                + " to the configured Unlimited-OCR service for read-only analysis."
            ),
            "open_app": lambda r: f"Launch: {r.get('app', '') or r.get('app_name', '???')}",
            "computer_use": lambda r: (
                "Start a bounded Computer Use session for this explicit goal:\n\n"
                f"{str(r.get('goal', '') or '[goal omitted]')[:300]}\n\n"
                "The session may interact only with configured/authorized applications "
                "and can be stopped immediately."
            ),
            "open_file": lambda r: f"Open file: {r.get('path', '???')}",
            "open_folder": lambda r: f"Open folder: {r.get('path', '???')}",
            "copy_to_clipboard": lambda r: f"Copy to clipboard: \"{(r.get('text', '???'))[:100]}\"",
            "set_clipboard": lambda r: f"Set clipboard: \"{(r.get('text', '???'))[:100]}\"",
            "search_files": lambda r: (
                f"Search pattern: {r.get('pattern', '???')}\nDirectory: {r.get('directory', '???')}"
            ),
            "play_sound": lambda r: f"Play sound: {r.get('path', '') or r.get('sound', '???')}",
            "set_volume": lambda r: f"Volume action: {r.get('action', 'set')} level: {r.get('level', '???')}%",
            "set_wallpaper": lambda r: f"Set wallpaper: {r.get('path', '???')}",
            "type_text": lambda r: CommandGuard._describe_type_text(r),
            "list_dir": lambda r: f"List directory: {r.get('path', '???')}",
            "list_directory": lambda r: f"List directory: {r.get('path', '???')}",
            "show_dialog": lambda r: (
                f"Show {r.get('dialog_type', 'info')} dialog: {r.get('title', 'Agetha')}: "
                f"{(r.get('message', '???'))[:100]}"
            ),
        }
        fn = formatters.get(command)
        if fn:
            try:
                return fn(response)
            except Exception:
                pass
        parts = [f"Command: {command}"]
        for key in ("path", "cmd", "app", "text", "url", "file_path", "target_app"):
            val = response.get(key)
            if val:
                parts.append(f"  {key}: {val}")
        return "\n".join(parts)
