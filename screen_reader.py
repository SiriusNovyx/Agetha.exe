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

# Conditional Win32 imports for Linux safety
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

try:
    import pyautogui
    PYAUTOGUI_OK = True
except ImportError:
    PYAUTOGUI_OK = False


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
               )),

    PatternDef("security_access",
               "angry",
               "Access denied",
               re.compile(r"access denied|403 Forbidden|401 Unauthorized", re.I)),

    PatternDef("virus_alert",
               "paranoid",
               "Security threat detected",
               re.compile(
                   r"virus detected|malware detected|threat detected"
                   r"|quarantined|real-time protection blocked",
                   re.I
               )),

    # ── System Crash ──────────────────────────────────────────────────────────
    PatternDef("crash_bsod",
               "manic",
               "BSOD / kernel panic",
               re.compile(
                   r"PAGE_FAULT_IN_NONPAGED_AREA|IRQL_NOT_LESS_OR_EQUAL"
                   r"|CRITICAL_PROCESS_DIED|STOP:\s*0x[0-9A-Fa-f]+",
                   re.I
               )),

    PatternDef("fatal_error",
               "manic",
               "Fatal / unhandled error",
               re.compile(
                   r"FATAL ERROR|CRITICAL FAILURE|EXCEPTION_ACCESS_VIOLATION"
                   r"|Unhandled exception|Application crash",
                   re.I
               )),
]


@dataclass
class PatternMatch:
    """Result of a successful PATTERN_REGISTRY match."""
    category: str
    mood:     str
    label:    str
    snippet:  str   # the matching line, capped at 120 chars


def _scan_patterns(text: str) -> list[PatternMatch]:
    """Run every PatternDef against text. Returns one match per unique category."""
    seen:    set[str]         = set()
    results: list[PatternMatch] = []
    lines = text.splitlines()
    for pdef in PATTERN_REGISTRY:
        if pdef.category in seen:
            continue
        for line in lines:
            if pdef.pattern.search(line):
                results.append(PatternMatch(
                    category = pdef.category,
                    mood     = pdef.mood,
                    label    = pdef.label,
                    snippet  = line.strip()[:120],
                ))
                seen.add(pdef.category)
                break
    return results


# ── 1. Foreground-window + 4. Multi-monitor helpers (Windows) ─────────────────

def _get_foreground_window_info(skip_hwnd: int | None = None) -> dict | None:
    """Return mss-compatible capture dict + metadata for the focused window.

    Returns {left, top, width, height, title, hwnd} in physical pixels, or None.
    skip_hwnd: pass Agetha's own HWND to avoid capturing herself.
    """
    if not IS_WINDOWS or not PIL_OK:
        return None
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
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

        # Bounding rect in physical pixels (DPI awareness is already set)
        rect = ctypes.wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None

        w = rect.right  - rect.left
        h = rect.bottom - rect.top
        if w <= 20 or h <= 20:
            return None   # minimised or invisible
        if w > 7680 or h > 4320:
            return None   # absurd size — skip to avoid mss crash

        return {
            "left":   rect.left,
            "top":    rect.top,
            "width":  w,
            "height": h,
            "title":  title,
            "hwnd":   hwnd,
        }
    except Exception as e:
        print(f"[ScreenReader] _get_foreground_window_info: {e}")
        return None


def _get_foreground_window_info_linux(skip_hwnd: int | None = None) -> dict | None:
    """Return mss-compatible capture dict + metadata for the focused window on Linux.
    Uses xdotool, xprop, or wmctrl.
    """
    if not IS_LINUX or not PIL_OK:
        return None

    # 1. Try xdotool
    try:
        res = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0:
            window_id_str = res.stdout.strip()
            if window_id_str.isdigit():
                window_id = int(window_id_str)
                if skip_hwnd and window_id == skip_hwnd:
                    return None  # Don't capture ourselves

                title = ""
                res_title = subprocess.run(["xdotool", "getwindowname", window_id_str], capture_output=True, text=True, timeout=2)
                if res_title.returncode == 0:
                    title = res_title.stdout.strip()

                res_geom = subprocess.run(["xdotool", "getwindowgeometry", window_id_str], capture_output=True, text=True, timeout=2)
                if res_geom.returncode == 0:
                    geom_out = res_geom.stdout
                    pos_match = re.search(r"Position:\s*(-?\d+),(-?\d+)", geom_out)
                    size_match = re.search(r"Geometry:\s*(\d+)x(\d+)", geom_out)
                    if pos_match and size_match:
                        left = int(pos_match.group(1))
                        top = int(pos_match.group(2))
                        w = int(size_match.group(1))
                        h = int(size_match.group(2))
                        return {
                            "left": left,
                            "top": top,
                            "width": w,
                            "height": h,
                            "title": title,
                            "hwnd": window_id,
                        }
    except Exception as e:
        print(f"[ScreenReader] xdotool failed: {e}")

    # 2. Fallback to wmctrl + xprop
    try:
        res_xprop = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture_output=True, text=True, timeout=2)
        if res_xprop.returncode == 0:
            m = re.search(r"_NET_ACTIVE_WINDOW\(WINDOW\):\s*window id #\s*(0x[0-9a-fA-F]+)", res_xprop.stdout)
            if m:
                active_hex = m.group(1)
                active_dec = int(active_hex, 16)
                if skip_hwnd and active_dec == skip_hwnd:
                    return None

                res_wmctrl = subprocess.run(["wmctrl", "-l", "-G"], capture_output=True, text=True, timeout=2)
                if res_wmctrl.returncode == 0:
                    for line in res_wmctrl.stdout.splitlines():
                        parts = line.split(maxsplit=6)
                        if len(parts) >= 7:
                            line_hex = parts[0]
                            try:
                                line_dec = int(line_hex, 16)
                            except ValueError:
                                continue
                            if line_dec == active_dec:
                                left = int(parts[2])
                                top = int(parts[3])
                                w = int(parts[4])
                                h = int(parts[5])
                                title = parts[6]
                                return {
                                    "left": left,
                                    "top": top,
                                    "width": w,
                                    "height": h,
                                    "title": title,
                                    "hwnd": active_dec,
                                }
    except Exception as e:
        print(f"[ScreenReader] wmctrl fallback failed: {e}")

    # Log non-blocking warning and fall back to full screen
    print("[ScreenReader] WARNING: Active window scanning failed or tools are missing on Linux. Falling back to full desktop capture.")
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
        print(f"[ScreenReader] _find_monitor_for_window: {e}")
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


def _grab_mss(monitor_dict: dict | None = None) -> "Image.Image | None":
    if not PIL_OK:
        return None
    try:
        import mss
        with mss.mss() as sct:
            target = monitor_dict if monitor_dict else sct.monitors[0]
            raw = sct.grab(target)
            return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    except Exception:
        return None


def _grab_imagegrab(bbox: tuple | None = None) -> "Image.Image | None":
    if not IMAGEGRAB_OK:
        return None
    try:
        img = ImageGrab.grab(bbox=bbox)
        return img if img else None
    except Exception:
        return None


def _grab_pyautogui() -> "Image.Image | None":
    if not PYAUTOGUI_OK:
        return None
    try:
        return pyautogui.screenshot()
    except Exception:
        return None


def _grab_scrot() -> "Image.Image | None":
    if not PIL_OK or not _cmd_exists("scrot"):
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        result = subprocess.run(["scrot", "--silent", tmp], capture_output=True, timeout=10)
        if result.returncode != 0:
            return None
        img = Image.open(tmp).copy(); os.unlink(tmp); return img
    except Exception:
        return None


def _grab_grim() -> "Image.Image | None":
    if not PIL_OK or not _cmd_exists("grim"):
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        result = subprocess.run(["grim", tmp], capture_output=True, timeout=10)
        if result.returncode != 0:
            return None
        img = Image.open(tmp).copy(); os.unlink(tmp); return img
    except Exception:
        return None


def _grab_spectacle() -> "Image.Image | None":
    if not PIL_OK or not _cmd_exists("spectacle"):
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        result = subprocess.run(
            ["spectacle", "--background", "--nonotify", "--fullscreen", "--output", tmp],
            capture_output=True, timeout=15,
        )
        if result.returncode != 0 or not Path(tmp).stat().st_size:
            return None
        img = Image.open(tmp).copy(); os.unlink(tmp); return img
    except Exception:
        return None


def _grab_gnome_screenshot() -> "Image.Image | None":
    if not PIL_OK or not _cmd_exists("gnome-screenshot"):
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        result = subprocess.run(["gnome-screenshot", "-f", tmp], capture_output=True, timeout=10)
        if result.returncode != 0:
            return None
        img = Image.open(tmp).copy(); os.unlink(tmp); return img
    except Exception:
        return None


def _grab_screencapture() -> "Image.Image | None":
    if not PIL_OK:
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp = f.name
        result = subprocess.run(["screencapture", "-x", tmp], capture_output=True, timeout=10)
        if result.returncode != 0:
            return None
        img = Image.open(tmp).copy(); os.unlink(tmp); return img
    except Exception:
        return None


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

        # Capture origin — used to translate Tesseract pixel coords → screen coords
        self._capture_left: int = 0
        self._capture_top:  int = 0

        # Public state (consumed by main.py after each scan)
        self.last_angry_keywords:     list[str]          = []
        self.last_pattern_matches:    list[PatternMatch]  = []
        self.last_word_positions:     list[dict]          = []
        self.last_active_window_title: str               = ""

        # Tesseract path (Windows)
        if self._system == "Windows" and TESSERACT_OK:
            tess = _find_tesseract_windows()
            if tess:
                pytesseract.pytesseract.tesseract_cmd = tess
            else:
                print("[ScreenReader] WARNING: Tesseract not found in standard paths.")

        self._backend_name, self._backend_fn = self._choose_backend()
        self._available = TESSERACT_OK and self._backend_fn is not None

        if not self._available:
            reasons = []
            if not TESSERACT_OK:       reasons.append("pytesseract/tesseract missing")
            if self._backend_fn is None: reasons.append("no screenshot backend")
            print(f"[ScreenReader] Screen capture disabled: {', '.join(reasons)}")
        else:
            print(f"[ScreenReader] Phase 3 ready — backend: {self._backend_name}")

    # ── Backend selection ─────────────────────────────────────────────────────

    def _ordered_backends(self) -> list[tuple]:
        # mss is always first on Windows — it supports partial capture dicts
        head = [("mss", lambda: _grab_mss())]
        if self._system == "Windows":
            return head
        elif self._system == "Darwin":
            return head + [("screencapture", _grab_screencapture), ("pyautogui", _grab_pyautogui)]
        else:
            if _is_wayland():
                return [("spectacle", _grab_spectacle), ("grim", _grab_grim),
                        ("gnome-screenshot", _grab_gnome_screenshot), ("pyautogui", _grab_pyautogui)]
            return head + [("scrot", _grab_scrot), ("pyautogui", _grab_pyautogui)]

    def _choose_backend(self) -> tuple:
        if not _has_display():
            return ("none", None)
        for name, fn in self._ordered_backends():
            try:
                img = fn()
                if img is not None:
                    return (name, fn)
            except Exception:
                continue
        return ("none", None)

    # ── 1. Own-window HWND cache ──────────────────────────────────────────────

    def _get_own_hwnd(self) -> int | None:
        """Cache and return Agetha's own top-level window HWND."""
        if self._own_hwnd:
            return self._own_hwnd
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

    # ── 1 + 4. Focused capture ────────────────────────────────────────────────

    def capture_image(self, focused_only: bool = True) -> "Image.Image | None":
        """Capture a screenshot.

        focused_only=True  — Windows/Linux: grabs only the active foreground window.
                             Falls back to full-monitor if window can't be resolved.
        focused_only=False — Always captures the full primary monitor.
        """
        self._capture_left = 0
        self._capture_top  = 0

        if focused_only:
            win = None
            if IS_WINDOWS:
                win = _get_foreground_window_info(skip_hwnd=self._get_own_hwnd())
            elif IS_LINUX:
                win = _get_foreground_window_info_linux(skip_hwnd=self._get_own_hwnd())

            if win:
                self.last_active_window_title = win["title"]
                self._capture_left = win["left"]
                self._capture_top  = win["top"]
                img = _grab_mss({
                    "left":   win["left"],
                    "top":    win["top"],
                    "width":  win["width"],
                    "height": win["height"],
                })
                if img:
                    print(f"[ScreenReader] Focused: '{win['title']}' "
                          f"({win['width']}×{win['height']} px)")
                    return img
                # mss failed for the focused rect — fall through to full monitor

        # Full-monitor fallback
        self.last_active_window_title = ""
        return self._backend_fn() if self._backend_fn else None

    # ── 2 + 3. Main OCR entry point ───────────────────────────────────────────

    def capture_text(self, max_chars: int = 3000, focused_only: bool = True) -> str:
        """Capture screen text, run pattern matching, populate word positions.

        Returns: plain OCR text string.
        Side-effects (read by main.py after this call):
            self.last_angry_keywords      — legacy flat-keyword hits
            self.last_pattern_matches     — regex pattern matches with mood hints
            self.last_word_positions      — every word's (screen_x, screen_y, w, h)
            self.last_active_window_title — title of the captured window
        """
        self.last_angry_keywords  = []
        self.last_pattern_matches = []
        self.last_word_positions  = []

        if not self._available:
            return ""
        try:
            screenshot = self.capture_image(focused_only=focused_only)
            if screenshot is None:
                return ""

            # 2× upscale → greyscale  (same as Phase 1 — improves Tesseract accuracy)
            scale = 2
            w, h = screenshot.size
            upscaled = screenshot.resize((w * scale, h * scale), Image.LANCZOS).convert("L")

            # ── 2. image_to_data: words + bounding boxes ───────────────────────
            plain_text = ""
            try:
                data = pytesseract.image_to_data(
                    upscaled,
                    lang="eng",
                    config="--psm 3",
                    output_type=pytesseract.Output.DICT,
                )
                words: list[str]  = []
                positions: list[dict] = []
                for i in range(len(data["text"])):
                    txt  = str(data["text"][i]).strip()
                    conf = int(data["conf"][i])
                    if not txt or conf <= 0:
                        continue
                    words.append(txt)
                    if conf > 30:          # only store high-confidence positions
                        # Divide by scale to convert upscaled-image pixels → original
                        # then add capture offset to get desktop screen coordinates.
                        positions.append({
                            "text":     txt,
                            "screen_x": self._capture_left + int(data["left"][i])  // scale,
                            "screen_y": self._capture_top  + int(data["top"][i])   // scale,
                            "w":        int(data["width"][i])  // scale,
                            "h":        int(data["height"][i]) // scale,
                            "conf":     conf,
                        })
                plain_text = " ".join(words)
                self.last_word_positions = positions

            except Exception as e:
                # image_to_data may fail on older pytesseract builds; fall back
                print(f"[ScreenReader] image_to_data failed ({e}), using image_to_string")
                raw = pytesseract.image_to_string(upscaled, lang="eng", config="--psm 3")
                plain_text = raw
                self.last_word_positions = []

            # Normalise & cap
            lines  = [ln.strip() for ln in plain_text.splitlines() if ln.strip()]
            result = "\n".join(lines)[:max_chars]

            # ── 3. Pattern scan ────────────────────────────────────────────────
            self.last_pattern_matches = _scan_patterns(result)
            if self.last_pattern_matches:
                cats = [m.category for m in self.last_pattern_matches]
                print(f"[ScreenReader] Patterns: {cats}")

            # Legacy keyword scan (backward compat)
            low = result.lower()
            self.last_angry_keywords = [kw for kw in ANGRY_KEYWORDS if kw in low]
            if self.last_angry_keywords:
                print(f"[ScreenReader] Angry keywords: {self.last_angry_keywords}")

            print(f"[ScreenReader] {len(result)} chars | "
                  f"{len(self.last_word_positions)} words | "
                  f"{len(self.last_pattern_matches)} patterns")
            return result

        except Exception as e:
            print(f"[ScreenReader] OCR error: {e}")
            return ""

    # ── Convenience properties ─────────────────────────────────────────────────

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
