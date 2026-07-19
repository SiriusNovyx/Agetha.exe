"""
tray_scaffold.py — Optional system-tray presence, compatibility scaffold (v5.0.0).

`pystray` is NOT bundled with Agetha and no dependency is added for v5, so
this module is a compatibility scaffold rather than a guaranteed runtime
feature: if the user installs pystray (and Pillow) themselves AND sets
ENABLE_TRAY=yes, a visible tray icon appears with:

    Open Agetha · Pause status observation · Settings · Exit

When pystray is absent this module stays completely silent — no import
errors, no per-run warnings, no failed Medic checks. Closing the main window
never leaves Agetha running in the background unless the tray is active AND
TRAY_BACKGROUND_CLOSE=yes.

Import-safe on all platforms. Never raises.
"""

from __future__ import annotations

import importlib.util
import threading
from typing import Any

from agetha.utils import ICON_PATH, logger

_tray_icon: Any = None
_tray_thread: threading.Thread | None = None
_lock = threading.RLock()


def is_tray_available() -> bool:
    """True only if the OPTIONAL pystray + Pillow packages are installed."""
    try:
        return (importlib.util.find_spec("pystray") is not None
                and importlib.util.find_spec("PIL") is not None)
    except Exception:
        return False


def is_tray_running() -> bool:
    with _lock:
        return _tray_icon is not None


def should_background_close() -> bool:
    """Only hide-to-tray when the tray is actually visible AND configured."""
    try:
        from agetha.app_config import get_settings
        return is_tray_running() and bool(get_settings().tray_background_close)
    except Exception:
        return False


def tray_summary() -> str:
    """One-line status for settings UI / diagnostics."""
    try:
        from agetha.app_config import get_settings
        if not get_settings().enable_tray:
            return "Tray icon: OFF (ENABLE_TRAY=no)"
    except Exception:
        pass
    if not is_tray_available():
        return "Tray icon: unavailable (optional 'pystray' package not installed)"
    return "Tray icon: ON" if is_tray_running() else "Tray icon: enabled, not started"


def start_tray(app: Any) -> bool:
    """Start the tray icon if configured AND the optional packages exist.

    Silent no-op otherwise. Returns True only when the tray actually started.
    """
    global _tray_icon, _tray_thread
    try:
        from agetha.app_config import get_settings
        if not get_settings().enable_tray:
            return False
    except Exception:
        return False
    if not is_tray_available():
        # Scaffold path: absence of pystray is expected and must stay silent.
        logger.debug("tray_scaffold: pystray not installed; tray skipped")
        return False
    with _lock:
        if _tray_icon is not None:
            return True
    try:
        import pystray
        from PIL import Image

        image = Image.open(ICON_PATH) if ICON_PATH.is_file() else Image.new("RGB", (16, 16), "black")

        def _open(_icon=None, _item=None) -> None:
            try:
                app.root.after(0, app.root.deiconify)
                app.root.after(0, lambda: app.root.attributes("-topmost", True))
            except Exception:
                pass

        def _toggle_pause(_icon=None, _item=None) -> None:
            try:
                from agetha.features import status_providers
                status_providers.set_paused(not status_providers.is_paused())
            except Exception:
                pass

        def _settings(_icon=None, _item=None) -> None:
            try:
                app.root.after(0, getattr(app, "_open_dashboard", lambda: None))
            except Exception:
                pass

        def _exit(_icon=None, _item=None) -> None:
            stop_tray()
            try:
                app.root.after(0, app._shutdown)
            except Exception:
                pass

        def _pause_label(_item) -> str:
            try:
                from agetha.features import status_providers
                return ("Resume status observation" if status_providers.is_paused()
                        else "Pause status observation")
            except Exception:
                return "Pause status observation"

        menu = pystray.Menu(
            pystray.MenuItem("Open Agetha", _open, default=True),
            pystray.MenuItem(_pause_label, _toggle_pause),
            pystray.MenuItem("Settings", _settings),
            pystray.MenuItem("Exit", _exit),
        )
        icon = pystray.Icon("Agetha", image, "Agetha", menu)

        def _run() -> None:
            try:
                icon.run()
            except Exception as exc:
                logger.warning(f"tray_scaffold: tray loop ended: {exc}")

        thread = threading.Thread(target=_run, daemon=True, name="agetha-tray")
        with _lock:
            _tray_icon = icon
            _tray_thread = thread
        thread.start()
        logger.info("tray_scaffold: tray icon started")
        return True
    except Exception as exc:
        logger.warning(f"tray_scaffold: could not start tray: {exc}")
        return False


def stop_tray() -> None:
    """Stop the tray icon if running. Never raises."""
    global _tray_icon, _tray_thread
    with _lock:
        icon = _tray_icon
        _tray_icon = None
        _tray_thread = None
    if icon is not None:
        try:
            icon.stop()
        except Exception:
            pass
