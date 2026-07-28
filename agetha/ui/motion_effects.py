"""Centralized, guarded geometry motion for mood expression."""

from __future__ import annotations

import random
import sys
import time
from collections.abc import Callable


MOTION_STEPS: dict[str, tuple[tuple[int, int], ...]] = {
    "gentle_bounce": ((0, -5), (0, 0)),
    "double_bounce": ((0, -7), (0, 0), (0, -4), (0, 0)),
    "surprise_jump": ((0, -12), (0, 0)),
    "angry_shake": ((-8, 0), (8, 0), (-5, 0), (5, 0), (0, 0)),
    "manic_jitter": ((-5, -2), (4, 2), (-3, 1), (3, -1), (0, 0)),
    "dominant_shift": ((6, 0), (8, 0), (4, 0), (0, 0)),
}

# (motion name, probability); passive moods intentionally have no entry.
MOOD_MOTION_MAP: dict[str, tuple[str, float]] = {
    "happy": ("gentle_bounce", 0.20),
    "excited": ("double_bounce", 1.00),
    "surprised": ("surprise_jump", 1.00),
    "angry": ("angry_shake", 1.00),
    "manic": ("manic_jitter", 0.25),
    "paranoid": ("manic_jitter", 0.08),
    "dominant": ("dominant_shift", 0.65),
}


def _active_work_area(root) -> tuple[int, int, int, int]:
    """Return the current monitor's visible work area when the platform exposes it."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class RECT(ctypes.Structure):
                _fields_ = [(name, wintypes.LONG) for name in ("left", "top", "right", "bottom")]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", RECT),
                    ("rcWork", RECT),
                    ("dwFlags", wintypes.DWORD),
                ]

            hwnd = int(root.winfo_id())
            monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)
            info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
            if monitor and ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                work = info.rcWork
                return work.left, work.top, work.right, work.bottom
        except Exception:
            pass
    try:
        left = int(root.winfo_vrootx())
        top = int(root.winfo_vrooty())
        return left, top, left + int(root.winfo_vrootwidth()), top + int(root.winfo_vrootheight())
    except Exception:
        return 0, 0, int(root.winfo_screenwidth()), int(root.winfo_screenheight())


class MoodMotionController:
    """Play at most one bounded motion while respecting all geometry owners."""

    def __init__(
        self,
        root,
        *,
        enabled: bool = True,
        reduced_motion: bool = False,
        cooldown_seconds: float = 4.0,
        is_dragging: Callable[[], bool] = lambda: False,
        is_closing: Callable[[], bool] = lambda: False,
        is_minimized: Callable[[], bool] = lambda: False,
        geometry_busy: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.monotonic,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        self.root = root
        self.enabled = bool(enabled)
        self.reduced_motion = bool(reduced_motion)
        self.cooldown_seconds = max(1.0, min(float(cooldown_seconds), 60.0))
        self._is_dragging = is_dragging
        self._is_closing = is_closing
        self._is_minimized = is_minimized
        self._geometry_busy = geometry_busy
        self._clock = clock
        self._random = random_value
        self._job_ids: set = set()
        self._active = False
        self._origin: tuple[int, int] | None = None
        self._last_played = float("-inf")

    @property
    def active(self) -> bool:
        return self._active

    @property
    def job_ids(self) -> frozenset:
        return frozenset(self._job_ids)

    def _blocked(self) -> bool:
        try:
            return any((
                self._is_dragging(), self._is_closing(), self._is_minimized(),
                self._geometry_busy(),
            ))
        except Exception:
            return True

    def play_mood(self, mood: str | None) -> bool:
        entry = MOOD_MOTION_MAP.get(str(mood or "").strip().lower())
        if not entry:
            return False
        motion, chance = entry
        if self._random() > chance:
            return False
        return self.play_motion(motion)

    def play_motion(self, name: str) -> bool:
        steps = MOTION_STEPS.get(str(name or "").strip().lower())
        now = self._clock()
        if (
            steps is None or not self.enabled or self.reduced_motion or self._active
            or self._blocked() or now - self._last_played < self.cooldown_seconds
        ):
            return False
        try:
            self.root.update_idletasks()
            self._origin = (int(self.root.winfo_x()), int(self.root.winfo_y()))
        except Exception:
            return False
        self._active = True
        self._last_played = now
        for index, offset in enumerate(steps):
            self._schedule(50 * (index + 1), lambda o=offset: self._apply_offset(o))
        self._schedule(50 * (len(steps) + 1), self._finish)
        return True

    def _schedule(self, delay_ms: int, callback: Callable[[], None]) -> None:
        holder: list = [None]

        def _run() -> None:
            self._job_ids.discard(holder[0])
            callback()

        holder[0] = self.root.after(delay_ms, _run)
        self._job_ids.add(holder[0])

    def _apply_offset(self, offset: tuple[int, int]) -> None:
        if not self._active or self._origin is None:
            return
        if self._blocked():
            self.cancel_motion(restore=True)
            return
        try:
            ox, oy = self._origin
            dx, dy = offset
            width = max(1, int(self.root.winfo_width()))
            height = max(1, int(self.root.winfo_height()))
            left, top, right, bottom = _active_work_area(self.root)
            x = max(left, min(ox + dx, right - width))
            y = max(top, min(oy + dy, bottom - height))
            self.root.geometry(f"+{x}+{y}")
        except Exception:
            self.cancel_motion(restore=True)

    def _finish(self) -> None:
        if self._origin is not None:
            try:
                self.root.geometry(f"+{self._origin[0]}+{self._origin[1]}")
            except Exception:
                pass
        self._origin = None
        self._active = False

    def cancel_motion(self, *, restore: bool = True) -> None:
        for job_id in tuple(self._job_ids):
            try:
                self.root.after_cancel(job_id)
            except Exception:
                pass
        self._job_ids.clear()
        if restore and self._origin is not None:
            try:
                self.root.geometry(f"+{self._origin[0]}+{self._origin[1]}")
            except Exception:
                pass
        self._origin = None
        self._active = False


__all__ = ["MOOD_MOTION_MAP", "MOTION_STEPS", "MoodMotionController"]
