"""
w95_window.py — Borderless Toplevel setup for Win95 custom chrome on Win10/11.

Removes the native DWM title bar so only the drawn Win95 header is visible.
"""

from __future__ import annotations

import sys
import tkinter as tk
from typing import TYPE_CHECKING

from agetha.utils import logger

if TYPE_CHECKING:
    from tkinter import Misc

IS_WINDOWS = sys.platform == "win32"
_GWL_STYLE = -16
_WS_CAPTION = 0x00C00000
_WS_THICKFRAME = 0x00040000
_WS_MINIMIZEBOX = 0x00020000
_WS_MAXIMIZEBOX = 0x00010000
_SWP_FRAMECHANGED = 0x0020
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004


def _resolve_hwnd(win: tk.Misc) -> int:
    if not IS_WINDOWS:
        return 0
    try:
        import ctypes
        wid = int(win.winfo_id())
        hwnd = ctypes.windll.user32.GetParent(wid)
        return int(hwnd or wid)
    except Exception:
        return 0


def strip_native_caption(win: tk.Misc) -> None:
    """Strip Win32 caption frame after overrideredirect (Win10/11 DWM quirk)."""
    if not IS_WINDOWS:
        return
    hwnd = _resolve_hwnd(win)
    if not hwnd:
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, _GWL_STYLE)
        style &= ~(_WS_CAPTION | _WS_THICKFRAME | _WS_MINIMIZEBOX | _WS_MAXIMIZEBOX)
        user32.SetWindowLongW(hwnd, _GWL_STYLE, style)
        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_FRAMECHANGED,
        )
    except Exception as exc:
        logger.warning(f"w95_window: strip_native_caption failed: {exc}")


def apply_borderless_win95(
    win: tk.Toplevel,
    parent: tk.Misc | None = None,
    *,
    win: tk.Toplevel,
    parent: tk.Misc | None = None,
    *,
    topmost: bool = True,
) -> None:
    """
    Prepare a Toplevel for custom Win95 chrome only (no native title bar).
    Call before packing widgets; call refresh_borderless() after minimize restore.
    """
    if IS_WINDOWS:
        try:
            win.withdraw()
        except Exception:
            pass
        try:
            win.overrideredirect(True)
        except Exception:
            pass
    if parent is not None:
        try:
            win.transient(parent)
        except Exception:
            pass
    if topmost:
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass


def refresh_borderless(win: tk.Misc) -> None:
    """Re-apply borderless chrome after map/deiconify (e.g. after minimize)."""
    if not IS_WINDOWS:
        return
    try:
        win.overrideredirect(True)
    except Exception:
        pass
    try:
        win.update_idletasks()
        strip_native_caption(win)
        win.lift()
    except Exception as exc:
        logger.warning(f"w95_window: refresh_borderless failed: {exc}")


def show_borderless(win: tk.Misc) -> None:
    """Finalize and show a borderless window."""
    try:
        win.update_idletasks()
        if IS_WINDOWS:
            strip_native_caption(win)
        win.deiconify()
        if IS_WINDOWS:
            win.lift()
    except Exception as exc:
        logger.warning(f"w95_window: show_borderless failed: {exc}")


def minimize_managed(win: tk.Misc) -> bool:
    """Use the platform window manager's ordinary minimize transition."""
    try:
        if win.state() != "iconic":
            win.iconify()
        return True
    except Exception:
        return False


def restore_managed(win: tk.Misc) -> bool:
    """Restore a managed window without changing its decoration policy."""
    try:
        if win.state() != "normal":
            win.deiconify()
        return True
    except Exception:
        return False
