"""Non-activating Win95 status window for an explicit Computer Use session.

The window renders an already-sanitized snapshot.  It never observes the
desktop, calls a provider, or performs input itself; STOP only invokes the
session owner's cancellation callback.
"""

from __future__ import annotations

import re
import sys
import tkinter as tk
from dataclasses import dataclass
from typing import Callable

from agetha.ui.display_scale import scale_px
from agetha.ui.w95_window import apply_borderless_win95, strip_native_caption
from agetha.utils import apply_window_icon, logger


def _safe_line(value: object, maximum: int) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max(0, int(maximum))]


@dataclass(frozen=True)
class ComputerUseStatusView:
    goal: str
    target: str
    step: int
    max_steps: int
    last_action: str = ""
    last_result: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "goal", _safe_line(self.goal, 180) or "Computer task")
        object.__setattr__(self, "target", _safe_line(self.target, 80) or "Waiting for target")
        object.__setattr__(self, "step", max(0, int(self.step)))
        object.__setattr__(self, "max_steps", max(1, min(100, int(self.max_steps))))
        object.__setattr__(self, "last_action", _safe_line(self.last_action, 140))
        object.__setattr__(self, "last_result", _safe_line(self.last_result, 140))


class ComputerUseStatusWindow:
    """Small non-modal status panel with an exact-once STOP callback."""

    def __init__(
        self,
        parent: tk.Misc,
        view: ComputerUseStatusView,
        *,
        on_stop: Callable[[], None],
    ) -> None:
        self._closed = False
        self._stop_sent = False
        self._on_stop = on_stop
        try:
            scale = float(getattr(parent, "_agetha_ui_scale", 1.0))
        except (TypeError, ValueError):
            scale = 1.0

        def px(value: int) -> int:
            return scale_px(value, scale)

        self.win = tk.Toplevel(parent)
        self.win.title("Agetha — Computer Use")
        apply_borderless_win95(self.win, None, topmost=False)
        apply_window_icon(self.win)
        self.win.configure(bg="#c0c0c0")
        width, height = px(420), px(255)
        x = max(0, self.win.winfo_screenwidth() - width - px(24))
        y = max(0, self.win.winfo_screenheight() - height - px(48))
        self.win.geometry(f"{width}x{height}+{x}+{y}")

        outer = tk.Frame(self.win, bg="#c0c0c0", relief="raised", bd=2)
        outer.pack(fill="both", expand=True)
        title = tk.Frame(outer, bg="#000080", height=px(20))
        title.pack(fill="x", padx=2, pady=(2, 0))
        title.pack_propagate(False)
        tk.Label(
            title,
            text="Agetha — Computer Use",
            bg="#000080",
            fg="#ffffff",
            font=("MS Sans Serif", 8, "bold"),
            anchor="w",
            padx=4,
        ).pack(side="left", fill="y")
        tk.Button(
            title,
            text="✕",
            command=self.request_stop,
            width=2,
            bg="#c0c0c0",
            relief="raised",
            bd=2,
            font=("MS Sans Serif", 7, "bold"),
        ).pack(side="right", padx=(0, 2), pady=1)

        body = tk.Frame(outer, bg="#c0c0c0")
        body.pack(fill="both", expand=True, padx=px(10), pady=px(8))
        self._values: dict[str, tk.StringVar] = {}
        for key, label in (
            ("goal", "Goal"),
            ("target", "Target"),
            ("step", "Step"),
            ("last_action", "Last action"),
            ("last_result", "Last result"),
        ):
            row = tk.Frame(body, bg="#c0c0c0")
            row.pack(fill="x", pady=1)
            tk.Label(
                row,
                text=f"{label}:",
                width=13,
                anchor="w",
                bg="#c0c0c0",
                fg="#000000",
                font=("MS Sans Serif", 8, "bold"),
            ).pack(side="left")
            variable = tk.StringVar(self.win)
            self._values[key] = variable
            tk.Label(
                row,
                textvariable=variable,
                anchor="w",
                justify="left",
                wraplength=px(285),
                bg="#c0c0c0",
                fg="#000000",
                font=("MS Sans Serif", 8),
            ).pack(side="left", fill="x", expand=True)

        tk.Button(
            outer,
            text="STOP",
            command=self.request_stop,
            width=14,
            bg="#c0c0c0",
            fg="#800000",
            relief="raised",
            bd=2,
            font=("MS Sans Serif", 9, "bold"),
        ).pack(side="bottom", pady=(0, px(10)))

        self.win.bind("<Escape>", lambda _event: self.request_stop(), add="+")
        self.win.protocol("WM_DELETE_WINDOW", self.request_stop)
        self.update(view)
        self._show_without_activation()

    def update(self, view: ComputerUseStatusView) -> None:
        if self._closed:
            return
        self._values["goal"].set(view.goal)
        self._values["target"].set(view.target)
        self._values["step"].set(f"{view.step} / {view.max_steps}")
        self._values["last_action"].set(view.last_action or "Waiting")
        self._values["last_result"].set(view.last_result or "—")

    def _show_without_activation(self) -> None:
        try:
            self.win.update_idletasks()
            if sys.platform == "win32":
                import ctypes

                user32 = ctypes.windll.user32
                widget_id = int(self.win.winfo_id())
                hwnd = int(user32.GetParent(widget_id) or widget_id)
                style = int(user32.GetWindowLongW(hwnd, -20))
                user32.SetWindowLongW(hwnd, -20, style | 0x80 | 0x08000000)
                strip_native_caption(self.win)
                user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
            else:
                self.win.deiconify()
        except Exception as exc:
            logger.debug("Computer Use status show fallback: %s", type(exc).__name__)
            try:
                self.win.deiconify()
            except Exception:
                self.close()

    def request_stop(self) -> None:
        if self._closed or self._stop_sent:
            return
        self._stop_sent = True
        try:
            self._on_stop()
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.win.destroy()
        except Exception as exc:
            logger.debug("Computer Use status close skipped: %s", type(exc).__name__)


def open_computer_use_status(
    parent: tk.Misc,
    view: ComputerUseStatusView,
    *,
    on_stop: Callable[[], None],
) -> ComputerUseStatusWindow:
    return ComputerUseStatusWindow(parent, view, on_stop=on_stop)


__all__ = [
    "ComputerUseStatusView", "ComputerUseStatusWindow", "open_computer_use_status",
]
