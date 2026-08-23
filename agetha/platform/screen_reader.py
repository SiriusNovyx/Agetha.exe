"""
screen_reader.py — Phase 3: Precision Screen Intelligence

Four upgrades:
  1. Focused Window Scanning   — GetForegroundWindow + GetWindowRect via ctypes.
                                  Only captures the app you are actively using.
                                  Falls back to full-monitor on non-Windows / failure.
  2. Spatial Text Mapping      — pytesseract.image_to_data() returns each word's
                                  (x, y) in physical desktop-screen coordinates.
                                  Lets Agetha say "Error is at (640, 200)" and move
                                  her window right next to it.
  3. Advanced Error Patterns   — re-based pattern registry covering Luau/Roblox,
                                  Python, terminal, build tools, and system crashes.
                                  Each match carries a suggested mood for the AI.
  4. Multi-Monitor & DPI       — SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
                                  at module level forces Win32 to return physical pixel
                                  coordinates; MonitorFromWindow resolves the correct
                                  display device for any multi-monitor layout.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import tempfile
import hashlib
import importlib
import threading
import time
from dataclasses import dataclass
from pathlib import Path
import sys

# Platform Detection Setup
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

# ── 4. DPI Awareness — must run before ANY Win32 geometry call ─────────────────
def _setup_dpi_awareness() -> None:
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # Windows 8.1 +
    except (AttributeError, OSError):
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()     # Vista / 7 fallback
        except Exception:
            pass

_setup_dpi_awareness()

from agetha.utils import logger
from agetha.platform.linux_session import detect_linux_desktop
from agetha.platform.ocr_backends.base import OCRLine, OCRResult
from agetha.platform.ocr_backends.tesseract_backend import TesseractOCRBackend
from agetha.platform.screen_monitoring import (
    CapturedFrame,
    PatternEventTracker,
    ScreenChangeDetector,
    compile_title_exclusions,
    is_capture_excluded,
    parse_csv_values,
    preprocess_ocr_image,
    redact_sensitive_text,
)
ctypes = None
if IS_WINDOWS:
    import ctypes
    try:
        import ctypes.wintypes
    except ImportError:
        pass

# ── Optional third-party imports ──────────────────────────────────────────────
try:
    from PIL import Image
    PIL_OK = True
except ImportError:
    PIL_OK = False

try:
    from PIL import ImageGrab
    IMAGEGRAB_OK = True
except ImportError:
    IMAGEGRAB_OK = False

try:
    import pytesseract
    TESSERACT_OK = True
except ImportError:
    TESSERACT_OK = False

pyautogui = None
PYAUTOGUI_OK = False


def _load_optional_pyautogui(importer=importlib.import_module):
    """Load PyAutoGUI without allowing display-runtime failures to abort import."""
    try:
        module = importer("pyautogui")
    except Exception as exc:
        logger.warning(
            "PyAutoGUI unavailable; continuing without it: %s",
            type(exc).__name__,
        )
        return None, False
    return module, True


pyautogui, PYAUTOGUI_OK = _load_optional_pyautogui()


# ── Tesseract path helper (Windows) ──────────────────────────────────────────
def _find_tesseract_windows() -> str | None:
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    pf   = os.environ.get("PROGRAMFILES")
    pf86 = os.environ.get("PROGRAMFILES(X86)")
    if pf:   candidates.append(str(Path(pf)   / "Tesseract-OCR" / "tesseract.exe"))
    if pf86: candidates.append(str(Path(pf86) / "Tesseract-OCR" / "tesseract.exe"))
    for c in candidates:
        if Path(c).exists():
            return c
    return None


# ── Legacy flat-keyword list (kept for backward-compat with ai_engine check) ──
ANGRY_KEYWORDS = [
    "cheating", "error 404", "you have been banned", "access denied",
    "virus detected", "your account", "suspicious activity",
    "malware", "unauthorized", "security breach", "account suspended",
    "payment failed", "your ip has been", "please verify",
]


# ── 3. Advanced Pattern Registry ──────────────────────────────────────────────
@dataclass
class PatternDef:
    """One category of screen event Agetha should recognise and react to."""
    category: str        # internal key
    mood:     str        # mood to suggest (angry / thinking / manic / paranoid)
    label:    str        # injected into AI context: "[label: snippet]"
    pattern:  re.Pattern
    severity: str = "error"
    app_names: tuple[str, ...] = ()
    window_title_tokens: tuple[str, ...] = ()
    cooldown_seconds: float | None = None
    minimum_confidence: float = 0.0


PATTERN_REGISTRY: list[PatternDef] = [

    # ── Python / General IDE ──────────────────────────────────────────────────
    PatternDef("py_syntax",
               "thinking",
               "Python syntax error",
               re.compile(r"SyntaxError|IndentationError|TabError", re.I)),

    PatternDef("py_runtime",
               "angry",
               "Python runtime error",
               re.compile(
                   r"Traceback \(most recent call last\)"
                   r"|(?:TypeError|AttributeError|NameError|KeyError|ValueError"
                   r"|IndexError|RuntimeError):",
                   re.I
               )),

    PatternDef("py_import",
               "thinking",
               "Python import failure",
               re.compile(r"ModuleNotFoundError|ImportError|No module named", re.I)),

    PatternDef("py_assert",
               "angry",
               "Test / assertion failure",
               re.compile(r"AssertionError|FAILED.*test|pytest.*FAILED", re.I)),

    # ── Terminal / Shell ──────────────────────────────────────────────────────
    PatternDef("cmd_not_found",
               "thinking",
               "Command not found",
               re.compile(
                   r"is not recognized as an internal or external command"
                   r"|command not found|No such file or directory",
                   re.I
               )),

    PatternDef("cmd_access",
               "angry",
               "Terminal access denied",
               re.compile(r"Access is denied|Permission denied|EPERM|EACCES|Operation not permitted", re.I)),

    PatternDef("cmd_failed",
               "angry",
               "Process execution failure",
               re.compile(
                   r"The process cannot access|failed with exit code"
                   r"|returned non-zero exit status|exited with error",
                   re.I
               )),

    PatternDef("powershell_err",
               "thinking",
               "PowerShell error",
               re.compile(r"FullyQualifiedErrorId|At line:\d+|CategoryInfo\s*:", re.I)),

    # ── Build / Compile ───────────────────────────────────────────────────────
    PatternDef("build_error",
               "angry",
               "Build failure",
               re.compile(r"Build FAILED|error\s+MSB\d{4}|LINK\s*:\s*fatal error|error\s+C\d{4}", re.I)),

    PatternDef("npm_error",
               "thinking",
               "npm / Node.js error",
               re.compile(r"npm ERR!|yarn error|node:internal|ENOENT.*require|Cannot find module", re.I)),

    # ── Security / Network ────────────────────────────────────────────────────
    PatternDef("security_ban",
               "angry",
               "Account security alert",
               re.compile(
                   r"you have been banned|account suspended|unauthorized access"
                   r"|suspicious activity|your account has",
                   re.I
               ), minimum_confidence=65),

    PatternDef("security_access",
               "angry",
               "Access denied",
               re.compile(r"access denied|403 Forbidden|401 Unauthorized", re.I),
               minimum_confidence=60),

    PatternDef("virus_alert",
               "paranoid",
               "Security threat detected",
               re.compile(
                   r"virus detected|malware detected|threat detected"
                   r"|quarantined|real-time protection blocked",
                   re.I
               ), minimum_confidence=65),

    # ── System Crash ──────────────────────────────────────────────────────────
    PatternDef("crash_bsod",
               "manic",
               "BSOD / kernel panic",
               re.compile(
                   r"PAGE_FAULT_IN_NONPAGED_AREA|IRQL_NOT_LESS_OR_EQUAL"
                   r"|CRITICAL_PROCESS_DIED|STOP:\s*0x[0-9A-Fa-f]+",
                   re.I
               ), minimum_confidence=30),

    PatternDef("fatal_error",
               "manic",
               "Fatal / unhandled error",
               re.compile(
                   r"FATAL ERROR|CRITICAL FAILURE|EXCEPTION_ACCESS_VIOLATION"
                   r"|Unhandled exception|Application crash",
                   re.I
               ), severity="critical", cooldown_seconds=120,
               minimum_confidence=30),

    # Roblox/Luau patterns are title-scoped to avoid generic "nil" false alarms.
    PatternDef(
        "luau_runtime", "angry", "Roblox Luau runtime error",
        re.compile(
            r"attempt to (?:index|call|perform arithmetic on).*nil"
            r"|is not a valid member of|stack begin|stack end",
            re.I,
        ),
        app_names=("Roblox Studio", "Roblox"),
        severity="error",
        minimum_confidence=35,
    ),
    PatternDef(
        "luau_infinite_yield", "thinking", "Roblox infinite yield warning",
        re.compile(r"Infinite yield possible on", re.I),
        app_names=("Roblox Studio", "Roblox"),
        severity="warning",
        cooldown_seconds=120,
    ),
    PatternDef(
        "luau_script_location", "thinking", "Roblox script error location",
        re.compile(r"Script ['\"].+['\"], Line \d+|\b\w+\.lua:\d+", re.I),
        app_names=("Roblox Studio", "Roblox"),
        severity="error",
    ),

    # A deliberately small set of high-signal developer/system failures.
    PatternDef(
        "git_failure", "thinking", "Git operation failure",
        re.compile(
            r"fatal: not a git repository|CONFLICT \(.+\):|non-fast-forward"
            r"|failed to push some refs",
            re.I,
        ),
        severity="error",
    ),
    PatternDef(
        "docker_failure", "thinking", "Docker operation failure",
        re.compile(
            r"Cannot connect to the Docker daemon|pull access denied"
            r"|container .* is unhealthy|docker: Error response from daemon",
            re.I,
        ),
        severity="error",
    ),
    PatternDef(
        "network_failure", "thinking", "Network connection failure",
        re.compile(
            r"connection (?:refused|timed out|reset by peer)"
            r"|temporary failure in name resolution|ERR_NAME_NOT_RESOLVED",
            re.I,
        ),
        severity="warning",
        cooldown_seconds=120,
    ),
    PatternDef(
        "windows_application_error", "angry", "Windows application error",
        re.compile(
            r"The application was unable to start correctly \(0x[0-9a-f]+\)"
            r"|Windows protected your PC|has stopped working",
            re.I,
        ),
        severity="critical",
        cooldown_seconds=120,
    ),
]


@dataclass
class PatternMatch:
    """Result of a successful PATTERN_REGISTRY match."""
    category: str
    mood:     str
    label:    str
    snippet:  str   # the matching line, capped at 120 chars
    severity: str = "error"
    confidence: float | None = None
    screen_x: int | None = None
    screen_y: int | None = None
    cooldown_seconds: float | None = None


_custom_patterns_loaded = False


def _ensure_custom_patterns() -> None:
    global _custom_patterns_loaded
    if _custom_patterns_loaded:
        return
    try:
        from agetha.app_config import get_settings
        for label, mood, pattern_str in get_settings().ocr_custom_patterns():
            try:
                pat = re.compile(pattern_str, re.I)
            except re.error:
                logger.warning(f"Invalid OCR_CUSTOM_PATTERNS regex: {pattern_str!r}")
                continue
            key = f"custom_{label.lower().replace(' ', '_')[:40]}"
            PATTERN_REGISTRY.append(PatternDef(key, mood, label, pat))
    except Exception as exc:
        logger.debug(f"Custom OCR patterns skipped: {exc}")
    _custom_patterns_loaded = True


def _scan_patterns(
    text: str,
    ocr_lines: list[OCRLine] | None = None,
    *,
    window_title: str = "",
    process_name: str = "",
    minimum_confidence: float = 0.0,
) -> list[PatternMatch]:
    """Match structured OCR lines, preserving confidence and desktop position."""
    seen:    set[str]         = set()
    results: list[PatternMatch] = []
    candidates: list[tuple[str, OCRLine | None]] = (
        [(line.text, line) for line in ocr_lines]
        if ocr_lines else [(line, None) for line in text.splitlines()]
    )
    app_context = f"{window_title} {process_name}".strip().casefold()
    for pdef in PATTERN_REGISTRY:
        if pdef.category in seen:
            continue
        title_scopes = (*pdef.app_names, *pdef.window_title_tokens)
        if title_scopes and not any(
            token.casefold() in app_context for token in title_scopes
        ):
            continue
        for line_text, line in candidates:
            match = pdef.pattern.search(line_text)
            if match:
                confidence = (
                    float(line.average_confidence) if line is not None else None
                )
                required = max(
                    float(minimum_confidence),
                    float(pdef.minimum_confidence),
                )
                if confidence is not None and confidence < required:
                    continue
                snippet_start = max(0, match.start() - 50)
                snippet_end = min(len(line_text), match.end() + 50)
                snippet = line_text[snippet_start:snippet_end].strip()[:120]
                results.append(PatternMatch(
                    category = pdef.category,
                    mood     = pdef.mood,
                    label    = pdef.label,
                    snippet  = snippet,
                    severity = pdef.severity,
                    confidence = confidence,
                    screen_x = (
                        line.x + line.width // 2 if line is not None else None
                    ),
                    screen_y = (
                        line.y + line.height // 2 if line is not None else None
                    ),
                    cooldown_seconds = pdef.cooldown_seconds,
                ))
                seen.add(pdef.category)
                break
    return results


# ── 1. Foreground-window + 4. Multi-monitor helpers (Windows) ─────────────────

def _get_window_process(hwnd: int) -> tuple[str, int | None]:
    if not IS_WINDOWS or not hwnd:
        return "", None
    handle = None
    pid = None
    try:
        process_id = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(
            hwnd, ctypes.byref(process_id),
        )
        if not process_id.value:
            return "", None
        pid = int(process_id.value)
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, process_id.value)
        if not handle:
            return "", pid
        size = ctypes.wintypes.DWORD(1024)
        path_buffer = ctypes.create_unicode_buffer(size.value)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
            handle, 0, path_buffer, ctypes.byref(size),
        ):
            return "", pid
        return Path(path_buffer.value).name[:120], pid
    except Exception:
        return "", pid
    finally:
        if handle:
            try:
                ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                pass


def _get_window_process_name(hwnd: int) -> str:
    """Compatibility wrapper for callers that only need the executable name."""
    return _get_window_process(hwnd)[0]


def _get_window_info(hwnd: int, skip_hwnd: int | None = None) -> dict | None:
    """Return current geometry and metadata for one Windows HWND."""
    if not IS_WINDOWS:
        return None
    try:
        hwnd = int(hwnd)
        if not hwnd:
            return None
        if skip_hwnd and hwnd == skip_hwnd:
            return None                        # don't capture our own window

        # Window title (for AI context: "Active: Roblox Studio")
        title = ""
        try:
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value[:60].strip()
        except Exception:
            pass

        minimized = bool(ctypes.windll.user32.IsIconic(hwnd))
        mapped = bool(ctypes.windll.user32.IsWindowVisible(hwnd))

        # Bounding rect in physical pixels (DPI awareness is already set)
        rect = ctypes.wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None

        w = rect.right  - rect.left
        h = rect.bottom - rect.top
        if w > 7680 or h > 4320:
            return None   # absurd size — skip to avoid mss crash

        process_name, process_id = _get_window_process(hwnd)
        return {
            "left":   rect.left,
            "top":    rect.top,
            "width":  w,
            "height": h,
            "title":  title,
            "hwnd":   hwnd,
            "process_name": process_name,
            "process_id": process_id,
            "minimized": minimized,
            "mapped": mapped,
        }
    except Exception as e:
        logger.debug(f"Window lookup failed: {type(e).__name__}")
        return None


def _get_foreground_window_info(skip_hwnd: int | None = None) -> dict | None:
    """Return mss-compatible capture metadata for the focused Windows window."""
    if not IS_WINDOWS:
        return None
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
    except Exception as e:
        logger.debug(f"Foreground-window lookup failed: {type(e).__name__}")
        return None
    return _get_window_info(hwnd, skip_hwnd=skip_hwnd)


def _linux_window_process(window_id: int) -> tuple[str, int | None]:
    """Resolve an X11 window's executable and PID without a Python dependency."""
    if not IS_LINUX:
        return "", None
    pid = None
    try:
        result = subprocess.run(
            ["xdotool", "getwindowpid", str(int(window_id))],
            capture_output=True, text=True, timeout=2,
        )
        value = result.stdout.strip()
        if result.returncode == 0 and value.isdigit():
            pid = int(value)
    except Exception:
        pass
    if pid is None:
        try:
            result = subprocess.run(
                ["xprop", "-id", f"0x{int(window_id):x}", "_NET_WM_PID"],
                capture_output=True, text=True, timeout=2,
            )
            match = re.search(r"_NET_WM_PID(?:\([^)]*\))?\s*=\s*(\d+)", result.stdout)
            if result.returncode == 0 and match:
                pid = int(match.group(1))
        except Exception:
            pass
    if pid is None or pid <= 0:
        return "", None
    try:
        name = Path(os.readlink(f"/proc/{pid}/exe")).name[:120]
    except (OSError, ValueError):
        try:
            name = (Path("/proc") / str(pid) / "comm").read_text(
                encoding="utf-8", errors="replace",
            ).strip()[:120]
        except OSError:
            name = ""
    return name, pid


def _linux_process_name_from_window(window_id: int) -> str:
    """Backward-compatible executable-name helper for one X11 window."""
    return _linux_window_process(window_id)[0]


def _linux_window_state(window_id: int) -> tuple[bool | None, bool]:
    """Return (mapped, minimized) from X11 metadata without importing Xlib."""
    try:
        result = subprocess.run(
            [
                "xprop", "-id", f"0x{int(window_id):x}",
                "WM_STATE", "_NET_WM_STATE",
            ],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            return None, False
        output = result.stdout.casefold()
        minimized = "_net_wm_state_hidden" in output or "iconic state" in output
        mapped = True if "normal state" in output or "iconic state" in output else None
        return mapped, minimized
    except Exception:
        return None, False


def _get_linux_window_info(
    window_id: int,
    skip_hwnd: int | None = None,
) -> dict | None:
    """Refresh geometry and process identity for a specific X11 window ID."""
    if not IS_LINUX:
        return None
    try:
        window_id = int(window_id)
    except (TypeError, ValueError):
        return None
    if not window_id or (skip_hwnd and window_id == skip_hwnd):
        return None

    title = ""
    try:
        result = subprocess.run(
            ["xdotool", "getwindowname", str(window_id)],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            title = result.stdout.strip()
        result = subprocess.run(
            ["xdotool", "getwindowgeometry", str(window_id)],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            position = re.search(r"Position:\s*(-?\d+),(-?\d+)", result.stdout)
            size = re.search(r"Geometry:\s*(\d+)x(\d+)", result.stdout)
            if position and size:
                process_name, process_id = _linux_window_process(window_id)
                mapped, minimized = _linux_window_state(window_id)
                return {
                    "left": int(position.group(1)),
                    "top": int(position.group(2)),
                    "width": int(size.group(1)),
                    "height": int(size.group(2)),
                    "title": title,
                    "hwnd": window_id,
                    "process_name": process_name,
                    "process_id": process_id,
                    "mapped": mapped,
                    "minimized": minimized,
                }
    except Exception as exc:
        logger.debug(f"xdotool window lookup failed: {type(exc).__name__}")

    try:
        result = subprocess.run(
            ["wmctrl", "-l", "-G"], capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split(maxsplit=7)
                if len(parts) < 8:
                    continue
                try:
                    listed_id = int(parts[0], 16)
                except ValueError:
                    continue
                if listed_id != window_id:
                    continue
                process_name, process_id = _linux_window_process(window_id)
                mapped, minimized = _linux_window_state(window_id)
                return {
                    "left": int(parts[2]),
                    "top": int(parts[3]),
                    "width": int(parts[4]),
                    "height": int(parts[5]),
                    "title": parts[7],
                    "hwnd": window_id,
                    "process_name": process_name,
                    "process_id": process_id,
                    "mapped": mapped,
                    "minimized": minimized,
                }
    except Exception as exc:
        logger.debug(f"wmctrl window lookup failed: {type(exc).__name__}")
    return None


def _get_foreground_window_info_linux(skip_hwnd: int | None = None) -> dict | None:
    """Return mss-compatible capture dict + metadata for the focused window on Linux.
    Uses xdotool, xprop, or wmctrl.
    """
    if not IS_LINUX:
        return None

    window_id = None
    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=2,
        )
        value = result.stdout.strip()
        if result.returncode == 0 and value.isdigit():
            window_id = int(value)
    except Exception as e:
        logger.debug(f"xdotool foreground lookup failed: {type(e).__name__}")

    if window_id is None:
        try:
            result = subprocess.run(
                ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                capture_output=True, text=True, timeout=2,
            )
            match = re.search(
                r"_NET_ACTIVE_WINDOW\(WINDOW\):\s*window id #\s*"
                r"(0x[0-9a-fA-F]+)",
                result.stdout,
            )
            if result.returncode == 0 and match:
                window_id = int(match.group(1), 16)
        except Exception as e:
            logger.debug(f"xprop foreground lookup failed: {type(e).__name__}")

    if window_id is not None:
        info = _get_linux_window_info(window_id, skip_hwnd=skip_hwnd)
        if info is not None:
            return info

    logger.debug("Linux active-window tools unavailable; full capture may be used")
    return None


def _find_monitor_for_window(hwnd: int) -> dict | None:
    """Return the mss monitor dict for whichever display contains hwnd.
    Handles multi-monitor layouts correctly — even negative-origin secondary monitors.
    """
    if platform.system() != "Windows":
        return None
    try:
        MONITOR_DEFAULTTONEAREST = 2
        hmonitor = ctypes.windll.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not hmonitor:
            return None

        class _MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize",    ctypes.c_ulong),
                ("rcMonitor", ctypes.wintypes.RECT),
                ("rcWork",    ctypes.wintypes.RECT),
                ("dwFlags",   ctypes.c_ulong),
            ]

        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if ctypes.windll.user32.GetMonitorInfoA(hmonitor, ctypes.byref(mi)):
            r = mi.rcMonitor
            return {
                "left":   r.left,
                "top":    r.top,
                "width":  r.right  - r.left,
                "height": r.bottom - r.top,
            }
    except Exception as e:
        logger.debug(f"Monitor lookup failed: {type(e).__name__}")
    return None


# ── Screenshot backends ────────────────────────────────────────────────────────

def _cmd_exists(cmd: str) -> bool:
    if platform.system() == "Windows":
        try:
            return subprocess.run(["where", cmd], capture_output=True).returncode == 0
        except FileNotFoundError:
            return False
    try:
        return subprocess.run(["which", cmd], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def _has_display() -> bool:
    return bool(
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or
        os.environ.get("XDG_SESSION_TYPE") or
        platform.system() in ("Windows", "Darwin")
    )


def _is_wayland() -> bool:
    return (bool(os.environ.get("WAYLAND_DISPLAY")) or
            os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland")


def _validate_capture_target(info: dict | None) -> tuple[dict | None, str]:
    """Normalize one window target and reject unsafe state or geometry."""
    if not info:
        return None, "skipped_capture_target_missing"
    if info.get("minimized") is True:
        return None, "skipped_minimized"
    if info.get("mapped") is False:
        return None, "skipped_unmapped"
    try:
        target = {
            "left": int(info["left"]),
            "top": int(info["top"]),
            "width": int(info["width"]),
            "height": int(info["height"]),
        }
    except (KeyError, TypeError, ValueError, OverflowError):
        return None, "skipped_invalid_geometry"
    if target["width"] <= 0 or target["height"] <= 0:
        return None, "skipped_invalid_geometry"
    return target, ""


def _clip_capture_rect(
    rect: dict | None,
    display: dict | None,
) -> tuple[dict | None, str]:
    """Clip a capture rectangle to a positive virtual-display rectangle."""
    target, status = _validate_capture_target(rect)
    if target is None:
        return None, status
    bounds, status = _validate_capture_target(display)
    if bounds is None:
        return None, "skipped_invalid_display"
    left = max(target["left"], bounds["left"])
    top = max(target["top"], bounds["top"])
    right = min(
        target["left"] + target["width"],
        bounds["left"] + bounds["width"],
    )
    bottom = min(
        target["top"] + target["height"],
        bounds["top"] + bounds["height"],
    )
    if right <= left or bottom <= top:
        return None, "skipped_fully_offscreen"
    return {
        "left": left,
        "top": top,
        "width": right - left,
        "height": bottom - top,
    }, ""


def _grab_mss_frame(
    monitor_dict: dict | None = None,
    *,
    title: str = "",
    hwnd: int | None = None,
    scope: str = "virtual_desktop",
    process_name: str = "",
    process_id: int | None = None,
) -> CapturedFrame | None:
    """Capture through mss and retain the exact desktop origin used."""
    if not PIL_OK:
        return None
    try:
        import mss
        with mss.mss() as sct:
            virtual_display = dict(sct.monitors[0])
            if monitor_dict:
                target, _status = _clip_capture_rect(
                    dict(monitor_dict), virtual_display,
                )
                if target is None:
                    return None
            else:
                target, _status = _clip_capture_rect(
                    virtual_display, virtual_display,
                )
                if target is None:
                    return None
            raw = sct.grab(target)
            raw_width, raw_height = raw.size
            if int(raw_width) <= 0 or int(raw_height) <= 0:
                return None
            image = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            if image.width <= 0 or image.height <= 0:
                return None
            return CapturedFrame(
                image=image,
                left=int(target.get("left", 0)),
                top=int(target.get("top", 0)),
                title=str(title or "")[:120],
                hwnd=int(hwnd) if hwnd is not None else None,
                scope=scope,
                process_name=str(process_name or "")[:120],
                process_id=int(process_id) if process_id is not None else None,
            )
    except Exception as exc:
        logger.debug(f"mss capture failed ({scope}): {type(exc).__name__}")
        return None


def _image_looks_uniform(image: "Image.Image | None") -> bool:
    """Detect the uniform frames returned by some failed desktop captures."""
    if image is None or image.width <= 0 or image.height <= 0:
        return True
    try:
        sample = image.convert("RGB")
        sample.thumbnail((128, 128))
        return all(high - low <= 2 for low, high in sample.getextrema())
    except Exception:
        return False


_WM_PRINT = 0x0317
_PRF_PRINT_ALL = 0x001F
_SMTO_BOUNDED = 0x0001 | 0x0002 | 0x0020
_PRINTWINDOW_TIMEOUT_MS = 750


def _send_wm_print_with_timeout(
    user32,
    hwnd: int,
    memory_dc,
    *,
    timeout_ms: int = _PRINTWINDOW_TIMEOUT_MS,
) -> bool:
    """Ask one external window to render, with a strict wait bound."""
    timeout = max(1, min(int(timeout_ms), 5_000))
    message_result = ctypes.c_size_t()
    send_message_timeout = user32.SendMessageTimeoutW
    send_message_timeout.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.wintypes.UINT,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
        ctypes.wintypes.UINT,
        ctypes.wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    send_message_timeout.restype = ctypes.wintypes.LPARAM
    return bool(send_message_timeout(
        hwnd,
        _WM_PRINT,
        memory_dc,
        _PRF_PRINT_ALL,
        _SMTO_BOUNDED,
        timeout,
        ctypes.byref(message_result),
    ))


def _grab_printwindow_frame(
    info: dict,
    *,
    crop_frame: CapturedFrame | None = None,
) -> CapturedFrame | None:
    """Render one approved visible Windows target with bounded ``WM_PRINT``.

    This primitive performs no target selection. Callers must retain the normal
    focused-window, exclusion, capability, generation, and revalidation policy.
    Minimized/unmapped targets are rejected again here as defense in depth.
    """
    if not IS_WINDOWS or not PIL_OK or ctypes is None:
        return None
    target, status = _validate_capture_target(info)
    if target is None or status:
        return None
    try:
        hwnd = int(info.get("hwnd") or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    width = int(target["width"])
    height = int(target["height"])
    if not hwnd or width * height > 20_000_000:
        return None

    class _BitmapInfoHeader(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.wintypes.DWORD),
            ("biWidth", ctypes.wintypes.LONG),
            ("biHeight", ctypes.wintypes.LONG),
            ("biPlanes", ctypes.wintypes.WORD),
            ("biBitCount", ctypes.wintypes.WORD),
            ("biCompression", ctypes.wintypes.DWORD),
            ("biSizeImage", ctypes.wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.wintypes.LONG),
            ("biYPelsPerMeter", ctypes.wintypes.LONG),
            ("biClrUsed", ctypes.wintypes.DWORD),
            ("biClrImportant", ctypes.wintypes.DWORD),
        ]

    class _BitmapInfo(ctypes.Structure):
        _fields_ = [
            ("bmiHeader", _BitmapInfoHeader),
            ("bmiColors", ctypes.wintypes.DWORD * 3),
        ]

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    window_dc = memory_dc = bitmap = previous = None
    try:
        user32.GetWindowDC.argtypes = [ctypes.wintypes.HWND]
        user32.GetWindowDC.restype = ctypes.wintypes.HDC
        gdi32.CreateCompatibleDC.argtypes = [ctypes.wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = ctypes.wintypes.HDC
        gdi32.CreateCompatibleBitmap.argtypes = [
            ctypes.wintypes.HDC, ctypes.c_int, ctypes.c_int,
        ]
        gdi32.CreateCompatibleBitmap.restype = ctypes.wintypes.HBITMAP
        gdi32.SelectObject.argtypes = [ctypes.wintypes.HDC, ctypes.wintypes.HANDLE]
        gdi32.SelectObject.restype = ctypes.wintypes.HANDLE

        window_dc = user32.GetWindowDC(hwnd)
        if not window_dc:
            return None
        memory_dc = gdi32.CreateCompatibleDC(window_dc)
        bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
        if not memory_dc or not bitmap:
            return None
        previous = gdi32.SelectObject(memory_dc, bitmap)
        if not _send_wm_print_with_timeout(user32, hwnd, memory_dc):
            return None

        info_header = _BitmapInfo()
        info_header.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
        info_header.bmiHeader.biWidth = width
        info_header.bmiHeader.biHeight = -height
        info_header.bmiHeader.biPlanes = 1
        info_header.bmiHeader.biBitCount = 32
        buffer = ctypes.create_string_buffer(width * height * 4)
        rows = gdi32.GetDIBits(
            memory_dc,
            bitmap,
            0,
            height,
            buffer,
            ctypes.byref(info_header),
            0,
        )
        if int(rows) != height:
            return None
        image = Image.frombuffer(
            "RGB", (width, height), buffer, "raw", "BGRX", 0, 1,
        ).copy()
        left = int(target["left"])
        top = int(target["top"])
        if crop_frame is not None:
            crop_left = int(crop_frame.left) - left
            crop_top = int(crop_frame.top) - top
            crop_right = crop_left + crop_frame.image.width
            crop_bottom = crop_top + crop_frame.image.height
            if (
                crop_left < 0 or crop_top < 0
                or crop_right > image.width or crop_bottom > image.height
            ):
                return None
            image = image.crop((crop_left, crop_top, crop_right, crop_bottom))
            left = crop_frame.left
            top = crop_frame.top
        return CapturedFrame(
            image=image,
            left=left,
            top=top,
            title=str(info.get("title") or "")[:120],
            hwnd=hwnd,
            scope="focused_window",
            process_name=str(info.get("process_name") or "")[:120],
            process_id=(
                int(info["process_id"])
                if info.get("process_id") is not None else None
            ),
        )
    except Exception as exc:
        logger.debug(f"PrintWindow capture failed: {type(exc).__name__}")
        return None
    finally:
        if memory_dc and previous:
            try:
                gdi32.SelectObject(memory_dc, previous)
            except Exception:
                pass
        if bitmap:
            try:
                gdi32.DeleteObject(bitmap)
            except Exception:
                pass
        if memory_dc:
            try:
                gdi32.DeleteDC(memory_dc)
            except Exception:
                pass
        if window_dc:
            try:
                user32.ReleaseDC(hwnd, window_dc)
            except Exception:
                pass


def _grab_focused_windows_frame(
    info: dict,
    *,
    allow_printwindow: bool,
) -> CapturedFrame | None:
    """Capture the approved focused target, using PrintWindow only for blank MSS."""
    target, status = _validate_capture_target(info)
    if target is None or status:
        return None
    frame = _grab_mss_frame(
        target,
        title=info.get("title", ""),
        hwnd=info.get("hwnd"),
        scope="focused_window",
        process_name=info.get("process_name", ""),
        process_id=info.get("process_id"),
    )
    if (
        not allow_printwindow
        or frame is None
        or not _image_looks_uniform(frame.image)
    ):
        return frame
    rendered = _grab_printwindow_frame(info, crop_frame=frame)
    return rendered if rendered is not None else frame


def _grab_mss(monitor_dict: dict | None = None) -> "Image.Image | None":
    """Backward-compatible image-only wrapper used by older callers/tests."""
    frame = _grab_mss_frame(monitor_dict)
    return frame.image if frame is not None else None


def _grab_imagegrab(bbox: tuple | None = None) -> "Image.Image | None":
    if not IMAGEGRAB_OK:
        return None
    if bbox is not None:
        try:
            left, top, right, bottom = (int(value) for value in bbox)
        except (TypeError, ValueError):
            return None
        if right <= left or bottom <= top:
            return None
    try:
        img = ImageGrab.grab(bbox=bbox)
        return img if img and img.width > 0 and img.height > 0 else None
    except Exception:
        return None


def _grab_pyautogui() -> "Image.Image | None":
    if not PYAUTOGUI_OK:
        return None
    try:
        image = pyautogui.screenshot()
        return image if image and image.width > 0 and image.height > 0 else None
    except Exception:
        return None


def _grab_scrot() -> "Image.Image | None":
    if not PIL_OK or not _cmd_exists("scrot"):
        return None
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        result = subprocess.run(["scrot", "--silent", tmp], capture_output=True, timeout=10)
        if result.returncode != 0:
            return None
        image = Image.open(tmp).copy()
        return image if image.width > 0 and image.height > 0 else None
    except Exception:
        return None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _grab_temp_png(grabber_fn) -> "Image.Image | None":
    """Run a grabber that writes to a temp path; always clean up the file."""
    if not PIL_OK:
        return None
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        if not grabber_fn(tmp):
            return None
        if not Path(tmp).is_file() or Path(tmp).stat().st_size <= 0:
            return None
        image = Image.open(tmp).copy()
        return image if image.width > 0 and image.height > 0 else None
    except Exception:
        return None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _grab_grim() -> "Image.Image | None":
    if not PIL_OK or not _cmd_exists("grim"):
        return None
    return _grab_temp_png(lambda p: subprocess.run(["grim", p], capture_output=True, timeout=10).returncode == 0)


def _grab_spectacle() -> "Image.Image | None":
    if not PIL_OK or not _cmd_exists("spectacle"):
        return None
    return _grab_temp_png(
        lambda p: subprocess.run(
            ["spectacle", "--background", "--nonotify", "--fullscreen", "--output", p],
            capture_output=True, timeout=15,
        ).returncode == 0 and Path(p).stat().st_size > 0
    )


def _grab_gnome_screenshot() -> "Image.Image | None":
    if not PIL_OK or not _cmd_exists("gnome-screenshot"):
        return None
    return _grab_temp_png(
        lambda p: subprocess.run(["gnome-screenshot", "-f", p], capture_output=True, timeout=10).returncode == 0
    )


def _grab_screencapture() -> "Image.Image | None":
    if not PIL_OK:
        return None
    return _grab_temp_png(
        lambda p: subprocess.run(["screencapture", "-x", p], capture_output=True, timeout=10).returncode == 0
    )


# ── ScreenReader ──────────────────────────────────────────────────────────────

class ScreenReader:
    """Phase 3 precision screen reader.

    Public attributes set after each capture_text() call:
        last_angry_keywords      list[str]          — legacy flat-keyword hits
        last_pattern_matches     list[PatternMatch]  — regex pattern hits
        last_word_positions      list[dict]          — word coords in desktop space
        last_active_window_title str                 — title of captured window
    """

    def __init__(self, own_tk_root=None):
        """
        own_tk_root: pass CompanionApp.root so ScreenReader can skip Agetha's
        own window when using focused capture.
        """
        self._system    = platform.system()
        self._own_root  = own_tk_root
        self._own_hwnd: int | None = None
        self._capture_lock = threading.RLock()
        self._standard_scan_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._stopped = False
        self._clock = time.monotonic
        self._app_mapped = True
        self._app_minimized = False
        self._app_closing = False

        # Capture origin — used to translate Tesseract pixel coords → screen coords
        self._capture_left: int = 0
        self._capture_top:  int = 0

        # Public state (consumed by main.py after each scan)
        self.last_angry_keywords:     list[str]          = []
        self.last_pattern_matches:    list[PatternMatch]  = []
        self.last_new_pattern_events: list[PatternMatch]  = []
        self.last_word_positions:     list[dict]          = []
        self.last_active_window_title: str               = ""
        self.last_capture_metadata: CapturedFrame | None = None
        self.last_monitor_status: str = "initializing"

        # Tesseract path (Windows)
        from agetha.app_config import get_settings
        _cfg = get_settings()
        self._ocr_max_dimension = _cfg.ocr_max_dimension
        self._ocr_preprocessing = _cfg.ocr_preprocessing
        self._ocr_languages = _cfg.ocr_languages
        self._effective_ocr_languages = self._ocr_languages
        self._ocr_psm = _cfg.ocr_psm
        self._ocr_min_word_confidence = _cfg.ocr_min_word_confidence
        self._ocr_min_pattern_confidence = _cfg.ocr_min_pattern_confidence
        self._pattern_cooldown_seconds = _cfg.ocr_pattern_cooldown_seconds
        self._pattern_confirm_scans = _cfg.ocr_pattern_confirm_scans
        self._low_confidence_confirm_scans = _cfg.ocr_low_confidence_confirm_scans
        self._pattern_clear_scans = _cfg.ocr_pattern_clear_scans
        self._redact_sensitive_context = _cfg.ocr_redact_sensitive_text
        self._enable_printwindow_fallback = bool(
            getattr(_cfg, "enable_printwindow_fallback", True)
        )
        self._excluded_apps = parse_csv_values(_cfg.ocr_excluded_apps)
        self._title_exclusions = compile_title_exclusions(
            _cfg.ocr_excluded_title_patterns,
        )
        self._change_detector = ScreenChangeDetector(
            enabled=_cfg.ocr_change_detection,
            threshold=_cfg.ocr_change_threshold,
            force_refresh_seconds=_cfg.ocr_force_refresh_seconds,
            state_expiry_seconds=_cfg.ocr_state_expiry_seconds,
        )
        self._event_tracker = PatternEventTracker()
        self._standard_ocr_backend = TesseractOCRBackend(
            pytesseract if TESSERACT_OK else None,
        )
        self._deep_backend_name = _cfg.deep_ocr_backend
        self._deep_ocr_backend = None
        self._deep_ocr_options = {
            "server_url": _cfg.unlimited_ocr_server_url,
            "model": _cfg.unlimited_ocr_model,
            "timeout_seconds": _cfg.unlimited_ocr_timeout_seconds,
            "allow_remote": _cfg.unlimited_ocr_allow_remote,
            "max_output_chars": _cfg.deep_ocr_max_output_chars,
            "api_key": _cfg.unlimited_ocr_api_key,
        }

        if self._system == "Windows" and TESSERACT_OK and _cfg.enable_screen_reader:
            custom_tess = _cfg.tesseract_path
            if custom_tess and Path(custom_tess).is_file():
                pytesseract.pytesseract.tesseract_cmd = custom_tess
            else:
                tess = _find_tesseract_windows()
                if tess:
                    pytesseract.pytesseract.tesseract_cmd = tess
                else:
                    logger.warning("Tesseract was not found in standard Windows paths")

        self._backend_name = "lazy"
        self._backend_fn = None
        self._explicit_backend_name = "lazy"
        self._explicit_backend_fn = None
        self._disabled_backends: set[str] = set()
        self._capture_warning_emitted = False
        self._linux_capabilities = None
        if self._system == "Linux":
            self._linux_capabilities = detect_linux_desktop(
                pyautogui_ok=PYAUTOGUI_OK,
                imagegrab_ok=IMAGEGRAB_OK,
            )
            logger.info(self._linux_capabilities.diagnostic_summary())
        self._backend_candidates = self._ordered_backends(automatic=True)
        self._explicit_backend_candidates = self._ordered_backends(automatic=False)
        display_available = _has_display()
        if self._linux_capabilities is not None:
            automatic_policy = self._linux_capabilities.automatic_ocr_supported
            explicit_policy = self._linux_capabilities.explicit_capture_supported
        else:
            automatic_policy = display_available
            explicit_policy = display_available
        self._automatic_capture_available = bool(
            _cfg.enable_screen_reader and display_available and automatic_policy
        )
        self._explicit_capture_available = bool(
            _cfg.enable_screen_reader and display_available and explicit_policy
        )
        self._capture_available = self._automatic_capture_available
        self._available = self._automatic_capture_available and TESSERACT_OK
        self._tesseract_checked = False
        self._tesseract_ready = False

        if not self._available:
            reasons = []
            if not _cfg.enable_screen_reader:
                reasons.append("ENABLE_SCREEN_READER=no")
            if not TESSERACT_OK:
                reasons.append("pytesseract package missing")
            if not _has_display():
                reasons.append("no display")
            if self._system == "Linux" and self._linux_capabilities is not None:
                if self._linux_capabilities.session_type == "wayland":
                    reasons.append("automatic Wayland capture unavailable")
                    self.last_monitor_status = "skipped_wayland_capture_restricted"
                else:
                    reasons.append("capture backend unavailable")
                    self.last_monitor_status = "skipped_capture_unavailable"
            else:
                self.last_monitor_status = "capture_disabled"
            logger.warning(f"Screen capture disabled: {', '.join(reasons)}")
        else:
            logger.info("[ScreenReader] Phase 3 ready — backend: lazy (first capture)")

        _ensure_custom_patterns()
        # Resolve Tk's native handle while construction is still on its UI thread.
        if self._own_root is not None:
            self._get_own_hwnd()

    def get_active_window_title(self, skip_hwnd: int | None = None) -> str:
        """Lightweight foreground title (no OCR)."""
        try:
            if self._system == "Windows":
                win = _get_foreground_window_info(skip_hwnd=skip_hwnd)
                return (win or {}).get("title", "") or ""
            if self._system == "Linux":
                win = _get_foreground_window_info_linux(skip_hwnd=skip_hwnd)
                return (win or {}).get("title", "") or ""
        except Exception:
            pass
        return getattr(self, "last_active_window_title", "") or ""

    def _ensure_tesseract(self) -> bool:
        """Validate the executable and configured languages once, at first OCR."""
        if not hasattr(self, "_tesseract_checked"):
            return True  # compatibility for lightweight test doubles
        if self._tesseract_checked:
            return self._tesseract_ready
        self._tesseract_checked = True
        if not TESSERACT_OK:
            self.last_monitor_status = "ocr_package_unavailable"
            return False
        try:
            pytesseract.get_tesseract_version()
            requested = [
                item for item in self._ocr_languages.split("+") if item
            ] or ["eng"]
            try:
                installed = set(pytesseract.get_languages(config=""))
            except Exception:
                self._effective_ocr_languages = "+".join(requested)
                self._tesseract_ready = True
                logger.warning(
                    "Tesseract language metadata unavailable; using configured languages"
                )
                return True
            available = [item for item in requested if item in installed]
            if not available and "eng" in installed:
                available = ["eng"]
            if not available:
                self.last_monitor_status = "ocr_language_unavailable"
                logger.warning("No requested Tesseract OCR language is installed")
                return False
            self._effective_ocr_languages = "+".join(available)
            missing = sorted(set(requested) - set(available))
            if missing:
                logger.warning("Some configured Tesseract languages are unavailable")
            self._tesseract_ready = True
            return True
        except Exception as exc:
            self.last_monitor_status = "ocr_executable_unavailable"
            logger.warning(
                f"Tesseract executable unavailable: {type(exc).__name__}"
            )
            return False

    @staticmethod
    def _as_frame(capture, *, backend_name: str) -> CapturedFrame | None:
        if capture is None:
            return None
        if isinstance(capture, CapturedFrame):
            return capture
        return CapturedFrame(
            image=capture,
            left=0,
            top=0,
            title="",
            hwnd=None,
            scope="primary_monitor",
            process_name="",
        )

    def _capture_with_backend(self, *, automatic: bool = True) -> CapturedFrame | None:
        """Select a screenshot backend without throwing away its first frame."""
        prefix = "_backend" if automatic else "_explicit_backend"
        pending_attr = f"{prefix}_pending_frame"
        pending = getattr(self, pending_attr, None)
        if pending is None and automatic:
            pending = getattr(self, "_pending_backend_frame", None)
            self._pending_backend_frame = None
        if pending is not None:
            setattr(self, pending_attr, None)
            return pending
        disabled_backends = getattr(self, "_disabled_backends", None)
        if disabled_backends is None:
            disabled_backends = set()
            self._disabled_backends = disabled_backends
        backend_fn = getattr(self, f"{prefix}_fn", None)
        backend_name = getattr(self, f"{prefix}_name", "lazy")
        if backend_fn is not None:
            try:
                frame = self._as_frame(backend_fn(), backend_name=backend_name)
            except Exception as exc:
                frame = None
                logger.debug(
                    f"Backend {backend_name} failed: {type(exc).__name__}"
                )
            if frame is not None:
                return frame
            disabled_backends.add(backend_name)
            setattr(self, f"{prefix}_fn", None)
            setattr(self, f"{prefix}_name", "unavailable")
        candidates = (
            self._backend_candidates if automatic
            else getattr(self, "_explicit_backend_candidates", [])
        )
        for name, fn in candidates:
            if name in disabled_backends:
                continue
            try:
                frame = self._as_frame(fn(), backend_name=name)
            except Exception as exc:
                frame = None
                logger.debug(f"Backend {name} failed: {type(exc).__name__}")
            if frame is None:
                disabled_backends.add(name)
                continue
            setattr(self, f"{prefix}_name", name)
            setattr(self, f"{prefix}_fn", fn)
            logger.info(f"[ScreenReader] Selected backend: {name}")
            return frame
        self.last_monitor_status = "capture_backend_unavailable"
        if not getattr(self, "_capture_warning_emitted", False):
            self._capture_warning_emitted = True
            logger.warning("[ScreenReader] capture backend unavailable for this session")
        return None

    def _ensure_backend(self) -> bool:
        """Compatibility helper that preserves the frame used during probing."""
        if self._backend_fn is not None:
            return True
        frame = self._capture_with_backend()
        self._pending_backend_frame = frame
        return frame is not None

    # ── Backend selection ─────────────────────────────────────────────────────

    def _ordered_backends(self, *, automatic: bool = True) -> list[tuple]:
        # mss is always first on Windows — it supports partial capture dicts
        head = [("mss", lambda: _grab_mss_frame(scope="virtual_desktop"))]
        if self._system == "Windows":
            return head
        elif self._system == "Darwin":
            return head + [("screencapture", _grab_screencapture), ("pyautogui", _grab_pyautogui)]
        else:
            if _is_wayland():
                if automatic:
                    return []
                return [("spectacle", _grab_spectacle), ("grim", _grab_grim)]
            return head + [
                ("imagegrab", _grab_imagegrab),
                ("scrot", _grab_scrot),
                ("pyautogui", _grab_pyautogui),
            ]

    # ── 1. Own-window HWND cache ──────────────────────────────────────────────

    def _get_own_hwnd(self) -> int | None:
        """Cache and return Agetha's own top-level window HWND."""
        if self._own_hwnd:
            return self._own_hwnd
        if threading.current_thread() is not threading.main_thread():
            return None
        if self._own_root:
            if IS_WINDOWS:
                try:
                    canvas_id = self._own_root.winfo_id()
                    # GA_ROOT = 2: walk up to the overrideredirect top-level
                    top = ctypes.windll.user32.GetAncestor(canvas_id, 2)
                    self._own_hwnd = top or canvas_id
                except Exception:
                    pass
            elif IS_LINUX:
                try:
                    self._own_hwnd = self._own_root.winfo_id()
                except Exception:
                    pass
        return self._own_hwnd

    def cache_own_window_handle(self) -> int | None:
        """Resolve the native app handle while called from Tk's main thread."""
        return self._get_own_hwnd()

    @staticmethod
    def _normalized_capture_target(info: dict | None) -> dict | None:
        if not info:
            return None
        geometry, _status = _validate_capture_target(info)
        if geometry is None:
            return None
        try:
            target = {
                **geometry,
                "title": str(info.get("title", ""))[:60],
                "hwnd": int(info["hwnd"]) if info.get("hwnd") is not None else None,
                "process_name": str(info.get("process_name", ""))[:120],
                "process_id": (
                    int(info["process_id"])
                    if info.get("process_id") is not None else None
                ),
                "mapped": info.get("mapped"),
                "minimized": bool(info.get("minimized", False)),
            }
        except (KeyError, TypeError, ValueError):
            return None
        if target["width"] <= 20 or target["height"] <= 20:
            return None
        return target

    def _resolve_capture_target(self, target: dict | None) -> dict | None:
        normalized = self._normalized_capture_target(target)
        if normalized is None or normalized["hwnd"] is None:
            return None
        own_hwnd = self._get_own_hwnd()
        if own_hwnd is None or normalized["hwnd"] == own_hwnd:
            return None
        if self._system == "Windows":
            refreshed = self._normalized_capture_target(
                _get_window_info(normalized["hwnd"], skip_hwnd=own_hwnd),
            )
            if (
                refreshed is None
                or normalized["process_id"] is None
                or refreshed.get("process_id") != normalized["process_id"]
            ):
                return None
            return refreshed
        if self._system == "Linux":
            refreshed = self._normalized_capture_target(
                _get_linux_window_info(normalized["hwnd"], skip_hwnd=own_hwnd),
            )
            if (
                refreshed is None
                or normalized["process_id"] is None
                or refreshed.get("process_id") != normalized["process_id"]
                or refreshed["process_name"] != normalized["process_name"]
                or refreshed["title"] != normalized["title"]
            ):
                return None
            return refreshed
        return normalized

    def preserve_external_target(self) -> dict | None:
        """Snapshot an external focused target before a confirmation takes focus."""
        own_hwnd = self._get_own_hwnd()
        if own_hwnd is None:
            return None
        current = self._normalized_capture_target(self._foreground_info())
        if current is not None and current["hwnd"] != own_hwnd:
            return self._resolve_capture_target(current)

        state_lock = getattr(self, "_state_lock", None)
        if state_lock is not None:
            with state_lock:
                previous = getattr(self, "last_capture_metadata", None)
        else:
            previous = getattr(self, "last_capture_metadata", None)
        if (
            previous is None
            or previous.scope != "focused_window"
            or previous.hwnd is None
            or previous.hwnd == own_hwnd
        ):
            return None
        width, height = previous.image.size
        return self._resolve_capture_target({
            "left": previous.left,
            "top": previous.top,
            "width": width,
            "height": height,
            "title": previous.title,
            "hwnd": previous.hwnd,
            "process_name": previous.process_name,
            "process_id": previous.process_id,
        })

    # ── 1 + 4. Focused capture ────────────────────────────────────────────────

    def _foreground_info(self) -> dict | None:
        if self._system == "Windows":
            return _get_foreground_window_info(skip_hwnd=None)
        if self._system == "Linux":
            return _get_foreground_window_info_linux(skip_hwnd=None)
        return None

    def _capture_frame(
        self,
        *,
        focused_only: bool = True,
        automatic: bool = True,
        capture_target: dict | None = None,
    ) -> CapturedFrame | None:
        """Return an immutable image/origin/title snapshot for one operation."""
        with self._capture_lock:
            if getattr(self, "_app_closing", False):
                self._clear_stale_capture_state("capture_disabled")
                return None
            if automatic and getattr(self, "_app_minimized", False):
                self._clear_stale_capture_state("skipped_minimized")
                return None
            if automatic and not getattr(self, "_app_mapped", True):
                self._clear_stale_capture_state("skipped_unmapped")
                return None
            capture_available = (
                getattr(
                    self, "_automatic_capture_available",
                    getattr(self, "_capture_available", False),
                )
                if automatic else
                getattr(
                    self, "_explicit_capture_available",
                    getattr(self, "_capture_available", False),
                )
            )
            if self._stopped or not capture_available:
                if automatic and self._system == "Linux" and _is_wayland():
                    status = "skipped_wayland_capture_restricted"
                else:
                    status = "skipped_capture_unavailable"
                self._clear_stale_capture_state(status)
                return None
            strict_target = focused_only and capture_target is not None
            if strict_target:
                win = self._resolve_capture_target(capture_target)
                if win is None:
                    self.last_monitor_status = "capture_target_unavailable"
                    return None
            else:
                win = self._foreground_info() if focused_only or automatic else None
            if win is not None:
                geometry, invalid_status = _validate_capture_target(win)
                if geometry is None:
                    self._clear_stale_capture_state(invalid_status)
                    return None
                win = {**win, **geometry}
                hwnd = win.get("hwnd")
                if hwnd is not None and hwnd == self._get_own_hwnd():
                    self._clear_stale_capture_state("skipped_own_window")
                    return None
                if automatic and is_capture_excluded(
                    title=win.get("title", ""),
                    process_name=win.get("process_name", ""),
                    excluded_apps=self._excluded_apps,
                    title_exclusions=self._title_exclusions,
                ):
                    self._clear_stale_capture_state("skipped_excluded_window")
                    return None
            if focused_only and win is not None:
                hwnd = win.get("hwnd")
                if self._system == "Windows":
                    frame = _grab_focused_windows_frame(
                        win,
                        allow_printwindow=bool(getattr(
                            self, "_enable_printwindow_fallback", True,
                        )),
                    )
                else:
                    frame = _grab_mss_frame(
                        {
                            "left": int(win["left"]),
                            "top": int(win["top"]),
                            "width": int(win["width"]),
                            "height": int(win["height"]),
                        },
                        title=win.get("title", ""),
                        hwnd=hwnd,
                        scope="focused_window",
                        process_name=win.get("process_name", ""),
                        process_id=win.get("process_id"),
                    )
                if frame is not None:
                    self.last_monitor_status = "captured_focused_window"
                    return frame
                if strict_target:
                    self.last_monitor_status = "capture_target_failed"
                    return None
                logger.debug("Focused capture failed; trying a full-display backend")
                if IS_WINDOWS and hwnd is not None:
                    monitor = _find_monitor_for_window(int(hwnd))
                    if monitor is not None:
                        frame = _grab_mss_frame(
                            monitor,
                            title=win.get("title", ""),
                            hwnd=hwnd,
                            scope="active_monitor",
                            process_name=win.get("process_name", ""),
                            process_id=win.get("process_id"),
                        )
                        if frame is not None:
                            self.last_monitor_status = "captured_active_monitor"
                            return frame
            frame = self._capture_with_backend(automatic=automatic)
            if frame is not None:
                self.last_monitor_status = f"captured_{frame.scope}"
                return frame
            if automatic:
                self._clear_stale_capture_state("capture_failed")
            else:
                self.last_monitor_status = "capture_failed"
            return None

    def _clear_stale_capture_state(self, status: str) -> None:
        """Fail closed so skipped captures cannot reuse another window's OCR."""
        state_lock = getattr(self, "_state_lock", None)
        if state_lock is None:
            self.last_monitor_status = status
            self.last_active_window_title = ""
            self.last_capture_metadata = None
            self.last_word_positions = []
            self.last_angry_keywords = []
            self.last_pattern_matches = []
            self.last_new_pattern_events = []
            return
        with state_lock:
            self.last_monitor_status = status
            self.last_active_window_title = ""
            self.last_capture_metadata = None
            self.last_word_positions = []
            self.last_angry_keywords = []
            self.last_pattern_matches = []
            self.last_new_pattern_events = []

    @property
    def automatic_capture_supported(self) -> bool:
        return bool(getattr(self, "_automatic_capture_available", False))

    def set_app_window_state(
        self,
        *,
        mapped: bool,
        minimized: bool,
        closing: bool = False,
    ) -> None:
        """Receive Tk window state from the UI thread without touching widgets."""
        with self._state_lock:
            self._app_mapped = bool(mapped)
            self._app_minimized = bool(minimized)
            self._app_closing = bool(closing)

    def _commit_capture_metadata(self, frame: CapturedFrame) -> None:
        with self._state_lock:
            self._capture_left = frame.left
            self._capture_top = frame.top
            self.last_active_window_title = frame.title
            self.last_capture_metadata = frame

    def capture_image(self, focused_only: bool = True) -> "Image.Image | None":
        """Compatibility image API; metadata is committed only after success."""
        frame = self._capture_frame(
            focused_only=bool(focused_only), automatic=False,
        )
        if frame is None:
            return None
        self._commit_capture_metadata(frame)
        return frame.image

    def _focused_target_is_current(self, frame: CapturedFrame) -> bool:
        if frame.scope not in {"focused_window", "active_monitor"} or frame.hwnd is None:
            return True
        current = self._foreground_info()
        return current is not None and current.get("hwnd") == frame.hwnd

    def _capture_target_is_current(
        self,
        frame: CapturedFrame,
        capture_target: dict,
    ) -> bool:
        """Revalidate an exact passive target without requiring foreground."""

        refreshed = self._resolve_capture_target(capture_target)
        if refreshed is None or frame.hwnd is None:
            return False
        width, height = frame.image.size
        return bool(
            refreshed.get("hwnd") == frame.hwnd
            and refreshed.get("process_id") == frame.process_id
            and str(refreshed.get("process_name", "")).casefold()
            == str(frame.process_name or "").casefold()
            and int(refreshed.get("left", 0)) == frame.left
            and int(refreshed.get("top", 0)) == frame.top
            and int(refreshed.get("width", 0)) == width
            and int(refreshed.get("height", 0)) == height
        )

    def _selected_psm(self, frame: CapturedFrame) -> int:
        configured = getattr(self, "_ocr_psm", "auto")
        if configured != "auto":
            return int(configured)
        context = f"{frame.title} {frame.process_name}".casefold()
        editor_tokens = (
            "terminal", "powershell", "command prompt", "cmd.exe", "console",
            "visual studio", "code", "pycharm", "intellij", "roblox studio",
        )
        if any(token in context for token in editor_tokens):
            return 6
        if frame.scope in {"virtual_desktop", "primary_monitor", "active_monitor"}:
            return 11
        return 3

    def capture_text(
        self,
        max_chars: int = 3000,
        focused_only: bool = True,
        *,
        force_refresh: bool = False,
        capture_target: dict | None = None,
    ) -> str:
        """Capture and OCR one stable frame, then atomically publish its state.

        ``force_refresh`` bypasses only the unchanged-frame OCR cache.  A
        ``capture_target`` is an already preserved external window and is
        revalidated before and after OCR without requiring it to steal focus.
        Normal monitoring keeps its existing behavior when both are omitted.
        """
        scan_lock = getattr(self, "_standard_scan_lock", None)
        if scan_lock is None:
            scan_lock = threading.Lock()
            self._standard_scan_lock = scan_lock
        with scan_lock:
            state_lock = getattr(self, "_state_lock", None)
            if state_lock is None:
                state_lock = threading.RLock()
                self._state_lock = state_lock
            with state_lock:
                self.last_new_pattern_events = []
            if not getattr(self, "_available", False):
                status = getattr(self, "last_monitor_status", "capture_disabled")
                self._clear_stale_capture_state(status)
                return ""
            if not self._ensure_tesseract():
                return ""
            try:
                modern_capture = hasattr(self, "_capture_lock")
                if modern_capture:
                    frame = self._capture_frame(
                        focused_only=bool(focused_only),
                        automatic=True,
                        capture_target=capture_target,
                    )
                else:
                    if not self._ensure_backend():
                        return ""
                    screenshot = self.capture_image(focused_only=focused_only)
                    frame = None if screenshot is None else CapturedFrame(
                        image=screenshot,
                        left=int(getattr(self, "_capture_left", 0)),
                        top=int(getattr(self, "_capture_top", 0)),
                        title=str(getattr(self, "last_active_window_title", "")),
                        hwnd=None,
                        scope="focused_window" if focused_only else "primary_monitor",
                    )
                if frame is None:
                    return ""

                now = getattr(self, "_clock", time.monotonic)()
                detector = getattr(self, "_change_detector", None)
                thumbnail = None
                cached_state = None
                if detector is not None:
                    should_scan, reason, thumbnail, cached_state = detector.should_scan(
                        frame, now,
                    )
                    if (
                        not force_refresh
                        and not should_scan
                        and cached_state is not None
                    ):
                        with self._state_lock:
                            self.last_new_pattern_events = []
                            self.last_monitor_status = reason
                        return cached_state.last_text

                processed = preprocess_ocr_image(
                    frame.image,
                    max_dimension=int(getattr(self, "_ocr_max_dimension", 2560)),
                    mode=getattr(self, "_ocr_preprocessing", "basic"),
                )
                ocr_result = self._standard_ocr_backend.analyze(
                    processed.image,
                    capture_left=frame.left,
                    capture_top=frame.top,
                    processing_scale_x=processed.scale_x,
                    processing_scale_y=processed.scale_y,
                    max_chars=max_chars,
                    min_word_confidence=float(
                        getattr(self, "_ocr_min_word_confidence", 30.0)
                    ),
                    languages=getattr(self, "_effective_ocr_languages", "eng"),
                    psm=(self._selected_psm(frame) if modern_capture else 3),
                )
                result = "\n".join(
                    line.strip() for line in ocr_result.text.splitlines()
                    if line.strip()
                )[:max_chars]

                if modern_capture and self._stopped:
                    self.last_monitor_status = "discarded_during_shutdown"
                    return ""
                if modern_capture:
                    if capture_target is not None:
                        if not self._capture_target_is_current(frame, capture_target):
                            self._clear_stale_capture_state("discarded_stale_target")
                            logger.debug(
                                "Discarded OCR result because the capture target changed"
                            )
                            return ""
                    elif not self._focused_target_is_current(frame):
                        self.last_monitor_status = "discarded_stale_window"
                        logger.debug(
                            "Discarded OCR result because the focused window changed"
                        )
                        return ""

                matches = _scan_patterns(
                    result,
                    ocr_result.lines,
                    window_title=frame.title,
                    process_name=frame.process_name,
                    minimum_confidence=float(
                        getattr(self, "_ocr_min_pattern_confidence", 0.0)
                    ),
                )
                low = result.casefold()
                angry_keywords = [kw for kw in ANGRY_KEYWORDS if kw in low]
                positions = [{
                    "text": word.text,
                    "screen_x": word.x,
                    "screen_y": word.y,
                    "w": word.width,
                    "h": word.height,
                    "conf": int(word.confidence or 0),
                } for word in ocr_result.words]

                tracker = getattr(self, "_event_tracker", None)
                if tracker is None:
                    new_events = list(matches)
                else:
                    minimum = float(self._ocr_min_pattern_confidence)
                    new_events = tracker.update(
                        matches,
                        window_key=frame.key,
                        now=now,
                        cooldown_seconds=self._pattern_cooldown_seconds,
                        confirm_scans=self._pattern_confirm_scans,
                        low_confidence_confirm_scans=(
                            self._low_confidence_confirm_scans
                        ),
                        clear_scans=self._pattern_clear_scans,
                        minimum_confidence=min(100.0, minimum + 20.0),
                    )

                if (
                    detector is not None
                    and thumbnail is not None
                    and (result or cached_state is None)
                ):
                    detector.record(
                        frame,
                        thumbnail=thumbnail,
                        text=result,
                        text_hash=hashlib.sha256(
                            result.encode("utf-8", errors="replace")
                        ).hexdigest(),
                        now=now,
                    )

                with state_lock:
                    self._capture_left = frame.left
                    self._capture_top = frame.top
                    self.last_active_window_title = frame.title
                    self.last_capture_metadata = frame
                    self.last_angry_keywords = angry_keywords
                    self.last_pattern_matches = matches
                    self.last_new_pattern_events = new_events
                    self.last_word_positions = positions
                    self.last_monitor_status = (
                        "ocr_complete" if result else "ocr_empty"
                    )

                logger.debug(
                    f"OCR complete: {len(result)} chars, {len(positions)} words, "
                    f"{len(matches)} current patterns, {len(new_events)} new events"
                )
                return result
            except Exception as exc:
                self.last_monitor_status = "ocr_failed"
                logger.warning(f"OCR failed safely: {type(exc).__name__}")
                return ""

    def redact_for_external_context(self, value: str) -> str:
        if not getattr(self, "_redact_sensitive_context", True):
            return str(value or "")
        return redact_sensitive_text(value)

    def _deep_error(self, code: str, message: str) -> OCRResult:
        return OCRResult(
            text=message,
            words=[],
            backend="unlimited_ocr",
            metadata={"status": "error", "error": code},
        )

    def _get_deep_backend(self):
        if self._deep_ocr_backend is None and self._deep_backend_name == "unlimited_ocr":
            from agetha.platform.ocr_backends.unlimited_ocr_backend import UnlimitedOCRBackend
            self._deep_ocr_backend = UnlimitedOCRBackend(**self._deep_ocr_options)
        return self._deep_ocr_backend

    def capture_deep_text(
        self,
        focused_only: bool = True,
        prompt: str = "<image>document parsing.",
        capture_target: dict | None = None,
        require_target: bool = False,
    ) -> OCRResult:
        """Run the configured deep backend only for an explicit caller request."""
        if getattr(self, "_deep_backend_name", "none") != "unlimited_ocr":
            return self._deep_error(
                "disabled",
                "Deep OCR is not configured. Standard Tesseract OCR is still available. "
                "Set DEEP_OCR_BACKEND = unlimited_ocr and configure "
                "UNLIMITED_OCR_SERVER_URL to enable it.",
            )

        try:
            backend = self._get_deep_backend()
        except Exception:
            backend = None
        if backend is None:
            return self._deep_error(
                "unavailable",
                "Deep OCR is unavailable. Standard Tesseract OCR is still available.",
            )
        problem = backend.configuration_error()
        if problem:
            return self._deep_error(*problem)
        if focused_only and require_target and capture_target is None:
            return self._deep_error(
                "target_unavailable",
                "The previously focused window is no longer available for deep OCR.",
            )

        # Deep OCR owns only its immutable frame and never mutates standard state.
        try:
            if hasattr(self, "_capture_lock"):
                frame = self._capture_frame(
                    focused_only=bool(focused_only), automatic=False,
                    capture_target=capture_target,
                )
            else:
                legacy_state = (
                    getattr(self, "_capture_left", 0),
                    getattr(self, "_capture_top", 0),
                    getattr(self, "last_active_window_title", ""),
                    getattr(self, "last_angry_keywords", []),
                    getattr(self, "last_pattern_matches", []),
                    getattr(self, "last_word_positions", []),
                )
                screenshot = self.capture_image(focused_only=bool(focused_only))
                frame = None if screenshot is None else CapturedFrame(
                    screenshot,
                    int(getattr(self, "_capture_left", 0)),
                    int(getattr(self, "_capture_top", 0)),
                    str(getattr(self, "last_active_window_title", "")),
                    None,
                    "focused_window" if focused_only else "primary_monitor",
                )
                (
                    self._capture_left,
                    self._capture_top,
                    self.last_active_window_title,
                    self.last_angry_keywords,
                    self.last_pattern_matches,
                    self.last_word_positions,
                ) = legacy_state
            if frame is None:
                return self._deep_error("capture_failed", "Deep OCR could not capture the screen.")
            result = backend.analyze(
                frame.image,
                prompt=str(prompt or "<image>document parsing.")[:2000],
            )
            result.metadata = dict(result.metadata)
            result.metadata.setdefault("focused_only", bool(focused_only))
            result.metadata.setdefault("capture_scope", frame.scope)
            result.metadata.setdefault("capture_left", frame.left)
            result.metadata.setdefault("capture_top", frame.top)
            if frame.title:
                result.metadata.setdefault("active_window_title", frame.title)
            return result
        except Exception:
            return self._deep_error(
                "request_failed",
                "Deep OCR failed safely. Standard Tesseract OCR is still working.",
            )

    def stop(self) -> None:
        """Release the optional reusable HTTP session during app shutdown."""
        if getattr(self, "_stopped", False):
            return
        self._stopped = True
        backend = getattr(self, "_deep_ocr_backend", None)
        if backend is not None:
            try:
                backend.close()
            except Exception:
                pass

    @property
    def has_angry_trigger(self) -> bool:
        """True when angry keywords OR high-severity patterns were detected."""
        if self.last_angry_keywords:
            return True
        return any(m.mood in ("angry", "manic") for m in self.last_pattern_matches)

    @property
    def has_pattern_match(self) -> bool:
        return len(self.last_pattern_matches) > 0

    def dominant_mood(self) -> str:
        """Highest-priority mood from detected patterns, or 'neutral'."""
        for mood in ("manic", "angry", "paranoid", "thinking"):
            if any(m.mood == mood for m in self.last_pattern_matches):
                return mood
        return "neutral"
