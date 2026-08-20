"""Win95-style metadata preview for guarded Unicode text entry.

The platform typing module decides *when* a preview is required.  This module
only renders already-sanitized display data and reports the user's decision;
it performs no clipboard, focus, keyboard, provider, or command work.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from agetha.platform.unicode_typing import TypingPreview
from agetha.ui.display_scale import scale_px
from agetha.ui.w95_window import apply_borderless_win95, show_borderless
from agetha.utils import apply_window_icon, logger


W95_BG = "#c0c0c0"
W95_TEXT = "#000000"
W95_TITLE_BG = "#000080"
W95_TITLE_FG = "#ffffff"
W95_INPUT_BG = "#ffffff"


class TypingPreviewDialog:
    """Non-blocking dialog that invokes ``on_decision`` exactly once."""

    def __init__(
        self,
        parent: tk.Misc,
        preview: TypingPreview,
        *,
        content_preview: str,
        on_decision: Callable[[bool], None],
        preview_only: bool = False,
    ) -> None:
        self._on_decision = on_decision
        self._finished = False
        try:
            scale = float(getattr(parent, "_agetha_ui_scale", 1.0))
        except (TypeError, ValueError):
            scale = 1.0

        def px(value: int) -> int:
            return scale_px(value, scale)

        self.win = tk.Toplevel(parent)
        self.win.title("Agetha — Typing Preview")
        apply_borderless_win95(self.win, parent, topmost=True)
        apply_window_icon(self.win)
        self.win.configure(bg=W95_BG)
        self.win.geometry(f"{px(520)}x{px(430)}")
        self.win.minsize(px(440), px(360))

        outer = tk.Frame(self.win, bg=W95_BG, relief="raised", bd=2)
        outer.pack(fill="both", expand=True)
        title = tk.Frame(outer, bg=W95_TITLE_BG, height=px(20))
        title.pack(fill="x", padx=2, pady=(2, 0))
        title.pack_propagate(False)
        title_label = tk.Label(
            title,
            text="⌨  Agetha — Typing Preview",
            bg=W95_TITLE_BG,
            fg=W95_TITLE_FG,
            font=("MS Sans Serif", 8, "bold"),
            anchor="w",
            padx=4,
        )
        title_label.pack(side="left", fill="y")
        tk.Button(
            title,
            text="✕",
            command=lambda: self.finish(False),
            width=2,
            bg=W95_BG,
            fg=W95_TEXT,
            relief="raised",
            bd=2,
            font=("MS Sans Serif", 7, "bold"),
        ).pack(side="right", padx=(0, 2), pady=1)

        drag = {"x": 0, "y": 0, "wx": 0, "wy": 0}

        def drag_start(event: tk.Event) -> None:
            drag.update(
                x=event.x_root,
                y=event.y_root,
                wx=self.win.winfo_x(),
                wy=self.win.winfo_y(),
            )

        def drag_move(event: tk.Event) -> None:
            self.win.geometry(
                f"+{drag['wx'] + event.x_root - drag['x']}"
                f"+{drag['wy'] + event.y_root - drag['y']}"
            )

        for widget in (title, title_label):
            widget.bind("<ButtonPress-1>", drag_start)
            widget.bind("<B1-Motion>", drag_move)

        body = tk.Frame(outer, bg=W95_BG)
        body.pack(fill="both", expand=True, padx=px(10), pady=px(8))
        fields = (
            ("Target application", preview.target_application),
            ("Window title", preview.target_window_title or "(unavailable)"),
            ("Characters", str(preview.character_count)),
            ("Lines", str(preview.line_count)),
            ("Planned method", preview.method),
            (
                "Clipboard fallback",
                "may be used" if preview.clipboard_fallback_may_be_used else "not planned",
            ),
            ("Reversible", "yes" if preview.reversible else "no"),
            ("Review reasons", ", ".join(preview.reasons) or "explicit preview"),
        )
        for label, value in fields:
            row = tk.Frame(body, bg=W95_BG)
            row.pack(fill="x", pady=1)
            tk.Label(
                row,
                text=f"{label}:",
                width=20,
                anchor="w",
                bg=W95_BG,
                fg=W95_TEXT,
                font=("MS Sans Serif", 8, "bold"),
            ).pack(side="left")
            tk.Label(
                row,
                text=value,
                anchor="w",
                justify="left",
                wraplength=px(300),
                bg=W95_BG,
                fg=W95_TEXT,
                font=("MS Sans Serif", 8),
            ).pack(side="left", fill="x", expand=True)

        tk.Label(
            body,
            text="Content preview (sensitive values are hidden):",
            anchor="w",
            bg=W95_BG,
            fg=W95_TEXT,
            font=("MS Sans Serif", 8, "bold"),
        ).pack(fill="x", pady=(px(8), 2))
        content = tk.Text(
            body,
            height=7,
            wrap="word",
            bg=W95_INPUT_BG,
            fg=W95_TEXT,
            relief="sunken",
            bd=2,
            font=("Courier New", 9),
        )
        content.pack(fill="both", expand=True)
        content.insert("1.0", content_preview)
        content.configure(state="disabled")

        buttons = tk.Frame(outer, bg=W95_BG)
        buttons.pack(fill="x", padx=px(10), pady=(0, px(10)))
        if not preview_only:
            tk.Button(
                buttons,
                text="Cancel",
                width=12,
                command=lambda: self.finish(False),
                bg=W95_BG,
                relief="raised",
                bd=2,
                font=("MS Sans Serif", 8),
            ).pack(side="right")
        tk.Button(
            buttons,
            text="Close" if preview_only else "Type text",
            width=12,
            command=lambda: self.finish(True),
            bg=W95_BG,
            relief="raised",
            bd=2,
            font=("MS Sans Serif", 8, "bold"),
        ).pack(side="right", padx=(0, 6))

        self.win.protocol("WM_DELETE_WINDOW", lambda: self.finish(False))
        show_borderless(self.win)

    def finish(self, approved: bool) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            self.win.destroy()
        except Exception as exc:
            logger.debug("Typing preview close skipped: %s", type(exc).__name__)
        self._on_decision(bool(approved))

    def close(self) -> None:
        self.finish(False)


def open_typing_preview(
    parent: tk.Misc,
    preview: TypingPreview,
    *,
    content_preview: str,
    on_decision: Callable[[bool], None],
    preview_only: bool = False,
) -> TypingPreviewDialog:
    return TypingPreviewDialog(
        parent,
        preview,
        content_preview=content_preview,
        on_decision=on_decision,
        preview_only=preview_only,
    )


__all__ = ["TypingPreviewDialog", "open_typing_preview"]
