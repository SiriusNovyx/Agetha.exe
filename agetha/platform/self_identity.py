"""Exact, runtime-aware identity checks for Agetha's own process/window.

Name checks are intentionally a fallback.  A known process ID or native
window handle is stronger and avoids treating every similarly named program as
the running app.  Frozen executable aliases are limited to the two names used
by the existing distribution artifacts.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from pathlib import PurePath


_FROZEN_EXECUTABLE_NAMES = frozenset({"agetha.exe", "main.exe"})
_SOURCE_ENTRYPOINT_NAMES = frozenset({"main.py"})


def _basename(value: object) -> str:
    text = str(value or "").strip().strip('"').replace("\\", "/")
    return PurePath(text).name.casefold()


def is_frozen_runtime() -> bool:
    """Return PyInstaller-compatible frozen state without assuming the attr exists."""

    return bool(getattr(sys, "frozen", False))


def self_executable_names(
    *,
    executable: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
) -> frozenset[str]:
    """Return exact executable/entrypoint names that can identify this app.

    Source mode includes the current interpreter because an image-name kill of
    that interpreter would include this process.  Frozen mode instead includes
    only the current executable and the repository's established distribution
    aliases; it never treats an unrelated ``python.exe`` as Agetha.
    """

    frozen_now = is_frozen_runtime() if frozen is None else bool(frozen)
    current_name = _basename(sys.executable if executable is None else executable)
    names = set(_FROZEN_EXECUTABLE_NAMES if frozen_now else _SOURCE_ENTRYPOINT_NAMES)
    if current_name:
        names.add(current_name)
    return frozenset(names)


def _matches_executable_name(process_name: object) -> bool:
    name = _basename(process_name)
    if not name:
        return False
    names = self_executable_names()
    if name in names:
        return True
    # Commands often omit the Windows executable suffix (``main`` rather than
    # ``main.exe``).  Only an exact suffix-completed match is accepted.
    return "." not in name and f"{name}.exe" in names


def is_self_process_identity(
    *,
    process_name: object = "",
    process_id: int | None = None,
    current_pid: int | None = None,
) -> bool:
    """Recognize this process by PID first, then by one exact executable name."""

    if process_id is not None:
        try:
            return int(process_id) == int(os.getpid() if current_pid is None else current_pid)
        except (TypeError, ValueError):
            pass
    return _matches_executable_name(process_name)


def is_self_window_identity(
    *,
    process_name: object = "",
    process_id: int | None = None,
    window_handle: int | None = None,
    own_window_handles: Iterable[int] = (),
    current_pid: int | None = None,
) -> bool:
    """Recognize an owned HWND or a window belonging to the current process."""

    if window_handle is not None:
        try:
            handle = int(window_handle)
            if any(handle == int(owned) for owned in own_window_handles):
                return True
        except (TypeError, ValueError):
            pass
    return is_self_process_identity(
        process_name=process_name,
        process_id=process_id,
        current_pid=current_pid,
    )


def process_id_from_stable_target_id(stable_id: object) -> int | None:
    """Extract the PID from Agetha's ``platform:hwnd:pid`` target identity."""

    parts = str(stable_id or "").rsplit(":", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        return None
    value = int(parts[1])
    return value if value > 0 else None


__all__ = [
    "is_frozen_runtime",
    "is_self_process_identity",
    "is_self_window_identity",
    "process_id_from_stable_target_id",
    "self_executable_names",
]
