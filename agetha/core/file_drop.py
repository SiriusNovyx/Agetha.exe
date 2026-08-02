"""Validate local drag-and-drop files without exposing private filesystem paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import stat

from agetha.core.external_context import PreparedExternalContext, prepare_external_context


MAX_FILE_DROP_BYTES = 10 * 1024 * 1024
_SENSITIVE_EXACT = frozenset({
    ".env", "id_rsa", "id_ed25519", "credentials.json",
})
_SENSITIVE_SUFFIXES = (".pem", ".key", ".kdbx")


@dataclass(frozen=True)
class PreparedFileDrop:
    local_path: Path | None
    filename: str
    size_bytes: int
    accepted: bool
    provider_context: PreparedExternalContext
    reason: str = ""


def _parse_single_path(raw: object) -> Path | None:
    value = str(raw or "").strip()
    if len(value) >= 2 and value[0] == "{" and value[-1] == "}":
        value = value[1:-1]
    elif len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return Path(value) if value else None


def _is_sensitive(filename: str) -> bool:
    name = filename.casefold()
    return (
        name in _SENSITIVE_EXACT
        or name.startswith(".env.")
        or name.startswith("secrets.")
        or name.endswith(_SENSITIVE_SUFFIXES)
    )


def _looks_binary(path: Path) -> bool:
    with path.open("rb") as handle:
        sample = handle.read(4096)
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _size_category(size: int) -> str:
    if size < 64 * 1024:
        return "small"
    if size < 1024 * 1024:
        return "medium"
    return "large"


def prepare_file_drop(
    raw_path: object,
    *,
    max_bytes: int = MAX_FILE_DROP_BYTES,
) -> PreparedFileDrop:
    """Validate one dropped file.

    Symlinks are rejected: the displayed name must identify the same filesystem
    object used locally, avoiding a target swap or unexpected external location.
    Sensitive or binary files still create a local semantic event, but their
    names and contents are withheld from providers.
    """
    path = _parse_single_path(raw_path)
    filename = path.name if path is not None and path.name else "unknown file"

    def rejected(reason: str, *, local_path: Path | None = None, size: int = 0) -> PreparedFileDrop:
        return PreparedFileDrop(
            local_path, filename, size, False,
            prepare_external_context("", source="file_drop", allowed=False, reason=reason),
            reason,
        )

    if path is None:
        return rejected("missing_path")
    try:
        if path.is_symlink():
            return rejected("symlink_rejected")
        info = path.stat()
    except (OSError, ValueError):
        return rejected("not_found")
    if not stat.S_ISREG(info.st_mode):
        return rejected("not_regular_file")

    size = int(info.st_size)
    if size > max(0, int(max_bytes)):
        return rejected("file_too_large", local_path=path, size=size)

    sensitive = _is_sensitive(filename)
    try:
        binary = _looks_binary(path)
    except OSError:
        return rejected("unreadable", local_path=path, size=size)

    if sensitive or binary:
        reason = "sensitive_filename" if sensitive else "binary_unsupported"
        safe_event = prepare_external_context(
            "",
            source="file_drop",
            allowed=False,
            reason=reason,
        )
        return PreparedFileDrop(path, filename, size, True, safe_event, reason)

    external = prepare_external_context(
        f"filename: {filename}\nsafe metadata: text file, size category {_size_category(size)}",
        source="file_drop",
        max_chars=500,
    )
    return PreparedFileDrop(path, filename, size, True, external)
