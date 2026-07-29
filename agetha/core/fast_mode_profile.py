"""Atomic, reversible Fast Mode configuration profile.

This module deliberately depends only on the standard library and app_config's
low-level document helpers. In particular it must not import ``agetha.utils``:
utils loads and caches settings at import time, before startup reconciliation.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import stat
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

from agetha.app_config import (
    AtomicWriteError,
    CONFIG_PATH,
    FAST_MODE_OVERRIDES,
    _fsync_parent_directory,
    _is_secret_key,
    _write_atomic_config,
    default_config_dict,
    ensure_config_file,
    parse_config_document,
    read_config_document,
    render_config_document,
    validate_config_document,
    validate_config_value,
    write_config_document,
)


FAST_MODE_SCHEMA_VERSION = 1
FAST_MODE_PROFILE_VERSION = 1
FAST_MODE_SNAPSHOT_NAME = "fast_mode_snapshot.json"
FAST_MODE_LOCK_RETRY_SECONDS = 0.075
FAST_MODE_LOCK_TIMEOUT_SECONDS = 4.0

FAST_MODE_FORBIDDEN_KEYS = frozenset({
    # Provider selection, models, credentials, and remote endpoints.
    "USE_LOCAL_AI", "ENABLE_GROQ", "ENABLE_OPENROUTER", "GROQ_MODEL",
    "OPENROUTER_MODEL", "LOCAL_AI_MODEL", "UNLIMITED_OCR_SERVER_URL",
    # Command permissions, confirmations, and protected targets.
    "ENABLE_COMMAND_EXECUTION", "ENABLE_COMMAND_CONFIRMATIONS",
    "ENABLE_WINDOW_CONTROL", "FORCE_CLOSE_AUTO_ALLOW", "PROTECTED_PROCESSES",
    "DRY_RUN_MODE", "ENABLE_AUTOSTART_CONTROL", "ENABLE_THEME_CONTROL",
    # OCR privacy, exclusions, and explicit remote deep-OCR authorization.
    "ENABLE_SCREEN_READER", "OCR_FOCUSED_WINDOW_ONLY",
    "OCR_REDACT_SENSITIVE_TEXT", "OCR_EXCLUDED_APPS",
    "OCR_EXCLUDED_TITLE_PATTERNS", "INCLUDE_WINDOW_TITLE_IN_CONTEXT",
    "UNLIMITED_OCR_ALLOW_REMOTE", "DEEP_OCR_BACKEND",
    # Consent-bearing external context and persistence features.
    "ENABLE_LONGTERM_MEMORY", "ENABLE_WEB_RAG", "ENABLE_STATUS_PROVIDERS",
})

logger = logging.getLogger("Agetha")

_FALSE_STATUSES = frozenset({
    "config_write_failed",
    "invalid_updates",
    "profile_busy",
    "snapshot_cleanup_failed",
    "snapshot_invalid",
    "snapshot_write_failed",
    "unsafe_path_state",
    "unsafe_profile_definition",
    "verification_pending",
})


class FastModeProfileBusyError(RuntimeError):
    """Another process held the profile lock past the bounded wait."""


class UnsafeFastModePathError(RuntimeError):
    """A transaction path failed link, type, or descriptor-identity checks."""


class FastModeVerificationPendingError(RuntimeError):
    """A replacement may be visible, but its verification was interrupted."""


@dataclass(frozen=True)
class FastModeReconcileResult:
    status: str
    changed_keys: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status not in _FALSE_STATUSES


@dataclass(frozen=True)
class FastModeProfileInspection:
    status: str
    active: bool
    valid: bool
    managed_count: int
    original_values: dict[str, str | None]
    forced_values: dict[str, str]
    warnings: tuple[str, ...] = ()


_PROFILE_LOCK = threading.RLock()
_CACHE_LOCK = threading.RLock()
# Resolved config path -> (resolved snapshot path, validated snapshot).
_SNAPSHOT_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}
_ACTIVE_CACHE: set[str] = set()

_BUSY_WARNING = (
    "Fast Mode is currently being updated by another process. Close the other "
    "Agetha or Medic Checker instance and try again. No settings were changed."
)
_VERIFICATION_WARNING = (
    "The configuration write may have completed, but verification was interrupted. "
    "Recovery metadata was preserved. Run Fast Mode reconciliation again to inspect "
    "the current disk state safely."
)


def _enabled(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "yes", "true", "on"}


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _audit_fast_mode(
    config: Path,
    event: str,
    status: str,
    *,
    changed_keys: tuple[str, ...] = (),
    conflict_keys: tuple[str, ...] = (),
) -> None:
    """Best-effort structured audit record containing names, never values."""
    try:
        from agetha.core.audit_log import log_audit

        safe_changed = tuple(
            key for key in (str(item).strip().upper() for item in changed_keys)
            if key and key.replace("_", "").isalnum()
        )
        safe_conflicts = tuple(
            key for key in (str(item).strip().upper() for item in conflict_keys)
            if key and key.replace("_", "").isalnum()
        )
        details = {
            "schema_version": FAST_MODE_SCHEMA_VERSION,
            "profile_version": FAST_MODE_PROFILE_VERSION,
            "managed_count": len(FAST_MODE_OVERRIDES),
            "platform": platform.system() or "unknown",
            "changed_keys": ",".join(dict.fromkeys(safe_changed)),
            "conflict_keys": ",".join(dict.fromkeys(safe_conflicts)),
        }
        log_audit(
            event,
            details,
            status,
            audit_file=config.parent / "memory" / "audit_log.jsonl",
        )
    except Exception:
        # Audit availability must never interfere with recovery.
        pass


def _result_from_exception(
    config: Path,
    exc: Exception,
    *,
    default_status: str = "config_write_failed",
) -> FastModeReconcileResult:
    _clear_cache(config)
    if isinstance(exc, FastModeProfileBusyError):
        result = FastModeReconcileResult(
            "profile_busy", warnings=(_BUSY_WARNING,), error=str(exc),
        )
        _audit_fast_mode(config, "fast_mode_profile_busy", result.status)
        return result
    if isinstance(exc, (UnsafeFastModePathError, ValueError)):
        result = FastModeReconcileResult(
            "unsafe_path_state",
            warnings=("Fast Mode transaction paths failed safety validation.",),
            error=str(exc),
        )
        _audit_fast_mode(config, "fast_mode_path_rejected", result.status)
        return result
    if isinstance(exc, FastModeVerificationPendingError):
        result = FastModeReconcileResult(
            "verification_pending",
            warnings=(_VERIFICATION_WARNING,),
            error=str(exc),
        )
        _audit_fast_mode(config, "fast_mode_verification_pending", result.status)
        return result
    return FastModeReconcileResult(default_status, error=str(exc))


def _unsafe_profile_result() -> FastModeReconcileResult | None:
    safe, unsafe_keys = validate_fast_mode_override_allowlist()
    if safe:
        return None
    names = ", ".join(unsafe_keys)
    return FastModeReconcileResult(
        "unsafe_profile_definition",
        warnings=(f"Unsafe Fast Mode override keys: {names}",),
        error="the Fast Mode override definition failed its safety invariant",
    )


def _profile_paths(
    config_path: Path | str | None,
    snapshot_path: Path | str | None,
) -> tuple[Path, Path]:
    config = Path(config_path or CONFIG_PATH)
    if not config.is_absolute():
        config = Path(os.path.abspath(config))
    expected = config.parent / "memory" / FAST_MODE_SNAPSHOT_NAME
    snapshot = Path(snapshot_path) if snapshot_path is not None else expected
    if not snapshot.is_absolute():
        snapshot = Path(os.path.abspath(snapshot))
    if _path_key(snapshot) != _path_key(expected):
        raise ValueError("Fast Mode snapshot path is outside the owned memory directory")
    return config, snapshot


def fast_mode_snapshot_path(config_path: Path | str | None = None) -> Path:
    return _profile_paths(config_path, None)[1]


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attributes & reparse_flag)


def _stat_identity(info: os.stat_result) -> tuple[int, int] | None:
    device = int(getattr(info, "st_dev", 0) or 0)
    inode = int(getattr(info, "st_ino", 0) or 0)
    return (device, inode) if inode else None


def _assert_regular_path(path: Path, category: str) -> os.stat_result:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise UnsafeFastModePathError(f"unsafe Fast Mode {category}") from exc
    if _is_reparse_or_symlink(path) or not stat.S_ISREG(info.st_mode):
        raise UnsafeFastModePathError(f"unsafe Fast Mode {category}")
    return info


def _assert_safe_mutation_paths(config: Path, snapshot: Path) -> tuple[int, int] | None:
    if config.exists() and _is_reparse_or_symlink(config):
        raise UnsafeFastModePathError("unsafe Fast Mode config path")
    if config.exists():
        _assert_regular_path(config, "config path")
    memory_dir = snapshot.parent
    if memory_dir.exists() and _is_reparse_or_symlink(memory_dir):
        raise UnsafeFastModePathError("unsafe Fast Mode memory directory")
    memory_dir.mkdir(parents=True, exist_ok=True)
    try:
        memory_info = os.lstat(memory_dir)
    except OSError as exc:
        raise UnsafeFastModePathError("unsafe Fast Mode memory directory") from exc
    if _is_reparse_or_symlink(memory_dir) or not stat.S_ISDIR(memory_info.st_mode):
        raise UnsafeFastModePathError("unsafe Fast Mode memory directory")
    if snapshot.exists():
        _assert_regular_path(snapshot, "snapshot path")
    return _stat_identity(memory_info)


def _restrict_user_file(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # mkstemp already creates 0600 files on POSIX. Windows ACLs inherit from
        # the containing user directory, and chmod support varies by filesystem.
        pass


def _validate_open_file_identity(handle: BinaryIO, path: Path, category: str) -> None:
    try:
        opened = os.fstat(handle.fileno())
        current = _assert_regular_path(path, category)
    except OSError as exc:
        raise UnsafeFastModePathError(f"unsafe Fast Mode {category}") from exc
    if not stat.S_ISREG(opened.st_mode):
        raise UnsafeFastModePathError(f"unsafe Fast Mode {category}")
    opened_identity = _stat_identity(opened)
    path_identity = _stat_identity(current)
    if opened_identity is not None and path_identity is not None:
        if opened_identity != path_identity:
            raise UnsafeFastModePathError(f"unsafe Fast Mode {category} identity")


def _open_verified_lock_file(path: Path) -> BinaryIO:
    """Open the persistent lock without following links, then verify identity."""
    if path.exists() and _is_reparse_or_symlink(path):
        raise UnsafeFastModePathError("unsafe Fast Mode lock path")

    if os.name != "nt":
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            raise UnsafeFastModePathError("unsafe Fast Mode lock path") from exc
        try:
            try:
                os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            except (AttributeError, OSError):
                pass
            handle = os.fdopen(fd, "r+b", buffering=0)
        except Exception:
            os.close(fd)
            raise
    else:
        import ctypes
        import ctypes.wintypes
        import msvcrt

        class _ByHandleFileInformation(ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", ctypes.wintypes.DWORD),
                ("ftCreationTime", ctypes.wintypes.FILETIME),
                ("ftLastAccessTime", ctypes.wintypes.FILETIME),
                ("ftLastWriteTime", ctypes.wintypes.FILETIME),
                ("dwVolumeSerialNumber", ctypes.wintypes.DWORD),
                ("nFileSizeHigh", ctypes.wintypes.DWORD),
                ("nFileSizeLow", ctypes.wintypes.DWORD),
                ("nNumberOfLinks", ctypes.wintypes.DWORD),
                ("nFileIndexHigh", ctypes.wintypes.DWORD),
                ("nFileIndexLow", ctypes.wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD,
            ctypes.wintypes.DWORD, ctypes.c_void_p, ctypes.wintypes.DWORD,
            ctypes.wintypes.DWORD, ctypes.wintypes.HANDLE,
        ]
        create_file.restype = ctypes.wintypes.HANDLE
        get_info = kernel32.GetFileInformationByHandle
        get_info.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
        get_info.restype = ctypes.wintypes.BOOL
        get_type = kernel32.GetFileType
        get_type.argtypes = [ctypes.wintypes.HANDLE]
        get_type.restype = ctypes.wintypes.DWORD

        native = create_file(
            str(path),
            0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
            0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE
            None,
            4,  # OPEN_ALWAYS: a persistent unlocked lock file is valid.
            0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if native in (None, invalid_handle):
            raise UnsafeFastModePathError("unsafe Fast Mode lock path")
        converted = False
        try:
            info = _ByHandleFileInformation()
            if not get_info(native, ctypes.byref(info)) or get_type(native) != 1:
                raise UnsafeFastModePathError("unsafe Fast Mode lock handle")
            if info.dwFileAttributes & 0x400 or info.dwFileAttributes & 0x10:
                raise UnsafeFastModePathError("unsafe Fast Mode lock reparse point")
            fd = msvcrt.open_osfhandle(
                int(native), os.O_RDWR | getattr(os, "O_BINARY", 0),
            )
            converted = True
            handle = os.fdopen(fd, "r+b", buffering=0)
        finally:
            if not converted:
                kernel32.CloseHandle(native)

    try:
        _validate_open_file_identity(handle, path, "lock path")
        _restrict_user_file(path)
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        return handle
    except Exception:
        handle.close()
        raise


def _acquire_file_lock(handle: BinaryIO, timeout_seconds: float | None = None) -> None:
    timeout = FAST_MODE_LOCK_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except (BlockingIOError, OSError) as exc:
            if time.monotonic() >= deadline:
                raise FastModeProfileBusyError(
                    "another Agetha or Medic process is updating Fast Mode"
                ) from exc
            time.sleep(max(0.05, min(0.1, FAST_MODE_LOCK_RETRY_SECONDS)))


def _release_file_lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _revalidate_transaction_paths(
    config: Path,
    snapshot: Path,
    lock_path: Path,
    lock_handle: BinaryIO,
    memory_identity: tuple[int, int] | None,
) -> None:
    """Recheck every mutable path after the operating-system lock is held."""
    _resolved_config, resolved_snapshot = _profile_paths(config, snapshot)
    if _path_key(resolved_snapshot) != _path_key(snapshot):
        raise UnsafeFastModePathError("unsafe Fast Mode snapshot location")
    if config.exists():
        _assert_regular_path(config, "config path")
    memory_dir = snapshot.parent
    try:
        current_memory = os.lstat(memory_dir)
    except OSError as exc:
        raise UnsafeFastModePathError("unsafe Fast Mode memory directory") from exc
    if _is_reparse_or_symlink(memory_dir) or not stat.S_ISDIR(current_memory.st_mode):
        raise UnsafeFastModePathError("unsafe Fast Mode memory directory")
    current_identity = _stat_identity(current_memory)
    if memory_identity is not None and current_identity is not None:
        if current_identity != memory_identity:
            raise UnsafeFastModePathError("unsafe Fast Mode memory directory identity")
    if snapshot.exists():
        _assert_regular_path(snapshot, "snapshot path")
    _validate_open_file_identity(lock_handle, lock_path, "lock path")


@contextmanager
def _transaction(
    config: Path,
    snapshot: Path,
    *,
    timeout_seconds: float | None = None,
) -> Iterator[None]:
    """Serialize changes with bounded locking and post-lock path validation."""
    with _PROFILE_LOCK:
        memory_identity = _assert_safe_mutation_paths(config, snapshot)
        lock_path = snapshot.parent / ".fast_mode.lock"
        handle = _open_verified_lock_file(lock_path)
        locked = False
        try:
            _acquire_file_lock(handle, timeout_seconds)
            locked = True
            _revalidate_transaction_paths(
                config, snapshot, lock_path, handle, memory_identity,
            )
            yield
        finally:
            if locked:
                try:
                    _release_file_lock(handle)
                except OSError:
                    pass
            handle.close()


def _clear_cache(config: Path | None = None) -> None:
    with _CACHE_LOCK:
        if config is None:
            _SNAPSHOT_CACHE.clear()
            _ACTIVE_CACHE.clear()
        else:
            key = _path_key(config)
            _SNAPSHOT_CACHE.pop(key, None)
            _ACTIVE_CACHE.discard(key)


def invalidate_fast_mode_profile_cache(
    config_path: Path | str | None = None,
) -> None:
    _clear_cache(None if config_path is None else Path(config_path))


def _cache_snapshot(
    config: Path,
    snapshot_path: Path,
    snapshot: dict[str, Any],
    *,
    active: bool | None = None,
) -> None:
    with _CACHE_LOCK:
        key = _path_key(config)
        _SNAPSHOT_CACHE[key] = (
            _path_key(snapshot_path), snapshot,
        )
        if active is True:
            _ACTIVE_CACHE.add(key)
        elif active is False:
            _ACTIVE_CACHE.discard(key)


def _cached_snapshot(config: Path, snapshot_path: Path) -> dict[str, Any] | None:
    with _CACHE_LOCK:
        cached = _SNAPSHOT_CACHE.get(_path_key(config))
        if cached is None or cached[0] != _path_key(snapshot_path):
            return None
        return cached[1]


def _one_line_string(value: object, *, nullable: bool = False) -> bool:
    if value is None:
        return nullable
    return isinstance(value, str) and "\r" not in value and "\n" not in value


def validate_fast_mode_override_allowlist(
    overrides: Mapping[str, str] | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Fail closed if the canonical performance profile crosses safety bounds."""
    overrides = FAST_MODE_OVERRIDES if overrides is None else overrides
    supported = default_config_dict()
    unsafe: list[str] = []
    for raw_key, raw_value in overrides.items():
        key = str(raw_key).strip().upper()
        value = str(raw_value)
        if (
            key != raw_key
            or _is_secret_key(key)
            or key in FAST_MODE_FORBIDDEN_KEYS
            or key not in supported
            or not _one_line_string(value)
            or not validate_config_value(key, value, enforce_range=True)
        ):
            unsafe.append(key or "<empty>")
    ordered = tuple(dict.fromkeys(unsafe))
    return not ordered, ordered


def _validate_restorable_value(key: str, value: object) -> bool:
    return (
        _one_line_string(value)
        and validate_config_value(key, str(value), enforce_range=True)
    )


def _validate_snapshot(payload: object) -> tuple[dict[str, Any] | None, str | None]:
    profile_safe, unsafe_keys = validate_fast_mode_override_allowlist()
    if not profile_safe:
        return None, (
            "unsafe Fast Mode profile definition: " + ", ".join(unsafe_keys)
        )
    if not isinstance(payload, dict):
        return None, "snapshot root must be an object"
    allowed_top = {
        "schema_version", "created_at", "app_version", "profile_version",
        "active", "managed_keys",
    }
    if set(payload) != allowed_top:
        return None, "snapshot contains missing or unapproved top-level fields"
    if payload.get("schema_version") != FAST_MODE_SCHEMA_VERSION:
        return None, "unsupported snapshot schema version"
    profile_version = payload.get("profile_version")
    if (
        not isinstance(profile_version, int)
        or isinstance(profile_version, bool)
        or profile_version < 1
        or profile_version > FAST_MODE_PROFILE_VERSION
    ):
        return None, "unsupported Fast Mode profile version"
    if not isinstance(payload.get("active"), bool):
        return None, "invalid snapshot activity state"
    if not _one_line_string(payload.get("app_version")):
        return None, "invalid snapshot app version"
    created_at = payload.get("created_at")
    if not _one_line_string(created_at):
        return None, "invalid snapshot creation time"
    try:
        parsed_time = datetime.fromisoformat(str(created_at))
        if parsed_time.tzinfo is None:
            raise ValueError
    except ValueError:
        return None, "snapshot creation time must include a time zone"

    managed = payload.get("managed_keys")
    if not isinstance(managed, dict) or set(managed) != set(FAST_MODE_OVERRIDES):
        return None, "snapshot managed-key allowlist mismatch"
    for key, target in FAST_MODE_OVERRIDES.items():
        entry = managed.get(key)
        if not isinstance(entry, dict):
            return None, f"invalid managed entry: {key}"
        if not set(entry).issubset({
            "was_present", "original_value", "forced_value", "restore_override",
        }):
            return None, f"unapproved snapshot metadata for: {key}"
        if set(entry) < {"was_present", "original_value", "forced_value"}:
            return None, f"incomplete managed entry: {key}"
        was_present = entry.get("was_present")
        original = entry.get("original_value")
        forced = entry.get("forced_value")
        if not isinstance(was_present, bool):
            return None, f"invalid presence metadata for: {key}"
        if was_present != isinstance(original, str):
            return None, f"invalid original value for: {key}"
        if not _one_line_string(original, nullable=not was_present):
            return None, f"invalid original value for: {key}"
        if not _one_line_string(forced):
            return None, f"invalid forced value for: {key}"
        if was_present and not _validate_restorable_value(key, original):
            return None, f"invalid original value for: {key}"
        if not validate_config_value(key, forced, enforce_range=True):
            return None, f"invalid forced value for: {key}"
        if profile_version == FAST_MODE_PROFILE_VERSION and forced != target:
            return None, f"forced profile value mismatch for: {key}"
        override = entry.get("restore_override")
        if override is not None:
            if not isinstance(override, dict) or set(override) != {"was_present", "value"}:
                return None, f"invalid restoration metadata for: {key}"
            override_present = override.get("was_present")
            override_value = override.get("value")
            if not isinstance(override_present, bool):
                return None, f"invalid restoration presence for: {key}"
            if override_present != isinstance(override_value, str):
                return None, f"invalid restoration value for: {key}"
            if not _one_line_string(override_value, nullable=not override_present):
                return None, f"invalid restoration value for: {key}"
            if override_present and not _validate_restorable_value(key, override_value):
                return None, f"invalid restoration value for: {key}"
    return payload, None


def _load_snapshot(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None
    if _is_reparse_or_symlink(path):
        return None, "snapshot is a symlink or reparse point"
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"snapshot could not be read: {exc}"
    return _validate_snapshot(payload)


def _write_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    valid, error = _validate_snapshot(snapshot)
    if valid is None:
        raise ValueError(error or "invalid Fast Mode snapshot")
    if path.exists() and _is_reparse_or_symlink(path):
        raise OSError("Refusing to replace a symlink or reparse-point snapshot")
    payload = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"
    try:
        _write_atomic_config(path, payload)
    except AtomicWriteError as exc:
        if exc.write_applied:
            raise FastModeVerificationPendingError(
                "snapshot write may have completed; reconciliation must inspect disk"
            ) from exc
        raise
    _restrict_user_file(path)
    verified, verify_error = _load_snapshot(path)
    if verify_error or verified != snapshot:
        raise FastModeVerificationPendingError(
            "snapshot write may have completed; reconciliation must inspect disk"
        )


def _new_snapshot(raw_config: Mapping[str, str]) -> dict[str, Any]:
    app_version = default_config_dict().get("APP_VERSION", "unknown")
    return {
        "schema_version": FAST_MODE_SCHEMA_VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "app_version": app_version,
        "profile_version": FAST_MODE_PROFILE_VERSION,
        "active": True,
        "managed_keys": {
            key: {
                "was_present": key in raw_config,
                "original_value": raw_config.get(key),
                "forced_value": forced,
            }
            for key, forced in FAST_MODE_OVERRIDES.items()
        },
    }


def _config_matches(text: str, expected: Mapping[str, str], absent: set[str] | None = None) -> bool:
    raw = parse_config_document(text)
    if any(raw.get(key) != value for key, value in expected.items()):
        return False
    return not any(key in raw for key in (absent or set()))


def _write_and_verify_config(
    config: Path,
    rendered: str,
    expected: Mapping[str, str],
    absent: set[str] | None = None,
) -> None:
    try:
        write_config_document(config, rendered)
    except AtomicWriteError as exc:
        if exc.write_applied:
            raise FastModeVerificationPendingError(
                "configuration write may have completed; reconciliation must inspect disk"
            ) from exc
        raise
    try:
        verified = config.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise FastModeVerificationPendingError(
            "configuration write may have completed; reconciliation must inspect disk"
        ) from exc
    if not _config_matches(verified, expected, absent):
        raise FastModeVerificationPendingError(
            "configuration write may have completed; reconciliation must inspect disk"
        )


def _snapshot_originals(snapshot: Mapping[str, Any]) -> dict[str, str | None]:
    return {
        key: entry.get("original_value")
        for key, entry in snapshot["managed_keys"].items()
    }


def managed_fast_mode_keys() -> tuple[str, ...]:
    return tuple(FAST_MODE_OVERRIDES)


def inspect_fast_mode_profile(
    config_path: Path | str | None = None,
    snapshot_path: Path | str | None = None,
) -> FastModeProfileInspection:
    unsafe_profile = _unsafe_profile_result()
    if unsafe_profile is not None:
        return FastModeProfileInspection(
            unsafe_profile.status, False, False, 0, {}, dict(FAST_MODE_OVERRIDES),
            unsafe_profile.warnings,
        )
    try:
        config, snapshot_file = _profile_paths(config_path, snapshot_path)
    except (OSError, ValueError) as exc:
        return FastModeProfileInspection(
            "unsafe_path_state", False, False, 0, {}, dict(FAST_MODE_OVERRIDES),
            ("Fast Mode transaction paths failed safety validation.",),
        )
    try:
        raw = read_config_document(config)[1] if config.exists() else {}
    except OSError:
        raw = {}
    requested = _enabled(raw.get("FASTER_MODE"))
    snapshot, error = _load_snapshot(snapshot_file)
    if error:
        _clear_cache(config)
        return FastModeProfileInspection(
            "snapshot_invalid", False, False, 0, {}, dict(FAST_MODE_OVERRIDES),
            (error,),
        )
    if snapshot is None:
        _clear_cache(config)
        return FastModeProfileInspection(
            "snapshot_missing" if requested else "inactive_clean",
            False, True, 0, {}, dict(FAST_MODE_OVERRIDES), (),
        )
    snapshot_active = snapshot["active"] is True
    effective_active = requested and snapshot_active
    _cache_snapshot(config, snapshot_file, snapshot, active=effective_active)
    if not snapshot_active:
        return FastModeProfileInspection(
            "cleanup_pending",
            False,
            True,
            len(snapshot["managed_keys"]),
            _snapshot_originals(snapshot),
            dict(FAST_MODE_OVERRIDES),
            (),
        )
    drifted = requested and any(
        raw.get(key) != forced for key, forced in FAST_MODE_OVERRIDES.items()
    )
    return FastModeProfileInspection(
        "active_drift" if drifted else "active_valid" if requested else "restore_required",
        requested,
        True,
        len(snapshot["managed_keys"]),
        _snapshot_originals(snapshot),
        dict(FAST_MODE_OVERRIDES),
        (),
    )


def is_fast_mode_profile_active(
    config_path: Path | str | None = None,
    snapshot_path: Path | str | None = None,
) -> bool:
    if not validate_fast_mode_override_allowlist()[0]:
        return False
    try:
        config, snapshot_file = _profile_paths(config_path, snapshot_path)
    except (OSError, ValueError):
        return False
    with _CACHE_LOCK:
        cached = _SNAPSHOT_CACHE.get(_path_key(config))
        if (
            cached is not None
            and cached[0] == _path_key(snapshot_file)
            and _path_key(config) in _ACTIVE_CACHE
        ):
            return True
    return inspect_fast_mode_profile(config, snapshot_file).active


def _valid_snapshot_for_helpers(
    config_path: Path | str | None,
    snapshot_path: Path | str | None,
) -> tuple[Path, Path, dict[str, Any] | None]:
    if not validate_fast_mode_override_allowlist()[0]:
        return Path(config_path or CONFIG_PATH), Path(), None
    try:
        config, snapshot_file = _profile_paths(config_path, snapshot_path)
    except (OSError, ValueError):
        return Path(config_path or CONFIG_PATH), Path(), None
    cached = _cached_snapshot(config, snapshot_file)
    if cached is not None:
        return config, snapshot_file, cached
    snapshot, error = _load_snapshot(snapshot_file)
    if snapshot is None or error:
        _clear_cache(config)
        return config, snapshot_file, None
    _cache_snapshot(config, snapshot_file, snapshot)
    return config, snapshot_file, snapshot


def get_fast_mode_original_value(
    key: str,
    config_path: Path | str | None = None,
    snapshot_path: Path | str | None = None,
) -> str | None:
    _config, _snapshot_file, snapshot = _valid_snapshot_for_helpers(
        config_path, snapshot_path,
    )
    entry = (snapshot or {}).get("managed_keys", {}).get(str(key).upper())
    return entry.get("original_value") if isinstance(entry, dict) else None


def get_fast_mode_forced_value(key: str) -> str | None:
    if not validate_fast_mode_override_allowlist()[0]:
        return None
    return FAST_MODE_OVERRIDES.get(str(key).strip().upper())


def get_fast_mode_runtime_overrides(
    *,
    config_path: Path | str | None = None,
    snapshot_path: Path | str | None = None,
    config_enabled: bool = True,
) -> dict[str, str]:
    """Cached effective overlay applied after .env by app_config.parse_config_file."""
    if not config_enabled:
        return {}
    _config, _snapshot_file, snapshot = _valid_snapshot_for_helpers(
        config_path, snapshot_path,
    )
    if snapshot is None or snapshot.get("active") is not True:
        return {}
    return {**FAST_MODE_OVERRIDES, "FASTER_MODE": "yes"}


def _cleanup_inactive_snapshot_locked(
    config: Path,
    snapshot_file: Path,
    snapshot: dict[str, Any],
    *,
    required_for_activation: bool = False,
) -> FastModeReconcileResult:
    """Remove a completed-restoration marker without touching config values."""
    if snapshot.get("active") is not False:
        return FastModeReconcileResult(
            "snapshot_invalid", error="cleanup requires an inactive snapshot",
        )
    try:
        snapshot_file.unlink()
        _fsync_parent_directory(snapshot_file)
    except FileNotFoundError:
        pass
    except OSError as exc:
        _cache_snapshot(config, snapshot_file, snapshot, active=False)
        logger.warning("Fast Mode completed snapshot cleanup remains pending")
        warning = "Restoration is complete; snapshot cleanup remains pending"
        _audit_fast_mode(config, "fast_mode_cleanup_pending", "cleanup_pending")
        if required_for_activation:
            return FastModeReconcileResult(
                "snapshot_cleanup_failed", warnings=(warning,), error=str(exc),
            )
        return FastModeReconcileResult("cleanup_pending", warnings=(warning,))
    _clear_cache(config)
    logger.info("Fast Mode completed snapshot removed")
    _audit_fast_mode(config, "fast_mode_cleanup_completed", "cleanup_completed")
    return FastModeReconcileResult("cleanup_completed")


def _activate_locked(
    config: Path,
    snapshot_file: Path,
    prospective_text: str | None = None,
) -> FastModeReconcileResult:
    unsafe_profile = _unsafe_profile_result()
    if unsafe_profile is not None:
        _audit_fast_mode(config, "fast_mode_activation_failed", unsafe_profile.status)
        return unsafe_profile
    _audit_fast_mode(config, "fast_mode_activation_started", "started")
    existing, error = _load_snapshot(snapshot_file)
    if error:
        _clear_cache(config)
        result = FastModeReconcileResult("snapshot_invalid", error=error)
        _audit_fast_mode(config, "fast_mode_snapshot_invalid", result.status)
        return result
    if existing is not None:
        if existing.get("active") is False:
            cleanup = _cleanup_inactive_snapshot_locked(
                config,
                snapshot_file,
                existing,
                required_for_activation=True,
            )
            if not cleanup.ok:
                return cleanup
            existing = None
        else:
            text = prospective_text
            if text is None:
                text = read_config_document(config)[0]
            return _repair_active_locked(config, snapshot_file, existing, text)
    if prospective_text is None:
        prospective_text = read_config_document(config)[0]
    raw = parse_config_document(prospective_text)
    typed_ok, invalid_keys = validate_config_document(
        prospective_text, (*FAST_MODE_OVERRIDES, "FASTER_MODE"),
    )
    range_invalid = tuple(
        key for key in FAST_MODE_OVERRIDES
        if key in raw and not validate_config_value(
            key, raw[key], enforce_range=True,
        )
    )
    invalid_originals = tuple(dict.fromkeys((*invalid_keys, *range_invalid)))
    if not typed_ok or invalid_originals:
        names = ", ".join(invalid_originals)
        logger.warning("Fast Mode activation validation failed for: %s", names)
        result = FastModeReconcileResult(
            "invalid_updates",
            warnings=(f"Original values failed typed validation for: {names}",),
            error="managed settings must be valid before Fast Mode can be enabled",
        )
        _audit_fast_mode(config, "fast_mode_activation_failed", result.status)
        return result
    snapshot = _new_snapshot(raw)
    try:
        _write_snapshot(snapshot_file, snapshot)
    except Exception as exc:
        _clear_cache(config)
        if isinstance(exc, FastModeVerificationPendingError):
            raise
        logger.warning("Fast Mode snapshot write failed")
        result = FastModeReconcileResult("snapshot_write_failed", error=str(exc))
        _audit_fast_mode(config, "fast_mode_activation_failed", result.status)
        return result

    updates = {**FAST_MODE_OVERRIDES, "FASTER_MODE": "yes"}
    rendered = render_config_document(prospective_text, updates)
    try:
        _write_and_verify_config(config, rendered, updates)
    except Exception as exc:
        if isinstance(exc, FastModeVerificationPendingError):
            _clear_cache(config)
            raise
        # Snapshot-first is intentional: it retains the originals for recovery.
        _cache_snapshot(config, snapshot_file, snapshot, active=False)
        logger.warning("Fast Mode activation config write failed")
        result = FastModeReconcileResult("config_write_failed", error=str(exc))
        _audit_fast_mode(config, "fast_mode_activation_failed", result.status)
        return result
    _cache_snapshot(config, snapshot_file, snapshot, active=True)
    changed = tuple(
        key for key, value in updates.items() if raw.get(key) != value
    )
    logger.info("Fast Mode activated: %d managed settings", len(FAST_MODE_OVERRIDES))
    result = FastModeReconcileResult("activated", changed)
    _audit_fast_mode(
        config, "fast_mode_activated", result.status, changed_keys=changed,
    )
    return result


def _state_matches_original(entry: Mapping[str, Any], present: bool, value: str | None) -> bool:
    if bool(entry["was_present"]) != present:
        return False
    return not present or value == entry.get("original_value")


def _set_restore_override(
    entry: dict[str, Any], present: bool, value: str | None,
) -> bool:
    desired = {"was_present": present, "value": value if present else None}
    if entry.get("restore_override") == desired:
        return False
    entry["restore_override"] = desired
    return True


def _clear_restore_override(entry: dict[str, Any]) -> bool:
    if "restore_override" not in entry:
        return False
    del entry["restore_override"]
    return True


def _repair_active_locked(
    config: Path,
    snapshot_file: Path,
    snapshot: dict[str, Any],
    text: str,
    submitted_updates: Mapping[str, str] | None = None,
) -> FastModeReconcileResult:
    metadata_changed = False
    conflicts: list[str] = []
    submitted_updates = dict(submitted_updates or {})
    submitted_preferences: set[str] = set()

    # Unmanaged dashboard edits are part of the same config replacement. Managed
    # forced-value echoes are ignored; a third value becomes the eventual restore
    # preference without changing the active effective value.
    unmanaged = {
        key: value for key, value in submitted_updates.items()
        if key not in FAST_MODE_OVERRIDES and key != "FASTER_MODE"
    }
    if unmanaged:
        text = render_config_document(text, unmanaged)
    for key, value in submitted_updates.items():
        if key not in FAST_MODE_OVERRIDES:
            continue
        entry = snapshot["managed_keys"][key]
        if value in {FAST_MODE_OVERRIDES[key], entry.get("forced_value")}:
            continue
        submitted_preferences.add(key)
        if _state_matches_original(entry, True, value):
            if _clear_restore_override(entry):
                metadata_changed = True
            continue
        if _set_restore_override(entry, True, value):
            metadata_changed = True
        conflicts.append(key)

    raw = parse_config_document(text)
    drifted: list[str] = []
    migrating = snapshot["profile_version"] != FAST_MODE_PROFILE_VERSION
    if migrating:
        unsafe_profile = _unsafe_profile_result()
        if unsafe_profile is not None:
            return unsafe_profile
    for key, target in FAST_MODE_OVERRIDES.items():
        entry = snapshot["managed_keys"][key]
        present = key in raw
        value = raw.get(key)
        old_forced = entry.get("forced_value")
        if present and value == target:
            continue
        drifted.append(key)
        if present and value == old_forced:
            continue
        if _state_matches_original(entry, present, value):
            if key not in submitted_preferences and _clear_restore_override(entry):
                metadata_changed = True
            continue
        if key not in submitted_preferences:
            if _set_restore_override(entry, present, value):
                metadata_changed = True
            conflicts.append(key)

    # Preserve a newly discovered preference before overwriting drift on disk.
    if metadata_changed:
        try:
            _write_snapshot(snapshot_file, snapshot)
        except Exception as exc:
            _clear_cache(config)
            if isinstance(exc, FastModeVerificationPendingError):
                raise
            logger.warning("Fast Mode restore-preference snapshot write failed")
            return FastModeReconcileResult("snapshot_write_failed", error=str(exc))

    updates = {**FAST_MODE_OVERRIDES, "FASTER_MODE": "yes"}
    rendered = render_config_document(text, updates)
    config_changed = rendered != text
    if config_changed:
        try:
            _write_and_verify_config(config, rendered, updates)
        except Exception as exc:
            if isinstance(exc, FastModeVerificationPendingError):
                _clear_cache(config)
                raise
            _cache_snapshot(
                config, snapshot_file, snapshot,
                active=_enabled(parse_config_document(text).get("FASTER_MODE")),
            )
            logger.warning("Fast Mode drift repair config write failed")
            return FastModeReconcileResult("config_write_failed", error=str(exc))

    if migrating:
        snapshot["profile_version"] = FAST_MODE_PROFILE_VERSION
        for key, target in FAST_MODE_OVERRIDES.items():
            snapshot["managed_keys"][key]["forced_value"] = target
        try:
            # Config-first migration leaves the old snapshot recoverable if this
            # metadata write fails; the next reconcile recognizes both values.
            _write_snapshot(snapshot_file, snapshot)
        except Exception as exc:
            if isinstance(exc, FastModeVerificationPendingError):
                _clear_cache(config)
                raise
            _cache_snapshot(config, snapshot_file, snapshot, active=True)
            logger.warning("Fast Mode profile migration metadata write failed")
            return FastModeReconcileResult("snapshot_write_failed", error=str(exc))

    _cache_snapshot(config, snapshot_file, snapshot, active=True)
    changed = tuple(dict.fromkeys((*unmanaged, *drifted)))
    warning_tuple = tuple(
        f"Preserved a user-edited restore value for {key}" for key in dict.fromkeys(conflicts)
    )
    if drifted or migrating:
        status = "active_repaired"
    elif unmanaged or conflicts:
        status = "config_updated"
    else:
        status = "active_valid"
    if drifted:
        logger.info("Fast Mode drift repaired: %s", ", ".join(drifted))
    if migrating:
        logger.info("Fast Mode profile migrated to version %d", FAST_MODE_PROFILE_VERSION)
        _audit_fast_mode(config, "fast_mode_profile_migrated", status)
    if conflicts:
        logger.info(
            "Fast Mode restore preferences preserved: %s",
            ", ".join(dict.fromkeys(conflicts)),
        )
    result = FastModeReconcileResult(status, changed, warning_tuple)
    if status == "active_repaired":
        _audit_fast_mode(
            config,
            "fast_mode_repaired",
            status,
            changed_keys=changed,
            conflict_keys=tuple(dict.fromkeys(conflicts)),
        )
    return result


def _restore_target(
    entry: Mapping[str, Any],
    current_present: bool,
    current_value: str | None,
    target_forced: str,
    submitted_value: str | None,
    submitted: bool,
) -> tuple[bool, str | None, bool]:
    """Return (present, value, conflict_preserved)."""
    old_forced = entry.get("forced_value")
    if submitted and submitted_value not in {target_forced, old_forced}:
        return True, submitted_value, True

    if current_present and current_value in {target_forced, old_forced}:
        override = entry.get("restore_override")
        if isinstance(override, dict):
            return bool(override["was_present"]), override.get("value"), True
        return bool(entry["was_present"]), entry.get("original_value"), False

    if _state_matches_original(entry, current_present, current_value):
        return current_present, current_value, False

    # Missing or third-valued managed keys are intentional compare-and-swap
    # conflicts. Preserve them rather than silently restoring stale originals.
    return current_present, current_value, True


def _deactivate_locked(
    config: Path,
    snapshot_file: Path,
    snapshot: dict[str, Any],
    text: str,
    submitted_updates: Mapping[str, str] | None = None,
) -> FastModeReconcileResult:
    _audit_fast_mode(config, "fast_mode_restoration_started", "started")
    submitted_updates = dict(submitted_updates or {})
    unmanaged = {
        key: value for key, value in submitted_updates.items()
        if key not in FAST_MODE_OVERRIDES and key != "FASTER_MODE"
    }
    if unmanaged:
        text = render_config_document(text, unmanaged)
    raw = parse_config_document(text)

    restore_updates: dict[str, str] = {"FASTER_MODE": "no"}
    removals: set[str] = set()
    conflicts: list[str] = []
    changed: list[str] = list(unmanaged)
    for key, target in FAST_MODE_OVERRIDES.items():
        entry = snapshot["managed_keys"][key]
        present, value, conflict = _restore_target(
            entry,
            key in raw,
            raw.get(key),
            target,
            submitted_updates.get(key),
            key in submitted_updates,
        )
        if present:
            restore_updates[key] = str(value)
        else:
            removals.add(key)
        if conflict:
            conflicts.append(key)
        if (key in raw) != present or (present and raw.get(key) != value):
            changed.append(key)

    rendered = render_config_document(text, restore_updates, removals)
    expected = dict(restore_updates)
    typed_ok, invalid_keys = validate_config_document(
        rendered, (*FAST_MODE_OVERRIDES, "FASTER_MODE"),
    )
    restored_raw = parse_config_document(rendered)
    range_invalid = tuple(
        key for key in FAST_MODE_OVERRIDES
        if key in restored_raw and not validate_config_value(
            key, restored_raw[key], enforce_range=True,
        )
    )
    invalid_restored = tuple(dict.fromkeys((*invalid_keys, *range_invalid)))
    if not typed_ok or invalid_restored:
        names = ", ".join(invalid_restored)
        logger.warning("Fast Mode restoration validation failed for: %s", names)
        _cache_snapshot(
            config, snapshot_file, snapshot,
            active=_enabled(raw.get("FASTER_MODE")),
        )
        return FastModeReconcileResult(
            "config_write_failed",
            warnings=(f"Restored values failed typed validation for: {names}",),
            error="restored managed settings failed typed validation",
        )
    try:
        _write_and_verify_config(config, rendered, expected, removals)
    except Exception as exc:
        # Replacement may have succeeded before the verification read failed.
        # Never cache the pre-write switch as authoritative in that ambiguous
        # state; the next helper call must inspect the actual files.
        _clear_cache(config)
        if isinstance(exc, FastModeVerificationPendingError):
            raise
        logger.warning("Fast Mode restoration config write failed")
        return FastModeReconcileResult("config_write_failed", error=str(exc))

    warnings = tuple(
        f"Preserved a user-edited restore value for {key}"
        for key in dict.fromkeys(conflicts)
    )
    snapshot["active"] = False
    try:
        # Persist completion before deletion so a failed unlink or crash becomes
        # cleanup-only work and can never replay stale restoration values.
        _write_snapshot(snapshot_file, snapshot)
    except Exception as exc:
        if isinstance(exc, FastModeVerificationPendingError):
            snapshot["active"] = True
            _clear_cache(config)
            raise
        snapshot["active"] = True
        _cache_snapshot(config, snapshot_file, snapshot, active=False)
        logger.warning("Fast Mode cleanup marker write failed after restoration")
        return FastModeReconcileResult(
            "snapshot_write_failed",
            tuple(dict.fromkeys(changed)),
            (*warnings, "Configuration was restored, but cleanup state could not be saved"),
            str(exc),
        )
    _cache_snapshot(config, snapshot_file, snapshot, active=False)
    cleanup = _cleanup_inactive_snapshot_locked(config, snapshot_file, snapshot)
    if cleanup.status == "cleanup_pending":
        return FastModeReconcileResult(
            "restored_snapshot_retained",
            tuple(dict.fromkeys(changed)),
            (*warnings, *cleanup.warnings),
        )
    if conflicts:
        logger.info(
            "Fast Mode restored with preserved preferences: %s",
            ", ".join(dict.fromkeys(conflicts)),
        )
    else:
        logger.info("Fast Mode restored: %d managed settings", len(FAST_MODE_OVERRIDES))
    result = FastModeReconcileResult(
        "restore_conflict_preserved" if conflicts else "restored",
        tuple(dict.fromkeys(changed)),
        warnings,
    )
    _audit_fast_mode(
        config,
        "fast_mode_restore_conflict_preserved" if conflicts else "fast_mode_restored",
        result.status,
        changed_keys=result.changed_keys,
        conflict_keys=tuple(dict.fromkeys(conflicts)),
    )
    return result


def _quarantine_snapshot(path: Path) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f")
    quarantined = path.with_name(f"fast_mode_snapshot.invalid-{stamp}.json")
    os.replace(path, quarantined)
    _fsync_parent_directory(quarantined)
    _restrict_user_file(quarantined)
    return quarantined


def activate_fast_mode(
    config_path: Path | str | None = None,
    snapshot_path: Path | str | None = None,
) -> FastModeReconcileResult:
    config = Path(config_path or CONFIG_PATH)
    unsafe_profile = _unsafe_profile_result()
    if unsafe_profile is not None:
        return unsafe_profile
    try:
        config, snapshot_file = _profile_paths(config_path, snapshot_path)
        with _transaction(config, snapshot_file):
            ensure_config_file(config, write_if_missing=True)
            return _activate_locked(config, snapshot_file)
    except Exception as exc:
        return _result_from_exception(config, exc)


def deactivate_fast_mode(
    config_path: Path | str | None = None,
    snapshot_path: Path | str | None = None,
) -> FastModeReconcileResult:
    config = Path(config_path or CONFIG_PATH)
    unsafe_profile = _unsafe_profile_result()
    if unsafe_profile is not None:
        return unsafe_profile
    try:
        config, snapshot_file = _profile_paths(config_path, snapshot_path)
        with _transaction(config, snapshot_file):
            ensure_config_file(config, write_if_missing=True)
            text, _raw = read_config_document(config)
            snapshot, error = _load_snapshot(snapshot_file)
            if error:
                _clear_cache(config)
                return FastModeReconcileResult("snapshot_invalid", error=error)
            if snapshot is None:
                rendered = render_config_document(text, {"FASTER_MODE": "no"})
                if rendered != text:
                    _write_and_verify_config(config, rendered, {"FASTER_MODE": "no"})
                    _clear_cache(config)
                    return FastModeReconcileResult("inactive_clean", ("FASTER_MODE",))
                _clear_cache(config)
                return FastModeReconcileResult("inactive_clean")
            if snapshot.get("active") is False:
                rendered = render_config_document(text, {"FASTER_MODE": "no"})
                changed = rendered != text
                if changed:
                    _write_and_verify_config(config, rendered, {"FASTER_MODE": "no"})
                cleanup = _cleanup_inactive_snapshot_locked(
                    config, snapshot_file, snapshot,
                )
                return FastModeReconcileResult(
                    cleanup.status,
                    ("FASTER_MODE",) if changed else (),
                    cleanup.warnings,
                    cleanup.error,
                )
            return _deactivate_locked(config, snapshot_file, snapshot, text)
    except Exception as exc:
        return _result_from_exception(config, exc)


def reconcile_fast_mode_profile(
    config_path: Path | str | None = None,
    snapshot_path: Path | str | None = None,
) -> FastModeReconcileResult:
    config = Path(config_path or CONFIG_PATH)
    unsafe_profile = _unsafe_profile_result()
    if unsafe_profile is not None:
        return unsafe_profile
    try:
        config, snapshot_file = _profile_paths(config_path, snapshot_path)
        # The overwhelmingly common disabled state is genuinely read-only: it
        # must not require a writable memory directory or create a lock file.
        if config.exists() and not snapshot_file.exists():
            _text, raw = read_config_document(config)
            if not _enabled(raw.get("FASTER_MODE")):
                _clear_cache(config)
                return FastModeReconcileResult("inactive_clean")
        with _transaction(config, snapshot_file):
            ensure_config_file(config, write_if_missing=True)
            text, raw = read_config_document(config)
            requested = _enabled(raw.get("FASTER_MODE"))
            snapshot, error = _load_snapshot(snapshot_file)
            if error:
                _clear_cache(config)
                logger.warning("Fast Mode snapshot validation failed")
                _audit_fast_mode(
                    config, "fast_mode_snapshot_invalid", "snapshot_invalid",
                )
                if requested:
                    return FastModeReconcileResult("snapshot_invalid", error=error)
                try:
                    quarantined = _quarantine_snapshot(snapshot_file)
                except OSError as exc:
                    return FastModeReconcileResult("snapshot_invalid", error=str(exc))
                logger.warning("Invalid inactive Fast Mode snapshot quarantined")
                _audit_fast_mode(
                    config, "fast_mode_snapshot_quarantined", "snapshot_quarantined",
                )
                return FastModeReconcileResult(
                    "snapshot_quarantined",
                    warnings=(f"Invalid Fast Mode snapshot quarantined as {quarantined.name}",),
                )
            if snapshot is None:
                _clear_cache(config)
                if not requested:
                    return FastModeReconcileResult("inactive_clean")
                return _activate_locked(config, snapshot_file, text)
            if snapshot.get("active") is False:
                cleanup = _cleanup_inactive_snapshot_locked(
                    config,
                    snapshot_file,
                    snapshot,
                    required_for_activation=requested,
                )
                if not cleanup.ok:
                    return cleanup
                if requested:
                    return _activate_locked(config, snapshot_file, text)
                return cleanup
            if not requested:
                return _deactivate_locked(config, snapshot_file, snapshot, text)
            return _repair_active_locked(config, snapshot_file, snapshot, text)
    except Exception as exc:
        return _result_from_exception(config, exc)


def _normalise_submitted_updates(updates: Mapping[str, object]) -> dict[str, str]:
    # Reuse app_config's structural validation (secret refusal and newline guard)
    # without writing anything.
    rendered = render_config_document("", updates)
    return parse_config_document(rendered)


def apply_config_updates_with_fast_mode(
    updates: Mapping[str, object],
    config_path: Path | str | None = None,
    snapshot_path: Path | str | None = None,
) -> FastModeReconcileResult:
    """Apply dashboard/config updates and any Fast Mode transition coherently."""
    try:
        submitted = _normalise_submitted_updates(updates)
    except (TypeError, ValueError) as exc:
        return FastModeReconcileResult("invalid_updates", error=str(exc))
    if "FASTER_MODE" in submitted and str(submitted["FASTER_MODE"]).lower() not in {
        "1", "0", "yes", "no", "true", "false", "on", "off",
    }:
        return FastModeReconcileResult("invalid_updates", error="Invalid FASTER_MODE boolean")

    config = Path(config_path or CONFIG_PATH)
    unsafe_profile = _unsafe_profile_result()
    if unsafe_profile is not None:
        return unsafe_profile
    try:
        config, snapshot_file = _profile_paths(config_path, snapshot_path)
        with _transaction(config, snapshot_file):
            ensure_config_file(config, write_if_missing=True)
            text, raw = read_config_document(config)
            currently_requested = _enabled(raw.get("FASTER_MODE"))
            desired = _enabled(submitted.get("FASTER_MODE")) if "FASTER_MODE" in submitted else currently_requested
            snapshot, error = _load_snapshot(snapshot_file)
            if error:
                _clear_cache(config)
                logger.warning("Fast Mode snapshot validation failed")
                _audit_fast_mode(
                    config, "fast_mode_snapshot_invalid", "snapshot_invalid",
                )
                if desired:
                    return FastModeReconcileResult("snapshot_invalid", error=error)
                try:
                    quarantined = _quarantine_snapshot(snapshot_file)
                except OSError as exc:
                    return FastModeReconcileResult("snapshot_invalid", error=str(exc))
                logger.warning("Invalid inactive Fast Mode snapshot quarantined")
                _audit_fast_mode(
                    config, "fast_mode_snapshot_quarantined", "snapshot_quarantined",
                )
                text = render_config_document(text, submitted)
                try:
                    _write_and_verify_config(config, text, submitted)
                except Exception as exc:
                    if isinstance(exc, FastModeVerificationPendingError):
                        raise
                    return FastModeReconcileResult("config_write_failed", error=str(exc))
                return FastModeReconcileResult(
                    "snapshot_quarantined",
                    tuple(submitted),
                    (f"Invalid Fast Mode snapshot quarantined as {quarantined.name}",),
                )

            if snapshot is not None and snapshot.get("active") is False:
                if desired:
                    cleanup = _cleanup_inactive_snapshot_locked(
                        config,
                        snapshot_file,
                        snapshot,
                        required_for_activation=True,
                    )
                    if not cleanup.ok:
                        return cleanup
                    prospective = render_config_document(text, submitted)
                    return _activate_locked(config, snapshot_file, prospective)

                rendered = render_config_document(text, submitted)
                if rendered != text:
                    try:
                        _write_and_verify_config(config, rendered, submitted)
                    except Exception as exc:
                        if isinstance(exc, FastModeVerificationPendingError):
                            raise
                        return FastModeReconcileResult(
                            "config_write_failed", error=str(exc),
                        )
                cleanup = _cleanup_inactive_snapshot_locked(
                    config, snapshot_file, snapshot,
                )
                return FastModeReconcileResult(
                    cleanup.status,
                    tuple(submitted) if rendered != text else (),
                    cleanup.warnings,
                    cleanup.error,
                )

            if desired:
                if snapshot is None:
                    # Prospective managed edits are captured as the originals,
                    # then all forced values and FASTER_MODE are written once.
                    prospective = render_config_document(text, submitted)
                    return _activate_locked(config, snapshot_file, prospective)
                return _repair_active_locked(
                    config, snapshot_file, snapshot, text, submitted,
                )

            if snapshot is not None:
                return _deactivate_locked(
                    config, snapshot_file, snapshot, text, submitted,
                )

            rendered = render_config_document(text, submitted)
            if rendered == text:
                _clear_cache(config)
                return FastModeReconcileResult("inactive_clean")
            try:
                _write_and_verify_config(config, rendered, submitted)
            except Exception as exc:
                if isinstance(exc, FastModeVerificationPendingError):
                    raise
                return FastModeReconcileResult("config_write_failed", error=str(exc))
            _clear_cache(config)
            return FastModeReconcileResult("config_updated", tuple(submitted))
    except Exception as exc:
        return _result_from_exception(config, exc)


# Short aliases for callers that used the implementation-plan names.
reconcile_fast_mode = reconcile_fast_mode_profile
inspect_fast_mode = inspect_fast_mode_profile
restore_fast_mode_profile = deactivate_fast_mode


def fast_mode_cli_exit_code(status: str) -> int:
    normalized = str(status or "unknown").strip().lower()
    if normalized == "profile_busy":
        return 3
    if normalized in {
        "snapshot_invalid", "unsafe_path_state", "unsafe_profile_definition",
        "config_write_failed", "snapshot_write_failed", "snapshot_cleanup_failed",
        "invalid_updates", "unavailable", "unknown",
    }:
        return 2
    if normalized in {
        "activation_required", "snapshot_missing", "active_drift", "repair_required",
        "restore_required", "restoration_pending", "cleanup_pending",
        "restored_snapshot_retained", "verification_pending",
    }:
        return 1
    return 0


def _cli_payload(result: object) -> dict[str, object]:
    status = str(getattr(result, "status", "unknown"))
    warnings = tuple(getattr(result, "warnings", ()) or ())
    changed = tuple(str(key) for key in (getattr(result, "changed_keys", ()) or ()))
    active_value = getattr(result, "active", None)
    if active_value is None:
        active_value = is_fast_mode_profile_active()
    return {
        "status": status,
        "ok": fast_mode_cli_exit_code(status) == 0,
        "active": bool(active_value),
        "managed_count": len(FAST_MODE_OVERRIDES),
        "changed_keys": changed,
        "warning_count": len(warnings),
    }


def main(argv: list[str] | None = None) -> int:
    """Secret-free recovery CLI for Windows and supported Linux systems."""
    args = list(sys.argv[1:] if argv is None else argv)
    command = str(args[0] if args else "status").strip().lower()
    if command == "status":
        result: object = inspect_fast_mode_profile()
    elif command == "reconcile":
        result = reconcile_fast_mode_profile()
    elif command == "restore":
        result = restore_fast_mode_profile()
    else:
        result = FastModeReconcileResult(
            "invalid_updates", error="expected status, reconcile, or restore",
        )
    payload = _cli_payload(result)
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return fast_mode_cli_exit_code(str(payload["status"]))


if __name__ == "__main__":
    raise SystemExit(main())
