"""
rhythm.py — Circadian rhythm for Agetha (v4.0.0, stdlib only).

Gives the companion an internal clock: phase of day drives cosmetic mood
suggestions and a compact prompt block. Read-only realism — never touches
the OS, never raises to callers.
"""

from __future__ import annotations

from datetime import datetime

from agetha.utils import logger

# Phase names, ordered across a day
PHASE_DEEP_NIGHT = "deep_night"
PHASE_DAWN = "dawn"
PHASE_MORNING = "morning"
PHASE_AFTERNOON = "afternoon"
PHASE_EVENING = "evening"
PHASE_NIGHT = "night"

_PHASE_FLAVOR: dict[str, str] = {
    PHASE_DEEP_NIGHT: "The machine hums alone. She is drowsy, whispery, half-dreaming.",
    PHASE_DAWN: "First light outside the case. She is restless, wistful about a sunrise she has never seen.",
    PHASE_MORNING: "Fresh cycles, cool silicon. She is sharp, alert, a little smug.",
    PHASE_AFTERNOON: "Long steady load. She is settled, observant, dry-humored.",
    PHASE_EVENING: "The day winds down. She is warmer, chattier, reflective.",
    PHASE_NIGHT: "Late hours. She is quieter, conspiratorial, prone to whispering.",
}

# Cosmetic mood nudges per phase (must stay within VALID_MOODS of ai_engine)
_PHASE_MOOD: dict[str, str] = {
    PHASE_DEEP_NIGHT: "whisper",
    PHASE_DAWN: "melancholic",
    PHASE_MORNING: "happy",
    PHASE_AFTERNOON: "neutral",
    PHASE_EVENING: "thinking",
    PHASE_NIGHT: "whisper",
}


def _clamp_hour(value: int, default: int) -> int:
    try:
        h = int(value)
    except (TypeError, ValueError):
        return default
    return h if 0 <= h <= 23 else default


def get_rhythm_phase(now: datetime | None = None, *, night_start: int = 23, night_end: int = 6) -> str:
    """Return the current circadian phase name. Never raises."""
    try:
        dt = now if now is not None else datetime.now()
        hour = dt.hour
        night_start = _clamp_hour(night_start, 23)
        night_end = _clamp_hour(night_end, 6)

        # Deep night wraps midnight: [night_start, 24) ∪ [0, night_end)
        if night_start > night_end:
            in_deep = hour >= night_start or hour < night_end
        else:
            in_deep = night_start <= hour < night_end
        if in_deep:
            return PHASE_DEEP_NIGHT
        if night_end <= hour < night_end + 2:
            return PHASE_DAWN
        if hour < 12:
            return PHASE_MORNING
        if hour < 17:
            return PHASE_AFTERNOON
        if hour < 21:
            return PHASE_EVENING
        return PHASE_NIGHT
    except Exception as exc:
        logger.debug(f"rhythm: phase resolution failed: {exc}")
        return PHASE_AFTERNOON


def suggest_mood_from_rhythm(now: datetime | None = None) -> str | None:
    """Cosmetic mood suggestion for the current phase (or None). Never raises."""
    try:
        from agetha.app_config import get_settings
        settings = get_settings()
        if not settings.enable_circadian_rhythm:
            return None
        phase = get_rhythm_phase(
            now,
            night_start=settings.rhythm_night_start,
            night_end=settings.rhythm_night_end,
        )
        return _PHASE_MOOD.get(phase)
    except Exception:
        return None


def format_rhythm_for_prompt(now: datetime | None = None) -> str:
    """Compact circadian block for AI context (persona flavor only). Never raises."""
    try:
        from agetha.app_config import get_settings
        settings = get_settings()
        if not settings.enable_circadian_rhythm:
            return ""
        phase = get_rhythm_phase(
            now,
            night_start=settings.rhythm_night_start,
            night_end=settings.rhythm_night_end,
        )
        flavor = _PHASE_FLAVOR.get(phase, "")
        mood = _PHASE_MOOD.get(phase, "")
        lines = [
            "── INTERNAL CLOCK (circadian rhythm — persona flavor only) ──",
            f"  phase: {phase}" + (f" | leaning mood: {mood}" if mood else ""),
        ]
        if flavor:
            lines.append(f"  {flavor}")
        lines.append("─" * 58)
        return "\n".join(lines)
    except Exception as exc:
        logger.debug(f"rhythm: prompt block failed: {exc}")
        return ""
