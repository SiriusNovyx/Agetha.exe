"""Optional, cancellable Win95-compatible mood border effects."""

from __future__ import annotations

import math


BLACK = "#000000"

MOOD_COLOURS: dict[str, str] = {
    "neutral": "#36516b",
    "happy": "#b59638",
    "excited": "#b3652b",
    "sad": "#344d66",
    "surprised": "#665080",
    "thinking": "#397582",
    "whisper": "#566776",
    "angry": "#8a3030",
    "sleeping": "#101925",
    "manic": "#8a4e64",
    "melancholic": "#26384d",
    "paranoid": "#435946",
    "vulnerable": "#8496a6",
    "dominant": "#682d32",
}


def mood_colour(mood: str | None) -> str:
    return MOOD_COLOURS.get(str(mood or "").strip().lower(), MOOD_COLOURS["neutral"])


def _interpolate(start: str, end: str, amount: float) -> str:
    amount = max(0.0, min(1.0, amount))
    left = tuple(int(start[i:i + 2], 16) for i in (1, 3, 5))
    right = tuple(int(end[i:i + 2], 16) for i in (1, 3, 5))
    rgb = tuple(round(a + (b - a) * amount) for a, b in zip(left, right))
    return "#{:02x}{:02x}{:02x}".format(*rgb)


class MoodGlowController:
    """Own the single optional glow loop for a GIF border widget."""

    def __init__(
        self,
        root,
        widget,
        *,
        enabled: bool = False,
        animated: bool = True,
        interval_ms: int = 150,
        reduced_motion: bool = False,
    ) -> None:
        self.root = root
        self.widget = widget
        self.enabled = bool(enabled)
        self.animated = bool(animated) and not bool(reduced_motion)
        self.interval_ms = max(100, min(int(interval_ms), 1000))
        self._job_id = None
        self._mood = "neutral"
        self._phase = 0
        self._closed = False

    @property
    def job_id(self):
        return self._job_id

    def set_mood(self, mood: str | None) -> None:
        if self._closed:
            return
        self._mood = str(mood or "neutral").strip().lower()
        if not self.enabled:
            self.cancel(reset=True)
            return
        if not self.animated:
            self.cancel(reset=False)
            self._apply(mood_colour(self._mood), visible=True)
            return
        if self._job_id is None:
            self._tick()

    def _apply(self, colour: str, *, visible: bool) -> None:
        try:
            self.widget.configure(
                bg=colour,
                highlightbackground=colour,
                highlightcolor=colour,
                highlightthickness=1 if visible else 0,
            )
        except Exception:
            # A decorative failure must never disturb the main UI.
            pass

    def _tick(self) -> None:
        self._job_id = None
        if self._closed or not self.enabled or not self.animated:
            return
        try:
            # A full pulse takes roughly 3.2 seconds at the default interval.
            amount = 0.18 + 0.42 * ((math.sin(self._phase * math.pi / 10) + 1) / 2)
            target = mood_colour(self._mood)
            if self._mood == "manic":
                # Slowly wander between muted orange and purple; never flash.
                shift = (math.sin(self._phase * math.pi / 20) + 1) / 2
                target = _interpolate("#8b552e", "#624477", shift)
            colour = _interpolate(BLACK, target, amount)
            self._apply(colour, visible=True)
            self._phase = (self._phase + 1) % 40
            if not self._closed:
                self._job_id = self.root.after(self.interval_ms, self._tick)
        except Exception:
            self._job_id = None

    def cancel(self, *, reset: bool = True) -> None:
        if self._job_id is not None:
            try:
                self.root.after_cancel(self._job_id)
            except Exception:
                pass
            self._job_id = None
        if reset:
            self._apply(BLACK, visible=False)

    def close(self) -> None:
        self._closed = True
        self.cancel(reset=False)


__all__ = ["BLACK", "MOOD_COLOURS", "MoodGlowController", "mood_colour"]
