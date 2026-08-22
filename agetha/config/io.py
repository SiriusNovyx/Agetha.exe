"""Durable low-level writes for Agetha-owned configuration state."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path


class AtomicWriteError(OSError):
    """Report whether an atomic replacement may already be visible on disk."""

    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.state = state
        self.write_applied = state == "write_applied_verification_failed"


def fsync_parent_directory(path: Path) -> None:
    """Persist a completed rename on POSIX; Windows has no portable equivalent."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    directory_fd = os.open(path.parent, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def write_atomic_config(
    path: Path,
    content: str,
    *,
    mkstemp: Callable[..., tuple[int, str]] = tempfile.mkstemp,
    fsync_parent: Callable[[Path], None] = fsync_parent_directory,
) -> None:
    """Durably replace a text file using an exclusive same-directory temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    replaced = False
    try:
        fd, temp_name = mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        try:
            os.chmod(temp_name, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            # mkstemp is 0600 on POSIX. Windows files inherit the ACL of the
            # per-user application directory; chmod is not an ACL boundary.
            pass
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        replaced = True
        fsync_parent(path)
    except Exception as exc:
        if temp_name is not None and not replaced:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
        state = (
            "write_applied_verification_failed" if replaced else "write_not_applied"
        )
        raise AtomicWriteError(state, f"atomic write failed during {state}") from exc
