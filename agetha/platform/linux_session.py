"""Side-effect-free Linux desktop session and capture capability detection."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


_SCREENSHOT_COMMANDS = ("grim", "spectacle", "scrot", "gnome-screenshot")


@dataclass(frozen=True)
class LinuxDesktopCapabilities:
    platform: str
    session_type: str
    display_present: bool
    wayland_display_present: bool
    xauthority_readable: bool
    desktop_environment: str
    available_screenshot_commands: tuple[str, ...]
    selected_screenshot_backend: str
    automatic_ocr_supported: bool
    explicit_capture_supported: bool
    x11_bridge: bool

    def diagnostic_summary(self) -> str:
        return (
            f"[Linux] session={self.session_type} "
            f"x11_bridge={'yes' if self.x11_bridge else 'no'} "
            f"screenshot_backend={self.selected_screenshot_backend} "
            f"automatic_ocr={'yes' if self.automatic_ocr_supported else 'no'}"
        )


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def detect_linux_desktop(
    *,
    env: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    pyautogui_ok: bool = False,
    imagegrab_ok: bool = False,
    mss_ok: bool | None = None,
) -> LinuxDesktopCapabilities:
    """Describe Linux capture policy without connecting to X11 or Wayland."""
    values = os.environ if env is None else env
    current_platform = sys.platform if platform_name is None else platform_name
    is_linux = str(current_platform).startswith("linux")
    display_present = bool(values.get("DISPLAY")) if is_linux else False
    wayland_present = bool(values.get("WAYLAND_DISPLAY")) if is_linux else False
    declared = str(values.get("XDG_SESSION_TYPE", "")).strip().casefold()
    if not is_linux:
        session_type = "unknown"
    elif declared in {"x11", "wayland"}:
        session_type = declared
    elif wayland_present:
        session_type = "wayland"
    elif display_present:
        session_type = "x11"
    else:
        session_type = "unknown"

    authority = str(values.get("XAUTHORITY", "")).strip()
    xauthority_readable = False
    if is_linux and authority:
        try:
            xauthority_readable = Path(authority).is_file() and os.access(authority, os.R_OK)
        except OSError:
            xauthority_readable = False

    desktop_environment = str(
        values.get("XDG_CURRENT_DESKTOP") or values.get("DESKTOP_SESSION") or "unknown"
    ).strip().casefold()[:80]
    commands = tuple(
        command for command in _SCREENSHOT_COMMANDS
        if is_linux and which(command) is not None
    )
    has_mss = _module_available("mss") if mss_ok is None else bool(mss_ok)

    selected = "unavailable"
    automatic = False
    explicit = False
    if session_type == "x11" and display_present:
        if has_mss:
            selected = "x11-mss"
        elif imagegrab_ok:
            selected = "x11-imagegrab"
        elif "scrot" in commands:
            selected = "x11-scrot"
        elif pyautogui_ok:
            selected = "x11-pyautogui"
        automatic = selected != "unavailable"
        explicit = automatic
    elif session_type == "wayland" and wayland_present:
        # GNOME's screenshot command may fall back to X11 and emit invalid-rectangle
        # GTK errors. Keep it out of the policy. These two tools are only selected
        # when explicitly installed for their compositor and are never used by
        # background OCR.
        if "grim" in commands:
            selected = "wayland-grim"
            explicit = True
        elif "spectacle" in commands:
            selected = "wayland-spectacle"
            explicit = True

    return LinuxDesktopCapabilities(
        platform="linux" if is_linux else str(current_platform),
        session_type=session_type,
        display_present=display_present,
        wayland_display_present=wayland_present,
        xauthority_readable=xauthority_readable,
        desktop_environment=desktop_environment or "unknown",
        available_screenshot_commands=commands,
        selected_screenshot_backend=selected,
        automatic_ocr_supported=automatic,
        explicit_capture_supported=explicit,
        x11_bridge=display_present,
    )
