"""Atomic, reversible Fast Mode configuration profile.

This module deliberately depends only on the standard library and app_config's
low-level document helpers. In particular it must not import ``agetha.utils``:
utils loads and caches settings at import time, before startup reconciliation.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agetha.app_config import (
    CONFIG_PATH,
    FAST_MODE_OVERRIDES,
    _write_atomic_config,
    default_config_dict,
    ensure_config_file,
    parse_config_document,
    read_config_document,
    render_config_document,
    validate_config_document,
    write_config_document,
)


FAST_MODE_SCHEMA_VERSION = 1
FAST_MODE_PROFILE_VERSION = 1
FAST_MODE_SNAPSHOT_NAME = "fast_mode_snapshot.json"

logger = logging.getLogger("Agetha")

_FALSE_STATUSES = frozenset({
    "config_write_failed",
    "invalid_updates",
    "snapshot_cleanup_failed",
    "snapshot_invalid",
    "snapshot_write_failed",
})


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


def _enabled(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "yes", "true", "on"}


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


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
        raise ValueError(
            f"Fast Mode snapshot must be {expected}; arbitrary state paths are refused"
        )
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


def _assert_safe_mutation_paths(config: Path, snapshot: Path) -> None:
    if config.exists() and _is_reparse_or_symlink(config):
        raise OSError("Refusing to mutate a symlink or reparse-point config.txt")
    memory_dir = snapshot.parent
    if memory_dir.exists() and _is_reparse_or_symlink(memory_dir):
        raise OSError("Refusing to use a symlink or reparse-point memory directory")
    memory_dir.mkdir(parents=True, exist_ok=True)
    if _is_reparse_or_symlink(memory_dir):
        raise OSError("Refusing to use a symlink or reparse-point memory directory")
    if snapshot.exists() and _is_reparse_or_symlink(snapshot):
        raise OSError("Refusing to follow a symlink or reparse-point Fast Mode snapshot")


def _restrict_user_file(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # mkstemp already creates 0600 files on POSIX. Windows ACLs inherit from
        # the containing user directory, and chmod support varies by filesystem.
        pass


@contextmanager
def _transaction(config: Path, snapshot: Path) -> Iterator[None]:
    """Serialize profile changes in-process and, where available, cross-process."""
    with _PROFILE_LOCK:
        _assert_safe_mutation_paths(config, snapshot)
        lock_path = snapshot.parent / ".fast_mode.lock"
        if lock_path.exists() and _is_reparse_or_symlink(lock_path):
            raise OSError("Refusing a symlink or reparse-point Fast Mode lock")
        handle = lock_path.open("a+b")
        _restrict_user_file(lock_path)
        locked = False
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                locked = True
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                locked = True
            yield
        finally:
            if locked:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
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


def _validate_snapshot(payload: object) -> tuple[dict[str, Any] | None, str | None]:
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
    _write_atomic_config(path, payload)
    _restrict_user_file(path)


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
    write_config_document(config, rendered)
    verified = config.read_text(encoding="utf-8", errors="strict")
    if not _config_matches(verified, expected, absent):
        raise OSError("atomic config verification failed")


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
    try:
        config, snapshot_file = _profile_paths(config_path, snapshot_path)
    except (OSError, ValueError) as exc:
        return FastModeProfileInspection(
            "snapshot_invalid", False, False, 0, {}, dict(FAST_MODE_OVERRIDES),
            (str(exc),),
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
    except FileNotFoundError:
        pass
    except OSError as exc:
        _cache_snapshot(config, snapshot_file, snapshot, active=False)
        logger.warning("Fast Mode completed snapshot cleanup remains pending")
        warning = "Restoration is complete; snapshot cleanup remains pending"
        if required_for_activation:
            return FastModeReconcileResult(
                "snapshot_cleanup_failed", warnings=(warning,), error=str(exc),
            )
        return FastModeReconcileResult("cleanup_pending", warnings=(warning,))
    _clear_cache(config)
    logger.info("Fast Mode completed snapshot removed")
    return FastModeReconcileResult("cleanup_completed")


def _activate_locked(
    config: Path,
    snapshot_file: Path,
    prospective_text: str | None = None,
) -> FastModeReconcileResult:
    existing, error = _load_snapshot(snapshot_file)
    if error:
        _clear_cache(config)
        return FastModeReconcileResult("snapshot_invalid", error=error)
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
    typed_ok, invalid_keys = validate_config_document(
        prospective_text, (*FAST_MODE_OVERRIDES, "FASTER_MODE"),
    )
    if not typed_ok:
        names = ", ".join(invalid_keys)
        logger.warning("Fast Mode activation validation failed for: %s", names)
        return FastModeReconcileResult(
            "invalid_updates",
            warnings=(f"Original values failed typed validation for: {names}",),
            error="managed settings must be valid before Fast Mode can be enabled",
        )
    raw = parse_config_document(prospective_text)
    snapshot = _new_snapshot(raw)
    try:
        _write_snapshot(snapshot_file, snapshot)
    except Exception as exc:
        _clear_cache(config)
        logger.warning("Fast Mode snapshot write failed")
        return FastModeReconcileResult("snapshot_write_failed", error=str(exc))

    updates = {**FAST_MODE_OVERRIDES, "FASTER_MODE": "yes"}
    rendered = render_config_document(prospective_text, updates)
    try:
        _write_and_verify_config(config, rendered, updates)
    except Exception as exc:
        # Snapshot-first is intentional: it retains the originals for recovery.
        _cache_snapshot(config, snapshot_file, snapshot, active=False)
        logger.warning("Fast Mode activation config write failed")
        return FastModeReconcileResult("config_write_failed", error=str(exc))
    _cache_snapshot(config, snapshot_file, snapshot, active=True)
    changed = tuple(
        key for key, value in updates.items() if raw.get(key) != value
    )
    logger.info("Fast Mode activated: %d managed settings", len(FAST_MODE_OVERRIDES))
    return FastModeReconcileResult("activated", changed)


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
            logger.warning("Fast Mode restore-preference snapshot write failed")
            return FastModeReconcileResult("snapshot_write_failed", error=str(exc))

    updates = {**FAST_MODE_OVERRIDES, "FASTER_MODE": "yes"}
    rendered = render_config_document(text, updates)
    config_changed = rendered != text
    if config_changed:
        try:
            _write_and_verify_config(config, rendered, updates)
        except Exception as exc:
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
    if conflicts:
        logger.info(
            "Fast Mode restore preferences preserved: %s",
            ", ".join(dict.fromkeys(conflicts)),
        )
    return FastModeReconcileResult(status, changed, warning_tuple)


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
    if not typed_ok:
        names = ", ".join(invalid_keys)
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
    return FastModeReconcileResult(
        "restore_conflict_preserved" if conflicts else "restored",
        tuple(dict.fromkeys(changed)),
        warnings,
    )


def _quarantine_snapshot(path: Path) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f")
    quarantined = path.with_name(f"fast_mode_snapshot.invalid-{stamp}.json")
    os.replace(path, quarantined)
    _restrict_user_file(quarantined)
    return quarantined


def activate_fast_mode(
    config_path: Path | str | None = None,
    snapshot_path: Path | str | None = None,
) -> FastModeReconcileResult:
    try:
        config, snapshot_file = _profile_paths(config_path, snapshot_path)
        with _transaction(config, snapshot_file):
            ensure_config_file(config, write_if_missing=True)
            return _activate_locked(config, snapshot_file)
    except Exception as exc:
        return FastModeReconcileResult("config_write_failed", error=str(exc))


def deactivate_fast_mode(
    config_path: Path | str | None = None,
    snapshot_path: Path | str | None = None,
) -> FastModeReconcileResult:
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
        return FastModeReconcileResult("config_write_failed", error=str(exc))


def reconcile_fast_mode_profile(
    config_path: Path | str | None = None,
    snapshot_path: Path | str | None = None,
) -> FastModeReconcileResult:
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
                if requested:
                    return FastModeReconcileResult("snapshot_invalid", error=error)
                try:
                    quarantined = _quarantine_snapshot(snapshot_file)
                except OSError as exc:
                    return FastModeReconcileResult("snapshot_invalid", error=str(exc))
                logger.warning("Invalid inactive Fast Mode snapshot quarantined")
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
        return FastModeReconcileResult("config_write_failed", error=str(exc))


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
                if desired:
                    return FastModeReconcileResult("snapshot_invalid", error=error)
                try:
                    quarantined = _quarantine_snapshot(snapshot_file)
                except OSError as exc:
                    return FastModeReconcileResult("snapshot_invalid", error=str(exc))
                logger.warning("Invalid inactive Fast Mode snapshot quarantined")
                text = render_config_document(text, submitted)
                try:
                    _write_and_verify_config(config, text, submitted)
                except Exception as exc:
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
                return FastModeReconcileResult("config_write_failed", error=str(exc))
            _clear_cache(config)
            return FastModeReconcileResult("config_updated", tuple(submitted))
    except Exception as exc:
        return FastModeReconcileResult("config_write_failed", error=str(exc))


# Short aliases for callers that used the implementation-plan names.
reconcile_fast_mode = reconcile_fast_mode_profile
inspect_fast_mode = inspect_fast_mode_profile
