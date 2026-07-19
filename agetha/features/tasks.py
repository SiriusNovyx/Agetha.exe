"""
tasks.py — Task keeper for Agetha (v4.0.0, stdlib only).

The user can ask Agetha to remember small tasks; she stores them in
memory/tasks.json and nags about pending ones during ambient polls.

Safety: read/write limited to the app's own memory/ folder — never the
wider filesystem. Never raises to callers.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

from agetha.utils import logger
from agetha.app_config import BASE_DIR

MEMORY_DIR = BASE_DIR / "memory"
TASKS_FILE = MEMORY_DIR / "tasks.json"

_lock = threading.Lock()

_MAX_TASK_CHARS = 200


def _ensure_dir() -> None:
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _load_unlocked() -> list[dict[str, Any]]:
    """Read task list; caller must hold `_lock`."""
    if not TASKS_FILE.exists():
        return []
    try:
        raw = json.loads(TASKS_FILE.read_text(encoding="utf-8", errors="replace"))
        if isinstance(raw, list):
            return [t for t in raw if isinstance(t, dict) and t.get("text")]
    except Exception as exc:
        logger.warning(f"tasks: load failed: {exc}")
    return []


def _save_unlocked(tasks: list[dict[str, Any]]) -> None:
    """Persist task list; caller must hold `_lock`."""
    try:
        TASKS_FILE.write_text(
            json.dumps(tasks, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning(f"tasks: save failed: {exc}")


def _next_id(tasks: list[dict[str, Any]]) -> int:
    highest = 0
    for t in tasks:
        try:
            highest = max(highest, int(t.get("id", 0)))
        except (TypeError, ValueError):
            continue
    return highest + 1


def add_task(text: str) -> dict[str, Any] | None:
    """Add a pending task. Returns the record or None. Never raises."""
    body = (text or "").strip()[:_MAX_TASK_CHARS]
    if not body:
        return None
    try:
        from agetha.app_config import get_settings
        max_entries = get_settings().tasks_max_entries
    except Exception:
        max_entries = 100
    try:
        _ensure_dir()
        with _lock:
            tasks = _load_unlocked()
            record: dict[str, Any] = {
                "id": _next_id(tasks),
                "text": body,
                "done": False,
                "created": datetime.now(timezone.utc).isoformat(),
                "completed": "",
            }
            tasks.append(record)
            if len(tasks) > max_entries:
                # Drop oldest completed tasks first, then oldest overall
                done_first = sorted(tasks, key=lambda t: (not t.get("done"), str(t.get("created", ""))))
                tasks = done_first[len(tasks) - max_entries:]
                tasks.sort(key=lambda t: int(t.get("id", 0)))
            _save_unlocked(tasks)
        return record
    except Exception as exc:
        logger.warning(f"tasks: add failed: {exc}")
        return None


def complete_task(task: str | int) -> dict[str, Any] | None:
    """Mark a task done by id or text substring. Returns record or None."""
    try:
        with _lock:
            tasks = _load_unlocked()
            target: dict[str, Any] | None = None
            # Numeric id match first
            try:
                tid = int(task)
                for t in tasks:
                    if int(t.get("id", 0)) == tid and not t.get("done"):
                        target = t
                        break
            except (TypeError, ValueError):
                pass
            # Fall back to case-insensitive substring on pending tasks
            if target is None:
                needle = str(task or "").strip().lower()
                if needle:
                    for t in tasks:
                        if not t.get("done") and needle in str(t.get("text", "")).lower():
                            target = t
                            break
            if target is None:
                return None
            target["done"] = True
            target["completed"] = datetime.now(timezone.utc).isoformat()
            _save_unlocked(tasks)
            return dict(target)
    except Exception as exc:
        logger.warning(f"tasks: complete failed: {exc}")
        return None


def get_tasks(*, include_done: bool = True, limit: int = 50) -> list[dict[str, Any]]:
    """Return tasks (pending first, newest first within groups). Never raises."""
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 50
    try:
        with _lock:
            tasks = _load_unlocked()
        if not include_done:
            tasks = [t for t in tasks if not t.get("done")]
        tasks.sort(key=lambda t: (bool(t.get("done")), -int(t.get("id", 0))))
        return tasks[:limit]
    except Exception:
        return []


def get_pending_count() -> int:
    """Number of pending (not done) tasks. Never raises."""
    try:
        return len(get_tasks(include_done=False, limit=200))
    except Exception:
        return 0


def format_tasks_for_display(tasks: list[dict[str, Any]]) -> list[str]:
    """Popup-friendly lines for the list_tasks command."""
    if not tasks:
        return ["[no tasks — she has nothing to nag you about. yet.]"]
    lines: list[str] = []
    for t in tasks:
        mark = "[x]" if t.get("done") else "[ ]"
        lines.append(f"{mark} #{t.get('id', '?')} {str(t.get('text', '')).strip()}")
    return lines


def format_tasks_for_prompt(max_tasks: int = 5) -> str:
    """Compact pending-task block for AI context (empty when none)."""
    try:
        from agetha.app_config import get_settings
        if not get_settings().enable_tasks:
            return ""
        pending = get_tasks(include_done=False, limit=max_tasks)
        if not pending:
            return ""
        lines = [
            "── USER TASKS (she keeps this list; she may nag about pending ones) ──",
        ]
        for t in pending:
            lines.append(f"  #{t.get('id', '?')} {str(t.get('text', '')).strip()[:120]}")
        lines.append("─" * 58)
        return "\n".join(lines)
    except Exception:
        return ""
