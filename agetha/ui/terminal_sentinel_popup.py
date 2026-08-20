"""No-activation Win95 notification for Terminal Sentinel."""

from __future__ import annotations

import sys
import tkinter as tk
from typing import Callable

from agetha.features.terminal_sentinel import SentinelNotification
from agetha.ui.display_scale import scale_px
from agetha.ui.w95_window import apply_borderless_win95, strip_native_caption
from agetha.utils import apply_window_icon, logger


class TerminalSentinelPopup:
    """Local three-action popup that never requests foreground focus itself."""

    def __init__(
        self,
        parent: tk.Misc,
        notification: SentinelNotification,
        *,
        on_explain: Callable[[], None],
        on_dismiss: Callable[[], None],
        on_ignore: Callable[[], None],
        on_close: Callable[["TerminalSentinelPopup"], None] | None = None,
    ) -> None:
        self._closed = False
        self._on_close = on_close
        self._callbacks = {
            "explain": on_explain,
            "dismiss": on_dismiss,
            "ignore": on_ignore,
        }
        try:
            scale = float(getattr(parent, "_agetha_ui_scale", 1.0))
        except (TypeError, ValueError):
            scale = 1.0

        def px(value: int) -> int:
            return scale_px(value, scale)

        self.win = tk.Toplevel(parent)
        self.win.title("Agetha — Terminal Sentinel")
        # Deliberately omit a transient owner and topmost/lift behavior: this is
        # an advisory notification, not permission to steal the user's focus.
        apply_borderless_win95(self.win, None, topmost=False)
        apply_window_icon(self.win)
        self.win.configure(bg="#c0c0c0")
        width, height = px(430), px(245)
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
            text="⚠  Agetha — Terminal Sentinel",
            bg="#000080",
            fg="#ffffff",
            font=("MS Sans Serif", 8, "bold"),
            anchor="w",
            padx=4,
        ).pack(side="left", fill="y")
        tk.Button(
            title,
            text="✕",
            command=lambda: self._choose("dismiss"),
            width=2,
            bg="#c0c0c0",
            relief="raised",
            bd=2,
            font=("MS Sans Serif", 7, "bold"),
        ).pack(side="right", padx=(0, 2), pady=1)

        body = tk.Frame(outer, bg="#c0c0c0")
        body.pack(fill="both", expand=True, padx=px(10), pady=px(8))
        tk.Label(
            body,
            text=notification.message,
            bg="#c0c0c0",
            fg="#000000",
            font=("MS Sans Serif", 9, "bold"),
            anchor="w",
            justify="left",
            wraplength=px(390),
        ).pack(fill="x")
        destination = " — ".join(
            item for item in (notification.application, notification.window_title) if item
        )
        tk.Label(
            body,
            text=destination,
            bg="#c0c0c0",
            fg="#404040",
            font=("MS Sans Serif", 8),
            anchor="w",
            wraplength=px(390),
        ).pack(fill="x", pady=(2, 5))
        snippet = notification.snippet or "Validated error pattern; details withheld."
        tk.Label(
            body,
            text=snippet[:500],
            bg="#ffffff",
            fg="#000000",
            relief="sunken",
            bd=2,
            padx=5,
            pady=5,
            font=("Courier New", 8),
            anchor="nw",
            justify="left",
            wraplength=px(380),
        ).pack(fill="both", expand=True)

        buttons = tk.Frame(outer, bg="#c0c0c0")
        buttons.pack(fill="x", padx=px(10), pady=(0, px(10)))
        for label, action in (
            ("Explain", "explain"),
            ("Dismiss", "dismiss"),
            ("Ignore Pattern", "ignore"),
        ):
            tk.Button(
                buttons,
                text=label,
                command=lambda selected=action: self._choose(selected),
                bg="#c0c0c0",
                relief="raised",
                bd=2,
                font=("MS Sans Serif", 8, "bold" if action == "explain" else "normal"),
                width=14,
            ).pack(side="left", padx=(0, 6))

        self.win.protocol("WM_DELETE_WINDOW", lambda: self._choose("dismiss"))
        self._show_without_activation()

    def _show_without_activation(self) -> None:
        try:
            self.win.update_idletasks()
            if sys.platform == "win32":
                import ctypes

                user32 = ctypes.windll.user32
                widget_id = int(self.win.winfo_id())
                hwnd = int(user32.GetParent(widget_id) or widget_id)
                gwl_exstyle = -20
                ws_ex_toolwindow = 0x00000080
                ws_ex_noactivate = 0x08000000
                style = int(user32.GetWindowLongW(hwnd, gwl_exstyle))
                user32.SetWindowLongW(
                    hwnd,
                    gwl_exstyle,
                    style | ws_ex_toolwindow | ws_ex_noactivate,
                )
                strip_native_caption(self.win)
                user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
            else:
                self.win.deiconify()
        except Exception as exc:
            logger.warning("Terminal Sentinel popup could not be shown: %s", type(exc).__name__)
            try:
                self.win.deiconify()
            except Exception as fallback_exc:
                logger.debug(
                    "Terminal Sentinel popup fallback failed: %s",
                    type(fallback_exc).__name__,
                )
                self.close()

    def _choose(self, action: str) -> None:
        if self._closed:
            return
        callback = self._callbacks.get(action)
        self.close()
        if callback is not None:
            callback()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.win.destroy()
        except Exception as exc:
            logger.debug("Terminal Sentinel popup close skipped: %s", type(exc).__name__)
        if self._on_close is not None:
            self._on_close(self)


__all__ = ["TerminalSentinelPopup"]
