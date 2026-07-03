"""
app_config.py — Central config.txt loader for Agetha Mod.

Parses config.txt once, merges .env overrides, exposes typed settings.
Missing, unreadable, or invalid config.txt always falls back to DEFAULT_CONFIG.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

CONFIG_PATH = BASE_DIR / "config.txt"
ENV_PATH = BASE_DIR / ".env"

DEFAULT_CONFIG = """# =============================================================================
# Agetha Mod — config.txt
# =============================================================================
#
# HOW THIS FILE WORKS
#   • Format:  KEY = value   (one setting per line)
#   • Comments start with #
#   • Booleans: yes / no  (also: true, false, 1, 0, on, off)
#   • If a line is missing, corrupt, or has an invalid value → built-in default
#   • Secrets: prefer .env for API keys (copy .env.example → .env)
#             .env overrides the same key here when non-empty
#
# After editing, restart Agetha (or re-run Medic_Checker.bat).
# =============================================================================


# ── AI Backend ───────────────────────────────────────────────────────────────
# Choose cloud Groq OR local Ollama — not both at once.

# USE_LOCAL_AI — yes = use Ollama on this PC; no = use Groq cloud API.
USE_LOCAL_AI = no

# ENABLE_GROQ — yes = allow Groq API (ignored when USE_LOCAL_AI = yes).
ENABLE_GROQ = yes

# ENABLE_OPENROUTER — yes = use OpenRouter instead of Groq (experimental).
# Ignored when USE_LOCAL_AI = yes. Get a key: https://openrouter.ai/keys
ENABLE_OPENROUTER = no

# OPENROUTER_API_KEY — single OpenRouter key (prefer .env: OPENROUTER_API_KEY=...)
OPENROUTER_API_KEY =

# OPENROUTER_MODEL — model slug from openrouter.ai/models
OPENROUTER_MODEL = nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free

# FASTER_MODE — yes = shorter prompts (less personality, fewer tokens, cheaper).
FASTER_MODE = no


# ── Groq API Keys ─────────────────────────────────────────────────────────────
# Get free keys: https://console.groq.com
# Up to 10 keys — Agetha rotates on rate limits. Prefer .env: GROQ_API_KEY_1=gsk_...

# GROQ_API_KEY — primary key (GROQ_API_KEY_1 in .env maps here).
GROQ_API_KEY =
GROQ_API_KEY_2 =
GROQ_API_KEY_3 =
GROQ_API_KEY_4 =
GROQ_API_KEY_5 =
GROQ_API_KEY_6 =
GROQ_API_KEY_7 =
GROQ_API_KEY_8 =
GROQ_API_KEY_9 =
GROQ_API_KEY_10 =

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

# INCLUDE_WINDOW_TITLE_IN_CONTEXT — yes = send [Active: Window Title] to AI each poll.
INCLUDE_WINDOW_TITLE_IN_CONTEXT = yes

# TESSERACT_PATH — full path to tesseract.exe if not on PATH (Windows).
# Example: C:\\Program Files\\Tesseract-OCR\\tesseract.exe
TESSERACT_PATH =


# ── UI ────────────────────────────────────────────────────────────────────────

# WINDOW_TOPMOST — yes = Agetha stays above other windows.
WINDOW_TOPMOST = yes

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
APP_VERSION = 3.6.0

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
# TTS requires: pip install pyttsx3  (optional — app runs without it)
# Medic_Checker installs pyttsx3 when VOICE_OUTPUT_MODE is tts_only or both
#   and AUTO_PIP_INSTALL = yes.

VOICE_OUTPUT_MODE = bleeps_only
TTS_RATE = 165
TTS_VOLUME = 0.8
TTS_VOICE_NAME =
"""

_BOOL_KEYS = frozenset({
    "USE_LOCAL_AI", "ENABLE_GROQ", "ENABLE_OPENROUTER", "FASTER_MODE",
    "ENABLE_VOICE", "USE_LOCAL_STT", "ENABLE_FILE_DRAG_DROP",
    "ENABLE_STREAMING", "ENABLE_AMBIENT_POLLS",
    "ENABLE_COMMAND_EXECUTION", "ENABLE_WINDOW_CONTROL", "ENABLE_COMMAND_CONFIRMATIONS",
    "FORCE_CLOSE_AUTO_ALLOW", "ENABLE_ATTENTION_SNAP", "ENABLE_SCREEN_READER",
    "OCR_FOCUSED_WINDOW_ONLY", "INCLUDE_WINDOW_TITLE_IN_CONTEXT", "WINDOW_TOPMOST",
    "SKIP_TESSERACT_CHECK", "SKIP_ASSET_CHECK", "AUTO_PIP_INSTALL",
    "CREATE_DESKTOP_SHORTCUT", "CHECK_FOR_UPDATES", "WINDOW_PICKER_ON_AMBIGUOUS",
    "DRY_RUN_MODE", "WINDOW_MOVE_SMOOTH", "ENABLE_LONGTERM_MEMORY",
    "ENABLE_WEB_RAG",
    "ENABLE_GLITCH_EFFECTS", "GLITCH_MOOD_AUTO", "GLITCH_FULLSCREEN",
    "ENABLE_COMPANION_STATS_CONTEXT",
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
})

_FLOAT_KEYS = frozenset({
    "AI_TEMPERATURE", "AI_TOP_P", "TOUCH_COOLDOWN_SEC", "SUBTITLE_CHAR_DELAY",
    "ANIMATION_SPEED", "OCR_PAUSE_WHILE_TYPING_SEC",
    "TTS_VOLUME",
})

_VOICE_OUTPUT_MODES = frozenset({"bleeps_only", "tts_only", "both"})
_GLITCH_STYLES = frozenset({
    "scanlines", "static", "rgb_split", "flicker", "bsod", "matrix", "tear",
})

_VALID_BOOLS = frozenset({"1", "yes", "true", "on", "0", "no", "false", "off"})


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
            float(str(value).strip())
        else:
            int(str(value).strip())
        return True
    except (TypeError, ValueError, AttributeError):
        return False


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


def _load_env_overrides(config: dict[str, str]) -> None:
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
            if v:
                config[k] = v
    except Exception as exc:
        _log_config(f"Could not read .env: {exc}")


def get_last_config_load() -> ConfigLoadResult | None:
    return _last_load


def parse_config_file(path: Path | None = None) -> dict[str, str]:
    """Load config.txt merged over defaults; .env overrides non-empty values."""
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

    merged, invalid_keys = _merge_with_defaults(file_config)
    result.invalid_keys = invalid_keys
    if invalid_keys:
        result.warnings.append(
            f"Ignored invalid values for: {', '.join(invalid_keys[:12])}"
            + ("…" if len(invalid_keys) > 12 else "")
        )

    _load_env_overrides(merged)
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
    except (TypeError, ValueError, AttributeError):
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


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
        return self.get("OPENROUTER_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free").strip()

    @property
    def enable_voice(self) -> bool:
        return self.bool("ENABLE_VOICE", False)

    # ── Voice output ────────────────────────────────────────────────────────
    @property
    def voice_output_mode(self) -> str:
        raw = self.get("VOICE_OUTPUT_MODE", "bleeps_only").strip().lower()
        return raw if raw in _VOICE_OUTPUT_MODES else "bleeps_only"

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
    def include_window_title_in_context(self) -> bool:
        return self.bool("INCLUDE_WINDOW_TITLE_IN_CONTEXT", True)

    @property
    def tesseract_path(self) -> str:
        return self.get("TESSERACT_PATH", "").strip()

    # ── UI ────────────────────────────────────────────────────────────────────
    @property
    def window_topmost(self) -> bool:
        return self.bool("WINDOW_TOPMOST", True)

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
        return self.get("APP_VERSION", "3.6.0").strip() or "3.6.0"

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


def patch_config_key(key: str, value: str) -> bool:
    """Update one KEY = value line in config.txt. Returns True on success. Never raises."""
    key = key.strip().upper()
    if not key:
        return False
    path = CONFIG_PATH
    try:
        ensure_config_file(write_if_missing=True)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        pattern = re.compile(rf"^(\s*{re.escape(key)}\s*=).*", re.IGNORECASE)
        replaced = False
        out: list[str] = []
        for line in lines:
            if pattern.match(line):
                out.append(f"{key} = {value}\n")
                replaced = True
            else:
                out.append(line if line.endswith("\n") else line + "\n")
        if not replaced:
            out.append(f"{key} = {value}\n")
        path.write_text("".join(out), encoding="utf-8")
        get_settings(reload=True)
        return True
    except Exception as exc:
        _log_config(f"patch_config_key failed for {key}: {exc}")
        return False


_settings: AppSettings | None = None


def get_settings(reload: bool = False) -> AppSettings:
    global _settings
    if _settings is None or reload:
        ensure_config_file(write_if_missing=True)
        _settings = AppSettings(parse_config_file())
    return _settings


def create_default_config(path: Path | None = None) -> None:
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG, encoding="utf-8")
