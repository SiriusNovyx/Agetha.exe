"""
autostart.py — "Start Agetha when I sign in" (v5.0.0).

Transparent, opt-in sign-in startup implemented as a single plainly named,
user-visible shortcut in the CURRENT USER's Startup folder:

    %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\Agetha.lnk

Explicitly NOT used: administrator rights, Windows services, scheduled tasks,
registry Run keys, or hidden files. The shortcut is easy to find and delete
by hand.

Safety:
- `disable()` refuses to delete any shortcut whose target or arguments do not
  match Agetha's expected launcher (path-normalized, containment-checked).
- Paths are never interpolated into PowerShell source; they are passed through
  environment variables read inside the script.
- Import-safe on non-Windows: all platform calls are lazy / guarded.

Never raises to callers.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from agetha.utils import IS_WINDOWS, ICON_PATH, logger
from agetha.app_config import BASE_DIR

_SHORTCUT_NAME = "Agetha.lnk"
_DESCRIPTION = "Agetha AI Companion - Start when I sign in"

# Validation outcomes
STATUS_MISSING = "missing"
STATUS_VALID = "valid"
STATUS_MALFORMED = "malformed"
STATUS_FOREIGN = "foreign"


def startup_dir() -> Path:
    """Current-user, visible Startup folder."""
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_dir() / _SHORTCUT_NAME


def expected_launcher() -> tuple[str, str, str]:
    """(target, arguments, working_dir) for Agetha's own launcher."""
    from agetha.platform.windows_notify import _launcher_paths
    return _launcher_paths()


# ── Path-aware validation helpers (pure, testable) ────────────────────────────

def _resolve(path_str: str) -> Path | None:
    try:
        return Path(path_str).expanduser().resolve()
    except Exception:
        return None


def _is_within(child: Path, parent: Path) -> bool:
    """Path-aware containment (not string prefix)."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _arg_path(arguments: str) -> Path | None:
    """Extract a filesystem path from an argument string (strips one quote pair)."""
    a = (arguments or "").strip()
    if not a:
        return None
    if a[0] in "\"'" and a[-1] == a[0] and len(a) >= 2:
        a = a[1:-1]
    return _resolve(a)


def targets_match(actual_target: str, actual_args: str) -> bool:
    """True only if a shortcut's target AND arguments match Agetha's launcher,
    both path-normalized and contained within the app directory."""
    exp_target, exp_args, _ = expected_launcher()
    base = _resolve(str(BASE_DIR))
    at = _resolve(actual_target)
    et = _resolve(exp_target)
    if at is None or et is None or base is None:
        return False
    if at != et or not _is_within(at, base):
        return False
    # Arguments: require exact normalized match when expected args are empty
    # (non-path flags like "/c calc" must NOT silently match). When a path is
    # present, compare resolved path containment.
    exp_raw = (exp_args or "").strip()
    act_raw = (actual_args or "").strip()
    if not exp_raw and not act_raw:
        return True
    if not exp_raw and act_raw:
        return False
    exp_arg_path = _arg_path(exp_args)
    act_arg_path = _arg_path(actual_args)
    if exp_arg_path is not None and act_arg_path is not None:
        return act_arg_path == exp_arg_path and _is_within(act_arg_path, base)
    # Fall back to exact string equality for non-path argument forms
    return exp_raw == act_raw


# ── PowerShell IO (Windows only; injected paths via env, never source) ────────

def _run_powershell(script: str, extra_env: dict[str, str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(extra_env)
    kwargs: dict = {
        "args": ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "shell": False,
        "env": env,
    }
    if IS_WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return subprocess.run(**kwargs)


def _write_lnk(path: Path, target: str, args: str, workdir: str, icon: str) -> bool:
    """Create/overwrite the shortcut. Paths passed via env vars, not source."""
    if not IS_WINDOWS:
        return False
    script = (
        "$ErrorActionPreference='Stop';"
        "$sh=New-Object -ComObject WScript.Shell;"
        "$s=$sh.CreateShortcut($env:AGETHA_LNK);"
        "$s.TargetPath=$env:AGETHA_TARGET;"
        "$s.Arguments=$env:AGETHA_ARGS;"
        "$s.WorkingDirectory=$env:AGETHA_WORKDIR;"
        "if($env:AGETHA_ICON){$s.IconLocation=$env:AGETHA_ICON};"
        "$s.Description=$env:AGETHA_DESC;"
        "$s.Save();"
        "Write-Output 'OK'"
    )
    env = {
        "AGETHA_LNK": str(path),
        "AGETHA_TARGET": target,
        "AGETHA_ARGS": args,
        "AGETHA_WORKDIR": workdir,
        "AGETHA_ICON": icon,
        "AGETHA_DESC": _DESCRIPTION,
    }
    try:
        proc = _run_powershell(script, env, timeout=30)
        return path.is_file() and (proc.returncode == 0 or "OK" in (proc.stdout or ""))
    except Exception as exc:
        logger.warning(f"autostart: shortcut write failed: {exc}")
        return False


def _read_lnk_raw(path: Path) -> tuple[str, str] | None:
    """Return (target, arguments) for an existing shortcut, or None if unreadable."""
    if not IS_WINDOWS or not path.is_file():
        return None
    script = (
        "$ErrorActionPreference='Stop';"
        "$sh=New-Object -ComObject WScript.Shell;"
        "$s=$sh.CreateShortcut($env:AGETHA_LNK);"
        "[Console]::Out.Write((ConvertTo-Json @{target=$s.TargetPath;args=$s.Arguments} -Compress))"
    )
    try:
        proc = _run_powershell(script, {"AGETHA_LNK": str(path)}, timeout=30)
        out = (proc.stdout or "").strip()
        if not out:
            return None
        obj = json.loads(out)
        return str(obj.get("target", "")), str(obj.get("args", ""))
    except Exception as exc:
        logger.warning(f"autostart: shortcut read failed: {exc}")
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def validate() -> str:
    """Classify the current shortcut: missing / valid / malformed / foreign."""
    if not IS_WINDOWS:
        return STATUS_MISSING
    path = shortcut_path()
    if not path.is_file():
        return STATUS_MISSING
    raw = _read_lnk_raw(path)
    if raw is None:
        return STATUS_MALFORMED
    target, args = raw
    if not target:
        return STATUS_MALFORMED
    return STATUS_VALID if targets_match(target, args) else STATUS_FOREIGN


def is_enabled() -> bool:
    """True only when a valid Agetha startup shortcut exists."""
    return validate() == STATUS_VALID


def describe_change(action: str) -> str:
    """Human-readable summary for confirmation dialogs / dry-run."""
    target, args, _ = expected_launcher()
    path = shortcut_path()
    if action == "enable":
        return (
            "Create a startup shortcut so Agetha starts when you sign in:\n\n"
            f"  Shortcut: {path}\n"
            f"  Runs: {target} {args}".rstrip()
            + "\n\nNo admin rights, service, scheduled task, or registry change. "
            "You can delete this shortcut yourself at any time."
        )
    return (
        "Remove Agetha's sign-in startup shortcut:\n\n"
        f"  Shortcut: {path}\n\n"
        "Only Agetha's own shortcut is removed."
    )


def enable() -> tuple[bool, str]:
    """Create the startup shortcut. Idempotent. Returns (ok, message).

    Refuses to overwrite a foreign or malformed shortcut that happens to share
    the Agetha.lnk name — the user must remove it manually first.
    """
    if not IS_WINDOWS:
        return False, "Sign-in startup is only available on Windows."
    status = validate()
    if status == STATUS_VALID:
        return True, "Agetha is already set to start when you sign in."
    if status in (STATUS_FOREIGN, STATUS_MALFORMED):
        return False, (
            f"Refused: an existing shortcut at {shortcut_path()} does not match "
            "Agetha's launcher, so it was left untouched. Remove it manually, "
            "then try again."
        )
    try:
        startup_dir().mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return False, f"Could not access the Startup folder: {exc}"
    target, args, workdir = expected_launcher()
    icon = str(ICON_PATH.resolve()) if ICON_PATH.is_file() else ""
    ok = _write_lnk(shortcut_path(), target, args, workdir, icon)
    if ok and is_enabled():
        return True, "Agetha will now start when you sign in."
    return False, "Could not create the startup shortcut."


def disable() -> tuple[bool, str]:
    """Remove ONLY Agetha's own valid shortcut. Refuses foreign/malformed ones."""
    if not IS_WINDOWS:
        return False, "Sign-in startup is only available on Windows."
    status = validate()
    if status == STATUS_MISSING:
        return True, "Agetha was not set to start at sign-in (nothing to remove)."
    if status in (STATUS_FOREIGN, STATUS_MALFORMED):
        return False, (
            f"Refused: the shortcut at {shortcut_path()} does not match Agetha's "
            "launcher, so it was left untouched. Remove it manually if you created it."
        )
    try:
        shortcut_path().unlink()
        return True, "Agetha will no longer start when you sign in."
    except Exception as exc:
        return False, f"Could not remove the startup shortcut: {exc}"


def status_line() -> str:
    """One-line status for the settings UI."""
    status = validate()
    if status == STATUS_VALID:
        return f"Start Agetha when I sign in: ON  ({shortcut_path()})"
    if status == STATUS_MISSING:
        return "Start Agetha when I sign in: OFF"
    if status == STATUS_FOREIGN:
        return f"Start at sign-in: a NON-Agetha shortcut exists at {shortcut_path()}"
    return f"Start at sign-in: shortcut present but unreadable ({shortcut_path()})"
