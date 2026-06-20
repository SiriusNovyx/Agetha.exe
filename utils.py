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
            # 0x10 = MB_ICONERROR, 0x1000 = MB_TOPMOST
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10 | 0x1000)
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
                if value:  # only store non-empty values
                    env_vars[key] = value
    except Exception as e:
        logger.warning(f"Failed to read .env file: {e}")
    return env_vars

# ── Default Config Template ────────────────────────────────────────────────────
DEFAULT_CONFIG = """# Agetha config file
# ─────────────────────────────────────────────────────────────────────────────

# ── AI Backend ────────────────────────────────────────────────────────────────

# Set to "yes" to use a local AI (Ollama) instead of Groq.
USE_LOCAL_AI = no

# Set to "no" to fully disable Groq even when USE_LOCAL_AI = no.
ENABLE_GROQ = yes

# ── Groq API Keys ─────────────────────────────────────────────────────────────
# Add up to 10 keys. Or use .env file for sensitive keys.
GROQ_API_KEY =
GROQ_API_KEY_2 =
GROQ_API_KEY_3 =
GROQ_API_KEY_4 =
GROQ_API_KEY_5 =
GROQ_API_KEY_6 =
GROQ_API_KEY_7 =
GROQ_API_KEY_8 =
GROQ_API_KEY_9 =
GROQ_API_KEY_10 =

# Groq model.
GROQ_MODEL = llama-3.3-70b-versatile

# ── Local AI (Ollama) ─────────────────────────────────────────────────────────
LOCAL_AI_MODEL =
LOCAL_AI_TIMEOUT = 30

# ── OS Permissions ────────────────────────────────────────────────────────────
ENABLE_COMMAND_EXECUTION = yes

# ── Context & Memory ─────────────────────────────────────────────────────────
MEMORY_CHARS = 600
HISTORY_LIMIT = 6
FILE_READ_CHARS = 200

# ── Animation ─────────────────────────────────────────────────────────────────
ANIMATION_SPEED = 0.6
"""

def create_default_config(config_path: Path = None) -> None:
    """Write the default config template."""
    config_path = config_path or CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    logger.info(f"Created config.txt at {config_path}")

# ── Named Constants ────────────────────────────────────────────────────────────
# Replaces magic numbers scattered throughout the codebase
TOUCH_COOLDOWN_SEC = 10.0
WAKE_DELAY_MS = 8000
LOAF_TIMER_MS = 15 * 60 * 1000  # 15 minutes
THREAD_JOIN_TIMEOUT = 0.4
SCREEN_POLL_INTERVAL_MS = 2 * 60 * 1000  # 2 minutes
WINDOW_W = 340
WINDOW_H = 560
GIF_W = 340
GIF_H = 300
