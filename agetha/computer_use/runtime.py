"""Runtime adapters for the deterministic Computer Use package.

The Computer Use core deliberately owns no platform effects.  This module is
the narrow bridge to the existing process-awareness and screen-reader
facilities.  Every effect remains injected, every capture is tied to one
strict process/window identity, and importing this module performs no screen
capture, input, or optional dependency import.
"""

from __future__ import annotations

import ctypes
import importlib
import math
import ntpath
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Mapping, MutableMapping, Protocol, runtime_checkable

from agetha.platform.process_awareness import (
    ProcessContextMode,
    ProcessIdentity as AwarenessProcessIdentity,
    RunningApplication,
)

from .executor import ExecutorDependencies
from .models import (
    MAX_CONTROLS,
    LiveTargetState,
    ProcessIdentity as ComputerProcessIdentity,
    Rect,
    WindowIdentity,
    clamp_confidence,
    safe_text,
)
from .observer import AtomicScreenSnapshot, RawControl


_WINDOWS_VIRTUAL_SCREEN_X = 76
_WINDOWS_VIRTUAL_SCREEN_Y = 77
_WINDOWS_VIRTUAL_SCREEN_WIDTH = 78
_WINDOWS_VIRTUAL_SCREEN_HEIGHT = 79


class ComputerUseRuntimeUnavailable(RuntimeError):
    """The platform cannot provide the required target-locking guarantees."""


@runtime_checkable
class ProcessAwarenessAdapter(Protocol):
    """The process-awareness surface required by this bridge."""

    @property
    def mode(self) -> ProcessContextMode: ...

    def validate_identity(
        self,
        expected: AwarenessProcessIdentity,
        *,
        strict: bool = True,
    ) -> bool: ...


@runtime_checkable
class ScreenReaderAdapter(Protocol):
    """The existing ScreenReader members used for an atomic locked capture."""

    _capture_lock: object
    last_capture_metadata: object | None
    last_word_positions: list[dict]

    def preserve_external_target(self) -> dict | None: ...

    def capture_text(
        self,
        max_chars: int = 3000,
        focused_only: bool = True,
        *,
        force_refresh: bool = False,
    ) -> str: ...

    def _foreground_info(self) -> dict | None: ...

    def _resolve_capture_target(self, target: dict | None) -> dict | None: ...


def _platform_key(value: str | None) -> str:
    current = str(value or sys.platform).strip().casefold()
    if current.startswith("win"):
        return "windows"
    if current.startswith("linux"):
        return "linux"
    return current or "unknown"


def _is_wayland(environment: Mapping[str, str]) -> bool:
    return bool(environment.get("WAYLAND_DISPLAY")) or (
        str(environment.get("XDG_SESSION_TYPE", "")).casefold() == "wayland"
    )


def runtime_platform_status(
    *,
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Return a capability status without probing the display or importing input."""

    platform_key = _platform_key(platform_name)
    env = os.environ if environment is None else environment
    if platform_key == "windows":
        return "available_windows"
    if platform_key == "linux":
        if _is_wayland(env):
            return "unavailable_wayland"
        if not env.get("DISPLAY"):
            return "unavailable_x11_display"
        return "available_xorg"
    return "unavailable_platform"


def process_identity_to_computer_use(
    identity: AwarenessProcessIdentity,
) -> ComputerProcessIdentity:
    """Convert a process-awareness identity without widening its information."""

    if not isinstance(identity, AwarenessProcessIdentity):
        raise TypeError("identity must be a process-awareness ProcessIdentity")
    return ComputerProcessIdentity(
        pid=identity.pid,
        name=identity.name,
        created_at=identity.created_at,
    )


def running_application_to_window_identity(
    application: RunningApplication,
) -> WindowIdentity | None:
    """Convert an interactive application only when HWND and bounds are known."""

    if not isinstance(application, RunningApplication):
        raise TypeError("application must be a RunningApplication")
    if application.window_handle is None or application.bounds is None:
        return None
    try:
        bounds = Rect(*application.bounds)
        return WindowIdentity(
            hwnd=application.window_handle,
            process=process_identity_to_computer_use(application.identity),
            bounds=bounds,
            title=application.window_title,
        )
    except (TypeError, ValueError, OverflowError):
        return None


# Short aliases make wiring code readable while the longer names remain
# unambiguous at integration boundaries.
to_computer_use_process_identity = process_identity_to_computer_use
to_window_identity = running_application_to_window_identity


def _to_awareness_identity(identity: ComputerProcessIdentity) -> AwarenessProcessIdentity:
    return AwarenessProcessIdentity(
        pid=identity.pid,
        name=identity.name,
        created_at=identity.created_at,
    )


def _basename(value: object) -> str:
    normalized = str(value or "").replace("/", "\\")
    return ntpath.basename(normalized).strip().casefold()


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result


def _rect_from_info(info: Mapping[str, object] | None) -> Rect | None:
    if not isinstance(info, Mapping):
        return None
    if info.get("minimized") is True or info.get("mapped") is False:
        return None
    values = tuple(
        _safe_int(info.get(name)) for name in ("left", "top", "width", "height")
    )
    if any(value is None for value in values):
        return None
    try:
        return Rect(*values)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None


def _info_identity_matches(
    info: Mapping[str, object] | None,
    expected: WindowIdentity,
) -> bool:
    if not isinstance(info, Mapping):
        return False
    hwnd = _safe_int(info.get("hwnd"))
    pid = _safe_int(info.get("process_id"))
    name = _basename(info.get("process_name"))
    return (
        hwnd == expected.hwnd
        and pid == expected.process.pid
        and bool(name)
        and name == _basename(expected.process.name)
    )


def _info_matches_target(
    info: Mapping[str, object] | None,
    expected: WindowIdentity,
    *,
    require_bounds: bool = True,
) -> bool:
    if not _info_identity_matches(info, expected):
        return False
    bounds = _rect_from_info(info)
    return bounds is not None and (
        not require_bounds or bounds == expected.bounds
    )


def _capture_target_dict(expected: WindowIdentity) -> dict[str, object]:
    return {
        "left": expected.bounds.left,
        "top": expected.bounds.top,
        "width": expected.bounds.width,
        "height": expected.bounds.height,
        "title": expected.title,
        "hwnd": expected.hwnd,
        "process_name": expected.process.name,
        "process_id": expected.process.pid,
        "mapped": True,
        "minimized": False,
    }


def _frame_matches_target(frame: object, expected: WindowIdentity) -> bool:
    if frame is None:
        return False
    if str(getattr(frame, "scope", "")) != "focused_window":
        return False
    if _safe_int(getattr(frame, "hwnd", None)) != expected.hwnd:
        return False
    if _safe_int(getattr(frame, "process_id", None)) != expected.process.pid:
        return False
    if _basename(getattr(frame, "process_name", "")) != _basename(
        expected.process.name
    ):
        return False
    image = getattr(frame, "image", None)
    size = getattr(image, "size", None)
    if not isinstance(size, tuple) or len(size) != 2:
        return False
    width = _safe_int(size[0])
    height = _safe_int(size[1])
    left = _safe_int(getattr(frame, "left", None))
    top = _safe_int(getattr(frame, "top", None))
    return (
        left == expected.bounds.left
        and top == expected.bounds.top
        and width == expected.bounds.width
        and height == expected.bounds.height
    )


def ocr_word_to_raw_control(
    word: Mapping[str, object],
    *,
    target_bounds: Rect | None = None,
    screen_bounds: Rect | None = None,
) -> RawControl | None:
    """Convert one ScreenReader word to a bounded desktop-space OCR control."""

    if not isinstance(word, Mapping):
        return None
    x = _safe_int(word.get("screen_x"))
    y = _safe_int(word.get("screen_y"))
    width = _safe_int(word.get("w"))
    height = _safe_int(word.get("h"))
    if None in {x, y, width, height}:
        return None
    try:
        bounds = Rect(x, y, width, height)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    if target_bounds is not None and not target_bounds.contains_rect(bounds):
        return None
    if screen_bounds is not None and not screen_bounds.contains_rect(bounds):
        return None

    label = safe_text(word.get("text"))
    if not label:
        return None
    raw_confidence = word.get("conf", 0.0)
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError, OverflowError):
        confidence = 0.0
    if not math.isfinite(confidence):
        confidence = 0.0
    if confidence > 1.0:
        confidence /= 100.0
    return RawControl(
        label=label,
        bounds=bounds,
        confidence=clamp_confidence(confidence),
    )


class LockedTargetValidator:
    """Revalidate process creation time, HWND, foreground, and exact bounds."""

    def __init__(
        self,
        process_awareness: ProcessAwarenessAdapter,
        screen_reader: ScreenReaderAdapter,
        *,
        platform_name: str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._process_awareness = process_awareness
        self._screen_reader = screen_reader
        self._platform_name = platform_name
        self._environment = dict(os.environ if environment is None else environment)

    @property
    def status(self) -> str:
        status = runtime_platform_status(
            platform_name=self._platform_name,
            environment=self._environment,
        )
        if status.startswith("unavailable"):
            return status
        if _awareness_is_off(self._process_awareness):
            return "unavailable_process_awareness_off"
        if not _has_capture_lock(self._screen_reader):
            return "unavailable_capture_lock"
        return status

    @property
    def available(self) -> bool:
        return self.status.startswith("available")

    def __call__(
        self,
        expected: WindowIdentity,
        require_foreground: bool,
    ) -> LiveTargetState:
        return self.validate(expected, require_foreground=require_foreground)

    def validate(
        self,
        expected: WindowIdentity,
        require_foreground: bool = True,
    ) -> LiveTargetState:
        unavailable = LiveTargetState(None, False, False, False)
        if not isinstance(expected, WindowIdentity) or not self.available:
            return unavailable
        try:
            awareness_identity = _to_awareness_identity(expected.process)
        except (TypeError, ValueError, OverflowError):
            return unavailable
        if not _strict_identity_current(self._process_awareness, awareness_identity):
            return unavailable

        lock = getattr(self._screen_reader, "_capture_lock", None)
        try:
            with lock:  # type: ignore[union-attr]
                resolved = _safe_reader_call(
                    self._screen_reader,
                    "_resolve_capture_target",
                    _capture_target_dict(expected),
                )
                foreground_info = _safe_reader_call(
                    self._screen_reader,
                    "_foreground_info",
                )
                if resolved is None and _info_identity_matches(foreground_info, expected):
                    resolved = foreground_info

                is_window = _info_identity_matches(resolved, expected)
                live_bounds = _rect_from_info(resolved)
                foreground = _info_identity_matches(foreground_info, expected)
                live_target = None
                if is_window and live_bounds is not None:
                    live_target = WindowIdentity(
                        hwnd=expected.hwnd,
                        process=expected.process,
                        bounds=live_bounds,
                        title=safe_text(
                            resolved.get("title", "")
                            if isinstance(resolved, Mapping)
                            else ""
                        ),
                    )
                identity_still_current = _strict_identity_current(
                    self._process_awareness,
                    awareness_identity,
                )
                authorized = bool(
                    identity_still_current
                    and live_target is not None
                    and expected.matches(live_target, require_same_bounds=True)
                )
                if require_foreground and not foreground:
                    authorized = False
                return LiveTargetState(
                    target=live_target,
                    is_window=bool(is_window and live_bounds is not None),
                    foreground=foreground,
                    authorized=authorized,
                )
        except Exception:
            return unavailable


def _has_capture_lock(screen_reader: object) -> bool:
    lock = getattr(screen_reader, "_capture_lock", None)
    return hasattr(lock, "__enter__") and hasattr(lock, "__exit__")


def _awareness_is_off(process_awareness: object) -> bool:
    mode = getattr(process_awareness, "mode", None)
    value = getattr(mode, "value", mode)
    return str(value or "").casefold() == ProcessContextMode.OFF.value


def _strict_identity_current(
    process_awareness: ProcessAwarenessAdapter,
    identity: AwarenessProcessIdentity,
) -> bool:
    try:
        return bool(process_awareness.validate_identity(identity, strict=True))
    except Exception:
        return False


def _safe_reader_call(
    screen_reader: object,
    name: str,
    *args: object,
) -> object | None:
    callback = getattr(screen_reader, name, None)
    if not callable(callback):
        return None
    try:
        return callback(*args)
    except Exception:
        return None


class LazyPyAutoGUIInput:
    """Lazy, fail-safe-preserving input callbacks with no text-writing method."""

    def __init__(
        self,
        *,
        importer: Callable[[str], object] = importlib.import_module,
        platform_name: str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._importer = importer
        self._platform_name = platform_name
        self._environment = dict(os.environ if environment is None else environment)
        self._lock = threading.Lock()
        self._module: object | None = None
        self._load_attempted = False
        self._status = "not_loaded"

    @property
    def loaded(self) -> bool:
        with self._lock:
            return self._module is not None

    @property
    def status(self) -> str:
        platform_status = runtime_platform_status(
            platform_name=self._platform_name,
            environment=self._environment,
        )
        if platform_status.startswith("unavailable"):
            return platform_status
        with self._lock:
            return self._status

    def _get_module(self) -> object | None:
        platform_status = runtime_platform_status(
            platform_name=self._platform_name,
            environment=self._environment,
        )
        if platform_status.startswith("unavailable"):
            return None
        with self._lock:
            if self._load_attempted:
                module = self._module
            else:
                self._load_attempted = True
                try:
                    module = self._importer("pyautogui")
                except Exception:
                    module = None
                if module is None:
                    self._status = "unavailable_pyautogui"
                elif getattr(module, "FAILSAFE", None) is not True:
                    # Never turn FAILSAFE off or silently turn it back on.  A
                    # caller that disabled it must explicitly repair its state.
                    self._status = "unavailable_failsafe_disabled"
                    module = None
                else:
                    self._status = "available"
                self._module = module
            return module

    def _invoke(self, name: str, *args: object, **kwargs: object) -> bool:
        module = self._get_module()
        if module is None or getattr(module, "FAILSAFE", None) is not True:
            return False
        callback = getattr(module, name, None)
        if not callable(callback):
            return False
        try:
            callback(*args, **kwargs)
            return True
        except Exception:
            return False

    def move_pointer(self, x: int, y: int) -> bool:
        return self._invoke("moveTo", int(x), int(y))

    def click(self, x: int, y: int) -> bool:
        return self._invoke("click", x=int(x), y=int(y))

    def double_click(self, x: int, y: int) -> bool:
        return self._invoke("doubleClick", x=int(x), y=int(y))

    def scroll(self, amount: int, x: int | None, y: int | None) -> bool:
        if (x is None) != (y is None):
            return False
        if x is None:
            return self._invoke("scroll", int(amount))
        return self._invoke("scroll", int(amount), x=int(x), y=int(y))

    def keypress(self, key: str) -> bool:
        return self._invoke("press", str(key))

    def hotkey(self, keys: tuple[str, ...]) -> bool:
        if not keys:
            return False
        return self._invoke("hotkey", *tuple(str(item) for item in keys))

    def screen_bounds(self) -> Rect | None:
        if _platform_key(self._platform_name) == "windows":
            native = _windows_virtual_screen_bounds()
            if native is not None:
                return native
        module = self._get_module()
        if module is None:
            return None
        callback = getattr(module, "size", None)
        if not callable(callback):
            return None
        try:
            size = callback()
            width = _safe_int(getattr(size, "width", size[0]))
            height = _safe_int(getattr(size, "height", size[1]))
            if width is None or height is None:
                return None
            return Rect(0, 0, width, height)
        except (IndexError, TypeError, ValueError, OverflowError):
            return None

    def cursor_position(self) -> tuple[int, int] | None:
        if _platform_key(self._platform_name) == "windows":
            native = _windows_cursor_position()
            if native is not None:
                return native
        module = self._get_module()
        if module is None:
            return None
        callback = getattr(module, "position", None)
        if not callable(callback):
            return None
        try:
            point = callback()
            x = _safe_int(getattr(point, "x", point[0]))
            y = _safe_int(getattr(point, "y", point[1]))
            if x is None or y is None:
                return None
            return x, y
        except (IndexError, TypeError, ValueError, OverflowError):
            return None


def _windows_virtual_screen_bounds() -> Rect | None:
    if _platform_key(sys.platform) != "windows":
        return None
    try:
        user32 = ctypes.windll.user32
        values = (
            int(user32.GetSystemMetrics(_WINDOWS_VIRTUAL_SCREEN_X)),
            int(user32.GetSystemMetrics(_WINDOWS_VIRTUAL_SCREEN_Y)),
            int(user32.GetSystemMetrics(_WINDOWS_VIRTUAL_SCREEN_WIDTH)),
            int(user32.GetSystemMetrics(_WINDOWS_VIRTUAL_SCREEN_HEIGHT)),
        )
        return Rect(*values)
    except (AttributeError, OSError, TypeError, ValueError, OverflowError):
        return None


def _windows_cursor_position() -> tuple[int, int] | None:
    if _platform_key(sys.platform) != "windows":
        return None

    class _Point(ctypes.Structure):
        _fields_ = (("x", ctypes.c_long), ("y", ctypes.c_long))

    try:
        point = _Point()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
        return int(point.x), int(point.y)
    except (AttributeError, OSError, TypeError, ValueError, OverflowError):
        return None


class LockedTargetScreenSource:
    """Capture fresh OCR state only while one exact foreground target is locked."""

    def __init__(
        self,
        screen_reader: ScreenReaderAdapter,
        process_awareness: ProcessAwarenessAdapter,
        *,
        validator: LockedTargetValidator | None = None,
        input_adapter: LazyPyAutoGUIInput | None = None,
        screen_bounds: Callable[[], Rect | tuple[int, int, int, int] | None]
        | None = None,
        cursor_position: Callable[[], tuple[int, int] | None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        max_controls: int = MAX_CONTROLS,
        max_ocr_chars: int = 3000,
        platform_name: str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        if max_controls <= 0 or max_controls > MAX_CONTROLS:
            raise ValueError(f"max_controls must be 1..{MAX_CONTROLS}")
        if max_ocr_chars <= 0:
            raise ValueError("max_ocr_chars must be positive")
        self._screen_reader = screen_reader
        self._process_awareness = process_awareness
        self._platform_name = platform_name
        self._environment = dict(os.environ if environment is None else environment)
        self._validator = validator or LockedTargetValidator(
            process_awareness,
            screen_reader,
            platform_name=platform_name,
            environment=self._environment,
        )
        self._input = input_adapter or LazyPyAutoGUIInput(
            platform_name=platform_name,
            environment=self._environment,
        )
        self._screen_bounds = screen_bounds or self._input.screen_bounds
        self._cursor_position = cursor_position or self._input.cursor_position
        self._monotonic = monotonic
        self._max_controls = max_controls
        self._max_ocr_chars = min(int(max_ocr_chars), 20_000)

    @property
    def status(self) -> str:
        if not self._validator.available:
            return self._validator.status
        capture_supported = getattr(
            self._screen_reader,
            "automatic_capture_supported",
            getattr(self._screen_reader, "_automatic_capture_available", True),
        )
        if capture_supported is False:
            return "unavailable_capture"
        if not callable(getattr(self._screen_reader, "capture_text", None)):
            return "unavailable_capture"
        return self._validator.status

    @property
    def available(self) -> bool:
        return self.status.startswith("available")

    def capture(self, expected_target: WindowIdentity | None) -> AtomicScreenSnapshot:
        if not self.available:
            raise ComputerUseRuntimeUnavailable(self.status)
        screen_bounds = _coerce_screen_bounds(_safe_callback(self._screen_bounds))
        cursor = _coerce_cursor(_safe_callback(self._cursor_position))
        captured_at = _safe_monotonic(self._monotonic)
        if screen_bounds is None or cursor is None:
            raise ComputerUseRuntimeUnavailable("unavailable_desktop_geometry")
        if expected_target is None or not screen_bounds.contains_rect(
            expected_target.bounds
        ):
            return _empty_atomic_snapshot(screen_bounds, cursor, captured_at)

        lock = getattr(self._screen_reader, "_capture_lock", None)
        try:
            with lock:  # type: ignore[union-attr]
                before_state = self._validator.validate(
                    expected_target,
                    require_foreground=True,
                )
                if not _valid_live_state(before_state, expected_target, foreground=True):
                    return _empty_atomic_snapshot(screen_bounds, cursor, captured_at)

                preserved_before = _safe_reader_call(
                    self._screen_reader,
                    "preserve_external_target",
                )
                foreground_before = _safe_reader_call(
                    self._screen_reader,
                    "_foreground_info",
                )
                if preserved_before is not None and not _info_matches_target(
                    preserved_before,
                    expected_target,
                ):
                    return _empty_atomic_snapshot(screen_bounds, cursor, captured_at)
                if not _info_matches_target(foreground_before, expected_target):
                    return _empty_atomic_snapshot(screen_bounds, cursor, captured_at)

                previous_metadata = getattr(
                    self._screen_reader,
                    "last_capture_metadata",
                    None,
                )
                previous_words = getattr(
                    self._screen_reader,
                    "last_word_positions",
                    None,
                )
                capture_callback = getattr(self._screen_reader, "capture_text", None)
                if not callable(capture_callback):
                    return _empty_atomic_snapshot(screen_bounds, cursor, captured_at)
                try:
                    # OCR text is deliberately ignored.  The Computer Use
                    # observer consumes only sanitized, bounded word controls.
                    capture_callback(
                        max_chars=self._max_ocr_chars,
                        focused_only=True,
                        force_refresh=True,
                    )
                except Exception:
                    return _empty_atomic_snapshot(screen_bounds, cursor, captured_at)

                metadata = getattr(self._screen_reader, "last_capture_metadata", None)
                published_words = getattr(
                    self._screen_reader,
                    "last_word_positions",
                    None,
                )
                # ScreenReader's cached/failed paths do not republish both
                # values.  Reject them rather than mixing an old OCR map with
                # a newly validated window.
                if metadata is previous_metadata or published_words is previous_words:
                    return _empty_atomic_snapshot(screen_bounds, cursor, captured_at)
                if not _frame_matches_target(metadata, expected_target):
                    return _empty_atomic_snapshot(screen_bounds, cursor, captured_at)

                preserved_after = _safe_reader_call(
                    self._screen_reader,
                    "preserve_external_target",
                )
                foreground_after = _safe_reader_call(
                    self._screen_reader,
                    "_foreground_info",
                )
                if preserved_after is not None and not _info_matches_target(
                    preserved_after,
                    expected_target,
                ):
                    return _empty_atomic_snapshot(screen_bounds, cursor, captured_at)
                if not _info_matches_target(foreground_after, expected_target):
                    return _empty_atomic_snapshot(screen_bounds, cursor, captured_at)

                after_state = self._validator.validate(
                    expected_target,
                    require_foreground=True,
                )
                if not _valid_live_state(after_state, expected_target, foreground=True):
                    return _empty_atomic_snapshot(screen_bounds, cursor, captured_at)

                controls = _controls_from_words(
                    published_words,
                    target_bounds=expected_target.bounds,
                    screen_bounds=screen_bounds,
                    limit=self._max_controls,
                )
                title = safe_text(getattr(metadata, "title", ""))
                target = WindowIdentity(
                    hwnd=expected_target.hwnd,
                    process=expected_target.process,
                    bounds=expected_target.bounds,
                    title=title,
                )
                return AtomicScreenSnapshot(
                    target=target,
                    foreground=True,
                    screen_bounds=screen_bounds,
                    cursor=cursor,
                    ocr_controls=controls,
                    process_alive=True,
                    captured_at=captured_at,
                )
        except Exception:
            return _empty_atomic_snapshot(screen_bounds, cursor, captured_at)


# Descriptive alias for callers that prefer the protocol name in wiring code.
ScreenReaderObservationSource = LockedTargetScreenSource


def _safe_callback(callback: Callable[[], object]) -> object | None:
    try:
        return callback()
    except Exception:
        return None


def _safe_monotonic(callback: Callable[[], float]) -> float:
    try:
        value = float(callback())
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return value if math.isfinite(value) and value >= 0.0 else 0.0


def _coerce_screen_bounds(value: object) -> Rect | None:
    if isinstance(value, Rect):
        return value
    if isinstance(value, tuple) and len(value) == 4:
        try:
            return Rect(*(int(item) for item in value))
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def _coerce_cursor(value: object) -> tuple[int, int] | None:
    if not isinstance(value, tuple) or len(value) != 2:
        return None
    x = _safe_int(value[0])
    y = _safe_int(value[1])
    return None if x is None or y is None else (x, y)


def _empty_atomic_snapshot(
    screen_bounds: Rect,
    cursor: tuple[int, int],
    captured_at: float,
) -> AtomicScreenSnapshot:
    return AtomicScreenSnapshot(
        target=None,
        foreground=False,
        screen_bounds=screen_bounds,
        cursor=cursor,
        ocr_controls=(),
        process_alive=False,
        captured_at=captured_at,
    )


def _valid_live_state(
    state: LiveTargetState,
    expected: WindowIdentity,
    *,
    foreground: bool,
) -> bool:
    return bool(
        state.is_window
        and state.authorized
        and state.target is not None
        and expected.matches(state.target, require_same_bounds=True)
        and (not foreground or state.foreground)
    )


def _controls_from_words(
    values: object,
    *,
    target_bounds: Rect,
    screen_bounds: Rect,
    limit: int,
) -> tuple[RawControl, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    output: list[RawControl] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        control = ocr_word_to_raw_control(
            value,
            target_bounds=target_bounds,
            screen_bounds=screen_bounds,
        )
        if control is not None:
            output.append(control)
        if len(output) >= limit:
            break
    return tuple(output)


class ExactWindowFocus:
    """Focus only the exact validated HWND, then verify it became foreground."""

    def __init__(
        self,
        validator: LockedTargetValidator,
        *,
        native_focus: Callable[[int], bool] | None = None,
        platform_name: str | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._validator = validator
        self._platform_name = platform_name
        self._environment = dict(os.environ if environment is None else environment)
        self._native_focus = native_focus or self._windows_focus

    @property
    def available(self) -> bool:
        return (
            runtime_platform_status(
                platform_name=self._platform_name,
                environment=self._environment,
            ).startswith("available")
            and _platform_key(self._platform_name) == "windows"
        )

    def __call__(self, expected: WindowIdentity) -> bool:
        if not self.available or not isinstance(expected, WindowIdentity):
            return False
        before = self._validator.validate(expected, require_foreground=False)
        if not _valid_live_state(before, expected, foreground=False):
            return False
        try:
            focused = bool(self._native_focus(expected.hwnd))
        except Exception:
            return False
        if not focused:
            return False
        after = self._validator.validate(expected, require_foreground=True)
        return _valid_live_state(after, expected, foreground=True)

    @staticmethod
    def _windows_focus(hwnd: int) -> bool:
        if _platform_key(sys.platform) != "windows":
            return False
        try:
            user32 = ctypes.windll.user32
            handle = ctypes.c_void_p(int(hwnd))
            if not bool(user32.IsWindow(handle)):
                return False
            return bool(user32.SetForegroundWindow(handle))
        except (AttributeError, OSError, TypeError, ValueError, OverflowError):
            return False


def build_executor_dependencies(
    process_awareness: ProcessAwarenessAdapter,
    screen_reader: ScreenReaderAdapter,
    *,
    guarded_type: Callable[[str, WindowIdentity, threading.Event], bool],
    validator: LockedTargetValidator | None = None,
    input_adapter: LazyPyAutoGUIInput | None = None,
    native_focus: Callable[[int], bool] | None = None,
    wait: Callable[[float, threading.Event], bool] | None = None,
    is_shutdown: Callable[[], bool] = lambda: False,
    monotonic: Callable[[], float] = time.monotonic,
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> ExecutorDependencies:
    """Wire executor effects without providing any unguarded typing fallback."""

    if not callable(guarded_type):
        raise TypeError("guarded_type must be injected by the caller")
    env: MutableMapping[str, str] = dict(
        os.environ if environment is None else environment
    )
    selected_validator = validator or LockedTargetValidator(
        process_awareness,
        screen_reader,
        platform_name=platform_name,
        environment=env,
    )
    inputs = input_adapter or LazyPyAutoGUIInput(
        platform_name=platform_name,
        environment=env,
    )
    focus = ExactWindowFocus(
        selected_validator,
        native_focus=native_focus,
        platform_name=platform_name,
        environment=env,
    )
    waiter = wait or (lambda seconds, event: not event.wait(seconds))
    return ExecutorDependencies(
        validate_target=selected_validator,
        move_pointer=inputs.move_pointer,
        click=inputs.click,
        double_click=inputs.double_click,
        scroll=inputs.scroll,
        keypress=inputs.keypress,
        hotkey=inputs.hotkey,
        focus_window=focus,
        guarded_type=guarded_type,
        wait=waiter,
        is_shutdown=is_shutdown,
        monotonic=monotonic,
    )


__all__ = [
    "ComputerUseRuntimeUnavailable",
    "ExactWindowFocus",
    "LazyPyAutoGUIInput",
    "LockedTargetScreenSource",
    "LockedTargetValidator",
    "ProcessAwarenessAdapter",
    "ScreenReaderAdapter",
    "ScreenReaderObservationSource",
    "build_executor_dependencies",
    "ocr_word_to_raw_control",
    "process_identity_to_computer_use",
    "running_application_to_window_identity",
    "runtime_platform_status",
    "to_computer_use_process_identity",
    "to_window_identity",
]
