"""
system_commands.py — OS utility functions for Agetha command handlers.
"""

from __future__ import annotations

import glob
import os
import platform
import shutil
import subprocess
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Callable

from agetha.utils import IS_WINDOWS, IS_LINUX, BASE_DIR, logger


def open_url(url: str) -> str:
    if not url:
        return "[no url]"
    try:
        webbrowser.open(url)
        return f"[opened: {url}]"
    except Exception as exc:
        return f"[open_url error: {exc}]"


def copy_to_clipboard(
    text: str, tk_root=None, *,
    effect_runner: Callable[[Callable[[], object]], tuple[bool, object | None]] | None = None,
) -> str:
    if not text:
        return "[no text]"
    try:
        if tk_root:
            def _copy():
                tk_root.clipboard_clear()
                tk_root.clipboard_append(text)

            performed, _value = effect_runner(_copy) if effect_runner is not None else (True, _copy())
            if not performed:
                return "[clipboard blocked: capability changed]"
            # Tk event processing must not retain the capability lock.
            tk_root.update()
            return "[copied to clipboard]"
        if effect_runner is not None:
            return "[clipboard blocked: Tk root unavailable]"
        if IS_WINDOWS:
            subprocess.run(["clip"], input=text, text=True, check=True, timeout=5)
            return "[copied via clip]"
        if IS_LINUX:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True, check=True, timeout=5)
            return "[copied via xclip]"
        return "[clipboard not supported]"
    except Exception as exc:
        return f"[clipboard error: {exc}]"


def get_clipboard(tk_root=None) -> str:
    try:
        if tk_root:
            return tk_root.clipboard_get()
        if IS_WINDOWS:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                capture_output=True, text=True, timeout=5,
            )
            return (r.stdout or "").strip() or "[empty clipboard]"
        if IS_LINUX:
            r = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True, timeout=5)
            return (r.stdout or "").strip() or "[empty clipboard]"
        return "[clipboard not supported]"
    except Exception as exc:
        return f"[clipboard read error: {exc}]"


def open_folder(
    path: str, *,
    effect_runner: Callable[[Callable[[], object]], tuple[bool, object | None]] | None = None,
) -> str:
    """Authorize only launch when a runner is supplied; wait outside its lock."""
    if not path:
        return "[no path]"
    p = Path(path)
    if not p.exists():
        return f"[not found: {path}]"
    try:
        folder = str(p if p.is_dir() else p.parent)
        if IS_WINDOWS:
            effect = lambda: os.startfile(folder)
        elif IS_LINUX:
            effect = lambda: subprocess.Popen(["xdg-open", folder], start_new_session=True)
        else:
            return "[open_folder not supported]"
        performed, proc = effect_runner(effect) if effect_runner is not None else (True, effect())
        if not performed:
            return "[open_folder blocked: capability changed]"
        if not IS_WINDOWS:
            with proc:
                try:
                    proc.wait(timeout=30)
                except BaseException:
                    proc.kill()
                    raise
        return f"[opened folder: {path}]"
    except Exception as exc:
        return f"[open_folder error: {exc}]"


def system_info() -> str:
    lines = [f"OS: {platform.system()} {platform.release()}", f"Machine: {platform.machine()}"]
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        lines.append(f"CPU: {cpu}%")
        lines.append(f"RAM: {mem.percent}% used ({mem.used // (1024**2)} MB / {mem.total // (1024**2)} MB)")
        disk = psutil.disk_usage("/" if not IS_WINDOWS else "C:\\")
        lines.append(f"Disk: {disk.percent}% used")
    except ImportError:
        lines.append("Install psutil for detailed CPU/RAM/disk stats.")
    except Exception as exc:
        lines.append(f"Stats error: {exc}")
    return "\n".join(lines)


def set_volume(
    level: int = 50, action: str = "set", *,
    effect_runner: Callable[[Callable[[], object]], tuple[bool, object | None]] | None = None,
) -> str:
    action = (action or "set").lower()
    level = max(0, min(100, int(level)))
    try:
        timeout = 5
        if IS_WINDOWS:
            if action in ("mute", "unmute"):
                script = "(New-Object -ComObject WScript.Shell).SendKeys([char]173)"
                result = "[muted]" if action == "mute" else "[unmuted]"
            else:
                steps = level // 2
                script = (
                    f"$s=(New-Object -ComObject WScript.Shell); 1..50 | ForEach-Object {{ $s.SendKeys([char]174) }}; "
                    f"1..{steps} | ForEach-Object {{ $s.SendKeys([char]175) }}"
                )
                timeout = 10
                result = f"[volume set ~{level}%]"
            command = ["powershell", "-NoProfile", "-Command", script]
        elif IS_LINUX and shutil.which("pactl"):
            if action in ("mute", "unmute"):
                command = ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if action == "mute" else "0"]
                result = "[muted]" if action == "mute" else "[unmuted]"
            else:
                command = ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"]
                result = f"[volume {level}%]"
        else:
            return "[set_volume not supported on this platform]"
        effect = lambda: subprocess.Popen(command)
        performed, proc = effect_runner(effect) if effect_runner is not None else (True, effect())
        if not performed:
            return "[set_volume blocked: capability changed]"
        with proc:
            try:
                proc.wait(timeout=timeout)
            except BaseException:
                proc.kill()
                raise
        return result
    except Exception as exc:
        return f"[set_volume error: {exc}]"


def set_wallpaper(
    path: str, *,
    effect_runner: Callable[[Callable[[], object]], tuple[bool, object | None]] | None = None,
) -> str:
    """Keep preparation and child waits outside the supplied effect boundary."""
    if not path or not Path(path).exists():
        return f"[wallpaper not found: {path}]"
    try:
        if IS_WINDOWS:
            import ctypes
            SPI_SETDESKWALLPAPER = 20
            resolved = str(Path(path).resolve())
            effect = lambda: ctypes.windll.user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, resolved, 3)
        elif IS_LINUX and shutil.which("gsettings"):
            resolved = str(Path(path).resolve())
            effect = lambda: subprocess.Popen(
                ["gsettings", "set", "org.gnome.desktop.background", "picture-uri", f"file://{resolved}"],
            )
            timeout = 5
        else:
            return "[set_wallpaper not supported]"
        performed, proc = effect_runner(effect) if effect_runner is not None else (True, effect())
        if not performed:
            return "[set_wallpaper blocked: capability changed]"
        if not IS_WINDOWS:
            with proc:
                try:
                    proc.wait(timeout=timeout)
                except BaseException:
                    proc.kill()
                    raise
        return f"[wallpaper set: {path}]"
    except Exception as exc:
        return f"[set_wallpaper error: {exc}]"


def search_files(pattern: str, directory: str, limit: int = 50) -> list[str]:
    if not pattern or not directory:
        return ["[missing pattern or directory]"]
    allowed_roots = [Path.home().resolve(), BASE_DIR.resolve()]
    try:
        base = Path(directory).resolve()
    except OSError as exc:
        return [f"[invalid directory: {exc}]"]
    if not base.is_dir():
        return [f"[not a directory: {directory}]"]
    if not any(base == root or base.is_relative_to(root) for root in allowed_roots):
        return ["[directory not permitted]"]
    try:
        matches = []
        for p in base.rglob(pattern):
            if len(matches) >= limit:
                break
            matches.append(str(p))
        return matches or ["[no matches]"]
    except Exception as exc:
        return [f"[search error: {exc}]"]


def type_text(text: str) -> str:
    """Compatibility shim: direct text entry is intentionally refused.

    The registered command handler owns feature gates, Command Guard, target
    capture, preview, cancellation, and clipboard policy.  A standalone helper
    cannot reproduce that authority safely, so callers must use normal dispatch.
    """
    _ = text
    return "[type_text refused: use guarded command dispatch]"


def lock_screen(
    *, effect_runner: Callable[[Callable[[], object]], tuple[bool, object | None]] | None = None,
) -> str:
    try:
        if IS_WINDOWS:
            command = ["rundll32.exe", "user32.dll,LockWorkStation"]
        elif IS_LINUX and shutil.which("loginctl"):
            command = ["loginctl", "lock-session"]
        else:
            return "[lock_screen not supported]"
        effect = lambda: subprocess.Popen(command)
        performed, proc = effect_runner(effect) if effect_runner is not None else (True, effect())
        if not performed:
            return "[lock_screen blocked: capability changed]"
        with proc:
            try:
                code = proc.wait(timeout=5)
            except BaseException:
                proc.kill()
                raise
            if code:
                raise subprocess.CalledProcessError(code, command)
        return "[screen locked]"
    except Exception as exc:
        return f"[lock_screen error: {exc}]"


def _schedule_delayed_shutdown(
    delay: int, *, reboot: bool = False,
    effect_runner: Callable[[Callable[[], object]], tuple[bool, object | None]] | None = None,
) -> bool:
    """Schedule shutdown/restart with second precision where possible."""
    if not (IS_WINDOWS or IS_LINUX):
        return False
    flag = "-r" if reboot else "-h"
    if IS_WINDOWS:
        command = ["shutdown", "/r" if reboot else "/s", "/t", str(delay)]
    elif delay <= 0:
        command = ["shutdown", flag, "now"]
    elif IS_LINUX and shutil.which("systemd-run"):
        command = ["systemd-run", f"--on-active={delay}s", "--unit=agetha-shutdown",
                   "shutdown", flag, "now"]
    elif IS_LINUX and shutil.which("at"):
        cmd = "reboot" if reboot else "shutdown -h now"
        command = ["bash", "-c", f"echo '{cmd}' | at now + {delay} seconds"]
    else:
        command = ["shutdown", flag, f"+{max(1, (delay + 59) // 60)}"]
    effect = lambda: subprocess.Popen(command)
    performed, proc = effect_runner(effect) if effect_runner is not None else (True, effect())
    if not performed:
        return False
    with proc:
        try:
            code = proc.wait(timeout=5)
        except BaseException:
            proc.kill()
            raise
        if code:
            raise subprocess.CalledProcessError(code, command)
    return True


def shutdown_system(
    delay: int = 60, *,
    effect_runner: Callable[[Callable[[], object]], tuple[bool, object | None]] | None = None,
) -> str:
    delay = max(0, int(delay))
    try:
        if IS_WINDOWS or IS_LINUX:
            if not _schedule_delayed_shutdown(delay, reboot=False, effect_runner=effect_runner):
                return "[shutdown blocked: capability changed]"
            return f"[shutdown in {delay}s]"
    except Exception as exc:
        return f"[shutdown error: {exc}]"
    return "[shutdown not supported]"


def restart_system(
    delay: int = 60, *,
    effect_runner: Callable[[Callable[[], object]], tuple[bool, object | None]] | None = None,
) -> str:
    delay = max(0, int(delay))
    try:
        if IS_WINDOWS or IS_LINUX:
            if not _schedule_delayed_shutdown(delay, reboot=True, effect_runner=effect_runner):
                return "[restart blocked: capability changed]"
            return f"[restart in {delay}s]"
    except Exception as exc:
        return f"[restart error: {exc}]"
    return "[restart not supported]"


def set_reminder(
    seconds: int,
    reminder_text: str,
    callback,
    *,
    cancel_check=None,
) -> str:
    seconds = max(1, int(seconds))
    text = (reminder_text or "Reminder").strip()

    def _fire():
        try:
            if cancel_check is not None and cancel_check():
                return
        except Exception as exc:
            logger.warning("Reminder authorization check failed closed: %s", type(exc).__name__)
            return
        try:
            callback(text)
        except Exception as exc:
            logger.warning(f"Reminder callback failed: {exc}")

    threading.Timer(float(seconds), _fire).start()
    return f"[reminder in {seconds}s: {text}]"


def show_notification(title: str, message: str) -> str:
    if not message:
        return "[no message]"
    try:
        if IS_WINDOWS:
            # Prefer WinRT toast under AppUserModelID "Agetha.Desktop" (Start Menu shortcut).
            try:
                from agetha.platform.windows_notify import show_toast
                if show_toast(title or "Agetha", message):
                    return "[notification sent]"
            except Exception as toast_exc:
                logger.warning(f"Agetha toast path failed, falling back to balloon: {toast_exc}")

            import base64
            from agetha.utils import ICON_PATH
            msg_ps = message.replace("'", "''").replace("`", "``")
            title_ps = title.replace("'", "''").replace("`", "``")
            icon_ps = str(ICON_PATH.resolve()).replace("'", "''") if ICON_PATH.is_file() else ""
            # Fallback: classic NotifyIcon balloon with icon.ico
            ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$t = '{title_ps}'
$m = '{msg_ps}'
$iconPath = '{icon_ps}'
try {{
    $n = New-Object System.Windows.Forms.NotifyIcon
    if ($iconPath -and (Test-Path -LiteralPath $iconPath)) {{
        $n.Icon = New-Object System.Drawing.Icon($iconPath)
    }} else {{
        $n.Icon = [System.Drawing.SystemIcons]::Application
    }}
    $n.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::None
    $n.BalloonTipTitle = $t
    $n.BalloonTipText = $m
    $n.Text = 'Agetha'
    $n.Visible = $true
    $n.ShowBalloonTip(8000)
    Start-Sleep -Milliseconds 8500
    $n.Dispose()
}} catch {{
    [System.Windows.Forms.MessageBox]::Show($m, $t, 'OK', 'Information') | Out-Null
}}
"""
            encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
            # Prefer CREATE_NO_WINDOW over -WindowStyle Hidden (latter can minimize caller console).
            popen_kw: dict = {"shell": False}
            if IS_WINDOWS:
                popen_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                **popen_kw,
            )
            return "[notification sent]"
        if IS_LINUX and shutil.which("notify-send"):
            subprocess.Popen(["notify-send", title, message])
            return "[notification sent]"
    except Exception as exc:
        return f"[notification error: {exc}]"
    return "[notification not supported]"


def screenshot_path(default_dir: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(default_dir, f"screenshot_{ts}.png")
