"""Win95 capability panel with a headless, side-effect-free state collector.

Opening or refreshing this panel never probes an AI/OCR provider, captures the
screen, opens a microphone, or reads memory contents.  It reports configured
and already-known runtime state only.  Tk construction is intentionally kept
below the pure capability model so the collector is usable in headless tests.
"""

from __future__ import annotations

import importlib.util
import os
import queue
import re
import shutil
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping

from agetha.platform.screen_monitoring import redact_sensitive_text
from agetha.utils import logger


class CapabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    DISABLED = "DISABLED"
    DEGRADED = "DEGRADED"
    NOT_CONFIGURED = "NOT CONFIGURED"
    UNKNOWN = "UNKNOWN"
    CHECKING = "CHECKING"


@dataclass(frozen=True)
class SenseCapability:
    key: str
    label: str
    status: CapabilityStatus
    detail: str = ""


@dataclass(frozen=True)
class SensesRuntime:
    """Already-known runtime state; no member triggers a capability probe."""

    screen_reader: object | None = None
    voice_input: object | None = None
    voice_output: object | None = None
    ai_engine: object | None = None
    terminal_sentinel: object | None = None
    companion_state: str | None = None
    selected_microphone: str | None = None
    provider_available: bool | None = None
    deep_ocr_checked: bool = False
    deep_ocr_reachable: bool | None = None
    last_safe_scan_time: datetime | str | None = None
    closing: bool = False

    @classmethod
    def from_app(cls, app: object | None) -> "SensesRuntime":
        if app is None:
            return cls()
        return cls(
            screen_reader=getattr(app, "_screen", None),
            voice_input=getattr(app, "_voice", None),
            voice_output=getattr(app, "_voice_out", None),
            ai_engine=getattr(app, "_ai", None),
            terminal_sentinel=getattr(app, "_terminal_sentinel", None),
            companion_state=str(getattr(app, "_state", "") or "") or None,
            selected_microphone=getattr(app, "_selected_microphone", None),
            provider_available=getattr(app, "_provider_available", None),
            deep_ocr_checked=bool(getattr(app, "_deep_ocr_checked", False)),
            deep_ocr_reachable=getattr(app, "_deep_ocr_reachable", None),
            last_safe_scan_time=getattr(app, "_last_safe_scan_time", None),
            closing=bool(getattr(app, "_closing", False)),
        )


@dataclass(frozen=True)
class SensesSnapshot:
    platform: str
    collected_at: datetime
    vision: tuple[SenseCapability, ...]
    hearing: tuple[SenseCapability, ...]
    memory: tuple[SenseCapability, ...]
    network_ai: tuple[SenseCapability, ...]
    actions: tuple[SenseCapability, ...]
    presence: tuple[SenseCapability, ...]

    @property
    def sections(self) -> tuple[tuple[str, tuple[SenseCapability, ...]], ...]:
        return (
            ("Vision", self.vision),
            ("Hearing", self.hearing),
            ("Memory", self.memory),
            ("Network + AI", self.network_ai),
            ("Actions", self.actions),
            ("Presence", self.presence),
        )

    def get(self, key: str) -> SenseCapability | None:
        for _section, items in self.sections:
            for item in items:
                if item.key == key:
                    return item
        return None

    def as_dict(self) -> dict[str, dict[str, dict[str, str]]]:
        return {
            section: {
                item.key: {"status": item.status.value, "detail": item.detail}
                for item in items
            }
            for section, items in self.sections
        }


# Compatibility-friendly aliases for callers that prefer report terminology.
CapabilityReport = SensesSnapshot
CapabilityItem = SenseCapability


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    if text in {"1", "yes", "true", "on"}:
        return True
    if text in {"0", "no", "false", "off"}:
        return False
    return default


def _setting(settings: object, key: str, default: object = "") -> object:
    raw = getattr(settings, "raw", None)
    if isinstance(raw, Mapping) and key in raw:
        return raw[key]
    attribute = key.casefold()
    # AppSettings calls this one "faster_mode" rather than "fast_mode".
    if key == "FASTER_MODE":
        attribute = "faster_mode"
    try:
        value = getattr(settings, attribute)
        if not callable(value):
            return value
    except (AttributeError, TypeError, ValueError):
        pass
    getter = getattr(settings, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except Exception as exc:
            logger.debug("Senses setting lookup failed: %s", type(exc).__name__)
    return default


_PATH_RE = re.compile(
    r"(?i)(?:[a-z]:[\\/](?:[^\s,:;]+[\\/])*[^\s,:;]*|/(?:home|users)/[^\s,:;]+)"
)


def sanitize_status_detail(value: object, limit: int = 180) -> str:
    """Defensively remove secrets and user-specific paths from panel strings."""
    text = redact_sensitive_text(str(value or ""))
    text = _PATH_RE.sub("[local path]", text)
    try:
        home = str(Path.home())
        if home:
            text = text.replace(home, "[local path]")
    except Exception as exc:
        logger.debug("Senses home-path redaction failed: %s", type(exc).__name__)
    return " ".join(text.split())[: max(0, min(int(limit), 500))]


def _cap(
    key: str,
    label: str,
    status: CapabilityStatus,
    detail: object = "",
) -> SenseCapability:
    return SenseCapability(key, label, status, sanitize_status_detail(detail))


def _known_bool(obj: object | None, attribute: str) -> bool | None:
    if obj is None:
        return None
    try:
        value = getattr(obj, attribute)
        if callable(value):
            value = value()
        return value if isinstance(value, bool) else None
    except Exception as exc:
        logger.debug("Senses runtime capability lookup failed: %s", type(exc).__name__)
        return None


def _memory_is_accessible() -> bool | None:
    try:
        from agetha.app_config import BASE_DIR
        directory = BASE_DIR / "memory"
        if not directory.exists():
            return None
        return os.access(directory, os.R_OK | os.W_OK)
    except Exception as exc:
        logger.debug("Senses memory capability check failed: %s", type(exc).__name__)
        return None


def _load_microphone_name() -> str | None:
    try:
        from agetha.platform.voice_input import load_mic_settings
        data = load_mic_settings()
        if isinstance(data, Mapping):
            value = str(data.get("mic_device_name", "") or "").strip()
            return value or None
    except Exception as exc:
        logger.debug("Senses microphone setting lookup failed: %s", type(exc).__name__)
    return None


def _provider_name(settings: object, runtime: SensesRuntime) -> str:
    engine = runtime.ai_engine
    if engine is not None:
        try:
            status = engine.get_token_status()
            provider = str(status.get("provider", "")).strip().casefold()
            if provider in {"local", "ollama", "groq", "openrouter"}:
                return "local" if provider == "ollama" else provider
        except Exception as exc:
            logger.debug("Senses provider status lookup failed: %s", type(exc).__name__)
        if isinstance(getattr(engine, "_use_local_ai", None), bool):
            return "local" if engine._use_local_ai else "unknown"  # type: ignore[attr-defined]
    if _bool(_setting(settings, "USE_LOCAL_AI", False), False):
        return "local"
    groq = _bool(_setting(settings, "ENABLE_GROQ", True), True)
    openrouter = _bool(_setting(settings, "ENABLE_OPENROUTER", False), False)
    if groq and not openrouter:
        return "groq"
    if openrouter and not groq:
        return "openrouter"
    return "unknown"


def _is_remote_deep_ocr(settings: object) -> bool | None:
    backend = str(_setting(settings, "DEEP_OCR_BACKEND", "none") or "none").casefold()
    if backend == "none":
        return None
    if _bool(_setting(settings, "UNLIMITED_OCR_ALLOW_REMOTE", False), False):
        return True
    url = str(_setting(settings, "UNLIMITED_OCR_SERVER_URL", "") or "").strip().casefold()
    if not url:
        return False
    return not any(token in url for token in ("localhost", "127.0.0.1", "[::1]"))


def _linux_capabilities(
    *,
    env: Mapping[str, str],
    which: Callable[[str], str | None],
    module_available: Callable[[str], bool],
):
    from agetha.platform.linux_session import detect_linux_desktop
    return detect_linux_desktop(
        env=env,
        platform_name="linux",
        which=which,
        pyautogui_ok=module_available("pyautogui"),
        imagegrab_ok=module_available("PIL"),
        mss_ok=module_available("mss"),
    )


def collect_senses_state(
    settings: object | None = None,
    *,
    runtime: SensesRuntime | object | None = None,
    platform_name: str | None = None,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
    module_available: Callable[[str], bool] = _module_available,
    which: Callable[[str], str | None] = shutil.which,
    memory_accessible: bool | None = None,
    microphone_name: str | None = None,
    linux_capabilities: object | None = None,
) -> SensesSnapshot:
    """Collect current capability state without any paid or remote request."""
    if settings is None:
        try:
            from agetha.app_config import get_settings
            settings = get_settings()
        except Exception as exc:
            logger.debug("Senses settings initialization failed: %s", type(exc).__name__)
            settings = object()
    view = runtime if isinstance(runtime, SensesRuntime) else SensesRuntime.from_app(runtime)
    platform_value = str(platform_name or sys.platform).casefold()
    current_time = now or datetime.now().astimezone()
    environment = os.environ if env is None else env
    reader = view.screen_reader
    is_linux = platform_value.startswith("linux")
    is_windows = platform_value.startswith("win")

    linux = linux_capabilities
    if is_linux and linux is None:
        try:
            linux = _linux_capabilities(
                env=environment, which=which, module_available=module_available,
            )
        except Exception as exc:
            logger.debug("Senses Linux capability lookup failed: %s", type(exc).__name__)
            linux = None

    # Vision -----------------------------------------------------------------
    screen_enabled = _bool(_setting(settings, "ENABLE_SCREEN_READER", True), True)
    focused_only = _bool(_setting(settings, "OCR_FOCUSED_WINDOW_ONLY", True), True)
    runtime_auto = _known_bool(reader, "_automatic_capture_available")
    runtime_ocr = _known_bool(reader, "_available")
    explicit_capture = _known_bool(reader, "_explicit_capture_available")
    session_type = str(getattr(linux, "session_type", "unknown") or "unknown")

    if not screen_enabled:
        automatic_status = CapabilityStatus.DISABLED
        automatic_detail = "Screen reader disabled"
    elif runtime_auto is True:
        automatic_status = CapabilityStatus.AVAILABLE
        automatic_detail = "Runtime capture path is available"
    elif runtime_auto is False:
        if explicit_capture is True:
            automatic_status = CapabilityStatus.DEGRADED
            automatic_detail = "Explicit capture is available; automatic capture is restricted"
        else:
            automatic_status = CapabilityStatus.UNAVAILABLE
            automatic_detail = "No automatic capture path is available"
    elif linux is not None:
        automatic = bool(getattr(linux, "automatic_ocr_supported", False))
        explicit = bool(getattr(linux, "explicit_capture_supported", False))
        if automatic:
            automatic_status = CapabilityStatus.AVAILABLE
            automatic_detail = "Xorg automatic capture path is available"
        elif explicit:
            automatic_status = CapabilityStatus.DEGRADED
            automatic_detail = "Wayland permits explicit capture only; automatic capture is restricted"
        else:
            automatic_status = CapabilityStatus.UNAVAILABLE
            automatic_detail = (
                "Wayland compositor restriction" if session_type == "wayland"
                else "No supported capture backend detected"
            )
    elif is_windows:
        if module_available("mss"):
            automatic_status = CapabilityStatus.AVAILABLE
            automatic_detail = "Windows capture package is installed; runtime not yet opened"
        else:
            automatic_status = CapabilityStatus.UNAVAILABLE
            automatic_detail = "Windows capture package is missing"
    else:
        automatic_status = CapabilityStatus.UNKNOWN
        automatic_detail = "Runtime capture state is not known"

    if not screen_enabled:
        focused_status = CapabilityStatus.DISABLED
        focused_detail = "OCR disabled"
    elif not focused_only:
        focused_status = CapabilityStatus.DISABLED
        focused_detail = "Configured for full-display capture"
    elif automatic_status is CapabilityStatus.AVAILABLE and runtime_ocr is True:
        focused_status = CapabilityStatus.AVAILABLE
        focused_detail = "Focused-window OCR selected"
    elif automatic_status is CapabilityStatus.AVAILABLE and not module_available("pytesseract"):
        focused_status = CapabilityStatus.UNAVAILABLE
        focused_detail = "Tesseract Python support is missing"
    elif automatic_status is CapabilityStatus.AVAILABLE:
        focused_status = CapabilityStatus.UNKNOWN
        focused_detail = "Capture is available; OCR executable has not been runtime-verified"
    elif automatic_status is CapabilityStatus.DEGRADED:
        focused_status = CapabilityStatus.DEGRADED
        focused_detail = automatic_detail
    elif automatic_status is CapabilityStatus.UNAVAILABLE:
        focused_status = CapabilityStatus.UNAVAILABLE
        focused_detail = automatic_detail
    else:
        focused_status = CapabilityStatus.UNKNOWN
        focused_detail = "OCR engine has not been runtime-verified"

    deep_backend = str(_setting(settings, "DEEP_OCR_BACKEND", "none") or "none").casefold()
    deep_configured = deep_backend != "none"
    if not deep_configured:
        deep_reachable_status = CapabilityStatus.NOT_CONFIGURED
        deep_reachable_detail = "Deep OCR is explicit-only and not configured"
    elif not view.deep_ocr_checked:
        deep_reachable_status = CapabilityStatus.UNKNOWN
        deep_reachable_detail = "Not checked; reachability is tested only on explicit request"
    elif view.deep_ocr_reachable is True:
        deep_reachable_status = CapabilityStatus.AVAILABLE
        deep_reachable_detail = "Last explicit check succeeded"
    elif view.deep_ocr_reachable is False:
        deep_reachable_status = CapabilityStatus.UNAVAILABLE
        deep_reachable_detail = "Last explicit check failed"
    else:
        deep_reachable_status = CapabilityStatus.UNKNOWN
        deep_reachable_detail = "Explicit check returned no known state"

    backend_name = str(getattr(reader, "_backend_name", "") or "")
    if backend_name and backend_name not in {"lazy", "unavailable"}:
        capture_mode_status = CapabilityStatus.AVAILABLE
        capture_mode_detail = backend_name
    elif linux is not None:
        selected = str(getattr(linux, "selected_screenshot_backend", "unavailable"))
        capture_mode_status = (
            CapabilityStatus.DEGRADED if session_type == "wayland" and selected != "unavailable"
            else CapabilityStatus.AVAILABLE if selected != "unavailable"
            else CapabilityStatus.UNAVAILABLE
        )
        capture_mode_detail = selected.replace("-", " ")
    elif is_windows:
        capture_mode_status = CapabilityStatus.UNKNOWN
        capture_mode_detail = "Windows backend selected lazily on first capture"
    else:
        capture_mode_status = CapabilityStatus.UNKNOWN
        capture_mode_detail = "Capture mode is not known"

    scan_status = str(getattr(reader, "last_monitor_status", "") or "")
    if view.last_safe_scan_time is not None:
        last_scan_status = CapabilityStatus.AVAILABLE
        if isinstance(view.last_safe_scan_time, datetime):
            last_scan_detail = view.last_safe_scan_time.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        else:
            last_scan_detail = str(view.last_safe_scan_time)
    elif scan_status in {"ocr_complete", "ocr_empty", "unchanged"}:
        last_scan_status = CapabilityStatus.AVAILABLE
        last_scan_detail = f"Last runtime result: {scan_status.replace('_', ' ')}; time not retained"
    else:
        last_scan_status = CapabilityStatus.UNKNOWN
        last_scan_detail = "No safe scan time retained"

    excluded_apps = str(_setting(settings, "OCR_EXCLUDED_APPS", "") or "").strip()
    excluded_titles = str(_setting(settings, "OCR_EXCLUDED_TITLE_PATTERNS", "") or "").strip()
    exclusions_active = bool(excluded_apps or excluded_titles)
    if is_windows:
        gui_status = CapabilityStatus.AVAILABLE
        gui_detail = "Windows desktop session detected"
    elif linux is not None:
        gui_present = bool(
            getattr(linux, "display_present", False)
            or getattr(linux, "wayland_display_present", False)
            or session_type in {"x11", "wayland"}
        )
        gui_status = CapabilityStatus.AVAILABLE if gui_present else CapabilityStatus.UNAVAILABLE
        gui_detail = "Desktop session detected" if gui_present else "No desktop display detected"
    else:
        gui_status = CapabilityStatus.UNKNOWN
        gui_detail = "Session not known"
    vision = (
        _cap(
            "desktop_gui", "Desktop GUI", gui_status, gui_detail,
        ),
        _cap("focused_window_ocr", "Focused-window OCR", focused_status, focused_detail),
        _cap("automatic_capture", "Automatic capture", automatic_status, automatic_detail),
        _cap(
            "deep_ocr_configured", "Deep OCR configured",
            CapabilityStatus.AVAILABLE if deep_configured else CapabilityStatus.NOT_CONFIGURED,
            "Explicit-only backend configured" if deep_configured else "Standard Tesseract remains the automatic backend",
        ),
        _cap("deep_ocr_reachable", "Deep OCR reachable", deep_reachable_status, deep_reachable_detail),
        _cap("capture_mode", "Platform capture mode", capture_mode_status, capture_mode_detail),
        _cap("last_safe_scan", "Last safe scan", last_scan_status, last_scan_detail),
        _cap(
            "ocr_exclusions", "OCR exclusions",
            CapabilityStatus.AVAILABLE if exclusions_active else CapabilityStatus.NOT_CONFIGURED,
            "One or more exclusions are active" if exclusions_active else "No custom exclusions configured",
        ),
    )

    # Hearing ----------------------------------------------------------------
    voice_enabled = _bool(_setting(settings, "ENABLE_VOICE", False), False)
    local_stt = _bool(_setting(settings, "USE_LOCAL_STT", False), False)
    runtime_voice_available = _known_bool(view.voice_input, "available")
    speech_package = module_available("speech_recognition")
    audio_package = module_available("pyaudio") or module_available("sounddevice")
    local_package = module_available("faster_whisper")
    if not voice_enabled:
        voice_status = CapabilityStatus.DISABLED
        voice_detail = "Voice input disabled"
    elif runtime_voice_available is True:
        voice_status = CapabilityStatus.AVAILABLE
        voice_detail = "Runtime microphone input is available"
    elif runtime_voice_available is False:
        voice_status = CapabilityStatus.UNAVAILABLE
        voice_detail = "Runtime microphone input is unavailable"
    elif not speech_package or not audio_package or (local_stt and not local_package):
        voice_status = CapabilityStatus.UNAVAILABLE
        voice_detail = "Required microphone or speech package is missing"
    else:
        voice_status = CapabilityStatus.UNKNOWN
        voice_detail = "Dependencies found; microphone has not been opened"

    selected_mic = view.selected_microphone or microphone_name or _load_microphone_name()
    if not voice_enabled:
        mic_status = CapabilityStatus.DISABLED
        mic_detail = "Voice input disabled"
    elif selected_mic:
        mic_status = CapabilityStatus.AVAILABLE
        mic_detail = sanitize_status_detail(selected_mic, 100)
    else:
        mic_status = CapabilityStatus.NOT_CONFIGURED
        mic_detail = "No saved microphone selection"

    tts_mode = str(_setting(settings, "VOICE_OUTPUT_MODE", "bleeps_only") or "bleeps_only").casefold()
    tts_engine = str(_setting(settings, "VOICE_TTS_ENGINE", "pyttsx3") or "pyttsx3").casefold()
    runtime_uses_tts = _known_bool(view.voice_output, "uses_tts")
    tts_package_name = {"edge_tts": "edge_tts", "kokoro": "kokoro", "pyttsx3": "pyttsx3"}.get(
        tts_engine, "pyttsx3",
    )
    if tts_mode == "bleeps_only":
        tts_status = CapabilityStatus.DISABLED
        tts_detail = "Speech synthesis disabled; bleeps only"
    elif runtime_uses_tts is True:
        tts_status = CapabilityStatus.AVAILABLE
        tts_detail = f"{tts_engine} selected"
    elif runtime_uses_tts is False or not module_available(tts_package_name):
        tts_status = CapabilityStatus.UNAVAILABLE
        tts_detail = f"{tts_engine} is not available"
    else:
        tts_status = CapabilityStatus.UNKNOWN
        tts_detail = f"{tts_engine} configured; runtime state unknown"

    hearing = (
        _cap("voice_input", "Voice input", voice_status, voice_detail),
        _cap("microphone", "Selected microphone", mic_status, mic_detail),
        _cap(
            "stt_mode", "Speech recognition mode",
            CapabilityStatus.DISABLED if not voice_enabled else CapabilityStatus.AVAILABLE,
            "Local speech recognition" if local_stt else "Online speech recognition" if voice_enabled else "Voice input disabled",
        ),
        _cap("voice_output", "Voice output", tts_status, tts_detail),
    )

    # Memory -----------------------------------------------------------------
    try:
        episodic_enabled = int(_setting(settings, "EPISODIC_PROMPT_LIMIT", 10) or 0) > 0
    except (TypeError, ValueError):
        episodic_enabled = True
    longterm_enabled = _bool(_setting(settings, "ENABLE_LONGTERM_MEMORY", True), True)
    dreams_enabled = _bool(_setting(settings, "ENABLE_DREAMS", True), True)
    tasks_enabled = _bool(_setting(settings, "ENABLE_TASKS", True), True)
    accessible = _memory_is_accessible() if memory_accessible is None else memory_accessible
    memory = (
        _cap(
            "episodic_memory", "Episodic memory",
            CapabilityStatus.AVAILABLE if episodic_enabled else CapabilityStatus.DISABLED,
            "Enabled" if episodic_enabled else "Prompt limit is zero",
        ),
        _cap(
            "longterm_memory", "Long-term memory",
            CapabilityStatus.AVAILABLE if longterm_enabled else CapabilityStatus.DISABLED,
            "Enabled" if longterm_enabled else "Disabled",
        ),
        _cap(
            "dreams", "Dream journal",
            CapabilityStatus.AVAILABLE if dreams_enabled else CapabilityStatus.DISABLED,
            "Enabled" if dreams_enabled else "Disabled",
        ),
        _cap(
            "tasks", "Task keeper",
            CapabilityStatus.AVAILABLE if tasks_enabled else CapabilityStatus.DISABLED,
            "Enabled" if tasks_enabled else "Disabled",
        ),
        _cap(
            "memory_files", "Memory files accessible",
            CapabilityStatus.AVAILABLE if accessible is True
            else CapabilityStatus.UNAVAILABLE if accessible is False
            else CapabilityStatus.UNKNOWN,
            "Local memory directory is readable and writable" if accessible is True
            else "Local memory directory is inaccessible" if accessible is False
            else "Memory directory has not been created or checked",
        ),
    )

    # Network and AI ---------------------------------------------------------
    provider = _provider_name(settings, view)
    provider_remote = provider in {"groq", "openrouter"}
    if view.provider_available is True:
        provider_availability_status = CapabilityStatus.AVAILABLE
        provider_availability_detail = "Already-known runtime state: available"
    elif view.provider_available is False:
        provider_availability_status = CapabilityStatus.UNAVAILABLE
        provider_availability_detail = "Already-known runtime state: unavailable"
    else:
        provider_availability_status = CapabilityStatus.UNKNOWN
        provider_availability_detail = "Not probed when opening this panel"
    web_enabled = _bool(_setting(settings, "ENABLE_WEB_RAG", False), False)
    remote_deep = _is_remote_deep_ocr(settings)
    network_ai = (
        _cap(
            "provider", "Selected provider",
            CapabilityStatus.AVAILABLE if provider != "unknown" else CapabilityStatus.UNKNOWN,
            "Local Ollama" if provider == "local" else provider.title() if provider != "unknown" else "Active provider is not known",
        ),
        _cap(
            "provider_location", "Provider location",
            CapabilityStatus.AVAILABLE if provider == "local"
            else CapabilityStatus.DEGRADED if provider_remote
            else CapabilityStatus.UNKNOWN,
            "Local" if provider == "local" else "Remote; prompts leave this computer" if provider_remote else "Unknown",
        ),
        _cap(
            "web_retrieval", "Web retrieval",
            CapabilityStatus.AVAILABLE if web_enabled else CapabilityStatus.DISABLED,
            "Enabled; requests remain user-confirmed" if web_enabled else "Disabled",
        ),
        _cap(
            "deep_ocr_remote", "Deep OCR privacy",
            CapabilityStatus.DEGRADED if remote_deep is True
            else CapabilityStatus.AVAILABLE if remote_deep is False
            else CapabilityStatus.NOT_CONFIGURED,
            "Remote endpoint may receive screenshots only after explicit approval" if remote_deep is True
            else "Configured for a local endpoint" if remote_deep is False
            else "Deep OCR is not configured",
        ),
        _cap(
            "provider_availability", "Provider availability",
            provider_availability_status, provider_availability_detail,
        ),
    )

    # Actions ----------------------------------------------------------------
    command_execution = _bool(_setting(settings, "ENABLE_COMMAND_EXECUTION", True), True)
    confirmations = _bool(_setting(settings, "ENABLE_COMMAND_CONFIRMATIONS", True), True)
    dry_run = _bool(_setting(settings, "DRY_RUN_MODE", False), False)
    fast_mode = _bool(_setting(settings, "FASTER_MODE", False), False)
    protected = ()
    try:
        protected_fn = getattr(settings, "protected_processes", None)
        if callable(protected_fn):
            protected = tuple(protected_fn())
    except Exception as exc:
        logger.debug("Senses protected-process lookup failed: %s", type(exc).__name__)
        protected = ()
    if not protected:
        protected = tuple(
            item.strip() for item in str(_setting(settings, "PROTECTED_PROCESSES", "") or "").split(",")
            if item.strip()
        )
    actions = (
        _cap(
            "command_execution", "Command execution",
            CapabilityStatus.AVAILABLE if command_execution else CapabilityStatus.DISABLED,
            "Enabled behind command gates" if command_execution else "All OS commands blocked",
        ),
        _cap(
            "confirmations", "Command confirmations",
            CapabilityStatus.AVAILABLE if confirmations
            else CapabilityStatus.DISABLED if not command_execution
            else CapabilityStatus.DEGRADED,
            "Enabled" if confirmations else "Disabled; caution and danger prompts will not appear",
        ),
        _cap(
            "protected_processes", "Protected-process policy",
            CapabilityStatus.AVAILABLE if protected else CapabilityStatus.DEGRADED,
            "Protected entries configured" if protected else "No protected entries reported",
        ),
        _cap(
            "dry_run", "Dry-run mode",
            CapabilityStatus.AVAILABLE if dry_run else CapabilityStatus.DISABLED,
            "Enabled; actions are previewed" if dry_run else "Disabled",
        ),
        _cap(
            "fast_mode", "Fast Mode",
            CapabilityStatus.AVAILABLE if fast_mode else CapabilityStatus.DISABLED,
            "Enabled; safety and privacy settings remain authoritative" if fast_mode else "Disabled",
        ),
    )

    # Presence ---------------------------------------------------------------
    circadian_enabled = _bool(_setting(settings, "ENABLE_CIRCADIAN_RHYTHM", True), True)
    if circadian_enabled:
        try:
            from agetha.core.rhythm import get_rhythm_phase
            phase = get_rhythm_phase(
                current_time,
                night_start=int(_setting(settings, "RHYTHM_NIGHT_START", 23)),
                night_end=int(_setting(settings, "RHYTHM_NIGHT_END", 6)),
            )
        except Exception as exc:
            logger.debug("Senses rhythm phase lookup failed: %s", type(exc).__name__)
            phase = "unknown"
    else:
        phase = "disabled"
    state = sanitize_status_detail(view.companion_state or "unknown", 40).casefold()
    valid_states = {"sleeping", "thinking", "idle", "talking"}
    state_known = state in valid_states
    etiquette_enabled = _bool(
        _setting(settings, "ENABLE_PRESENCE_ETIQUETTE", True), True,
    )
    sentinel_enabled = _bool(
        _setting(settings, "ENABLE_TERMINAL_SENTINEL", False), False,
    )
    if view.terminal_sentinel is not None:
        runtime_sentinel = _known_bool(view.terminal_sentinel, "enabled")
        if runtime_sentinel is not None:
            sentinel_enabled = runtime_sentinel
    presence = (
        _cap(
            "circadian", "Circadian state",
            CapabilityStatus.AVAILABLE if circadian_enabled else CapabilityStatus.DISABLED,
            phase.replace("_", " "),
        ),
        _cap(
            "companion_state", "Companion state",
            CapabilityStatus.AVAILABLE if state_known else CapabilityStatus.UNKNOWN,
            state if state_known else "Runtime state not supplied",
        ),
        _cap(
            "presence_etiquette", "Presence Etiquette",
            CapabilityStatus.AVAILABLE if etiquette_enabled else CapabilityStatus.DISABLED,
            "Enabled" if etiquette_enabled else "Disabled",
        ),
        _cap(
            "terminal_sentinel", "Terminal Sentinel",
            CapabilityStatus.AVAILABLE if sentinel_enabled else CapabilityStatus.DISABLED,
            "Enabled; allowlist still required" if sentinel_enabled else "Disabled by default",
        ),
        _cap(
            "shutdown", "Application lifecycle",
            CapabilityStatus.DEGRADED if view.closing else CapabilityStatus.AVAILABLE,
            "Shutdown in progress" if view.closing else "Running",
        ),
    )

    return SensesSnapshot(
        platform=platform_value,
        collected_at=current_time,
        vision=vision,
        hearing=hearing,
        memory=memory,
        network_ai=network_ai,
        actions=actions,
        presence=presence,
    )


# Public synonym used by some integration code.
collect_capability_state = collect_senses_state


class SensesRefreshController:
    """Generation-token cancellation for one off-thread refresh at a time."""

    def __init__(
        self,
        collector: Callable[[], SensesSnapshot],
        publish: Callable[[int, SensesSnapshot], None],
        *,
        start_worker: Callable[[Callable[[], None]], object] | None = None,
    ) -> None:
        self._collector = collector
        self._publish = publish
        self._start_worker = start_worker
        self._lock = threading.Lock()
        self._generation = 0
        self._closed = False
        self._cancel_event = threading.Event()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def refresh(self) -> int | None:
        with self._lock:
            if self._closed:
                return None
            self._cancel_event.set()
            self._cancel_event = threading.Event()
            cancelled = self._cancel_event
            self._generation += 1
            generation = self._generation

        def _work() -> None:
            try:
                snapshot = self._collector()
            except Exception as exc:
                logger.debug("Senses refresh collection failed: %s", type(exc).__name__)
                return
            with self._lock:
                stale = self._closed or cancelled.is_set() or generation != self._generation
            if stale:
                return
            self._publish(generation, snapshot)

        if self._start_worker is not None:
            try:
                self._start_worker(_work)
                return generation
            except Exception as exc:
                logger.debug("Senses worker start failed: %s", type(exc).__name__)
                return None
        worker = threading.Thread(target=_work, daemon=True, name="agetha-senses-refresh")
        worker.start()
        return generation

    def is_current(self, generation: int) -> bool:
        with self._lock:
            return (
                not self._closed
                and not self._cancel_event.is_set()
                and generation == self._generation
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._cancel_event.set()


class SensesPanel:
    """Tk renderer; all capability decisions remain in ``collect_senses_state``."""

    def __init__(
        self,
        parent,
        settings: object,
        *,
        runtime: SensesRuntime | object | None = None,
        collector: Callable[..., SensesSnapshot] = collect_senses_state,
        schedule_ui: Callable[[Callable[[], None]], object | None] | None = None,
        start_worker: Callable[..., object | None] | None = None,
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        from agetha.ui.display_scale import resolve_ui_scale, scale_px
        from agetha.ui.w95_window import apply_borderless_win95, show_borderless

        self._tk = tk
        self._settings = settings
        self._runtime = runtime
        self._collector_fn = collector
        self._schedule_ui = schedule_ui
        self._closing = False
        self._after_jobs: set[object] = set()
        self._mailbox: queue.Queue[tuple[int, SensesSnapshot]] = queue.Queue(maxsize=4)
        self._mailbox_job: object | None = None

        self.win = tk.Toplevel(parent)
        apply_borderless_win95(self.win, parent, topmost=True)
        self.win.configure(bg="#c0c0c0")
        scale = resolve_ui_scale(
            self.win.winfo_screenwidth(), self.win.winfo_screenheight(),
            getattr(settings, "ui_scale", None),
        )
        self.win.geometry(f"{scale_px(650, scale)}x{scale_px(500, scale)}")
        self.win.minsize(scale_px(520, scale), scale_px(380, scale))

        outer = tk.Frame(self.win, bg="#c0c0c0", relief="raised", bd=2)
        outer.pack(fill="both", expand=True)
        title = tk.Frame(outer, bg="#000080", height=scale_px(20, scale))
        title.pack(fill="x", padx=2, pady=(2, 0))
        title.pack_propagate(False)
        title_label = tk.Label(
            title, text="⚠  Agetha — Senses Control Panel",
            bg="#000080", fg="#ffffff", font=("MS Sans Serif", 8, "bold"),
            anchor="w", padx=4,
        )
        title_label.pack(side="left", fill="y")
        tk.Button(
            title, text="✕", command=self.close, width=2,
            bg="#c0c0c0", fg="#000000", relief="raised", bd=2,
            font=("MS Sans Serif", 7, "bold"),
        ).pack(side="right", padx=(0, 2), pady=1)

        drag = {"x": 0, "y": 0, "wx": 0, "wy": 0}

        def _drag_start(event) -> None:
            drag.update(x=event.x_root, y=event.y_root, wx=self.win.winfo_x(), wy=self.win.winfo_y())

        def _drag_move(event) -> None:
            self.win.geometry(
                f"+{drag['wx'] + event.x_root - drag['x']}+{drag['wy'] + event.y_root - drag['y']}"
            )

        for widget in (title, title_label):
            widget.bind("<ButtonPress-1>", _drag_start)
            widget.bind("<B1-Motion>", _drag_move)

        self._notebook = ttk.Notebook(outer)
        self._notebook.pack(fill="both", expand=True, padx=6, pady=6)
        self._section_frames: dict[str, object] = {}
        for section in ("Vision", "Hearing", "Memory", "Network + AI", "Actions", "Presence"):
            frame = tk.Frame(self._notebook, bg="#c0c0c0")
            self._notebook.add(frame, text=section)
            self._section_frames[section] = frame

        footer = tk.Frame(outer, bg="#c0c0c0")
        footer.pack(fill="x", padx=8, pady=(0, 8))
        self._status_var = tk.StringVar(value="Capability state has not been collected yet.")
        tk.Label(
            footer, textvariable=self._status_var, bg="#c0c0c0", fg="#000000",
            font=("MS Sans Serif", 8), anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self._refresh_button = tk.Button(
            footer, text="Refresh", command=self.refresh,
            bg="#c0c0c0", fg="#000000", relief="raised", bd=2,
            font=("MS Sans Serif", 8, "bold"), width=10,
        )
        self._refresh_button.pack(side="right")

        def _collector() -> SensesSnapshot:
            return self._collector_fn(settings, runtime=runtime)

        def _worker_starter(callback: Callable[[], None]):
            if start_worker is None:
                worker = threading.Thread(
                    target=callback, daemon=True, name="agetha-senses-refresh",
                )
                worker.start()
                return worker
            try:
                return start_worker(callback, name="senses-refresh")
            except TypeError:
                return start_worker(callback)

        self._refresh_controller = SensesRefreshController(
            _collector, self._publish_from_worker, start_worker=_worker_starter,
        )
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<Destroy>", self._on_destroy, add="+")
        if self._schedule_ui is None:
            self._schedule_mailbox_pump()
        show_borderless(self.win)
        self.refresh()

    def _schedule(self, delay_ms: int, callback: Callable[[], None]) -> object | None:
        if self._closing:
            return None
        try:
            job = self.win.after(delay_ms, callback)
            self._after_jobs.add(job)
            return job
        except Exception as exc:
            logger.debug("Senses UI scheduling failed: %s", type(exc).__name__)
            return None

    def _schedule_mailbox_pump(self) -> None:
        if self._closing or self._mailbox_job is not None:
            return

        def _pump() -> None:
            self._mailbox_job = None
            if self._closing:
                return
            try:
                while True:
                    generation, snapshot = self._mailbox.get_nowait()
                    self._apply_snapshot(generation, snapshot)
            except queue.Empty:
                pass
            if not self._closing:
                self._mailbox_job = self._schedule(50, _pump)

        self._mailbox_job = self._schedule(50, _pump)

    def _publish_from_worker(self, generation: int, snapshot: SensesSnapshot) -> None:
        if self._closing:
            return
        if self._schedule_ui is not None:
            try:
                self._schedule_ui(lambda: self._apply_snapshot(generation, snapshot))
            except Exception as exc:
                logger.debug("Senses UI publication failed: %s", type(exc).__name__)
            return
        try:
            self._mailbox.put_nowait((generation, snapshot))
        except queue.Full:
            try:
                self._mailbox.get_nowait()
                self._mailbox.put_nowait((generation, snapshot))
            except (queue.Empty, queue.Full):
                pass

    def refresh(self) -> None:
        if self._closing:
            return
        self._status_var.set("CHECKING — local capability state only; no provider request")
        self._refresh_button.config(state="disabled")
        if self._refresh_controller.refresh() is None:
            self._status_var.set("UNAVAILABLE — refresh could not start")
            self._refresh_button.config(state="normal")

    def _apply_snapshot(self, generation: int, snapshot: SensesSnapshot) -> None:
        if self._closing or not self._refresh_controller.is_current(generation):
            return
        tk = self._tk
        color = {
            CapabilityStatus.AVAILABLE: "#006000",
            CapabilityStatus.UNAVAILABLE: "#800000",
            CapabilityStatus.DISABLED: "#606060",
            CapabilityStatus.DEGRADED: "#806000",
            CapabilityStatus.NOT_CONFIGURED: "#606060",
            CapabilityStatus.UNKNOWN: "#404080",
            CapabilityStatus.CHECKING: "#404080",
        }
        for section, items in snapshot.sections:
            frame = self._section_frames[section]
            for child in frame.winfo_children():
                child.destroy()
            tk.Label(
                frame, text=f"{section} capabilities", bg="#c0c0c0", fg="#000000",
                font=("MS Sans Serif", 8, "bold"), anchor="w",
            ).pack(fill="x", padx=10, pady=(10, 5))
            for item in items:
                row = tk.Frame(frame, bg="#c0c0c0")
                row.pack(fill="x", padx=10, pady=3)
                tk.Label(
                    row, text=f"{item.label}:", width=25, anchor="w",
                    bg="#c0c0c0", fg="#000000", font=("MS Sans Serif", 8),
                ).pack(side="left")
                tk.Label(
                    row, text=item.status.value, width=16, anchor="w",
                    bg="#c0c0c0", fg=color[item.status],
                    font=("MS Sans Serif", 8, "bold"),
                ).pack(side="left")
                tk.Label(
                    row, text=item.detail, anchor="w", justify="left", wraplength=280,
                    bg="#c0c0c0", fg="#000000", font=("MS Sans Serif", 8),
                ).pack(side="left", fill="x", expand=True)
        self._status_var.set(
            f"Updated {snapshot.collected_at.astimezone().strftime('%H:%M:%S')} — no network checks run"
        )
        self._refresh_button.config(state="normal")

    def _on_destroy(self, event=None) -> None:
        if event is not None and getattr(event, "widget", None) is not self.win:
            return
        self._closing = True
        self._refresh_controller.close()

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._refresh_controller.close()
        for job in tuple(self._after_jobs):
            try:
                self.win.after_cancel(job)
            except Exception as exc:
                logger.debug("Senses scheduled job cancellation failed: %s", type(exc).__name__)
        self._after_jobs.clear()
        self._mailbox_job = None
        try:
            self.win.destroy()
        except Exception as exc:
            logger.debug("Senses panel close failed: %s", type(exc).__name__)


def open_senses_panel(
    parent,
    app_settings: object | None = None,
    *,
    runtime: SensesRuntime | object | None = None,
    schedule_ui: Callable[[Callable[[], None]], object | None] | None = None,
    start_worker: Callable[..., object | None] | None = None,
) -> SensesPanel:
    """Create the panel; callers should retain it and call ``close`` on shutdown."""
    if app_settings is None:
        from agetha.app_config import get_settings
        app_settings = get_settings()
    if runtime is not None:
        schedule_ui = schedule_ui or getattr(runtime, "_schedule_ui", None)
        start_worker = start_worker or getattr(runtime, "_start_worker", None)
    return SensesPanel(
        parent,
        app_settings,
        runtime=runtime,
        schedule_ui=schedule_ui,
        start_worker=start_worker,
    )


__all__ = [
    "CapabilityItem",
    "CapabilityReport",
    "CapabilityStatus",
    "SenseCapability",
    "SensesPanel",
    "SensesRefreshController",
    "SensesRuntime",
    "SensesSnapshot",
    "collect_capability_state",
    "collect_senses_state",
    "open_senses_panel",
    "sanitize_status_detail",
]
