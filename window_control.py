"""Windows window discovery and control (move, resize, close, kill by title)."""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from difflib import SequenceMatcher
from typing import Callable

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

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

PickerFn = Callable[[list[tuple[int, str]]], int | None]

_SELF_WINDOW_ALIASES = frozenset({
    "agetha", "agetha.exe", "agetha mod", "self", "me", "myself", "herself",
    "this", "my window", "i", "virus",
})

_SELF_PROCESS_ALIASES = _SELF_WINDOW_ALIASES | frozenset({
    "python", "python.exe", "pythonw", "pythonw.exe", "main.py",
})


def is_self_window_target(name: str) -> bool:
    """True when AI likely meant Agetha's own window, not an external app."""
    n = name.strip().lower()
    if not n:
        return False
    if n in _SELF_WINDOW_ALIASES:
        return True
    if n.startswith("agetha"):
        return True
    return "agetha.exe" in n


def is_self_process_target(name: str) -> bool:
    """True when force_close would target Agetha or her Python host."""
    n = name.strip().lower()
    if not n:
        return False
    if n in _SELF_PROCESS_ALIASES:
        return True
    base = os.path.basename(n.replace("\\", "/"))
    if base in _SELF_PROCESS_ALIASES:
        return True
    return is_self_window_target(n)


_FRAME_MS = 16


def ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def _animation_settings() -> tuple[bool, int]:
    try:
        from app_config import get_settings
        s = get_settings()
        return s.window_move_smooth, s.window_move_duration_ms
    except Exception:
        return True, 280


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


def resolve_target_name(partial_name: str) -> str:
    """Apply TARGET_APP_ALIASES from config (e.g. notepad → Notepad)."""
    name = partial_name.strip()
    if not name:
        return name
    try:
        from app_config import get_settings
        aliases = get_settings().target_app_aliases()
        return aliases.get(name.lower(), name)
    except Exception:
        return name


def score_window_match(needle: str, title: str) -> float:
    """Higher score = better match."""
    n, t = needle.lower().strip(), title.lower().strip()
    if not n or not t:
        return 0.0
    if t == n:
        return 100.0
    if t.startswith(n):
        return 90.0 - len(t) * 0.01
    if n in t:
        return 75.0 - len(t) * 0.01
    return SequenceMatcher(None, n, t).ratio() * 55.0


def rank_window_matches(needle: str, matches: list[tuple[int, str]]) -> list[tuple[int, str]]:
    ranked = sorted(
        matches,
        key=lambda item: (-score_window_match(needle, item[1]), len(item[1])),
    )
    return ranked


def find_windows(partial_name: str, *, exclude_hwnd: int | None = None) -> list[tuple[int, str]]:
    """Return visible windows whose title contains partial_name (case-insensitive)."""
    if not IS_WINDOWS or not partial_name.strip():
        return []

    needle = resolve_target_name(partial_name).lower()
    matches: list[tuple[int, str]] = []

    def _enum_cb(hwnd: int, _lparam: int) -> bool:
        if exclude_hwnd and hwnd == exclude_hwnd:
            return True
        if not _user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        if not title:
            return True
        t_lower = title.lower()
        if needle in t_lower or score_window_match(needle, title) >= 40.0:
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
    return rank_window_matches(resolve_target_name(partial_name), matches)[0][0]


def _pick_hwnd(
    needle: str,
    matches: list[tuple[int, str]],
    *,
    picker: PickerFn | None,
) -> int | None:
    ranked = rank_window_matches(needle, matches)
    if len(ranked) == 1:
        return ranked[0][0]

    exact = [item for item in ranked if item[1].lower() == needle.lower()]
    if len(exact) == 1:
        return exact[0][0]

    try:
        from app_config import get_settings
        use_picker = get_settings().window_picker_on_ambiguous
    except Exception:
        use_picker = True

    if use_picker and picker and len(ranked) > 1:
        top_score = score_window_match(needle, ranked[0][1])
        second_score = score_window_match(needle, ranked[1][1])
        if top_score - second_score < 8.0 or top_score < 85.0:
            picked = picker(ranked[:8])
            if picked:
                return picked

    return ranked[0][0]


def _prepare_window(hwnd: int) -> tuple[bool, str]:
    if not _user32.IsWindow(hwnd):
        return False, "Window handle is no longer valid."
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, _SW_RESTORE)
    return True, ""


def _last_error(prefix: str) -> str:
    code = _kernel32.GetLastError()
    hint = ""
    if code == 5:
        hint = " (Admin-only — try Run_Agetha_Admin.ps1 for elevated apps.)"
    return f"{prefix} (Win32 error {code}){hint}"


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """Return (x, y, width, height) in screen pixels. Measure once before animating."""
    if not IS_WINDOWS:
        return None
    rect = _RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    return int(rect.left), int(rect.top), int(w), int(h)


def _animate_rect(
    hwnd: int,
    x0: int,
    y0: int,
    w0: int,
    h0: int,
    x1: int,
    y1: int,
    w1: int,
    h1: int,
    duration_ms: int,
) -> bool:
    """Step position/size with ease-out cubic; fixed frame interval, clear stop at t=1."""
    dx, dy, dw, dh = x1 - x0, y1 - y0, w1 - w0, h1 - h0
    if dx == 0 and dy == 0 and dw == 0 and dh == 0:
        return True
    duration_s = max(duration_ms, 1) / 1000.0
    start = time.perf_counter()
    flags = _SWP_NOZORDER | _SWP_NOACTIVATE
    resize = dw != 0 or dh != 0 or w1 != w0 or h1 != h0

    while True:
        elapsed = time.perf_counter() - start
        t = min(1.0, elapsed / duration_s)
        e = ease_out_cubic(t)
        cx = int(x0 + dx * e)
        cy = int(y0 + dy * e)
        if resize:
            cw = max(1, int(w0 + dw * e))
            ch = max(1, int(h0 + dh * e))
            ok = bool(_user32.SetWindowPos(hwnd, 0, cx, cy, cw, ch, flags))
        else:
            ok = bool(_user32.SetWindowPos(hwnd, 0, cx, cy, 0, 0, flags | _SWP_NOSIZE))
        if not ok:
            return False
        if t >= 1.0:
            break
        time.sleep(_FRAME_MS / 1000.0)
    return True


def move_window(
    hwnd: int,
    x: int,
    y: int,
    *,
    smooth: bool | None = None,
    duration_ms: int | None = None,
) -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Not supported on this platform."
    ok, msg = _prepare_window(hwnd)
    if not ok:
        return False, msg

    x, y = int(x), int(y)
    use_smooth, dur = _animation_settings()
    if smooth is not None:
        use_smooth = smooth
    if duration_ms is not None:
        dur = duration_ms

    rect = get_window_rect(hwnd)
    if use_smooth and dur > 0 and rect:
        x0, y0, w0, h0 = rect
        if abs(x - x0) + abs(y - y0) >= 4:
            if _animate_rect(hwnd, x0, y0, w0, h0, x, y, w0, h0, dur):
                return True, "moved"
            return False, _last_error("SetWindowPos failed during animation")

    flags = _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE
    if not _user32.SetWindowPos(hwnd, 0, x, y, 0, 0, flags):
        return False, _last_error("SetWindowPos failed")
    return True, "moved"


def resize_window(
    hwnd: int,
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    smooth: bool | None = None,
    duration_ms: int | None = None,
) -> tuple[bool, str]:
    if not IS_WINDOWS:
        return False, "Not supported on this platform."
    ok, msg = _prepare_window(hwnd)
    if not ok:
        return False, msg

    x, y, width, height = int(x), int(y), max(1, int(width)), max(1, int(height))
    use_smooth, dur = _animation_settings()
    if smooth is not None:
        use_smooth = smooth
    if duration_ms is not None:
        dur = duration_ms

    rect = get_window_rect(hwnd)
    if use_smooth and dur > 0 and rect:
        x0, y0, w0, h0 = rect
        delta = abs(x - x0) + abs(y - y0) + abs(width - w0) + abs(height - h0)
        if delta >= 4:
            if _animate_rect(hwnd, x0, y0, w0, h0, x, y, width, height, dur):
                return True, "resized"
            return False, _last_error("SetWindowPos failed during animation")

    if not _user32.MoveWindow(hwnd, x, y, width, height, True):
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
    name = os.path.basename(resolve_target_name(target).strip())
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


def resolve_target_hwnd(
    partial_name: str,
    *,
    exclude_hwnd: int | None = None,
    picker: PickerFn | None = None,
) -> tuple[int | None, str]:
    """Find a window by title fragment; fall back to process executable name."""
    needle = resolve_target_name(partial_name)
    matches = find_windows(needle, exclude_hwnd=exclude_hwnd)
    hwnd: int | None = None
    if matches:
        hwnd = _pick_hwnd(needle, matches, picker=picker)
        if hwnd:
            return hwnd, _window_title(hwnd)

    if IS_WINDOWS and needle.lower().endswith(".exe"):
        try:
            import psutil
        except ImportError:
            return None, ""

        exe = os.path.basename(needle).lower()
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
    picker: PickerFn | None = None,
) -> tuple[bool, str]:
    hwnd, title = resolve_target_hwnd(partial_name, exclude_hwnd=exclude_hwnd, picker=picker)
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
