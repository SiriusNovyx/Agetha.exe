"""
screen_reader.py — Cross-platform screen capture + OCR
Now with keyword-triggered angry mood detection.

pip install mss          (recommended)
pip install python3-xlib  (X11 Linux alternative)
sudo apt install scrot    (X11 Linux fallback)
sudo apt install grim     (Wayland/wlroots: sway, Hyprland, etc.)
# KDE Wayland: spectacle is used automatically (ships with Plasma)
"""

import os
import platform
import subprocess
import tempfile
from pathlib import Path

# ── Keywords that make Agetha angry when found on screen ─────────────────────
ANGRY_KEYWORDS = [
    "cheating", "error 404", "you have been banned", "access denied",
    "virus detected", "your account", "suspicious activity",
    "malware", "unauthorized", "security breach", "account suspended",
    "payment failed", "your ip has been", "please verify",
]


def _find_tesseract_windows() -> str | None:
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    pf = os.environ.get("PROGRAMFILES")
    pf86 = os.environ.get("PROGRAMFILES(X86)")
    if pf:
        candidates.append(str(Path(pf) / "Tesseract-OCR" / "tesseract.exe"))
    if pf86:
        candidates.append(str(Path(pf86) / "Tesseract-OCR" / "tesseract.exe"))
    for c in candidates:
        if Path(c).exists():
            return c
    return None


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
        os.environ.get("DISPLAY") or
        os.environ.get("WAYLAND_DISPLAY") or
        os.environ.get("XDG_SESSION_TYPE") or
        platform.system() in ("Windows", "Darwin")
    )


def _is_wayland() -> bool:
    return (
        bool(os.environ.get("WAYLAND_DISPLAY"))
        or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    )


def _grab_mss() -> "Image.Image | None":
    if not PIL_OK:
        return None
    try:
        import mss
        with mss.mss() as sct:
            raw = sct.grab(sct.monitors[0])
            return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    except Exception:
        return None


def _grab_imagegrab() -> "Image.Image | None":
    if not IMAGEGRAB_OK:
        return None
    try:
        img = ImageGrab.grab()
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
        img = Image.open(tmp).copy()
        os.unlink(tmp)
        return img
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
        img = Image.open(tmp).copy()
        os.unlink(tmp)
        return img
    except Exception:
        return None


def _grab_spectacle() -> "Image.Image | None":
    """KDE Plasma (Wayland or X11). Ships with Plasma — no install needed."""
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
        img = Image.open(tmp).copy()
        os.unlink(tmp)
        return img
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
        img = Image.open(tmp).copy()
        os.unlink(tmp)
        return img
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
        img = Image.open(tmp).copy()
        os.unlink(tmp)
        return img
    except Exception:
        return None


class ScreenReader:
    """Captures the screen and extracts text via OCR.
    Now also scans for angry-trigger keywords and exposes them via .last_angry_keywords.
    """

    def __init__(self):
        self._system = platform.system()
        self.last_angry_keywords: list[str] = []  # populated after each capture_text()

        if self._system == "Windows" and TESSERACT_OK:
            tess_path = _find_tesseract_windows()
            if tess_path:
                pytesseract.pytesseract.tesseract_cmd = tess_path
            else:
                print("[ScreenReader] WARNING: Tesseract not found. Install from https://github.com/UB-Mannheim/tesseract/wiki")

        self._backend_name, self._backend_fn = self._choose_backend()
        self._available = TESSERACT_OK and self._backend_fn is not None

        if not self._available:
            reasons = []
            if not TESSERACT_OK:
                reasons.append("pytesseract/tesseract missing")
            if self._backend_fn is None:
                reasons.append("no screenshot backend found")
            print(f"[ScreenReader] Screen capture disabled: {', '.join(reasons)}")
            if self._backend_fn is None and _has_display():
                self._print_install_hints()
        else:
            print(f"[ScreenReader] Using backend: {self._backend_name}")

    def _ordered_backends(self) -> list:
        common_head = [
            ("mss",       _grab_mss),
            ("ImageGrab", _grab_imagegrab),
        ]
        if self._system == "Windows":
            return common_head + [("pyautogui", _grab_pyautogui)]
        elif self._system == "Darwin":
            return common_head + [
                ("screencapture", _grab_screencapture),
                ("pyautogui",     _grab_pyautogui),
            ]
        else:
            if _is_wayland():
                return [
                    ("spectacle",        _grab_spectacle),
                    ("grim",             _grab_grim),
                    ("gnome-screenshot", _grab_gnome_screenshot),
                    ("pyautogui",        _grab_pyautogui),
                ]
            else:
                return common_head + [
                    ("scrot",     _grab_scrot),
                    ("pyautogui", _grab_pyautogui),
                ]

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

    @staticmethod
    def _print_install_hints():
        sys = platform.system()
        print("[ScreenReader] To enable screen capture, install one of:")
        if sys == "Linux":
            if _is_wayland():
                print("  KDE     : spectacle (ships with Plasma, should already be installed)")
                print("  wlroots : sudo pacman -S grim  (sway, Hyprland, etc.)")
                print("  GNOME   : sudo pacman -S gnome-screenshot")
            else:
                print("  X11     : pip install mss")
                print("            pip install python3-xlib")
                print("            sudo apt install scrot")
        elif sys == "Windows":
            print("  Windows : pip install mss pillow")
        elif sys == "Darwin":
            print("  macOS   : pip install mss pillow")

    def capture_image(self) -> "Image.Image | None":
        if self._backend_fn is None:
            return None
        return self._backend_fn()

    def capture_text(self, max_chars: int = 3000) -> str:
        """Capture screen text. Also sets self.last_angry_keywords if any triggers found."""
        self.last_angry_keywords = []
        if not self._available:
            return ""
        try:
            screenshot = self.capture_image()
            if screenshot is None:
                return ""
            w, h = screenshot.size
            screenshot = screenshot.resize((w * 2, h * 2), Image.LANCZOS)
            screenshot = screenshot.convert("L")
            text = pytesseract.image_to_string(screenshot, lang="eng", config="--psm 3")
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            result = "\n".join(lines)[:max_chars]

            # ── Keyword scan ──────────────────────────────────────────────
            low = result.lower()
            found = [kw for kw in ANGRY_KEYWORDS if kw in low]
            if found:
                self.last_angry_keywords = found
                print(f"[ScreenReader] ANGRY KEYWORDS DETECTED: {found}")

            print(f"[ScreenReader] Captured {len(result)} chars:\n{result}\n")
            return result
        except Exception as e:
            print(f"[ScreenReader] OCR error: {e}")
            return ""

    @property
    def has_angry_trigger(self) -> bool:
        """True if the last capture_text() found angry keywords."""
        return len(self.last_angry_keywords) > 0
