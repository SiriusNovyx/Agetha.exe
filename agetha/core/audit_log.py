"""
audit_log.py — Local append-only audit log for user-visible system changes (v5.0.0).

Records transparent, user-reviewable entries for actions like autostart or
theme changes. Local only (memory/audit_log.jsonl), never networked, never
raises to callers. Read-modify-write is guarded by a process-level RLock.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agetha.app_config import BASE_DIR

logger = logging.getLogger("Agetha")

MEMORY_DIR = BASE_DIR / "memory"
AUDIT_FILE = MEMORY_DIR / "audit_log.jsonl"

_lock = threading.RLock()

_MAX_DETAIL_CHARS = 500


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


def _ensure_dir(path: Path | None = None) -> None:
    try:
        (path or AUDIT_FILE).parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def log_audit(
    action: str,
    details: dict[str, Any] | str,
    outcome: str,
    *,
    now_fn: Callable[[], datetime] | None = None,
    audit_file: Path | None = None,
) -> bool:
    """Append one audit record. Returns True on success. Never raises."""
    try:
        if isinstance(details, dict):
            detail_obj: Any = {
                str(k)[:64]: str(v)[:_MAX_DETAIL_CHARS] for k, v in details.items()
            }
        else:
            detail_obj = str(details)[:_MAX_DETAIL_CHARS]
        record = {
            "schema_version": 1,
            "ts": _utcnow(now_fn).isoformat(),
            "action": (action or "unknown").strip()[:64],
            "details": detail_obj,
            "outcome": (outcome or "unknown").strip()[:64],
        }
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        target = Path(audit_file or AUDIT_FILE)
        with _lock:
            _ensure_dir(target)
            with target.open("a", encoding="utf-8") as fh:
                fh.write(line)
        logger.info(f"audit: {record['action']} -> {record['outcome']}")
        return True
    except Exception as exc:
        logger.warning(f"audit_log: append failed: {exc}")
        return False


def read_audit(limit: int = 50) -> list[dict[str, Any]]:
    """Return newest-first audit records. Never raises."""
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 50
    entries: list[dict[str, Any]] = []
    try:
        with _lock:
            if not AUDIT_FILE.exists():
                return []
            with AUDIT_FILE.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict) and obj.get("action"):
                        entries.append(obj)
        return list(reversed(entries[-limit:]))
    except Exception as exc:
        logger.warning(f"audit_log: read failed: {exc}")
        return []
