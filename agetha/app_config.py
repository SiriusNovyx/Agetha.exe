"""
app_config.py — Central config.txt loader for Agetha Mod.

Parses config.txt once, merges .env overrides, exposes typed settings.
Missing, unreadable, or invalid config.txt always falls back to DEFAULT_CONFIG.

API keys (GROQ_API_KEY*, OPENROUTER_API_KEY, UNLIMITED_OCR_API_KEY) are loaded from .env only —
values in config.txt are ignored.
"""

from __future__ import annotations

import os
import re
import stat
import sys
import tempfile
import math
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

CONFIG_PATH = BASE_DIR / "config.txt"
ENV_PATH = BASE_DIR / ".env"

_CONFIG_WRITE_LOCK = threading.RLock()


class AtomicWriteError(OSError):
    """Report whether an atomic replacement may already be visible on disk."""

    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.state = state
        self.write_applied = state == "write_applied_verification_failed"


def _fsync_parent_directory(path: Path) -> None:
    """Persist a completed rename on POSIX; Windows has no portable equivalent."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    directory_fd = os.open(path.parent, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_atomic_config(path: Path, content: str) -> None:
    """Durably replace a text file using an exclusive same-directory temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    replaced = False
    try:
        fd, temp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        try:
            os.chmod(temp_name, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            # mkstemp is 0600 on POSIX. Windows files inherit the ACL of the
            # per-user application directory; chmod is not an ACL boundary.
            pass
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        replaced = True
        _fsync_parent_directory(path)
    except Exception as exc:
        if temp_name is not None and not replaced:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
        state = (
            "write_applied_verification_failed" if replaced else "write_not_applied"
        )
        raise AtomicWriteError(state, f"atomic write failed during {state}") from exc

DEFAULT_CONFIG = """# =============================================================================
# Agetha Mod — config.txt
# =============================================================================
#
# HOW THIS FILE WORKS
#   • Format:  KEY = value   (one setting per line)
#   • Comments start with #
#   • Booleans: yes / no  (also: true, false, 1, 0, on, off)
#   • If a line is missing, corrupt, or has an invalid value → built-in default
#   • Secrets: API keys go in .env ONLY (copy .env.example → .env)
#             Never put GROQ_API_KEY* or OPENROUTER_API_KEY in this file
#
# After editing, restart Agetha (or re-run Medic_Checker.bat).
# =============================================================================


# ── AI Backend ───────────────────────────────────────────────────────────────
# Priority: local Ollama > Groq / OpenRouter (cloud).
# With both Groq + OpenRouter enabled, Agetha asks which to use at startup
# (Yes=Groq, No=OpenRouter). If Groq is chosen, OpenRouter auto-starts when
# Groq tokens/keys run out.

# USE_LOCAL_AI — yes = use Ollama on this PC; no = use cloud APIs below.
USE_LOCAL_AI = no

# ENABLE_GROQ — yes = allow Groq API (ignored when USE_LOCAL_AI = yes).
ENABLE_GROQ = yes

# ENABLE_OPENROUTER — yes = enable OpenRouter (fallback after Groq, or solo if Groq off).
# Ignored when USE_LOCAL_AI = yes. Get a key: https://openrouter.ai/keys
# Put OPENROUTER_API_KEY in .env only (never in this file).
# Tip: keep ENABLE_GROQ=yes for free tier first; use OpenRouter when Groq is exhausted.
ENABLE_OPENROUTER = no

# OPENROUTER_MODEL — model slug from openrouter.ai/models
# Use exact IDs from https://openrouter.ai/models (many ":free" variants are retired).
# Non-:free models may be billed — keep ENABLE_GROQ=yes to use free Groq first.
# Recommended for Agetha: deepseek/deepseek-v4-flash-0731
# Model availability and pricing may change; verify them on OpenRouter before use.
OPENROUTER_MODEL = google/gemma-4-31b-it:free

# FASTER_MODE — enables Agetha's reversible performance profile.
# Selected AI, context, polling, and OCR values are temporarily forced and
# restored when disabled. Provider, permission, privacy, and security settings
# are never changed. Recovery metadata stays in memory/fast_mode_snapshot.json.
FASTER_MODE = no


# ── Groq (API keys in .env only) ──────────────────────────────────────────────
# Get free keys: https://console.groq.com
# Copy .env.example → .env and set GROQ_API_KEY_1=… (up to _10 for rotation).
# Do not put GROQ_API_KEY* lines in this file — they are ignored.

# GROQ_MODEL — model name from Groq console (default: llama-3.3-70b-versatile).
GROQ_MODEL = llama-3.3-70b-versatile


# ── Local AI (Ollama) ─────────────────────────────────────────────────────────
# Requires Ollama running: https://ollama.com — run "ollama list" for model names.

# LOCAL_AI_MODEL — e.g. llama3, mistral, qwen2.5 (must be installed in Ollama).
LOCAL_AI_MODEL =

# LOCAL_AI_TIMEOUT — seconds to wait for Ollama reply (5–120).
LOCAL_AI_TIMEOUT = 30


# ── AI tuning ─────────────────────────────────────────────────────────────────
# Controls how Agetha's brain writes replies (Groq and Ollama).

# AI_TEMPERATURE — randomness (0.0 = stiff, 2.0 = chaotic). Default 0.85.
AI_TEMPERATURE = 0.85

# AI_MAX_TOKENS — max length of each AI reply (64–8192). Default 400.
AI_MAX_TOKENS = 400

# AI_TOP_P — nucleus sampling 0.0–1.0. Lower = more focused. Default 0.95.
AI_TOP_P = 0.95

# ENABLE_STREAMING — yes = typewriter subtitle while Groq streams; no = wait for full reply.
ENABLE_STREAMING = yes

# ENABLE_AMBIENT_POLLS — yes = periodic background screen checks when idle; no = only when you chat.
ENABLE_AMBIENT_POLLS = yes

# ENABLE_DATETIME_CONTEXT - include compact local weekday/date/time in every AI prompt.
ENABLE_DATETIME_CONTEXT = yes

# DATETIME_INCLUDE_SECONDS - include seconds (off by default to save prompt tokens).
DATETIME_INCLUDE_SECONDS = no

# DATETIME_INCLUDE_TIMEZONE - include local zone name and UTC offset.
DATETIME_INCLUDE_TIMEZONE = yes


# ── OS Permissions ────────────────────────────────────────────────────────────
# Master switches for commands that touch files, apps, or other windows.

# ENABLE_COMMAND_EXECUTION — no = block ALL OS commands (speak/idle still work).
ENABLE_COMMAND_EXECUTION = yes

# ENABLE_WINDOW_CONTROL — no = block target_window_move / resize / close / force_close.
ENABLE_WINDOW_CONTROL = yes

# ENABLE_COMMAND_CONFIRMATIONS — no = skip native Yes/No dialogs for risky actions.
ENABLE_COMMAND_CONFIRMATIONS = yes

# FORCE_CLOSE_AUTO_ALLOW — yes = kill user apps without confirm; system apps still protected.
FORCE_CLOSE_AUTO_ALLOW = yes

# PROTECTED_PROCESSES — extra comma-separated names Agetha must not kill
# (explorer.exe, python.exe, etc. are always protected). Example: myapp.exe,obs64.exe
PROTECTED_PROCESSES =


# ── Context & Memory ─────────────────────────────────────────────────────────
# soul.md = permanent personality (memory/soul.md). episodic = recent events JSON.

# MEMORY_CHARS — chars from long-term memory.txt injected per prompt.
MEMORY_CHARS = 600

# HISTORY_LIMIT — recent chat turns kept in context (1–20).
HISTORY_LIMIT = 6

# FILE_READ_CHARS — max chars when AI reads a file into context.
FILE_READ_CHARS = 200

# EPISODIC_PROMPT_LIMIT — how many episodic memories injected per prompt (0–50).
EPISODIC_PROMPT_LIMIT = 10

# EPISODIC_ENTRY_MAX_CHARS — max chars per episodic log entry.
EPISODIC_ENTRY_MAX_CHARS = 300

# EPISODIC_MAX_ENTRIES — max entries stored in memory/episodic_memory.json.
EPISODIC_MAX_ENTRIES = 50

# ENABLE_LONGTERM_MEMORY — yes = dual-write summary_memory to memory/longterm_memory.jsonl.
# Medic_Checker reports this and memory/ file status in step [6/7].
ENABLE_LONGTERM_MEMORY = yes

# LONGTERM_MEMORY_MAX_RESULTS — max BM25 hits returned by search_memory command.
LONGTERM_MEMORY_MAX_RESULTS = 5

# LONGTERM_MEMORY_MAX_CHARS — max chars of search results injected into AI prompt.
LONGTERM_MEMORY_MAX_CHARS = 2500

# ENABLE_WEB_RAG — yes = allow search_web / fetch_webpage commands (network).
ENABLE_WEB_RAG = no

# WEB_FETCH_MAX_CHARS — max chars of fetched page text injected into AI prompt.
WEB_FETCH_MAX_CHARS = 8000

# WEB_TIMEOUT_SEC — HTTP timeout for web search/fetch (seconds).
WEB_TIMEOUT_SEC = 10

# WEB_SEARCH_MAX_RESULTS — max DuckDuckGo hits for search_web command.
WEB_SEARCH_MAX_RESULTS = 5

# ENABLE_GLITCH_EFFECTS — yes = allow harmless visual glitch_overlay command.
ENABLE_GLITCH_EFFECTS = no

# GLITCH_MAX_DURATION_MS — max overlay lifetime (200–5000 ms).
GLITCH_MAX_DURATION_MS = 2000

# GLITCH_DEFAULT_STYLE — scanlines|static|rgb_split|flicker|bsod|matrix|tear
GLITCH_DEFAULT_STYLE = scanlines

# GLITCH_MOOD_AUTO — yes = rare mood-themed glitch on manic/angry/dominant (needs ENABLE_GLITCH_EFFECTS=yes).
GLITCH_MOOD_AUTO = no

# GLITCH_FULLSCREEN — yes = brief fullscreen overlay; no = small corner panel (safer default).
GLITCH_FULLSCREEN = no

# ENABLE_COMPANION_STATS_CONTEXT — yes = inject virus-registry stats into AI prompts.
ENABLE_COMPANION_STATS_CONTEXT = yes


# ── Presence & realism (v4.0.0) ──────────────────────────────────────────────
# Circadian rhythm, dream journal, and task keeper. All local — no network,
# no OS mutation; data lives only in the memory\\ folder.

# ENABLE_CIRCADIAN_RHYTHM — yes = internal clock flavors her mood by time of day.
ENABLE_CIRCADIAN_RHYTHM = yes

# RHYTHM_NIGHT_START / RHYTHM_NIGHT_END — hours (0–23) bounding her "deep night"
# drowsy window. Wraps midnight (default 23 → 6).
RHYTHM_NIGHT_START = 23
RHYTHM_NIGHT_END = 6

# ENABLE_DREAMS — yes = she dreams during deep sleep (memory fragments woven into
# surreal entries in memory\\dreams.jsonl) and may mention the dream on waking.
ENABLE_DREAMS = yes

# DREAMS_MAX_ENTRIES — max dream records kept (5–500).
DREAMS_MAX_ENTRIES = 40

# ENABLE_TASKS — yes = add_task / complete_task / list_tasks commands
# (memory\\tasks.json); she nags about pending tasks during ambient polls.
ENABLE_TASKS = yes

# TASKS_MAX_ENTRIES — max stored tasks (10–1000). Oldest completed pruned first.
TASKS_MAX_ENTRIES = 100


# ── Emotion engine (v5.0.0) ──────────────────────────────────────────────────
# Persistent bounded emotional state (memory\\emotional_state.json) and
# relationship history (memory\\emotional_history.jsonl). Tone flavor only —
# never permissions, never safety. Viewable/removable/resettable by you.

# ENABLE_EMOTION_ENGINE — yes = persistent valence/arousal/trust/loneliness state.
ENABLE_EMOTION_ENGINE = yes

# EMOTION_BASELINE_* — resting values the state decays toward.
# Valence -100..100; arousal/trust/loneliness 0..100.
EMOTION_BASELINE_VALENCE = 0
EMOTION_BASELINE_AROUSAL = 30
EMOTION_BASELINE_TRUST = 50
EMOTION_BASELINE_LONELINESS = 25

# EMOTION_DECAY_PER_HOUR — fraction of distance-to-baseline recovered per hour (0.0–1.0).
EMOTION_DECAY_PER_HOUR = 0.10

# EMOTION_HISTORY_MAX — max emotional-history records (20–1000); old low-importance
# events are compacted into summaries.
EMOTION_HISTORY_MAX = 200


# ── Windows integration (v5.0.0) ─────────────────────────────────────────────
# Transparent, current-user-only, reversible. Every change is confirmed via a
# native dialog and written to memory\\audit_log.jsonl.

# ENABLE_AUTOSTART_CONTROL — yes = allow the set_autostart command
# ("Start Agetha when I sign in"): a plainly named, visible shortcut in your
# Startup folder. No service, no scheduled task, no registry Run key.
ENABLE_AUTOSTART_CONTROL = no

# ENABLE_THEME_CONTROL — yes = allow set_theme (Windows light/dark, current user
# only, HKCU personalization values only, previous values backed up for rollback).
ENABLE_THEME_CONTROL = no

# ENABLE_STATUS_PROVIDERS — yes = coarse read-only status observations (battery,
# disk space, network up/down) for companion reactions. Local only, pausable.
# Never keystrokes, clipboard, screen, credentials, or document contents.
ENABLE_STATUS_PROVIDERS = no

# STATUS_POLL_INTERVAL_SEC — seconds between status-provider checks (60–3600).
STATUS_POLL_INTERVAL_SEC = 300

# ENABLE_TRAY — yes = system tray icon IF the optional pystray package is
# installed (not bundled). Menu: Open, Pause observation, Settings, Exit.
ENABLE_TRAY = no

# TRAY_BACKGROUND_CLOSE — yes = closing the main window keeps Agetha in the
# tray (only when the tray is active); no = closing the window exits completely.
TRAY_BACKGROUND_CLOSE = no


# ── Behavior & timing ─────────────────────────────────────────────────────────

# SCREEN_POLL_INTERVAL_SEC — seconds between ambient screen AI polls (15–3600).
SCREEN_POLL_INTERVAL_SEC = 120

# TOUCH_COOLDOWN_SEC — seconds after clicking her GIF before another touch counts.
TOUCH_COOLDOWN_SEC = 10

# WAKE_DELAY_SEC — seconds before wake-from-sleep animation after idle.
WAKE_DELAY_SEC = 8

# LOAF_TIMER_MIN — minutes idle before loaf/sleeping animation.
LOAF_TIMER_MIN = 15


# ── Mood & attention snap ─────────────────────────────────────────────────────
# When idle in certain moods, Agetha may move herself (snap_to_center or side drift).

# ENABLE_ATTENTION_SNAP — yes = mood-based auto reposition; no = never auto-move.
ENABLE_ATTENTION_SNAP = yes

# MOOD_SNAP_*_SEC — seconds of user inactivity before snap triggers per mood.
# Lower = more aggressive (manic snaps fastest). Range per key: 30–3600.
MOOD_SNAP_MANIC_SEC = 120
MOOD_SNAP_ANGRY_SEC = 180
MOOD_SNAP_PARANOID_SEC = 240
MOOD_SNAP_DOMINANT_SEC = 300
MOOD_SNAP_SURPRISED_SEC = 360
MOOD_SNAP_EXCITED_SEC = 420
MOOD_SNAP_HAPPY_SEC = 600
MOOD_SNAP_NEUTRAL_SEC = 600
MOOD_SNAP_THINKING_SEC = 600
MOOD_SNAP_VULNERABLE_SEC = 720
MOOD_SNAP_MELANCHOLIC_SEC = 900
MOOD_SNAP_SAD_SEC = 900
MOOD_SNAP_WHISPER_SEC = 900


# ── Screen reader / OCR ─────────────────────────────────────────────────────
# Needs Tesseract installed (optional). Medic_Checker step [4/7] verifies it.

# ENABLE_SCREEN_READER — no = disable OCR entirely (app still runs).
ENABLE_SCREEN_READER = yes

# OCR_MAX_DIMENSION — max screenshot edge in pixels before OCR (640–8192). Lower = faster.
OCR_MAX_DIMENSION = 2560

# OCR_FOCUSED_WINDOW_ONLY — yes = OCR only the active window; no = full screen.
OCR_FOCUSED_WINDOW_ONLY = yes

# Skip Tesseract when the captured target has not materially changed.
OCR_CHANGE_DETECTION = yes
OCR_CHANGE_THRESHOLD = 0.025
OCR_FORCE_REFRESH_SECONDS = 20
OCR_STATE_EXPIRY_SECONDS = 300

# Screen-event deduplication and confirmation controls.
OCR_PATTERN_COOLDOWN_SECONDS = 60
OCR_PATTERN_CONFIRM_SCANS = 1
OCR_LOW_CONFIDENCE_CONFIRM_SCANS = 2
OCR_PATTERN_CLEAR_SCANS = 2

# OCR quality, layout, language, privacy, and opt-out controls.
OCR_MIN_WORD_CONFIDENCE = 30
OCR_MIN_PATTERN_CONFIDENCE = 45
OCR_PREPROCESSING = auto
OCR_LANGUAGES = eng
OCR_PSM = auto
OCR_EXCLUDED_APPS =
OCR_EXCLUDED_TITLE_PATTERNS =
OCR_REDACT_SENSITIVE_TEXT = yes

# INCLUDE_WINDOW_TITLE_IN_CONTEXT — yes = send [Active: Window Title] to AI each poll.
INCLUDE_WINDOW_TITLE_IN_CONTEXT = yes

# TESSERACT_PATH — full path to tesseract.exe if not on PATH (Windows).
# Example: C:\\Program Files\\Tesseract-OCR\\tesseract.exe
TESSERACT_PATH =

# Deep OCR is opt-in and is never used by automatic screen polling.
DEEP_OCR_BACKEND = none

# Unlimited-OCR runs as a separate OpenAI-compatible service. Loopback is
# allowed by default; remote hosts require explicit opt-in.
UNLIMITED_OCR_SERVER_URL = http://127.0.0.1:10000
UNLIMITED_OCR_MODEL = Unlimited-OCR
UNLIMITED_OCR_TIMEOUT_SECONDS = 180
UNLIMITED_OCR_ALLOW_REMOTE = no
DEEP_OCR_MAX_OUTPUT_CHARS = 12000


# ── UI ────────────────────────────────────────────────────────────────────────

# WINDOW_TOPMOST — yes = Agetha stays above other windows.
WINDOW_TOPMOST = yes

# UI_SCALE - auto scales from display resolution; or use a manual value (0.75-2.50).
UI_SCALE = auto

# WINDOW_START_X / Y — pixel position when Agetha first opens.
WINDOW_START_X = 80
WINDOW_START_Y = 80

# SUBTITLE_CHAR_DELAY — seconds between subtitle typewriter chars (0.005–0.5).
SUBTITLE_CHAR_DELAY = 0.035

# ANIMATION_SPEED — GIF playback multiplier (0.1–3.0). Lower = slower loops.
ANIMATION_SPEED = 0.6

# WINDOW_MOVE_SMOOTH — yes = eased slide for move_window / target_window_* / snap.
WINDOW_MOVE_SMOOTH = yes

# WINDOW_MOVE_DURATION_MS — animation length in ms (0 = instant, max 2000).
WINDOW_MOVE_DURATION_MS = 280

# ENABLE_CRT_CLOSE_ANIMATION - brief CRT-style collapse before graceful exit.
ENABLE_CRT_CLOSE_ANIMATION = yes

# REDUCED_MOTION - disable decorative pulsing and window motion, including CRT close.
REDUCED_MOTION = no

# ENABLE_MOOD_GLOW - show a subtle mood-coloured border around the GIF.
ENABLE_MOOD_GLOW = no

# MOOD_GLOW_ANIMATED - slowly pulse the mood border; no = solid colour.
MOOD_GLOW_ANIMATED = yes

# MOOD_GLOW_INTERVAL_MS - decorative border update interval (100-1000 ms).
MOOD_GLOW_INTERVAL_MS = 150

# ENABLE_MOOD_MOTION - allow one guarded, brief motion per completed AI response.
ENABLE_MOOD_MOTION = yes

# MOOD_MOTION_COOLDOWN_SECONDS - minimum delay between mood motions (1-60 seconds).
MOOD_MOTION_COOLDOWN_SECONDS = 4


# ── Medic_Checker (launcher) ────────────────────────────────────────────────
# Settings for Medic_Checker.ps1 / Medic_Checker.bat only.

# SKIP_TESSERACT_CHECK — yes = skip Tesseract step in health check.
SKIP_TESSERACT_CHECK = no

# SKIP_ASSET_CHECK — yes = skip verifying assets\\ GIFs and fonts.
SKIP_ASSET_CHECK = no

# AUTO_PIP_INSTALL — yes = auto pip install -r requirements.txt if packages missing.
AUTO_PIP_INSTALL = yes

# CREATE_DESKTOP_SHORTCUT — yes = create Desktop\\Agetha.lnk on each Medic_Checker run.
CREATE_DESKTOP_SHORTCUT = no

# CHECK_FOR_UPDATES — yes = compare APP_VERSION to GITHUB_RELEASES_URL (needs URL below).
CHECK_FOR_UPDATES = yes


# ── App meta ──────────────────────────────────────────────────────────────────

# APP_VERSION — shown in window title and Medic_Checker banner.
APP_VERSION = 5.7

# GITHUB_RELEASES_URL — GitHub API URL for latest release (leave empty to skip).
# Example: https://api.github.com/repos/YOUR_USER/YOUR_REPO/releases/latest
GITHUB_RELEASES_URL =


# ── Window control ────────────────────────────────────────────────────────────
# For target_window_move / resize / close — match windows by partial title.

# TARGET_APP_ALIASES — short name = partial title fragment (comma-separated).
# AI can say "notepad" and Agetha searches for "Notepad" in window titles.
TARGET_APP_ALIASES = notepad=Notepad,chrome=Google Chrome,firefox=Mozilla Firefox,code=Visual Studio Code

# WINDOW_PICKER_ON_AMBIGUOUS — yes = dialog if multiple windows match same name.
WINDOW_PICKER_ON_AMBIGUOUS = yes


# ── Command safety ────────────────────────────────────────────────────────────

# DRY_RUN_MODE — yes = every command shows confirm dialog before running (test mode).
DRY_RUN_MODE = no


# ── Voice input (microphone) ──────────────────────────────────────────────────
# Requires: SpeechRecognition, pyaudio (Medic_Checker installs when ENABLE_VOICE=yes).

# ENABLE_VOICE — yes = show microphone button in UI.
ENABLE_VOICE = no

# USE_LOCAL_STT — yes = faster-whisper (offline); no = Google Speech Recognition (online).
# Local STT needs: pip install faster-whisper numpy (~75 MB model on first run).
USE_LOCAL_STT = no


# ── File drag-and-drop ────────────────────────────────────────────────────────
# Drop files onto Agetha's GIF. Requires tkinterdnd2 on Windows.

# ENABLE_FILE_DRAG_DROP — yes = enable drag-and-drop onto the character.
ENABLE_FILE_DRAG_DROP = yes


# ── OCR extras ────────────────────────────────────────────────────────────────

# OCR_CUSTOM_PATTERNS — extra screen text triggers for AI context.
# Format per pattern:  label:mood:regex   mood optional (default: thinking)
# Separate multiple with ;  Example: Banned:angry:you have been banned;Deploy:happy:build succeeded
OCR_CUSTOM_PATTERNS =

# OCR_PAUSE_WHILE_TYPING_SEC — skip OCR for N seconds after you type or touch her (saves CPU).
OCR_PAUSE_WHILE_TYPING_SEC = 8


# ── Voice output (retro bleeps + optional TTS) ───────────────────────────────
# VOICE_OUTPUT_MODE — bleeps_only (default) | tts_only | both
# VOICE_TTS_ENGINE — pyttsx3 (default) | edge_tts | kokoro
#   pyttsx3  — OS voices (pip install pyttsx3); TTS_VOICE_NAME e.g. Zira
#   edge_tts — free cloud neural (pip install edge-tts); needs internet;
#              TTS_VOICE_NAME e.g. en-US-AvaNeural
#   kokoro   — local neural (pip install kokoro soundfile); offline; heavier;
#              TTS_VOICE_NAME e.g. af_heart
# Medic_Checker installs the matching package when VOICE_OUTPUT_MODE is
#   tts_only or both and AUTO_PIP_INSTALL = yes.

VOICE_OUTPUT_MODE = bleeps_only
VOICE_TTS_ENGINE = pyttsx3
TTS_RATE = 165
TTS_VOLUME = 0.8
TTS_VOICE_NAME =
"""

# API secrets — never accepted from config.txt; loaded from .env only.
_SECRET_KEYS = frozenset(
    {
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
        "UNLIMITED_OCR_API_KEY",
        *(f"GROQ_API_KEY_{i}" for i in range(1, 11)),
    }
)

# Canonical reversible Fast Mode profile. This configuration-layer allowlist is
# imported and re-exported by core.fast_mode_profile; do not duplicate it there.
FAST_MODE_OVERRIDES: dict[str, str] = {
    "AI_MAX_TOKENS": "220",
    "HISTORY_LIMIT": "3",
    "MEMORY_CHARS": "300",
    "EPISODIC_PROMPT_LIMIT": "3",
    "AI_TEMPERATURE": "0.65",
    "AI_TOP_P": "0.90",
    "ENABLE_STREAMING": "yes",
    "ENABLE_COMPANION_STATS_CONTEXT": "no",
    "DATETIME_INCLUDE_SECONDS": "no",
    "SCREEN_POLL_INTERVAL_SEC": "180",
    "OCR_MAX_DIMENSION": "1920",
    "OCR_PREPROCESSING": "basic",
    "OCR_FORCE_REFRESH_SECONDS": "30",
}

_BOOL_KEYS = frozenset({
    "USE_LOCAL_AI", "ENABLE_GROQ", "ENABLE_OPENROUTER", "FASTER_MODE",
    "ENABLE_VOICE", "USE_LOCAL_STT", "ENABLE_FILE_DRAG_DROP",
    "ENABLE_STREAMING", "ENABLE_AMBIENT_POLLS",
    "ENABLE_DATETIME_CONTEXT", "DATETIME_INCLUDE_SECONDS", "DATETIME_INCLUDE_TIMEZONE",
    "ENABLE_COMMAND_EXECUTION", "ENABLE_WINDOW_CONTROL", "ENABLE_COMMAND_CONFIRMATIONS",
    "FORCE_CLOSE_AUTO_ALLOW", "ENABLE_ATTENTION_SNAP", "ENABLE_SCREEN_READER",
    "OCR_FOCUSED_WINDOW_ONLY", "OCR_CHANGE_DETECTION",
    "OCR_REDACT_SENSITIVE_TEXT", "INCLUDE_WINDOW_TITLE_IN_CONTEXT", "WINDOW_TOPMOST",
    "UNLIMITED_OCR_ALLOW_REMOTE",
    "SKIP_TESSERACT_CHECK", "SKIP_ASSET_CHECK", "AUTO_PIP_INSTALL",
    "CREATE_DESKTOP_SHORTCUT", "CHECK_FOR_UPDATES", "WINDOW_PICKER_ON_AMBIGUOUS",
    "DRY_RUN_MODE", "WINDOW_MOVE_SMOOTH", "ENABLE_LONGTERM_MEMORY",
    "ENABLE_WEB_RAG",
    "ENABLE_GLITCH_EFFECTS", "GLITCH_MOOD_AUTO", "GLITCH_FULLSCREEN",
    "ENABLE_COMPANION_STATS_CONTEXT",
    "ENABLE_CIRCADIAN_RHYTHM", "ENABLE_DREAMS", "ENABLE_TASKS",
    "ENABLE_EMOTION_ENGINE", "ENABLE_AUTOSTART_CONTROL", "ENABLE_THEME_CONTROL",
    "ENABLE_STATUS_PROVIDERS", "ENABLE_TRAY", "TRAY_BACKGROUND_CLOSE",
    "ENABLE_CRT_CLOSE_ANIMATION", "REDUCED_MOTION", "ENABLE_MOOD_GLOW",
    "MOOD_GLOW_ANIMATED", "ENABLE_MOOD_MOTION",
})

_INT_KEYS = frozenset({
    "LOCAL_AI_TIMEOUT", "AI_MAX_TOKENS", "MEMORY_CHARS", "HISTORY_LIMIT",
    "FILE_READ_CHARS", "EPISODIC_PROMPT_LIMIT", "EPISODIC_ENTRY_MAX_CHARS",
    "EPISODIC_MAX_ENTRIES", "SCREEN_POLL_INTERVAL_SEC", "WAKE_DELAY_SEC",
    "LOAF_TIMER_MIN", "OCR_MAX_DIMENSION", "WINDOW_START_X", "WINDOW_START_Y",
    "MOOD_SNAP_MANIC_SEC", "MOOD_SNAP_ANGRY_SEC", "MOOD_SNAP_PARANOID_SEC",
    "MOOD_SNAP_DOMINANT_SEC", "MOOD_SNAP_SURPRISED_SEC", "MOOD_SNAP_EXCITED_SEC",
    "MOOD_SNAP_HAPPY_SEC", "MOOD_SNAP_NEUTRAL_SEC", "MOOD_SNAP_THINKING_SEC",
    "MOOD_SNAP_VULNERABLE_SEC", "MOOD_SNAP_MELANCHOLIC_SEC", "MOOD_SNAP_SAD_SEC",
    "MOOD_SNAP_WHISPER_SEC",
    "WINDOW_MOVE_DURATION_MS",
    "LONGTERM_MEMORY_MAX_RESULTS", "LONGTERM_MEMORY_MAX_CHARS",
    "WEB_FETCH_MAX_CHARS", "WEB_TIMEOUT_SEC", "WEB_SEARCH_MAX_RESULTS",
    "GLITCH_MAX_DURATION_MS",
    "TTS_RATE",
    "RHYTHM_NIGHT_START", "RHYTHM_NIGHT_END",
    "DREAMS_MAX_ENTRIES", "TASKS_MAX_ENTRIES",
    "EMOTION_BASELINE_VALENCE", "EMOTION_BASELINE_AROUSAL",
    "EMOTION_BASELINE_TRUST", "EMOTION_BASELINE_LONELINESS",
    "EMOTION_HISTORY_MAX", "STATUS_POLL_INTERVAL_SEC",
    "MOOD_GLOW_INTERVAL_MS", "MOOD_MOTION_COOLDOWN_SECONDS",
    "UNLIMITED_OCR_TIMEOUT_SECONDS", "DEEP_OCR_MAX_OUTPUT_CHARS",
    "OCR_PATTERN_CONFIRM_SCANS", "OCR_LOW_CONFIDENCE_CONFIRM_SCANS",
    "OCR_PATTERN_CLEAR_SCANS",
})

_FLOAT_KEYS = frozenset({
    "AI_TEMPERATURE", "AI_TOP_P", "TOUCH_COOLDOWN_SEC", "SUBTITLE_CHAR_DELAY",
    "ANIMATION_SPEED", "OCR_PAUSE_WHILE_TYPING_SEC",
    "OCR_CHANGE_THRESHOLD", "OCR_FORCE_REFRESH_SECONDS",
    "OCR_STATE_EXPIRY_SECONDS", "OCR_PATTERN_COOLDOWN_SECONDS",
    "OCR_MIN_WORD_CONFIDENCE", "OCR_MIN_PATTERN_CONFIDENCE",
    "TTS_VOLUME",
    "EMOTION_DECAY_PER_HOUR",
})

_VOICE_OUTPUT_MODES = frozenset({"bleeps_only", "tts_only", "both"})
_VOICE_TTS_ENGINES = frozenset({"pyttsx3", "edge_tts", "kokoro"})
_GLITCH_STYLES = frozenset({
    "scanlines", "static", "rgb_split", "flicker", "bsod", "matrix", "tear",
})

_VALID_BOOLS = frozenset({"1", "yes", "true", "on", "0", "no", "false", "off"})

# Canonical ranges used when a persisted value must round-trip exactly rather
# than merely be accepted and clamped by a runtime property. Fast Mode uses
# these limits before saving restoration metadata.
_CONFIG_VALUE_RANGES: dict[str, tuple[float, float]] = {
    "AI_MAX_TOKENS": (64, 8192),
    "HISTORY_LIMIT": (1, 20),
    "MEMORY_CHARS": (100, 5000),
    "EPISODIC_PROMPT_LIMIT": (0, 50),
    "AI_TEMPERATURE": (0.0, 2.0),
    "AI_TOP_P": (0.0, 1.0),
    "SCREEN_POLL_INTERVAL_SEC": (15, 3600),
    "OCR_MAX_DIMENSION": (640, 8192),
    "OCR_FORCE_REFRESH_SECONDS": (1.0, 3600.0),
}


@dataclass
class ConfigLoadResult:
    """Summary of the last config load (for diagnostics / first-run hints)."""
    path: Path
    used_defaults: bool = False
    file_missing: bool = False
    file_read_error: str = ""
    invalid_lines: int = 0
    invalid_keys: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_last_load: ConfigLoadResult | None = None


def _log_config(msg: str, level: str = "warning") -> None:
    # Avoid importing utils here (utils imports get_settings at import time).
    print(f"[config] {msg}")


def _parse_config_text(text: str) -> tuple[dict[str, str], int]:
    """Parse KEY = VALUE lines from text. Returns (config dict, invalid_line_count)."""
    config: dict[str, str] = {}
    invalid = 0
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            invalid += 1
            continue
        k, v = s.split("=", 1)
        key = k.strip().upper()
        if not key or not key.replace("_", "").isalnum():
            invalid += 1
            continue
        config[key] = v.strip()
    return config, invalid


def default_config_dict() -> dict[str, str]:
    """Built-in defaults parsed from DEFAULT_CONFIG template."""
    cfg, _ = _parse_config_text(DEFAULT_CONFIG)
    return cfg


def _is_valid_bool(value: str) -> bool:
    return str(value).strip().lower() in _VALID_BOOLS


def _is_valid_number(value: str, *, as_float: bool = False) -> bool:
    try:
        if as_float:
            if not math.isfinite(float(str(value).strip())):
                return False
        else:
            int(str(value).strip())
        return True
    except (TypeError, ValueError, AttributeError):
        return False


def validate_config_value(
    key: str,
    value: str,
    *,
    enforce_range: bool = False,
) -> bool:
    """Validate one raw setting without loading defaults or environment data."""
    normalized = str(key).strip().upper()
    raw = str(value)
    if "\r" in raw or "\n" in raw:
        return False
    if normalized in _BOOL_KEYS:
        return _is_valid_bool(raw)
    if normalized in _INT_KEYS:
        if not _is_valid_number(raw, as_float=False):
            return False
        if enforce_range:
            limits = _CONFIG_VALUE_RANGES.get(normalized)
            return limits is not None and limits[0] <= int(raw.strip()) <= limits[1]
        return True
    if normalized in _FLOAT_KEYS:
        if not _is_valid_number(raw, as_float=True):
            return False
        if enforce_range:
            limits = _CONFIG_VALUE_RANGES.get(normalized)
            return limits is not None and limits[0] <= float(raw.strip()) <= limits[1]
        return True
    if normalized == "OCR_PREPROCESSING":
        return raw.strip().lower() in {"basic", "auto"}
    # Strict persisted profiles may contain only settings with an explicit
    # typed or enum validator. General config parsing remains permissive.
    return not enforce_range


def _merge_with_defaults(file_config: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Overlay file values onto defaults; drop invalid typed values."""
    defaults = default_config_dict()
    merged = dict(defaults)
    invalid_keys: list[str] = []
    for key, val in file_config.items():
        if key in _BOOL_KEYS and not _is_valid_bool(val):
            invalid_keys.append(key)
            continue
        if key in _INT_KEYS and not _is_valid_number(val, as_float=False):
            invalid_keys.append(key)
            continue
        if key in _FLOAT_KEYS and not _is_valid_number(val, as_float=True):
            invalid_keys.append(key)
            continue
        merged[key] = val
    return merged, invalid_keys


def _is_secret_key(key: str) -> bool:
    return key.strip().upper() in _SECRET_KEYS


def _strip_secrets_from_config(
    file_config: dict[str, str],
) -> list[str]:
    """Remove API-key entries from config.txt values. Returns non-empty keys ignored."""
    ignored: list[str] = []
    for key in list(file_config.keys()):
        if not _is_secret_key(key):
            continue
        if file_config[key].strip():
            ignored.append(key)
        del file_config[key]
    return ignored


def _load_env_overrides(config: dict[str, str]) -> None:
    """Apply non-empty .env values. API keys are sourced from .env only."""
    # Ensure secret slots exist (empty) so callers can .get() them safely.
    for key in (
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
        "UNLIMITED_OCR_API_KEY",
        *(f"GROQ_API_KEY_{i}" for i in range(2, 11)),
    ):
        config.setdefault(key, "")

    if not ENV_PATH.exists():
        return
    try:
        for line in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k, v = k.strip().upper(), v.strip()
            if k == "GROQ_API_KEY_1":
                k = "GROQ_API_KEY"
            if k == "FASTER_MODE" or k in FAST_MODE_OVERRIDES:
                # Fast Mode is a disk-backed transaction. Environment copies
                # of managed values would create a split-brain runtime state.
                continue
            if v:
                config[k] = v
    except Exception as exc:
        _log_config(f"Could not read .env: {exc}")


def get_last_config_load() -> ConfigLoadResult | None:
    return _last_load


def parse_config_file(path: Path | None = None) -> dict[str, str]:
    """Load config.txt merged over defaults; .env supplies API keys and overrides."""
    global _last_load
    path = path or CONFIG_PATH
    result = ConfigLoadResult(path=path)
    defaults = default_config_dict()

    file_config: dict[str, str] = {}
    if not path.exists():
        result.file_missing = True
        result.used_defaults = True
        result.warnings.append("config.txt not found — using built-in defaults.")
    else:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
            file_config, result.invalid_lines = _parse_config_text(raw)
            if not file_config and raw.strip():
                result.used_defaults = True
                result.warnings.append("config.txt had no valid KEY = VALUE lines — using defaults.")
        except OSError as exc:
            result.file_read_error = str(exc)
            result.used_defaults = True
            result.warnings.append(f"Could not read config.txt ({exc}) — using defaults.")

    ignored_secrets = _strip_secrets_from_config(file_config)
    if ignored_secrets:
        # Count only — do not log key names (CodeQL: clear-text logging of secrets).
        n_ignored = len(ignored_secrets)
        result.warnings.append(
            f"Ignored {n_ignored} API key entr{'y' if n_ignored == 1 else 'ies'} "
            "in config.txt (use .env only)."
        )

    merged, invalid_keys = _merge_with_defaults(file_config)
    result.invalid_keys = invalid_keys
    if invalid_keys:
        result.warnings.append(
            f"Ignored invalid values for: {', '.join(invalid_keys[:12])}"
            + ("…" if len(invalid_keys) > 12 else "")
        )

    # Never inherit secret values from defaults/template leftovers.
    for key in list(merged.keys()):
        if _is_secret_key(key):
            merged[key] = ""

    # The on-disk Fast Mode switch is authoritative for startup reconciliation.
    # Keep this value before .env is merged: .env remains untouched, but a stale
    # non-secret override there must not defeat an already validated profile.
    fast_mode_requested = _parse_bool(merged.get("FASTER_MODE"), False)
    _load_env_overrides(merged)
    # FASTER_MODE is a disk-backed transaction switch, not an environment
    # override. Keeping config.txt authoritative prevents split-brain states
    # where snapshot reconciliation and runtime prompt behavior disagree.
    merged["FASTER_MODE"] = "yes" if fast_mode_requested else "no"
    try:
        # Lazy import avoids app_config <-> fast_mode_profile import cycles.
        from agetha.core.fast_mode_profile import get_fast_mode_runtime_overrides

        merged.update(get_fast_mode_runtime_overrides(
            config_path=path,
            config_enabled=fast_mode_requested,
        ))
    except Exception:
        # Profile diagnostics/recovery are handled by its public reconciliation
        # API. A broken optional state file must never prevent config loading.
        pass
    _last_load = result

    for w in result.warnings:
        _log_config(w)

    return merged


def ensure_config_file(path: Path | None = None, *, write_if_missing: bool = True) -> Path:
    """Create config.txt from template if missing. Never overwrites an existing file."""
    path = path or CONFIG_PATH
    if not path.exists() and write_if_missing:
        try:
            create_default_config(path)
            _log_config(f"Created config.txt at {path}", "info")
        except OSError as exc:
            _log_config(f"Could not create config.txt: {exc}")
    return path


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "yes", "true", "on")


def _parse_int(value: str | None, default: int, lo: int | None = None, hi: int | None = None) -> int:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def _parse_float(value: str | None, default: float, lo: float | None = None, hi: float | None = None) -> float:
    try:
        n = float(str(value).strip())
        if not math.isfinite(n):
            n = default
    except (TypeError, ValueError, AttributeError):
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def _parse_http_url(value: str | None, default: str) -> str:
    """Return a credential-free HTTP(S) URL or a known-safe default."""
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
        _ = parsed.port
        if (
            not raw
            or len(raw) > 2048
            or parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        return urlunsplit((
            parsed.scheme.lower(), parsed.netloc, parsed.path.rstrip("/"), "", "",
        ))
    except (TypeError, ValueError):
        return default


_BUILTIN_PROTECTED = frozenset({
    "explorer.exe", "svchost.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "lsass.exe", "services.exe", "smss.exe", "system", "registry",
    "dwm.exe", "fontdrvhost.exe", "sihost.exe", "taskhostw.exe",
    "runtimebroker.exe", "searchindexer.exe", "spoolsv.exe",
    "systemd", "init", "kernel", "kthreadd", "ksoftirqd",
    "agetha.exe", "python.exe", "pythonw.exe",
})


class AppSettings:
    """Typed view of config.txt values."""

    def __init__(self, raw: dict[str, str] | None = None):
        self._raw = raw if raw is not None else parse_config_file()

    def get(self, key: str, default: str = "") -> str:
        key = key.upper()
        if key in self._raw:
            return self._raw[key]
        return default_config_dict().get(key, default)

    def bool(self, key: str, default: bool = False) -> bool:
        return _parse_bool(self.get(key), default)

    def int(self, key: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
        return _parse_int(self.get(key), default, lo, hi)

    def float(self, key: str, default: float, lo: float | None = None, hi: float | None = None) -> float:
        return _parse_float(self.get(key), default, lo, hi)

    @property
    def raw(self) -> dict[str, str]:
        return self._raw

    # ── AI ──────────────────────────────────────────────────────────────────
    @property
    def ai_temperature(self) -> float:
        return self.float("AI_TEMPERATURE", 0.85, 0.0, 2.0)

    @property
    def ai_max_tokens(self) -> int:
        return self.int("AI_MAX_TOKENS", 400, 64, 8192)

    @property
    def ai_top_p(self) -> float:
        return self.float("AI_TOP_P", 0.95, 0.0, 1.0)

    @property
    def enable_streaming(self) -> bool:
        return self.bool("ENABLE_STREAMING", True)

    @property
    def faster_mode(self) -> bool:
        return self.bool("FASTER_MODE", False)

    @property
    def enable_openrouter(self) -> bool:
        return self.bool("ENABLE_OPENROUTER", False)

    @property
    def openrouter_api_key(self) -> str:
        return self.get("OPENROUTER_API_KEY", "").strip()

    @property
    def openrouter_model(self) -> str:
        return self.get("OPENROUTER_MODEL", "google/gemma-4-31b-it:free").strip()

    @property
    def enable_voice(self) -> bool:
        return self.bool("ENABLE_VOICE", False)

    # ── Voice output ────────────────────────────────────────────────────────
    @property
    def voice_output_mode(self) -> str:
        raw = self.get("VOICE_OUTPUT_MODE", "bleeps_only").strip().lower()
        return raw if raw in _VOICE_OUTPUT_MODES else "bleeps_only"

    @property
    def voice_tts_engine(self) -> str:
        raw = self.get("VOICE_TTS_ENGINE", "pyttsx3").strip().lower()
        return raw if raw in _VOICE_TTS_ENGINES else "pyttsx3"

    @property
    def tts_rate(self) -> int:
        return self.int("TTS_RATE", 165, 80, 300)

    @property
    def tts_volume(self) -> float:
        return self.float("TTS_VOLUME", 0.8, 0.0, 1.0)

    @property
    def tts_voice_name(self) -> str:
        return self.get("TTS_VOICE_NAME", "").strip()

    @property
    def use_local_stt(self) -> bool:
        return self.bool("USE_LOCAL_STT", False)

    @property
    def enable_file_drag_drop(self) -> bool:
        return self.bool("ENABLE_FILE_DRAG_DROP", True)

    @property
    def enable_ambient_polls(self) -> bool:
        return self.bool("ENABLE_AMBIENT_POLLS", True)

    @property
    def enable_datetime_context(self) -> bool:
        return self.bool("ENABLE_DATETIME_CONTEXT", True)

    @property
    def datetime_include_seconds(self) -> bool:
        return self.bool("DATETIME_INCLUDE_SECONDS", False)

    @property
    def datetime_include_timezone(self) -> bool:
        return self.bool("DATETIME_INCLUDE_TIMEZONE", True)

    # ── Permissions ─────────────────────────────────────────────────────────
    @property
    def enable_command_execution(self) -> bool:
        return self.bool("ENABLE_COMMAND_EXECUTION", True)

    @property
    def enable_window_control(self) -> bool:
        return self.bool("ENABLE_WINDOW_CONTROL", True)

    @property
    def enable_command_confirmations(self) -> bool:
        return self.bool("ENABLE_COMMAND_CONFIRMATIONS", True)

    @property
    def force_close_auto_allow(self) -> bool:
        return self.bool("FORCE_CLOSE_AUTO_ALLOW", True)

    def protected_processes(self) -> frozenset[str]:
        extra = self.get("PROTECTED_PROCESSES", "")
        names = set(_BUILTIN_PROTECTED)
        for part in extra.replace(";", ",").split(","):
            p = part.strip().lower()
            if not p:
                continue
            if not p.endswith(".exe"):
                p = f"{p}.exe"
            names.add(p)
            names.add(p.replace(".exe", ""))
        return frozenset(names)

    # ── Memory ────────────────────────────────────────────────────────────────
    @property
    def episodic_prompt_limit(self) -> int:
        return self.int("EPISODIC_PROMPT_LIMIT", 10, 0, 50)

    @property
    def episodic_entry_max_chars(self) -> int:
        return self.int("EPISODIC_ENTRY_MAX_CHARS", 300, 50, 2000)

    @property
    def episodic_max_entries(self) -> int:
        return self.int("EPISODIC_MAX_ENTRIES", 50, 5, 500)

    @property
    def enable_longterm_memory(self) -> bool:
        return self.bool("ENABLE_LONGTERM_MEMORY", True)

    @property
    def longterm_memory_max_results(self) -> int:
        return self.int("LONGTERM_MEMORY_MAX_RESULTS", 5, 1, 20)

    @property
    def longterm_memory_max_chars(self) -> int:
        return self.int("LONGTERM_MEMORY_MAX_CHARS", 2500, 200, 10000)

    @property
    def enable_web_rag(self) -> bool:
        return self.bool("ENABLE_WEB_RAG", False)

    @property
    def web_fetch_max_chars(self) -> int:
        return self.int("WEB_FETCH_MAX_CHARS", 8000, 500, 50000)

    @property
    def web_timeout_sec(self) -> int:
        return self.int("WEB_TIMEOUT_SEC", 10, 3, 60)

    @property
    def web_search_max_results(self) -> int:
        return self.int("WEB_SEARCH_MAX_RESULTS", 5, 1, 20)

    @property
    def enable_glitch_effects(self) -> bool:
        return self.bool("ENABLE_GLITCH_EFFECTS", False)

    @property
    def glitch_max_duration_ms(self) -> int:
        return self.int("GLITCH_MAX_DURATION_MS", 2000, 200, 5000)

    @property
    def glitch_default_style(self) -> str:
        raw = self.get("GLITCH_DEFAULT_STYLE", "scanlines").strip().lower()
        return raw if raw in _GLITCH_STYLES else "scanlines"

    @property
    def glitch_mood_auto(self) -> bool:
        return self.bool("GLITCH_MOOD_AUTO", False)

    @property
    def glitch_fullscreen(self) -> bool:
        return self.bool("GLITCH_FULLSCREEN", False)

    @property
    def enable_companion_stats_context(self) -> bool:
        return self.bool("ENABLE_COMPANION_STATS_CONTEXT", True)

    # ── v4.0.0 — Circadian rhythm / dreams / tasks ────────────────────────────
    @property
    def enable_circadian_rhythm(self) -> bool:
        return self.bool("ENABLE_CIRCADIAN_RHYTHM", True)

    @property
    def rhythm_night_start(self) -> int:
        return self.int("RHYTHM_NIGHT_START", 23, 0, 23)

    @property
    def rhythm_night_end(self) -> int:
        return self.int("RHYTHM_NIGHT_END", 6, 0, 23)

    @property
    def enable_dreams(self) -> bool:
        return self.bool("ENABLE_DREAMS", True)

    @property
    def dreams_max_entries(self) -> int:
        return self.int("DREAMS_MAX_ENTRIES", 40, 5, 500)

    @property
    def enable_tasks(self) -> bool:
        return self.bool("ENABLE_TASKS", True)

    @property
    def tasks_max_entries(self) -> int:
        return self.int("TASKS_MAX_ENTRIES", 100, 10, 1000)

    # ── v5.0.0 — Emotion engine ───────────────────────────────────────────────
    @property
    def enable_emotion_engine(self) -> bool:
        return self.bool("ENABLE_EMOTION_ENGINE", True)

    @property
    def emotion_baseline_valence(self) -> int:
        return self.int("EMOTION_BASELINE_VALENCE", 0, -100, 100)

    @property
    def emotion_baseline_arousal(self) -> int:
        return self.int("EMOTION_BASELINE_AROUSAL", 30, 0, 100)

    @property
    def emotion_baseline_trust(self) -> int:
        return self.int("EMOTION_BASELINE_TRUST", 50, 0, 100)

    @property
    def emotion_baseline_loneliness(self) -> int:
        return self.int("EMOTION_BASELINE_LONELINESS", 25, 0, 100)

    @property
    def emotion_decay_per_hour(self) -> float:
        return self.float("EMOTION_DECAY_PER_HOUR", 0.10, 0.0, 1.0)

    @property
    def emotion_history_max(self) -> int:
        return self.int("EMOTION_HISTORY_MAX", 200, 20, 1000)

    # ── v5.0.0 — Windows integration & presence ───────────────────────────────
    @property
    def enable_autostart_control(self) -> bool:
        return self.bool("ENABLE_AUTOSTART_CONTROL", False)

    @property
    def enable_theme_control(self) -> bool:
        return self.bool("ENABLE_THEME_CONTROL", False)

    @property
    def enable_status_providers(self) -> bool:
        return self.bool("ENABLE_STATUS_PROVIDERS", False)

    @property
    def status_poll_interval_sec(self) -> int:
        return self.int("STATUS_POLL_INTERVAL_SEC", 300, 60, 3600)

    @property
    def enable_tray(self) -> bool:
        return self.bool("ENABLE_TRAY", False)

    @property
    def tray_background_close(self) -> bool:
        return self.bool("TRAY_BACKGROUND_CLOSE", False)

    # ── Timing ────────────────────────────────────────────────────────────────
    @property
    def screen_poll_interval_ms(self) -> int:
        return self.int("SCREEN_POLL_INTERVAL_SEC", 120, 15, 3600) * 1000

    @property
    def touch_cooldown_sec(self) -> float:
        return self.float("TOUCH_COOLDOWN_SEC", 10.0, 0.0, 300.0)

    @property
    def wake_delay_ms(self) -> int:
        return self.int("WAKE_DELAY_SEC", 8, 0, 120) * 1000

    @property
    def loaf_timer_ms(self) -> int:
        return self.int("LOAF_TIMER_MIN", 15, 1, 1440) * 60 * 1000

    # ── Mood snap ─────────────────────────────────────────────────────────────
    @property
    def enable_attention_snap(self) -> bool:
        return self.bool("ENABLE_ATTENTION_SNAP", True)

    def mood_snap_thresholds(self) -> dict[str, int]:
        return {
            "manic": self.int("MOOD_SNAP_MANIC_SEC", 120, 30, 3600),
            "angry": self.int("MOOD_SNAP_ANGRY_SEC", 180, 30, 3600),
            "paranoid": self.int("MOOD_SNAP_PARANOID_SEC", 240, 30, 3600),
            "dominant": self.int("MOOD_SNAP_DOMINANT_SEC", 300, 30, 3600),
            "surprised": self.int("MOOD_SNAP_SURPRISED_SEC", 360, 30, 3600),
            "excited": self.int("MOOD_SNAP_EXCITED_SEC", 420, 30, 3600),
            "happy": self.int("MOOD_SNAP_HAPPY_SEC", 600, 30, 3600),
            "neutral": self.int("MOOD_SNAP_NEUTRAL_SEC", 600, 30, 3600),
            "thinking": self.int("MOOD_SNAP_THINKING_SEC", 600, 30, 3600),
            "vulnerable": self.int("MOOD_SNAP_VULNERABLE_SEC", 720, 30, 3600),
            "melancholic": self.int("MOOD_SNAP_MELANCHOLIC_SEC", 900, 30, 3600),
            "sad": self.int("MOOD_SNAP_SAD_SEC", 900, 30, 3600),
            "whisper": self.int("MOOD_SNAP_WHISPER_SEC", 900, 30, 3600),
        }

    # ── OCR ───────────────────────────────────────────────────────────────────
    @property
    def enable_screen_reader(self) -> bool:
        return self.bool("ENABLE_SCREEN_READER", True)

    @property
    def ocr_max_dimension(self) -> int:
        return self.int("OCR_MAX_DIMENSION", 2560, 640, 8192)

    @property
    def ocr_focused_window_only(self) -> bool:
        return self.bool("OCR_FOCUSED_WINDOW_ONLY", True)

    @property
    def ocr_change_detection(self) -> bool:
        return self.bool("OCR_CHANGE_DETECTION", True)

    @property
    def ocr_change_threshold(self) -> float:
        return self.float("OCR_CHANGE_THRESHOLD", 0.025, 0.0, 1.0)

    @property
    def ocr_force_refresh_seconds(self) -> float:
        return self.float("OCR_FORCE_REFRESH_SECONDS", 20.0, 1.0, 3600.0)

    @property
    def ocr_state_expiry_seconds(self) -> float:
        return self.float("OCR_STATE_EXPIRY_SECONDS", 300.0, 30.0, 86400.0)

    @property
    def ocr_pattern_cooldown_seconds(self) -> float:
        return self.float("OCR_PATTERN_COOLDOWN_SECONDS", 60.0, 0.0, 86400.0)

    @property
    def ocr_pattern_confirm_scans(self) -> int:
        return self.int("OCR_PATTERN_CONFIRM_SCANS", 1, 1, 20)

    @property
    def ocr_low_confidence_confirm_scans(self) -> int:
        return self.int("OCR_LOW_CONFIDENCE_CONFIRM_SCANS", 2, 1, 20)

    @property
    def ocr_pattern_clear_scans(self) -> int:
        return self.int("OCR_PATTERN_CLEAR_SCANS", 2, 1, 20)

    @property
    def ocr_min_word_confidence(self) -> float:
        return self.float("OCR_MIN_WORD_CONFIDENCE", 30.0, 0.0, 100.0)

    @property
    def ocr_min_pattern_confidence(self) -> float:
        return self.float("OCR_MIN_PATTERN_CONFIDENCE", 45.0, 0.0, 100.0)

    @property
    def ocr_preprocessing(self) -> str:
        value = self.get("OCR_PREPROCESSING", "auto").strip().lower()
        return value if value in {"basic", "auto"} else "auto"

    @property
    def ocr_languages(self) -> str:
        value = self.get("OCR_LANGUAGES", "eng").strip().replace(" ", "")
        return value[:100] if re.fullmatch(r"[A-Za-z0-9_+-]+", value) else "eng"

    @property
    def ocr_psm(self) -> str:
        value = self.get("OCR_PSM", "auto").strip().lower()
        return value if value in {"auto", "3", "6", "11"} else "auto"

    @property
    def ocr_excluded_apps(self) -> str:
        return self.get("OCR_EXCLUDED_APPS", "")[:2000]

    @property
    def ocr_excluded_title_patterns(self) -> str:
        return self.get("OCR_EXCLUDED_TITLE_PATTERNS", "")[:4000]

    @property
    def ocr_redact_sensitive_text(self) -> bool:
        return self.bool("OCR_REDACT_SENSITIVE_TEXT", True)

    @property
    def include_window_title_in_context(self) -> bool:
        return self.bool("INCLUDE_WINDOW_TITLE_IN_CONTEXT", True)

    @property
    def tesseract_path(self) -> str:
        return self.get("TESSERACT_PATH", "").strip()

    @property
    def deep_ocr_backend(self) -> str:
        value = self.get("DEEP_OCR_BACKEND", "none").strip().lower()
        return value if value in {"none", "unlimited_ocr"} else "none"

    @property
    def unlimited_ocr_server_url(self) -> str:
        return _parse_http_url(
            self.get("UNLIMITED_OCR_SERVER_URL", "http://127.0.0.1:10000"),
            "http://127.0.0.1:10000",
        )

    @property
    def unlimited_ocr_model(self) -> str:
        value = (
            self.get("UNLIMITED_OCR_MODEL", "Unlimited-OCR")
            .replace("\r", " ")
            .replace("\n", " ")
            .strip()
        )
        return value[:200] or "Unlimited-OCR"

    @property
    def unlimited_ocr_timeout_seconds(self) -> int:
        return self.int("UNLIMITED_OCR_TIMEOUT_SECONDS", 180, 10, 1200)

    @property
    def unlimited_ocr_allow_remote(self) -> bool:
        return self.bool("UNLIMITED_OCR_ALLOW_REMOTE", False)

    @property
    def deep_ocr_max_output_chars(self) -> int:
        return self.int("DEEP_OCR_MAX_OUTPUT_CHARS", 12000, 1000, 50000)

    @property
    def unlimited_ocr_api_key(self) -> str:
        return self.get("UNLIMITED_OCR_API_KEY", "").strip()

    # ── UI ────────────────────────────────────────────────────────────────────
    @property
    def window_topmost(self) -> bool:
        return self.bool("WINDOW_TOPMOST", True)

    @property
    def ui_scale(self) -> float | None:
        raw = self.get("UI_SCALE", "auto").strip().lower()
        if not raw or raw == "auto":
            return None
        try:
            return max(0.75, min(float(raw), 2.50))
        except (TypeError, ValueError):
            return None

    @property
    def window_start_x(self) -> int:
        return self.int("WINDOW_START_X", 80, -4096, 8192)

    @property
    def window_start_y(self) -> int:
        return self.int("WINDOW_START_Y", 80, -4096, 8192)

    @property
    def subtitle_char_delay(self) -> float:
        return self.float("SUBTITLE_CHAR_DELAY", 0.035, 0.005, 0.5)

    @property
    def animation_speed(self) -> float:
        return self.float("ANIMATION_SPEED", 0.6, 0.1, 3.0)

    @property
    def window_move_smooth(self) -> bool:
        return self.bool("WINDOW_MOVE_SMOOTH", True)

    @property
    def window_move_duration_ms(self) -> int:
        return self.int("WINDOW_MOVE_DURATION_MS", 280, 0, 2000)

    @property
    def enable_crt_close_animation(self) -> bool:
        return self.bool("ENABLE_CRT_CLOSE_ANIMATION", True)

    @property
    def reduced_motion(self) -> bool:
        return self.bool("REDUCED_MOTION", False)

    @property
    def enable_mood_glow(self) -> bool:
        return self.bool("ENABLE_MOOD_GLOW", False)

    @property
    def mood_glow_animated(self) -> bool:
        return self.bool("MOOD_GLOW_ANIMATED", True)

    @property
    def mood_glow_interval_ms(self) -> int:
        return self.int("MOOD_GLOW_INTERVAL_MS", 150, 100, 1000)

    @property
    def enable_mood_motion(self) -> bool:
        return self.bool("ENABLE_MOOD_MOTION", True)

    @property
    def mood_motion_cooldown_seconds(self) -> int:
        return self.int("MOOD_MOTION_COOLDOWN_SECONDS", 4, 1, 60)

    # ── Launcher ──────────────────────────────────────────────────────────────
    @property
    def skip_tesseract_check(self) -> bool:
        return self.bool("SKIP_TESSERACT_CHECK", False)

    @property
    def skip_asset_check(self) -> bool:
        return self.bool("SKIP_ASSET_CHECK", False)

    @property
    def auto_pip_install(self) -> bool:
        return self.bool("AUTO_PIP_INSTALL", True)

    @property
    def app_version(self) -> str:
        return self.get("APP_VERSION", "5.7").strip() or "5.7"

    @property
    def github_releases_url(self) -> str:
        return self.get("GITHUB_RELEASES_URL", "").strip()

    @property
    def create_desktop_shortcut(self) -> bool:
        return self.bool("CREATE_DESKTOP_SHORTCUT", False)

    @property
    def check_for_updates(self) -> bool:
        return self.bool("CHECK_FOR_UPDATES", True)

    @property
    def dry_run_mode(self) -> bool:
        return self.bool("DRY_RUN_MODE", False)

    @property
    def window_picker_on_ambiguous(self) -> bool:
        return self.bool("WINDOW_PICKER_ON_AMBIGUOUS", True)

    @property
    def ocr_pause_while_typing_sec(self) -> float:
        return self.float("OCR_PAUSE_WHILE_TYPING_SEC", 8.0, 0.0, 120.0)

    def target_app_aliases(self) -> dict[str, str]:
        raw = self.get("TARGET_APP_ALIASES", "")
        aliases: dict[str, str] = {}
        for part in raw.replace(";", ",").split(","):
            if "=" not in part:
                continue
            key, val = part.split("=", 1)
            key, val = key.strip().lower(), val.strip()
            if key and val:
                aliases[key] = val
        return aliases

    def ocr_custom_patterns(self) -> list[tuple[str, str, str]]:
        """Return (label, mood, regex_str) tuples from OCR_CUSTOM_PATTERNS."""
        raw = self.get("OCR_CUSTOM_PATTERNS", "").strip()
        if not raw:
            return []
        items: list[tuple[str, str, str]] = []
        for part in raw.replace("\n", ";").split(";"):
            chunk = part.strip()
            if not chunk:
                continue
            pieces = chunk.split(":", 2)
            if len(pieces) == 3:
                label, mood, pattern = (p.strip() for p in pieces)
            elif len(pieces) == 2:
                label, pattern = pieces[0].strip(), pieces[1].strip()
                mood = "thinking"
            else:
                continue
            if label and pattern:
                items.append((label, mood or "thinking", pattern))
        return items


_CONFIG_DOCUMENT_LINE_RE = re.compile(
    r"^(?P<prefix>\s*)(?P<key>[A-Za-z0-9_]+)(?P<separator>\s*=\s*)(?P<value>.*)$"
)


def _normalise_config_updates(
    updates: Mapping[str, object],
) -> tuple[dict[str, str], list[str]]:
    """Validate structural config updates without parsing or normalising values."""
    clean: dict[str, str] = {}
    failed: list[str] = []
    for raw_key, raw_value in updates.items():
        key = str(raw_key).strip().upper()
        if not key or not key.replace("_", "").isalnum():
            failed.append(key or "<empty>")
            continue
        if _is_secret_key(key):
            _log_config("Refused to write an API key to config.txt — use .env")
            failed.append(key)
            continue
        value = str(raw_value)
        if "\r" in value or "\n" in value:
            # Config values are deliberately one-line. Refusing embedded lines
            # also prevents a value from injecting an unrelated setting.
            failed.append(key)
            continue
        clean[key] = value
    return clean, failed


def read_config_document(path: Path | None = None) -> tuple[str, dict[str, str]]:
    """Return raw config text and only the KEY=VALUE entries actually present."""
    target = Path(path or CONFIG_PATH)
    text = target.read_text(encoding="utf-8", errors="replace")
    parsed, _invalid = _parse_config_text(text)
    return text, parsed


def parse_config_document(text: str) -> dict[str, str]:
    """Parse only entries present in one raw config document (no defaults/.env)."""
    parsed, _invalid = _parse_config_text(text)
    return parsed


def validate_config_document(
    text: str,
    keys: Iterable[str] | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Validate raw typed values without merging defaults or reading .env."""
    raw = parse_config_document(text)
    selected = tuple(dict.fromkeys(
        str(key).strip().upper() for key in (keys if keys is not None else raw)
    ))
    invalid: list[str] = []
    for key in selected:
        if key not in raw:
            continue
        value = raw[key]
        if not validate_config_value(key, value):
            invalid.append(key)
    invalid_set = set(invalid)
    ordered = tuple(key for key in selected if key in invalid_set)
    return not ordered, ordered


def render_config_document(
    text: str,
    updates: Mapping[str, object] | None = None,
    remove_keys: Iterable[str] = (),
) -> str:
    """Patch raw config text while retaining comments, order, blanks and unknowns.

    Every occurrence of an updated key is changed so duplicate settings cannot
    make Python and Medic observe different effective values. Removed keys are
    removed at every occurrence. Missing updates are appended in caller order.
    """
    clean, failed = _normalise_config_updates(updates or {})
    if failed:
        raise ValueError(f"Invalid or forbidden config keys: {', '.join(failed)}")

    removals: set[str] = set()
    for raw_key in remove_keys:
        key = str(raw_key).strip().upper()
        if not key or not key.replace("_", "").isalnum():
            raise ValueError(f"Invalid config key for removal: {raw_key!r}")
        removals.add(key)
    # An explicit update wins if a caller accidentally supplies both.
    removals.difference_update(clean)

    newline = "\r\n" if "\r\n" in text else "\n"
    seen: set[str] = set()
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.endswith("\r\n"):
            body, ending = line[:-2], "\r\n"
        elif line.endswith(("\n", "\r")):
            body, ending = line[:-1], line[-1]
        else:
            body, ending = line, ""
        match = _CONFIG_DOCUMENT_LINE_RE.match(body)
        if not match:
            output.append(line)
            continue
        key = match.group("key").upper()
        if key in removals:
            continue
        if key not in clean:
            output.append(line)
            continue
        output.append(
            f"{match.group('prefix')}{match.group('key')}"
            f"{match.group('separator')}{clean[key]}{ending}"
        )
        seen.add(key)

    rendered = "".join(output)
    missing = [(key, value) for key, value in clean.items() if key not in seen]
    if missing:
        if rendered and not rendered.endswith(("\n", "\r")):
            rendered += newline
        rendered += "".join(f"{key} = {value}{newline}" for key, value in missing)
    return rendered


def write_config_document(path: Path, content: str) -> None:
    """Atomically replace one config document; callers coordinate transactions."""
    with _CONFIG_WRITE_LOCK:
        _write_atomic_config(Path(path), content)


def patch_config_keys(updates: dict[str, str]) -> tuple[bool, list[str]]:
    """Update multiple KEY = value lines in one write. Returns (ok, failed_keys)."""
    if not updates:
        return True, []
    clean, failed = _normalise_config_updates(updates)
    if not clean:
        return False, failed
    path = CONFIG_PATH
    try:
        with _CONFIG_WRITE_LOCK:
            ensure_config_file(write_if_missing=True)
            text = path.read_text(encoding="utf-8", errors="replace")
            rendered = render_config_document(text, clean)
            _write_atomic_config(path, rendered)
        get_settings(reload=True)
        return True, failed
    except Exception as exc:
        _log_config(f"patch_config_keys failed: {exc}")
        return False, list(clean.keys()) + failed


def patch_config_key(key: str, value: str) -> bool:
    """Update one KEY = value line in config.txt. Returns True on success. Never raises."""
    ok, failed = patch_config_keys({key: value})
    return ok and not failed


_settings: AppSettings | None = None


def get_settings(reload: bool = False) -> AppSettings:
    global _settings
    if _settings is None or reload:
        ensure_config_file(write_if_missing=True)
        _settings = AppSettings(parse_config_file())
    return _settings


def create_default_config(path: Path | None = None) -> None:
    path = path or CONFIG_PATH
    _write_atomic_config(path, DEFAULT_CONFIG)
