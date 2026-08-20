"""Privacy-bounded foreground, visible-application, and process awareness.

The module deliberately separates local process inventory from the small
application snapshot that may be formatted for an AI provider.  Discovery is
read-only: it does not launch applications, manipulate windows, publish an
``Observation`` directly, or authorize any command.

Windows uses the standard library's ``ctypes`` APIs and opportunistically uses
the already-required :mod:`psutil` package.  Linux window discovery is limited
to an X11 session with existing command-line tools.  Generic Wayland sessions
are reported honestly as unavailable for foreground/window enumeration.
"""

from __future__ import annotations

import ctypes
import importlib
import logging
import math
import ntpath
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

MAX_PROCESS_NAME_CHARS = 120
MAX_LOCAL_INVENTORY = 4096
MAX_PROVIDER_PROCESS_NAMES = 100
MAX_VISIBLE_APPS = 100
MAX_KNOWN_IDENTITIES = 4096

_AUTO = object()
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_WINDOWS_USER_PATH_RE = re.compile(
    r"(?i)(?:[A-Z]:\\|\\\\[^\\]+\\[^\\]+\\)Users\\[^\\/\s]+"
)
_POSIX_USER_PATH_RE = re.compile(r"(?i)/home/[^/\s]+")
_SENSITIVE_TITLE_RE = re.compile(
    r"(?i)\b(?:password|passphrase|recovery\s+code|private\s+key|secret\s+key|"
    r"authenticator|one[- ]?time\s+(?:password|code)|bank(?:ing)?|financial\s+account|"
    r"crypto\s+wallet|seed\s+phrase|credential|secure\s+vault)\b"
)
_SECRET_REDACTIONS = (
    (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
        "Bearer [REDACTED]",
    ),
    (
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{12,}|gsk_[A-Za-z0-9_-]{12,}|"
            r"gh[pousr]_[A-Za-z0-9]{12,}|AKIA[A-Z0-9]{16})\b"
        ),
        "[REDACTED API KEY]",
    ),
    (
        re.compile(
            r"(?i)\b(password|passwd|sessionid|session|auth_token|cookie|"
            r"api_key|apikey|client_secret|secret|token)\s*([:=])\s*[^\s,;]+"
        ),
        r"\1\2 [REDACTED]",
    ),
)

_BUILTIN_SENSITIVE_EXECUTABLES = frozenset({
    "1password", "1password.exe",
    "bitwarden", "bitwarden.exe",
    "dashlane", "dashlane.exe",
    "enpass", "enpass.exe",
    "keepass", "keepass.exe",
    "keepassxc", "keepassxc.exe",
    "keeper", "keeper.exe",
    "lastpass", "lastpass.exe",
    "nordpass", "nordpass.exe",
    "proton-pass", "proton-pass.exe", "protonpass", "protonpass.exe",
    "password-manager", "password-manager.exe",
    "passwordmanager", "passwordmanager.exe",
    "roboform", "roboform.exe",
})

_FRIENDLY_APPLICATION_NAMES = {
    "code": "Visual Studio Code",
    "code.exe": "Visual Studio Code",
    "windowsterminal": "Windows Terminal",
    "windowsterminal.exe": "Windows Terminal",
    "devenv": "Visual Studio",
    "devenv.exe": "Visual Studio",
    "notepad": "Notepad",
    "notepad.exe": "Notepad",
    "spotify": "Spotify",
    "spotify.exe": "Spotify",
    "discord": "Discord",
    "discord.exe": "Discord",
}


class ProcessContextMode(str, Enum):
    """How much process state may be inspected locally."""

    OFF = "off"
    FOREGROUND_ONLY = "foreground_only"
    VISIBLE_APPS = "visible_apps"
    ALL_PROCESSES = "all_processes"


class ProcessTransitionKind(str, Enum):
    """Neutral local transition names suitable for Observation Bus adapters."""

    PROCESS_STARTED = "process_started"
    PROCESS_EXITED = "process_exited"
    FOREGROUND_APP_CHANGED = "foreground_app_changed"
    VISIBLE_APP_APPEARED = "visible_app_appeared"
    VISIBLE_APP_HIDDEN = "visible_app_hidden"


def _basename(value: object) -> str:
    raw = _CONTROL_RE.sub(" ", str(value or "")).strip().replace("/", "\\")
    name = ntpath.basename(raw).strip()
    name = " ".join(name.split())
    return name[:MAX_PROCESS_NAME_CHARS]


def _redact_sensitive_text(value: object) -> str:
    """Small dependency-free subset of the shared provider redaction policy."""

    text = str(value or "")
    for pattern, replacement in _SECRET_REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _normalized_name(value: object) -> str:
    return _basename(value).casefold()


def _name_aliases(value: object) -> frozenset[str]:
    name = _normalized_name(value)
    if not name:
        return frozenset()
    stem = name[:-4] if name.endswith(".exe") else name
    return frozenset((name, stem))


def _finite_monotonic(value: object) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("monotonic clock must return a finite number")
    return result


@dataclass(frozen=True)
class ProcessIdentity:
    """PID plus executable basename and creation time when it is available."""

    pid: int
    name: str
    created_at: float | None

    def __post_init__(self) -> None:
        pid = int(self.pid)
        if pid <= 0:
            raise ValueError("pid must be a positive integer")
        name = _basename(self.name)
        if not name:
            raise ValueError("process name must not be empty")
        created = self.created_at
        if created is not None:
            created = float(created)
            if not math.isfinite(created) or created < 0.0:
                raise ValueError("created_at must be a finite non-negative value")
            # psutil and GetProcessTimes can differ only in insignificant float
            # representation.  A fixed precision gives one stable identity key.
            created = round(created, 6)
        object.__setattr__(self, "pid", pid)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "created_at", created)

    @property
    def key(self) -> tuple[int, str, float | None]:
        return self.pid, self.name.casefold(), self.created_at


def identities_match(
    expected: ProcessIdentity,
    current: ProcessIdentity,
    *,
    strict: bool = False,
) -> bool:
    """Compare identities without ever treating PID alone as sufficient.

    In strict mode both creation times are required.  That mode is intended for
    effectful target locking.  Read-only awareness may fall back to PID plus
    basename only when one or both creation times cannot be queried.
    """

    if not isinstance(expected, ProcessIdentity) or not isinstance(current, ProcessIdentity):
        return False
    if expected.pid != current.pid or expected.name.casefold() != current.name.casefold():
        return False
    if strict and (expected.created_at is None or current.created_at is None):
        return False
    if expected.created_at is None or current.created_at is None:
        return not strict
    return expected.created_at == current.created_at


@dataclass(frozen=True)
class RunningApplication:
    """One locally discovered interactive application window."""

    identity: ProcessIdentity
    window_handle: int | None
    window_title: str
    visible: bool
    foreground: bool
    bounds: tuple[int, int, int, int] | None = None
    sensitive: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ProcessIdentity):
            raise TypeError("identity must be a ProcessIdentity")
        handle = None if self.window_handle is None else int(self.window_handle)
        if handle is not None and handle <= 0:
            handle = None
        title = _CONTROL_RE.sub(" ", str(self.window_title or ""))
        title = " ".join(title.split())[:512]
        bounds = self.bounds
        if bounds is not None:
            if len(bounds) != 4:
                raise ValueError("bounds must contain x, y, width, and height")
            bounds = tuple(int(part) for part in bounds)
            if bounds[2] <= 0 or bounds[3] <= 0:
                bounds = None
        object.__setattr__(self, "window_handle", handle)
        object.__setattr__(self, "window_title", title)
        object.__setattr__(self, "visible", bool(self.visible))
        object.__setattr__(self, "foreground", bool(self.foreground))
        object.__setattr__(self, "bounds", bounds)
        object.__setattr__(self, "sensitive", bool(self.sensitive))

    @property
    def key(self) -> tuple[int, str, float | None]:
        # A snapshot represents applications, not every document window.  The
        # full process identity therefore supplies the deduplication key.
        return self.identity.key


@dataclass(frozen=True)
class ProcessSnapshot:
    foreground: RunningApplication | None
    visible_apps: tuple[RunningApplication, ...]
    total_process_count: int | None
    captured_at_monotonic: float
    status: str = "available"

    def __post_init__(self) -> None:
        apps = tuple(self.visible_apps)
        if any(not isinstance(app, RunningApplication) for app in apps):
            raise TypeError("visible_apps must contain RunningApplication values")
        count = self.total_process_count
        if count is not None:
            count = max(0, int(count))
        status = _CONTROL_RE.sub(" ", str(self.status or "unknown"))
        status = " ".join(status.split())[:120] or "unknown"
        object.__setattr__(self, "visible_apps", apps)
        object.__setattr__(self, "total_process_count", count)
        object.__setattr__(
            self, "captured_at_monotonic", _finite_monotonic(self.captured_at_monotonic)
        )
        object.__setattr__(self, "status", status)


@dataclass(frozen=True)
class ProcessInventory:
    """Separate local-only background inventory.

    It is intentionally absent from :class:`ProcessSnapshot`, which is the
    object used by ordinary foreground/visible provider formatting.
    """

    processes: tuple[ProcessIdentity, ...]
    total_process_count: int | None
    captured_at_monotonic: float
    truncated: bool = False
    status: str = "available"

    def __post_init__(self) -> None:
        processes = tuple(self.processes)
        if any(not isinstance(item, ProcessIdentity) for item in processes):
            raise TypeError("processes must contain ProcessIdentity values")
        count = self.total_process_count
        if count is not None:
            count = max(0, int(count))
        object.__setattr__(self, "processes", processes)
        object.__setattr__(self, "total_process_count", count)
        object.__setattr__(
            self, "captured_at_monotonic", _finite_monotonic(self.captured_at_monotonic)
        )
        object.__setattr__(self, "truncated", bool(self.truncated))


@dataclass(frozen=True)
class ProcessTransition:
    """Privacy-minimized transition independent of Observation Bus types."""

    kind: ProcessTransitionKind
    summary: str
    identity: ProcessIdentity | None = None
    sensitive: bool = False

    def __post_init__(self) -> None:
        kind = self.kind
        if not isinstance(kind, ProcessTransitionKind):
            kind = ProcessTransitionKind(kind)
        summary = _CONTROL_RE.sub(" ", str(self.summary or ""))
        summary = " ".join(summary.split())[:160]
        if not summary:
            raise ValueError("transition summary must not be empty")
        sensitive = bool(self.sensitive)
        # A sensitive transition is deliberately coarse even for its publisher.
        identity = None if sensitive else self.identity
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "identity", identity)
        object.__setattr__(self, "sensitive", sensitive)


@runtime_checkable
class ProcessBackend(Protocol):
    """Injectable, read-only platform boundary."""

    @property
    def status(self) -> str: ...

    def foreground_application(self) -> RunningApplication | None: ...

    def visible_applications(self) -> Iterable[RunningApplication]: ...

    def identity_for_pid(self, pid: int) -> ProcessIdentity | None: ...

    def all_processes(self) -> Iterable[ProcessIdentity]: ...

    def process_count(self) -> int | None: ...

    def process_is_current(self, identity: ProcessIdentity) -> bool: ...

    def shutdown(self) -> None: ...


def _load_psutil() -> object | None:
    try:
        return importlib.import_module("psutil")
    except (ImportError, OSError):
        return None


def _iter_unique_identities(
    values: Iterable[ProcessIdentity],
    *,
    limit: int = MAX_LOCAL_INVENTORY,
) -> tuple[tuple[ProcessIdentity, ...], bool, int]:
    unique: dict[tuple[int, str, float | None], ProcessIdentity] = {}
    unique_total: set[tuple[int, str, float | None]] = set()
    for value in values:
        if not isinstance(value, ProcessIdentity):
            continue
        unique_total.add(value.key)
        if len(unique) < limit:
            unique.setdefault(value.key, value)
    ordered = tuple(sorted(unique.values(), key=lambda item: (item.name.casefold(), item.pid)))
    return ordered, len(unique_total) > len(ordered), len(unique_total)


class WindowsProcessBackend:
    """Win32 foreground/window discovery with a psutil/native identity path."""

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _TH32CS_SNAPPROCESS = 0x00000002
    _WINDOWS_EPOCH_100NS = 116_444_736_000_000_000

    def __init__(self, *, psutil_module: object = _AUTO) -> None:
        self._psutil = _load_psutil() if psutil_module is _AUTO else psutil_module
        self._status = "available" if sys.platform == "win32" else "unavailable_platform"
        self._closed = False
        if sys.platform == "win32":
            self._configure_native_api()

    @property
    def status(self) -> str:
        return self._status

    @staticmethod
    def _native() -> tuple[object, object]:
        return ctypes.windll.user32, ctypes.windll.kernel32

    @staticmethod
    def _handle_address(handle: object) -> int:
        value = getattr(handle, "value", handle)
        return int(value or 0)

    def _configure_native_api(self) -> None:
        """Assign pointer-sized signatures before any Windows handle is used."""

        from ctypes import wintypes

        try:
            user32, kernel32 = self._native()
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.IsWindow.argtypes = (wintypes.HWND,)
            user32.IsWindow.restype = wintypes.BOOL
            user32.IsWindowVisible.argtypes = (wintypes.HWND,)
            user32.IsWindowVisible.restype = wintypes.BOOL
            user32.IsIconic.argtypes = (wintypes.HWND,)
            user32.IsIconic.restype = wintypes.BOOL
            user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
            user32.GetWindowTextLengthW.restype = ctypes.c_int
            user32.GetWindowTextW.argtypes = (
                wintypes.HWND, wintypes.LPWSTR, ctypes.c_int,
            )
            user32.GetWindowTextW.restype = ctypes.c_int
            user32.GetWindowThreadProcessId.argtypes = (
                wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
            )
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            user32.GetWindowRect.argtypes = (
                wintypes.HWND, ctypes.POINTER(wintypes.RECT),
            )
            user32.GetWindowRect.restype = wintypes.BOOL

            kernel32.OpenProcess.argtypes = (
                wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
            )
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.QueryFullProcessImageNameW.argtypes = (
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
            )
            kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            kernel32.GetProcessTimes.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CreateToolhelp32Snapshot.argtypes = (
                wintypes.DWORD, wintypes.DWORD,
            )
            kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        except (AttributeError, OSError, TypeError):
            self._status = "degraded_native_signatures"

    @staticmethod
    def _window_title(user32: object, hwnd: int) -> str:
        length = int(user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(min(length, 4095) + 1)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    @staticmethod
    def _window_pid(user32: object, hwnd: int) -> int | None:
        from ctypes import wintypes

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) if pid.value else None

    @staticmethod
    def _window_bounds(user32: object, hwnd: int) -> tuple[int, int, int, int] | None:
        from ctypes import wintypes

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0 or width > 32768 or height > 32768:
            return None
        return int(rect.left), int(rect.top), width, height

    def _psutil_identity(self, pid: int) -> ProcessIdentity | None:
        module = self._psutil
        if module is None:
            return None
        try:
            process = module.Process(int(pid))
            return ProcessIdentity(int(pid), process.name(), process.create_time())
        except Exception:
            return None

    def _native_identity(self, pid: int) -> ProcessIdentity | None:
        if sys.platform != "win32" or int(pid) <= 0:
            return None
        from ctypes import wintypes

        handle = None
        try:
            _user32, kernel32 = self._native()
            handle = kernel32.OpenProcess(
                self._PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
            if not handle:
                return None

            size = wintypes.DWORD(32768)
            path_buffer = ctypes.create_unicode_buffer(size.value)
            name = ""
            if kernel32.QueryFullProcessImageNameW(
                handle, 0, path_buffer, ctypes.byref(size)
            ):
                name = _basename(path_buffer.value)
            if not name:
                return None

            class _FILETIME(ctypes.Structure):
                _fields_ = (
                    ("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD),
                )

            creation = _FILETIME()
            exit_time = _FILETIME()
            kernel = _FILETIME()
            user = _FILETIME()
            created_at = None
            if kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                ticks = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
                if ticks >= self._WINDOWS_EPOCH_100NS:
                    created_at = (
                        ticks - self._WINDOWS_EPOCH_100NS
                    ) / 10_000_000.0
            return ProcessIdentity(int(pid), name, created_at)
        except (AttributeError, OSError, TypeError, ValueError):
            return None
        finally:
            if handle:
                try:
                    ctypes.windll.kernel32.CloseHandle(handle)
                except (AttributeError, OSError):
                    pass

    def identity_for_pid(self, pid: int) -> ProcessIdentity | None:
        if self._closed:
            return None
        return self._psutil_identity(pid) or self._native_identity(pid)

    def _application_from_hwnd(
        self,
        hwnd: int,
        *,
        foreground: bool,
    ) -> RunningApplication | None:
        if self._closed or sys.platform != "win32" or not hwnd:
            return None
        try:
            user32, _kernel32 = self._native()
            if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
                return None
            if user32.IsIconic(hwnd):
                return None
            title = self._window_title(user32, hwnd)
            if not title:
                return None
            bounds = self._window_bounds(user32, hwnd)
            if bounds is None:
                return None
            pid = self._window_pid(user32, hwnd)
            identity = self.identity_for_pid(pid or 0)
            if identity is None:
                return None
            return RunningApplication(
                identity=identity,
                window_handle=int(hwnd),
                window_title=title,
                bounds=bounds,
                visible=True,
                foreground=foreground,
            )
        except (AttributeError, OSError, TypeError, ValueError):
            self._status = "degraded_window_api"
            return None

    def foreground_application(self) -> RunningApplication | None:
        if self._closed or sys.platform != "win32":
            return None
        try:
            user32, _kernel32 = self._native()
            hwnd = int(user32.GetForegroundWindow())
            return self._application_from_hwnd(hwnd, foreground=True)
        except (AttributeError, OSError, TypeError, ValueError):
            self._status = "degraded_foreground_api"
            return None

    def visible_applications(self) -> tuple[RunningApplication, ...]:
        if self._closed or sys.platform != "win32":
            return ()
        from ctypes import wintypes

        apps: list[RunningApplication] = []
        try:
            user32, _kernel32 = self._native()
            foreground_hwnd = int(user32.GetForegroundWindow())
            callback_type = ctypes.WINFUNCTYPE(
                ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
            )

            def _visit(hwnd: int, _lparam: int) -> bool:
                app = self._application_from_hwnd(
                    int(hwnd), foreground=int(hwnd) == foreground_hwnd
                )
                if app is not None:
                    apps.append(app)
                return len(apps) < MAX_LOCAL_INVENTORY

            callback = callback_type(_visit)
            user32.EnumWindows(callback, 0)
        except (AttributeError, OSError, TypeError, ValueError):
            self._status = "degraded_window_enumeration"
        return tuple(apps)

    def _psutil_processes(self) -> tuple[ProcessIdentity, ...] | None:
        module = self._psutil
        if module is None:
            return None
        items: list[ProcessIdentity] = []
        try:
            iterator = module.process_iter(["pid", "name", "create_time"])
            for process in iterator:
                try:
                    info = process.info
                    name = info.get("name") or ""
                    if not name:
                        continue
                    items.append(ProcessIdentity(
                        info["pid"], name, info.get("create_time")
                    ))
                except Exception:
                    continue
            return tuple(items)
        except Exception:
            return None

    def _toolhelp_processes(self, *, include_creation: bool) -> tuple[ProcessIdentity, ...]:
        if self._closed or sys.platform != "win32":
            return ()
        from ctypes import wintypes

        class _PROCESSENTRY32W(ctypes.Structure):
            _fields_ = (
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            )

        handle = None
        results: list[ProcessIdentity] = []
        try:
            _user32, kernel32 = self._native()
            kernel32.Process32FirstW.argtypes = (
                wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W),
            )
            kernel32.Process32FirstW.restype = wintypes.BOOL
            kernel32.Process32NextW.argtypes = (
                wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W),
            )
            kernel32.Process32NextW.restype = wintypes.BOOL
            handle = kernel32.CreateToolhelp32Snapshot(self._TH32CS_SNAPPROCESS, 0)
            invalid = ctypes.c_void_p(-1).value
            if not handle or self._handle_address(handle) == invalid:
                return ()
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = kernel32.Process32FirstW(handle, ctypes.byref(entry))
            while ok and len(results) < MAX_LOCAL_INVENTORY:
                pid = int(entry.th32ProcessID)
                name = _basename(entry.szExeFile)
                if pid > 0 and name:
                    identity = self._native_identity(pid) if include_creation else None
                    results.append(identity or ProcessIdentity(pid, name, None))
                ok = kernel32.Process32NextW(handle, ctypes.byref(entry))
        except (AttributeError, OSError, TypeError, ValueError):
            self._status = "degraded_process_enumeration"
        finally:
            if handle:
                try:
                    ctypes.windll.kernel32.CloseHandle(handle)
                except (AttributeError, OSError):
                    pass
        return tuple(results)

    def all_processes(self) -> tuple[ProcessIdentity, ...]:
        if self._closed:
            return ()
        values = self._psutil_processes()
        if values is not None:
            return values
        self._status = "degraded_psutil_unavailable"
        return self._toolhelp_processes(include_creation=True)

    def process_count(self) -> int | None:
        if self._closed:
            return None
        if self._psutil is not None:
            try:
                return len(self._psutil.pids())
            except Exception:
                pass
        values = self._toolhelp_processes(include_creation=False)
        return len(values) if values else None

    def process_is_current(self, identity: ProcessIdentity) -> bool:
        current = self.identity_for_pid(identity.pid)
        return current is not None and identities_match(identity, current, strict=False)

    def shutdown(self) -> None:
        self._closed = True


class LinuxProcessBackend:
    """Best-effort X11 discovery and process-only Wayland degradation."""

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        run: Callable[..., object] = subprocess.run,
        psutil_module: object = _AUTO,
        proc_root: Path | str = Path("/proc"),
    ) -> None:
        self._env = dict(os.environ if env is None else env)
        declared = str(self._env.get("XDG_SESSION_TYPE", "")).strip().casefold()
        if declared in {"x11", "wayland"}:
            self.session_type = declared
        elif self._env.get("WAYLAND_DISPLAY"):
            self.session_type = "wayland"
        elif self._env.get("DISPLAY"):
            self.session_type = "x11"
        else:
            self.session_type = "unknown"
        self._which = which
        self._run = run
        self._psutil = _load_psutil() if psutil_module is _AUTO else psutil_module
        self._proc_root = Path(proc_root)
        self._xdotool = which("xdotool") if self.session_type == "x11" else None
        self._wmctrl = which("wmctrl") if self.session_type == "x11" else None
        if self.session_type == "wayland":
            self._status = "degraded_wayland_processes_only"
        elif self.session_type == "x11" and (self._xdotool or self._wmctrl):
            self._status = "available_x11"
        elif self.session_type == "x11":
            self._status = "degraded_x11_tools_unavailable"
        else:
            self._status = "unavailable_desktop_session"
        self._closed = False

    @property
    def status(self) -> str:
        return self._status

    def _command(self, command: list[str]) -> object | None:
        try:
            return self._run(
                command,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
                env=self._env,
            )
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            return None

    @staticmethod
    def _result_text(result: object | None) -> str:
        if result is None or int(getattr(result, "returncode", 1)) != 0:
            return ""
        return str(getattr(result, "stdout", "") or "").strip()

    def _proc_identity(self, pid: int) -> ProcessIdentity | None:
        if int(pid) <= 0:
            return None
        if self._psutil is not None:
            try:
                process = self._psutil.Process(int(pid))
                return ProcessIdentity(pid, process.name(), process.create_time())
            except Exception:
                pass
        process_dir = self._proc_root / str(int(pid))
        try:
            name = (process_dir / "comm").read_text(
                encoding="utf-8", errors="replace"
            ).strip()
        except OSError:
            try:
                name = Path(os.readlink(process_dir / "exe")).name
            except OSError:
                return None
        created_at = None
        try:
            stat_text = (process_dir / "stat").read_text(
                encoding="utf-8", errors="replace"
            )
            close_paren = stat_text.rfind(")")
            fields = stat_text[close_paren + 2:].split()
            start_ticks = int(fields[19])
            ticks_per_second = int(os.sysconf("SC_CLK_TCK"))
            boot_time = None
            for line in (self._proc_root / "stat").read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                if line.startswith("btime "):
                    boot_time = int(line.split()[1])
                    break
            if boot_time is not None and ticks_per_second > 0:
                created_at = boot_time + start_ticks / ticks_per_second
        except (OSError, ValueError, IndexError):
            created_at = None
        try:
            return ProcessIdentity(pid, name, created_at)
        except (TypeError, ValueError):
            return None

    def identity_for_pid(self, pid: int) -> ProcessIdentity | None:
        if self._closed:
            return None
        return self._proc_identity(pid)

    def _xdotool_application(
        self,
        window_id: int,
        *,
        foreground: bool,
    ) -> RunningApplication | None:
        if not self._xdotool:
            return None
        title_result = self._command([self._xdotool, "getwindowname", str(window_id)])
        pid_result = self._command([self._xdotool, "getwindowpid", str(window_id)])
        geometry_result = self._command([
            self._xdotool, "getwindowgeometry", "--shell", str(window_id)
        ])
        title = self._result_text(title_result)
        pid_text = self._result_text(pid_result)
        geometry_text = self._result_text(geometry_result)
        if not title or not pid_text.isdigit():
            return None
        values: dict[str, int] = {}
        for line in geometry_text.splitlines():
            if "=" not in line:
                continue
            key, raw = line.split("=", 1)
            if key in {"X", "Y", "WIDTH", "HEIGHT"}:
                try:
                    values[key] = int(raw)
                except ValueError:
                    pass
        bounds = None
        if values.get("WIDTH", 0) > 0 and values.get("HEIGHT", 0) > 0:
            bounds = (
                values.get("X", 0), values.get("Y", 0),
                values["WIDTH"], values["HEIGHT"],
            )
        identity = self.identity_for_pid(int(pid_text))
        if identity is None:
            return None
        return RunningApplication(
            identity=identity,
            window_handle=int(window_id),
            window_title=title,
            bounds=bounds,
            visible=True,
            foreground=foreground,
        )

    def foreground_application(self) -> RunningApplication | None:
        if self._closed or self.session_type != "x11" or not self._xdotool:
            return None
        result = self._command([self._xdotool, "getactivewindow"])
        value = self._result_text(result)
        if not value.isdigit():
            return None
        return self._xdotool_application(int(value), foreground=True)

    def visible_applications(self) -> tuple[RunningApplication, ...]:
        if self._closed or self.session_type != "x11":
            return ()
        foreground = self.foreground_application()
        foreground_id = foreground.window_handle if foreground is not None else None
        results: list[RunningApplication] = []
        if self._xdotool:
            search = self._command([
                self._xdotool, "search", "--onlyvisible", "--name", "."
            ])
            for raw in self._result_text(search).splitlines()[:MAX_LOCAL_INVENTORY]:
                if not raw.strip().isdigit():
                    continue
                window_id = int(raw.strip())
                app = self._xdotool_application(
                    window_id, foreground=window_id == foreground_id
                )
                if app is not None:
                    results.append(app)
            return tuple(results)

        # wmctrl has no portable minimized-state column, so this is explicitly
        # a degraded approximation rather than claimed accessibility support.
        if self._wmctrl:
            listing = self._command([self._wmctrl, "-lpG"])
            for line in self._result_text(listing).splitlines()[:MAX_LOCAL_INVENTORY]:
                parts = line.split(maxsplit=8)
                if len(parts) < 9:
                    continue
                try:
                    window_id = int(parts[0], 16)
                    pid = int(parts[2])
                    bounds = tuple(int(value) for value in parts[3:7])
                except ValueError:
                    continue
                identity = self.identity_for_pid(pid)
                if identity is None or bounds[2] <= 0 or bounds[3] <= 0:
                    continue
                results.append(RunningApplication(
                    identity=identity,
                    window_handle=window_id,
                    window_title=parts[8],
                    bounds=bounds,
                    visible=True,
                    foreground=window_id == foreground_id,
                ))
            self._status = "degraded_x11_wmctrl_visibility"
        return tuple(results)

    def all_processes(self) -> tuple[ProcessIdentity, ...]:
        if self._closed:
            return ()
        results: list[ProcessIdentity] = []
        if self._psutil is not None:
            try:
                for process in self._psutil.process_iter(["pid", "name", "create_time"]):
                    try:
                        info = process.info
                        if info.get("name"):
                            results.append(ProcessIdentity(
                                info["pid"], info["name"], info.get("create_time")
                            ))
                    except Exception:
                        continue
                return tuple(results)
            except Exception:
                pass
        try:
            pids = sorted(
                int(item.name) for item in self._proc_root.iterdir()
                if item.name.isdigit()
            )
        except OSError:
            return ()
        for pid in pids[:MAX_LOCAL_INVENTORY]:
            identity = self._proc_identity(pid)
            if identity is not None:
                results.append(identity)
        return tuple(results)

    def process_count(self) -> int | None:
        if self._closed:
            return None
        if self._psutil is not None:
            try:
                return len(self._psutil.pids())
            except Exception:
                pass
        try:
            return sum(1 for item in self._proc_root.iterdir() if item.name.isdigit())
        except OSError:
            return None

    def process_is_current(self, identity: ProcessIdentity) -> bool:
        current = self.identity_for_pid(identity.pid)
        return current is not None and identities_match(identity, current, strict=False)

    def shutdown(self) -> None:
        self._closed = True


class UnavailableProcessBackend:
    """Import-safe backend for unsupported desktop platforms."""

    status = "unavailable_platform"

    def __init__(self) -> None:
        self._closed = False

    def foreground_application(self) -> None:
        return None

    def visible_applications(self) -> tuple[RunningApplication, ...]:
        return ()

    def identity_for_pid(self, pid: int) -> None:
        return None

    def all_processes(self) -> tuple[ProcessIdentity, ...]:
        return ()

    def process_count(self) -> None:
        return None

    def process_is_current(self, identity: ProcessIdentity) -> bool:
        return False

    def shutdown(self) -> None:
        self._closed = True


def default_process_backend(
    *,
    platform_name: str | None = None,
    env: Mapping[str, str] | None = None,
) -> ProcessBackend:
    current = str(platform_name or sys.platform).casefold()
    if current.startswith("win"):
        return WindowsProcessBackend()
    if current.startswith("linux"):
        return LinuxProcessBackend(env=env)
    return UnavailableProcessBackend()


def _parse_exclusions(value: str | Iterable[str]) -> frozenset[str]:
    raw_items: Iterable[object]
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    else:
        raw_items = value
    result: set[str] = set()
    for item in raw_items:
        result.update(_name_aliases(item))
    return frozenset(item for item in result if item)


def _safe_provider_name(identity: ProcessIdentity) -> str:
    name = _basename(identity.name)
    lowered = name.casefold()
    friendly = _FRIENDLY_APPLICATION_NAMES.get(lowered)
    if friendly:
        return friendly
    if lowered.endswith(".exe"):
        name = name[:-4]
    safe = _redact_sensitive_text(name)
    safe = _WINDOWS_USER_PATH_RE.sub(r"C:\\Users\\[user]", safe)
    safe = _POSIX_USER_PATH_RE.sub("/home/[user]", safe)
    safe = _CONTROL_RE.sub(" ", safe)
    safe = " ".join(safe.split())[:80]
    return safe or "Unknown application"


class ProcessAwareness:
    """Thread-safe owner for bounded process snapshots and transitions."""

    def __init__(
        self,
        backend: ProcessBackend | None = None,
        *,
        mode: ProcessContextMode | str = ProcessContextMode.VISIBLE_APPS,
        max_visible_apps: int = 20,
        excluded_apps: str | Iterable[str] = (),
        monotonic: Callable[[], float] = time.monotonic,
        publisher: Callable[[ProcessTransition], object] | None = None,
    ) -> None:
        self._backend = backend or default_process_backend()
        self._mode = ProcessContextMode(mode)
        self._max_visible_apps = max(1, min(int(max_visible_apps), MAX_VISIBLE_APPS))
        self._excluded_apps = _parse_exclusions(excluded_apps)
        self._monotonic = monotonic
        self._publisher = publisher
        self._lock = threading.RLock()
        self._shutdown = False
        self._previous_snapshot: ProcessSnapshot | None = None
        self._previous_visible_state: tuple[RunningApplication, ...] | None = None
        self._current_visible_state: tuple[RunningApplication, ...] = ()
        self._known_identities: dict[
            tuple[int, str, float | None], ProcessIdentity
        ] = {}
        self._last_snapshot: ProcessSnapshot | None = None
        self._local_inventory: ProcessInventory | None = None
        self._last_status = "not_collected"

    @property
    def mode(self) -> ProcessContextMode:
        return self._mode

    @property
    def is_shutdown(self) -> bool:
        with self._lock:
            return self._shutdown

    @property
    def last_status(self) -> str:
        with self._lock:
            return self._last_status

    @property
    def last_snapshot(self) -> ProcessSnapshot | None:
        with self._lock:
            return self._last_snapshot

    @property
    def local_inventory(self) -> ProcessInventory | None:
        with self._lock:
            return self._local_inventory

    def _is_sensitive(self, app: RunningApplication) -> bool:
        aliases = _name_aliases(app.identity.name)
        if aliases & _BUILTIN_SENSITIVE_EXECUTABLES:
            return True
        if aliases & self._excluded_apps:
            return True
        title = app.window_title.casefold()
        if any(item and item in title for item in self._excluded_apps):
            return True
        return bool(_SENSITIVE_TITLE_RE.search(app.window_title))

    def _classified(self, app: RunningApplication) -> RunningApplication:
        sensitive = app.sensitive or self._is_sensitive(app)
        return app if app.sensitive == sensitive else replace(app, sensitive=sensitive)

    @staticmethod
    def _empty_snapshot(now: float, status: str) -> ProcessSnapshot:
        return ProcessSnapshot(None, (), None, now, status)

    def _call(self, label: str, callback: Callable[[], Any], failures: list[str]) -> Any:
        try:
            return callback()
        except Exception as exc:
            failures.append(label)
            logger.debug("Process awareness %s failed: %s", label, type(exc).__name__)
            return None

    def _dedup_visible(
        self,
        foreground: RunningApplication | None,
        values: Iterable[RunningApplication],
        *,
        apply_limit: bool = True,
    ) -> tuple[RunningApplication, ...]:
        selected: dict[
            tuple[int, str, float | None], RunningApplication
        ] = {}
        if foreground is not None:
            foreground = self._classified(replace(
                foreground, foreground=True, visible=True
            ))
            selected[foreground.key] = foreground
        for raw in values:
            if not isinstance(raw, RunningApplication) or not raw.visible:
                continue
            app = self._classified(raw)
            if app.key in selected:
                if app.foreground and not selected[app.key].foreground:
                    selected[app.key] = app
                continue
            selected[app.key] = app
        apps = list(selected.values())
        apps.sort(key=lambda app: (
            0 if app.foreground else 1,
            app.identity.name.casefold(),
            app.identity.pid,
        ))
        return tuple(apps[:self._max_visible_apps] if apply_limit else apps)

    def _capture_locked(self, mode: ProcessContextMode) -> ProcessSnapshot:
        now = _finite_monotonic(self._monotonic())
        if self._shutdown:
            snapshot = self._empty_snapshot(now, "shutdown")
            self._last_snapshot = snapshot
            self._last_status = snapshot.status
            return snapshot
        if mode is ProcessContextMode.OFF:
            snapshot = self._empty_snapshot(now, "disabled")
            self._last_snapshot = snapshot
            self._last_status = snapshot.status
            return snapshot

        failures: list[str] = []
        foreground = self._call(
            "foreground", self._backend.foreground_application, failures
        )
        if not isinstance(foreground, RunningApplication):
            foreground = None
        else:
            foreground = self._classified(foreground)

        visible_values: Iterable[RunningApplication] = ()
        total_count = None
        if mode in {ProcessContextMode.VISIBLE_APPS, ProcessContextMode.ALL_PROCESSES}:
            value = self._call(
                "visible_apps", self._backend.visible_applications, failures
            )
            if value is not None:
                visible_values = value

        if mode is ProcessContextMode.FOREGROUND_ONLY:
            full_visible = self._dedup_visible(foreground, (), apply_limit=False)
        else:
            full_visible = self._dedup_visible(
                foreground, visible_values, apply_limit=False
            )
        self._current_visible_state = full_visible
        visible = full_visible[:self._max_visible_apps]

        if mode is ProcessContextMode.ALL_PROCESSES:
            raw_inventory = self._call(
                "all_processes", self._backend.all_processes, failures
            )
            if raw_inventory is None:
                processes, truncated, total_count = (), False, None
            else:
                processes, truncated, total_count = _iter_unique_identities(
                    raw_inventory
                )
            self._local_inventory = ProcessInventory(
                processes=processes,
                total_process_count=total_count,
                captured_at_monotonic=now,
                truncated=truncated,
                status="degraded" if "all_processes" in failures else "available",
            )
        elif mode is ProcessContextMode.VISIBLE_APPS:
            value = self._call("process_count", self._backend.process_count, failures)
            if value is not None:
                try:
                    total_count = max(0, int(value))
                except (TypeError, ValueError):
                    failures.append("process_count")
            self._local_inventory = None
        else:
            self._local_inventory = None

        backend_status = str(getattr(self._backend, "status", "available") or "available")
        if failures:
            status = "degraded:" + ",".join(dict.fromkeys(failures))
        else:
            status = backend_status
        snapshot = ProcessSnapshot(
            foreground=foreground,
            visible_apps=visible,
            total_process_count=total_count,
            captured_at_monotonic=now,
            status=status,
        )
        self._last_snapshot = snapshot
        self._last_status = status
        return snapshot

    def snapshot(
        self,
        mode: ProcessContextMode | str | None = None,
    ) -> ProcessSnapshot:
        requested = self._mode if mode is None else ProcessContextMode(mode)
        privacy_rank = {
            ProcessContextMode.OFF: 0,
            ProcessContextMode.FOREGROUND_ONLY: 1,
            ProcessContextMode.VISIBLE_APPS: 2,
            ProcessContextMode.ALL_PROCESSES: 3,
        }
        # Callers may request a narrower one-shot view, never widen the
        # configured privacy ceiling (including OFF).
        selected = (
            requested
            if privacy_rank[requested] <= privacy_rank[self._mode]
            else self._mode
        )
        with self._lock:
            return self._capture_locked(selected)

    def get_active_app(self) -> RunningApplication | None:
        return self.snapshot().foreground

    def list_running_apps(self) -> tuple[RunningApplication, ...]:
        return self.snapshot().visible_apps

    def monitor_process(self, name: str) -> tuple[ProcessIdentity, ...]:
        """Return exact basename matches from an explicit local inventory query."""

        aliases = _name_aliases(name)
        if not aliases:
            return ()
        with self._lock:
            if self._shutdown or self._mode is ProcessContextMode.OFF:
                return ()
            raw = self._call("monitor_process", self._backend.all_processes, [])
            if raw is None:
                return ()
            matched: dict[tuple[int, str, float | None], ProcessIdentity] = {}
            for identity in raw:
                if not isinstance(identity, ProcessIdentity):
                    continue
                if aliases & _name_aliases(identity.name):
                    matched.setdefault(identity.key, identity)
                if len(matched) >= 32:
                    break
            return tuple(sorted(matched.values(), key=lambda item: item.pid))

    def validate_identity(
        self,
        expected: ProcessIdentity,
        *,
        strict: bool = True,
    ) -> bool:
        """Re-resolve an identity; strict mode is required before effects."""

        if not isinstance(expected, ProcessIdentity):
            return False
        with self._lock:
            if self._shutdown or self._mode is ProcessContextMode.OFF:
                return False
            current = self._call(
                "identity_validation",
                lambda: self._backend.identity_for_pid(expected.pid),
                [],
            )
            return isinstance(current, ProcessIdentity) and identities_match(
                expected, current, strict=strict
            )

    def _remember(self, identities: Iterable[ProcessIdentity]) -> None:
        for identity in identities:
            self._known_identities.setdefault(identity.key, identity)
            if len(self._known_identities) > MAX_KNOWN_IDENTITIES:
                oldest = next(iter(self._known_identities))
                self._known_identities.pop(oldest, None)

    @staticmethod
    def _transition(
        kind: ProcessTransitionKind,
        app: RunningApplication | None,
        summary: str,
        *,
        identity: ProcessIdentity | None = None,
    ) -> ProcessTransition:
        sensitive = bool(app.sensitive) if app is not None else False
        selected_identity = identity or (app.identity if app is not None else None)
        return ProcessTransition(
            kind=kind,
            summary=("Sensitive application state changed" if sensitive else summary),
            identity=selected_identity,
            sensitive=sensitive,
        )

    def _diff_locked(self, current: ProcessSnapshot) -> tuple[ProcessTransition, ...]:
        previous = self._previous_snapshot
        self._previous_snapshot = current
        current_visible = self._current_visible_state
        previous_visible = self._previous_visible_state
        self._previous_visible_state = current_visible
        current_apps = {app.key: app for app in current_visible}
        current_identities = tuple(app.identity for app in current_visible)

        inventory = self._local_inventory
        if previous is None:
            self._remember(current_identities)
            if inventory is not None:
                self._remember(inventory.processes)
            return ()

        previous_apps = {
            app.key: app for app in (
                previous.visible_apps if previous_visible is None else previous_visible
            )
        }
        transitions: list[ProcessTransition] = []

        new_keys = set(current_apps) - set(previous_apps)
        missing_keys = set(previous_apps) - set(current_apps)

        sort_key = lambda key: (key[0], key[1], -1.0 if key[2] is None else key[2])

        for key in sorted(missing_keys, key=sort_key):
            app = previous_apps[key]
            alive = self._call(
                "process_liveness",
                lambda identity=app.identity: self._backend.process_is_current(identity),
                [],
            )
            if alive is False:
                transitions.append(self._transition(
                    ProcessTransitionKind.PROCESS_EXITED,
                    app,
                    "Application process exited",
                ))
                self._known_identities.pop(app.identity.key, None)

        for key in sorted(new_keys, key=sort_key):
            app = current_apps[key]
            if app.identity.key not in self._known_identities:
                transitions.append(self._transition(
                    ProcessTransitionKind.PROCESS_STARTED,
                    app,
                    "Application process started",
                ))

        old_foreground = previous.foreground.key if previous.foreground is not None else None
        new_foreground = current.foreground.key if current.foreground is not None else None
        if old_foreground != new_foreground:
            transitions.append(self._transition(
                ProcessTransitionKind.FOREGROUND_APP_CHANGED,
                current.foreground,
                "Foreground application changed",
            ))

        for key in sorted(missing_keys, key=sort_key):
            transitions.append(self._transition(
                ProcessTransitionKind.VISIBLE_APP_HIDDEN,
                previous_apps[key],
                "Visible application hidden",
            ))
        for key in sorted(new_keys, key=sort_key):
            transitions.append(self._transition(
                ProcessTransitionKind.VISIBLE_APP_APPEARED,
                current_apps[key],
                "Visible application appeared",
            ))

        self._remember(current_identities)
        if inventory is not None:
            self._remember(inventory.processes)
        return tuple(transitions)

    def poll(self) -> tuple[ProcessTransition, ...]:
        """Capture and diff once; the first poll establishes a silent baseline."""

        with self._lock:
            if self._shutdown or self._mode is ProcessContextMode.OFF:
                return ()
            current = self._capture_locked(self._mode)
            transitions = self._diff_locked(current)
            publisher = self._publisher
            if publisher is not None:
                for transition in transitions:
                    if self._shutdown:
                        break
                    try:
                        publisher(transition)
                    except Exception as exc:
                        logger.warning(
                            "Process transition publisher failed: %s",
                            type(exc).__name__,
                        )
            return transitions

    def provider_context(
        self,
        snapshot: ProcessSnapshot | None = None,
        *,
        explicit_all_processes: bool = False,
    ) -> str:
        """Return basename-only minimized context; titles and PIDs never appear."""

        selected = snapshot or self.snapshot()
        if self._mode is ProcessContextMode.OFF or selected.status in {"disabled", "shutdown"}:
            return ""

        lines: list[str] = []
        foreground = selected.foreground
        if foreground is None:
            lines.append("Foreground: unavailable")
        elif foreground.sensitive:
            lines.append("Foreground: Sensitive application active")
        else:
            lines.append(f"Foreground: {_safe_provider_name(foreground.identity)}")

        if self._mode in {ProcessContextMode.VISIBLE_APPS, ProcessContextMode.ALL_PROCESSES}:
            names: list[str] = []
            sensitive_seen = False
            for app in selected.visible_apps:
                if app.sensitive:
                    if foreground is None or app.key != foreground.key:
                        sensitive_seen = True
                    continue
                name = _safe_provider_name(app.identity)
                if name not in names:
                    names.append(name)
            if sensitive_seen:
                names.append("Sensitive application")
            if names:
                lines.append("Visible applications:")
                lines.extend(f"- {name}" for name in names[:self._max_visible_apps])

        # ALL_PROCESSES is local-only by default.  A caller must attest that an
        # explicit user process-list request authorized this additional section.
        if (
            explicit_all_processes
            and self._mode is ProcessContextMode.ALL_PROCESSES
            and self._local_inventory is not None
        ):
            names: list[str] = []
            for identity in self._local_inventory.processes:
                aliases = _name_aliases(identity.name)
                if aliases & (_BUILTIN_SENSITIVE_EXECUTABLES | self._excluded_apps):
                    continue
                safe = _safe_provider_name(identity)
                if safe not in names:
                    names.append(safe)
                if len(names) >= MAX_PROVIDER_PROCESS_NAMES:
                    break
            if names:
                lines.append("Running processes (explicit request):")
                lines.extend(f"- {name}" for name in names)

        safe = _redact_sensitive_text("\n".join(lines))
        safe = _WINDOWS_USER_PATH_RE.sub(r"C:\\Users\\[user]", safe)
        safe = _POSIX_USER_PATH_RE.sub("/home/[user]", safe)
        return safe[:8000]

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            self._previous_snapshot = None
            self._previous_visible_state = None
            self._current_visible_state = ()
            self._known_identities.clear()
            self._last_snapshot = None
            self._local_inventory = None
            self._last_status = "shutdown"
            try:
                self._backend.shutdown()
            except Exception as exc:
                logger.debug(
                    "Process backend shutdown failed: %s", type(exc).__name__
                )


__all__ = [
    "LinuxProcessBackend",
    "ProcessAwareness",
    "ProcessBackend",
    "ProcessContextMode",
    "ProcessIdentity",
    "ProcessInventory",
    "ProcessSnapshot",
    "ProcessTransition",
    "ProcessTransitionKind",
    "RunningApplication",
    "UnavailableProcessBackend",
    "WindowsProcessBackend",
    "default_process_backend",
    "identities_match",
]
