"""
glitch_overlay.py — Safe visual-only CRT glitch overlay (Phase 3B+).

Pure Tkinter Canvas drawing. No filesystem, registry, subprocess, or display changes.
Disabled by default via ENABLE_GLITCH_EFFECTS in config.txt.
"""

from __future__ import annotations

import random
import string
import tkinter as tk
from typing import TYPE_CHECKING

from agetha.app_config import get_settings
from agetha.utils import IS_WINDOWS, logger

if TYPE_CHECKING:
    from tkinter import Misc

VALID_GLITCH_STYLES = frozenset({
    "scanlines", "static", "rgb_split", "flicker", "bsod", "matrix", "tear",
})
_DEFAULT_STYLE = "scanlines"
_MIN_DURATION_MS = 200
_CORNER_W = 300
_CORNER_H = 200
_MAGENTA_KEY = "#ff00ff"
_MOOD_GLITCH_STYLES: dict[str, str] = {
    "manic": "flicker",
    "angry": "tear",
    "dominant": "bsod",
    "paranoid": "matrix",
}


def normalize_glitch_style(style: str | None) -> str:
    """Return a valid glitch style name; invalid values fall back to scanlines."""
    if not style:
        return _DEFAULT_STYLE
    key = str(style).strip().lower()
    return key if key in VALID_GLITCH_STYLES else _DEFAULT_STYLE


def clamp_glitch_duration(
    requested_ms: int | float | str | None,
    *,
    max_ms: int | None = None,
    min_ms: int = _MIN_DURATION_MS,
) -> int:
    """Clamp glitch overlay duration to configured safe bounds."""
    settings = get_settings()
    cap = max_ms if max_ms is not None else settings.glitch_max_duration_ms
    cap = max(min_ms, int(cap))
    try:
        raw = int(requested_ms) if requested_ms is not None else cap
    except (TypeError, ValueError):
        raw = cap
    return max(min_ms, min(raw, cap))


def maybe_mood_glitch(parent: tk.Misc, mood: str) -> None:
    """Occasionally trigger a brief mood-themed glitch (visual only)."""
    try:
        settings = get_settings()
        if not settings.enable_glitch_effects or not settings.glitch_mood_auto:
            return
        mood_key = (mood or "").strip().lower()
        style = _MOOD_GLITCH_STYLES.get(mood_key)
        if not style or random.random() > 0.16:
            return
        show_glitch_overlay(
            parent,
            style=style,
            duration_ms=settings.glitch_max_duration_ms,
            fullscreen=settings.glitch_fullscreen,
        )
    except Exception as exc:
        logger.warning(f"maybe_mood_glitch failed: {exc}")


def show_glitch_overlay(
    parent: tk.Misc,
    style: str = "",
    duration_ms: int | None = None,
    *,
    fullscreen: bool | None = None,
) -> None:
    """Show a brief, non-blocking visual glitch overlay; auto-destroys after duration."""
    try:
        if parent is None:
            logger.warning("glitch_overlay: parent is None")
            return
        if not IS_WINDOWS:
            logger.info("glitch_overlay skipped (unsupported on managed Linux windows)")
            return

        settings = get_settings()
        if not settings.enable_glitch_effects:
            logger.info("glitch_overlay skipped (ENABLE_GLITCH_EFFECTS=no)")
            return

        resolved_style = normalize_glitch_style(style or settings.glitch_default_style)
        duration = clamp_glitch_duration(duration_ms)
        use_fullscreen = settings.glitch_fullscreen if fullscreen is None else bool(fullscreen)

        def _spawn() -> None:
            try:
                _GlitchOverlay(parent, resolved_style, duration, fullscreen=use_fullscreen)
            except Exception as exc:
                logger.warning(f"glitch_overlay spawn failed: {exc}")

        try:
            parent.after(0, _spawn)
        except Exception:
            _spawn()
    except Exception as exc:
        logger.warning(f"show_glitch_overlay failed: {exc}")


class _GlitchOverlay:
    """Borderless topmost overlay; visual only, auto-closes."""

    def __init__(self, parent: tk.Misc, style: str, duration_ms: int, *, fullscreen: bool) -> None:
        self._parent = parent
        self._style = style
        self._duration_ms = duration_ms
        self._fullscreen = fullscreen
        self._alive = True
        self._tick = 0
        self._static_photo: object | None = None

        sw = int(parent.winfo_screenwidth() or 800)
        sh = int(parent.winfo_screenheight() or 600)

        self._win = tk.Toplevel(parent)
        self._win.overrideredirect(True)
        try:
            self._win.attributes("-topmost", True)
        except Exception:
            pass

        if fullscreen:
            w, h, x, y = sw, sh, 0, 0
            try:
                self._win.attributes("-alpha", 0.88)
            except Exception:
                pass
        else:
            w, h = _CORNER_W, _CORNER_H
            x, y = max(0, sw - w - 12), 12
            if IS_WINDOWS:
                try:
                    self._win.configure(bg=_MAGENTA_KEY)
                    self._win.attributes("-transparentcolor", _MAGENTA_KEY)
                except Exception:
                    try:
                        self._win.attributes("-alpha", 0.78)
                    except Exception:
                        pass
            else:
                try:
                    self._win.attributes("-alpha", 0.78)
                except Exception:
                    pass

        self._win.geometry(f"{w}x{h}+{x}+{y}")
        self._w, self._h = w, h

        bg = "#101010" if (fullscreen or not IS_WINDOWS) else _MAGENTA_KEY
        self._canvas = tk.Canvas(
            self._win,
            width=w,
            height=h,
            highlightthickness=0,
            bd=0,
            bg=bg,
        )
        self._canvas.pack(fill=tk.BOTH, expand=True)

        try:
            self._win.update_idletasks()
        except Exception:
            pass

        self._draw_frame()
        self._win.after(duration_ms, self._destroy)
        if style in ("flicker", "matrix"):
            self._animate()

    def _destroy(self) -> None:
        if not self._alive:
            return
        self._alive = False
        try:
            self._win.destroy()
        except Exception:
            pass

    def _animate(self) -> None:
        if not self._alive:
            return
        self._tick += 1
        try:
            self._canvas.delete("all")
            if self._style == "matrix":
                self._draw_matrix_frame()
            elif self._tick % 2 == 0:
                self._draw_scanlines()
            else:
                shade = random.choice(("#1a1a1a", "#252525", "#0d0d0d"))
                self._canvas.create_rectangle(0, 0, self._w, self._h, fill=shade, outline="")
        except Exception as exc:
            logger.warning(f"glitch animate failed: {exc}")
        if self._tick < 16 and self._alive:
            self._win.after(70, self._animate)

    def _draw_frame(self) -> None:
        try:
            self._canvas.delete("all")
            draw_fn = {
                "scanlines": self._draw_scanlines,
                "static": self._draw_static,
                "rgb_split": self._draw_rgb_split,
                "flicker": self._draw_scanlines,
                "bsod": self._draw_bsod,
                "matrix": self._draw_matrix_frame,
                "tear": self._draw_tear,
            }.get(self._style, self._draw_scanlines)
            draw_fn()
        except Exception as exc:
            logger.warning(f"glitch draw failed ({self._style}): {exc}")

    def _draw_scanlines(self) -> None:
        for y in range(0, self._h, 3):
            color = "#1a3a1a" if (y // 3) % 2 == 0 else "#0a1f0a"
            self._canvas.create_line(0, y, self._w, y, fill=color, width=2)
        self._canvas.create_rectangle(2, 2, self._w - 2, self._h - 2, outline="#00ff66", width=1)

    def _draw_static(self) -> None:
        try:
            import numpy as np
            from PIL import Image, ImageTk

            grid_w, grid_h = 160, 120
            noise = np.random.randint(40, 221, (grid_h, grid_w), dtype=np.uint8)
            img = Image.fromarray(noise, mode="L")
            img = img.resize((self._w, self._h), Image.NEAREST)
            photo = ImageTk.PhotoImage(img)
            self._static_photo = photo
            self._canvas.create_image(0, 0, anchor="nw", image=photo)
            return
        except Exception:
            pass

        self._canvas.create_rectangle(0, 0, self._w, self._h, fill="#0a0a0a", outline="")
        n = min(2500, self._w * self._h // 2)
        for _ in range(n):
            x = random.randint(0, max(0, self._w - 1))
            y = random.randint(0, max(0, self._h - 1))
            gray = random.randint(40, 220)
            color = f"#{gray:02x}{gray:02x}{gray:02x}"
            self._canvas.create_rectangle(x, y, x + 1, y + 1, fill=color, outline="")

    def _draw_rgb_split(self) -> None:
        self._canvas.create_rectangle(0, 0, self._w, self._h, fill="#111111", outline="")
        offset = max(3, self._w // 80)
        for y in range(0, self._h, 6):
            self._canvas.create_line(offset, y, self._w, y, fill="#ff2244", width=1)
            self._canvas.create_line(0, y + 2, self._w - offset, y + 2, fill="#22ff88", width=1)
            self._canvas.create_line(-offset, y + 4, self._w, y + 4, fill="#4488ff", width=1)

    def _draw_bsod(self) -> None:
        self._canvas.create_rectangle(0, 0, self._w, self._h, fill="#0000aa", outline="")
        fs = max(8, min(14, self._w // 28))
        self._canvas.create_text(
            16, 20, anchor="nw", fill="#ffffff",
            font=("Courier New", fs, "bold"),
            text=":(  AGETHA.EXE",
        )
        body = (
            "Your display encountered a harmless glitch.\n"
            "This is a visual joke only.\n"
            "Nothing was harmed. Probably."
        )
        self._canvas.create_text(
            16, 52, anchor="nw", fill="#ffffff",
            font=("Courier New", max(7, fs - 1)),
            text=body, width=max(80, self._w - 32),
        )

    def _draw_matrix_frame(self) -> None:
        self._canvas.create_rectangle(0, 0, self._w, self._h, fill="#000a00", outline="")
        large = self._fullscreen or self._w >= 800
        col_step = 28 if large else 14
        row_step = 24 if large else 12
        spawn_prob = 0.2 if large else 0.35
        font_size = random.randint(10, 12) if large else 8
        cols = max(8, self._w // col_step)
        for col in range(cols):
            x = col * col_step + random.randint(0, 4)
            for row in range(0, self._h, row_step):
                if random.random() < spawn_prob:
                    ch = random.choice(string.ascii_letters + string.digits)
                    green = random.randint(80, 220)
                    color = f"#00{green:02x}00"
                    self._canvas.create_text(
                        x, row, text=ch, fill=color, font=("Courier", font_size),
                    )

    def _draw_tear(self) -> None:
        self._canvas.create_rectangle(0, 0, self._w, self._h, fill="#0c0c0c", outline="")
        tears = max(3, self._h // 60)
        for _ in range(tears):
            y = random.randint(0, max(0, self._h - 20))
            h = random.randint(6, 28)
            shift = random.randint(-18, 18)
            self._canvas.create_rectangle(0, y, self._w, y + h, fill="#1a1a2e", outline="")
            self._canvas.create_line(shift, y, self._w + shift, y, fill="#ff3366", width=2)
