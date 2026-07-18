"""
virus_trivia.py — Win95-style virus trivia minigame (visual only, no system changes).
"""

from __future__ import annotations

import random
import tkinter as tk
from tkinter import messagebox

from agetha.utils import logger

# Duplicated Win95 palette (no main.py import)
W95_BG = "#c0c0c0"
W95_TITLE_BG = "#000080"
W95_TITLE_FG = "#ffffff"
W95_TEXT = "#000000"
W95_BTN_BG = "#c0c0c0"
W95_FONT = ("MS Sans Serif", 8)
W95_FONT_BOLD = ("MS Sans Serif", 8, "bold")

_QUESTIONS: list[tuple[str, list[str], int]] = [
    (
        "What was the first widespread PC virus (1986)?",
        ["Brain", "ILOVEYOU", "Melissa", "Stuxnet"],
        0,
    ),
    (
        "What does 'malware' stand for?",
        ["Malicious software", "Managed firmware", "Machine learning ware", "Mail-aware"],
        0,
    ),
    (
        "Which protocol is safer for login pages?",
        ["HTTPS", "HTTP", "FTP", "Telnet"],
        0,
    ),
    (
        "A 'worm' differs from a 'virus' because it…",
        ["Spreads without a host file", "Only affects Macs", "Is always harmless", "Needs a CD-ROM"],
        0,
    ),
    (
        "What is phishing?",
        ["Tricking users into revealing secrets", "Cleaning a hard drive", "A GPU benchmark", "A Windows update"],
        0,
    ),
    (
        "Which is best practice for passwords?",
        ["Unique + long + manager", "Same password everywhere", "Your pet's name", "password123"],
        0,
    ),
    (
        "What does OCR stand for?",
        ["Optical Character Recognition", "Online Code Runtime", "Open CPU Relay", "Output Cache Reset"],
        0,
    ),
    (
        "The Morris worm (1988) targeted…",
        ["Unix systems", "Only printers", "Smart fridges", "Game consoles"],
        0,
    ),
    (
        "Agetha claims she is a…",
        ["Digital virus", "Cloud assistant", "Spreadsheet macro", "Screensaver"],
        0,
    ),
    (
        "Sandboxing helps by…",
        ["Isolating untrusted code", "Deleting all files", "Overclocking CPU", "Disabling the keyboard"],
        0,
    ),
]


def open_virus_trivia(parent: tk.Misc) -> None:
    """Open a small Win95 trivia window. Never raises."""
    try:
        if parent is None:
            return
        parent.after(0, lambda: _TriviaGame(parent))
    except Exception as exc:
        logger.warning(f"open_virus_trivia failed: {exc}")


class _TriviaGame:
    def __init__(self, parent: tk.Misc) -> None:
        from agetha.ui.w95_window import apply_borderless_win95, show_borderless

        self._win = tk.Toplevel(parent)
        apply_borderless_win95(self._win, parent, topmost=True)
        self._win.configure(bg=W95_BG)
        self._win.geometry("420x300")
        self._win.resizable(False, False)

        outer = tk.Frame(self._win, bg=W95_BG, relief="raised", bd=2)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg=W95_TITLE_BG, height=18)
        header.pack(fill="x", padx=2, pady=(2, 0))
        header.pack_propagate(False)
        title_lbl = tk.Label(
            header, text="⚠  Virus Trivia.exe", bg=W95_TITLE_BG, fg=W95_TITLE_FG,
            font=W95_FONT_BOLD, anchor="w", padx=4,
        )
        title_lbl.pack(side="left", fill="y")
        tk.Button(
            header, text="✕", font=("MS Sans Serif", 7, "bold"),
            bg=W95_BTN_BG, relief="raised", bd=2, width=2,
            command=self._win.destroy,
        ).pack(side="right", padx=(0, 2), pady=1)

        self._drag_x = self._drag_y = 0
        self._win_x = self._win_y = 0

        def _drag_start(e: tk.Event) -> None:
            self._drag_x, self._drag_y = e.x_root, e.y_root
            self._win_x, self._win_y = self._win.winfo_x(), self._win.winfo_y()

        def _drag_motion(e: tk.Event) -> None:
            dx = e.x_root - self._drag_x
            dy = e.y_root - self._drag_y
            self._win_x += dx
            self._win_y += dy
            self._win.geometry(f"+{self._win_x}+{self._win_y}")
            self._drag_x, self._drag_y = e.x_root, e.y_root

        for w in (header, title_lbl):
            w.bind("<ButtonPress-1>", _drag_start)
            w.bind("<B1-Motion>", _drag_motion)

        body = tk.Frame(outer, bg=W95_BG)
        body.pack(fill="both", expand=True, padx=2, pady=2)

        self._score = 0
        self._index = 0
        self._answering = False
        self._deck = random.sample(_QUESTIONS, min(5, len(_QUESTIONS)))

        self._prompt = tk.Label(
            body, text="", bg=W95_BG, fg=W95_TEXT, font=W95_FONT,
            wraplength=380, justify="left", anchor="w",
        )
        self._prompt.pack(fill="x", padx=12, pady=(10, 6))

        self._btn_frame = tk.Frame(body, bg=W95_BG)
        self._btn_frame.pack(fill="both", expand=True, padx=12, pady=4)

        self._status = tk.Label(body, text="Score: 0", bg=W95_BG, fg=W95_TEXT, font=W95_FONT)
        self._status.pack(pady=(0, 8))

        self._win.update_idletasks()
        try:
            px, py = parent.winfo_x(), parent.winfo_y()
            pw = parent.winfo_width()
            ww = self._win.winfo_width() or 420
            wh = self._win.winfo_height() or 300
            sw = self._win.winfo_screenwidth()
            sh = self._win.winfo_screenheight()
            x = px + (pw - ww) // 2
            y = py - wh - 10
            x = max(0, min(x, sw - ww))
            y = max(0, min(y, sh - wh))
            self._win.geometry(f"+{x}+{y}")
        except Exception:
            pass
        show_borderless(self._win)
        self._show_question()

    def _show_question(self) -> None:
        self._answering = False
        for w in self._btn_frame.winfo_children():
            w.destroy()
        if self._index >= len(self._deck):
            self._prompt.config(
                text=f"Done. You scored {self._score}/{len(self._deck)}.\n"
                "Agetha is mildly impressed. Or lying."
            )
            return
        q, choices, correct_idx = self._deck[self._index]
        self._correct_answer = choices[correct_idx]
        shuffled = list(choices)
        random.shuffle(shuffled)
        self._prompt.config(text=f"Q{self._index + 1}. {q}")
        for choice in shuffled:
            tk.Button(
                self._btn_frame, text=choice, font=W95_FONT, bg=W95_BTN_BG,
                anchor="w", relief="raised", bd=2,
                command=lambda c=choice: self._answer(c),
            ).pack(fill="x", pady=2)

    def _answer(self, choice: str) -> None:
        if self._answering:
            return
        self._answering = True
        if choice == self._correct_answer:
            self._score += 1
            try:
                from agetha.core.companion_stats import update_stats
                update_stats("user_polite")
            except Exception:
                pass
        self._index += 1
        self._status.config(text=f"Score: {self._score}")
        self._show_question()
