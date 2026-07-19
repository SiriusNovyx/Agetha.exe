"""
win_integration.py — Safe, documented Windows integration (v5.0.0).

Three narrowly scoped capabilities, all current-user only:

- open_settings(page): launch a Windows Settings page from a strict allowlist
  of documented ms-settings: URIs. No arbitrary URI or command execution.
- set_theme(mode): flip the user's light/dark preference by writing the two
  documented HKCU Personalize values. Previous values (including whether they
  existed at all) are backed up to memory/theme_backup.json for rollback;
  an existing backup is never silently overwritten — it is preserved in the
  new backup's "previous" chain.
- recycle_bin_status(): aggregate item count and total size only, via the
  documented SHQueryRecycleBinW API. No enumeration, restore, or delete.

Import-safe on non-Windows: winreg/ctypes are imported lazily inside
functions. Never raises to callers.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

from agetha.utils import IS_WINDOWS, logger
from agetha.app_config import BASE_DIR

MEMORY_DIR = BASE_DIR / "memory"
THEME_BACKUP_FILE = MEMORY_DIR / "theme_backup.json"

_theme_lock = threading.RLock()

# ── open_settings: strict allowlist of documented ms-settings URIs ────────────

SETTINGS_PAGES: dict[str, str] = {
    "home": "ms-settings:",
    "display": "ms-settings:display",
    "nightlight": "ms-settings:nightlight",
    "personalization": "ms-settings:personalization",
    "colors": "ms-settings:colors",
    "background": "ms-settings:personalization-background",
    "lockscreen": "ms-settings:lockscreen",
    "sound": "ms-settings:sound",
    "notifications": "ms-settings:notifications",
    "battery": "ms-settings:batterysaver",
    "storage": "ms-settings:storagesense",
    "bluetooth": "ms-settings:bluetooth",
    "wifi": "ms-settings:network-wifi",
    "network": "ms-settings:network-status",
    "windowsupdate": "ms-settings:windowsupdate",
    "privacy": "ms-settings:privacy",
    "about": "ms-settings:about",
}


def open_settings(page: str) -> tuple[bool, str]:
    """Open one allowlisted Windows Settings page. Never arbitrary URIs."""
    key = (page or "home").strip().lower().replace(" ", "")
    uri = SETTINGS_PAGES.get(key)
    if uri is None:
        allowed = ", ".join(sorted(SETTINGS_PAGES))
        return False, f"Unknown settings page '{page}'. Allowed: {allowed}"
    if not IS_WINDOWS:
        return False, "Windows Settings is only available on Windows."
    try:
        os.startfile(uri)  # documented ms-settings: URI, from allowlist only
        return True, f"Opened Windows Settings: {key} ({uri})"
    except Exception as exc:
        logger.warning(f"open_settings({key}) failed: {exc}")
        return False, f"Could not open settings page '{key}': {exc}"


# ── set_theme: HKCU light/dark with existence-aware rollback backup ───────────

THEME_MODES = ("light", "dark")
THEME_SCOPES = ("apps", "system", "both")
_PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
_THEME_VALUES = ("AppsUseLightTheme", "SystemUsesLightTheme")
_SCOPE_VALUES: dict[str, tuple[str, ...]] = {
    "apps": ("AppsUseLightTheme",),
    "system": ("SystemUsesLightTheme",),
    "both": ("AppsUseLightTheme", "SystemUsesLightTheme"),
}


def _normalize_scope(scope: str | None) -> str:
    """Return a valid scope name, or '' if invalid.

    ``None`` defaults to ``both``. An explicit empty / unknown string is invalid
    (so callers can reject ``scope=""`` instead of silently treating it as both).
    """
    if scope is None:
        return "both"
    s = str(scope).strip().lower()
    return s if s in THEME_SCOPES else ""


def _values_for_scope(scope: str) -> tuple[str, ...]:
    return _SCOPE_VALUES.get(scope, ())


def _broadcast_theme_change() -> None:
    """Notify the shell that ImmersiveColorSet changed (Windows only, lazy ctypes)."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "ImmersiveColorSet",
            SMTO_ABORTIFHUNG,
            2000,
            ctypes.byref(result),
        )
    except Exception as exc:
        logger.debug(f"win_integration: theme broadcast skipped: {exc}")


def _reg_read(value_name: str) -> tuple[bool, int | None]:
    """Return (existed, value) for one HKCU Personalize value."""
    if not IS_WINDOWS:
        return False, None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
            return True, int(value)
    except FileNotFoundError:
        return False, None
    except Exception as exc:
        logger.warning(f"win_integration: registry read {value_name} failed: {exc}")
        return False, None


def _reg_write(value_name: str, value: int) -> bool:
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_DWORD, int(value))
        return True
    except Exception as exc:
        logger.warning(f"win_integration: registry write {value_name} failed: {exc}")
        return False


def _reg_delete(value_name: str) -> bool:
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, value_name)
        return True
    except FileNotFoundError:
        return True  # already gone
    except Exception as exc:
        logger.warning(f"win_integration: registry delete {value_name} failed: {exc}")
        return False


def _load_backup() -> dict[str, Any] | None:
    try:
        if THEME_BACKUP_FILE.exists():
            obj = json.loads(THEME_BACKUP_FILE.read_text(encoding="utf-8", errors="replace"))
            if isinstance(obj, dict) and isinstance(obj.get("values"), dict):
                return obj
    except Exception as exc:
        logger.warning(f"win_integration: theme backup unreadable: {exc}")
    return None


def _save_backup(backup: dict[str, Any]) -> bool:
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(backup, ensure_ascii=False, indent=2, sort_keys=True)
        tmp = THEME_BACKUP_FILE.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, THEME_BACKUP_FILE)
        return True
    except Exception as exc:
        logger.warning(f"win_integration: theme backup write failed: {exc}")
        return False


def describe_theme_change(mode: str, scope: str = "both") -> str:
    """Exact registry changes shown to the user before confirmation."""
    mode = (mode or "").strip().lower()
    scope_norm = _normalize_scope(scope) or "both"
    names = _values_for_scope(scope_norm)
    target = 1 if mode == "light" else 0
    lines = [
        f"Switch Windows to {mode.upper()} mode "
        f"(scope: {scope_norm}, current user only).",
        "Exact changes under HKCU\\...\\Themes\\Personalize:",
    ]
    for name in names:
        existed, old = _reg_read(name)
        old_desc = str(old) if existed else "(value does not exist)"
        lines.append(f"  {name}: {old_desc}  ->  {target}")
    lines.append("Previous values are saved to memory\\theme_backup.json for rollback.")
    return "\n".join(lines)


def set_theme(mode: str, scope: str = "both") -> tuple[bool, str]:
    """Change light/dark preference for the given scope. Backs up only those values."""
    mode = (mode or "").strip().lower()
    if mode not in THEME_MODES:
        return False, f"Unknown theme '{mode}'. Allowed: light, dark."
    scope_norm = _normalize_scope(scope)
    if not scope_norm:
        return False, f"Unknown scope '{scope}'. Allowed: apps, system, both."
    if not IS_WINDOWS:
        return False, "Theme control is only available on Windows."
    names = _values_for_scope(scope_norm)
    target = 1 if mode == "light" else 0
    with _theme_lock:
        # Record existence AND value for ONLY the values being changed.
        values: dict[str, Any] = {}
        for name in names:
            existed, old = _reg_read(name)
            values[name] = {"existed": existed, "value": old}
        backup: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "mode_set": mode,
            "scope": scope_norm,
            "values": values,
        }
        # Preserve the full previous rollback chain (never drop nested state).
        older = _load_backup()
        if older is not None:
            backup["previous"] = older
        if not _save_backup(backup):
            return False, "Refused: could not save the rollback backup, so nothing was changed."
        changed: list[str] = []
        for name in names:
            if _reg_write(name, target):
                changed.append(name)
        if len(changed) != len(names):
            # Partial write: restore from the backup we just saved, then fail.
            _rollback_values_unlocked(backup)
            return False, (
                f"Theme change incomplete (wrote: {', '.join(changed) or 'none'}); "
                "prior values restored."
            )
    _broadcast_theme_change()
    return True, (
        f"Windows theme set to {mode} (scope: {scope_norm}). "
        "Previous values saved for rollback."
    )


def _rollback_values_unlocked(backup: dict[str, Any]) -> tuple[bool, list[str]]:
    """Apply one backup's values. Returns (all_ok, restored_descriptions)."""
    restored: list[str] = []
    expected = 0
    for name, info in backup.get("values", {}).items():
        if name not in _THEME_VALUES or not isinstance(info, dict):
            continue
        expected += 1
        if info.get("existed") and info.get("value") is not None:
            if _reg_write(name, int(info["value"])):
                restored.append(name)
        else:
            if _reg_delete(name):
                restored.append(f"{name} (removed — did not exist before)")
    return len(restored) == expected and expected > 0, restored


def rollback_theme() -> tuple[bool, str]:
    """Restore values from the backup; delete values that did not exist before.

    The backup chain is only unwound after a fully successful restore.
    Broadcasts WM_SETTINGCHANGE after a successful restore.
    """
    if not IS_WINDOWS:
        return False, "Theme control is only available on Windows."
    with _theme_lock:
        backup = _load_backup()
        if backup is None:
            return False, "No theme backup found — nothing to roll back."
        ok, restored = _rollback_values_unlocked(backup)
        if not ok:
            return False, (
                "Theme rollback incomplete — backup left in place so you can retry. "
                "Restored so far: " + ("; ".join(restored) if restored else "none")
            )
        # Unwind one level of the backup chain only after full success
        previous = backup.get("previous")
        if isinstance(previous, dict):
            _save_backup(previous)
        else:
            try:
                THEME_BACKUP_FILE.unlink(missing_ok=True)
            except Exception:
                pass
    _broadcast_theme_change()
    return True, "Theme rolled back: " + ("; ".join(restored) if restored else "no values touched.")


# ── recycle_bin_status: aggregate info only ───────────────────────────────────

def recycle_bin_status() -> tuple[bool, str, dict[str, int]]:
    """Aggregate Recycle Bin info (all drives): item count + total size.

    Uses documented SHQueryRecycleBinW. Never enumerates, restores, deletes,
    or reveals any item names/paths.
    """
    if not IS_WINDOWS:
        return False, "Recycle Bin status is only available on Windows.", {}
    try:
        import ctypes
        from ctypes import wintypes

        class SHQUERYRBINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("i64Size", ctypes.c_longlong),
                ("i64NumItems", ctypes.c_longlong),
            ]

        info = SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(SHQUERYRBINFO)
        result = ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
        if result != 0:
            return False, f"Could not query the Recycle Bin (code {result}).", {}
        count = int(info.i64NumItems)
        size = int(info.i64Size)
        mb = size / (1024 * 1024)
        msg = f"Recycle Bin: {count} item(s), {mb:.1f} MB total."
        return True, msg, {"items": count, "bytes": size}
    except Exception as exc:
        logger.warning(f"recycle_bin_status failed: {exc}")
        return False, f"Could not query the Recycle Bin: {exc}", {}
