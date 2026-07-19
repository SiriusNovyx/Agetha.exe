"""
Agetha — Shared Utilities
Centralizes platform detection, common helpers, and config defaults.
"""

import sys
import os
import logging
from pathlib import Path

# ── Platform Detection ─────────────────────────────────────────────────────────
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"

# ── Logging Setup ──────────────────────────────────────────────────────────────
def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure and return the root Agetha logger."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="[%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger("Agetha")

logger = setup_logging()

# ── Base Paths ─────────────────────────────────────────────────────────────────
from agetha.app_config import BASE_DIR, CONFIG_PATH, ENV_PATH

ASSETS = BASE_DIR / "assets"
FONT_PATH = ASSETS / "barrio.ttf"
ICON_PATH = ASSETS / "icon.ico"


def apply_window_icon(widget) -> bool:
    """Apply assets/icon.ico to a Tk window (title bar / taskbar)."""
    if widget is None or not ICON_PATH.is_file():
        return False
    try:
        path = str(ICON_PATH.resolve())
        widget.iconbitmap(default=path)
        widget.iconbitmap(path)
        return True
    except Exception:
        try:
            widget.iconbitmap(str(ICON_PATH))
            return True
        except Exception as exc:
            logger.debug(f"Could not apply window icon: {exc}")
            return False


def native_message_box(title: str, message: str, flags: int, owner_hwnd: int = 0) -> int:
    """
    Windows MessageBoxW with assets/icon.ico on the dialog title bar.
    Returns the MessageBox result code (0 on failure / non-Windows).
    Does not change WinRT toast notifications.
    """
    if not IS_WINDOWS:
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user32.MessageBoxW.restype = ctypes.c_int

    hwnd_owner = int(owner_hwnd or 0)
    hwnd_temp = 0
    hicon = 0
    created_owner = False

    try:
        if ICON_PATH.is_file():
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x0010
            user32.LoadImageW.argtypes = [
                wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                ctypes.c_int, ctypes.c_int, wintypes.UINT,
            ]
            user32.LoadImageW.restype = wintypes.HANDLE
            hicon = user32.LoadImageW(None, str(ICON_PATH.resolve()), IMAGE_ICON, 0, 0, LR_LOADFROMFILE)

        if hwnd_owner == 0 and hicon:
            # Tiny tool window as MessageBox owner so the title-bar icon is icon.ico
            WS_POPUP = 0x80000000
            WS_EX_TOOLWINDOW = 0x00000080
            user32.CreateWindowExW.argtypes = [
                wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
            ]
            user32.CreateWindowExW.restype = wintypes.HWND
            hwnd_temp = user32.CreateWindowExW(
                WS_EX_TOOLWINDOW, "STATIC", title,
                WS_POPUP, 0, 0, 0, 0,
                None, None, None, None,
            )
            if hwnd_temp:
                WM_SETICON = 0x0080
                ICON_SMALL = 0
                ICON_BIG = 1
                user32.SendMessageW(hwnd_temp, WM_SETICON, ICON_SMALL, hicon)
                user32.SendMessageW(hwnd_temp, WM_SETICON, ICON_BIG, hicon)
                hwnd_owner = int(hwnd_temp)
                created_owner = True
        elif hwnd_owner and hicon:
            WM_SETICON = 0x0080
            user32.SendMessageW(hwnd_owner, WM_SETICON, 0, hicon)
            user32.SendMessageW(hwnd_owner, WM_SETICON, 1, hicon)

        return int(user32.MessageBoxW(hwnd_owner, message, title, flags))
    except Exception as exc:
        logger.warning(f"native_message_box failed: {exc}")
        try:
            return int(ctypes.windll.user32.MessageBoxW(0, message, title, flags))
        except Exception:
            return 0
    finally:
        if created_owner and hwnd_temp:
            try:
                user32.DestroyWindow(hwnd_temp)
            except Exception:
                pass
        if hicon:
            try:
                ctypes.windll.user32.DestroyIcon(hicon)
            except Exception:
                pass


# ── Native Error Popup ─────────────────────────────────────────────────────────
def native_error_popup(title: str, message: str) -> None:
    """Show a native OS error dialog.
    Uses Windows MessageBoxW (MB_ICONERROR | MB_TOPMOST) with tkinter fallback."""
    logger.error(f"{title}: {message}")
    if IS_WINDOWS:
        try:
            # 0x10 = MB_ICONERROR, 0x00040000 = MB_TOPMOST
            native_message_box(title, message, 0x10 | 0x00040000)
            return
        except Exception:
            pass
    try:
        import tkinter as _tk
        from tkinter import messagebox as _mb
        _r = _tk.Tk()
        _r.withdraw()
        apply_window_icon(_r)
        try:
            _r.attributes("-topmost", True)
        except Exception:
            pass
        _mb.showerror(title, message, parent=_r)
        _r.destroy()
    except Exception:
        pass

# ── .env File Loader ───────────────────────────────────────────────────────────
def load_env_file(env_path: Path = None) -> dict:
    """Parse a simple .env file into a dict. Lines: KEY=VALUE (no quotes needed)."""
    env_path = env_path or ENV_PATH
    env_vars = {}
    if not env_path.exists():
        return env_vars
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if value:  # only store non-empty values
                    env_vars[key] = value
    except Exception as e:
        logger.warning(f"Failed to read .env file: {e}")
    return env_vars

# ── Default Config Template ────────────────────────────────────────────────────
from agetha.app_config import DEFAULT_CONFIG, create_default_config as _create_default_config

def create_default_config(config_path: Path = None) -> None:
    """Write the default config template."""
    _create_default_config(config_path or CONFIG_PATH)
    logger.info(f"Created config.txt at {config_path or CONFIG_PATH}")

# ── Named Constants (from config.txt) ─────────────────────────────────────────
from agetha.app_config import get_settings

_cfg = get_settings()
TOUCH_COOLDOWN_SEC = _cfg.touch_cooldown_sec
WAKE_DELAY_MS = _cfg.wake_delay_ms
LOAF_TIMER_MS = _cfg.loaf_timer_ms
THREAD_JOIN_TIMEOUT = 0.4
SCREEN_POLL_INTERVAL_MS = _cfg.screen_poll_interval_ms
WINDOW_W = 340
WINDOW_H = 560
GIF_W = 340
GIF_H = 300


def refresh_config_constants() -> None:
    """Re-read config.txt and update module-level timing constants."""
    global TOUCH_COOLDOWN_SEC, WAKE_DELAY_MS, LOAF_TIMER_MS, SCREEN_POLL_INTERVAL_MS
    cfg = get_settings(reload=True)
    TOUCH_COOLDOWN_SEC = cfg.touch_cooldown_sec
    WAKE_DELAY_MS = cfg.wake_delay_ms
    LOAF_TIMER_MS = cfg.loaf_timer_ms
    SCREEN_POLL_INTERVAL_MS = cfg.screen_poll_interval_ms
