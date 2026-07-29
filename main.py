"""
Desktop AI Companion - Main Application
Requires: pip install pillow pyautogui pytesseract numpy pygame-ce requests
Assets folder must contain mood GIFs (idle/talking/happy/sad/angry/thinking/
  surprised/want/loaf/sleeping/error + *-static variants). Every GIF is mapped
  to a mood or presence path. Font: barrio.ttf must be in assets/ folder.
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


_FAST_MODE_SKIP_RECONCILE_ENV = "AGETHA_SKIP_FAST_MODE_RECONCILE"


def _consume_fast_mode_reconcile_skip(environment=None) -> bool:
    """Consume the one-launch Medic opt-out without persisting it."""
    env = os.environ if environment is None else environment
    raw = str(env.pop(_FAST_MODE_SKIP_RECONCILE_ENV, "") or "").strip().lower()
    return raw in {"1", "yes", "true", "on"}


# Reconcile the on-disk Fast Mode transaction before imports that cache typed
# settings. Importing ``main`` for tests or tooling remains strictly read-only.
if __name__ == "__main__":
    if _consume_fast_mode_reconcile_skip():
        print("[FastMode] reconciliation skipped for this launch by user choice.")
    else:
        try:
            from agetha.core.fast_mode_profile import reconcile_fast_mode_profile
            _fast_mode_startup_result = reconcile_fast_mode_profile()
            if not _fast_mode_startup_result.ok:
                print(
                    f"[FastMode] {_fast_mode_startup_result.status}; existing settings kept. "
                    "Run Medic Checker for recovery."
                )
        except Exception as _fast_mode_startup_exc:
            print(f"[FastMode] startup reconciliation failed safely: {_fast_mode_startup_exc}")

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

from agetha.core.ai_engine import AIEngine
from agetha.platform.screen_reader import ScreenReader
from agetha.commands.command_guard import CommandGuard
from agetha.platform.voice_input import (
    VoiceInput, MicPickerDialog, list_microphones,
    load_mic_settings, save_mic_settings, coerce_device_index,
)
from agetha.utils import (
    native_error_popup, native_message_box, apply_window_icon, logger, BASE_DIR, WINDOW_W, WINDOW_H,
    TOUCH_COOLDOWN_SEC, WAKE_DELAY_MS, LOAF_TIMER_MS,
    refresh_config_constants,
)
from agetha.app_config import get_settings
from agetha.platform.window_control import ease_out_cubic
from agetha.features.tts_player import VoiceOutputCoordinator
from agetha.ui.mood_effects import MoodGlowController
from agetha.ui.motion_effects import MoodMotionController
from agetha.ui.window_effects import CRTCloseController
from agetha.ui.display_scale import resolve_ui_scale, scale_px

_SETTINGS = get_settings()

ASSETS      = BASE_DIR / "assets"
FONT_PATH   = ASSETS / "barrio.ttf"


BASE_WINDOW_W = WINDOW_W
BASE_WINDOW_H = WINDOW_H
BASE_GIF_W = 340
BASE_GIF_H = 300
_UI_SCALE = 1.0
GIF_W = BASE_GIF_W
GIF_H = BASE_GIF_H


def _px(value: int | float) -> int:
    return scale_px(value, _UI_SCALE)

# Phase 2: Attention-snap system
# Moods that qualify to trigger a center-snap during ambient polls
_ATTENTION_MOODS = {"manic", "angry", "paranoid", "dominant", "surprised", "excited"}

# Per-mood inactivity threshold (seconds) before Agetha snaps to center.
_MOOD_SNAP_THRESHOLDS: dict[str, int] = _SETTINGS.mood_snap_thresholds()

# ── Phase 2: ctypes external window helper ────────────────────────────────────
def _find_window_hwnd(partial_name: str) -> int | None:
    """Find the first visible window whose title contains partial_name (case-insensitive)."""
    from agetha.platform.window_control import find_window_hwnd
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

    def __init__(self, canvas: tk.Canvas, font_size: int = 17, bleep_player=None, voice_out=None):
        self._canvas     = canvas
        self._font_size  = font_size
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._bleep = bleep_player
        self._voice_out = voice_out

        self._canvas_w = WINDOW_W
        self._canvas_h = _px(130)
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
        self._draw_pending = False
        self._pending_text = ""
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
            if chunk and not self._stop_event.is_set():
                if self._voice_out and hasattr(self._voice_out, "speak_segment"):
                    try:
                        self._voice_out.speak_segment(chunk)
                    except Exception:
                        pass
            if pause > 0 and not self._stop_event.is_set():
                if self._voice_out:
                    try:
                        self._voice_out.pause()
                    except Exception:
                        pass
                elif self._bleep:
                    self._bleep.pause()
                time.sleep(pause)
                if self._voice_out:
                    try:
                        self._voice_out.resume()
                    except Exception:
                        pass
                elif self._bleep:
                    self._bleep.resume()
        try:
            if self._voice_out:
                self._voice_out.stop_bleeps()
            elif self._bleep:
                self._bleep.stop()
        except Exception:
            pass
        if on_done:
            self._canvas.after(0, on_done)

    def _compute_layout(self, text: str, color: str) -> dict | None:
        cw = self._canvas_w
        ch = self._canvas_h
        max_w = max(_px(40), cw - _px(24))
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
        y = _px(6)

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
                    if height <= ch - _px(12):
                        y = max(_px(6), (ch - height) // 2)
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
        from agetha.ui.w95_window import apply_borderless_win95, show_borderless

        self._win = tk.Toplevel(parent)
        apply_borderless_win95(self._win, parent, topmost=True)
        self._win.configure(bg=W95_BG)
        self._win.resizable(False, False)
        self._drag_x = self._drag_y = 0

        # ── Outer raised bevel ────────────────────────────────────────────
        outer = tk.Frame(self._win, bg=W95_BG, relief="raised", bd=2)
        outer.pack(fill="both", expand=True)

        # ── Title bar ─────────────────────────────────────────────────────
        title_bar = tk.Frame(outer, bg=W95_TITLE_BG, height=_px(18))
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

        # ── Position just above the parent window, clamped to screen ──────
        self._win.update_idletasks()
        px = parent.winfo_x()
        py = parent.winfo_y()
        pw = parent.winfo_width()
        ww = self._win.winfo_width() or 300
        wh = self._win.winfo_height() or 200
        sw = self._win.winfo_screenwidth()
        sh = self._win.winfo_screenheight()
        x  = px + (pw - ww) // 2
        y  = py - wh - 10
        x  = max(0, min(x, sw - ww))
        y  = max(0, min(y, sh - wh))
        self._win.geometry(f"+{x}+{y}")

        show_borderless(self._win)
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
    # Every mood maps to a distinct visual where the asset pack allows it.
    EXTRA_GIFS   = {
        "happy":       "happy.gif",
        "surprised":   "surprised.gif",
        "sad":         "sad.gif",
        "excited":     "want.gif",          # craving / hype — dedicated want clip
        "angry":       "angry.gif",
        "thinking":    "thinking.gif",
        "whisper":     "thinking-static.gif",  # quiet still presence
        "sleeping":    "sleeping.gif",
        "loaf":        "loaf.gif",
        "manic":       "want.gif",          # chaotic hunger energy
        "melancholic": "sad.gif",
        "paranoid":    "surprised.gif",     # jumpier than thinking
        "vulnerable":  "sad-static.gif",    # soft held expression
        "dominant":    "angry.gif",
    }

    # Static images to show after animated emotion gifs finish
    EXTRA_STATIC_GIFS = {
        "happy": "happy-static.gif",
        "sad":   "sad-static.gif",
        "angry": "angry-static.gif",
        "thinking": "thinking-static.gif",
        "excited": "want.gif",
        "manic": "want.gif",
        "melancholic": "sad-static.gif",
        "vulnerable": "sad-static.gif",
        "paranoid": "surprised.gif",
        "dominant": "angry-static.gif",
        "whisper": "thinking-static.gif",
    }

    # Always load (error is rare-path; want also listed via EXTRA_GIFS)
    EXTRA_LOAD_GIFS = ["error.gif", "want.gif"]

    # Talking-neutral variants by mood band (uses talking-1/2/3 uniquely)
    TALKING_BY_MOOD = {
        "whisper": "talking-2.gif",
        "vulnerable": "talking-2.gif",
        "melancholic": "talking-2.gif",
        "excited": "talking-3.gif",
        "manic": "talking-3.gif",
        "happy": "talking-3.gif",
        "dominant": "talking-3.gif",
        "angry": "talking-1.gif",
        "surprised": "talking-1.gif",
        "paranoid": "talking-1.gif",
    }

    # Animated clips while speaking (prefer these over idle statics)
    TALKING_MOOD_GIFS = {
        "happy": "happy.gif",
        "excited": "want.gif",
        "manic": "want.gif",
        "sad": "sad.gif",
        "melancholic": "sad.gif",
        "vulnerable": "sad.gif",
        "angry": "angry.gif",
        "dominant": "angry.gif",
        "thinking": "thinking.gif",
        "surprised": "surprised.gif",
        "paranoid": "surprised.gif",
        "whisper": "talking-2.gif",
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

        try:
            # Tk already scales point-sized fonts for the active Windows DPI.
            # Use that DPI only for pixel geometry; applying it to ``tk scaling``
            # again makes text oversized relative to the window on Surface PCs.
            display_dpi_scale = float(self.root.winfo_fpixels("1i")) / 96.0
        except Exception:
            display_dpi_scale = None

        global WINDOW_W, WINDOW_H, GIF_W, GIF_H, _UI_SCALE
        _UI_SCALE = resolve_ui_scale(
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
            _SETTINGS.ui_scale,
            dpi_scale=display_dpi_scale,
        )
        WINDOW_W = scale_px(BASE_WINDOW_W, _UI_SCALE)
        WINDOW_H = scale_px(BASE_WINDOW_H, _UI_SCALE)
        GIF_W = scale_px(BASE_GIF_W, _UI_SCALE)
        GIF_H = scale_px(BASE_GIF_H, _UI_SCALE)
        self.WINDOW_W, self.WINDOW_H = WINDOW_W, WINDOW_H
        self._ui_scale = _UI_SCALE
        setattr(self.root, "_agetha_ui_scale", _UI_SCALE)
        logger.info(
            f"[UI] scale={_UI_SCALE:.2f} display="
            f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()} "
            f"dpi_scale={display_dpi_scale or 1.0:.2f}"
        )

        self.root.title(f"Agetha.exe v{_SETTINGS.app_version}")
        self.root.geometry(
            f"{WINDOW_W}x{WINDOW_H}+{_SETTINGS.window_start_x}+{_SETTINGS.window_start_y}"
        )
        self.root.configure(bg=W95_BG)
        self.root.overrideredirect(True)
        self.root.resizable(False, False)
        apply_window_icon(self.root)
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
        self._placeholder_refresh_job = None
        self._restore_job = None
        self._wake_job = None
        self._motion_request_job = None
        self._persistent_mood: str | None = None  # holds sad/angry across speech→idle
        self._current_display_mood = "sleeping"

        # Defer heavy initialization to background thread so the window shows immediately
        self._bleep  = None
        self._voice_out = None
        self._screen = None
        self._ai     = None
        self._last_screen_text: str = ""
        self._loaf_job = None
        self._sleep_job = None
        self._is_loafing = False
        self._pending_shutdown = False
        self._last_touch_time: float = 0.0           # epoch time of last gif-click touch event
        self._last_direct_interaction_time: float = time.time()  # updated on keystroke OR gif-click
        self._guard = CommandGuard(self.root)
        self._cancel_event = threading.Event()
        self._ai_busy = False
        self._ai_busy_noninterruptible = False
        self._ai_tick_lock = threading.Lock()
        self._pending_user_message: str | None = None
        self._post_ai_tick_callbacks: list[Callable[[], None]] = []
        self._speech_active = False
        self._state_lock = threading.Lock()
        self._voice: VoiceInput | None = None
        self._mic_active = False
        self._dragging_file = False
        self._last_dragged_file = ""
        self._closing = False
        self._shutdown_complete = False
        self._is_dragging = False
        self._is_minimized = False
        self._drag_x = self._drag_y = 0
        self._win_x, self._win_y = _SETTINGS.window_start_x, _SETTINGS.window_start_y
        self._geom_anim_job = None

        self._build_ui()
        self._mood_glow = MoodGlowController(
            self.root,
            self._gif_border,
            enabled=_SETTINGS.enable_mood_glow,
            animated=_SETTINGS.mood_glow_animated,
            interval_ms=_SETTINGS.mood_glow_interval_ms,
            reduced_motion=_SETTINGS.reduced_motion,
        )
        self._motion = MoodMotionController(
            self.root,
            enabled=_SETTINGS.enable_mood_motion,
            reduced_motion=_SETTINGS.reduced_motion,
            cooldown_seconds=_SETTINGS.mood_motion_cooldown_seconds,
            is_dragging=lambda: self._is_dragging,
            is_closing=lambda: self._closing,
            is_minimized=lambda: self._is_minimized,
            geometry_busy=lambda: self._geom_anim_job is not None,
        )
        self._close_effect = CRTCloseController(
            self.root,
            self._graceful_shutdown,
            enabled=_SETTINGS.enable_crt_close_animation,
            reduced_motion=_SETTINGS.reduced_motion,
            cancel_geometry=self._cancel_geometry_animation,
            disable_input=self._disable_input_for_close,
        )
        self.root.protocol("WM_DELETE_WINDOW", self._request_close)
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

    def _build_ui(self):
        # Patch font constants now that Tk is alive and tkfont.families() is valid
        global W95_FONT, W95_FONT_BOLD
        W95_FONT      = _safe_win_font(8, bold=False)
        W95_FONT_BOLD = _safe_win_font(8, bold=True)

        # ── Outer raised bevel (whole window border) ──────────────────────────
        self._outer = tk.Frame(self.root, bg=W95_BG, relief="raised", bd=2)
        self._outer.pack(fill="both", expand=True)

        # ── Win95 Title bar ───────────────────────────────────────────────────
        title_bar = tk.Frame(self._outer, bg=W95_TITLE_BG, height=_px(18))
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
            command=self._request_close,
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

        # Dashboard button
        dash_btn = tk.Button(
            title_bar, text="📊",
            bg=W95_BTN_BG, fg=W95_TEXT,
            font=("MS Sans Serif", 7, "bold"),
            relief="raised", bd=2, width=2,
            activebackground=W95_BTN_BG, activeforeground=W95_TEXT,
            command=self._open_dashboard,
        )
        dash_btn.pack(side="right", padx=(0, 1), pady=1)

        # Drag bindings on title bar and its non-button children
        for w in (title_bar, title_lbl):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>",     self._drag_motion)
            w.bind("<ButtonRelease-1>", self._drag_end)


        # ── GIF display area — black background, raised border ───────────────
        self._gif_border = tk.Frame(self._outer, bg="#000000", relief="raised", bd=2)
        self._gif_border.pack(fill="x", padx=4, pady=(4, 0))

        self._gif_label = tk.Label(self._gif_border, bg="#000000", bd=0,
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
        self._sub_canvas = tk.Canvas(self._outer, width=WINDOW_W, height=_px(130),
                                     bg="#a0a0a0", bd=2, relief="sunken",
                                     highlightthickness=0)
        self._sub_canvas.pack(fill="x", padx=4, pady=(4, 0))
        self._subtitle = None

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
        self._input_box.pack(fill="both", expand=True, ipady=_px(6), padx=_px(2))

        placeholder_font = tkfont.Font(family="MS Sans Serif", size=7)
        self._placeholder_lbl = tk.Label(
            entry_wrapper, text="", font=placeholder_font,
            bg=W95_INPUT_BG, fg="#888888", anchor="w", padx=4, pady=0,
        )
        self._placeholder_lbl.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._placeholder_lbl.bind("<Button-1>", self._on_placeholder_click)
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
            return f"type here...  •  key {idx}/{total}  •  {pct}% tokens left"
        except Exception:
            return "type here..."

    def _on_placeholder_click(self, event=None) -> None:
        self._input_box.focus_set()
        self._update_placeholder(focused=True)

    def _input_has_focus(self) -> bool:
        try:
            return self._input_box.focus_get() == self._input_box
        except Exception:
            return False

    def _update_placeholder(self, focused=None) -> None:
        if focused is None:
            focused = self._input_has_focus()
        if bool(self._input_var.get()) or focused:
            self._placeholder_lbl.place_forget()
        else:
            self._placeholder_lbl.config(text=self._get_placeholder_text())
            self._placeholder_lbl.place(relx=0, rely=0, relwidth=1, relheight=1)

    def _start_placeholder_refresh(self) -> None:
        if self._closing or self._placeholder_refresh_job is not None:
            return

        def _tick():
            self._placeholder_refresh_job = None
            if self._closing:
                return
            try:
                self._update_placeholder(focused=self._input_has_focus())
            except Exception:
                pass
            if not self._closing:
                self._placeholder_refresh_job = self.root.after(10000, _tick)

        self._placeholder_refresh_job = self.root.after(10000, _tick)

    def _reset_mic_button(self) -> None:
        self._mic_active = False
        self._mic_btn_var.set("🎤")
        self._mic_btn.config(bg=W95_BTN_BG, fg=W95_TEXT, activebackground=W95_BTN_BG)

    def _toggle_mic(self) -> None:
        if self._voice is None:
            settings = load_mic_settings()
            device_index = coerce_device_index(settings.get("mic_device_index"))
            mics = list_microphones()
            if device_index is not None:
                valid_indices = {i for i, _ in mics}
                if device_index not in valid_indices:
                    logger.warning(
                        f"[Voice] Saved mic index {device_index} is no longer available; re-picking…"
                    )
                    device_index = None
                    settings.pop("mic_device_index", None)
                    settings.pop("mic_device_name", None)
                    save_mic_settings(settings)
            if device_index is None:
                if not mics:
                    native_error_popup(
                        "Agetha — Microphone",
                        "No microphone devices found.\n"
                        "Connect a mic and ensure PyAudio or sounddevice is installed.\n"
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
                on_fatal_error=lambda: self.root.after(0, self._reset_mic_button),
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
        want_player = self._gif_cache.get("want.gif") or self._gif_cache.get("surprised.gif")
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

    def _open_dashboard(self) -> None:
        try:
            from agetha.ui.dashboard import open_dashboard
            open_dashboard(self.root, get_settings())
        except Exception as exc:
            logger.warning(f"Dashboard open failed: {exc}")

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
        sampled = 0
        try:
            from agetha.core.companion_stats import update_stats
            if file_path and Path(file_path).exists():
                update_stats("file_drop", path=file_path)
                try:
                    from agetha.core.companion_stats import get_stats_summary
                    sampled = int(get_stats_summary().get("last_feed_bytes", 0))
                except Exception:
                    pass
            else:
                update_stats("file_drop", file_size=0)
        except Exception:
            pass
        try:
            from agetha.core.emotion_engine import note
            note("file_shared", summary=f"user shared a file: {filename}")
        except Exception:
            pass
        self._set_state(self.STATE_IDLE)
        if self._input_box["state"] == "disabled":
            return
        msg = (
            f'[system] file_dragged: "{filename}" (path: {file_path}; bytes_devoured: {sampled})'
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
        if self._closing:
            return
        self._is_dragging = True
        if hasattr(self, "_motion"):
            self._motion.cancel_motion(restore=True)
        self._cancel_geometry_animation()
        self._drag_x, self._drag_y = e.x_root, e.y_root
        self._win_x, self._win_y = self.root.winfo_x(), self.root.winfo_y()

    def _drag_end(self, _event=None):
        self._is_dragging = False

    def _cancel_geometry_animation(self) -> None:
        if self._geom_anim_job is not None:
            try:
                self.root.after_cancel(self._geom_anim_job)
            except Exception:
                pass
            self._geom_anim_job = None
        if hasattr(self, "_motion"):
            self._motion.cancel_motion(restore=True)

    def animate_geometry(
        self,
        target_x: int,
        target_y: int,
        *,
        duration_ms: int | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """Smooth move for Agetha's window — measure once, then only geometry writes."""
        if self._closing or self._is_dragging or self._is_minimized:
            return
        smooth = _SETTINGS.window_move_smooth
        if duration_ms is None:
            duration_ms = _SETTINGS.window_move_duration_ms

        target_x, target_y = int(target_x), int(target_y)

        self._cancel_geometry_animation()

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
                self._geom_anim_job = self.root.after(50, _tick)
            else:
                self._geom_anim_job = None
                self._win_x, self._win_y = target_x, target_y
                self.root.geometry(f"+{target_x}+{target_y}")
                if on_done:
                    on_done()

        _tick()

    def _drag_motion(self, e):
        if self._closing:
            return
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
        if self._closing:
            return
        self._is_minimized = True
        self._cancel_geometry_animation()
        if hasattr(self, "_mood_glow"):
            self._mood_glow.cancel(reset=False)
        self._pause_gif_playback()
        if IS_WINDOWS:
            try:
                self.root.overrideredirect(False)
                self.root.iconify()
            except Exception:
                return
            def _bind_restore():
                self._restore_job = None
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
                            self._refresh_mood_glow()
                            self.root.unbind("<Map>")
                    except Exception:
                        pass
                self.root.bind("<Map>", _on_map)
            self._restore_job = self.root.after(250, _bind_restore)
        else:
            try:
                self.root.overrideredirect(False)
                self.root.iconify()
            except Exception:
                pass
            def _bind_restore_linux():
                self._restore_job = None
                def _on_map(event):
                    try:
                        if self.root.state() != "iconic":
                            self.root.overrideredirect(True)
                            self.root.lift()
                            self._is_minimized = False
                            self._resume_gif_playback()
                            self._refresh_mood_glow()
                            self.root.unbind("<Map>")
                    except Exception:
                        pass
                self.root.bind("<Map>", _on_map)
            self._restore_job = self.root.after(250, _bind_restore_linux)

    def _on_gif_click(self, event=None):
        """Handle a click on the Agetha gif — sends a hidden touch message to the AI.
        A 10-second cooldown prevents spamming."""
        now = time.time()
        self._last_direct_interaction_time = now  # Phase 2: stamp interaction clock
        self._wake_from_presence_rest()
        if now - self._last_touch_time < TOUCH_COOLDOWN_SEC:
            return   # still in cooldown, silently ignore
        self._last_touch_time = now
        # Don't interrupt an ongoing AI response or block the input box permanently
        if self._input_box["state"] == "disabled":
            return
        try:
            from agetha.core.emotion_engine import note
            note("touch", summary="user touched the avatar")
        except Exception:
            pass
        self._persistent_mood = None
        threading.Thread(
            target=self._ai_tick,
            kwargs={"user_message": "__touch__"},
            daemon=True,
        ).start()

    def _on_user_input(self, event=None):
        self._last_direct_interaction_time = time.time()  # Phase 2: any key = direct interaction
        self._wake_from_presence_rest()
        text = self._input_var.get().strip()
        if not text:
            return
        if self._input_box["state"] == "disabled":
            return
        if self._poll_job:
            try:
                self.root.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        self._input_var.set("")
        self._input_box.config(state="disabled")
        if hasattr(self, "_placeholder_lbl"):
            self._placeholder_lbl.config(text="Processing...")
            self._placeholder_lbl.place(relx=0, rely=0, relwidth=1, relheight=1)
        # Clear any sticky mood — new interaction resets expression
        self._persistent_mood = None
        threading.Thread(target=self._ai_tick, kwargs={"user_message": text}, daemon=True).start()

    def _re_enable_input(self):
        if self._closing:
            return
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
                self._wake_from_presence_rest()
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
        if self._closing or self._is_minimized or self._is_dragging:
            return
        threshold  = _MOOD_SNAP_THRESHOLDS.get(mood, 600)
        inactivity = time.time() - self._last_direct_interaction_time

        def _do_position():
            if self._closing or self._is_minimized or self._is_dragging:
                return
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
        extra_load = list(getattr(self, "EXTRA_LOAD_GIFS", []) or [])
        all_names = list(dict.fromkeys(
            self.IDLE_GIFS
            + self.TALKING_GIFS
            + list(self.EXTRA_GIFS.values())
            + static_vals
            + extra_load
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
                if self._closing:
                    try:
                        if bleep:
                            bleep.stop()
                    except Exception:
                        pass
                    return
                try:
                    self._bleep = bleep
                    self._screen = screen
                    self._ai = ai
                    if self._screen:
                        try:
                            self._screen.cache_own_window_handle()
                        except Exception as exc:
                            logger.warning(f"Own-window handle cache failed: {exc}")
                    try:
                        self._voice_out = VoiceOutputCoordinator(self._bleep, get_settings())
                    except Exception as exc:
                        logger.warning(f"VoiceOutputCoordinator init failed: {exc}")
                        self._voice_out = None
                    if self._subtitle is None and hasattr(self, "_sub_canvas"):
                        self._subtitle = SubtitleRenderer(
                            self._sub_canvas, font_size=17,
                            bleep_player=self._bleep, voice_out=self._voice_out,
                        )
                    elif hasattr(self, "_subtitle") and self._subtitle:
                        self._subtitle._bleep = self._bleep
                        self._subtitle._voice_out = self._voice_out
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
                # Tk may already be closing; never run this UI handoff on the worker.
                try:
                    if bleep:
                        bleep.stop()
                except Exception:
                    pass
        except Exception as e:
            print(f"[BackgroundInit] Unexpected error: {e}")
            native_error_popup("Agetha — Unexpected Error", f"Unexpected startup error:\n{e}")

    def _pick_idle_gif(self) -> str:
        """Pick idle-1/2/3 from host affection/heat so all three idle clips matter."""
        available = [g for g in self.IDLE_GIFS if g in self._gif_cache]
        if not available:
            return "idle-1.gif"
        try:
            from agetha.core.companion_stats import get_stats_summary
            stats = get_stats_summary()
            affection = float(stats.get("affection", 50))
            heat = float(stats.get("core_heat", 0))
            if affection >= 70 and "idle-1.gif" in self._gif_cache:
                return "idle-1.gif"
            if (affection < 35 or heat >= 70) and "idle-3.gif" in self._gif_cache:
                return "idle-3.gif"
            if "idle-2.gif" in self._gif_cache:
                return "idle-2.gif"
        except Exception:
            pass
        return random.choice(available)

    def _pick_talking_gif(self, mood: str = "neutral") -> str:
        """Pick talking-1/2/3 by mood band so all three talking clips matter."""
        preferred = self.TALKING_BY_MOOD.get(mood or "neutral")
        if preferred and preferred in self._gif_cache:
            return preferred
        if "talking-1.gif" in self._gif_cache:
            return "talking-1.gif"
        available = [g for g in self.TALKING_GIFS if g in self._gif_cache]
        return random.choice(available) if available else "talking-1.gif"

    def flash_error_gif(self, hold_ms: int = 2200) -> None:
        """Show error.gif briefly (denied actions / faults). Cosmetic only."""
        def _run() -> None:
            player = self._gif_cache.get("error.gif")
            if not player:
                return
            if self._current_gif_player:
                try:
                    self._current_gif_player.stop()
                except Exception:
                    pass
            self._current_gif_player = player
            player.play()
            self.root.after(
                max(400, int(hold_ms)),
                lambda: self._set_state(self.STATE_IDLE, "angry"),
            )
        try:
            if threading.current_thread() is not threading.main_thread():
                self.root.after(0, _run)
            else:
                _run()
        except Exception as exc:
            logger.debug(f"flash_error_gif skipped: {exc}")

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

    def _start_talking_rotation(self, mood: str = "neutral"):
        self._talking_rotate_mood = mood or "neutral"
        self._talking_rotate_idx = 0
        self._rotate_talking()

    def _rotate_talking(self):
        if self._state != self.STATE_TALKING:
            return
        available = [g for g in self.TALKING_GIFS if g in self._gif_cache]
        if available:
            idx = int(getattr(self, "_talking_rotate_idx", 0) or 0)
            mood = getattr(self, "_talking_rotate_mood", "neutral")
            if idx == 0:
                name = self._pick_talking_gif(mood)
                if name not in available:
                    name = available[0]
            else:
                name = available[idx % len(available)]
            self._play_gif(name)
            self._talking_rotate_idx = idx + 1
        delay = random.randint(1800, 3200)
        self._talking_rotate_job = self.root.after(delay, self._rotate_talking)

    def _stop_talking_rotation(self):
        if self._talking_rotate_job:
            self.root.after_cancel(self._talking_rotate_job)
            self._talking_rotate_job = None

    def _refresh_mood_glow(self) -> None:
        if self._closing or self._is_minimized or not hasattr(self, "_mood_glow"):
            return
        self._mood_glow.set_mood(getattr(self, "_current_display_mood", "neutral"))

    def _set_display_mood(self, mood: str) -> None:
        self._current_display_mood = mood or "neutral"
        self._refresh_mood_glow()

    def _play_response_motion(self, mood: str) -> None:
        """Schedule at most one motion for this completed response."""
        if self._closing or not hasattr(self, "_motion"):
            return
        if self._motion_request_job is not None:
            return

        def _play() -> None:
            self._motion_request_job = None
            if not self._closing:
                self._motion.play_mood(mood)

        self._motion_request_job = self.root.after(0, _play)

    def _set_state(self, state: str, mood: str = "neutral"):
        if self._closing:
            return
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda s=state, m=mood: self._set_state(s, m))
            return
        with self._state_lock:
            self._apply_state(state, mood)

    def _apply_state(self, state: str, mood: str = "neutral"):
        # Cancel any pending loaf/sleep timers when changing state
        try:
            if getattr(self, "_loaf_job", None):
                self.root.after_cancel(self._loaf_job)
                self._loaf_job = None
        except Exception:
            self._loaf_job = None
        try:
            if getattr(self, "_sleep_job", None):
                self.root.after_cancel(self._sleep_job)
                self._sleep_job = None
        except Exception:
            self._sleep_job = None
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
        _STICKY_MOODS = {
            "sad", "angry", "happy", "thinking", "excited", "manic",
            "melancholic", "vulnerable", "paranoid", "dominant", "whisper", "surprised",
        }

        if state == self.STATE_SLEEPING:
            self._persistent_mood = None
            self._play_gif("sleeping.gif")
            self._set_display_mood("sleeping")
        elif state == self.STATE_THINKING:
            self._persistent_mood = None
            self._play_gif_once_then("thinking.gif", "thinking-static.gif",
                                     guard=lambda: self._state == self.STATE_THINKING)
            self._set_display_mood("thinking")
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
                    self._play_gif(self._pick_idle_gif())
            self._set_display_mood(effective_mood)
            # Schedule loaf.gif after idle; sleep follows another idle period
            try:
                self._loaf_job = self.root.after(LOAF_TIMER_MS, self._enter_loaf)
            except Exception:
                self._loaf_job = None
        elif state == self.STATE_TALKING:
            if mood in _STICKY_MOODS:
                self._persistent_mood = mood
            else:
                self._persistent_mood = None
            talk_gif = self.TALKING_MOOD_GIFS.get(mood) or self.EXTRA_GIFS.get(mood)
            if talk_gif and str(talk_gif).endswith("-static.gif"):
                talk_gif = self.TALKING_MOOD_GIFS.get(mood)
            static_name = self.EXTRA_STATIC_GIFS.get(mood)
            if mood != "neutral" and talk_gif and talk_gif in self._gif_cache:
                if (
                    static_name
                    and static_name in self._gif_cache
                    and static_name != talk_gif
                    and not str(talk_gif).startswith("talking-")
                ):
                    self._talking_emotion_looping = False
                    self._play_gif_once_then_loop(talk_gif, mood)
                else:
                    self._play_gif(talk_gif)
            else:
                self._start_talking_rotation(mood)
            self._set_display_mood(mood)
            # Speech bleeps/TTS are started from _speak_and_continue / _try_short_mood_speak

    def _enter_loaf(self):
        # Only enter loaf if still idle
        try:
            if self._state == self.STATE_IDLE and "loaf.gif" in self._gif_cache:
                self._play_gif("loaf.gif")
                self._is_loafing = True
                try:
                    self._sleep_job = self.root.after(LOAF_TIMER_MS, self._enter_deep_sleep)
                except Exception:
                    self._sleep_job = None
        except Exception:
            pass

    def _enter_deep_sleep(self):
        """After prolonged loafing, sleep — cosmetic presence only, no OS side effects."""
        try:
            if self._state == self.STATE_IDLE and (
                getattr(self, "_is_loafing", False) or "sleeping.gif" in self._gif_cache
            ):
                self._is_loafing = False
                self._set_state(self.STATE_SLEEPING)
                # v4.0.0 — she dreams while deep-sleeping (memory/ only, no OS)
                try:
                    from agetha.core.dreams import generate_dream
                    threading.Thread(target=generate_dream, daemon=True).start()
                except Exception:
                    pass
                try:
                    from agetha.core.emotion_engine import apply_event
                    apply_event("sleep")
                except Exception:
                    pass
        except Exception:
            pass

    def _wake_from_presence_rest(self) -> None:
        """Leave loaf/sleep when the user interacts (chat, touch, keystroke)."""
        try:
            if getattr(self, "_sleep_job", None):
                self.root.after_cancel(self._sleep_job)
                self._sleep_job = None
        except Exception:
            self._sleep_job = None
        try:
            if self._state == self.STATE_SLEEPING or getattr(self, "_is_loafing", False):
                self._is_loafing = False
                if self._state == self.STATE_SLEEPING:
                    # v4.0.0 — arm one-shot dream recall for the next AI prompt
                    try:
                        from agetha.core.dreams import mark_wake_recall
                        mark_wake_recall()
                    except Exception:
                        pass
                    try:
                        from agetha.core.emotion_engine import apply_event
                        apply_event("wake")
                    except Exception:
                        pass
                    self._set_state(self.STATE_IDLE, "surprised")
        except Exception:
            pass

    def _start_wake_sequence(self):
        self._set_state(self.STATE_SLEEPING)
        if self._wake_job is not None:
            try:
                self.root.after_cancel(self._wake_job)
            except Exception:
                pass
        self._wake_job = self.root.after(WAKE_DELAY_MS, self._finish_wake)

    def _finish_wake(self):
        self._wake_job = None
        if self._closing:
            return
        self._set_state(self.STATE_IDLE, "neutral")
        self.root.after(1000, self._schedule_screen_poll)

    def _schedule_screen_poll(self):
        if self._closing:
            return
        if not get_settings().enable_ambient_polls:
            return
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        threading.Thread(target=self._ai_tick, daemon=True).start()

    def _reschedule_screen_poll(self):
        if self._closing:
            return
        if not get_settings().enable_ambient_polls:
            if self._poll_job:
                self.root.after_cancel(self._poll_job)
                self._poll_job = None
            return
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
        self._poll_job = self.root.after(
            get_settings().screen_poll_interval_ms,
            self._schedule_screen_poll,
        )
        try:
            from agetha.core.companion_stats import update_stats
            update_stats("tick")
        except Exception:
            pass
        try:
            from agetha.core.emotion_engine import tick as _emotion_tick
            crossed = _emotion_tick()
            if crossed:
                from agetha.core.emotional_history import record_event
                record_event(
                    "long_absence", importance=0.7,
                    summary=f"no interaction for a while ({crossed})",
                )
        except Exception:
            pass
        # v5.0.0 — optional coarse status observations (self-rate-limited)
        try:
            if get_settings().enable_status_providers:
                from agetha.features.status_providers import poll as _status_poll
                threading.Thread(target=_status_poll, daemon=True).start()
        except Exception:
            pass

    def _on_cancel_ai(self, event=None):
        """Escape — cancel an in-flight AI request."""
        self._cancel_event.set()
        self._re_enable_input()
        self.root.after(0, lambda: self._set_state(self.STATE_IDLE))
        self.root.after(0, lambda: self._subtitle.show_message("Cancelled.", "#888888"))

    @staticmethod
    def _fast_ambient_is_local_idle(
        monitor_status: str,
        raw_screen_text: str,
        previous_screen_text: str,
        *,
        repeated_event: bool = False,
        has_new_pattern_event: bool = False,
    ) -> bool:
        """Return True only for a confirmed no-new-screen-event state."""
        if has_new_pattern_event:
            return False
        if monitor_status in {
            "unchanged",
            "ocr_empty",
            "skipped_own_window",
            "skipped_excluded_window",
        }:
            return True
        current = (raw_screen_text or "").strip()
        previous = (previous_screen_text or "").strip()
        if repeated_event and current == previous:
            return True
        # A forced OCR refresh reports ``ocr_complete`` even when its text is
        # byte-for-byte identical to the cached event.
        return bool(current and previous and current == previous)

    @staticmethod
    def _compact_fast_ambient_context(text: str, max_chars: int = 720) -> str:
        """Keep a meaningful Fast ambient event small without losing its tags."""
        value = (text or "").strip()
        limit = max(80, int(max_chars))
        if len(value) <= limit:
            return value
        return value[: limit - 1].rstrip() + "…"

    @staticmethod
    def _has_pending_fast_ambient_context() -> bool:
        """Check one-shot local context without consuming it."""
        try:
            from agetha.features.status_providers import has_pending_observations
            if has_pending_observations():
                return True
        except Exception:
            pass
        try:
            from agetha.core.dreams import has_pending_wake_recall
            if has_pending_wake_recall():
                return True
        except Exception:
            pass
        return False

    def _fast_mode_runtime_active(self) -> bool:
        ai = getattr(self, "_ai", None)
        if not ai or not getattr(ai, "_faster_mode", False):
            return False
        try:
            from agetha.core.fast_mode_profile import is_fast_mode_profile_active
            return bool(is_fast_mode_profile_active())
        except Exception:
            return False

    def _ai_tick(self, user_message: str | None = None):
        if self._closing:
            return
        is_user = user_message is not None
        fast_mode = self._fast_mode_runtime_active()

        # Deep sleep: skip ambient polls (presence rest). User interaction still wakes her.
        if not is_user and self._state == self.STATE_SLEEPING:
            self._reschedule_screen_poll()
            return

        with self._ai_tick_lock:
            if self._ai_busy or self._speech_active:
                if is_user:
                    if not self._ai_busy_noninterruptible:
                        self._cancel_event.set()
                    self._pending_user_message = user_message
                else:
                    self._reschedule_screen_poll()
                return
            self._ai_busy = True

        if not self._ai:
            self._ai_busy = False
            self.root.after(0, self._re_enable_input)
            self._reschedule_screen_poll()
            self._drain_pending_user_message()
            return

        if self._closing:
            self._ai_busy = False
            return
        self._cancel_event.clear()

        if is_user:
            self.root.after(0, lambda: self._input_box.config(state="disabled"))
            if (
                user_message
                and user_message != "__touch__"
                and not str(user_message).strip().lower().startswith("[system]")
            ):
                try:
                    from agetha.core.companion_stats import classify_user_tone, update_stats
                    update_stats("user_chat")
                    tone = classify_user_tone(user_message)
                    if tone:
                        update_stats(tone)
                except Exception:
                    tone = None
                try:
                    from agetha.core.emotion_engine import note
                    note(tone or "user_chat")
                except Exception:
                    pass
            self.root.after(0, self._wake_from_presence_rest)

        screen_text = ""
        raw_screen_text = ""
        monitor_status = ""
        repeated_event = False
        has_new_pattern_event = False
        previous_screen_text = self._last_screen_text
        if self._screen:
            own_hwnd = None
            try:
                own_hwnd = self._screen._get_own_hwnd()
            except Exception:
                pass

            active_title = ""
            if _SETTINGS.include_window_title_in_context:
                active_title = self._screen.get_active_window_title(skip_hwnd=own_hwnd)

            typing_pause = _SETTINGS.ocr_pause_while_typing_sec
            # User messages bypass typing pause — they just typed and expect fresh screen context.
            recently_active = (
                not is_user
                and typing_pause > 0
                and (time.time() - self._last_direct_interaction_time) < typing_pause
            )

            if get_settings().enable_screen_reader and not recently_active:
                screen_text = self._screen.capture_text(
                    focused_only=_SETTINGS.ocr_focused_window_only,
                )
                raw_screen_text = screen_text
                monitor_status = getattr(self._screen, "last_monitor_status", "")
                preserve_previous_context = False
                if monitor_status == "skipped_excluded_window":
                    active_title = ""
                    screen_text = "[Screen OCR skipped for an excluded window.]"
                    preserve_previous_context = True
                elif monitor_status == "skipped_own_window":
                    active_title = ""
                    screen_text = "[Screen OCR skipped while Agetha has focus.]"
                    preserve_previous_context = True
                elif monitor_status == "ocr_empty":
                    screen_text = "[Screen OCR found no readable text.]"
                    preserve_previous_context = True
                elif monitor_status == "unchanged" and not is_user:
                    screen_text = "[Screen unchanged; no new OCR event.]"
                    preserve_previous_context = True
                if screen_text and not preserve_previous_context:
                    self._last_screen_text = screen_text

                _matches = getattr(
                    self._screen,
                    "last_pattern_matches" if is_user else "last_new_pattern_events",
                    [],
                )
                _current_matches = getattr(self._screen, "last_pattern_matches", [])
                if not is_user and _current_matches and not _matches:
                    screen_text = "[Repeated screen event suppressed; no new OCR event.]"
                    preserve_previous_context = True
                    repeated_event = True
                    if (
                        fast_mode
                        and (raw_screen_text or "").strip()
                        != (previous_screen_text or "").strip()
                    ):
                        # The old pattern is repeated, but other OCR text changed;
                        # keep that meaningful new event for the tiny Fast prompt.
                        screen_text = raw_screen_text
                        preserve_previous_context = False
                if _matches:
                    has_new_pattern_event = True
                    tags = "\n".join(f"[{m.label}: {m.snippet[:80]}]" for m in _matches[:4])
                    screen_text = tags + "\n" + screen_text
                elif is_user and getattr(self._screen, "has_angry_trigger", False):
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
                if _important and (is_user or bool(_matches)) and not preserve_previous_context:
                    pos_str = " | ".join(f"{p['text']}@({p['screen_x']},{p['screen_y']})" for p in _important)
                    screen_text = f"[Error positions: {pos_str}]\n" + screen_text
            elif active_title:
                screen_text = f"[Active: {active_title}]"
                self._last_screen_text = screen_text

        if (
            fast_mode
            and not is_user
            and self._fast_ambient_is_local_idle(
                monitor_status,
                raw_screen_text,
                previous_screen_text,
                repeated_event=repeated_event,
                has_new_pattern_event=has_new_pattern_event,
            )
            and not self._has_pending_fast_ambient_context()
        ):
            logger.debug(f"Fast ambient local idle: {monitor_status or 'repeated'}")
            self._ai_busy = False

            def _finish_local_idle() -> None:
                self._reschedule_screen_poll()
                self._drain_pending_user_message()

            self.root.after(0, _finish_local_idle)
            return

        ai_screen_context = screen_text or self._last_screen_text
        if fast_mode and not is_user:
            ai_screen_context = self._compact_fast_ambient_context(ai_screen_context)
        if self._screen:
            ai_screen_context = self._screen.redact_for_external_context(
                ai_screen_context,
            )

        self.root.after(0, lambda: self._set_state(self.STATE_THINKING))

        def _on_token(raw_so_far: str):
            if not self._cancel_event.is_set():
                self.root.after(0, lambda r=raw_so_far: self._subtitle.show_thinking(r))

        normalized_message = (user_message or "").strip().lower()
        if not is_user:
            request_profile = "fast_ambient"
        elif (
            normalized_message == "__touch__"
            or normalized_message.startswith("[system]")
            or normalized_message.startswith("[reminder]")
        ):
            request_profile = "fast_command"
        else:
            request_profile = "fast_user"

        try:
            if _SETTINGS.enable_streaming:
                response = self._ai.query_streaming(
                    screen_context=ai_screen_context,
                    user_message=user_message or "",
                    on_token=_on_token,
                    request_profile=request_profile,
                )
            else:
                response = self._ai.query(
                    screen_context=ai_screen_context,
                    user_message=user_message or "",
                    request_profile=request_profile,
                )
        except Exception as exc:
            if self._closing:
                self._ai_busy = False
                return
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
            self._drain_pending_user_message()
            return

        if self._cancel_event.is_set():
            self._ai_busy = False
            self.root.after(0, self._re_enable_input)
            self._drain_pending_user_message()
            return

        print("\n" + "-" * 52)
        if user_message and user_message != "__touch__":
            print(f"[USER]  {user_message}")
        print(f"[AI]    {json.dumps(response, ensure_ascii=False)}")
        print("-" * 52)

        self.root.after(0, self._re_enable_input)
        self.root.after(0, self._update_token_status)
        try:
            self._dispatch_response(response, user_message)
        finally:
            self._ai_busy = False
            self._run_deferred_ai_tick_callbacks()
            self._drain_pending_user_message()

    def _defer_after_ai_tick(self, callback: Callable[[], None]) -> None:
        """Run callback after the current _ai_tick releases _ai_busy (avoids _ai_query races)."""
        self._post_ai_tick_callbacks.append(callback)

    def _defer_exclusive_ai_operation(self, callback: Callable[[], None]) -> None:
        """Run one deferred operation while retaining the app-wide AI slot."""
        def _start() -> None:
            if self._closing:
                return
            with self._ai_tick_lock:
                if self._ai_busy or self._speech_active:
                    logger.warning("Deferred exclusive AI operation could not reserve its slot")
                    return
                self._ai_busy = True
                self._ai_busy_noninterruptible = True
            self._cancel_event.clear()
            self.root.after(0, lambda: self._input_box.config(state="disabled"))

            def _run() -> None:
                try:
                    callback()
                except Exception as exc:
                    logger.warning(f"Deferred exclusive AI operation failed: {exc}")
                finally:
                    with self._ai_tick_lock:
                        self._ai_busy_noninterruptible = False
                        self._ai_busy = False
                    try:
                        self.root.after(0, self._re_enable_input)
                    except Exception:
                        pass
                    self._run_deferred_ai_tick_callbacks()
                    self._drain_pending_user_message()

            threading.Thread(target=_run, daemon=True).start()

        self._defer_after_ai_tick(_start)

    def _run_deferred_ai_tick_callbacks(self) -> None:
        if self._closing:
            self._post_ai_tick_callbacks.clear()
            return
        callbacks = self._post_ai_tick_callbacks[:]
        self._post_ai_tick_callbacks.clear()
        for cb in callbacks:
            try:
                cb()
            except Exception as exc:
                logger.debug(f"deferred ai tick callback failed: {exc}")

    def _drain_pending_user_message(self) -> None:
        if self._closing:
            with self._ai_tick_lock:
                self._pending_user_message = None
            return
        pending: str | None
        with self._ai_tick_lock:
            pending = self._pending_user_message
            self._pending_user_message = None
        if pending is not None:
            threading.Thread(
                target=self._ai_tick,
                kwargs={"user_message": pending},
                daemon=True,
            ).start()

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
            self._speech_active = True

            def _begin_speech() -> None:
                self._set_state(self.STATE_TALKING, mood)
                try:
                    from agetha.ui.glitch_overlay import maybe_mood_glitch
                    maybe_mood_glitch(self.root, mood)
                except Exception:
                    pass
                if self._voice_out:
                    try:
                        self._voice_out.start_speech(segments, mood)
                    except Exception:
                        if self._bleep:
                            try:
                                self._bleep.start_talking(tone=mood)
                            except Exception:
                                pass
                elif self._bleep:
                    try:
                        self._bleep.start_talking(tone=mood)
                    except Exception:
                        pass
                self._subtitle.speak(
                    segments,
                    on_done=lambda: self._on_speech_done(shutdown_requested),
                )

            self.root.after(0, _begin_speech)
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
        from agetha.ui.w95_window import apply_borderless_win95, show_borderless

        dlg = tk.Toplevel(self.root)
        apply_borderless_win95(dlg, self.root, topmost=True)
        dlg.configure(bg=W95_BG)
        dlg.resizable(False, False)

        outer = tk.Frame(dlg, bg=W95_BG, relief="raised", bd=2)
        outer.pack(fill="both", expand=True)

        title_bar = tk.Frame(outer, bg=W95_TITLE_BG, height=_px(18))
        title_bar.pack(fill="x", padx=2, pady=(2, 0))
        title_bar.pack_propagate(False)
        tk.Label(
            title_bar, text="⚠  Pick window",
            bg=W95_TITLE_BG, fg=W95_TITLE_FG,
            font=W95_FONT_BOLD, anchor="w", padx=4,
        ).pack(side="left", fill="y")

        chosen: list[int | None] = [None]

        def _cancel():
            chosen[0] = None
            dlg.destroy()

        tk.Button(
            title_bar, text="✕",
            bg=W95_BTN_BG, fg=W95_TEXT, font=("MS Sans Serif", 7, "bold"),
            relief="raised", bd=2, width=2, command=_cancel,
        ).pack(side="right", padx=(0, 2), pady=1)

        body = tk.Frame(outer, bg=W95_BG, padx=10, pady=10)
        body.pack(fill="both", expand=True)
        tk.Label(
            body, text="Multiple windows match. Which one?",
            bg=W95_BG, fg=W95_TEXT, font=W95_FONT,
        ).pack(anchor="w", pady=(0, 4))
        frame = tk.Frame(body, bg=W95_BG)
        frame.pack(fill="both", expand=True)
        listbox = tk.Listbox(frame, width=58, height=min(8, len(matches)), font=W95_FONT)
        scroll = tk.Scrollbar(frame, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scroll.set)
        listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for _hwnd, title in matches:
            listbox.insert("end", title[:72])
        listbox.selection_set(0)

        def _ok(_event=None):
            sel = listbox.curselection()
            idx = sel[0] if sel else 0
            chosen[0] = matches[idx][0]
            dlg.destroy()

        btn_row = tk.Frame(outer, bg=W95_BG, pady=6)
        btn_row.pack(fill="x")
        tk.Button(
            btn_row, text="OK", font=W95_FONT_BOLD, bg=W95_BTN_BG, fg=W95_TEXT,
            relief="raised", bd=2, width=8, command=_ok,
        ).pack(side="left", padx=(16, 4))
        tk.Button(
            btn_row, text="Cancel", font=W95_FONT_BOLD, bg=W95_BTN_BG, fg=W95_TEXT,
            relief="raised", bd=2, width=8, command=_cancel,
        ).pack(side="left", padx=4)
        listbox.bind("<Double-Button-1>", _ok)
        dlg.protocol("WM_DELETE_WINDOW", _cancel)
        dlg.update_idletasks()
        try:
            px, py = self.root.winfo_x(), self.root.winfo_y()
            pw = self.root.winfo_width()
            ww = dlg.winfo_width() or 400
            wh = dlg.winfo_height() or 300
            sw = dlg.winfo_screenwidth()
            sh = dlg.winfo_screenheight()
            x = px + (pw - ww) // 2
            y = py - wh - 10
            x = max(0, min(x, sw - ww))
            y = max(0, min(y, sh - wh))
            dlg.geometry(f"+{x}+{y}")
        except Exception:
            pass
        show_borderless(dlg)
        dlg.grab_set()
        dlg.wait_window()
        return chosen[0]

    def _ai_query(
        self,
        user_message: str,
        screen_context=None,
        doc_content: str = "",
        memory_search_context: str = "",
        suppress_search_memory: bool = False,
        reserved_ai_slot: bool = False,
        request_profile: str | None = None,
    ):
        if self._cancel_event.is_set() or not self._ai:
            return None
        owns_ai_slot = not reserved_ai_slot
        with self._ai_tick_lock:
            if reserved_ai_slot:
                if not self._ai_busy or not self._ai_busy_noninterruptible:
                    return None
            else:
                if self._ai_busy or self._speech_active:
                    return None
                self._ai_busy = True
        self.root.after(0, lambda: self._set_state(self.STATE_THINKING))

        def _on_token(raw):
            if not self._cancel_event.is_set():
                self.root.after(0, lambda r=raw: self._subtitle.show_thinking(r))

        try:
            selected_screen_context = (
                self._last_screen_text if screen_context is None else screen_context
            )
            if self._screen:
                selected_screen_context = self._screen.redact_for_external_context(
                    selected_screen_context,
                )
            if _SETTINGS.enable_streaming:
                return self._ai.query_streaming(
                    screen_context=selected_screen_context,
                    user_message=user_message,
                    doc_content=doc_content,
                    memory_search_context=memory_search_context,
                    suppress_search_memory=suppress_search_memory,
                    on_token=_on_token,
                    request_profile=request_profile,
                )
            return self._ai.query(
                screen_context=selected_screen_context,
                user_message=user_message,
                doc_content=doc_content,
                memory_search_context=memory_search_context,
                suppress_search_memory=suppress_search_memory,
                request_profile=request_profile,
            )
        except Exception as exc:
            logger.error(f"_ai_query failed: {exc}")
            return None
        finally:
            if owns_ai_slot:
                self._ai_busy = False
                self._drain_pending_user_message()

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

            def _begin_short_speech() -> None:
                self._set_state(self.STATE_TALKING, mood)
                if self._voice_out:
                    try:
                        self._voice_out.start_speech(segments, mood)
                    except Exception:
                        if self._bleep:
                            try:
                                self._bleep.start_talking(tone=mood)
                            except Exception:
                                pass
                elif self._bleep:
                    try:
                        self._bleep.start_talking(tone=mood)
                    except Exception:
                        pass
                self._subtitle.speak(
                    segments,
                    on_done=lambda: self._on_speech_done(ctx.shutdown_requested),
                )

            self._speech_active = True
            self.root.after(12, lambda: self._play_gif(static_name))
            self.root.after(0, _begin_short_speech)
            return True
        except Exception:
            return False

    def _dispatch_response(self, response: dict, user_message: str | None = None):
        from agetha.commands.command_handlers import dispatch
        dispatch(self, response, user_message)

    def _on_speech_done(self, shutdown: bool = False):
        self._speech_active = False
        # _persistent_mood already set — _set_state(STATE_IDLE) picks it up
        self.root.after(0, lambda: self._set_state(self.STATE_IDLE))
        if shutdown:
            self.root.after(50, self._shutdown)
        else:
            self.root.after(0, self._reschedule_screen_poll)

    def _restore_from_tray(self) -> None:
        if self._closing:
            return
        try:
            self.root.deiconify()
            self.root.lift()
        except Exception:
            return
        self._resume_gif_playback()
        self._refresh_mood_glow()

    def _disable_input_for_close(self) -> None:
        self._closing = True
        self._cancel_event.set()
        try:
            self._input_box.config(state="disabled")
        except Exception:
            pass

    def _request_close(self) -> None:
        if self._closing or self._shutdown_complete:
            return
        try:
            from agetha.features.tray_scaffold import should_background_close
            if should_background_close():
                self._cancel_geometry_animation()
                self._mood_glow.cancel(reset=False)
                self._pause_gif_playback()
                self.root.withdraw()
                return
        except Exception:
            pass
        self._close_effect.request_close()

    def _shutdown(self):
        """Compatibility entry point used by tray and AI-requested app exits."""
        self._request_close()

    def _cancel_all_after_jobs(self) -> None:
        try:
            job_ids = tuple(self.root.tk.call("after", "info"))
        except Exception:
            job_ids = ()
        for job_id in job_ids:
            try:
                self.root.after_cancel(job_id)
            except Exception:
                pass

    def _graceful_shutdown(self) -> None:
        """Idempotently stop workers, timers, child UI, audio, and then destroy Tk."""
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        self._closing = True
        self._cancel_event.set()

        for controller, method, kwargs in (
            (getattr(self, "_close_effect", None), "cancel", {}),
            (getattr(self, "_mood_glow", None), "close", {}),
            (getattr(self, "_motion", None), "cancel_motion", {"restore": False}),
        ):
            if controller is not None:
                try:
                    getattr(controller, method)(**kwargs)
                except Exception:
                    pass
        self._cancel_geometry_animation()
        self._stop_talking_rotation()
        for attr in (
            "_poll_job", "_placeholder_refresh_job", "_restore_job", "_wake_job",
            "_motion_request_job", "_loaf_job", "_sleep_job",
        ):
            job_id = getattr(self, attr, None)
            if job_id is not None:
                try:
                    self.root.after_cancel(job_id)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._cancel_all_after_jobs()

        try:
            if self._subtitle:
                self._subtitle.stop()
        except Exception:
            pass
        for player in tuple(getattr(self, "_gif_cache", {}).values()):
            try:
                player.stop()
            except Exception:
                pass
        for resource in (
            self._voice, self._voice_out, self._bleep, getattr(self, "_screen", None),
        ):
            if resource is not None:
                try:
                    resource.stop()
                except Exception:
                    pass
        try:
            from agetha.features.tray_scaffold import stop_tray
            stop_tray()
        except Exception:
            pass
        if PYGAME_OK:
            try:
                if pygame.mixer.get_init():
                    pygame.mixer.stop()
                    pygame.mixer.quit()
            except Exception:
                pass

        # Memory and emotional history are persisted atomically when changed.
        self._cancel_all_after_jobs()
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        # v5.0.0 — optional tray icon (compatibility scaffold: silent no-op
        # unless ENABLE_TRAY=yes AND the optional pystray package is installed)
        try:
            from agetha.features.tray_scaffold import start_tray
            start_tray(self)
        except Exception:
            pass
        try:
            self.root.mainloop()
        finally:
            self._graceful_shutdown()


def _warn_if_no_api_key():
    """First-run hint when config exists but no AI backend is configured."""
    from agetha.app_config import get_settings, CONFIG_PATH, ENV_PATH
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
        "Add GROQ_API_KEY_1 to .env (not config.txt),\n"
        "or set USE_LOCAL_AI=yes and LOCAL_AI_MODEL.\n\n"
        "Optional: TESSERACT_PATH for screen reading."
    )
    title = "Agetha — Setup"
    try:
        native_message_box(title, msg, 0x30 | 0x1000)
    except Exception:
        print(f"[Agetha] {msg}")


def _early_config_check():
    """
    Ensure config.txt exists; always continue with defaults if missing or invalid.
    """
    from agetha.app_config import ensure_config_file, get_settings, CONFIG_PATH

    ensure_config_file(CONFIG_PATH, write_if_missing=True)
    get_settings(reload=True)
    refresh_config_constants()
    # Config warnings are already emitted via app_config._log_config (no secret names).

    _warn_if_no_api_key()


if __name__ == "__main__":
    _early_config_check()
    try:
        from agetha.platform.windows_notify import ensure_start_menu_shortcut, set_process_aumid
        set_process_aumid()
        ensure_start_menu_shortcut()
    except Exception as _aumid_exc:
        logger.debug(f"Windows AUMID / Start shortcut setup skipped: {_aumid_exc}")
    app = CompanionApp()
    app.run()
