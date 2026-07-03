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

from agetha.utils import logger

from agetha.app_config import BASE_DIR

MEMORY_DIR = BASE_DIR / "memory"
STATS_FILE = MEMORY_DIR / "companion_stats.json"

_lock = threading.Lock()
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


def load_stats() -> dict[str, Any]:
    """Load stats from disk; returns defaults on any failure."""
    _ensure_dir()
    with _lock:
        try:
            if STATS_FILE.exists():
                raw = json.loads(STATS_FILE.read_text(encoding="utf-8", errors="replace"))
                if isinstance(raw, dict):
                    merged = dict(_DEFAULTS)
                    merged.update(raw)
                    return merged
        except Exception as exc:
            logger.warning(f"companion_stats: load failed: {exc}")
    return dict(_DEFAULTS)


def save_stats(stats: dict[str, Any]) -> None:
    """Persist stats dict; failures are logged only."""
    _ensure_dir()
    payload = dict(_DEFAULTS)
    payload.update(stats or {})
    payload["last_updated"] = datetime.now(timezone.utc).isoformat()
    with _lock:
        try:
            STATS_FILE.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"companion_stats: save failed: {exc}")


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


def suggest_mood_from_heat() -> str | None:
    """Suggest surface/deep mood when host CPU load is high (realistic, not dangerous)."""
    try:
        heat = _read_cpu_percent()
        if heat >= 90:
            return "manic"
        if heat >= 75:
            return "angry"
        if heat >= 60:
            return "thinking"
    except Exception:
        pass
    return None


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
        stats = load_stats()
        et = (event_type or "").strip().lower()

        if et == "file_drop":
            path = str(kwargs.get("path", "") or "")
            if path:
                size, sampled = _sample_file_bytes(path)
            else:
                size = float(kwargs.get("file_size", 0) or 0)
                sampled = min(size, _FEED_SAMPLE_BYTES)
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
        elif et == "tick":
            stats["entropy"] = _clamp(float(stats.get("entropy", 50)) - 0.4)

        if float(stats.get("infection_level", 0)) >= 100:
            stats["infection_level"] = 100.0
            stats["max_infection_reached"] = True

        stats["core_heat"] = _clamp(_read_cpu_percent())
        stats["uptime_seconds"] = round(time.monotonic() - _boot_time, 1)
        save_stats(stats)
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
