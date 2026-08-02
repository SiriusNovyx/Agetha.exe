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

from agetha.utils import IS_WINDOWS, IS_LINUX, IS_MACOS, BASE_DIR, logger


def open_url(url: str) -> str:
    if not url:
        return "[no url]"
    try:
        webbrowser.open(url)
        return f"[opened: {url}]"
    except Exception as exc:
        return f"[open_url error: {exc}]"


def copy_to_clipboard(text: str, tk_root=None) -> str:
    if not text:
        return "[no text]"
    try:
        if tk_root:
            tk_root.clipboard_clear()
            tk_root.clipboard_append(text)
            tk_root.update()
            return "[copied to clipboard]"
        if IS_WINDOWS:
            subprocess.run(["clip"], input=text, text=True, check=True, timeout=5)
            return "[copied via clip]"
        if IS_MACOS:
            subprocess.run(["pbcopy"], input=text, text=True, check=True, timeout=5)
            return "[copied via pbcopy]"
        subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True, check=True, timeout=5)
        return "[copied via xclip]"
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
        if IS_MACOS:
            r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
            return (r.stdout or "").strip() or "[empty clipboard]"
        r = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True, timeout=5)
        return (r.stdout or "").strip() or "[empty clipboard]"
    except Exception as exc:
        return f"[clipboard read error: {exc}]"


def open_folder(path: str) -> str:
    if not path:
        return "[no path]"
    p = Path(path)
    if not p.exists():
        return f"[not found: {path}]"
    try:
        if IS_WINDOWS:
            os.startfile(str(p if p.is_dir() else p.parent))
        elif IS_MACOS:
            proc = subprocess.Popen(["open", str(p if p.is_dir() else p.parent)])
            proc.wait(timeout=30)
        else:
            proc = subprocess.Popen(
                ["xdg-open", str(p if p.is_dir() else p.parent)],
                start_new_session=True,
            )
            proc.wait(timeout=30)
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


def set_volume(level: int = 50, action: str = "set") -> str:
    action = (action or "set").lower()
    level = max(0, min(100, int(level)))
    try:
        if IS_WINDOWS:
            if action == "mute":
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "(New-Object -ComObject WScript.Shell).SendKeys([char]173)"],
                    timeout=5,
                )
                return "[muted]"
            if action == "unmute":
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "(New-Object -ComObject WScript.Shell).SendKeys([char]173)"],
                    timeout=5,
                )
                return "[unmuted]"
            # nircmd alternative via PowerShell AudioDeviceCmdlets not guaranteed — use key simulation steps
            steps = level // 2
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"$s=(New-Object -ComObject WScript.Shell); 1..50 | ForEach-Object {{ $s.SendKeys([char]174) }}; "
                 f"1..{steps} | ForEach-Object {{ $s.SendKeys([char]175) }}"],
                timeout=10,
            )
            return f"[volume set ~{level}%]"
        if IS_LINUX:
            if shutil.which("pactl"):
                if action == "mute":
                    subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"], timeout=5)
                    return "[muted]"
                if action == "unmute":
                    subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], timeout=5)
                    return "[unmuted]"
                subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"], timeout=5)
                return f"[volume {level}%]"
        if IS_MACOS and shutil.which("osascript"):
            subprocess.run(
                ["osascript", "-e", f"set volume output volume {level}"],
                timeout=5,
            )
            return f"[volume {level}%]"
    except Exception as exc:
        return f"[set_volume error: {exc}]"
    return "[set_volume not supported on this platform]"


def set_wallpaper(path: str) -> str:
    if not path or not Path(path).exists():
        return f"[wallpaper not found: {path}]"
    try:
        if IS_WINDOWS:
            import ctypes
            SPI_SETDESKWALLPAPER = 20
            ctypes.windll.user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, str(Path(path).resolve()), 3)
            return f"[wallpaper set: {path}]"
        if IS_MACOS:
            script = f'tell application "System Events" to tell every desktop to set picture to POSIX file "{path}"'
            subprocess.run(["osascript", "-e", script], timeout=10)
            return f"[wallpaper set: {path}]"
        if IS_LINUX and shutil.which("gsettings"):
            subprocess.run(
                ["gsettings", "set", "org.gnome.desktop.background", "picture-uri", f"file://{Path(path).resolve()}"],
                timeout=5,
            )
            return f"[wallpaper set: {path}]"
    except Exception as exc:
        return f"[set_wallpaper error: {exc}]"
    return "[set_wallpaper not supported]"


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
    if not text:
        return "[no text]"
    try:
        import pyautogui
        pyautogui.write(text, interval=0.02)
        return f"[typed {len(text)} chars]"
    except ImportError:
        return "[pyautogui not installed]"
    except Exception as exc:
        return f"[type_text error: {exc}]"


def lock_screen() -> str:
    try:
        if IS_WINDOWS:
            subprocess.run(
                ["rundll32.exe", "user32.dll,LockWorkStation"],
                timeout=5,
                check=True,
            )
            return "[screen locked]"
        if IS_LINUX and shutil.which("loginctl"):
            subprocess.run(["loginctl", "lock-session"], timeout=5, check=True)
            return "[screen locked]"
        if IS_MACOS:
            subprocess.run(
                ["/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession",
                 "-suspend"],
                timeout=5, check=True,
            )
            return "[screen locked]"
    except Exception as exc:
        return f"[lock_screen error: {exc}]"
    return "[lock_screen not supported]"


def _schedule_delayed_shutdown(delay: int, *, reboot: bool = False) -> None:
    """Schedule shutdown/restart with second precision where possible."""
    flag = "-r" if reboot else "-h"
    if delay <= 0:
        subprocess.run(["shutdown", flag, "now"], timeout=5, check=True)
        return
    if IS_LINUX:
        if shutil.which("systemd-run"):
            subprocess.run(
                [
                    "systemd-run", f"--on-active={delay}s", "--unit=agetha-shutdown",
                    "shutdown", flag, "now",
                ],
                timeout=5, check=True,
            )
            return
        if shutil.which("at"):
            cmd = "reboot" if reboot else "shutdown -h now"
            subprocess.run(
                ["bash", "-c", f"echo '{cmd}' | at now + {delay} seconds"],
                timeout=5, check=True,
            )
            return
    if IS_MACOS and shutil.which("at"):
        cmd = "sudo reboot" if reboot else "sudo shutdown -h now"
        subprocess.run(
            ["bash", "-c", f"echo '{cmd}' | at now + {delay} seconds"],
            timeout=5, check=True,
        )
        return
    subprocess.run(
        ["shutdown", flag, f"+{max(1, (delay + 59) // 60)}"],
        timeout=5,
        check=True,
    )


def shutdown_system(delay: int = 60) -> str:
    delay = max(0, int(delay))
    try:
        if IS_WINDOWS:
            subprocess.run(
                ["shutdown", "/s", "/t", str(delay)], timeout=5, check=True,
            )
            return f"[shutdown in {delay}s]"
        if IS_LINUX:
            _schedule_delayed_shutdown(delay, reboot=False)
            return f"[shutdown in {delay}s]"
        if IS_MACOS:
            if delay <= 0:
                subprocess.run(
                    ["sudo", "shutdown", "-h", "now"], timeout=5, check=True,
                )
            else:
                _schedule_delayed_shutdown(delay, reboot=False)
            return f"[shutdown in {delay}s]"
    except Exception as exc:
        return f"[shutdown error: {exc}]"
    return "[shutdown not supported]"


def restart_system(delay: int = 60) -> str:
    delay = max(0, int(delay))
    try:
        if IS_WINDOWS:
            subprocess.run(
                ["shutdown", "/r", "/t", str(delay)], timeout=5, check=True,
            )
            return f"[restart in {delay}s]"
        if IS_LINUX:
            _schedule_delayed_shutdown(delay, reboot=True)
            return f"[restart in {delay}s]"
        if IS_MACOS:
            if delay <= 0:
                subprocess.run(
                    ["sudo", "shutdown", "-r", "now"], timeout=5, check=True,
                )
            else:
                _schedule_delayed_shutdown(delay, reboot=True)
            return f"[restart in {delay}s]"
    except Exception as exc:
        return f"[restart error: {exc}]"
    return "[restart not supported]"


def set_reminder(seconds: int, reminder_text: str, callback) -> str:
    seconds = max(1, int(seconds))
    text = (reminder_text or "Reminder").strip()

    def _fire():
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
        if IS_MACOS:
            safe_msg = message.replace('"', '\\"')
            safe_title = title.replace('"', '\\"')
            subprocess.Popen(["osascript", "-e", f'display notification "{safe_msg}" with title "{safe_title}"'])
            return "[notification sent]"
        if shutil.which("notify-send"):
            subprocess.Popen(["notify-send", title, message])
            return "[notification sent]"
    except Exception as exc:
        return f"[notification error: {exc}]"
    return "[notification not supported]"


def screenshot_path(default_dir: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(default_dir, f"screenshot_{ts}.png")
