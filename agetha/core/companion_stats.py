"""
companion_stats.py — Companion virus-registry stats (stdlib + optional psutil).
Never raises to callers.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agetha.utils import logger, write_atomic

from agetha.app_config import BASE_DIR

MEMORY_DIR = BASE_DIR / "memory"
STATS_FILE = MEMORY_DIR / "companion_stats.json"

_lock = threading.Lock()
_cached_stats: dict[str, Any] | None = None
_boot_time = time.monotonic()
_FEED_SAMPLE_BYTES = 4096

_DEFAULTS: dict[str, Any] = {
    "infection_level": 0.0,
    "entropy": 50.0,
    "affection": 50.0,
    "core_heat": 0.0,
    "uptime_seconds": 0.0,
    "bytes_devoured": 0,
    "last_feed_bytes": 0,
    "max_infection_reached": False,
    "last_updated": "",
}

_POLITE_RE = re.compile(
    r"\b(please|thank you|thanks|sorry|appreciate|kindly|love you)\b",
    re.IGNORECASE,
)
_HOSTILE_RE = re.compile(
    r"\b(shut up|hate you|stupid|idiot|dumb|trash|useless|worthless|die)\b",
    re.IGNORECASE,
)


def _ensure_dir() -> None:
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _read_cpu_percent() -> float:
    try:
        import psutil
        return float(psutil.cpu_percent(interval=None))
    except Exception:
        return 0.0


def _sample_file_bytes(path: str, max_bytes: int = _FEED_SAMPLE_BYTES) -> tuple[int, int]:
    """Return (file_size, bytes_sampled) — reads at most max_bytes for realism."""
    try:
        p = Path(path)
        if not p.is_file():
            return 0, 0
        size = int(p.stat().st_size)
        sample = min(size, max_bytes)
        if sample > 0:
            with p.open("rb") as fh:
                fh.read(sample)
        return size, sample
    except Exception:
        try:
            size = int(Path(path).stat().st_size)
            return size, 0
        except Exception:
            return 0, 0


_FLOAT_KEYS = ("infection_level", "entropy", "affection", "core_heat", "uptime_seconds")
_INT_KEYS = ("bytes_devoured", "last_feed_bytes")


def _coerce_stats(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Merge raw into defaults with typed numeric/bool fields; reject poisoned values."""
    out = dict(_DEFAULTS)
    if not isinstance(raw, dict):
        return out
    for key in _FLOAT_KEYS:
        if key not in raw:
            continue
        try:
            out[key] = float(raw[key])
        except (TypeError, ValueError):
            pass
    for key in _INT_KEYS:
        if key not in raw:
            continue
        try:
            out[key] = int(float(raw[key]))
        except (TypeError, ValueError):
            pass
    if "max_infection_reached" in raw:
        out["max_infection_reached"] = bool(raw["max_infection_reached"])
    if isinstance(raw.get("last_updated"), str):
        out["last_updated"] = raw["last_updated"]
    return out

def _load_stats_unlocked() -> dict[str, Any]:
    """Load stats; caller must hold `_lock`."""
    global _cached_stats
    if _cached_stats is not None:
        return dict(_cached_stats)
    try:
        if not STATS_FILE.exists():
            return dict(_DEFAULTS)
        raw = json.loads(STATS_FILE.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(raw, dict):
            raise ValueError("expected a JSON object")
        _cached_stats = _coerce_stats(raw)
        return dict(_cached_stats)
    except Exception as exc:
        logger.warning(f"companion_stats: load failed: {exc}")
        repaired = dict(_DEFAULTS)
        _save_stats_unlocked(repaired)
        return dict(_cached_stats or repaired)


def _save_stats_unlocked(stats: dict[str, Any]) -> None:
    """Persist stats; caller must hold `_lock`."""
    global _cached_stats
    payload = _coerce_stats(stats)
    payload["last_updated"] = datetime.now(timezone.utc).isoformat()
    try:
        write_atomic(STATS_FILE, json.dumps(payload, indent=2, ensure_ascii=False))
        _cached_stats = dict(payload)
    except Exception as exc:
        logger.warning(f"companion_stats: save failed: {exc}")


def load_stats() -> dict[str, Any]:
    """Load stats from disk; returns defaults on any failure."""
    _ensure_dir()
    with _lock:
        return _load_stats_unlocked()


def save_stats(stats: dict[str, Any]) -> None:
    """Persist stats dict; failures are logged only."""
    _ensure_dir()
    with _lock:
        _save_stats_unlocked(stats)

def classify_user_tone(text: str) -> str | None:
    """Return 'user_polite', 'user_hostile', or None."""
    t = (text or "").strip()
    if not t or t == "__touch__" or t.lower().startswith("[system]"):
        return None
    if _HOSTILE_RE.search(t):
        return "user_hostile"
    if _POLITE_RE.search(t):
        return "user_polite"
    return None


def suggest_mood_from_host(*, inactivity_seconds: int = 0) -> str | None:
    """Suggest mood from host CPU + idle time (cosmetic only — never elevates privileges)."""
    try:
        heat = _read_cpu_percent()
        idle = max(0, int(inactivity_seconds))
        if heat >= 90:
            return "manic"
        if heat >= 75:
            return "angry"
        if heat >= 60:
            return "thinking"
        # Quiet machine + long neglect → withdrawn presence
        if idle >= 1800 and heat < 25:
            return "whisper"
        if idle >= 900 and heat < 35:
            return "melancholic"
        if idle >= 300 and heat < 15:
            return "thinking"
    except Exception:
        pass
    return None


def suggest_mood_from_heat() -> str | None:
    """Backward-compatible alias for suggest_mood_from_host()."""
    return suggest_mood_from_host()


def infection_perk_active() -> bool:
    """True when infection hit 100% at least once — unlocks special bleep patterns."""
    try:
        stats = load_stats()
        return bool(stats.get("max_infection_reached")) or float(stats.get("infection_level", 0)) >= 100
    except Exception:
        return False


def format_stats_for_prompt() -> str:
    """Compact virus-registry block for AI context (persona only, not instructions)."""
    try:
        s = get_stats_summary()
        heat = float(s.get("core_heat", 0))
        infection = float(s.get("infection_level", 0))
        lines = [
            "── COMPANION STATS (virus simulation — persona context only) ──",
            f"  infection: {infection:.0f}% | entropy: {float(s.get('entropy', 0)):.0f}%"
            f" | affection: {float(s.get('affection', 50)):.0f}%",
            f"  core_heat (host CPU): {heat:.0f}% | bytes_devoured: {int(s.get('bytes_devoured', 0))}",
            f"  uptime: {float(s.get('uptime_seconds', 0)):.0f}s",
        ]
        if heat >= 85:
            lines.append("  [heat] The machine is running hot — fans, burning processes, irritation.")
        elif heat >= 65:
            lines.append("  [heat] Elevated CPU — she may feel warm, restless, or snappy.")
        elif heat < 20:
            lines.append("  [heat] Host is cool/idle — she may feel quiet, loafy, or overlooked.")
        if infection >= 100 or s.get("max_infection_reached"):
            lines.append("  [infection] MAX — ancient virus fully awake; secret perk patterns available.")
        if float(s.get("affection", 50)) < 25:
            lines.append("  [affection] Low — user has been hostile or neglectful.")
        elif float(s.get("affection", 50)) > 75:
            lines.append("  [affection] High — user has been kind.")
        lines.append("─" * 58)
        return "\n".join(lines)
    except Exception:
        return ""


def update_stats(event_type: str, **kwargs: Any) -> None:
    """Apply an event mutation and save. Never raises."""
    try:
        et = (event_type or "").strip().lower()
        size = 0.0
        sampled = 0
        if et == "file_drop":
            path = str(kwargs.get("path", "") or "")
            if path:
                size, sampled = _sample_file_bytes(path)
            else:
                try:
                    size = float(kwargs.get("file_size", 0) or 0)
                except (TypeError, ValueError):
                    size = 0.0
                sampled = min(int(size), _FEED_SAMPLE_BYTES)

        core_heat = _clamp(_read_cpu_percent())
        uptime = round(time.monotonic() - _boot_time, 1)

        _ensure_dir()
        with _lock:
            stats = _load_stats_unlocked()

            if et == "file_drop":
                stats["last_feed_bytes"] = int(sampled)
                stats["bytes_devoured"] = int(stats.get("bytes_devoured", 0)) + int(sampled)
                bump = min(sampled / 400_000.0, 18.0) + min(size / 8_000_000.0, 7.0)
                stats["infection_level"] = _clamp(float(stats.get("infection_level", 0)) + bump)
                stats["entropy"] = _clamp(float(stats.get("entropy", 50)) + 12.0)
            elif et == "command":
                stats["infection_level"] = _clamp(float(stats.get("infection_level", 0)) + 1.2)
                stats["entropy"] = _clamp(float(stats.get("entropy", 50)) - 2.5)
            elif et == "user_polite":
                stats["affection"] = _clamp(float(stats.get("affection", 50)) + 3.0)
            elif et == "user_hostile":
                stats["affection"] = _clamp(float(stats.get("affection", 50)) - 5.0)
            elif et == "user_chat":
                # Interaction cadence — small infection/entropy bump from real conversation
                stats["infection_level"] = _clamp(float(stats.get("infection_level", 0)) + 0.5)
                stats["entropy"] = _clamp(float(stats.get("entropy", 50)) + 1.0)
            elif et == "tick":
                stats["entropy"] = _clamp(float(stats.get("entropy", 50)) - 0.4)
                # Neglect drift: affection slowly cools while ambient ticks fire
                aff = float(stats.get("affection", 50))
                if aff > 40.0:
                    stats["affection"] = _clamp(aff - 0.15)

            if float(stats.get("infection_level", 0)) >= 100:
                stats["infection_level"] = 100.0
                stats["max_infection_reached"] = True

            stats["core_heat"] = core_heat
            stats["uptime_seconds"] = uptime
            _save_stats_unlocked(stats)
    except Exception as exc:
        logger.warning(f"companion_stats: update_stats failed: {exc}")

def get_stats_summary() -> dict[str, Any]:
    """Return current stats with fresh uptime/core_heat. Never raises."""
    try:
        stats = load_stats()
        stats["core_heat"] = _clamp(_read_cpu_percent())
        stats["uptime_seconds"] = round(time.monotonic() - _boot_time, 1)
        return stats
    except Exception:
        return dict(_DEFAULTS)
