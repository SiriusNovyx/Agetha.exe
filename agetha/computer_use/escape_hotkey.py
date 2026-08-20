"""Session-scoped Windows Escape hotkey for Computer Use cancellation."""

from __future__ import annotations

import sys
import threading
from typing import Callable

from agetha.utils import logger


_HOTKEY_ID = 0xA637
_WM_HOTKEY = 0x0312
_PM_REMOVE = 0x0001
_MOD_NOREPEAT = 0x4000
_VK_ESCAPE = 0x1B


def _run_windows_hotkey(
    on_escape: Callable[[], None],
    stop_event: threading.Event,
    ready: threading.Event,
    registered: list[bool],
) -> None:
    """Own RegisterHotKey and its message queue on one daemon thread."""

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        message = wintypes.MSG()
        # Materialize this thread's message queue before registration.
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        ok = bool(user32.RegisterHotKey(None, _HOTKEY_ID, _MOD_NOREPEAT, _VK_ESCAPE))
        registered[0] = ok
        ready.set()
        if not ok:
            return
        while not stop_event.is_set():
            while user32.PeekMessageW(
                ctypes.byref(message),
                None,
                0,
                0,
                _PM_REMOVE,
            ):
                if int(message.message) == _WM_HOTKEY and int(message.wParam) == _HOTKEY_ID:
                    try:
                        on_escape()
                    except Exception as exc:
                        logger.warning(
                            "Computer Use Escape callback failed safely: %s",
                            type(exc).__name__,
                        )
            stop_event.wait(0.025)
    except Exception as exc:
        logger.warning(
            "Computer Use Escape hotkey unavailable: %s",
            type(exc).__name__,
        )
        ready.set()
    finally:
        if registered[0]:
            try:
                import ctypes

                ctypes.windll.user32.UnregisterHotKey(None, _HOTKEY_ID)
            except Exception:
                pass


class SessionEscapeHotkey:
    """Register Escape only while one explicit Computer Use request is active."""

    def __init__(
        self,
        on_escape: Callable[[], None],
        *,
        platform_name: str | None = None,
        runner: Callable[
            [Callable[[], None], threading.Event, threading.Event, list[bool]],
            None,
        ] = _run_windows_hotkey,
    ) -> None:
        self._on_escape = on_escape
        self._platform = str(platform_name or sys.platform).casefold()
        self._runner = runner
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._registered = [False]
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> bool:
        if self._platform != "win32":
            return False
        with self._lock:
            if self._thread is not None:
                return bool(self._registered[0])
            worker = threading.Thread(
                target=self._runner,
                args=(self._on_escape, self._stop, self._ready, self._registered),
                name="agetha-computer-use-escape",
                daemon=True,
            )
            self._thread = worker
            worker.start()
        self._ready.wait(0.75)
        return bool(self._ready.is_set() and self._registered[0])

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            worker = self._thread
            self._thread = None
        if worker is not None and worker is not threading.current_thread():
            worker.join(0.3)

    @property
    def registered(self) -> bool:
        return bool(self._registered[0] and not self._stop.is_set())


__all__ = ["SessionEscapeHotkey"]
