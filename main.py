"""
Desktop AI Companion - Main Application
Requires: pip install pillow pyautogui pytesseract numpy pygame requests
Assets folder must contain: idle-1.gif, idle-2.gif, idle-3.gif,
  talking-1.gif, talking-2.gif, talking-3.gif,
  thinking.gif, sleeping.gif, happy.gif, surprised.gif, sad.gif, angry.gif
  (excited mood reuses happy.gif — no separate excited.gif needed)
Font: barrio.ttf must be in assets/ folder
"""

import tkinter as tk
from tkinter import font as tkfont
import threading
import time
import random
import json
import math
import os
import platform
import subprocess
import webbrowser
from pathlib import Path
from PIL import Image, ImageTk, ImageSequence
import pygame

from ai_engine import AIEngine
from screen_reader import ScreenReader

import sys
BASE_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
ASSETS      = BASE_DIR / "assets"
FONT_PATH   = ASSETS / "barrio.ttf"


def native_error_popup(title: str, message: str) -> None:
    """Show a native OS error dialog — same style as the first-run config popup.
    Uses Windows MessageBoxW (MB_ICONERROR | MB_TOPMOST) with a tkinter showerror fallback."""
    print(f"[ERROR] {title}: {message}")
    try:
        import ctypes
        # 0x10 = MB_ICONERROR, 0x1000 = MB_TOPMOST
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10 | 0x1000)
        return
    except Exception:
        pass
    try:
        import tkinter as _tk
        from tkinter import messagebox as _mb
        _r = _tk.Tk()
        _r.withdraw()
        _r.attributes("-topmost", True)
        _mb.showerror(title, message, parent=_r)
        _r.destroy()
    except Exception:
        pass

WINDOW_W = 340
WINDOW_H = 560
GIF_W    = 340
GIF_H    = 300

SCREEN_POLL_INTERVAL_MS = 2 * 60 * 1000

BLEEP_TONES = {
    "neutral":   440,
    "happy":     523,
    "excited":   659,
    "sad":       294,
    "surprised": 587,
    "thinking":  370,
    "whisper":   220,
    "angry":     185,
}


def _safe_win_font(size: int = 8, bold: bool = False) -> tuple:
    """Return a font tuple that renders correctly on Win10, Win11, Server, and LTSC.
    Tries MS Sans Serif first (Win95 look), falls back to Segoe UI, then TkDefaultFont."""
    weight = "bold" if bold else "normal"
    # MS Sans Serif ships with Windows but may be absent on some Server/LTSC installs.
    # Segoe UI is the modern fallback; Arial is the last resort.
    for family in ("MS Sans Serif", "Segoe UI", "Arial", "TkDefaultFont"):
        try:
            tkfont.Font(family=family, size=size, weight=weight)
            return (family, size, weight) if bold else (family, size)
        except Exception:
            continue
    return ("TkDefaultFont", size)


# ── Windows 95 colour palette ──────────────────────────────────────────────
W95_BG        = "#c0c0c0"
W95_TITLE_BG  = "#000080"
W95_TITLE_FG  = "#ffffff"
W95_TEXT      = "#000000"
W95_INPUT_BG  = "#ffffff"
W95_SHADOW    = "#808080"
W95_BTN_BG    = "#c0c0c0"
W95_BTN_ACT   = "#000080"
W95_BTN_AFG   = "#ffffff"
W95_FONT      = ("MS Sans Serif", 8)
W95_FONT_BOLD = ("MS Sans Serif", 8, "bold")
# Note: Tk uses the first available font in the family name; if MS Sans Serif is missing
# on a given Windows install, _build_ui() patches these at runtime via _safe_win_font().
# ───────────────────────────────────────────────────────────────────────────


def _register_barrio_font():
    if not FONT_PATH.exists():
        print(f"[Font] barrio.ttf not found at {FONT_PATH}")
        return False
    try:
        import tkextrafont
        tkextrafont.load(str(FONT_PATH))
        print("[Font] Loaded barrio.ttf via tkextrafont")
        return True
    except (ImportError, AttributeError):
        pass
    try:
        import shutil, subprocess, platform
        system = platform.system()
        if system == "Linux":
            font_dir = Path.home() / ".local/share/fonts"
            font_dir.mkdir(parents=True, exist_ok=True)
            dest = font_dir / "barrio.ttf"
            if not dest.exists():
                shutil.copy(FONT_PATH, dest)
                subprocess.run(["fc-cache", "-f"], capture_output=True)
            print("[Font] Installed barrio.ttf to ~/.local/share/fonts")
            return True
        elif system == "Darwin":
            font_dir = Path.home() / "Library/Fonts"
            font_dir.mkdir(parents=True, exist_ok=True)
            dest = font_dir / "barrio.ttf"
            if not dest.exists():
                shutil.copy(FONT_PATH, dest)
            print("[Font] Installed barrio.ttf to ~/Library/Fonts")
            return True
        elif system == "Windows":
            import ctypes, winreg
            user_fonts = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"
            user_fonts.mkdir(parents=True, exist_ok=True)
            dest = user_fonts / "barrio.ttf"
            if not dest.exists():
                shutil.copy(FONT_PATH, dest)
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows NT\CurrentVersion\Fonts",
                    0, winreg.KEY_SET_VALUE
                )
                winreg.SetValueEx(key, "Barrio (TrueType)", 0, winreg.REG_SZ, str(dest))
                winreg.CloseKey(key)
            except Exception:
                pass
            ctypes.windll.gdi32.AddFontResourceW(str(dest))
            # SendMessageW broadcast to all windows can stall for several seconds
            # on Windows 11 — run it in a daemon thread so it never blocks startup.
            def _broadcast():
                ctypes.windll.user32.SendMessageW(0xFFFF, 0x001D, 0, 0)
            threading.Thread(target=_broadcast, daemon=True).start()
            print("[Font] Installed barrio.ttf to user fonts dir (Windows)")
            return True
    except Exception as e:
        print(f"[Font] Could not install font: {e}")
    return False


class BleepPlayer:
    """Undertale-style 8-bit bleeps using a square wave with decay envelope."""

    SAMPLE_RATE = 44100

    def __init__(self):
        self._stop_event = threading.Event()
        self._paused = False
        self._thread: threading.Thread | None = None
        self._cache: dict[int, pygame.mixer.Sound] = {}
        self._mixer_ready = False

        # Run pygame mixer init in a background thread — on Windows 11, SDL2's
        # audio device enumeration can deadlock the main thread indefinitely.
        t = threading.Thread(target=self._init_mixer, daemon=True)
        t.start()
        t.join(timeout=5.0)
        if not self._mixer_ready:
            print("[BleepPlayer] WARNING: pygame mixer init timed out — audio disabled.")

    def _init_mixer(self):
        try:
            pygame.mixer.pre_init(self.SAMPLE_RATE, -16, 1, 256)
            pygame.mixer.init()
            self._mixer_ready = True
        except Exception as e:
            print(f"[BleepPlayer] mixer init error: {e}")

    def _make_bleep(self, freq: int) -> "pygame.mixer.Sound | None":
        if not self._mixer_ready:
            return None
        if freq in self._cache:
            return self._cache[freq]

        import array as arr
        duration   = 0.042
        n_samples  = int(self.SAMPLE_RATE * duration)
        volume     = 0.28
        buf        = arr.array("h", [0] * n_samples)

        for i in range(n_samples):
            t = i / self.SAMPLE_RATE
            wave = 1.0 if math.sin(2 * math.pi * freq * t) >= 0 else -1.0
            env  = math.exp(-t * 40)
            buf[i] = int(wave * env * volume * 32767)

        sound = pygame.mixer.Sound(buffer=buf)
        self._cache[freq] = sound
        return sound

    def start_talking(self, tone: str = "neutral"):
        if not self._mixer_ready:
            return
        self.stop()
        self._stop_event.clear()
        freq = BLEEP_TONES.get(tone, 440)
        self._thread = threading.Thread(target=self._loop, args=(freq,), daemon=True)
        self._thread.start()

    def _loop(self, freq: int):
        sound = self._make_bleep(freq)
        if sound is None:
            return
        while not self._stop_event.is_set():
            if self._paused:
                time.sleep(0.02)
                continue
            sound.play()
            time.sleep(random.uniform(0.03, 0.055))

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def stop(self):
        self._stop_event.set()
        self._paused = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.4)


def _read_animation_speed() -> float:
    """Read ANIMATION_SPEED from config.txt once at startup. Returns 0.6 if missing/invalid."""
    try:
        _base = Path(sys.argv[0]).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).parent
        _cfg = _base / "config.txt"
        if _cfg.exists():
            for ln in _cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                s = ln.strip()
                if s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                if k.strip().upper() == "ANIMATION_SPEED":
                    return float(v.strip())
    except Exception:
        pass
    return 0.6

# Read once at import time so GifPlayer doesn't re-read config per frame
_ANIMATION_SPEED = _read_animation_speed()


def _load_gif_frames_offthread(path: str) -> tuple[list[Image.Image], list[int]]:
    """Do all heavy PIL work (open, convert, resize, composite) off the main thread.
    Returns (pil_images, delays) — no ImageTk objects yet, those need the main thread."""
    pil_frames: list[Image.Image] = []
    delays: list[int] = []
    is_sleeping = Path(path).name == "sleeping.gif"
    speed = 1.0 if is_sleeping else _ANIMATION_SPEED
    try:
        img = Image.open(path)
        for frame in ImageSequence.Iterator(img):
            f = frame.convert("RGBA")
            f.thumbnail((GIF_W, GIF_H), Image.LANCZOS)
            canvas = Image.new("RGBA", (GIF_W, GIF_H), (10, 10, 15, 255))
            ox = (GIF_W - f.width) // 2
            oy = (GIF_H - f.height) // 2
            canvas.paste(f, (ox, oy), f)
            pil_frames.append(canvas)
            delay = frame.info.get("duration", 80)
            delays.append(max(int(delay * speed), 40))
    except Exception as e:
        print(f"[GifPlayer] Could not load {path}: {e}")
    return pil_frames, delays


class GifPlayer:
    """Loads and animates a GIF on a tk.Label, looping automatically.

    PIL work (open/convert/resize/composite) is done off the main thread in
    _load_gif_frames_offthread(). Only ImageTk.PhotoImage creation — which
    requires Tk to be alive — happens on the main thread, and it's fast.
    """

    def __init__(self, label: tk.Label, gif_path: str, after_cb,
                 pil_frames: list | None = None, delays: list | None = None):
        self._label   = label
        self._after   = after_cb
        self._frames: list[ImageTk.PhotoImage] = []
        self._delays: list[int] = delays or []
        self._idx     = 0
        self._job     = None
        self._running = False
        # once-play control
        self._once_counter: int | None = None
        self._on_once_done = None

        if pil_frames is not None:
            # Fast path: PIL work already done, just convert to ImageTk on main thread
            for pil_img in pil_frames:
                try:
                    self._frames.append(ImageTk.PhotoImage(pil_img))
                except Exception as e:
                    print(f"[GifPlayer] ImageTk conversion failed for {gif_path}: {e}")
        else:
            # Slow/legacy path: load synchronously (only used if called without pre-loading)
            pil_frames, self._delays = _load_gif_frames_offthread(gif_path)
            for pil_img in pil_frames:
                try:
                    self._frames.append(ImageTk.PhotoImage(pil_img))
                except Exception as e:
                    print(f"[GifPlayer] ImageTk conversion failed for {gif_path}: {e}")

    def play(self):
        if not self._frames:
            return
        self._running = True
        self._idx = 0
        # looped play
        self._once_counter = None
        self._on_once_done = None
        self._tick()

    def stop(self):
        self._running = False
        self._once_counter = None
        self._on_once_done = None
        if self._job:
            try:
                self._label.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _tick(self):
        if not self._running or not self._frames:
            return
        self._label.config(image=self._frames[self._idx])
        delay = self._delays[self._idx]
        # Advance index and handle once-play behavior
        self._idx = self._idx + 1
        if self._once_counter is not None:
            # counting down frames to play once
            self._once_counter -= 1
            if self._once_counter <= 0:
                # finished a single-play run
                self._running = False
                self._job = None
                cb = self._on_once_done
                self._on_once_done = None
                self._once_counter = None
                if cb:
                    try:
                        cb()
                    except Exception:
                        pass
                return
            else:
                # continue through frames (no wrap until counter finishes)
                self._idx = self._idx % len(self._frames)
                self._job = self._after(delay, self._tick)
                return

        # normal looping behavior
        self._idx = self._idx % len(self._frames)
        self._job = self._after(delay, self._tick)

    def play_once(self, on_done=None):
        """Play the GIF exactly once (all frames) then call on_done()."""
        if not self._frames:
            if on_done:
                try:
                    on_done()
                except Exception:
                    pass
            return
        self.stop()
        self._running = True
        self._idx = 0
        self._once_counter = len(self._frames)
        self._on_once_done = on_done
        self._tick()


class SubtitleRenderer:
    """Typewriter-style subtitles on a Canvas using the Barrio font."""

    # Slightly slower typing for a more natural pace
    CHAR_DELAY = 0.035

    def __init__(self, canvas: tk.Canvas, font_size: int = 17, bleep_player=None):
        self._canvas     = canvas
        self._font_size  = font_size
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._bleep = bleep_player

        self._canvas.config(bg="#a0a0a0")
        self._font = self._load_font(font_size)

    def _load_font(self, size: int) -> tkfont.Font:
        available = tkfont.families()
        for name in ("Barrio", "barrio"):
            if name in available:
                print(f"[Font] Using '{name}' from Tk font families")
                return tkfont.Font(family=name, size=size)
        print("[Font] Barrio not found in Tk families, using Courier fallback")
        return tkfont.Font(family="Courier", size=size, weight="bold")

    def clear(self):
        self._canvas.delete("all")

    def show_thinking(self, raw_text: str):
        """Show streaming tokens in grey while waiting for a response."""
        import re
        texts = re.findall(r'"text"\s*:\s*"([^"]*)', raw_text)
        preview = " ".join(texts).strip() or "…"
        self._canvas.after(0, lambda p=preview: self._draw(p, color="#888899"))

    def show_message(self, text: str, color: str = "#ffffff", duration: float = 6.0):
        """Immediately show a static subtitle message (optionally auto-clears)."""
        # Interrupt any running typewriter speak
        self.stop()
        # Draw immediately on the canvas
        self._canvas.after(0, lambda: self._draw(text, color))
        # Schedule clear after duration seconds (if > 0)
        try:
            if duration and duration > 0:
                self._canvas.after(int(duration * 1000), self.clear)
        except Exception:
            pass

    def speak(self, segments: list, on_done=None):
        self.stop()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, args=(segments, on_done), daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _run(self, segments: list, on_done):
        self._canvas.after(0, self.clear)
        full_text = ""
        for i, seg in enumerate(segments):
            if self._stop_event.is_set():
                break
            chunk = seg.get("text", "").strip()
            pause = seg.get("pause", 0.0)
            if full_text and not full_text.endswith(" "):
                full_text += " "
            for ch in chunk:
                if self._stop_event.is_set():
                    break
                full_text += ch
                t = full_text
                self._canvas.after(0, lambda txt=t: self._draw(txt))
                time.sleep(self.CHAR_DELAY)
            if pause > 0 and not self._stop_event.is_set():
                if self._bleep:
                    self._bleep.pause()
                time.sleep(pause)
                if self._bleep:
                    self._bleep.resume()
        # Speech finished — stop bleeps immediately so they don't trail into idle state
        try:
            if self._bleep:
                self._bleep.stop()
        except Exception:
            pass
        if on_done:
            self._canvas.after(0, on_done)

    def _draw(self, text: str, color: str = "#ffffff"):
        cw = self._canvas.winfo_width() or WINDOW_W
        ch = self._canvas.winfo_height() or 130
        max_w = max(40, cw - 24)
        max_lines = 3
        min_font_size = 8

        import re

        def estimate_lines(word_list, chars_per_line):
            line_chars = 0
            lines = 1
            for w in word_list:
                needed = len(w) + (1 if line_chars > 0 else 0)
                if line_chars > 0 and line_chars + needed > chars_per_line:
                    lines += 1
                    line_chars = len(w)
                else:
                    line_chars += needed
            return lines

        words = text.split()
        if not words:
            self._canvas.delete("all")
            return

        font_size = self._font_size
        font = self._font

        while font_size >= min_font_size:
            char_w = max(4, font_size * 0.62)
            chars_per_line = max(1, int(max_w // char_w))

            parts = []
            for w in re.split(r'(\s+)', text):
                if w.isspace() or not w:
                    parts.append(w)
                    continue
                if len(w) <= chars_per_line:
                    parts.append(w)
                else:
                    chunks = [w[i:i+chars_per_line] for i in range(0, len(w), chars_per_line)]
                    parts.append(" ".join(chunks))
            candidate_words = "".join(parts).strip().split()

            if estimate_lines(candidate_words, chars_per_line) <= max_lines:
                break

            font_size -= 1
            font = self._load_font(font_size)

        candidate = " ".join(candidate_words)
        x = cw // 2

        while font_size >= min_font_size:
            self._canvas.delete("all")
            try:
                shadow_id = self._canvas.create_text(
                    x + 2, 6 + 2, text=candidate, fill="#000000",
                    font=font, anchor="n", width=max_w, justify="center"
                )
                text_id = self._canvas.create_text(
                    x, 6, text=candidate, fill=color,
                    font=font, anchor="n", width=max_w, justify="center"
                )
                bbox = self._canvas.bbox(text_id)
                if bbox:
                    height = bbox[3] - bbox[1]
                    if height <= ch - 12:
                        y = max(6, (ch - height) // 2)
                        self._canvas.coords(shadow_id, x + 2, y + 2)
                        self._canvas.coords(text_id, x, y)
                        break
                    font_size -= 1
                    font = self._load_font(font_size)
                else:
                    break
            except Exception:
                break


class AgethaPopup:
    """Windows 95-style dialog popup spawned by Agetha."""

    def __init__(self, parent: tk.Tk, messages: list, mood: str = "neutral"):
        self._win = tk.Toplevel(parent)
        self._win.overrideredirect(True)   # we draw our own chrome
        self._win.attributes("-topmost", True)
        self._win.configure(bg=W95_BG)
        self._win.resizable(False, False)
        self._drag_x = self._drag_y = 0

        # ── Outer raised bevel ────────────────────────────────────────────
        outer = tk.Frame(self._win, bg=W95_BG, relief="raised", bd=2)
        outer.pack(fill="both", expand=True)

        # ── Title bar ─────────────────────────────────────────────────────
        title_bar = tk.Frame(outer, bg=W95_TITLE_BG, height=18)
        title_bar.pack(fill="x", padx=2, pady=(2, 0))
        title_bar.pack_propagate(False)

        tk.Label(
            title_bar, text="⚠  Agetha.exe",
            bg=W95_TITLE_BG, fg=W95_TITLE_FG,
            font=W95_FONT_BOLD, anchor="w", padx=4,
        ).pack(side="left", fill="y")

        close_btn = tk.Button(
            title_bar, text="✕",
            bg=W95_BTN_BG, fg=W95_TEXT,
            font=("MS Sans Serif", 7, "bold"),
            relief="raised", bd=2, width=2,
            activebackground=W95_BTN_BG, activeforeground=W95_TEXT,
            command=self._win.destroy,
        )
        close_btn.pack(side="right", padx=2, pady=1)

        # bind drag on title bar and its label child
        for w in (title_bar,) + tuple(title_bar.winfo_children()):
            if not isinstance(w, tk.Button):
                w.bind("<ButtonPress-1>", self._drag_start)
                w.bind("<B1-Motion>",     self._drag_motion)

        # ── Body ──────────────────────────────────────────────────────────
        body = tk.Frame(outer, bg=W95_BG, padx=12, pady=10)
        body.pack(fill="both", expand=True, padx=2)

        icon_frame = tk.Frame(body, bg=W95_BG, bd=2, relief="sunken",
                              width=36, height=36)
        icon_frame.grid(row=0, column=0,
                        rowspan=max(len(messages), 1) + 1,
                        sticky="n", padx=(0, 12), pady=2)
        icon_frame.pack_propagate(False)
        tk.Label(icon_frame, text="⚠", fg="#ff8000", bg=W95_BG,
                 font=("MS Sans Serif", 16, "bold")).pack(expand=True)

        for i, msg in enumerate(messages):
            tk.Label(
                body, text=msg,
                fg=W95_TEXT, bg=W95_BG,
                font=W95_FONT,
                wraplength=240, justify="left", anchor="w",
            ).grid(row=i, column=1, sticky="w", pady=1)

        # ── Separator ─────────────────────────────────────────────────────
        tk.Frame(outer, bg=W95_SHADOW, height=1).pack(fill="x", padx=2, pady=(4, 0))
        tk.Frame(outer, bg="#ffffff",  height=1).pack(fill="x", padx=2)

        # ── OK button ─────────────────────────────────────────────────────
        btn_row = tk.Frame(outer, bg=W95_BG, pady=6)
        btn_row.pack(fill="x")
        tk.Button(
            btn_row, text="OK",
            font=W95_FONT_BOLD,
            bg=W95_BTN_BG, fg=W95_TEXT,
            activebackground=W95_BTN_ACT, activeforeground=W95_BTN_AFG,
            relief="raised", bd=2, width=8, pady=2,
            command=self._win.destroy,
        ).pack()

        # ── Position just above the parent window ─────────────────────────
        self._win.update_idletasks()
        px = parent.winfo_x()
        py = parent.winfo_y()
        pw = parent.winfo_width()
        ww = self._win.winfo_width()
        wh = self._win.winfo_height()
        x  = px + (pw - ww) // 2
        y  = max(0, py - wh - 10)
        self._win.geometry(f"+{x}+{y}")

        self._win.bind("<Return>", lambda _: self._win.destroy())
        self._win.bind("<Escape>", lambda _: self._win.destroy())
        try:
            self._win.focus_force()
        except Exception:
            pass

    def _drag_start(self, event):
        self._drag_x, self._drag_y = event.x_root, event.y_root

    def _drag_motion(self, event):
        dx = event.x_root - self._drag_x
        dy = event.y_root - self._drag_y
        self._win.geometry(f"+{self._win.winfo_x()+dx}+{self._win.winfo_y()+dy}")
        self._drag_x, self._drag_y = event.x_root, event.y_root


class CompanionApp:

    STATE_SLEEPING = "sleeping"
    STATE_THINKING = "thinking"
    STATE_IDLE     = "idle"
    STATE_TALKING  = "talking"

    IDLE_GIFS    = ["idle-1.gif", "idle-2.gif", "idle-3.gif"]
    TALKING_GIFS = ["talking-1.gif", "talking-2.gif", "talking-3.gif"]
    EXTRA_GIFS   = {
        "happy":     "happy.gif",
        "surprised": "surprised.gif",
        "sad":       "sad.gif",
        "excited":   "happy.gif",   # excited shares happy.gif — same animation, distinct mood
        "angry":     "angry.gif",
        "thinking":  "thinking.gif",
        "sleeping":  "sleeping.gif",
        "loaf":      "loaf.gif",
    }

    # Static images to show after animated emotion gifs finish
    EXTRA_STATIC_GIFS = {
        "happy": "happy-static.gif",
        "sad":   "sad-static.gif",
        "angry": "angry-static.gif",
        "thinking": "thinking-static.gif",
    }

    def __init__(self):
        # Enable Per-Monitor DPI awareness before creating the Tk window.
        # Without this, Windows scales the window up with bicubic interpolation
        # making everything blurry on 125%/150%/200% displays (common on Win10/11).
        # We try the v2 API (Win10 1703+) first, fall back to v1 (Win8.1+), then
        # the legacy SetProcessDPIAware (Vista+). All calls are no-ops on non-Windows.
        try:
            import ctypes
            _shcore = ctypes.windll.shcore
            try:
                _shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE_V2
            except Exception:
                try:
                    _shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
                except Exception:
                    ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

        # Register font before creating the Tk window so families() sees it
        _register_barrio_font()

        self.root = tk.Tk()
        self.root.title("Agetha.exe")
        self.root.geometry(f"{WINDOW_W}x{WINDOW_H}+80+80")
        self.root.configure(bg=W95_BG)
        self.root.overrideredirect(True)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        self._state      = self.STATE_SLEEPING
        self._current_gif_player: GifPlayer | None = None
        self._gif_cache: dict[str, GifPlayer] = {}
        self._talking_rotate_job = None
        self._poll_job = None
        self._persistent_mood: str | None = None  # holds sad/angry across speech→idle

        # Defer heavy initialization to background thread so the window shows immediately
        self._bleep  = None
        self._screen = None
        self._ai     = None
        self._last_screen_text: str = ""
        self._loaf_job = None
        self._is_loafing = False
        self._pending_shutdown = False
        self._last_touch_time: float = 0.0   # epoch time of last gif-click touch event

        self._build_ui()

        # Show a Win95-style progress bar that covers the gif + subtitle area.
        # Built from plain tk widgets — no ttk — to match the existing W95 aesthetic.
        self._loading_label = tk.Frame(self._outer, bg=W95_BG)
        self._loading_label.place(x=0, y=20, relwidth=1.0, relheight=1.0)

        # Title text
        tk.Label(
            self._loading_label,
            text="Loading Agetha.exe",
            fg=W95_TEXT, bg=W95_BG,
            font=W95_FONT_BOLD,
        ).pack(pady=(40, 4))

        # Status message (updated as each step completes)
        self._load_status_var = tk.StringVar(value="Initializing…")
        tk.Label(
            self._loading_label,
            textvariable=self._load_status_var,
            fg=W95_SHADOW, bg=W95_BG,
            font=W95_FONT,
        ).pack(pady=(0, 8))

        # Win95 progress bar: sunken outer frame, filled inner canvas
        _pb_outer = tk.Frame(
            self._loading_label,
            bg=W95_BG, relief="sunken", bd=2,
            width=WINDOW_W - 60, height=20,
        )
        _pb_outer.pack(pady=(0, 4))
        _pb_outer.pack_propagate(False)

        self._pb_canvas = tk.Canvas(
            _pb_outer, bg=W95_INPUT_BG,
            highlightthickness=0, bd=0,
        )
        self._pb_canvas.pack(fill="both", expand=True)

        # Percentage label below the bar
        self._load_pct_var = tk.StringVar(value="0%")
        tk.Label(
            self._loading_label,
            textvariable=self._load_pct_var,
            fg=W95_TEXT, bg=W95_BG,
            font=W95_FONT,
        ).pack()

        # Progress tracking: 3 init steps + N GIFs (populated later)
        self._load_total   = 3   # will be increased when gif list is known
        self._load_done    = 0

        def _draw_progress():
            """Redraw the Win95-style filled progress bar on the canvas."""
            try:
                self._pb_canvas.update_idletasks()
                w = self._pb_canvas.winfo_width()
                h = self._pb_canvas.winfo_height()
                if w < 2 or h < 2:
                    return
                pct = min(self._load_done / max(self._load_total, 1), 1.0)
                fill_w = max(0, int(w * pct))
                self._pb_canvas.delete("all")
                # Blue filled blocks (Win95 uses chunky segmented blocks)
                block = 16
                gap   = 2
                x = 0
                while x + block <= fill_w:
                    self._pb_canvas.create_rectangle(
                        x, 1, x + block - gap, h - 1,
                        fill=W95_TITLE_BG, outline="",
                    )
                    x += block
                pct_int = int(pct * 100)
                self._load_pct_var.set(f"{pct_int}%")
            except Exception:
                pass

        self._draw_progress = _draw_progress

        def _advance_progress(status: str, steps: int = 1):
            """Thread-safe progress advance — call from any thread."""
            def _on_main():
                self._load_done += steps
                self._load_status_var.set(status)
                self._draw_progress()
            try:
                self.root.after(0, _on_main)
            except Exception:
                pass

        self._advance_progress = _advance_progress

        # Draw the initial (empty) bar once the canvas is mapped
        self._loading_label.after(50, _draw_progress)

        # Force update so the window and loading label become visible immediately.
        # On Windows 11, overrideredirect windows can render as a black rectangle
        # until the compositor receives a proper redraw signal. Calling both
        # update_idletasks() and update() — plus a deiconify/lift pair — flushes
        # the DWM pipeline and makes the loading label appear on the first frame.
        try:
            self.root.update_idletasks()
            self.root.update()
            # deiconify + lift forces the compositor to composite the window immediately
            self.root.deiconify()
            self.root.lift()
            self.root.update()
        except Exception:
            pass

        # Start background init (audio, screen reader, AI); UI-related work (preloading GIFs)
        # will be scheduled back on the main thread when ready.
        threading.Thread(target=self._init_background, daemon=True).start()

        self._drag_x = self._drag_y = 0
        self._is_minimized = False

    def _build_ui(self):
        # Patch font constants now that Tk is alive and tkfont.families() is valid
        global W95_FONT, W95_FONT_BOLD
        W95_FONT      = _safe_win_font(8, bold=False)
        W95_FONT_BOLD = _safe_win_font(8, bold=True)

        # ── Outer raised bevel (whole window border) ──────────────────────────
        self._outer = tk.Frame(self.root, bg=W95_BG, relief="raised", bd=2)
        self._outer.pack(fill="both", expand=True)

        # ── Win95 Title bar ───────────────────────────────────────────────────
        title_bar = tk.Frame(self._outer, bg=W95_TITLE_BG, height=18)
        title_bar.pack(fill="x", padx=2, pady=(2, 0))
        title_bar.pack_propagate(False)

        # App icon + title
        title_lbl = tk.Label(
            title_bar, text="⚠  Agetha.exe",
            bg=W95_TITLE_BG, fg=W95_TITLE_FG,
            font=W95_FONT_BOLD, anchor="w", padx=4,
        )
        title_lbl.pack(side="left", fill="y")

        # Close button
        close_btn = tk.Button(
            title_bar, text="✕",
            bg=W95_BTN_BG, fg=W95_TEXT,
            font=("MS Sans Serif", 7, "bold"),
            relief="raised", bd=2, width=2,
            activebackground=W95_BTN_BG, activeforeground=W95_TEXT,
            command=self.root.quit,
        )
        close_btn.pack(side="right", padx=(0, 2), pady=1)

        # Maximize button (no-op visual)
        max_btn = tk.Button(
            title_bar, text="□",
            bg=W95_BTN_BG, fg=W95_TEXT,
            font=("MS Sans Serif", 7, "bold"),
            relief="raised", bd=2, width=2,
            activebackground=W95_BTN_BG, activeforeground=W95_TEXT,
            command=lambda: None,
        )
        max_btn.pack(side="right", padx=(0, 1), pady=1)

        # Minimize button
        min_btn = tk.Button(
            title_bar, text="─",
            bg=W95_BTN_BG, fg=W95_TEXT,
            font=("MS Sans Serif", 7, "bold"),
            relief="raised", bd=2, width=2,
            activebackground=W95_BTN_BG, activeforeground=W95_TEXT,
            command=self._minimize,
        )
        min_btn.pack(side="right", padx=(0, 1), pady=1)

        # Drag bindings on title bar and its non-button children
        for w in (title_bar, title_lbl):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>",     self._drag_motion)


        # ── GIF display area — black background, raised border ───────────────
        gif_border = tk.Frame(self._outer, bg="#000000", relief="raised", bd=2)
        gif_border.pack(fill="x", padx=4, pady=(4, 0))

        self._gif_label = tk.Label(gif_border, bg="#000000", bd=0,
                                   width=GIF_W, height=GIF_H,
                                   anchor="center")
        self._gif_label.pack(fill="both", expand=True)
        # Clicking on Agetha sends a touch event to the AI (10 s cooldown)
        self._gif_label.bind("<Button-1>", self._on_gif_click)

        # ── Status bar ────────────────────────────────────────────────────────
        status_frame = tk.Frame(self._outer, bg=W95_BG, bd=1, relief="sunken")
        status_frame.pack(fill="x", padx=4, pady=(2, 0))
        self._status_var = tk.StringVar(value="zzz…")
        tk.Label(status_frame, textvariable=self._status_var,
                 fg=W95_SHADOW, bg=W95_BG,
                 font=W95_FONT, anchor="w").pack(side="left", padx=4, pady=1)

        # ── Subtitle canvas — dark gray, no border ────────────────────────────
        self._sub_canvas = tk.Canvas(self._outer, width=WINDOW_W, height=130,
                                     bg="#a0a0a0", bd=2, relief="sunken",
                                     highlightthickness=0)
        self._sub_canvas.pack(fill="x", padx=4, pady=(4, 0))
        self._subtitle = SubtitleRenderer(self._sub_canvas, font_size=17,
                                          bleep_player=self._bleep)

        # ── Input row — Win95 style ───────────────────────────────────────────
        input_frame = tk.Frame(self._outer, bg=W95_BG)
        input_frame.pack(fill="x", padx=4, pady=(6, 8))

        families = tkfont.families()
        if "Barrio" in families:
            input_font = tkfont.Font(family="Barrio", size=11)
        else:
            input_font = tkfont.Font(family="MS Sans Serif", size=8)

        self._input_var = tk.StringVar()
        self._input_box = tk.Entry(
            input_frame,
            textvariable=self._input_var,
            font=input_font,
            bg=W95_INPUT_BG, fg=W95_TEXT,
            insertbackground=W95_TEXT,
            relief="sunken", bd=2,
        )
        self._input_box.pack(side="left", fill="x", expand=True, ipady=6)
        self._input_box.bind("<Return>", self._on_user_input)

        tk.Button(
            input_frame, text="OK",
            font=W95_FONT_BOLD,
            bg=W95_BTN_BG, fg=W95_TEXT,
            activebackground=W95_BTN_ACT, activeforeground=W95_BTN_AFG,
            relief="raised", bd=2, padx=10, pady=5,
            command=self._on_user_input,
        ).pack(side="left", padx=(4, 0))

    def _drag_start(self, e):
        self._drag_x, self._drag_y = e.x_root, e.y_root

    def _drag_motion(self, e):
        dx = e.x_root - self._drag_x
        dy = e.y_root - self._drag_y
        self.root.geometry(f"+{self.root.winfo_x()+dx}+{self.root.winfo_y()+dy}")
        self._drag_x, self._drag_y = e.x_root, e.y_root

    def _minimize(self):
        """Minimize the overrideredirect window.
        On Windows, overrideredirect windows can't be iconified directly — we
        temporarily restore normal chrome, iconify, then re-apply our settings
        once the window is mapped again. A short delay avoids a race with DWM."""
        try:
            self.root.overrideredirect(False)
            self.root.iconify()
        except Exception:
            return
        def _bind_restore():
            def _on_map(event):
                try:
                    if self.root.state() != "iconic":
                        self.root.overrideredirect(True)
                        self.root.attributes("-topmost", True)
                        self.root.lift()
                        self.root.unbind("<Map>")
                except Exception:
                    pass
            self.root.bind("<Map>", _on_map)
        self.root.after(250, _bind_restore)

    def _on_gif_click(self, event=None):
        """Handle a click on the Agetha gif — sends a hidden touch message to the AI.
        A 10-second cooldown prevents spamming."""
        now = time.time()
        if now - self._last_touch_time < 10.0:
            return   # still in cooldown, silently ignore
        self._last_touch_time = now
        # Don't interrupt an ongoing AI response or block the input box permanently
        if self._input_box["state"] == "disabled":
            return
        self._persistent_mood = None
        threading.Thread(
            target=self._ai_tick,
            kwargs={"user_message": "__touch__"},
            daemon=True,
        ).start()

    def _on_user_input(self, event=None):
        text = self._input_var.get().strip()
        if not text:
            return
        if self._input_box["state"] == "disabled":
            return
        self._input_var.set("")
        self._input_box.config(state="disabled")
        # Clear any sticky mood — new interaction resets expression
        self._persistent_mood = None
        threading.Thread(target=self._ai_tick, kwargs={"user_message": text}, daemon=True).start()

    def _re_enable_input(self):
        self._input_box.config(state="normal")
        self._input_box.focus_set()

    def _preload_gifs(self):
        """Load all GIFs without blocking the main thread.

        Phase 1 (background thread): PIL open/convert/resize/composite for every GIF.
        Phase 2 (main thread, via after()):  ImageTk.PhotoImage creation + GifPlayer init.
        The loading label stays visible throughout Phase 1 so users never see a black screen.
        """
        static_vals = list(self.EXTRA_STATIC_GIFS.values()) if getattr(self, 'EXTRA_STATIC_GIFS', None) else []
        # Use dict.fromkeys to preserve order while deduplicating (excited shares happy.gif)
        all_names = list(dict.fromkeys(
            self.IDLE_GIFS + self.TALKING_GIFS + list(self.EXTRA_GIFS.values()) + static_vals
        ))

        def _phase1():
            """Run entirely in a background thread — no Tk calls allowed here.
            Each GIF is decoded in parallel via a ThreadPoolExecutor so PIL
            open/convert/resize/composite work for all assets happens at the same time.
            """
            from concurrent.futures import ThreadPoolExecutor, as_completed

            results: dict[str, tuple[list, list]] = {}  # name → (pil_frames, delays)
            missing: list[str] = []

            # Tell the progress bar how many total steps to expect (3 init + N gifs)
            try:
                self.root.after(0, lambda: setattr(self, '_load_total', 3 + len(all_names)))
            except Exception:
                pass

            # Separate existing from missing up front so we only submit real files
            to_load = []
            for name in all_names:
                asset_path = ASSETS / name
                if asset_path.exists():
                    to_load.append((name, str(asset_path)))
                else:
                    print(f"[WARN] Missing asset: {asset_path}")
                    missing.append(name)
                    try:
                        self._advance_progress(f"Missing {name}…")
                    except Exception:
                        pass

            # Use as many workers as there are GIFs (capped at 8 to avoid over-subscription)
            n_workers = min(len(to_load), 8) if to_load else 1

            def _load_one(name_and_path):
                n, p = name_and_path
                frames, delays = _load_gif_frames_offthread(p)
                try:
                    self._advance_progress(f"Loading {n}…")
                except Exception:
                    pass
                return n, frames, delays

            with ThreadPoolExecutor(max_workers=n_workers) as pool:
                futures = {pool.submit(_load_one, item): item[0] for item in to_load}
                for fut in as_completed(futures):
                    try:
                        n, frames, delays = fut.result()
                        results[n] = (frames, delays)
                    except Exception as e:
                        n = futures[fut]
                        print(f"[GifPlayer] Failed to load {n}: {e}")

            # Hand off to main thread for Phase 2
            self.root.after(0, lambda: _phase2(results, missing))

        def _phase2(results: dict, missing: list):
            """Run on the main thread — creates ImageTk objects and GifPlayer instances."""
            for name, (pil_frames, delays) in results.items():
                try:
                    self._gif_cache[name] = GifPlayer(
                        self._gif_label, name, self.root.after,
                        pil_frames=pil_frames, delays=delays,
                    )
                except Exception as e:
                    print(f"[GifPlayer] Failed to create player for {name}: {e}")

            if missing:
                msg = "Missing asset files in the assets/ folder:\n" + "\n".join(missing[:8])
                if len(missing) > 8:
                    msg += f"\n...and {len(missing) - 8} more."
                native_error_popup("Agetha — Missing Assets", msg)

            # Phase 2 complete — now safe to remove loading label and start wake sequence
            try:
                if hasattr(self, "_loading_label") and self._loading_label:
                    self._loading_label.destroy()
                    self._loading_label = None
            except Exception:
                pass
            try:
                self._start_wake_sequence()
            except Exception as e:
                print(f"[BackgroundInit] start_wake_sequence failed: {e}")
                native_error_popup("Agetha — Startup Error", f"Startup sequence failed:\n{e}")

        threading.Thread(target=_phase1, daemon=True).start()

    def _init_background(self):
        """Run heavy initialization off the main thread."""
        try:
            bleep = None
            screen = None
            ai = None

            def _show_error_popup(lines: list):
                """Thread-safe error reporter — native OS dialog only, no custom UI."""
                native_error_popup("Agetha — Error", "\n".join(lines))

            try:
                bleep = BleepPlayer()
            except Exception as e:
                print(f"[BackgroundInit] Bleep init failed: {e}")
                native_error_popup("Agetha — Audio Error", f"Audio init failed:\n{e}\n\nSound will be disabled.")
            try:
                self._advance_progress("Audio engine ready…")
            except Exception:
                pass
            try:
                screen = ScreenReader()
            except Exception as e:
                print(f"[BackgroundInit] ScreenReader init failed: {e}")
                native_error_popup("Agetha — Screen Reader Error", f"Screen reader failed to start:\n{e}\n\nScreen reading will be disabled.")
            try:
                self._advance_progress("Screen reader ready…")
            except Exception:
                pass
            try:
                ai = AIEngine(on_error=_show_error_popup)
            except Exception as e:
                print(f"[BackgroundInit] AIEngine init failed: {e}")
                native_error_popup("Agetha — AI Engine Error", f"AI engine failed to start:\n{e}")
            try:
                self._advance_progress("AI engine ready…")
            except Exception:
                pass

            # Apply the results on the main thread (UI-safe operations there)
            def _finish():
                try:
                    self._bleep = bleep
                    self._screen = screen
                    self._ai = ai
                    # Attach bleep to subtitle renderer
                    try:
                        if hasattr(self, "_subtitle") and self._subtitle:
                            self._subtitle._bleep = self._bleep
                    except Exception:
                        pass
                    # Kick off GIF preloading — it handles the loading label and
                    # wake sequence itself once PIL work is done off the main thread.
                    try:
                        self._preload_gifs()
                    except Exception as e:
                        print(f"[BackgroundInit] preload_gifs failed: {e}")
                        native_error_popup("Agetha — Asset Error", f"Failed to load GIF assets:\n{e}")
                except Exception:
                    pass

            try:
                self.root.after(0, _finish)
            except Exception:
                _finish()
        except Exception as e:
            print(f"[BackgroundInit] Unexpected error: {e}")
            native_error_popup("Agetha — Unexpected Error", f"Unexpected startup error:\n{e}")

    def _play_gif(self, name: str):
        if self._current_gif_player:
            self._current_gif_player.stop()
        player = self._gif_cache.get(name)
        if player:
            self._current_gif_player = player
            player.play()
        else:
            print(f"[WARN] GIF not loaded: {name}")

    def _play_gif_once_then(self, anim_name: str, static_name: str, guard=None):
        """Play anim_name once, then switch to static_name (if guard passes)."""
        player = self._gif_cache.get(anim_name)
        static = self._gif_cache.get(static_name)
        if not player:
            return
        if self._current_gif_player and self._current_gif_player is not player:
            self._current_gif_player.stop()
        self._current_gif_player = player
        def _done():
            if guard is None or guard():
                if static:
                    if self._current_gif_player:
                        self._current_gif_player.stop()
                    self._current_gif_player = static
                    static.play()
        player.play_once(lambda: self.root.after(0, _done))

    def _play_gif_once_then_loop(self, anim_name: str, mood: str):
        """Play anim_name once, then loop it for as long as state is TALKING."""
        player = self._gif_cache.get(anim_name)
        if not player:
            self._start_talking_rotation()
            return
        if self._current_gif_player and self._current_gif_player is not player:
            self._current_gif_player.stop()
        self._current_gif_player = player
        def _done():
            if self._state == self.STATE_TALKING and self._persistent_mood == mood:
                if self._current_gif_player:
                    self._current_gif_player.stop()
                self._current_gif_player = player
                player.play()
        player.play_once(lambda: self.root.after(0, _done))

    def _start_talking_rotation(self):
        self._rotate_talking()

    def _rotate_talking(self):
        if self._state != self.STATE_TALKING:
            return
        available = [g for g in self.TALKING_GIFS if g in self._gif_cache]
        if available:
            self._play_gif(random.choice(available))
        delay = random.randint(1800, 3200)
        self._talking_rotate_job = self.root.after(delay, self._rotate_talking)

    def _stop_talking_rotation(self):
        if self._talking_rotate_job:
            self.root.after_cancel(self._talking_rotate_job)
            self._talking_rotate_job = None

    def _set_state(self, state: str, mood: str = "neutral"):
        # Cancel any pending loaf timer when changing state
        try:
            if getattr(self, "_loaf_job", None):
                self.root.after_cancel(self._loaf_job)
                self._loaf_job = None
        except Exception:
            self._loaf_job = None
        # If we were loafing, stop loaf state
        try:
            if getattr(self, "_is_loafing", False):
                self._is_loafing = False
        except Exception:
            self._is_loafing = False

        self._state = state
        labels = {
            self.STATE_SLEEPING: "",
            self.STATE_THINKING: "",
            self.STATE_IDLE:     "",
            self.STATE_TALKING:  "",
        }
        self._status_var.set(labels.get(state, state))

        self._stop_talking_rotation()
        if self._bleep:
            try:
                self._bleep.stop()
            except Exception:
                pass

        # Moods that should linger after speech ends (until next response or explicit idle)
        # Make 'happy' and 'thinking' sticky as well per user preference
        _STICKY_MOODS = {"sad", "angry", "happy", "thinking"}

        if state == self.STATE_SLEEPING:
            self._persistent_mood = None
            self._play_gif("sleeping.gif")
        elif state == self.STATE_THINKING:
            self._persistent_mood = None
            self._play_gif_once_then("thinking.gif", "thinking-static.gif",
                                     guard=lambda: self._state == self.STATE_THINKING)
        elif state == self.STATE_IDLE:
            # If we have a sticky mood carry it forward; a new response will clear it
            effective_mood = self._persistent_mood if self._persistent_mood else mood
            # Prefer static emotion image after animated playback
            static_name = None
            try:
                static_name = self.EXTRA_STATIC_GIFS.get(effective_mood)
            except Exception:
                static_name = None

            if static_name and static_name in self._gif_cache:
                self._play_gif(static_name)
            else:
                mood_gif = self.EXTRA_GIFS.get(effective_mood)
                if mood_gif and mood_gif in self._gif_cache:
                    self._play_gif(mood_gif)
                else:
                    available = [g for g in self.IDLE_GIFS if g in self._gif_cache]
                    if available:
                        self._play_gif(random.choice(available))
            # Schedule loaf.gif after 15 minutes of idle
            try:
                self._loaf_job = self.root.after(15 * 60 * 1000, self._enter_loaf)
            except Exception:
                self._loaf_job = None
        elif state == self.STATE_TALKING:
            if mood in _STICKY_MOODS:
                self._persistent_mood = mood
            else:
                self._persistent_mood = None
            mood_gif = self.EXTRA_GIFS.get(mood)
            static_name = self.EXTRA_STATIC_GIFS.get(mood)
            if mood != "neutral" and mood_gif and mood_gif in self._gif_cache:
                if static_name and static_name in self._gif_cache:
                    # Play emotion gif once, then loop it until speech ends
                    self._talking_emotion_looping = False
                    self._play_gif_once_then_loop(mood_gif, mood)
                else:
                    # No static — just loop the emotion gif
                    self._play_gif(mood_gif)
            else:
                self._start_talking_rotation()
            self._bleep.start_talking(tone=mood)

    def _enter_loaf(self):
        # Only enter loaf if still idle
        try:
            if self._state == self.STATE_IDLE and "loaf.gif" in self._gif_cache:
                self._play_gif("loaf.gif")
                self._is_loafing = True
        except Exception:
            pass

    def _start_wake_sequence(self):
        self._set_state(self.STATE_SLEEPING)
        self.root.after(8000, self._finish_wake)

    def _finish_wake(self):
        self._set_state(self.STATE_IDLE, "neutral")
        self.root.after(1000, self._schedule_screen_poll)

    def _schedule_screen_poll(self):
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        threading.Thread(target=self._ai_tick, daemon=True).start()

    def _reschedule_screen_poll(self):
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
        self._poll_job = self.root.after(SCREEN_POLL_INTERVAL_MS, self._schedule_screen_poll)

    def _ai_tick(self, user_message: str | None = None):
        is_user = user_message is not None

        self.root.after(0, lambda: self._input_box.config(state="disabled"))

        screen_text = ""
        if not is_user:
            screen_text = self._screen.capture_text()
            self._last_screen_text = screen_text
            # If angry keywords detected on screen, inject a synthetic user message
            # so the AI reacts even on an ambient poll
            if self._screen and getattr(self._screen, "has_angry_trigger", False):
                kws = ", ".join(self._screen.last_angry_keywords[:3])
                screen_text = f"[ANGRY_TRIGGER: {kws}]\n" + screen_text

        self.root.after(0, lambda: self._set_state(self.STATE_THINKING))

        def _on_token(raw_so_far: str):
            self._subtitle.show_thinking(raw_so_far)

        try:
            response = self._ai.query_streaming(
                screen_context=screen_text if not is_user else self._last_screen_text,
                user_message=user_message or "",
                on_token=_on_token,
            )
        except Exception as exc:
            err_str = str(exc)
            print(f"[AI_TICK] Unhandled exception: {err_str}")
            # Only show for non-groq-limit errors
            _groq_limit_keywords = ("rate_limit", "rate limit", "429", "quota", "groq_exhausted")
            is_groq_limit = any(kw in err_str.lower() for kw in _groq_limit_keywords)
            if not is_groq_limit:
                _short = err_str[:200] if len(err_str) > 200 else err_str
                native_error_popup("Agetha — Error", f"An error occurred:\n{_short}")
            self.root.after(0, self._re_enable_input)
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE))
            self._reschedule_screen_poll()
            return

        print("\n" + "─" * 52)
        if user_message and user_message != "__touch__":
            print(f"[USER]  {user_message}")
        print(f"[AI]    {json.dumps(response, ensure_ascii=False)}")
        print("─" * 52)

        self.root.after(0, self._re_enable_input)
        self._dispatch_response(response, user_message)

    # ── Emotion Sound Player ──────────────────────────────────────────────────
    def _play_emotion_sound(self, emotion: str) -> None:
        """Play a Windows built-in system sound matching the emotion.
        Falls back to a pygame beep on Linux/macOS or if winsound is unavailable."""
        _sys = platform.system()
        emotion = (emotion or "angry").lower()

        # Windows built-in sound event names
        _WIN_SOUNDS = {
            "angry":   "SystemHand",        # the classic Windows error/stop sound
            "error":   "SystemHand",
            "happy":   "SystemAsterisk",     # the info/asterisk chime
            "sad":     "SystemQuestion",     # Windows question dialog sound
            "startup": "SystemStart",        # Windows startup
            "notify":  "SystemNotification",
        }

        if _sys == "Windows":
            try:
                import winsound
                snd = _WIN_SOUNDS.get(emotion, "SystemHand")
                # SND_ALIAS plays a named Windows sound event; SND_ASYNC so it's non-blocking
                winsound.PlaySound(snd, winsound.SND_ALIAS | winsound.SND_ASYNC)
                print(f"[SOUND] Windows emotion sound: {snd} ({emotion})")
                return
            except Exception as e:
                print(f"[SOUND] winsound failed: {e}")
        elif _sys == "Darwin":
            try:
                import subprocess as _sp
                # macOS system sounds
                _mac = {"angry": "Basso", "error": "Basso", "happy": "Glass",
                        "sad": "Sosumi", "notify": "Ping"}
                snd = _mac.get(emotion, "Basso")
                _sp.Popen(["afplay", f"/System/Library/Sounds/{snd}.aiff"])
                print(f"[SOUND] macOS emotion sound: {snd} ({emotion})")
                return
            except Exception as e:
                print(f"[SOUND] macOS afplay failed: {e}")
        else:
            # Linux: try paplay with freedesktop sound theme names
            try:
                import subprocess as _sp
                _linux = {"angry": "dialog-error", "error": "dialog-error",
                          "happy": "bell", "sad": "dialog-warning",
                          "notify": "message-new-instant"}
                snd = _linux.get(emotion, "dialog-error")
                _sp.Popen(["paplay", f"/usr/share/sounds/freedesktop/stereo/{snd}.oga"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"[SOUND] Linux emotion sound: {snd} ({emotion})")
                return
            except Exception:
                pass

        # Universal pygame fallback
        try:
            _freq_map = {"angry": 185, "error": 185, "happy": 659,
                         "sad": 294, "startup": 523, "notify": 440}
            freq = _freq_map.get(emotion, 185)
            tone_key = {185: "angry", 659: "excited", 294: "sad",
                        523: "happy", 440: "neutral"}.get(freq, "neutral")
            if self._bleep:
                self._bleep.start_talking(tone=tone_key)
                threading.Timer(0.9, self._bleep.stop).start()
        except Exception as e:
            print(f"[SOUND] pygame fallback failed: {e}")

    def _dispatch_response(self, response: dict, user_message: str | None = None):
        # Clear any temporary loading subtitle when handling a response
        try:
            self.root.after(0, lambda: self._subtitle.clear())
        except Exception:
            pass
        command  = response.get("command", "idle")
        mood     = response.get("mood", "neutral")
        segments = response.get("segments", [])
        popup_msgs = response.get("popup", None)
        shutdown_requested = bool(response.get("shutdown", False))

        # Show Groq keys exhausted message in red subtitle area
        if response.get("groq_exhausted"):
            self.root.after(0, lambda: self._subtitle.show_message("You reached your limit with your Groq keys", "#ff4444"))
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE))
            self._reschedule_screen_poll()
            return

        def _speak_and_continue(resp_segments, resp_mood, resp_shutdown):
            if resp_segments:
                self.root.after(0, lambda: self._set_state(self.STATE_TALKING, resp_mood))
                self.root.after(0, lambda: self._subtitle.speak(
                    resp_segments,
                    on_done=lambda: self._on_speech_done(resp_shutdown)
                ))
            else:
                self.root.after(0, lambda: self._set_state(self.STATE_IDLE, resp_mood))
                self._reschedule_screen_poll()

        # Short-response static gif handling: if AI speaks a very short excited/happy/surprised
        # message (single short segment), show the static emotion gif during speech instead of
        # playing the full animated version.
        try:
            short_moods = {"happy", "excited", "surprised"}
            is_short = (
                command == "speak" and isinstance(segments, list) and len(segments) == 1 and
                isinstance(segments[0].get("text", ""), str) and len(segments[0].get("text", "").split()) <= 6
            )
            if is_short and mood in short_moods:
                static_name = (self.EXTRA_STATIC_GIFS.get(mood) if getattr(self, 'EXTRA_STATIC_GIFS', None) else None)
                if not static_name and mood in ("excited", "surprised"):
                    # fallback to happy static if a mood-specific static gif isn't present
                    static_name = (self.EXTRA_STATIC_GIFS.get("happy") if getattr(self, 'EXTRA_STATIC_GIFS', None) else None)
                # If we have a static image, show it while speaking
                if static_name and static_name in self._gif_cache:
                    self.root.after(0, lambda: self._set_state(self.STATE_TALKING, mood))
                    # small delay to let _set_state run, then force the static image
                    self.root.after(12, lambda: self._play_gif(static_name))
                    # speak as normal (subtitle will clear loading text earlier)
                    self.root.after(0, lambda: self._subtitle.speak(
                        segments,
                        on_done=lambda: self._on_speech_done(shutdown_requested)
                    ))
                    return
        except Exception:
            pass

        # --- New: show_error_gif handling (display gif indefinitely) ---
        if command == "show_error_gif":
            path = response.get("path", "") or str(ASSETS / "error.gif")
            try:
                # Try to load the provided gif directly; fall back to bundled asset
                gif_path = Path(path)
                if not gif_path.exists():
                    gif_path = ASSETS / "error.gif"
                name = str(gif_path)
                # create a temporary GifPlayer for this path and play it
                player = GifPlayer(self._gif_label, name, self.root.after)
                self._current_gif_player and self._current_gif_player.stop()
                self._current_gif_player = player
                player.play()
                # Keep window always-on-top and idle
                self.root.after(0, lambda: self._set_state(self.STATE_IDLE, "neutral"))
                # Do not reschedule normal polling while error gif is showing
                return
            except Exception as e:
                print(f"[ERROR_GIF] Failed to show error gif: {e}")

        # --- New: move_window handling ---
        if command == "move_window":
            # Accept explicit x,y or a direction string
            try:
                x = response.get("x", None)
                y = response.get("y", None)
                direction = response.get("direction", "").lower() if isinstance(response.get("direction", ""), str) else ""

                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                ww = self.root.winfo_width() or WINDOW_W
                wh = self.root.winfo_height() or WINDOW_H

                if x is not None and y is not None:
                    nx = int(x); ny = int(y)
                else:
                    curx = self.root.winfo_x(); cury = self.root.winfo_y()
                    if direction == "left":
                        nx = 10; ny = cury
                    elif direction == "right":
                        nx = max(0, sw - ww - 10); ny = cury
                    elif direction == "up":
                        nx = curx; ny = 10
                    elif direction == "down":
                        nx = curx; ny = max(0, sh - wh - 50)
                    elif direction == "center":
                        nx = max(0, (sw - ww) // 2); ny = max(0, (sh - wh) // 2)
                    else:
                        # default: move left
                        nx = 10; ny = cury

                self.root.geometry(f"+{nx}+{ny}")
                print(f"[UI] Moved window to: {nx},{ny}")
            except Exception as e:
                print(f"[UI] Failed to move window: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "request_path":
            hint = response.get("path_hint", "").strip()
            lines = [hint] if hint else (
                [seg.get("text", "") for seg in segments if seg.get("text", "")] or
                ["Path resolved automatically."]
            )
            self.root.after(0, lambda: AgethaPopup(self.root, lines[:4], mood))
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE, mood))
            self._reschedule_screen_poll()
            return

        if command == "create_folder":
            path = response.get("path", "").strip()
            if path:
                try:
                    os.makedirs(path, exist_ok=True)
                    print(f"[FS] Created folder: {path}")
                except Exception as e:
                    print(f"[FS] Failed to create folder {path}: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "create_file":
            file_path = response.get("file_path", "").strip()
            if not file_path:
                path      = response.get("path",      "").strip()
                file_name = response.get("file_name", "").strip()
                if path and file_name:
                    file_path = os.path.join(path, file_name)
            content = response.get("content", "")
            if file_path:
                try:
                    parent = os.path.dirname(file_path)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    print(f"[FS] Created file: {file_path}")
                except Exception as e:
                    print(f"[FS] Failed to create file {file_path}: {e}")
            else:
                print("[FS] create_file: missing path/file_name")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "delete_file":
            path = response.get("path", "").strip()
            if path:
                import shutil
                try:
                    p = Path(path)
                    if p.is_dir():
                        shutil.rmtree(p)
                        print(f"[FS] Deleted folder: {path}")
                    elif p.exists():
                        p.unlink()
                        print(f"[FS] Deleted file: {path}")
                    else:
                        print(f"[FS] delete_file: not found: {path}")
                except Exception as e:
                    print(f"[FS] Failed to delete {path}: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "rename_file":
            path     = response.get("path",     "").strip()
            new_name = response.get("new_name", "").strip()
            if path and new_name:
                try:
                    p    = Path(path)
                    dest = p.parent / new_name
                    p.rename(dest)
                    print(f"[FS] Renamed: {path} → {dest}")
                except Exception as e:
                    print(f"[FS] Failed to rename {path}: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command in ("list_dir", "list_directory"):
            req_path = response.get("path", "").strip() or str(self._ai._system_path)
            try:
                p = Path(req_path)
                if not p.exists():
                    lines = [f"[not found: {req_path}]"]
                elif not p.is_dir():
                    lines = [f"[not a directory: {req_path}]"]
                else:
                    entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
                    lines = [e.name + ("/" if e.is_dir() else "") for e in entries]
                    if not lines:
                        lines = ["[empty directory]"]
            except Exception as e:
                lines = [f"[error listing: {e}]"]

            self.root.after(0, lambda: AgethaPopup(self.root, lines[:12], mood))
            if not segments:
                segments = [{"text": f"{len(lines)} items in {req_path}", "pause": 0.0}]
            print(f"[FS] Listed {req_path}: {len(lines)} entries")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "set_clipboard":
            text = response.get("text", "").strip()
            if text:
                try:
                    self.root.clipboard_clear()
                    self.root.clipboard_append(text)
                    self.root.update()
                    print(f"[CLIP] Set clipboard: {text[:60]}")
                except Exception as e:
                    print(f"[CLIP] Failed: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "play_sound":
            sound_name = response.get("sound", "beep").strip().lower()
            _sound_map = {
                "beep":   440,
                "chime":  880,
                "error":  185,
                "notify": 523,
            }
            freq = _sound_map.get(sound_name, 440)
            try:
                self._bleep.start_talking(tone={v: k for k, v in {
                    "neutral": 440, "happy": 523, "excited": 659,
                    "sad": 294, "surprised": 587, "thinking": 370,
                    "whisper": 220, "angry": 185,
                }.items()}.get(freq, "neutral"))
                threading.Timer(0.8, self._bleep.stop).start()
                print(f"[SOUND] Played: {sound_name}")
            except Exception as e:
                print(f"[SOUND] Failed: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "take_screenshot":
            save_path = response.get("save_path", "").strip()
            if not save_path:
                import datetime as _dt
                ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                save_path = os.path.join(self._ai._system_path, f"screenshot_{ts}.png")
            try:
                img = self._screen.capture_image()
                if img:
                    img.save(save_path)
                    print(f"[SCREEN] Screenshot saved: {save_path}")
                else:
                    print("[SCREEN] capture_image returned None")
            except Exception as e:
                print(f"[SCREEN] Failed to save screenshot: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "show_notification":
            title   = response.get("title",   "Agetha").strip()
            message = response.get("message", "").strip()
            if message:
                try:
                    _sys = platform.system()
                    import subprocess as _sp
                    if _sys == "Darwin":
                        script = f'display notification "{message}" with title "{title}"'
                        _sp.Popen(["osascript", "-e", script])
                    elif _sys == "Linux":
                        _sp.Popen(["notify-send", title, message])
                    elif _sys == "Windows":
                        # Safely escape characters to prevent XML or PowerShell crashes
                        s_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "''")
                        s_msg = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "''")
                        
                        ps = (
                            f'[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;'
                            f'$xml = [Windows.Data.Xml.Dom.XmlDocument]::new();'
                            f"$xml.LoadXml('<toast><visual><binding template=\"ToastText02\"><text id=\"1\">{s_title}</text><text id=\"2\">{s_msg}</text></binding></visual></toast>');"
                            f'$toast = [Windows.UI.Notifications.ToastNotification]::new($xml);'
                            f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Agetha").Show($toast);'
                        )
                        # -WindowStyle Hidden prevents the blue powershell window from flashing
                        _sp.Popen(["powershell", "-WindowStyle", "Hidden", "-Command", ps], shell=False)
                    print(f"[NOTIFY] {title}: {message}")
                except Exception as e:
                    print(f"[NOTIFY] Failed: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "run_command":
            cmd_str = response.get("cmd", "").strip()
            use_shell = bool(response.get("shell", True))
            if cmd_str:
                try:
                    import subprocess as _sp
                    result_proc = _sp.run(
                        cmd_str, shell=use_shell, capture_output=True,
                        text=True, timeout=15
                    )
                    out = (result_proc.stdout or "").strip()
                    err = (result_proc.stderr or "").strip()
                    print(f"[CMD] Ran: {cmd_str}")
                    if out:
                        print(f"[CMD] stdout: {out[:200]}")
                    if err:
                        print(f"[CMD] stderr: {err[:200]}")
                except Exception as e:
                    print(f"[CMD] Failed to run '{cmd_str}': {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "read_document":
            doc_path = response.get("path", "").strip()
            doc_content = self._ai.read_document(doc_path) if doc_path else "[no path provided]"
            print(f"[DOC] Read '{doc_path}': {doc_content[:80]}")
            def _requery_with_doc():
                self.root.after(0, lambda: self._set_state(self.STATE_THINKING))
                def _on_token(raw_so_far: str):
                    self._subtitle.show_thinking(raw_so_far)
                follow = self._ai.query_streaming(
                    screen_context=self._last_screen_text,
                    user_message="",
                    doc_content=doc_content,
                    on_token=_on_token,
                )
                print(f"[AI]    {json.dumps(follow, ensure_ascii=False)}")
                self._dispatch_response(follow, user_message)
            threading.Thread(target=_requery_with_doc, daemon=True).start()
            return

        if command == "open_file":
            # Open any file with the OS default program (PDF, images, docx, etc.)
            file_path = response.get("path", "").strip()
            if file_path:
                try:
                    _sys = platform.system()
                    if _sys == "Windows":
                        os.startfile(file_path)
                    elif _sys == "Darwin":
                        import subprocess as _sp
                        _sp.Popen(["open", file_path])
                    else:
                        import subprocess as _sp
                        _sp.Popen(["xdg-open", file_path])
                    print(f"[FILE] Opened: {file_path}")
                except Exception as e:
                    print(f"[FILE] Failed to open {file_path}: {e}")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "write_file":
            file_path = response.get("file_path", "").strip()
            content   = response.get("content", "")
            mode      = response.get("mode", "overwrite").strip()
            if file_path:
                try:
                    result_msg = self._ai.write_file(file_path, content, mode)
                    print(f"[FS] write_file → {result_msg}")
                except Exception as e:
                    print(f"[FS] write_file error: {e}")
            else:
                print("[FS] write_file: missing file_path")
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "monitor_process":
            process_name = response.get("process_name", "").strip()
            if process_name:
                def _check_and_respond():
                    is_running = self._ai.monitor_process(process_name)
                    status = "running" if is_running else "not running"
                    print(f"[PROC] {process_name} is {status}")
                    # Feed the result back to the AI for a natural response
                    self.root.after(0, lambda: self._set_state(self.STATE_THINKING))
                    def _on_token(raw_so_far: str):
                        self._subtitle.show_thinking(raw_so_far)
                    follow = self._ai.query_streaming(
                        screen_context=self._last_screen_text,
                        user_message=f"[SYSTEM] Process '{process_name}' is {status}.",
                        on_token=_on_token,
                    )
                    print(f"[AI]    {json.dumps(follow, ensure_ascii=False)}")
                    self._dispatch_response(follow, user_message)
                threading.Thread(target=_check_and_respond, daemon=True).start()
            else:
                print("[PROC] monitor_process: no process_name provided")
                _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "play_emotion_sound":
            emotion = response.get("emotion", "angry").strip()
            threading.Thread(target=self._play_emotion_sound, args=(emotion,), daemon=True).start()
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "show_dialog":
            dlg_type = response.get("dialog_type", "info").strip().lower()
            dlg_title = response.get("title", "Agetha").strip()
            dlg_msg   = response.get("message", "").strip()
            if dlg_msg:
                def _show():
                    try:
                        import tkinter as _tk
                        from tkinter import messagebox as _mb
                        _r = _tk.Tk()
                        _r.withdraw()
                        _r.attributes("-topmost", True)
                        if dlg_type == "warning":
                            _mb.showwarning(dlg_title, dlg_msg, parent=_r)
                        elif dlg_type == "error":
                            _mb.showerror(dlg_title, dlg_msg, parent=_r)
                        elif dlg_type == "yesno":
                            _mb.askyesno(dlg_title, dlg_msg, parent=_r)
                        else:
                            _mb.showinfo(dlg_title, dlg_msg, parent=_r)
                        _r.destroy()
                        print(f"[DIALOG] {dlg_type}: {dlg_title} — {dlg_msg}")
                    except Exception as e:
                        print(f"[DIALOG] Failed: {e}")
                threading.Thread(target=_show, daemon=True).start()
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "open_app":
            app_name = response.get("app", "").strip()
            if app_name:
                print(f"[APP] Opening {app_name}...")
                try:
                    import subprocess as _sp
                    if platform.system() == "Windows":
                        try:
                            os.startfile(app_name)
                        except OSError:
                            _sp.Popen([app_name])
                    elif platform.system() == "Darwin":
                        _sp.Popen(["open", app_name])
                    else:
                        _sp.Popen([app_name])
                except Exception as e:
                    print(f"[APP] Failed to open {app_name}: {e}")
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE, mood))
            self._reschedule_screen_poll()
            return

        if command == "force_close":
            target = (response.get("app", "") or response.get("process", "") or response.get("name", "")).strip()
            if target:
                try:
                    import subprocess as _sp
                    if platform.system() == "Windows":
                        proc_name = os.path.basename(target)
                        _sp.run(["taskkill", "/IM", proc_name, "/F"], capture_output=True, check=False)
                    else:
                        _sp.run(["pkill", "-f", target], check=False)
                    print(f"[APP] Force-closed: {target}")
                except Exception as e:
                    print(f"[APP] Failed to force-close {target}: {e}")
            else:
                print("[APP] force_close: no target provided")
            if not segments:
                segments = [{"text": "Talk to me.", "pause": 0.0}]
            _speak_and_continue(segments, mood, shutdown_requested)
            return

        if command == "open_browser":
            url    = response.get("url",    "").strip()
            search = response.get("search", "").strip()
            engine = response.get("engine", "google").strip()
            if not url and search:
                _engines = {
                    "google":     "https://www.google.com/search?q=",
                    "duckduckgo": "https://duckduckgo.com/?q=",
                    "bing":       "https://www.bing.com/search?q=",
                }
                url = _engines.get(engine, _engines["google"]) + search.replace(" ", "+")
                print(f"[BROWSER] Searching: {search} ({engine})")
            if url:
                try:
                    webbrowser.open(url)
                except Exception as e:
                    print(f"[BROWSER] Failed: {e}")
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE, mood))
            self._reschedule_screen_poll()
            return

        if command == "request_screen_read":
            print("[SCREEN] AI requesting screen read...")
            screen_text = self._screen.capture_text()
            self._last_screen_text = screen_text
            print(f"[SCREEN] Captured {len(screen_text)} chars")
            def _requery_with_screen():
                self.root.after(0, lambda: self._set_state(self.STATE_THINKING))
                def _on_token(raw_so_far: str):
                    self._subtitle.show_thinking(raw_so_far)
                follow = self._ai.query_streaming(
                    screen_context=screen_text,
                    user_message=user_message or "",
                    on_token=_on_token,
                )
                print(f"[AI]    {json.dumps(follow, ensure_ascii=False)}")
                self._dispatch_response(follow, user_message)
            threading.Thread(target=_requery_with_screen, daemon=True).start()
            return

        if popup_msgs and isinstance(popup_msgs, list) and len(popup_msgs) > 0:
            self.root.after(0, lambda: AgethaPopup(self.root, popup_msgs, mood))
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE, mood))
            self._reschedule_screen_poll()
            return

        if command == "wake_user" and segments:
            self.root.after(0, lambda: self._set_state(self.STATE_TALKING, mood))
            self.root.after(0, lambda: self._subtitle.speak(
                segments,
                on_done=lambda: self._on_speech_done(shutdown_requested)
            ))
        elif command == "speak" and segments:
            # Clear any loading subtitle immediately as we begin speaking
            try:
                self.root.after(0, lambda: self._subtitle.clear())
            except Exception:
                pass
            self.root.after(0, lambda: self._set_state(self.STATE_TALKING, mood))
            self.root.after(0, lambda: self._subtitle.speak(
                segments,
                on_done=lambda: self._on_speech_done(shutdown_requested)
            ))
        else:
            # Explicit idle response — clear any sticky mood so we return to normal
            self._persistent_mood = None
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE, mood))
            self._reschedule_screen_poll()

    def _on_speech_done(self, shutdown: bool = False):
        # _persistent_mood already set — _set_state(STATE_IDLE) picks it up
        self.root.after(0, lambda: self._set_state(self.STATE_IDLE))
        if shutdown:
            self.root.after(50, self._shutdown)
        else:
            self.root.after(0, self._reschedule_screen_poll)

    def _shutdown(self):
        self._stop_talking_rotation()
        self._bleep.stop()
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        self.root.quit()

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self._bleep.stop()


def _early_config_check():
    """
    Run before pygame or any heavy import. If config.txt is missing,
    create it, show the setup popup, and exit — before pygame can interfere.
    """
    import sys
    from pathlib import Path

    if getattr(sys, "frozen", False):
        base = Path(sys.argv[0]).resolve().parent
    else:
        base = Path(__file__).parent

    config_path = base / "config.txt"
    if config_path.exists():
        return  # Nothing to do

    default_config = """# Agetha version 4.0.2 config file, @tomiszivacs on TikTok
    
    # Set to "yes" to use a local AI model via Ollama instead of Groq. Make sure to set LOCAL_AI_MODEL if enabling.
    USE_LOCAL_AI = no
    
    # Groq configuration (make sure to use separate accounts per key to avoid rate limits)
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
    # Groq model configuration, stupider models may have more forgiving rate limits.
    GROQ_MODEL = llama-3.3-70b-versatile
    
    # Local AI configuration (using Ollama, make sure to have a compatible model downloaded)
    LOCAL_AI_MODEL = 
    LOCAL_AI_TIMEOUT = 30

    # If you are unsure what to put here, run `ollama list` in a terminal to see installed models.
    # If LOCAL_AI_MODEL is incorrect or the model isn't installed, Agetha will
    # disable local AI and fall back (which can cause repeated idle responses).

    # Let Agetha run commands on your machine?
    ENABLE_COMMAND_EXECUTION = yes
    
    # How many characters of stored memories to include for Agetha? (The higher, the more context but also the more expensive the prompts)
    MEMORY_CHARS = 600
    # How many previous interactions to keep in history? (The higher, the more context but also the more expensive the prompts)
    HISTORY_LIMIT = 6
    # How many characters Agetha can read from a file? (The higher, the more Agetha can understand documents but also the more expensive the prompts)
    FILE_READ_CHARS = 200
    # Animation speed multiplier for GIFs (lower = faster, higher = slower). Default: 0.6
    ANIMATION_SPEED = 0.6
"""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(default_config, encoding="utf-8")
    print(f"[Agetha] Created config.txt at {config_path}")
    print("[Agetha] Please fill in your API keys and restart.")

    msg   = "Please configure Agetha with your API keys.\nRead the README.txt for setup guide."
    title = "Agetha \u2014 First Run"
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, title, 0x40 | 0x1000)
    except Exception:
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            messagebox.showinfo(title, msg, parent=root)
            root.destroy()
        except Exception as e:
            print(f"[Agetha] Could not show popup: {e}")

    sys.exit(0)


if __name__ == "__main__":
    _early_config_check()
    app = CompanionApp()
    app.run()
