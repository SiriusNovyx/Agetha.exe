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
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

ASSETS = BASE_DIR / "assets"
FONT_PATH = ASSETS / "barrio.ttf"
CONFIG_PATH = BASE_DIR / "config.txt"
ENV_PATH = BASE_DIR / ".env"

# ── Native Error Popup ─────────────────────────────────────────────────────────
def native_error_popup(title: str, message: str) -> None:
    """Show a native OS error dialog.
    Uses Windows MessageBoxW (MB_ICONERROR | MB_TOPMOST) with tkinter fallback."""
    logger.error(f"{title}: {message}")
    if IS_WINDOWS:
        try:
            import ctypes
            # 0x10 = MB_ICONERROR, 0x00040000 = MB_TOPMOST
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10 | 0x00040000)
            return
        except Exception:
            pass
    try:
        import tkinter as _tk
        from tkinter import messagebox as _mb
        _r = _tk.Tk()
        _r.withdraw()
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
from app_config import DEFAULT_CONFIG, create_default_config as _create_default_config

def create_default_config(config_path: Path = None) -> None:
    """Write the default config template."""
    _create_default_config(config_path or CONFIG_PATH)
    logger.info(f"Created config.txt at {config_path or CONFIG_PATH}")

# ── Named Constants (from config.txt) ─────────────────────────────────────────
from app_config import get_settings

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
