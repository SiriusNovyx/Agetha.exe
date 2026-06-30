"""
Desktop AI Companion - Main Application
Requires: pip install pillow pyautogui pytesseract numpy pygame requests
Assets folder must contain: idle-1.gif, idle-2.gif, idle-3.gif,
  talking-1.gif, talking-2.gif, talking-3.gif,
  thinking.gif, sleeping.gif, happy.gif, surprised.gif, sad.gif, angry.gif
  (excited mood reuses happy.gif — no separate excited.gif needed)
Font: barrio.ttf must be in assets/ folder
"""

import sys
import re
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
from typing import Callable
from PIL import Image, ImageTk, ImageSequence

# Platform Detection Setup
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

# Conditional Win32 imports for Linux safety
ctypes = None
if IS_WINDOWS:
    import ctypes
    try:
        import ctypes.wintypes
    except ImportError:
        pass

# Safe Pygame import
try:
    import pygame
    PYGAME_OK = True
except ImportError:
    PYGAME_OK = False

from ai_engine import AIEngine
from screen_reader import ScreenReader
from command_guard import CommandGuard
from voice_input import (
    VoiceInput, MicPickerDialog, list_microphones,
    load_mic_settings, save_mic_settings,
)
from utils import (
    native_error_popup, logger, BASE_DIR, WINDOW_W, WINDOW_H,
    TOUCH_COOLDOWN_SEC, WAKE_DELAY_MS, LOAF_TIMER_MS, SCREEN_POLL_INTERVAL_MS,
)
from app_config import get_settings
from window_control import ease_out_cubic

_SETTINGS = get_settings()

BASE_DIR = BASE_DIR
ASSETS      = BASE_DIR / "assets"
FONT_PATH   = ASSETS / "barrio.ttf"


GIF_W    = 340
GIF_H    = 300

# Phase 2: Attention-snap system
# Moods that qualify to trigger a center-snap during ambient polls
_ATTENTION_MOODS = {"manic", "angry", "paranoid", "dominant", "surprised", "excited"}

# Per-mood inactivity threshold (seconds) before Agetha snaps to center.
_MOOD_SNAP_THRESHOLDS: dict[str, int] = _SETTINGS.mood_snap_thresholds()

# ── Phase 2: ctypes external window helper ────────────────────────────────────
def _find_window_hwnd(partial_name: str) -> int | None:
    """Find the first visible window whose title contains partial_name (case-insensitive)."""
    from window_control import find_window_hwnd
    return find_window_hwnd(partial_name)


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
    """Undertale-style 8-bit bleeps with Phase 2 deep emotional audio stratification.

    Each mood has a distinct audio profile controlling pitch, interval, volume and pattern:
      manic       — hyper-speed random pitch 600–900 Hz (4–12 ms intervals)
      melancholic — ultra-slow 120 Hz drone (200–320 ms intervals, low volume)
      paranoid    — rapid bursts of 2–6 bleeps followed by sudden silence
      vulnerable  — soft 261 Hz, slow (75–120 ms), quiet
      dominant    — deep 110 Hz, slow (100–160 ms), full volume
    """

    SAMPLE_RATE = 44100

    # (base_freq, min_interval_s, max_interval_s, volume_0_to_1)
    _MOOD_PROFILES: dict[str, tuple[int, float, float, float]] = {
        "neutral":     (440,  0.030, 0.055, 0.28),
        "happy":       (523,  0.028, 0.050, 0.30),
        "excited":     (659,  0.022, 0.040, 0.32),
        "sad":         (294,  0.060, 0.100, 0.20),
        "surprised":   (587,  0.025, 0.045, 0.32),
        "thinking":    (370,  0.045, 0.080, 0.22),
        "whisper":     (220,  0.055, 0.095, 0.14),
        "angry":       (185,  0.018, 0.035, 0.35),
        # Phase 2
        "manic":       (750,  0.004, 0.012, 0.36),
        "melancholic": (120,  0.200, 0.320, 0.16),
        "paranoid":    (330,  0.010, 0.030, 0.26),
        "vulnerable":  (261,  0.075, 0.120, 0.14),
        "dominant":    (110,  0.100, 0.160, 0.40),
    }

    def __init__(self):
        self._stop_event = threading.Event()
        self._paused = False
        self._thread: threading.Thread | None = None
        # Cache key is (freq, volume_rounded) so different-volume bleeps don't collide
        self._cache: dict[tuple[int, float], "pygame.mixer.Sound"] = {}
        self._mixer_ready = False
        self._current_tone = "neutral"

        if not PYGAME_OK:
            print("[BleepPlayer] pygame is not installed — audio disabled.")
            return

        # Run pygame mixer init in a background thread — on Windows 11, SDL2's
        # audio device enumeration can deadlock the main thread indefinitely.
        t = threading.Thread(target=self._init_mixer, daemon=True)
        t.start()
        t.join(timeout=5.0)
        if not self._mixer_ready:
            print("[BleepPlayer] WARNING: pygame mixer init timed out — audio disabled.")

    def _init_mixer(self):
        if not PYGAME_OK:
            return
        try:
            pygame.mixer.pre_init(self.SAMPLE_RATE, -16, 1, 256)
            pygame.mixer.init()
            self._mixer_ready = True
        except Exception as e:
            print(f"[BleepPlayer] mixer init error: {e}")

    def _make_bleep(self, freq: int, volume: float = 0.28) -> "pygame.mixer.Sound | None":
        if not self._mixer_ready:
            return None
        key = (freq, round(volume, 2))
        if key in self._cache:
            return self._cache[key]

        import array as arr
        duration  = 0.042
        n_samples = int(self.SAMPLE_RATE * duration)
        buf       = arr.array("h", [0] * n_samples)

        for i in range(n_samples):
            t = i / self.SAMPLE_RATE
            wave = 1.0 if math.sin(2 * math.pi * freq * t) >= 0 else -1.0
            env  = math.exp(-t * 40)
            buf[i] = int(wave * env * volume * 32767)

        sound = pygame.mixer.Sound(buffer=buf)
        self._cache[key] = sound
        return sound

    def start_talking(self, tone: str = "neutral"):
        if not self._mixer_ready:
            return
        self.stop()
        self._stop_event.clear()
        self._current_tone = tone
        self._thread = threading.Thread(target=self._loop, args=(tone,), daemon=True)
        self._thread.start()

    def _loop(self, tone: str):
        """Drive audio playback. Each mood has a distinct pattern:
        - manic:       hyper random pitch bursts
        - melancholic: slow low drone
        - paranoid:    rapid clusters with abrupt silence gaps
        - dominant:    deep resonant slow hits
        - all others:  standard steady bleep
        """
        profile = self._MOOD_PROFILES.get(tone, self._MOOD_PROFILES["neutral"])
        base_freq, min_int, max_int, vol = profile

        if tone == "manic":
            # Randomise pitch between 600–900 Hz at hyper-speed to evoke instability
            while not self._stop_event.is_set():
                if self._paused:
                    time.sleep(0.01)
                    continue
                freq = random.randint(600, 900)
                snd = self._make_bleep(freq, vol)
                if snd:
                    snd.play()
                time.sleep(random.uniform(min_int, max_int))

        elif tone == "melancholic":
            # Ultra-slow, ultra-low drone — barely alive
            snd = self._make_bleep(base_freq, vol)
            while not self._stop_event.is_set():
                if self._paused:
                    time.sleep(0.05)
                    continue
                if snd:
                    snd.play()
                time.sleep(random.uniform(min_int, max_int))

        elif tone == "paranoid":
            # Rapid bursts (2–6 bleeps) then sudden silence — anxious, erratic
            snd = self._make_bleep(base_freq, vol)
            while not self._stop_event.is_set():
                if self._paused:
                    time.sleep(0.02)
                    continue
                burst = random.randint(2, 6)
                for _ in range(burst):
                    if self._stop_event.is_set():
                        break
                    if snd:
                        snd.play()
                    time.sleep(random.uniform(0.008, 0.022))
                # Sudden silence gap — the paranoia breath
                time.sleep(random.uniform(0.04, 0.28))

        elif tone == "dominant":
            # Deep, slow, resonant — each bleep is a statement
            snd = self._make_bleep(base_freq, vol)
            while not self._stop_event.is_set():
                if self._paused:
                    time.sleep(0.03)
                    continue
                if snd:
                    snd.play()
                time.sleep(random.uniform(min_int, max_int))

        else:
            # Standard loop — covers neutral, happy, excited, sad, surprised,
            # thinking, whisper, angry, vulnerable and any unknown tone
            snd = self._make_bleep(base_freq, vol)
            if snd is None:
                return
            while not self._stop_event.is_set():
                if self._paused:
                    time.sleep(0.02)
                    continue
                snd.play()
                time.sleep(random.uniform(min_int, max_int))

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def stop(self):
        self._stop_event.set()
        self._paused = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.4)

    def play_file(self, path: str) -> None:
        if not self._mixer_ready or not PYGAME_OK:
            return
        try:
            snd = pygame.mixer.Sound(path)
            snd.play()
        except Exception as exc:
            logger.warning(f"BleepPlayer.play_file failed: {exc}")


def _read_animation_speed() -> float:
    return get_settings().animation_speed


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
        self._paused  = False
        self._was_running = False
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

    def pause(self):
        """Stop scheduling frames while hidden/minimized; resume() continues."""
        if self._paused:
            return
        self._paused = True
        self._was_running = self._running
        if self._job:
            try:
                self._label.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def resume(self):
        if not self._paused:
            return
        self._paused = False
        if self._was_running and self._frames:
            self._running = True
            self._tick()

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
        self._paused = False
        self._was_running = False
        self._once_counter = None
        self._on_once_done = None
        if self._job:
            try:
                self._label.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _tick(self):
        if self._paused or not self._running or not self._frames:
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

    CHAR_DELAY = get_settings().subtitle_char_delay
    _font_cache: dict[int, tkfont.Font] = {}

    def __init__(self, canvas: tk.Canvas, font_size: int = 17, bleep_player=None):
        self._canvas     = canvas
        self._font_size  = font_size
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._bleep = bleep_player

        self._canvas_w = WINDOW_W
        self._canvas_h = 130
        self._shadow_id: int | None = None
        self._text_id: int | None = None
        self._last_layout: dict | None = None
        self._draw_pending = False
        self._pending_text = ""
        self._pending_color = "#ffffff"

        self._canvas.config(bg="#a0a0a0")
        self._font = self._load_font(font_size)
        self._canvas.bind("<Configure>", self._on_configure, add=True)

    def _on_configure(self, event):
        if event.width > 1 and event.height > 1:
            self._canvas_w = event.width
            self._canvas_h = event.height
            self._last_layout = None

    def _load_font(self, size: int) -> tkfont.Font:
        if size in SubtitleRenderer._font_cache:
            return SubtitleRenderer._font_cache[size]
        available = tkfont.families()
        for name in ("Barrio", "barrio"):
            if name in available:
                font = tkfont.Font(family=name, size=size)
                SubtitleRenderer._font_cache[size] = font
                return font
        font = tkfont.Font(family="Courier", size=size, weight="bold")
        SubtitleRenderer._font_cache[size] = font
        return font

    def _reset_items(self):
        self._shadow_id = None
        self._text_id = None
        self._last_layout = None

    def clear(self):
        self._canvas.delete("all")
        self._reset_items()

    def _schedule_draw(self, text: str, color: str = "#ffffff"):
        """Coalesce redraws onto the main thread (one flush per event-loop turn)."""
        self._pending_text = text
        self._pending_color = color
        if self._draw_pending:
            return
        self._draw_pending = True
        self._canvas.after(0, self._flush_draw)

    def _flush_draw(self):
        self._draw_pending = False
        self._draw(self._pending_text, self._pending_color)

    def show_thinking(self, raw_text: str):
        """Show streaming tokens in grey while waiting for a response."""
        texts = re.findall(r'"text"\s*:\s*"([^"]*)', raw_text)
        preview = " ".join(texts).strip() or "…"
        self._schedule_draw(preview, color="#888899")

    def show_message(self, text: str, color: str = "#ffffff", duration: float = 6.0):
        """Immediately show a static subtitle message (optionally auto-clears)."""
        self.stop()
        self._canvas.after(0, lambda: self._draw(text, color))
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
        for seg in segments:
            if self._stop_event.is_set():
                break
            chunk = seg.get("text", "").strip()
            pause = seg.get("pause", 0.0)
            if full_text and not full_text.endswith(" "):
                full_text += " "
            # Typewriter timing per character; redraw only at word boundaries
            for i, ch in enumerate(chunk):
                if self._stop_event.is_set():
                    break
                full_text += ch
                at_word_end = ch.isspace() or i == len(chunk) - 1
                if at_word_end:
                    self._schedule_draw(full_text)
                time.sleep(self.CHAR_DELAY)
            if pause > 0 and not self._stop_event.is_set():
                if self._bleep:
                    self._bleep.pause()
                time.sleep(pause)
                if self._bleep:
                    self._bleep.resume()
        try:
            if self._bleep:
                self._bleep.stop()
        except Exception:
            pass
        if on_done:
            self._canvas.after(0, on_done)

    def _compute_layout(self, text: str, color: str) -> dict | None:
        cw = self._canvas_w
        ch = self._canvas_h
        max_w = max(40, cw - 24)
        max_lines = 3
        min_font_size = 8

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
            return None

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
                    chunks = [w[i:i + chars_per_line] for i in range(0, len(w), chars_per_line)]
                    parts.append(" ".join(chunks))
            candidate_words = "".join(parts).strip().split()

            if estimate_lines(candidate_words, chars_per_line) <= max_lines:
                break

            font_size -= 1
            font = self._load_font(font_size)

        candidate = " ".join(candidate_words)
        x = cw // 2
        y = 6

        while font_size >= min_font_size:
            try:
                tid = self._canvas.create_text(
                    -10000, -10000, text=candidate, fill=color,
                    font=font, anchor="n", width=max_w, justify="center",
                )
                bbox = self._canvas.bbox(tid)
                self._canvas.delete(tid)
                if bbox:
                    height = bbox[3] - bbox[1]
                    if height <= ch - 12:
                        y = max(6, (ch - height) // 2)
                        break
                    font_size -= 1
                    font = self._load_font(font_size)
                else:
                    break
            except Exception:
                break

        return {
            "candidate": candidate,
            "font": font,
            "font_size": font_size,
            "x": x,
            "y": y,
            "max_w": max_w,
            "color": color,
        }

    def _draw(self, text: str, color: str = "#ffffff"):
        layout = self._compute_layout(text, color)
        if not layout:
            self.clear()
            return

        reuse = (
            self._text_id is not None
            and self._last_layout is not None
            and self._last_layout.get("font_size") == layout["font_size"]
            and self._last_layout.get("max_w") == layout["max_w"]
        )

        if reuse:
            try:
                self._canvas.itemconfig(
                    self._shadow_id,
                    text=layout["candidate"],
                    font=layout["font"],
                )
                self._canvas.itemconfig(
                    self._text_id,
                    text=layout["candidate"],
                    fill=color,
                    font=layout["font"],
                )
                self._canvas.coords(self._shadow_id, layout["x"] + 2, layout["y"] + 2)
                self._canvas.coords(self._text_id, layout["x"], layout["y"])
                self._last_layout = layout
                return
            except Exception:
                self._reset_items()

        self._canvas.delete("all")
        try:
            self._shadow_id = self._canvas.create_text(
                layout["x"] + 2, layout["y"] + 2,
                text=layout["candidate"], fill="#000000",
                font=layout["font"], anchor="n",
                width=layout["max_w"], justify="center",
            )
            self._text_id = self._canvas.create_text(
                layout["x"], layout["y"],
                text=layout["candidate"], fill=color,
                font=layout["font"], anchor="n",
                width=layout["max_w"], justify="center",
            )
            self._last_layout = layout
        except Exception:
            self._reset_items()


class AgethaPopup:
    """Windows 95-style dialog popup spawned by Agetha."""

    def __init__(self, parent: tk.Tk, messages: list, mood: str = "neutral"):
        self._win = tk.Toplevel(parent)
        self._win.overrideredirect(True)   # we draw our own chrome
        try:
            self._win.attributes("-topmost", True)
        except Exception:
            pass
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

    WINDOW_W = WINDOW_W
    WINDOW_H = WINDOW_H
    _ATTENTION_MOODS = _ATTENTION_MOODS

    STATE_SLEEPING = "sleeping"
    STATE_THINKING = "thinking"
    STATE_IDLE     = "idle"
    STATE_TALKING  = "talking"

    IDLE_GIFS    = ["idle-1.gif", "idle-2.gif", "idle-3.gif"]
    TALKING_GIFS = ["talking-1.gif", "talking-2.gif", "talking-3.gif"]
    EXTRA_GIFS   = {
        "happy":      "happy.gif",
        "surprised":  "surprised.gif",
        "sad":        "sad.gif",
        "excited":    "happy.gif",    # excited shares happy.gif
        "angry":      "angry.gif",
        "thinking":   "thinking.gif",
        "sleeping":   "sleeping.gif",
        "loaf":       "loaf.gif",
        # Phase 2 — map new moods to nearest existing asset
        "manic":       "angry.gif",      # fast, intense → angry
        "melancholic": "sad.gif",        # deep sadness → sad
        "paranoid":    "thinking.gif",   # anxious scanning → thinking
        "vulnerable":  "sad.gif",        # exposed, soft → sad
        "dominant":    "angry.gif",      # powerful, threatening → angry
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

        self._dnd_ok = False
        try:
            from tkinterdnd2 import TkinterDnD
            self.root = TkinterDnD.Tk()
            self._dnd_ok = True
            logger.info("[DnD] tkinterdnd2 loaded — file drag-and-drop available")
        except Exception:
            self.root = tk.Tk()
            logger.info("[DnD] tkinterdnd2 not installed — drag-and-drop disabled")

        self.root.title(f"Agetha.exe v{_SETTINGS.app_version}")
        self.root.geometry(
            f"{WINDOW_W}x{WINDOW_H}+{_SETTINGS.window_start_x}+{_SETTINGS.window_start_y}"
        )
        self.root.configure(bg=W95_BG)
        self.root.overrideredirect(True)
        self.root.resizable(False, False)
        try:
            if _SETTINGS.window_topmost:
                self.root.attributes("-topmost", True)
        except Exception:
            pass

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
        self._last_touch_time: float = 0.0           # epoch time of last gif-click touch event
        self._last_direct_interaction_time: float = time.time()  # updated on keystroke OR gif-click
        self._guard = CommandGuard(self.root)
        self._cancel_event = threading.Event()
        self._ai_busy = False
        self._state_lock = threading.Lock()
        self._voice: VoiceInput | None = None
        self._mic_active = False
        self._dragging_file = False
        self._last_dragged_file = ""

        self._build_ui()
        self._bind_keystroke_tracking()   # Phase 2: track any key as direct interaction

        # Simple loading label — no progress bar, no multi-phase preloading
        self._loading_label = tk.Label(
            self._outer,
            text="Loading Agetha.exe…",
            fg=W95_SHADOW, bg=W95_BG,
            font=W95_FONT,
        )
        self._loading_label.place(relx=0.5, rely=0.5, anchor="center")

        # Stub so _init_background can still call _advance_progress without errors
        self._advance_progress = lambda status, steps=1: print(f"[INIT] {status}")

        # Flush the window so the label appears immediately (Windows DWM quirk)
        try:
            self.root.update_idletasks()
            self.root.update()
            self.root.deiconify()
            self.root.lift()
            self.root.update()
        except Exception:
            pass

        # Start heavy init (audio, screen reader, AI) on a background thread
        threading.Thread(target=self._init_background, daemon=True).start()

        self._drag_x = self._drag_y = 0
        self._win_x, self._win_y = _SETTINGS.window_start_x, _SETTINGS.window_start_y
        self._geom_anim_job = None
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
        if _SETTINGS.faster_mode:
            tk.Label(
                title_bar, text="FAST MODE",
                bg=W95_TITLE_BG, fg="#1a3a6b",
                font=("MS Sans Serif", 7, "bold"), anchor="w", padx=2,
            ).pack(side="left", fill="y")

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
        if _SETTINGS.enable_file_drag_drop and self._dnd_ok:
            try:
                self._gif_label.drop_target_register("DND_Files")  # type: ignore[attr-defined]
                self._gif_label.dnd_bind("<<DropEnter>>", self._on_file_drag_enter)  # type: ignore[attr-defined]
                self._gif_label.dnd_bind("<<DropLeave>>", self._on_file_drag_leave)  # type: ignore[attr-defined]
                self._gif_label.dnd_bind("<<Drop>>", self._on_file_drop)  # type: ignore[attr-defined]
            except Exception as exc:
                logger.warning(f"[DnD] Could not register drop target: {exc}")

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

        entry_wrapper = tk.Frame(input_frame, bg=W95_INPUT_BG, relief="sunken", bd=2)
        entry_wrapper.pack(side="left", fill="x", expand=True)

        self._input_box = tk.Entry(
            entry_wrapper,
            textvariable=self._input_var,
            font=input_font,
            bg=W95_INPUT_BG, fg=W95_TEXT,
            insertbackground=W95_TEXT,
            relief="flat", bd=0,
        )
        self._input_box.pack(fill="both", expand=True, ipady=6, padx=2)

        placeholder_font = tkfont.Font(family="MS Sans Serif", size=7)
        self._placeholder_lbl = tk.Label(
            entry_wrapper, text="", font=placeholder_font,
            bg=W95_INPUT_BG, fg="#888888", anchor="w", padx=4, pady=0,
        )
        self._placeholder_lbl.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._placeholder_lbl.bind("<Button-1>", lambda e: self._input_box.focus_set())
        self._input_box.bind("<FocusIn>", lambda e: self._update_placeholder(focused=True))
        self._input_box.bind("<FocusOut>", lambda e: self._update_placeholder(focused=False))
        self._input_var.trace_add("write", lambda *_: self._update_placeholder())
        self._input_box.bind("<Return>", self._on_user_input)
        self.root.bind("<Escape>", self._on_cancel_ai)

        if _SETTINGS.enable_voice:
            self._mic_btn_var = tk.StringVar(value="🎤")
            self._mic_btn = tk.Button(
                input_frame, textvariable=self._mic_btn_var,
                font=W95_FONT_BOLD, bg=W95_BTN_BG, fg=W95_TEXT,
                activebackground=W95_BTN_ACT, activeforeground=W95_BTN_AFG,
                relief="raised", bd=2, padx=6, pady=5,
                command=self._toggle_mic,
            )
            self._mic_btn.pack(side="left", padx=(2, 0))

        tk.Button(
            input_frame, text="OK",
            font=W95_FONT_BOLD,
            bg=W95_BTN_BG, fg=W95_TEXT,
            activebackground=W95_BTN_ACT, activeforeground=W95_BTN_AFG,
            relief="raised", bd=2, padx=10, pady=5,
            command=self._on_user_input,
        ).pack(side="left", padx=(4, 0))

    def _get_placeholder_text(self) -> str:
        try:
            if not self._ai:
                return "type here..."
            status = self._ai.get_token_status()
            if not status.get("using_groq"):
                if status.get("provider") == "openrouter":
                    return "OpenRouter  •  type here..."
                if status.get("provider") == "local":
                    return "local AI  •  type here..."
                return "type here..."
            idx = status.get("key_index", 1)
            total = status.get("key_count", 1)
            pct = status.get("pct_left", 100)
            return f"key {idx}/{total}  •  {pct}% tokens left"
        except Exception:
            return "type here..."

    def _update_placeholder(self, focused=None) -> None:
        if bool(self._input_var.get()):
            self._placeholder_lbl.place_forget()
        else:
            self._placeholder_lbl.config(text=self._get_placeholder_text())
            self._placeholder_lbl.place(relx=0, rely=0, relwidth=1, relheight=1)

    def _start_placeholder_refresh(self) -> None:
        def _tick():
            try:
                self._update_placeholder()
            except Exception:
                pass
            self.root.after(10000, _tick)
        self.root.after(10000, _tick)

    def _toggle_mic(self) -> None:
        if self._voice is None:
            settings = load_mic_settings()
            device_index = settings.get("mic_device_index")
            if device_index is None:
                mics = list_microphones()
                if not mics:
                    native_error_popup(
                        "Agetha — Microphone",
                        "No microphone devices found.\n"
                        "Connect a mic and ensure pyaudio is installed.\n"
                        "Run Medic_Checker with ENABLE_VOICE=yes.",
                    )
                    return
                picker = MicPickerDialog(self.root, mics)
                chosen = picker.wait()
                if chosen is None:
                    return
                device_index = chosen
                mic_name = next((n for i, n in mics if i == chosen), str(chosen))
                settings["mic_device_index"] = device_index
                settings["mic_device_name"] = mic_name
                save_mic_settings(settings)
                logger.info(f"[Voice] Microphone saved: [{device_index}] {mic_name}")

            self._voice = VoiceInput(
                on_text_callback=self._on_voice_text,
                device_index=device_index,
                use_local_stt=_SETTINGS.use_local_stt,
            )
            if not self._voice.available:
                native_error_popup(
                    "Agetha — Microphone",
                    self._voice.error or "SpeechRecognition unavailable.",
                )
                self._voice = None
                return

        if self._mic_active:
            self._mic_active = False
            if self._voice:
                self._voice.stop()
            self._mic_btn_var.set("🎤")
            self._mic_btn.config(bg=W95_BTN_BG, fg=W95_TEXT, activebackground=W95_BTN_BG)
            logger.info("[Voice] Microphone off")
        else:
            self._mic_active = True
            if self._voice:
                self._voice.start()
            self._mic_btn_var.set("🔴")
            self._mic_btn.config(bg="#cc0000", fg="#ffffff", activebackground="#990000")
            logger.info("[Voice] Microphone on — listening…")

    def _on_voice_text(self, text: str) -> None:
        def _send():
            self._input_var.set(text)
            self.root.update_idletasks()
            self._on_user_input()
        self.root.after(0, _send)

    def _on_file_drag_enter(self, event=None) -> None:
        if self._dragging_file:
            return
        self._dragging_file = True
        want_player = self._gif_cache.get("surprised.gif")
        if want_player:
            if self._current_gif_player:
                self._current_gif_player.stop()
            self._current_gif_player = want_player
            want_player.play()

    def _on_file_drag_leave(self, event=None) -> None:
        if not self._dragging_file:
            return
        self._dragging_file = False
        self._set_state(self.STATE_IDLE)

    def _on_file_drop(self, event=None) -> None:
        self._dragging_file = False
        try:
            file_path = getattr(event, "data", "") or ""
            file_path = file_path.strip().strip("{}")
            filename = Path(file_path).name if file_path else "unknown file"
        except Exception:
            filename = "a file"
            file_path = ""
        logger.info(f"[DnD] File dropped: {filename} at {file_path}")
        self._last_dragged_file = file_path if file_path else filename
        self._set_state(self.STATE_IDLE)
        if self._input_box["state"] == "disabled":
            return
        msg = (
            f'[system] file_dragged: "{filename}" (path: {file_path})'
            if file_path
            else f'[system] file_dragged: "{filename}"'
        )
        threading.Thread(target=self._ai_tick, kwargs={"user_message": msg}, daemon=True).start()

    def _update_token_status(self) -> None:
        try:
            if not self._ai:
                return
            status = self._ai.get_token_status()
            if status.get("using_groq"):
                key_info = f"Key {status['key_index']}/{status['key_count']}"
                pct = status.get("pct_left", 0)
                self._status_var.set(f"{key_info} | {pct}% left")
            self._update_placeholder()
        except Exception as exc:
            logger.debug(f"Token status update failed: {exc}")

    def _drag_start(self, e):
        if self._geom_anim_job is not None:
            try:
                self.root.after_cancel(self._geom_anim_job)
            except Exception:
                pass
            self._geom_anim_job = None
        self._drag_x, self._drag_y = e.x_root, e.y_root
        self._win_x, self._win_y = self.root.winfo_x(), self.root.winfo_y()

    def animate_geometry(
        self,
        target_x: int,
        target_y: int,
        *,
        duration_ms: int | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """Smooth move for Agetha's window — measure once, then only geometry writes."""
        smooth = _SETTINGS.window_move_smooth
        if duration_ms is None:
            duration_ms = _SETTINGS.window_move_duration_ms

        target_x, target_y = int(target_x), int(target_y)

        if self._geom_anim_job is not None:
            try:
                self.root.after_cancel(self._geom_anim_job)
            except Exception:
                pass
            self._geom_anim_job = None

        if not smooth or duration_ms <= 0:
            self._win_x, self._win_y = target_x, target_y
            self.root.geometry(f"+{target_x}+{target_y}")
            if on_done:
                on_done()
            return

        try:
            start_x = self.root.winfo_x()
            start_y = self.root.winfo_y()
        except Exception:
            start_x, start_y = self._win_x, self._win_y
        self._win_x, self._win_y = start_x, start_y

        dx, dy = target_x - start_x, target_y - start_y
        if abs(dx) + abs(dy) < 4:
            self._win_x, self._win_y = target_x, target_y
            self.root.geometry(f"+{target_x}+{target_y}")
            if on_done:
                on_done()
            return

        duration_s = duration_ms / 1000.0
        start_time = time.perf_counter()

        def _tick():
            elapsed = time.perf_counter() - start_time
            t = min(1.0, elapsed / duration_s)
            e = ease_out_cubic(t)
            nx = int(start_x + dx * e)
            ny = int(start_y + dy * e)
            self._win_x, self._win_y = nx, ny
            self.root.geometry(f"+{nx}+{ny}")
            if t < 1.0:
                self._geom_anim_job = self.root.after(16, _tick)
            else:
                self._geom_anim_job = None
                self._win_x, self._win_y = target_x, target_y
                self.root.geometry(f"+{target_x}+{target_y}")
                if on_done:
                    on_done()

        _tick()

    def _drag_motion(self, e):
        dx = e.x_root - self._drag_x
        dy = e.y_root - self._drag_y
        self._win_x += dx
        self._win_y += dy
        self.root.geometry(f"+{self._win_x}+{self._win_y}")
        self._drag_x, self._drag_y = e.x_root, e.y_root

    def _pause_gif_playback(self):
        if self._current_gif_player:
            self._current_gif_player.pause()

    def _resume_gif_playback(self):
        if self._current_gif_player:
            self._current_gif_player.resume()

    def _minimize(self):
        """Minimize the overrideredirect window.
        Handles Windows and Linux (compositors/window managers) safely."""
        self._is_minimized = True
        self._pause_gif_playback()
        if IS_WINDOWS:
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
                            try:
                                self.root.attributes("-topmost", True)
                            except Exception:
                                pass
                            self.root.lift()
                            self._is_minimized = False
                            self._resume_gif_playback()
                            self.root.unbind("<Map>")
                    except Exception:
                        pass
                self.root.bind("<Map>", _on_map)
            self.root.after(250, _bind_restore)
        else:
            try:
                self.root.overrideredirect(False)
                self.root.iconify()
            except Exception:
                pass
            def _bind_restore_linux():
                def _on_map(event):
                    try:
                        if self.root.state() != "iconic":
                            self.root.overrideredirect(True)
                            self.root.lift()
                            self._is_minimized = False
                            self._resume_gif_playback()
                            self.root.unbind("<Map>")
                    except Exception:
                        pass
                self.root.bind("<Map>", _on_map)
            self.root.after(250, _bind_restore_linux)

    def _on_gif_click(self, event=None):
        """Handle a click on the Agetha gif — sends a hidden touch message to the AI.
        A 10-second cooldown prevents spamming."""
        now = time.time()
        self._last_direct_interaction_time = now  # Phase 2: stamp interaction clock
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
        self._last_direct_interaction_time = time.time()  # Phase 2: any key = direct interaction
        text = self._input_var.get().strip()
        if not text:
            return
        if self._input_box["state"] == "disabled":
            return
        self._input_var.set("")
        self._input_box.config(state="disabled")
        if hasattr(self, "_placeholder_lbl"):
            self._placeholder_lbl.config(text="Processing...")
            self._placeholder_lbl.place(relx=0, rely=0, relwidth=1, relheight=1)
        # Clear any sticky mood — new interaction resets expression
        self._persistent_mood = None
        threading.Thread(target=self._ai_tick, kwargs={"user_message": text}, daemon=True).start()

    def _re_enable_input(self):
        self._input_box.config(state="normal")
        self._input_box.focus_set()
        try:
            self._update_placeholder()
        except Exception:
            pass

    # ── Phase 2: Attention-snap system ────────────────────────────────────────

    def _bind_keystroke_tracking(self):
        """Bind any key pressed in the entry box to update the interaction clock.
        Call this once after _build_ui() has created self._input_box."""
        try:
            def _on_any_key(event=None):
                self._last_direct_interaction_time = time.time()
            self._input_box.bind("<Key>", _on_any_key, add=True)
        except Exception as e:
            print(f"[InteractionClock] Could not bind keystroke tracking: {e}")

    def _maybe_snap_to_center(self, mood: str) -> None:
        """Called during ambient AI polls when Agetha returns an attention-seeking mood.

        Decision logic (runs on main thread via .after()):
          • Inactivity ≥ threshold for this mood  → center-snap, topmost, lift
          • Inactivity  < threshold               → drift to default side position

        The snap threshold is mood-severity-dependent: manic snaps after 2 min,
        melancholic waits 15 min. This makes her disruption feel proportional.
        """
        if mood not in _ATTENTION_MOODS or not _SETTINGS.enable_attention_snap:
            return
        threshold  = _MOOD_SNAP_THRESHOLDS.get(mood, 600)
        inactivity = time.time() - self._last_direct_interaction_time

        def _do_position():
            try:
                sw = self.root.winfo_screenwidth()
                sh = self.root.winfo_screenheight()
                if inactivity >= threshold:
                    nx = (sw - WINDOW_W) // 2
                    ny = (sh - WINDOW_H) // 2

                    def _after_snap():
                        try:
                            self.root.attributes("-topmost", True)
                        except Exception:
                            pass
                        self.root.lift()

                    self.animate_geometry(nx, ny, on_done=_after_snap)
                    print(
                        f"[SNAP] Center-snapped — mood={mood}, "
                        f"inactivity={inactivity:.0f}s, threshold={threshold}s"
                    )
                else:
                    nx = sw - WINDOW_W - 20
                    ny = 80
                    self.animate_geometry(nx, ny)
                    print(
                        f"[SNAP] Side-drift — mood={mood}, "
                        f"inactivity={inactivity:.0f}s < threshold={threshold}s"
                    )
            except Exception as e:
                print(f"[SNAP] Position error: {e}")

        self.root.after(0, _do_position)

    def _load_gifs_simple(self):
        """Decode GIF frames off-thread; build ImageTk on main thread."""
        static_vals = list(self.EXTRA_STATIC_GIFS.values()) if getattr(self, "EXTRA_STATIC_GIFS", None) else []
        all_names = list(dict.fromkeys(
            self.IDLE_GIFS + self.TALKING_GIFS + list(self.EXTRA_GIFS.values()) + static_vals
        ))

        def _worker():
            preloaded: dict[str, tuple] = {}
            missing: list[str] = []
            for name in all_names:
                path = ASSETS / name
                if not path.exists():
                    missing.append(name)
                    continue
                try:
                    preloaded[name] = _load_gif_frames_offthread(str(path))
                except Exception as exc:
                    logger.warning(f"Failed to decode {name}: {exc}")
            try:
                self.root.after(0, lambda: self._apply_gif_load(preloaded, missing))
            except Exception:
                self._apply_gif_load(preloaded, missing)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_gif_load(self, preloaded: dict, missing: list):
        for name, (pil_frames, delays) in preloaded.items():
            try:
                self._gif_cache[name] = GifPlayer(
                    self._gif_label, str(ASSETS / name), self.root.after,
                    pil_frames=pil_frames, delays=delays,
                )
                logger.info(f"Loaded GIF: {name}")
            except Exception as exc:
                logger.warning(f"Failed to load {name}: {exc}")

        if missing:
            logger.warning(f"{len(missing)} asset(s) not found: {missing}")

        try:
            if hasattr(self, "_loading_label") and self._loading_label:
                self._loading_label.destroy()
                self._loading_label = None
        except Exception:
            pass
        try:
            self._start_wake_sequence()
        except Exception as exc:
            logger.warning(f"start_wake_sequence failed: {exc}")

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
                screen = ScreenReader(own_tk_root=self.root)
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
                    # Load GIFs and start wake sequence — simple, flat loader
                    try:
                        self.root.after(0, self._load_gifs_simple)
                    except Exception as e:
                        print(f"[BackgroundInit] load_gifs_simple failed: {e}")
                    try:
                        self.root.after(0, self._start_placeholder_refresh)
                    except Exception:
                        pass
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
            logger.warning(f"GIF not loaded: {name}")

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
        with self._state_lock:
            self._apply_state(state, mood)

    def _apply_state(self, state: str, mood: str = "neutral"):
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
            if self._bleep:
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
        self.root.after(WAKE_DELAY_MS, self._finish_wake)

    def _finish_wake(self):
        self._set_state(self.STATE_IDLE, "neutral")
        self.root.after(1000, self._schedule_screen_poll)

    def _schedule_screen_poll(self):
        if not _SETTINGS.enable_ambient_polls:
            return
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        threading.Thread(target=self._ai_tick, daemon=True).start()

    def _reschedule_screen_poll(self):
        if not _SETTINGS.enable_ambient_polls:
            if self._poll_job:
                self.root.after_cancel(self._poll_job)
                self._poll_job = None
            return
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
        self._poll_job = self.root.after(SCREEN_POLL_INTERVAL_MS, self._schedule_screen_poll)

    def _on_cancel_ai(self, event=None):
        """Escape — cancel an in-flight AI request."""
        self._cancel_event.set()
        self._re_enable_input()
        self.root.after(0, lambda: self._set_state(self.STATE_IDLE))
        self.root.after(0, lambda: self._subtitle.show_message("Cancelled.", "#888888"))

    def _ai_tick(self, user_message: str | None = None):
        is_user = user_message is not None

        if not self._ai:
            self._re_enable_input()
            self._reschedule_screen_poll()
            return

        self._cancel_event.clear()
        self._ai_busy = True

        if is_user:
            self.root.after(0, lambda: self._input_box.config(state="disabled"))

        screen_text = ""
        if not is_user and self._screen:
            own_hwnd = None
            try:
                own_hwnd = self._screen._get_own_hwnd()
            except Exception:
                pass

            active_title = ""
            if _SETTINGS.include_window_title_in_context:
                active_title = self._screen.get_active_window_title(skip_hwnd=own_hwnd)

            typing_pause = _SETTINGS.ocr_pause_while_typing_sec
            recently_active = (
                typing_pause > 0
                and (time.time() - self._last_direct_interaction_time) < typing_pause
            )

            if _SETTINGS.enable_screen_reader and not recently_active:
                screen_text = self._screen.capture_text(
                    focused_only=_SETTINGS.ocr_focused_window_only,
                )
                self._last_screen_text = screen_text

                _matches = getattr(self._screen, "last_pattern_matches", [])
                if _matches:
                    tags = "\n".join(f"[{m.label}: {m.snippet[:80]}]" for m in _matches[:4])
                    screen_text = tags + "\n" + screen_text
                elif getattr(self._screen, "has_angry_trigger", False):
                    kws = ", ".join(self._screen.last_angry_keywords[:3])
                    screen_text = f"[ANGRY_TRIGGER: {kws}]\n" + screen_text

                if active_title:
                    screen_text = f"[Active: {active_title}]\n" + screen_text

                _KEY_WORDS = {
                    "error", "warning", "failed", "exception", "traceback",
                    "fatal", "crash", "denied", "undefined", "null", "critical",
                }
                _positions = getattr(self._screen, "last_word_positions", [])
                _important = [p for p in _positions if p.get("text", "").lower() in _KEY_WORDS][:5]
                if _important:
                    pos_str = " | ".join(f"{p['text']}@({p['screen_x']},{p['screen_y']})" for p in _important)
                    screen_text = f"[Error positions: {pos_str}]\n" + screen_text
            elif active_title:
                screen_text = f"[Active: {active_title}]"
                self._last_screen_text = screen_text

        self.root.after(0, lambda: self._set_state(self.STATE_THINKING))

        def _on_token(raw_so_far: str):
            if not self._cancel_event.is_set():
                self._subtitle.show_thinking(raw_so_far)

        try:
            if _SETTINGS.enable_streaming:
                response = self._ai.query_streaming(
                    screen_context=screen_text if not is_user else self._last_screen_text,
                    user_message=user_message or "",
                    on_token=_on_token,
                )
            else:
                response = self._ai.query(
                    screen_context=screen_text if not is_user else self._last_screen_text,
                    user_message=user_message or "",
                )
        except Exception as exc:
            err_str = str(exc)
            logger.error(f"AI tick failed: {err_str}")
            _groq_limit_keywords = ("rate_limit", "rate limit", "429", "quota", "groq_exhausted")
            if not any(kw in err_str.lower() for kw in _groq_limit_keywords):
                _short = err_str[:200] if len(err_str) > 200 else err_str
                native_error_popup("Agetha — Error", f"An error occurred:\n{_short}")
            self.root.after(0, self._re_enable_input)
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE))
            self._ai_busy = False
            self._reschedule_screen_poll()
            return

        if self._cancel_event.is_set():
            self._ai_busy = False
            self.root.after(0, self._re_enable_input)
            return

        print("\n" + "-" * 52)
        if user_message and user_message != "__touch__":
            print(f"[USER]  {user_message}")
        print(f"[AI]    {json.dumps(response, ensure_ascii=False)}")
        print("-" * 52)

        self.root.after(0, self._re_enable_input)
        self.root.after(0, self._update_token_status)
        self._ai_busy = False
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

        if IS_WINDOWS:
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
        if PYGAME_OK:
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
        else:
            print("[SOUND] Pygame not available; sound fallback skipped.")

    def _speak_and_continue(self, segments, mood, shutdown_requested: bool = False):
        if segments:
            self.root.after(0, lambda: self._set_state(self.STATE_TALKING, mood))
            self.root.after(0, lambda: self._subtitle.speak(
                segments,
                on_done=lambda: self._on_speech_done(shutdown_requested),
            ))
        else:
            self.root.after(0, lambda: self._set_state(self.STATE_IDLE, mood))
            self._reschedule_screen_poll()

    def _show_op_error(self, message: str) -> None:
        msg = str(message)[:140]
        self.root.after(0, lambda: self._subtitle.show_message(msg, "#ff4444"))

    def _show_op_success(self, message: str) -> None:
        msg = str(message)[:140]
        self.root.after(0, lambda: self._subtitle.show_message(msg, "#44cc66"))

    def pick_window_sync(self, matches: list[tuple[int, str]]) -> int | None:
        """Block until user picks a window (main-thread dialog)."""
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0][0]
        result: list[int | None] = [None]
        done = threading.Event()

        def _ui():
            try:
                result[0] = self._show_window_picker_dialog(matches)
            finally:
                done.set()

        self.root.after(0, _ui)
        done.wait(timeout=60)
        return result[0]

    def _show_window_picker_dialog(self, matches: list[tuple[int, str]]) -> int | None:
        dlg = tk.Toplevel(self.root)
        dlg.title("Agetha — Pick window")
        dlg.configure(bg=W95_BG)
        dlg.attributes("-topmost", True)
        dlg.resizable(False, False)
        tk.Label(
            dlg, text="Multiple windows match. Which one?",
            bg=W95_BG, fg="#000000", font=("Tahoma", 9),
        ).pack(padx=10, pady=(10, 4))
        frame = tk.Frame(dlg, bg=W95_BG)
        frame.pack(padx=10, pady=4, fill="both", expand=True)
        listbox = tk.Listbox(frame, width=58, height=min(8, len(matches)), font=("Tahoma", 9))
        scroll = tk.Scrollbar(frame, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scroll.set)
        listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for _hwnd, title in matches:
            listbox.insert("end", title[:72])
        listbox.selection_set(0)
        chosen: list[int | None] = [None]

        def _ok(_event=None):
            sel = listbox.curselection()
            idx = sel[0] if sel else 0
            chosen[0] = matches[idx][0]
            dlg.destroy()

        def _cancel():
            chosen[0] = None
            dlg.destroy()

        btn_row = tk.Frame(dlg, bg=W95_BG)
        btn_row.pack(pady=(4, 10))
        tk.Button(btn_row, text="OK", width=8, command=_ok).pack(side="left", padx=4)
        tk.Button(btn_row, text="Cancel", width=8, command=_cancel).pack(side="left", padx=4)
        listbox.bind("<Double-Button-1>", _ok)
        dlg.protocol("WM_DELETE_WINDOW", _cancel)
        dlg.grab_set()
        dlg.wait_window()
        return chosen[0]

    def _ai_query(self, user_message: str, screen_context=None, doc_content: str = ""):
        if self._cancel_event.is_set() or not self._ai:
            return None
        self.root.after(0, lambda: self._set_state(self.STATE_THINKING))

        def _on_token(raw):
            if not self._cancel_event.is_set():
                self._subtitle.show_thinking(raw)

        try:
            if _SETTINGS.enable_streaming:
                return self._ai.query_streaming(
                    screen_context=self._last_screen_text if screen_context is None else screen_context,
                    user_message=user_message,
                    doc_content=doc_content,
                    on_token=_on_token,
                )
            return self._ai.query(
                screen_context=self._last_screen_text if screen_context is None else screen_context,
                user_message=user_message,
                doc_content=doc_content,
            )
        except Exception as exc:
            logger.error(f"_ai_query failed: {exc}")
            return None

    def _try_short_mood_speak(self, command: str, ctx) -> bool:
        try:
            short_moods = {"happy", "excited", "surprised"}
            segments = ctx.segments
            mood = ctx.mood
            is_short = (
                command == "speak" and isinstance(segments, list) and len(segments) == 1
                and isinstance(segments[0].get("text", ""), str)
                and len(segments[0].get("text", "").split()) <= 6
            )
            if not (is_short and mood in short_moods):
                return False
            static_name = self.EXTRA_STATIC_GIFS.get(mood)
            if not static_name and mood in ("excited", "surprised"):
                static_name = self.EXTRA_STATIC_GIFS.get("happy")
            if not static_name or static_name not in self._gif_cache:
                return False
            self.root.after(0, lambda: self._set_state(self.STATE_TALKING, mood))
            self.root.after(12, lambda: self._play_gif(static_name))
            self.root.after(0, lambda: self._subtitle.speak(
                segments,
                on_done=lambda: self._on_speech_done(ctx.shutdown_requested),
            ))
            return True
        except Exception:
            return False

    def _dispatch_response(self, response: dict, user_message: str | None = None):
        try:
            self.root.after(0, lambda: self._subtitle.clear())
        except Exception:
            pass
        from command_handlers import dispatch
        dispatch(self, response, user_message)

    def _on_speech_done(self, shutdown: bool = False):
        # _persistent_mood already set — _set_state(STATE_IDLE) picks it up
        self.root.after(0, lambda: self._set_state(self.STATE_IDLE))
        if shutdown:
            self.root.after(50, self._shutdown)
        else:
            self.root.after(0, self._reschedule_screen_poll)

    def _shutdown(self):
        self._stop_talking_rotation()
        if self._bleep:
            try:
                self._bleep.stop()
            except Exception:
                pass
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        if self._voice:
            self._voice.stop()
        self.root.quit()

    def run(self):
        try:
            self.root.mainloop()
        finally:
            if self._bleep:
                try:
                    self._bleep.stop()
                except Exception:
                    pass
            if self._voice:
                self._voice.stop()


def _warn_if_no_api_key():
    """First-run hint when config exists but no AI backend is configured."""
    from app_config import get_settings, CONFIG_PATH, ENV_PATH
    s = get_settings()
    if s.bool("USE_LOCAL_AI"):
        if s.get("LOCAL_AI_MODEL", "").strip():
            return
    elif s.enable_openrouter:
        if s.openrouter_api_key:
            return
    else:
        has_key = bool(s.get("GROQ_API_KEY", "").strip())
        if not has_key and ENV_PATH.exists():
            for line in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("GROQ_API_KEY") and "=" in line:
                    if line.split("=", 1)[1].strip():
                        return
        if has_key:
            return
    flag = CONFIG_PATH.parent / ".agetha_setup_hint_shown"
    if flag.exists():
        return
    try:
        flag.write_text("1", encoding="utf-8")
    except Exception:
        pass
    msg = (
        "No Groq API key or Ollama model found.\n\n"
        "Add GROQ_API_KEY to config.txt or .env,\n"
        "or set USE_LOCAL_AI=yes and LOCAL_AI_MODEL.\n\n"
        "Optional: TESSERACT_PATH for screen reading."
    )
    title = "Agetha — Setup"
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, title, 0x30 | 0x1000)
    except Exception:
        print(f"[Agetha] {msg}")


def _early_config_check():
    """
    Ensure config.txt exists; always continue with defaults if missing or invalid.
    """
    from app_config import ensure_config_file, get_last_config_load, get_settings, CONFIG_PATH

    ensure_config_file(CONFIG_PATH, write_if_missing=True)
    get_settings(reload=True)
    load_info = get_last_config_load()
    if load_info and load_info.warnings:
        for w in load_info.warnings:
            print(f"[Agetha] Config: {w}")

    _warn_if_no_api_key()


if __name__ == "__main__":
    _early_config_check()
    app = CompanionApp()
    app.run()
