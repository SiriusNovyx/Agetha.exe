"""Compact, local-only date/time context for AI prompts."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable


Clock = Callable[[], datetime]


def local_now(clock: Clock | None = None) -> datetime:
    """Return an aware local datetime while keeping the clock injectable."""
    current = clock() if clock is not None else datetime.now().astimezone()
    if current.tzinfo is None:
        return current.astimezone()
    return current


def _format_utc_offset(offset: timedelta | None) -> str:
    if offset is None:
        return "UTC offset unavailable"
    total_minutes = int(offset.total_seconds() / 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _timezone_name(current: datetime) -> str:
    tzinfo = current.tzinfo
    if tzinfo is not None:
        for attr in ("key", "zone"):
            value = getattr(tzinfo, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
    try:
        value = current.tzname()
        if value:
            return str(value).strip()
    except Exception:
        pass
    return "Local time zone"


def build_datetime_context(
    *,
    include_seconds: bool = False,
    include_timezone: bool = True,
    clock: Clock | None = None,
) -> str:
    """Format weekday, ISO date, local time, and optional time-zone metadata."""
    current = local_now(clock)
    time_format = "%H:%M:%S" if include_seconds else "%H:%M"
    lines = [f"Local time: {current:%A, %Y-%m-%d} {current.strftime(time_format)}"]
    if include_timezone:
        lines.append(
            f"Time zone: {_timezone_name(current)} ({_format_utc_offset(current.utcoffset())})"
        )
    return "\n".join(lines)


__all__ = ["Clock", "build_datetime_context", "local_now"]
