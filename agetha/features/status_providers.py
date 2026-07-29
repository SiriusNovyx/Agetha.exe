"""
status_providers.py — Optional coarse local status observations (v5.0.0).

"Status providers", not monitoring: disabled by default
(ENABLE_STATUS_PROVIDERS=no), read-only, local-only, pausable at runtime, and
limited to coarse OS-level facts:

    - battery level / plugged state (psutil, already a dependency)
    - free disk space on the system drive
    - network: local non-loopback interface up/down (no outbound probes)

Never captured: keystrokes, clipboard, screen contents, credentials, browsing
data, or the contents of any document. Observations are short fixed-template
strings; nothing is sent anywhere.

Only *changes* are reported (edge-triggered), at most once per poll interval.
Uses an injectable UTC clock. Never raises.
"""

from __future__ import annotations

import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agetha.utils import logger

_lock = threading.RLock()
_paused = False
_last_poll: datetime | None = None
_last_seen: dict[str, Any] = {}
_pending: list[str] = []

_BATTERY_LOW = 20
_BATTERY_CRITICAL = 10
_DISK_LOW_PCT = 10.0


def _utcnow(now_fn: Callable[[], datetime] | None = None) -> datetime:
    """Injectable UTC clock (constraint: testable time)."""
    if now_fn is not None:
        try:
            dt = now_fn()
            if isinstance(dt, datetime):
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def _enabled() -> bool:
    try:
        from agetha.app_config import get_settings
        return bool(get_settings().enable_status_providers)
    except Exception:
        return False


def _interval_sec() -> int:
    try:
        from agetha.app_config import get_settings
        return int(get_settings().status_poll_interval_sec)
    except Exception:
        return 300


def is_paused() -> bool:
    with _lock:
        return _paused


def set_paused(paused: bool) -> None:
    """User-controlled runtime pause switch (also reachable from the tray)."""
    global _paused
    with _lock:
        _paused = bool(paused)
    logger.info(f"status_providers: {'paused' if paused else 'resumed'} by user")


def status_summary() -> str:
    """One-line status for settings UI."""
    if not _enabled():
        return "Status observations: OFF (ENABLE_STATUS_PROVIDERS=no)"
    if is_paused():
        return "Status observations: PAUSED (battery/disk/network only)"
    return f"Status observations: ON — battery/disk/network, every {_interval_sec()}s"


# ── Providers (each returns a raw sample or None) ─────────────────────────────

def _sample_battery() -> dict[str, Any] | None:
    try:
        import psutil
        batt = psutil.sensors_battery()
        if batt is None:
            return None
        return {"percent": int(batt.percent), "plugged": bool(batt.power_plugged)}
    except Exception:
        return None


def _sample_disk() -> dict[str, Any] | None:
    try:
        anchor = Path.home().anchor or "/"
        usage = shutil.disk_usage(anchor)
        free_pct = usage.free / usage.total * 100.0 if usage.total else 100.0
        return {"free_pct": round(free_pct, 1), "free_gb": round(usage.free / 1024**3, 1)}
    except Exception:
        return None


def _sample_network() -> dict[str, Any] | None:
    """Local interface up/down only — never opens outbound connections."""
    try:
        import psutil
        stats = psutil.net_if_stats()
        if not stats:
            return {"online": False}
        online = any(
            bool(getattr(info, "isup", False))
            and str(name).lower() not in ("lo", "loopback")
            and "loopback" not in str(name).lower()
            for name, info in stats.items()
        )
        return {"online": online}
    except Exception:
        return None


# ── Edge-triggered observation logic (pure, testable) ─────────────────────────

def _observations_from(samples: dict[str, dict[str, Any] | None],
                       previous: dict[str, Any]) -> list[str]:
    """Compare fresh samples with the previous snapshot; report changes only."""
    notes: list[str] = []
    batt = samples.get("battery")
    if batt is not None:
        prev = previous.get("battery") or {}
        level_band = ("critical" if batt["percent"] <= _BATTERY_CRITICAL
                      else "low" if batt["percent"] <= _BATTERY_LOW else "ok")
        prev_band = prev.get("band")
        if level_band != prev_band and level_band != "ok":
            notes.append(f"battery is {level_band} ({batt['percent']}%)")
        if prev and bool(prev.get("plugged")) != batt["plugged"]:
            notes.append("charger plugged in" if batt["plugged"] else "charger unplugged")
        previous["battery"] = {**batt, "band": level_band}
    disk = samples.get("disk")
    if disk is not None:
        prev = previous.get("disk") or {}
        low = disk["free_pct"] <= _DISK_LOW_PCT
        if low and not prev.get("low"):
            notes.append(f"system drive is nearly full ({disk['free_gb']} GB free)")
        previous["disk"] = {**disk, "low": low}
    net = samples.get("network")
    if net is not None:
        prev = previous.get("network") or {}
        if "online" in prev and prev["online"] != net["online"]:
            notes.append("network connection restored" if net["online"]
                         else "network connection lost")
        previous["network"] = dict(net)
    return notes


def poll(*, now_fn: Callable[[], datetime] | None = None,
         force: bool = False) -> list[str]:
    """Run one observation cycle (rate-limited by STATUS_POLL_INTERVAL_SEC).

    Returns new observation strings (also queued for prompt pickup).
    """
    global _last_poll
    if not _enabled() or is_paused():
        return []
    now = _utcnow(now_fn)
    with _lock:
        if (not force and _last_poll is not None
                and (now - _last_poll).total_seconds() < _interval_sec()):
            return []
        _last_poll = now
    try:
        samples = {
            "battery": _sample_battery(),
            "disk": _sample_disk(),
            "network": _sample_network(),
        }
        with _lock:
            notes = _observations_from(samples, _last_seen)
            for note in notes:
                if note not in _pending:
                    _pending.append(note)
            del _pending[:-5]  # keep the queue tiny
        if notes:
            try:
                from agetha.core.emotion_engine import apply_event
                apply_event("status_observation", now_fn=now_fn)
            except Exception:
                pass
        return notes
    except Exception as exc:
        logger.warning(f"status_providers: poll failed: {exc}")
        return []


def pop_observations_for_prompt() -> str:
    """One-shot: return and clear pending observations as a prompt block."""
    with _lock:
        if not _pending or not _enabled() or _paused:
            return ""
        notes = list(_pending)
        _pending.clear()
    lines = ["── STATUS OBSERVATION (coarse local OS facts, read-only) ──"]
    lines += [f"  - {note}" for note in notes]
    lines.append("  React in character if it fits; never claim deeper access than this.")
    return "\n".join(lines)


def has_pending_observations() -> bool:
    """Return whether an ambient status event is waiting, without consuming it."""
    with _lock:
        return bool(_pending) and _enabled() and not _paused
