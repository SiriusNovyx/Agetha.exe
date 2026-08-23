"""
dashboard.py — Win95-style companion dashboard (no main.py import).
"""

from __future__ import annotations

import tkinter as tk
import webbrowser
from dataclasses import dataclass
from tkinter import messagebox, ttk
from pathlib import Path
from typing import Any, Callable

from agetha.app_config import BASE_DIR, CONFIG_PATH, read_config_document
from agetha.core.capabilities import CapabilityPolicy, CapabilityProfile
from agetha.utils import logger, write_atomic

# Duplicated Win95 palette (must not import main.py)
W95_BG = "#c0c0c0"
W95_TITLE_BG = "#000080"
W95_TITLE_FG = "#ffffff"
W95_TEXT = "#000000"
W95_SHADOW = "#808080"
W95_BTN_BG = "#c0c0c0"
W95_FONT = ("MS Sans Serif", 8)
W95_FONT_BOLD = ("MS Sans Serif", 8, "bold")
W95_WARN = "#800000"

NOTEPAD_FILE = BASE_DIR / "memory" / "notepad.txt"


@dataclass(frozen=True)
class FastModeDashboardState:
    """Small, secret-free view used by both the Tk UI and focused tests."""

    status: str = "unavailable"
    active: bool = False
    managed_keys: tuple[str, ...] = ()


_FAST_MODE_ACTIVATION_PENDING = frozenset({
    "activation_required", "snapshot_missing",
})
_FAST_MODE_RESTORATION_PENDING = frozenset({
    "restore_required", "restoration_pending",
})


def _load_fast_mode_api() -> Any:
    """Import lazily so the dashboard remains usable during partial upgrades."""
    from agetha.core import fast_mode_profile

    return fast_mode_profile


def _fast_mode_result_status(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("status", "unknown") or "unknown")
    return str(getattr(result, "status", "unknown") or "unknown")


def _fast_mode_result_ok(result: Any) -> bool:
    """Accept the profile result contract while tolerating older test doubles."""
    if isinstance(result, bool):
        return result
    if isinstance(result, dict) and "ok" in result:
        return bool(result["ok"])
    explicit = getattr(result, "ok", None)
    if explicit is not None:
        return bool(explicit)
    return _fast_mode_result_status(result) not in {
        "snapshot_invalid", "snapshot_write_failed", "snapshot_cleanup_failed",
        "config_write_failed", "invalid_updates", "profile_busy", "restore_failed",
        "unsafe_path_state", "unsafe_profile_definition", "verification_pending",
        "failed",
    }


def get_fast_mode_dashboard_state(api: Any | None = None) -> FastModeDashboardState:
    """Return current profile state without exposing snapshot values or secrets."""
    try:
        api = api or _load_fast_mode_api()
        inspected = api.inspect_fast_mode_profile()
        status = _fast_mode_result_status(inspected)
        keys = tuple(str(key).upper() for key in api.managed_fast_mode_keys())
        inspected_active = (
            inspected.get("active") if isinstance(inspected, dict)
            else getattr(inspected, "active", None)
        )
        active = (
            bool(inspected_active)
            if inspected_active is not None
            else bool(api.is_fast_mode_profile_active())
        )
        return FastModeDashboardState(status=status, active=active, managed_keys=keys)
    except Exception as exc:
        logger.warning(f"dashboard: Fast Mode status unavailable: {exc}")
        return FastModeDashboardState()


def format_fast_mode_managed_status(
    key: str,
    *,
    state: FastModeDashboardState | None = None,
    api: Any | None = None,
) -> str:
    """Build the safe per-field status shown below a managed setting."""
    try:
        api = api or _load_fast_mode_api()
        state = state or get_fast_mode_dashboard_state(api)
        normalized = str(key).strip().upper()
        if not state.active or normalized not in state.managed_keys:
            return ""
        forced = api.get_fast_mode_forced_value(normalized)
        original = api.get_fast_mode_original_value(normalized)
        restored = "default (setting was absent)" if original is None else str(original)
        return f"Managed by Fast Mode — forced: {forced}; restored later: {restored}"
    except Exception as exc:
        logger.warning(f"dashboard: Fast Mode field status unavailable: {exc}")
        return "Managed by Fast Mode"


def format_fast_mode_summary(state: FastModeDashboardState) -> str:
    count = len(state.managed_keys)
    if state.active:
        return f"Fast Mode: active — {count} settings temporarily managed"
    if state.status == "snapshot_invalid":
        return "Fast Mode: snapshot invalid — use Medic Checker recovery"
    if state.status == "profile_busy":
        return "Fast Mode: another process is updating the profile"
    if state.status == "verification_pending":
        return "Fast Mode: write verification interrupted — reconciliation required"
    if state.status in {"unsafe_path_state", "unsafe_profile_definition"}:
        return "Fast Mode: safety validation failed"
    if state.status in {"restore_required", "restoration_pending"}:
        return "Fast Mode: restoration pending"
    if state.status in {"cleanup_pending", "restored_snapshot_retained"}:
        return "Fast Mode: disabled — recovery snapshot cleanup pending"
    if state.status in {"activation_required", "snapshot_missing"}:
        return "Fast Mode: activation pending"
    if state.status == "unavailable":
        return "Fast Mode: status unavailable"
    return "Fast Mode: disabled"


def _fast_mode_confirmation_action(
    *,
    current_enabled: bool,
    desired_enabled: bool,
    status: str,
) -> str | None:
    """Return the transition that needs consent using freshly inspected state."""
    normalized = str(status or "unknown").strip().lower()
    if desired_enabled and (
        not current_enabled or normalized in _FAST_MODE_ACTIVATION_PENDING
    ):
        return "activate"
    if not desired_enabled and (
        current_enabled or normalized in _FAST_MODE_RESTORATION_PENDING
    ):
        return "restore"
    return None


def get_fast_mode_apply_confirmation(
    updates: dict[str, str],
    api: Any | None = None,
    config_path: Path | None = None,
) -> tuple[str | None, FastModeDashboardState]:
    """Re-read disk/profile state immediately before a dashboard transaction."""
    _text, disk_values = read_config_document(config_path or CONFIG_PATH)
    current_enabled = str(disk_values.get("FASTER_MODE", "no")).strip().lower() in {
        "yes", "true", "1", "on",
    }
    desired_raw = updates.get("FASTER_MODE")
    desired_enabled = (
        str(desired_raw).strip().lower() in {"yes", "true", "1", "on"}
        if desired_raw is not None else current_enabled
    )
    state = get_fast_mode_dashboard_state(api)
    return (
        _fast_mode_confirmation_action(
            current_enabled=current_enabled,
            desired_enabled=desired_enabled,
            status=state.status,
        ),
        state,
    )


def apply_dashboard_config_updates(updates: dict[str, str], api: Any | None = None) -> Any:
    """Apply one coordinated config/Fast-Mode transaction."""
    api = api or _load_fast_mode_api()
    return api.apply_config_updates_with_fast_mode(dict(updates))


def format_fast_mode_failure(status: str) -> str:
    normalized = str(status or "unknown").strip().lower()
    if normalized == "profile_busy":
        return (
            "Fast Mode is currently being updated by another process.\n\n"
            "Close the other Agetha or Medic Checker instance and try again. "
            "No settings were changed."
        )
    if normalized == "verification_pending":
        return (
            "The configuration write may have completed, but verification was interrupted.\n\n"
            "Recovery metadata was preserved. Run Fast Mode reconciliation again to "
            "inspect the current disk state safely."
        )
    if normalized in {"unsafe_path_state", "unsafe_profile_definition"}:
        return "Fast Mode safety validation failed. No transaction was started."
    return (
        "Could not apply settings safely. The existing configuration and recovery "
        f"snapshot were preserved.\n\nStatus: {normalized}"
    )


PROJECT_LINKS: dict[str, str] = {
    "Fork repository": "https://github.com/SiriusNovyx/Agetha.exe",
    "Report a fork issue": "https://github.com/SiriusNovyx/Agetha.exe/issues",
    "Fork documentation": "https://github.com/SiriusNovyx/Agetha.exe#readme",
    "Original upstream": "https://github.com/tamsamas/Agetha.exe",
    "GNU GPLv3 license": "https://github.com/SiriusNovyx/Agetha.exe/blob/main/LICENSE",
}


def open_project_link(label: str) -> bool:
    """Open only a hardcoded project HTTPS URL after an explicit user click."""
    url = PROJECT_LINKS.get(label)
    if not url or not url.startswith("https://"):
        return False
    try:
        return bool(webbrowser.open_new_tab(url))
    except Exception:
        return False

_POLL_MS = 2000

# Curated voice ids shown in Settings for each VOICE_TTS_ENGINE.
_EDGE_TTS_VOICES: tuple[str, ...] = (
    "en-US-AvaNeural",
    "en-US-JennyNeural",
    "en-US-GuyNeural",
    "en-US-AriaNeural",
    "en-US-ChristopherNeural",
    "en-US-EricNeural",
    "en-US-MichelleNeural",
    "en-US-RogerNeural",
    "en-GB-SoniaNeural",
    "en-GB-RyanNeural",
    "en-AU-NatashaNeural",
    "en-AU-WilliamNeural",
)
_KOKORO_VOICES: tuple[str, ...] = (
    "af_heart",
    "af_bella",
    "af_nicole",
    "af_sarah",
    "af_sky",
    "am_adam",
    "am_michael",
    "bf_emma",
    "bf_isabella",
    "bm_george",
    "bm_lewis",
)
_PYTTSX3_FALLBACK_VOICES: tuple[str, ...] = ("Zira", "David", "Hazel", "Mark")


def _list_pyttsx3_voices() -> tuple[str, ...]:
    """Installed OS TTS short names; falls back to common Windows voices."""
    try:
        import pyttsx3  # type: ignore[import-untyped]

        engine = pyttsx3.init()
        found: list[str] = []
        seen: set[str] = set()
        for voice in engine.getProperty("voices") or []:
            name = str(getattr(voice, "name", "") or "").strip()
            if not name:
                continue
            # Prefer a short token the existing substring matcher can use (e.g. Zira).
            short = name
            for token in ("Zira", "David", "Hazel", "Mark", "Susan", "George"):
                if token.lower() in name.lower():
                    short = token
                    break
            if short.lower() not in seen:
                seen.add(short.lower())
                found.append(short)
        if found:
            return tuple(found)
    except Exception:
        pass
    return _PYTTSX3_FALLBACK_VOICES


def _tts_voice_choices(engine: str) -> tuple[str, ...]:
    """Voice dropdown options for the selected TTS engine."""
    name = (engine or "pyttsx3").strip().lower()
    if name == "edge_tts":
        return _EDGE_TTS_VOICES
    if name == "kokoro":
        return _KOKORO_VOICES
    return _list_pyttsx3_voices()


def _default_tts_voice(engine: str) -> str:
    choices = _tts_voice_choices(engine)
    return choices[0] if choices else ""


# kind: "bool" | "text" | "choice"
# needs_restart: True = Agetha must restart for full effect
# launcher_only: True = Medic_Checker only (no Agetha restart warning)
_SETTING_SECTIONS: tuple[tuple[str, tuple[tuple[str, str, bool, tuple[str, ...]], ...]], ...] = (
    (
        "Hot-reload (apply without restart)",
        (
            ("ENABLE_AMBIENT_POLLS", "bool", False, ()),
            ("DRY_RUN_MODE", "bool", False, ()),
            ("ENABLE_GLITCH_EFFECTS", "bool", False, ()),
            ("GLITCH_MOOD_AUTO", "bool", False, ()),
            ("GLITCH_FULLSCREEN", "bool", False, ()),
            ("GLITCH_MAX_DURATION_MS", "text", False, ()),
            ("GLITCH_DEFAULT_STYLE", "choice", False, (
                "scanlines", "static", "rgb_split", "flicker", "bsod", "matrix", "tear",
            )),
            ("SCREEN_POLL_INTERVAL_SEC", "text", False, ()),
            ("TOUCH_COOLDOWN_SEC", "text", False, ()),
            ("WAKE_DELAY_SEC", "text", False, ()),
            ("LOAF_TIMER_MIN", "text", False, ()),
            ("TARGET_APP_ALIASES", "text", False, ()),
            ("WINDOW_PICKER_ON_AMBIGUOUS", "bool", False, ()),
            ("WEB_FETCH_MAX_CHARS", "text", False, ()),
            ("WEB_TIMEOUT_SEC", "text", False, ()),
            ("WEB_SEARCH_MAX_RESULTS", "text", False, ()),
        ),
    ),
    (
        "AI Backend — restart required",
        (
            ("USE_LOCAL_AI", "bool", True, ()),
            ("ENABLE_GROQ", "bool", True, ()),
            ("ENABLE_GEMINI", "bool", True, ()),
            ("GEMINI_MODEL", "text", True, ()),
            ("ENABLE_OPENROUTER", "bool", True, ()),
            ("OPENROUTER_MODEL", "text", True, ()),
            ("GROQ_MODEL", "text", True, ()),
            ("LOCAL_AI_MODEL", "text", True, ()),
            ("LOCAL_AI_TIMEOUT", "text", True, ()),
            ("FASTER_MODE", "bool", True, ()),
        ),
    ),
    (
        "AI Tuning — restart required",
        (
            ("AI_TEMPERATURE", "text", True, ()),
            ("AI_MAX_TOKENS", "text", True, ()),
            ("AI_TOP_P", "text", True, ()),
            ("ENABLE_STREAMING", "bool", True, ()),
            ("ENABLE_DATETIME_CONTEXT", "bool", True, ()),
            ("DATETIME_INCLUDE_SECONDS", "bool", True, ()),
            ("DATETIME_INCLUDE_TIMEZONE", "bool", True, ()),
        ),
    ),
    (
        "Permissions — restart required",
        (
            ("ENABLE_COMMAND_EXECUTION", "bool", True, ()),
            ("ENABLE_WINDOW_CONTROL", "bool", True, ()),
            ("ENABLE_COMMAND_CONFIRMATIONS", "bool", True, ()),
            ("FORCE_CLOSE_AUTO_ALLOW", "bool", True, ()),
            ("PROTECTED_PROCESSES", "text", True, ()),
        ),
    ),
    (
        "Terminal Sentinel — restart required",
        (
            ("ENABLE_TERMINAL_SENTINEL", "bool", True, ()),
            ("TERMINAL_SENTINEL_APPS", "text", True, ()),
            ("TERMINAL_SENTINEL_TITLE_PATTERNS", "text", True, ()),
            ("TERMINAL_SENTINEL_COOLDOWN_SEC", "text", True, ()),
        ),
    ),
    (
        "Agent continuation & process awareness — restart required",
        (
            ("ENABLE_AGENT_CONTINUATION", "bool", True, ()),
            ("AGENT_MAX_STEPS", "text", True, ()),
            ("AGENT_MAX_DURATION_SEC", "text", True, ()),
            ("AGENT_MAX_TOOL_RESULT_CHARS", "text", True, ()),
            ("ENABLE_PROCESS_AWARENESS", "bool", True, ()),
            ("PROCESS_CONTEXT_MODE", "choice", True, (
                "off", "foreground_only", "visible_apps", "all_processes",
            )),
            ("PROCESS_MAX_VISIBLE_APPS", "text", True, ()),
            ("PROCESS_CONTEXT_EXCLUDED_APPS", "text", True, ()),
        ),
    ),
    (
        "Computer Use Lite — restart required",
        (
            ("ENABLE_COMPUTER_USE", "bool", True, ()),
            ("COMPUTER_USE_MAX_STEPS", "text", True, ()),
            ("COMPUTER_USE_TIMEOUT_SEC", "text", True, ()),
            ("COMPUTER_USE_PLANNER_PROVIDER", "choice", True, (
                "inherit", "ollama", "groq", "gemini", "openrouter",
            )),
            ("COMPUTER_USE_PLANNER_MODEL", "text", True, ()),
            ("COMPUTER_USE_PLANNER_CONFIDENCE_MIN", "text", True, ()),
            ("COMPUTER_USE_RECOVERY_AFTER_FAILURES", "text", True, ()),
            ("COMPUTER_USE_MAX_RECOVERY_CALLS", "text", True, ()),
            ("COMPUTER_USE_ALLOWED_APPS", "text", True, ()),
        ),
    ),
    (
        "Presence & realism (v4)",
        (
            ("ENABLE_CIRCADIAN_RHYTHM", "bool", False, ()),
            ("RHYTHM_NIGHT_START", "text", False, ()),
            ("RHYTHM_NIGHT_END", "text", False, ()),
            ("ENABLE_DREAMS", "bool", False, ()),
            ("DREAMS_MAX_ENTRIES", "text", False, ()),
            ("ENABLE_TASKS", "bool", False, ()),
            ("TASKS_MAX_ENTRIES", "text", False, ()),
        ),
    ),
    (
        "Emotion engine (v5)",
        (
            ("ENABLE_EMOTION_ENGINE", "bool", False, ()),
            ("EMOTION_BASELINE_VALENCE", "text", False, ()),
            ("EMOTION_BASELINE_AROUSAL", "text", False, ()),
            ("EMOTION_BASELINE_TRUST", "text", False, ()),
            ("EMOTION_BASELINE_LONELINESS", "text", False, ()),
            ("EMOTION_DECAY_PER_HOUR", "text", False, ()),
            ("EMOTION_HISTORY_MAX", "text", False, ()),
        ),
    ),
    (
        "Windows integration (v5) — tray needs restart",
        (
            # Command/status gates re-read via get_settings(); tray starts once at launch.
            ("ENABLE_AUTOSTART_CONTROL", "bool", False, ()),
            ("ENABLE_THEME_CONTROL", "bool", False, ()),
            ("ENABLE_STATUS_PROVIDERS", "bool", False, ()),
            ("STATUS_POLL_INTERVAL_SEC", "text", False, ()),
            ("ENABLE_TRAY", "bool", True, ()),
            ("TRAY_BACKGROUND_CLOSE", "bool", False, ()),
        ),
    ),
    (
        "Memory & Context — restart required",
        (
            ("MEMORY_CHARS", "text", True, ()),
            ("HISTORY_LIMIT", "text", True, ()),
            ("FILE_READ_CHARS", "text", True, ()),
            ("EPISODIC_PROMPT_LIMIT", "text", True, ()),
            ("EPISODIC_ENTRY_MAX_CHARS", "text", True, ()),
            ("EPISODIC_MAX_ENTRIES", "text", True, ()),
            ("ENABLE_LONGTERM_MEMORY", "bool", True, ()),
            ("LONGTERM_MEMORY_MAX_RESULTS", "text", True, ()),
            ("LONGTERM_MEMORY_MAX_CHARS", "text", True, ()),
            ("ENABLE_WEB_RAG", "bool", True, ()),
            ("ENABLE_COMPANION_STATS_CONTEXT", "bool", True, ()),
        ),
    ),
    (
        "Mood snap — restart required",
        (
            ("ENABLE_ATTENTION_SNAP", "bool", True, ()),
            ("MOOD_SNAP_MANIC_SEC", "text", True, ()),
            ("MOOD_SNAP_ANGRY_SEC", "text", True, ()),
            ("MOOD_SNAP_PARANOID_SEC", "text", True, ()),
            ("MOOD_SNAP_DOMINANT_SEC", "text", True, ()),
            ("MOOD_SNAP_SURPRISED_SEC", "text", True, ()),
            ("MOOD_SNAP_EXCITED_SEC", "text", True, ()),
            ("MOOD_SNAP_HAPPY_SEC", "text", True, ()),
            ("MOOD_SNAP_NEUTRAL_SEC", "text", True, ()),
            ("MOOD_SNAP_THINKING_SEC", "text", True, ()),
            ("MOOD_SNAP_VULNERABLE_SEC", "text", True, ()),
            ("MOOD_SNAP_MELANCHOLIC_SEC", "text", True, ()),
            ("MOOD_SNAP_SAD_SEC", "text", True, ()),
            ("MOOD_SNAP_WHISPER_SEC", "text", True, ()),
        ),
    ),
    (
        "Screen / OCR — restart required",
        (
            ("ENABLE_SCREEN_READER", "bool", True, ()),
            ("ENABLE_PRINTWINDOW_FALLBACK", "bool", True, ()),
            ("OCR_MAX_DIMENSION", "text", True, ()),
            ("OCR_FOCUSED_WINDOW_ONLY", "bool", True, ()),
            ("OCR_CHANGE_DETECTION", "bool", True, ()),
            ("OCR_CHANGE_THRESHOLD", "text", True, ()),
            ("OCR_FORCE_REFRESH_SECONDS", "text", True, ()),
            ("OCR_STATE_EXPIRY_SECONDS", "text", True, ()),
            ("OCR_PATTERN_COOLDOWN_SECONDS", "text", True, ()),
            ("OCR_PATTERN_CONFIRM_SCANS", "text", True, ()),
            ("OCR_LOW_CONFIDENCE_CONFIRM_SCANS", "text", True, ()),
            ("OCR_PATTERN_CLEAR_SCANS", "text", True, ()),
            ("OCR_MIN_WORD_CONFIDENCE", "text", True, ()),
            ("OCR_MIN_PATTERN_CONFIDENCE", "text", True, ()),
            ("OCR_PREPROCESSING", "choice", True, ("basic", "auto")),
            ("OCR_LANGUAGES", "text", True, ()),
            ("OCR_PSM", "choice", True, ("auto", "3", "6", "11")),
            ("OCR_EXCLUDED_APPS", "text", True, ()),
            ("OCR_EXCLUDED_TITLE_PATTERNS", "text", True, ()),
            ("OCR_REDACT_SENSITIVE_TEXT", "bool", True, ()),
            ("INCLUDE_WINDOW_TITLE_IN_CONTEXT", "bool", True, ()),
            ("TESSERACT_PATH", "text", True, ()),
            ("DEEP_OCR_BACKEND", "choice", True, ("none", "unlimited_ocr")),
            ("UNLIMITED_OCR_SERVER_URL", "text", True, ()),
            ("UNLIMITED_OCR_MODEL", "text", True, ()),
            ("UNLIMITED_OCR_TIMEOUT_SECONDS", "text", True, ()),
            ("UNLIMITED_OCR_ALLOW_REMOTE", "bool", True, ()),
            ("DEEP_OCR_MAX_OUTPUT_CHARS", "text", True, ()),
            ("OCR_CUSTOM_PATTERNS", "text", True, ()),
            ("OCR_PAUSE_WHILE_TYPING_SEC", "text", True, ()),
        ),
    ),
    (
        "UI — restart required",
        (
            ("WINDOW_TOPMOST", "bool", True, ()),
            ("UI_SCALE", "text", True, ()),
            ("WINDOW_START_X", "text", True, ()),
            ("WINDOW_START_Y", "text", True, ()),
            ("SUBTITLE_CHAR_DELAY", "text", True, ()),
            ("ANIMATION_SPEED", "text", True, ()),
            ("WINDOW_MOVE_SMOOTH", "bool", True, ()),
            ("WINDOW_MOVE_DURATION_MS", "text", True, ()),
            ("ENABLE_CRT_CLOSE_ANIMATION", "bool", True, ()),
            ("REDUCED_MOTION", "bool", True, ()),
            ("ENABLE_MOOD_GLOW", "bool", True, ()),
            ("MOOD_GLOW_ANIMATED", "bool", True, ()),
            ("MOOD_GLOW_INTERVAL_MS", "text", True, ()),
            ("ENABLE_MOOD_MOTION", "bool", True, ()),
            ("MOOD_MOTION_COOLDOWN_SECONDS", "text", True, ()),
            ("ENABLE_FILE_DRAG_DROP", "bool", True, ()),
            ("GITHUB_RELEASES_URL", "text", True, ()),
        ),
    ),
    (
        "Voice — restart required",
        (
            ("ENABLE_VOICE", "bool", True, ()),
            ("USE_LOCAL_STT", "bool", True, ()),
            ("VOICE_OUTPUT_MODE", "choice", True, ("bleeps_only", "tts_only", "both")),
            ("VOICE_TTS_ENGINE", "choice", True, ("pyttsx3", "edge_tts", "kokoro")),
            ("TTS_RATE", "text", True, ()),
            ("TTS_VOLUME", "text", True, ()),
            # Choices filled dynamically from VOICE_TTS_ENGINE (see settings UI).
            ("TTS_VOICE_NAME", "choice", True, ()),
        ),
    ),
    (
        "Medic_Checker (launcher — next Medic run)",
        (
            ("SKIP_TESSERACT_CHECK", "bool", False, ()),
            ("SKIP_ASSET_CHECK", "bool", False, ()),
            ("AUTO_PIP_INSTALL", "bool", False, ()),
            ("CREATE_DESKTOP_SHORTCUT", "bool", False, ()),
            ("CHECK_FOR_UPDATES", "bool", False, ()),
        ),
    ),
)

_PROFILE_SETTING_SECTION = (
    "Capability profile",
    (("COMPACT_MODE", "bool", False, ()),),
)

_COMPACT_SETTING_SECTION_TITLES = frozenset({
    "AI Backend — restart required",
    "AI Tuning — restart required",
    "Memory & Context — restart required",
    "UI — restart required",
    "Voice — restart required",
})


@dataclass(frozen=True, slots=True)
class DashboardPresentation:
    """Side-effect-free view of the Dashboard surfaces for one profile."""

    profile: CapabilityProfile
    compact_mode_on: bool
    tabs: tuple[str, ...]
    setting_sections: tuple[
        tuple[str, tuple[tuple[str, str, bool, tuple[str, ...]], ...]], ...
    ]
    show_system_monitor: bool
    show_senses: bool

    @property
    def setting_keys(self) -> tuple[str, ...]:
        return tuple(
            key
            for _section, items in self.setting_sections
            for key, _kind, _needs_restart, _choices in items
        )


@dataclass(frozen=True, slots=True)
class DashboardProfileUpdate:
    """Generic settings plus an isolated capability-profile request."""

    generic_updates: dict[str, str]
    requested_compact_mode: bool | None


def split_dashboard_profile_update(
    updates: dict[str, str],
    *,
    current_compact_mode: bool,
) -> DashboardProfileUpdate:
    """Keep mode transitions out of the ordinary config patch transaction."""
    generic = {
        key: value for key, value in updates.items()
        if str(key).strip().upper() != "COMPACT_MODE"
    }
    raw_request = next(
        (
            value for key, value in updates.items()
            if str(key).strip().upper() == "COMPACT_MODE"
        ),
        None,
    )
    if raw_request is None:
        requested = None
    else:
        requested_value = str(raw_request).strip().casefold() in {
            "1", "yes", "true", "on",
        }
        requested = (
            requested_value
            if requested_value is not bool(current_compact_mode)
            else None
        )
    return DashboardProfileUpdate(generic, requested)


def build_dashboard_presentation(settings: object) -> DashboardPresentation:
    """Return the visible Dashboard model without probing the host."""
    policy = CapabilityPolicy.from_settings(settings)
    compact = policy.profile is CapabilityProfile.COMPACT
    sections = (
        (_PROFILE_SETTING_SECTION,)
        + tuple(
            section for section in _SETTING_SECTIONS
            if not compact or section[0] in _COMPACT_SETTING_SECTION_TITLES
        )
    )
    tabs = (
        ("Virus Registry", "Notepad", "About", "Settings")
        if compact
        else ("System Monitor", "Virus Registry", "Notepad", "About", "Settings")
    )
    return DashboardPresentation(
        profile=policy.profile,
        compact_mode_on=compact,
        tabs=tabs,
        setting_sections=sections,
        show_system_monitor=not compact,
        show_senses=not compact,
    )

_LAUNCHER_KEYS = frozenset({
    "SKIP_TESSERACT_CHECK", "SKIP_ASSET_CHECK", "AUTO_PIP_INSTALL",
    "CREATE_DESKTOP_SHORTCUT", "CHECK_FOR_UPDATES",
})

_RESTART_KEYS = frozenset(
    key
    for _section, items in _SETTING_SECTIONS
    for key, _kind, needs_restart, _choices in items
    if needs_restart
)


def _w95_progress_row(parent: tk.Misc, label: str, pct_var: tk.DoubleVar, text_var: tk.StringVar) -> tk.Canvas:
    """Win95-style sunken progress bar with caption."""
    row = tk.Frame(parent, bg=W95_BG)
    row.pack(fill="x", padx=8, pady=4)
    tk.Label(row, text=f"{label}:", width=12, anchor="w", bg=W95_BG, fg=W95_TEXT, font=W95_FONT).pack(side="left")
    bar_frame = tk.Frame(row, bg=W95_SHADOW, bd=1, relief="sunken")
    bar_frame.pack(side="left", padx=(0, 6))
    canvas = tk.Canvas(bar_frame, width=180, height=14, bg="#ffffff", highlightthickness=0, bd=0)
    canvas.pack()
    tk.Label(row, textvariable=text_var, anchor="w", bg=W95_BG, fg=W95_TEXT, font=W95_FONT).pack(side="left", fill="x", expand=True)

    def _draw(_evt: tk.Event | None = None) -> None:
        canvas.delete("all")
        w = max(canvas.winfo_width(), 180)
        h = max(canvas.winfo_height(), 14)
        pct = max(0.0, min(100.0, float(pct_var.get())))
        fill_w = int((w - 2) * pct / 100.0)
        canvas.create_rectangle(1, 1, w - 1, h - 1, fill="#ffffff", outline="")
        if fill_w > 0:
            canvas.create_rectangle(1, 1, 1 + fill_w, h - 1, fill="#008080", outline="")

    pct_var.trace_add("write", lambda *_: _draw())
    canvas.bind("<Configure>", _draw)
    _draw()
    return canvas


def read_notepad_text() -> str:
    """Read memory/notepad.txt; returns empty string on failure."""
    try:
        NOTEPAD_FILE.parent.mkdir(parents=True, exist_ok=True)
        if NOTEPAD_FILE.exists():
            return NOTEPAD_FILE.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning(f"dashboard: notepad read failed: {exc}")
    return ""


def write_notepad_text(content: str) -> bool:
    """Persist dashboard notepad text atomically; never raise to Tk callbacks."""
    try:
        write_atomic(NOTEPAD_FILE, content)
        return True
    except Exception as exc:
        logger.warning(f"dashboard: notepad save failed: {exc}")
        return False


def _process_count() -> str:
    try:
        import psutil
        return str(len(psutil.pids()))
    except Exception:
        return "N/A"


def _system_snapshot() -> dict[str, str | float]:
    cpu = ram = disk = "N/A"
    cpu_pct = ram_pct = disk_pct = 0.0
    try:
        import psutil
        cpu_pct = float(psutil.cpu_percent(interval=None))
        cpu = f"{cpu_pct:.1f}%"
        mem = psutil.virtual_memory()
        ram_pct = float(mem.percent)
        ram = f"{ram_pct:.1f}% ({mem.used // (1024 * 1024)} MB used)"
        try:
            import sys
            disk_path = "C:\\" if sys.platform == "win32" else "/"
            du = psutil.disk_usage(disk_path)
            disk_pct = float(du.percent)
            disk = f"{disk_pct:.1f}% ({du.free // (1024 ** 3)} GB free)"
        except Exception:
            disk = "N/A"
    except Exception:
        pass
    return {
        "cpu": cpu,
        "ram": ram,
        "disk": disk,
        "cpu_pct": cpu_pct,
        "ram_pct": ram_pct,
        "disk_pct": disk_pct,
        "processes": _process_count(),
    }


class DashboardHandle:
    """Owned Dashboard window with an idempotent, timer-safe close path."""

    def __init__(self, win: tk.Toplevel) -> None:
        self.win = win
        self._close_callback: Callable[[], None] | None = None
        self._closed = False
        self._closing = False

    def _bind_close(self, callback: Callable[[], None]) -> None:
        self._close_callback = callback

    def _mark_closed(self) -> None:
        self._closed = True
        self._closing = False

    @property
    def is_open(self) -> bool:
        if self._closed:
            return False
        try:
            exists = bool(self.win.winfo_exists())
        except Exception:
            exists = False
        if not exists:
            self._mark_closed()
        return exists

    def present(self) -> bool:
        """Raise the existing Dashboard instead of creating a duplicate."""
        if not self.is_open:
            return False
        try:
            self.win.deiconify()
            self.win.lift()
            return True
        except Exception:
            self.close()
            return False

    def close(self) -> None:
        """Close through the window-owned cleanup callback exactly once."""
        if self._closed or self._closing:
            return
        self._closing = True
        callback = self._close_callback
        if callback is None:
            try:
                self.win.destroy()
            except Exception:
                pass
            finally:
                self._mark_closed()
            return
        try:
            callback()
        finally:
            if not self._closed:
                self._mark_closed()


def open_dashboard(
    parent: tk.Misc,
    app_settings,
    *,
    on_open_senses: Callable[[], None] | None = None,
    on_compact_mode_request: Callable[[bool], None] | None = None,
    on_close: Callable[[DashboardHandle], None] | None = None,
) -> DashboardHandle:
    """Open a Toplevel dashboard with System / Virus / Notepad / Settings tabs."""
    import sys
    from agetha.ui.w95_window import (
        apply_borderless_win95,
        minimize_managed,
        refresh_borderless,
        show_borderless,
    )
    presentation = build_dashboard_presentation(app_settings)

    try:
        ui_scale = float(getattr(parent, "_agetha_ui_scale", 1.0))
    except (TypeError, ValueError):
        ui_scale = 1.0

    def _px(value: int) -> int:
        return max(1, int(round(value * ui_scale)))

    win = tk.Toplevel(parent)
    handle = DashboardHandle(win)
    win.title("Agetha — Dashboard")
    apply_borderless_win95(win, parent, topmost=True)
    try:
        from agetha.utils import apply_window_icon
        apply_window_icon(win)
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            win.tk.call("wm", "transient", win._w, "")
        except Exception:
            pass
    win.configure(bg=W95_BG)
    win.geometry(f"{_px(560)}x{_px(480)}")
    win.minsize(_px(460), _px(360))

    _closing = False
    _after_jobs: list[str] = []
    _close_hooks: list = []

    def _schedule(ms: int, func) -> str:
        job = win.after(ms, func)
        _after_jobs.append(job)
        return job

    def _cancel_jobs() -> None:
        for job in _after_jobs:
            try:
                win.after_cancel(job)
            except Exception:
                pass
        _after_jobs.clear()

    # ── Outer raised bevel (whole window border) ────────────────────────────
    outer = tk.Frame(win, bg=W95_BG, relief="raised", bd=2)
    outer.pack(fill="both", expand=True)

    # ── Win95 title bar ─────────────────────────────────────────────────────
    title_bar = tk.Frame(outer, bg=W95_TITLE_BG, height=_px(18))
    title_bar.pack(fill="x", padx=2, pady=(2, 0))
    title_bar.pack_propagate(False)

    title_lbl = tk.Label(
        title_bar, text="⚠  Agetha — Dashboard",
        bg=W95_TITLE_BG, fg=W95_TITLE_FG,
        font=W95_FONT_BOLD, anchor="w", padx=4,
    )
    title_lbl.pack(side="left", fill="y")

    _btn_font = ("MS Sans Serif", 7, "bold")
    _btn_kw = dict(
        bg=W95_BTN_BG, fg=W95_TEXT, font=_btn_font,
        relief="raised", bd=2, width=2,
        activebackground=W95_BTN_BG, activeforeground=W95_TEXT,
    )

    def _notify_closed() -> None:
        handle._mark_closed()
        if on_close is not None:
            try:
                on_close(handle)
            except Exception as exc:
                logger.debug(
                    "Dashboard close notification failed: %s",
                    type(exc).__name__,
                )

    def _close_dashboard() -> None:
        nonlocal _closing
        if _closing:
            return
        _closing = True
        for hook in list(_close_hooks):
            try:
                hook()
            except Exception:
                pass
        _close_hooks.clear()
        _cancel_jobs()
        try:
            _save_notepad()
        except Exception as exc:
            logger.debug("Dashboard notepad close-save failed: %s", type(exc).__name__)
        try:
            win.destroy()
        except Exception:
            pass
        finally:
            _notify_closed()

    def _on_destroy(event: tk.Event | None = None) -> None:
        """Cancel owned work when the parent/Tk destroys this window."""
        nonlocal _closing
        if event is not None and getattr(event, "widget", None) is not win:
            return
        if _closing:
            return
        _closing = True
        for hook in list(_close_hooks):
            try:
                hook()
            except Exception:
                pass
        _close_hooks.clear()
        _cancel_jobs()
        _notify_closed()

    handle._bind_close(_close_dashboard)
    win.protocol("WM_DELETE_WINDOW", _close_dashboard)
    win.bind("<Destroy>", _on_destroy, add="+")

    tk.Button(
        title_bar, text="✕", command=_close_dashboard, **_btn_kw,
    ).pack(side="right", padx=(0, 2), pady=1)

    def _minimize() -> None:
        if sys.platform == "win32":
            try:
                win.overrideredirect(False)
                win.iconify()
            except Exception:
                return

            def _bind_restore() -> None:
                def _on_map(_e: tk.Event | None = None) -> None:
                    try:
                        if win.winfo_exists():
                            refresh_borderless(win)
                            win.unbind("<Map>")
                    except Exception:
                        pass

                win.bind("<Map>", _on_map)

            _schedule(250, _bind_restore)
        else:
            try:
                win.grab_release()
            except Exception:
                pass
            minimize_managed(win)

    tk.Button(
        title_bar, text="─", command=_minimize, **_btn_kw,
    ).pack(side="right", padx=(0, 1), pady=1)

    _drag_x = _drag_y = 0
    _win_x = _win_y = 0

    def _drag_start(e: tk.Event) -> None:
        nonlocal _drag_x, _drag_y, _win_x, _win_y
        _drag_x, _drag_y = e.x_root, e.y_root
        _win_x, _win_y = win.winfo_x(), win.winfo_y()

    def _drag_motion(e: tk.Event) -> None:
        nonlocal _drag_x, _drag_y, _win_x, _win_y
        dx = e.x_root - _drag_x
        dy = e.y_root - _drag_y
        _win_x += dx
        _win_y += dy
        win.geometry(f"+{_win_x}+{_win_y}")
        _drag_x, _drag_y = e.x_root, e.y_root

    for w in (title_bar, title_lbl):
        w.bind("<ButtonPress-1>", _drag_start)
        w.bind("<B1-Motion>", _drag_motion)

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True, padx=6, pady=6)

    # ── System Monitor ────────────────────────────────────────────────────────
    sys_frame = tk.Frame(notebook, bg=W95_BG)
    if presentation.show_system_monitor:
        notebook.add(sys_frame, text="System Monitor")

    sys_vars = {
        "cpu": tk.StringVar(value="…"),
        "ram": tk.StringVar(value="…"),
        "disk": tk.StringVar(value="…"),
        "processes": tk.StringVar(value="…"),
    }
    bar_vars = {
        "cpu": tk.DoubleVar(value=0.0),
        "ram": tk.DoubleVar(value=0.0),
        "disk": tk.DoubleVar(value=0.0),
        "heat": tk.DoubleVar(value=0.0),
    }
    heat_lbl = tk.StringVar(value="…")
    for label, key in (
        ("CPU", "cpu"), ("RAM", "ram"), ("Disk", "disk"),
    ):
        _w95_progress_row(sys_frame, label, bar_vars[key], sys_vars[key])
    _w95_progress_row(sys_frame, "Core heat", bar_vars["heat"], heat_lbl)
    row = tk.Frame(sys_frame, bg=W95_BG)
    row.pack(fill="x", padx=8, pady=4)
    tk.Label(row, text="Processes:", width=12, anchor="w", bg=W95_BG, fg=W95_TEXT, font=W95_FONT).pack(side="left")
    tk.Label(row, textvariable=sys_vars["processes"], anchor="w", bg=W95_BG, fg=W95_TEXT, font=W95_FONT).pack(side="left")
    if presentation.show_senses and on_open_senses is not None and bool(
        getattr(app_settings, "enable_senses_panel", True)
    ):
        tk.Button(
            sys_frame,
            text="Open Senses Control Panel",
            font=W95_FONT_BOLD,
            bg=W95_BTN_BG,
            relief="raised",
            bd=2,
            command=on_open_senses,
        ).pack(anchor="w", padx=8, pady=(4, 2))

    def _poll_system() -> None:
        if _closing or not win.winfo_exists():
            return
        snap = _system_snapshot()
        for k in ("cpu", "ram", "disk", "processes"):
            sys_vars[k].set(str(snap.get(k, "N/A")))
        for k in ("cpu_pct", "ram_pct", "disk_pct"):
            try:
                bar_vars[k.replace("_pct", "")].set(float(snap.get(k, 0.0)))
            except (TypeError, ValueError):
                pass
        try:
            from agetha.core.companion_stats import get_stats_summary
            heat = float(get_stats_summary().get("core_heat", 0))
            bar_vars["heat"].set(heat)
            heat_lbl.set(f"{heat:.0f}% (host CPU)")
        except Exception:
            heat_lbl.set("N/A")
        if not _closing and win.winfo_exists():
            _schedule(_POLL_MS, _poll_system)

    if presentation.show_system_monitor:
        _schedule(100, _poll_system)

    # ── Virus Registry ──────────────────────────────────────────────────────
    virus_frame = tk.Frame(notebook, bg=W95_BG)
    notebook.add(virus_frame, text="Virus Registry")

    virus_bars: dict[str, tk.DoubleVar] = {
        "infection_level": tk.DoubleVar(value=0.0),
        "entropy": tk.DoubleVar(value=0.0),
        "affection": tk.DoubleVar(value=0.0),
        "core_heat": tk.DoubleVar(value=0.0),
    }
    virus_lbls = {k: tk.StringVar(value="…") for k in virus_bars}
    for label, key in (
        ("Infection", "infection_level"),
        ("Entropy", "entropy"),
        ("Affection", "affection"),
        ("Core heat", "core_heat"),
    ):
        _w95_progress_row(virus_frame, label, virus_bars[key], virus_lbls[key])

    virus_text = tk.Text(virus_frame, wrap="word", height=8, font=W95_FONT, bg="#ffffff", fg=W95_TEXT)
    virus_text.pack(fill="both", expand=True, padx=8, pady=8)

    def _refresh_virus() -> None:
        if _closing or not win.winfo_exists():
            return
        lines: list[str] = []
        try:
            from agetha.core.companion_stats import get_stats_summary
            stats = get_stats_summary()
            for key in virus_bars:
                try:
                    val = float(stats.get(key, 0))
                    virus_bars[key].set(val)
                    virus_lbls[key].set(f"{val:.0f}%")
                except (TypeError, ValueError):
                    virus_lbls[key].set("?")
            lines.append("Companion stats:")
            for key in ("bytes_devoured", "last_feed_bytes", "max_infection_reached", "uptime_seconds", "last_updated"):
                lines.append(f"  {key}: {stats.get(key, '?')}")
        except Exception as exc:
            lines.append(f"Stats unavailable: {exc}")

        lines.append("")
        try:
            from agetha.core.memory_system import get_memory_stats
            ms = get_memory_stats()
            lines.append("Memory system:")
            soul = ms.get("soul", {})
            episodic = ms.get("episodic", {})
            lines.append(f"  soul exists: {soul.get('exists', '?')} ({soul.get('size_bytes', '?')} bytes)")
            lines.append(f"  episodic count: {episodic.get('count', '?')} / cap {episodic.get('hard_cap', '?')}")
        except Exception as exc:
            lines.append(f"Memory stats unavailable: {exc}")

        try:
            from agetha.core.memory_search import get_longterm_entry_count
            lines.append(f"  longterm entries: {get_longterm_entry_count()}")
        except Exception:
            lines.append("  longterm entries: ?")

        virus_text.delete("1.0", "end")
        virus_text.insert("1.0", "\n".join(lines))
        if not _closing and win.winfo_exists():
            _schedule(_POLL_MS, _refresh_virus)

    _schedule(150, _refresh_virus)

    # ── Notepad ─────────────────────────────────────────────────────────────
    note_frame = tk.Frame(notebook, bg=W95_BG)
    notebook.add(note_frame, text="Notepad")

    note_text = tk.Text(note_frame, wrap="word", font=W95_FONT, bg="#ffffff", fg=W95_TEXT)
    note_text.pack(fill="both", expand=True, padx=8, pady=(8, 4))
    note_text.insert("1.0", read_notepad_text())

    btn_row = tk.Frame(note_frame, bg=W95_BG)
    btn_row.pack(fill="x", padx=8, pady=(0, 8))

    def _save_notepad() -> None:
        write_notepad_text(note_text.get("1.0", "end-1c"))

    tk.Button(btn_row, text="Save", font=W95_FONT, bg=W95_BTN_BG, command=_save_notepad).pack(side="right")
    tk.Button(
        btn_row, text="Reload", font=W95_FONT, bg=W95_BTN_BG,
        command=lambda: (note_text.delete("1.0", "end"), note_text.insert("1.0", read_notepad_text())),
    ).pack(side="right", padx=(0, 4))

    # ── Settings (full config editor, apply once) ─────────────────────────────
    about_frame = tk.Frame(notebook, bg=W95_BG)
    notebook.add(about_frame, text="About")
    tk.Label(
        about_frame,
        text=(
            "Agetha Mod by SiriusNovyx\n"
            "Based on Agetha.exe by tamsamas\n"
            "Licensed under GNU GPLv3"
        ),
        bg=W95_BG, fg=W95_TEXT, font=W95_FONT_BOLD,
        justify="left", anchor="w",
    ).pack(fill="x", padx=14, pady=(16, 10))
    tk.Label(
        about_frame,
        text=(
            "Fork support and issue reporting belong to SiriusNovyx. "
            "Supported platforms: Windows 10/11 and Linux desktop paths; "
            "Windows ARM/Snapdragon uses x64 Python under Prism. macOS is "
            "unsupported. The original "
            "upstream project does not maintain or support this fork."
        ),
        bg=W95_BG, fg=W95_WARN, font=W95_FONT,
        justify="left", anchor="w", wraplength=_px(500),
    ).pack(fill="x", padx=14, pady=(0, 10))
    for link_label in PROJECT_LINKS:
        tk.Button(
            about_frame, text=link_label, font=W95_FONT, bg=W95_BTN_BG,
            command=lambda label=link_label: open_project_link(label),
        ).pack(anchor="w", padx=14, pady=2)

    settings_frame = tk.Frame(notebook, bg=W95_BG)
    notebook.add(settings_frame, text="Settings")

    try:
        from agetha.app_config import get_settings as _get_settings_now
        raw_cfg: dict[str, Any] = dict(_get_settings_now().raw)
    except Exception:
        raw_cfg = dict(getattr(app_settings, "raw", {}) or {})

    editors: dict[str, tuple[str, Any]] = {}
    editor_widgets: dict[str, tk.Widget] = {}
    managed_hints: dict[str, tuple[tk.StringVar, tk.Label]] = {}
    ui_syncing = {"active": False}
    try:
        fast_mode_api: Any | None = _load_fast_mode_api()
    except Exception as exc:
        logger.warning(f"dashboard: Fast Mode controls unavailable: {exc}")
        fast_mode_api = None
    fast_mode_state = get_fast_mode_dashboard_state(fast_mode_api)
    fast_mode_summary_var = tk.StringVar(value=format_fast_mode_summary(fast_mode_state))
    status_var = tk.StringVar(value="Edit settings, then click Apply settings.")

    header = tk.Frame(settings_frame, bg=W95_BG)
    header.pack(fill="x", padx=8, pady=(8, 4))
    tk.Label(
        header,
        text="All config.txt settings (API keys stay in .env). Changes apply only when you click Apply.",
        bg=W95_BG, fg=W95_TEXT, font=W95_FONT, anchor="w", wraplength=_px(500), justify="left",
    ).pack(fill="x")
    tk.Label(
        header,
        text="* = requires restarting Agetha   ·   Medic section applies on next Medic_Checker run",
        bg=W95_BG, fg=W95_WARN, font=W95_FONT, anchor="w",
        wraplength=_px(500), justify="left",
    ).pack(fill="x", pady=(2, 0))
    fast_mode_summary_label = tk.Label(
        header,
        textvariable=fast_mode_summary_var,
        bg=W95_BG,
        fg=W95_WARN if fast_mode_state.status == "snapshot_invalid" else W95_TEXT,
        font=W95_FONT_BOLD,
        anchor="w",
        wraplength=_px(500),
        justify="left",
    )
    fast_mode_summary_label.pack(fill="x", pady=(4, 0))
    # v5.0.0 — live read-only status lines (never fail, never mutate state)
    _live_lines: list[str] = []
    try:
        from agetha.platform.autostart import status_line as _autostart_status
        _live_lines.append(_autostart_status())
    except Exception:
        pass
    try:
        from agetha.features.status_providers import status_summary as _status_sum
        _live_lines.append(_status_sum())
    except Exception:
        pass
    try:
        from agetha.features.tray_scaffold import tray_summary as _tray_sum
        _live_lines.append(_tray_sum())
    except Exception:
        pass
    for _line in _live_lines:
        tk.Label(
            header,
            text=_line,
            bg=W95_BG, fg=W95_TEXT, font=W95_FONT, anchor="w",
            wraplength=_px(500), justify="left",
        ).pack(fill="x", pady=(2, 0))

    canvas_host = tk.Frame(settings_frame, bg=W95_BG)
    canvas_host.pack(fill="both", expand=True, padx=8, pady=4)
    settings_canvas = tk.Canvas(canvas_host, bg=W95_BG, highlightthickness=0, bd=0)
    settings_scroll = tk.Scrollbar(canvas_host, orient="vertical", command=settings_canvas.yview)
    settings_inner = tk.Frame(settings_canvas, bg=W95_BG)
    settings_inner_id = settings_canvas.create_window((0, 0), window=settings_inner, anchor="nw")
    settings_canvas.configure(yscrollcommand=settings_scroll.set)
    settings_scroll.pack(side="right", fill="y")
    settings_canvas.pack(side="left", fill="both", expand=True)

    def _on_inner_configure(_event: tk.Event | None = None) -> None:
        settings_canvas.configure(scrollregion=settings_canvas.bbox("all"))

    def _on_canvas_configure(event: tk.Event) -> None:
        settings_canvas.itemconfigure(settings_inner_id, width=event.width)

    settings_inner.bind("<Configure>", _on_inner_configure)
    settings_canvas.bind("<Configure>", _on_canvas_configure)

    def _wheel(event: tk.Event) -> None:
        if getattr(event, "delta", 0):
            settings_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        elif getattr(event, "num", None) == 4:
            settings_canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            settings_canvas.yview_scroll(1, "units")

    settings_canvas.bind_all("<MouseWheel>", _wheel)
    settings_canvas.bind_all("<Button-4>", _wheel)
    settings_canvas.bind_all("<Button-5>", _wheel)

    def _unbind_wheel() -> None:
        try:
            settings_canvas.unbind_all("<MouseWheel>")
            settings_canvas.unbind_all("<Button-4>")
            settings_canvas.unbind_all("<Button-5>")
        except Exception:
            pass

    _close_hooks.append(_unbind_wheel)

    def _cfg_yes(key: str) -> bool:
        if key == "COMPACT_MODE":
            return presentation.compact_mode_on
        return str(raw_cfg.get(key, "no")).strip().lower() in ("yes", "true", "1", "on")

    def _mark_dirty(_key: str | None = None) -> None:
        if ui_syncing["active"]:
            return
        status_var.set("Unsaved changes — click Apply settings.")

    voice_menu_ref: dict[str, Any] = {}
    voice_hint_var = tk.StringVar(value="")

    for section_title, items in presentation.setting_sections:
        tk.Label(
            settings_inner, text=section_title,
            bg=W95_BG, fg=W95_TEXT, font=W95_FONT_BOLD, anchor="w",
        ).pack(fill="x", pady=(8, 2))
        for key, kind, needs_restart, choices in items:
            row = tk.Frame(settings_inner, bg=W95_BG)
            row.pack(fill="x", pady=1)
            control_row = tk.Frame(row, bg=W95_BG)
            control_row.pack(fill="x")
            label = f"{key} *" if needs_restart else key
            if kind == "bool":
                var = tk.BooleanVar(value=_cfg_yes(key))
                widget = tk.Checkbutton(
                    control_row, text=label, variable=var, command=lambda k=key: _mark_dirty(k),
                    bg=W95_BG, fg=W95_TEXT, font=W95_FONT, activebackground=W95_BG,
                    selectcolor=W95_BG, anchor="w",
                )
                widget.pack(side="left", fill="x", expand=True)
                editors[key] = ("bool", var)
                editor_widgets[key] = widget
            elif kind == "choice":
                tk.Label(control_row, text=label, width=28, anchor="w", bg=W95_BG, fg=W95_TEXT, font=W95_FONT).pack(side="left")
                effective_choices = choices
                if key == "TTS_VOICE_NAME":
                    engine_now = str(raw_cfg.get("VOICE_TTS_ENGINE", "pyttsx3")).strip().lower()
                    if "VOICE_TTS_ENGINE" in editors:
                        engine_now = str(editors["VOICE_TTS_ENGINE"][1].get()).strip().lower()
                    effective_choices = _tts_voice_choices(engine_now)
                current = str(raw_cfg.get(key, effective_choices[0] if effective_choices else "")).strip()
                if effective_choices and current not in effective_choices:
                    # Keep custom/config value visible if possible by prepending it.
                    if key == "TTS_VOICE_NAME" and current:
                        effective_choices = (current, *effective_choices)
                    else:
                        current = effective_choices[0] if effective_choices else current
                if not effective_choices:
                    effective_choices = (current or _default_tts_voice("pyttsx3"),)
                var = tk.StringVar(value=current)
                om = tk.OptionMenu(control_row, var, *effective_choices)
                om.configure(bg=W95_BTN_BG, fg=W95_TEXT, font=W95_FONT, activebackground=W95_BTN_BG)
                om.pack(side="left", fill="x", expand=True)
                var.trace_add("write", lambda *_a, k=key: _mark_dirty(k))
                editors[key] = ("choice", var)
                editor_widgets[key] = om
                if key == "TTS_VOICE_NAME":
                    voice_menu_ref["menu"] = om
                    voice_menu_ref["var"] = var
                    voice_hint_var.set(
                        f"Voices for {str(raw_cfg.get('VOICE_TTS_ENGINE', 'pyttsx3')).strip().lower() or 'pyttsx3'}"
                    )
                    tk.Label(
                        settings_inner, textvariable=voice_hint_var,
                        bg=W95_BG, fg=W95_SHADOW, font=W95_FONT, anchor="w",
                    ).pack(fill="x", padx=(4, 0), pady=(0, 2))
            else:
                tk.Label(control_row, text=label, width=28, anchor="w", bg=W95_BG, fg=W95_TEXT, font=W95_FONT).pack(side="left")
                var = tk.StringVar(value=str(raw_cfg.get(key, "")))
                entry = tk.Entry(control_row, textvariable=var, font=W95_FONT, bg="#ffffff", fg=W95_TEXT)
                entry.pack(side="left", fill="x", expand=True)
                var.trace_add("write", lambda *_a, k=key: _mark_dirty(k))
                editors[key] = ("text", var)
                editor_widgets[key] = entry

            if key in fast_mode_state.managed_keys:
                hint_var = tk.StringVar(value="")
                hint = tk.Label(
                    row,
                    textvariable=hint_var,
                    bg=W95_BG,
                    fg=W95_WARN,
                    font=W95_FONT,
                    anchor="w",
                    justify="left",
                    wraplength=_px(470),
                )
                hint.pack(fill="x", padx=(12, 0), pady=(0, 2))
                managed_hints[key] = (hint_var, hint)

    def _sync_tts_voice_options(*_args: Any, mark_dirty: bool = True) -> None:
        """When VOICE_TTS_ENGINE changes, refresh TTS_VOICE_NAME dropdown choices."""
        try:
            if "VOICE_TTS_ENGINE" not in editors or "menu" not in voice_menu_ref:
                return
            engine = str(editors["VOICE_TTS_ENGINE"][1].get()).strip().lower() or "pyttsx3"
            choices = list(_tts_voice_choices(engine))
            if not choices:
                choices = [_default_tts_voice(engine)]
            voice_var: tk.StringVar = voice_menu_ref["var"]
            om: tk.OptionMenu = voice_menu_ref["menu"]
            prev = str(voice_var.get()).strip()
            menu = om["menu"]
            menu.delete(0, "end")
            for choice in choices:
                menu.add_command(
                    label=choice,
                    command=lambda value=choice, v=voice_var: v.set(value),
                )
            # Keep current voice only if it belongs to the new engine; else pick default.
            if prev in choices:
                voice_var.set(prev)
            else:
                voice_var.set(choices[0])
            voice_hint_var.set(f"Voices for {engine}")
            if mark_dirty:
                _mark_dirty("TTS_VOICE_NAME")
        except Exception as exc:
            logger.warning(f"dashboard: TTS voice options sync failed: {exc}")

    if "VOICE_TTS_ENGINE" in editors:
        editors["VOICE_TTS_ENGINE"][1].trace_add(
            "write",
            lambda *_a: _sync_tts_voice_options(mark_dirty=True),
        )
        _sync_tts_voice_options(mark_dirty=False)

    def _refresh_fast_mode_controls() -> None:
        nonlocal fast_mode_state
        fast_mode_state = get_fast_mode_dashboard_state(fast_mode_api)
        fast_mode_summary_var.set(format_fast_mode_summary(fast_mode_state))
        fast_mode_summary_label.configure(
            fg=W95_WARN if fast_mode_state.status in {
                "snapshot_invalid", "invalid_active", "restore_required",
                "restoration_pending", "activation_required", "snapshot_missing",
                "cleanup_pending", "restored_snapshot_retained",
            } else W95_TEXT,
        )
        for key, widget in editor_widgets.items():
            managed = fast_mode_state.active and key in fast_mode_state.managed_keys
            try:
                widget.configure(state="disabled" if managed else "normal")
            except Exception:
                pass
            hint_pair = managed_hints.get(key)
            if hint_pair is None:
                continue
            hint_var, hint = hint_pair
            if managed:
                hint_var.set(
                    format_fast_mode_managed_status(
                        key,
                        state=fast_mode_state,
                        api=fast_mode_api,
                    )
                )
                if not hint.winfo_manager():
                    hint.pack(fill="x", padx=(12, 0), pady=(0, 2))
            else:
                hint_var.set("")
                if hint.winfo_manager():
                    hint.pack_forget()

    def _sync_editor_values(values: dict[str, Any]) -> None:
        ui_syncing["active"] = True
        try:
            for key, (kind, var) in editors.items():
                fallback = (
                    ("yes" if presentation.compact_mode_on else "no")
                    if key == "COMPACT_MODE" else ""
                )
                value = str(values.get(key, fallback))
                if kind == "bool":
                    var.set(value.strip().lower() in ("yes", "true", "1", "on"))
                else:
                    var.set(value.strip())
        finally:
            ui_syncing["active"] = False

    _refresh_fast_mode_controls()

    footer = tk.Frame(settings_frame, bg=W95_BG)
    footer.pack(fill="x", padx=8, pady=(0, 8))
    tk.Label(
        footer, textvariable=status_var, bg=W95_BG, fg=W95_TEXT, font=W95_FONT, anchor="w",
    ).pack(side="left", fill="x", expand=True)

    def _collect_values() -> dict[str, str]:
        out: dict[str, str] = {}
        for key, (kind, var) in editors.items():
            if kind == "bool":
                out[key] = "yes" if bool(var.get()) else "no"
            else:
                out[key] = str(var.get()).strip()
        return out

    def _restore_compact_mode_editor() -> None:
        pair = editors.get("COMPACT_MODE")
        if pair is None:
            return
        ui_syncing["active"] = True
        try:
            pair[1].set(presentation.compact_mode_on)
        finally:
            ui_syncing["active"] = False

    def _request_compact_mode(compact_on: bool) -> bool:
        # The current Dashboard never presents an unconfirmed profile as active.
        _restore_compact_mode_editor()
        if on_compact_mode_request is None:
            status_var.set("Compact Mode unchanged — transition handler unavailable.")
            messagebox.showerror(
                "Agetha — Compact Mode",
                "The capability-profile transition is unavailable. Compact Mode was not changed.",
                parent=win,
            )
            return False
        try:
            on_compact_mode_request(bool(compact_on))
        except Exception as exc:
            logger.warning(
                "dashboard: Compact Mode transition request failed: %s",
                type(exc).__name__,
            )
            status_var.set("Compact Mode unchanged — transition request failed.")
            messagebox.showerror(
                "Agetha — Compact Mode",
                "The capability-profile transition could not start. Compact Mode was not changed.",
                parent=win,
            )
            return False
        return True

    def _apply_settings() -> None:
        try:
            from agetha.app_config import get_settings as _reload_settings
            from agetha.utils import refresh_config_constants
        except Exception as exc:
            logger.warning(f"dashboard: apply imports failed: {exc}")
            status_var.set(f"Apply failed: {exc}")
            return

        new_vals = _collect_values()
        updates: dict[str, str] = {}
        restart_changed: list[str] = []
        launcher_changed: list[str] = []
        for key, val in new_vals.items():
            old = str(raw_cfg.get(key, "")).strip()
            if editors[key][0] == "bool":
                old_norm = "yes" if old.lower() in ("yes", "true", "1", "on") else "no"
                changed = old_norm != val
            else:
                changed = old != val
            if not changed:
                continue
            updates[key] = val
            if key in _RESTART_KEYS:
                restart_changed.append(key)
            elif key in _LAUNCHER_KEYS:
                launcher_changed.append(key)

        split = split_dashboard_profile_update(
            updates,
            current_compact_mode=presentation.compact_mode_on,
        )
        updates = split.generic_updates
        profile_request = split.requested_compact_mode
        if profile_request is not None:
            _restore_compact_mode_editor()

        if not updates and profile_request is None:
            status_var.set("No changes to apply.")
            return
        if not updates:
            if _request_compact_mode(profile_request):
                status_var.set(
                    "Compact Mode transition requested; the current view remains unchanged."
                )
            return

        # Do not decide consent from the values captured when the dashboard was
        # opened. config.txt or the recovery snapshot may have changed since
        # then, so re-read both immediately before the coordinated transaction.
        try:
            confirmation, current_fast_mode_state = get_fast_mode_apply_confirmation(
                updates, fast_mode_api,
            )
        except Exception as exc:
            logger.warning(f"dashboard: Fast Mode pre-apply inspection failed: {exc}")
            status_var.set("Apply paused — Fast Mode state could not be verified.")
            messagebox.showerror(
                "Agetha — Settings",
                "Could not verify the current Fast Mode state. No settings were changed.",
                parent=win,
            )
            return

        if confirmation == "activate":
            managed_names = "\n".join(
                f"  • {key}" for key in current_fast_mode_state.managed_keys
            )
            if not messagebox.askyesno(
                "Agetha — Enable Fast Mode",
                "Fast Mode will temporarily manage these settings:\n\n"
                f"{managed_names}\n\n"
                "Their current values will be saved locally and restored when Fast Mode is disabled. "
                "Provider, permission, privacy, and security settings are never changed.\n\n"
                "Enable Fast Mode?",
                parent=win,
            ):
                status_var.set("Fast Mode activation cancelled — no settings changed.")
                return
        elif confirmation == "restore":
            if not messagebox.askyesno(
                "Agetha — Disable Fast Mode",
                "Disabling Fast Mode will restore the values saved before activation. "
                "Intentional manual edits made while it was active are preserved.\n\n"
                "Disable Fast Mode and restore saved settings?",
                parent=win,
            ):
                status_var.set("Fast Mode restoration cancelled — no settings changed.")
                return

        try:
            result = apply_dashboard_config_updates(updates, fast_mode_api)
        except Exception as exc:
            logger.warning(f"dashboard: coordinated settings transaction failed: {exc}")
            status_var.set("Apply failed — configuration was not changed.")
            messagebox.showerror(
                "Agetha — Settings",
                f"Could not apply settings safely.\n\n{exc}",
                parent=win,
            )
            return

        if not _fast_mode_result_ok(result):
            status = _fast_mode_result_status(result)
            error = (
                result.get("error") if isinstance(result, dict)
                else getattr(result, "error", None)
            )
            logger.warning(f"dashboard: coordinated settings transaction returned {status}")
            status_var.set(f"Apply failed — Fast Mode status: {status}.")
            messagebox.showerror(
                "Agetha — Settings",
                format_fast_mode_failure(status)
                + (f"\n{error}" if error else ""),
                parent=win,
            )
            return

        try:
            refresh_config_constants()
        except Exception as exc:
            logger.warning(f"dashboard: refresh_config_constants failed: {exc}")

        try:
            latest = dict(_reload_settings(reload=True).raw)
            raw_cfg.clear()
            raw_cfg.update(latest)
            _sync_editor_values(raw_cfg)
        except Exception as exc:
            logger.warning(f"dashboard: settings reload after apply failed: {exc}")
        _refresh_fast_mode_controls()

        profile_requested = (
            _request_compact_mode(profile_request)
            if profile_request is not None else None
        )

        changed_keys = tuple(getattr(result, "changed_keys", ()) or ())
        applied_status = (
            f"Applied {max(len(updates), len(changed_keys))} setting(s) "
            f"({_fast_mode_result_status(result)})."
        )
        if profile_requested is True:
            applied_status += " Capability-profile transition requested."
        elif profile_requested is False:
            applied_status += " Compact Mode remained unchanged."
        status_var.set(applied_status)

        if restart_changed:
            names = "\n".join(f"  • {k}" for k in restart_changed[:20])
            more = "" if len(restart_changed) <= 20 else f"\n  …and {len(restart_changed) - 20} more"
            messagebox.showwarning(
                "Agetha — Restart Required",
                "These settings require restarting Agetha to take full effect:\n\n"
                f"{names}{more}\n\n"
                "Close Agetha and launch it again (or re-run Medic_Checker).",
                parent=win,
            )
        elif launcher_changed and not restart_changed:
            messagebox.showinfo(
                "Agetha — Settings Applied",
                "Medic_Checker settings were saved.\n"
                "They apply on the next Medic_Checker run (Agetha restart not required).",
                parent=win,
            )
        elif not restart_changed:
            messagebox.showinfo(
                "Agetha — Settings Applied",
                "Settings applied. Hot-reload keys are active now.",
                parent=win,
            )

    tk.Button(
        footer, text="Apply settings", font=W95_FONT_BOLD, bg=W95_BTN_BG,
        relief="raised", bd=2, command=_apply_settings,
    ).pack(side="right", padx=(8, 0))

    # Position near parent, clamped to screen bounds
    win.update_idletasks()
    try:
        px, py = parent.winfo_x(), parent.winfo_y()
        pw = parent.winfo_width()
        ww = win.winfo_width() or 520
        wh = win.winfo_height() or 420
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()

        x = px + pw + 12
        if x + ww > sw:
            x = px - ww - 12
        x = max(0, min(x, sw - ww))
        y = max(0, min(py, sh - wh))
        win.geometry(f"+{x}+{y}")
    except Exception:
        pass
    show_borderless(win)

    win.protocol("WM_DELETE_WINDOW", _close_dashboard)
    return handle
