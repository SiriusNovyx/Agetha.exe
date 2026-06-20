"""Windows window discovery and control (move, resize, close, kill by title)."""

from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes

from utils import IS_WINDOWS, logger

if IS_WINDOWS:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    _WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    _SW_RESTORE = 9
    _WM_CLOSE = 0x0010
    _SWP_NOSIZE = 0x0001
    _SWP_NOZORDER = 0x0004
    _SWP_NOACTIVATE = 0x0010


def _window_title(hwnd: int) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _window_pid(hwnd: int) -> int | None:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) if pid.value else None


def find_windows(partial_name: str, *, exclude_hwnd: int | None = None) -> list[tuple[int, str]]:
    """Return visible windows whose title contains partial_name (case-insensitive)."""
    if not IS_WINDOWS or not partial_name.strip():
        return []

    needle = partial_name.strip().lower()
    matches: list[tuple[int, str]] = []

    def _enum_cb(hwnd: int, _lparam: int) -> bool:
        if exclude_hwnd and hwnd == exclude_hwnd:
            return True
        if not _user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        if title and needle in title.lower():
            matches.append((hwnd, title))
        return True

    try:
        _user32.EnumWindows(_WNDENUMPROC(_enum_cb), 0)
    except Exception as exc:
        logger.error(f"EnumWindows failed: {exc}")
    return matches


def find_window_hwnd(partial_name: str, *, exclude_hwnd: int | None = None) -> int | None:
    matches = find_windows(partial_name, exclude_hwnd=exclude_hwnd)
    if not matches:
        return None
    # Prefer the shortest title match (usually the main/top-level window).
    matches.sort(key=lambda item: len(item[1]))
    return matches[0][0]


def _prepare_window(hwnd: int) -> tuple[bool, str]:
    if not _user32.IsWindow(hwnd):
        return False, "Window handle is no longer valid."
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, _SW_RESTORE)
    return True, ""


def _last_error(prefix: str) -> str:
    code = _kernel32.GetLastError()
    return f"{prefix} (Win32 error {code})"


def move_window(hwnd: int, x: int, y: int) -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Not supported on this platform."
    ok, msg = _prepare_window(hwnd)
    if not ok:
        return False, msg
    flags = _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE
    if not _user32.SetWindowPos(hwnd, 0, int(x), int(y), 0, 0, flags):
        return False, _last_error("SetWindowPos failed")
    return True, "moved"


def resize_window(hwnd: int, x: int, y: int, width: int, height: int) -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Not supported on this platform."
    ok, msg = _prepare_window(hwnd)
    if not ok:
        return False, msg
    if not _user32.MoveWindow(hwnd, int(x), int(y), int(width), int(height), True):
        return False, _last_error("MoveWindow failed")
    return True, "resized"


def close_window(hwnd: int) -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Not supported on this platform."
    if not _user32.IsWindow(hwnd):
        return False, "Window handle is no longer valid."
    _user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
    return True, "close sent"


def kill_process_by_hwnd(hwnd: int) -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Not supported on this platform."
    pid = _window_pid(hwnd)
    if not pid:
        return False, "Could not resolve process ID for window."
    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True, f"killed pid {pid}"
    detail = (result.stderr or result.stdout or "").strip()
    return False, detail or f"taskkill failed for pid {pid}"


def kill_process_by_name(target: str) -> tuple[bool, str]:
    if not target.strip():
        return False, "No process name provided."
    name = os.path.basename(target.strip())
    if IS_WINDOWS:
        if not name.lower().endswith(".exe"):
            name = f"{name}.exe"
        result = subprocess.run(
            ["taskkill", "/IM", name, "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return True, f"killed {name}"
        detail = (result.stderr or result.stdout or "").strip()
        return False, detail or f"taskkill failed for {name}"
    result = subprocess.run(["pkill", "-f", target], capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return True, f"killed {target}"
    return False, (result.stderr or result.stdout or "").strip() or "pkill failed"


def resolve_target_hwnd(partial_name: str, *, exclude_hwnd: int | None = None) -> tuple[int | None, str]:
    """Find a window by title fragment; fall back to process executable name."""
    hwnd = find_window_hwnd(partial_name, exclude_hwnd=exclude_hwnd)
    if hwnd:
        return hwnd, _window_title(hwnd)

    # If the model passed "Notepad.exe", try windows owned by that process.
    if IS_WINDOWS and partial_name.lower().endswith(".exe"):
        try:
            import psutil
        except ImportError:
            return None, ""

        exe = os.path.basename(partial_name).lower()
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if (proc.info.get("name") or "").lower() != exe:
                    continue
                pid = proc.info["pid"]

                def _enum_pid(candidate: int, _lparam: int) -> bool:
                    nonlocal hwnd
                    if _window_pid(candidate) == pid and _user32.IsWindowVisible(candidate):
                        title = _window_title(candidate)
                        if title:
                            hwnd = candidate
                            return False
                    return True

                _user32.EnumWindows(_WNDENUMPROC(_enum_pid), 0)
                if hwnd:
                    return hwnd, _window_title(hwnd)
            except (psutil.Error, OSError):
                continue
    return None, ""


def operate_on_target(
    partial_name: str,
    *,
    exclude_hwnd: int | None = None,
    move: tuple[int, int] | None = None,
    resize: tuple[int, int, int, int] | None = None,
    close: bool = False,
    kill: bool = False,
) -> tuple[bool, str]:
    hwnd, title = resolve_target_hwnd(partial_name, exclude_hwnd=exclude_hwnd)
    if not hwnd:
        return False, f"Window not found: {partial_name}"

    if move:
        ok, msg = move_window(hwnd, move[0], move[1])
        if not ok:
            return False, f"{title}: {msg}"
    if resize:
        ok, msg = resize_window(hwnd, resize[0], resize[1], resize[2], resize[3])
        if not ok:
            return False, f"{title}: {msg}"
    if close:
        ok, msg = close_window(hwnd)
        if not ok:
            return False, f"{title}: {msg}"
        return True, f"Close sent to: {title}"
    if kill:
        ok, msg = kill_process_by_hwnd(hwnd)
        if not ok:
            return False, f"{title}: {msg}"
        return True, f"Killed process for: {title}"

    action = "moved" if move else "resized"
    return True, f"{action.title()} window: {title}"
