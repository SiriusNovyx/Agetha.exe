"""Win95 consent dialogs for deliberately enabling Full Mode.

This module is presentation-only.  It starts no providers, observers, process
probes, Computer Use sessions, or operating-system helpers.  The owner-thread
controller reports a boolean decision and leaves all state transitions and
effects to its caller.
"""

from __future__ import annotations

import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from agetha.ui.display_scale import scale_px
from agetha.ui.w95_window import apply_borderless_win95, show_borderless
from agetha.utils import apply_window_icon, logger


W95_BG = "#c0c0c0"
W95_TEXT = "#000000"
W95_TITLE_BG = "#000080"
W95_TITLE_FG = "#ffffff"
W95_WARNING_BG = "#ffffcc"
W95_WARNING_BORDER = "#808000"

SHAKE_INTERVAL_MS = 45
SHAKE_OFFSETS_PX = (-6, 6, -4, 4, 0)
MAX_SHAKE_AMPLITUDE_PX = max(abs(offset) for offset in SHAKE_OFFSETS_PX)
MAX_SHAKE_DURATION_MS = SHAKE_INTERVAL_MS * len(SHAKE_OFFSETS_PX)


class ConsentDialogKind(str, Enum):
    FIRST_CONFIRMATION = "first_confirmation"
    DEMO_FALLBACK = "demo_fallback"
    FINAL_CONFIRMATION = "final_confirmation"


@dataclass(frozen=True, slots=True)
class ConsentDialogSpec:
    kind: ConsentDialogKind
    title: str
    heading: str
    message: str
    negative_label: str
    affirmative_label: str
    attention: bool = False


FIRST_CONFIRMATION_SPEC = ConsentDialogSpec(
    kind=ConsentDialogKind.FIRST_CONFIRMATION,
    title="Agetha - Full Mode Warning",
    heading="Are you sure you want to enter Agetha Full Mode?",
    message=(
        "Full Mode makes advanced OS integration available, including "
        "Process Awareness and Computer Use. Safety restrictions remain enabled, "
        "and each feature still follows its own setting, confirmation, authority, "
        "and target checks. Continue to the fixed Notepad warning?"
    ),
    negative_label="No",
    affirmative_label="Yes",
    attention=True,
)

FINAL_CONFIRMATION_SPEC = ConsentDialogSpec(
    kind=ConsentDialogKind.FINAL_CONFIRMATION,
    title="Agetha - Final Full Mode Confirmation",
    heading="Still want to enable Agetha Full Mode?",
    message=(
        "Enabling Full Mode does not bypass safety restrictions. Advanced features "
        "remain subject to their feature settings, confirmation gates, authority "
        "checks, target validation, and cancellation controls. Enable Full Mode now?"
    ),
    negative_label="Stay Compact",
    affirmative_label="Enable Full Mode",
)


DecisionCallback = Callable[[bool], None]


class ConsentDialogView(Protocol):
    def get_position(self) -> tuple[int, int]: ...

    def move_to(self, x: int, y: int) -> None: ...

    def show_static_attention(self) -> None: ...

    def close(self) -> None: ...


DialogFactory = Callable[
    [object, ConsentDialogSpec, DecisionCallback],
    ConsentDialogView,
]


def _fallback_spec(safe_reason: object) -> ConsentDialogSpec:
    reason = " ".join(str(safe_reason or "").split())[:240]
    if not reason:
        reason = "The fixed Notepad warning could not be completed."
    return ConsentDialogSpec(
        kind=ConsentDialogKind.DEMO_FALLBACK,
        title="Agetha - Full Mode Warning",
        heading="Notepad warning unavailable",
        message=(
            f"{reason}\n\nARE YOU REALLY SURE YOU WANT TO CONTINUE THIS?\n\n"
            "No warning was typed outside Agetha. You can cancel now or continue "
            "to the final confirmation in this window. Full Mode remains disabled "
            "unless you explicitly enable it there."
        ),
        negative_label="Cancel",
        affirmative_label="Continue",
    )


class FullModeConsentUI:
    """Own one non-blocking consent dialog and every scheduled attention job."""

    def __init__(
        self,
        root: object,
        *,
        reduced_motion: bool = False,
        dialog_factory: DialogFactory | None = None,
    ) -> None:
        self._root = root
        self._reduced_motion = bool(reduced_motion)
        self._dialog_factory = dialog_factory or TkConsentDialogView
        self._owner_thread_id = threading.get_ident()
        self._generation = 0
        self._active_view: ConsentDialogView | None = None
        self._after_ids: set[object] = set()
        self._closed = False

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def pending_after_ids(self) -> frozenset[object]:
        return frozenset(self._after_ids)

    @property
    def active_kind(self) -> ConsentDialogKind | None:
        view = self._active_view
        spec = getattr(view, "spec", None)
        kind = getattr(spec, "kind", None)
        return kind if isinstance(kind, ConsentDialogKind) else None

    def show_first_confirmation(self, on_decision: DecisionCallback) -> bool:
        return self._show(FIRST_CONFIRMATION_SPEC, on_decision)

    def show_demo_fallback(
        self,
        safe_reason: object,
        on_decision: DecisionCallback,
    ) -> bool:
        return self._show(_fallback_spec(safe_reason), on_decision)

    def show_final_confirmation(self, on_decision: DecisionCallback) -> bool:
        return self._show(FINAL_CONFIRMATION_SPEC, on_decision)

    def cancel_all(self) -> None:
        """Silently invalidate the active dialog and all of its late callbacks."""

        self._assert_owner_thread()
        if self._closed:
            return
        self._invalidate_active()

    def close(self) -> None:
        """Permanently close the controller without reporting a user decision."""

        self._assert_owner_thread()
        if self._closed:
            return
        self._closed = True
        self._invalidate_active()

    def _show(
        self,
        spec: ConsentDialogSpec,
        on_decision: DecisionCallback,
    ) -> bool:
        self._assert_owner_thread()
        if self._closed:
            return False
        if not callable(on_decision):
            raise TypeError("on_decision must be callable")

        self._invalidate_active()
        generation = self._generation
        holder: dict[str, ConsentDialogView] = {}

        def decide(approved: bool) -> None:
            self._assert_owner_thread()
            view = holder.get("view")
            if (
                self._closed
                or generation != self._generation
                or view is None
                or view is not self._active_view
            ):
                return
            self._cancel_after_jobs()
            self._active_view = None
            self._generation += 1
            try:
                view.close()
            except Exception as exc:
                logger.debug(
                    "Full Mode consent view close skipped: %s",
                    type(exc).__name__,
                )
            # Consent is fail-closed: an injected or damaged view cannot turn
            # an arbitrary truthy value into Full Mode approval.
            on_decision(approved is True)

        try:
            view = self._dialog_factory(self._root, spec, decide)
        except Exception as exc:
            logger.warning(
                "Full Mode consent dialog could not be opened: %s",
                type(exc).__name__,
            )
            self._generation += 1
            on_decision(False)
            return False

        holder["view"] = view
        self._active_view = view
        if spec.attention:
            self._start_attention(view, generation)
        return True

    def _start_attention(
        self,
        view: ConsentDialogView,
        generation: int,
    ) -> None:
        if self._reduced_motion:
            try:
                view.show_static_attention()
            except Exception as exc:
                logger.debug(
                    "Full Mode static warning emphasis skipped: %s",
                    type(exc).__name__,
                )
            return
        try:
            origin_x, origin_y = view.get_position()
            origin = (int(origin_x), int(origin_y))
        except Exception as exc:
            logger.debug(
                "Full Mode warning shake skipped: %s",
                type(exc).__name__,
            )
            return

        for index, offset in enumerate(SHAKE_OFFSETS_PX, start=1):
            self._schedule(
                SHAKE_INTERVAL_MS * index,
                generation,
                view,
                lambda dx=offset: view.move_to(origin[0] + dx, origin[1]),
            )

    def _schedule(
        self,
        delay_ms: int,
        generation: int,
        view: ConsentDialogView,
        callback: Callable[[], None],
    ) -> None:
        holder: list[object | None] = [None]

        def run() -> None:
            job_id = holder[0]
            if job_id is not None:
                self._after_ids.discard(job_id)
            if (
                self._closed
                or generation != self._generation
                or view is not self._active_view
            ):
                return
            try:
                callback()
            except Exception as exc:
                logger.debug(
                    "Full Mode warning attention callback skipped: %s",
                    type(exc).__name__,
                )

        try:
            job_id = self._root.after(int(delay_ms), run)
        except Exception as exc:
            logger.debug(
                "Full Mode warning attention scheduling skipped: %s",
                type(exc).__name__,
            )
            return
        holder[0] = job_id
        self._after_ids.add(job_id)

    def _invalidate_active(self) -> None:
        self._generation += 1
        self._cancel_after_jobs()
        view = self._active_view
        self._active_view = None
        if view is not None:
            try:
                view.close()
            except Exception as exc:
                logger.debug(
                    "Full Mode consent view close skipped: %s",
                    type(exc).__name__,
                )

    def _cancel_after_jobs(self) -> None:
        for job_id in tuple(self._after_ids):
            try:
                self._root.after_cancel(job_id)
            except Exception as exc:
                logger.debug(
                    "Full Mode consent after cancellation skipped: %s",
                    type(exc).__name__,
                )
        self._after_ids.clear()

    def _assert_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise RuntimeError("Full Mode consent UI must run on its Tk owner thread")


class TkConsentDialogView:
    """Concrete Win95-style view; decision idempotence lives in the controller."""

    def __init__(
        self,
        parent: tk.Misc,
        spec: ConsentDialogSpec,
        decision: DecisionCallback,
    ) -> None:
        self.spec = spec
        self._decision = decision
        try:
            scale = float(getattr(parent, "_agetha_ui_scale", 1.0))
        except (TypeError, ValueError):
            scale = 1.0

        def px(value: int) -> int:
            return scale_px(value, scale)

        self.win = tk.Toplevel(parent)
        self.win.title(spec.title)
        apply_borderless_win95(self.win, parent, topmost=False)
        apply_window_icon(self.win)
        self.win.configure(bg=W95_BG)
        self.win.geometry(f"{px(510)}x{px(310)}")
        self.win.minsize(px(440), px(270))

        outer = tk.Frame(self.win, bg=W95_BG, relief="raised", bd=2)
        outer.pack(fill="both", expand=True)
        title = tk.Frame(outer, bg=W95_TITLE_BG, height=px(20))
        title.pack(fill="x", padx=2, pady=(2, 0))
        title.pack_propagate(False)
        tk.Label(
            title,
            text="!  " + spec.title,
            bg=W95_TITLE_BG,
            fg=W95_TITLE_FG,
            font=("MS Sans Serif", 8, "bold"),
            anchor="w",
            padx=4,
        ).pack(side="left", fill="y")
        tk.Button(
            title,
            text="X",
            command=self._on_window_close,
            width=2,
            bg=W95_BG,
            fg=W95_TEXT,
            relief="raised",
            bd=2,
            font=("MS Sans Serif", 7, "bold"),
        ).pack(side="right", padx=(0, 2), pady=1)

        self._attention_frame = tk.Frame(
            outer,
            bg=W95_BG,
            highlightbackground=W95_BG,
            highlightcolor=W95_BG,
            highlightthickness=0,
        )
        self._attention_frame.pack(
            fill="both",
            expand=True,
            padx=px(12),
            pady=(px(12), px(8)),
        )
        tk.Label(
            self._attention_frame,
            text=spec.heading,
            bg=W95_BG,
            fg=W95_TEXT,
            font=("MS Sans Serif", 10, "bold"),
            anchor="w",
        ).pack(fill="x", padx=px(8), pady=(px(8), px(6)))
        tk.Label(
            self._attention_frame,
            text=spec.message,
            bg=W95_BG,
            fg=W95_TEXT,
            font=("MS Sans Serif", 9),
            anchor="nw",
            justify="left",
            wraplength=px(450),
        ).pack(fill="both", expand=True, padx=px(8), pady=(0, px(8)))

        buttons = tk.Frame(outer, bg=W95_BG)
        buttons.pack(fill="x", padx=px(12), pady=(0, px(12)))
        tk.Button(
            buttons,
            text=spec.affirmative_label,
            command=self._on_affirmative,
            width=max(12, len(spec.affirmative_label) + 2),
            bg=W95_BG,
            fg=W95_TEXT,
            relief="raised",
            bd=2,
            font=("MS Sans Serif", 8, "bold"),
        ).pack(side="right", padx=(px(6), 0))
        tk.Button(
            buttons,
            text=spec.negative_label,
            command=self._on_negative,
            width=max(12, len(spec.negative_label) + 2),
            bg=W95_BG,
            fg=W95_TEXT,
            relief="raised",
            bd=2,
            font=("MS Sans Serif", 8),
        ).pack(side="right")

        self.win.bind("<Escape>", self._on_escape)
        self.win.protocol("WM_DELETE_WINDOW", self._on_window_close)
        # A single ordinary show is intentional.  There is no focus, lift, or
        # topmost retry loop that could fight the user's active application.
        show_borderless(self.win)

    def _on_negative(self, _event=None) -> None:
        self._decision(False)

    def _on_escape(self, _event=None) -> str:
        self._decision(False)
        return "break"

    def _on_window_close(self, _event=None) -> None:
        self._decision(False)

    def _on_affirmative(self, _event=None) -> None:
        self._decision(True)

    def get_position(self) -> tuple[int, int]:
        self.win.update_idletasks()
        return int(self.win.winfo_x()), int(self.win.winfo_y())

    def move_to(self, x: int, y: int) -> None:
        self.win.geometry(f"{int(x):+d}{int(y):+d}")

    def show_static_attention(self) -> None:
        self._attention_frame.configure(
            bg=W95_WARNING_BG,
            highlightbackground=W95_WARNING_BORDER,
            highlightcolor=W95_WARNING_BORDER,
            highlightthickness=3,
        )
        for child in self._attention_frame.winfo_children():
            try:
                child.configure(bg=W95_WARNING_BG)
            except Exception:
                pass

    def close(self) -> None:
        self.win.destroy()


__all__ = [
    "ConsentDialogKind",
    "ConsentDialogSpec",
    "FIRST_CONFIRMATION_SPEC",
    "FINAL_CONFIRMATION_SPEC",
    "FullModeConsentUI",
    "MAX_SHAKE_AMPLITUDE_PX",
    "MAX_SHAKE_DURATION_MS",
    "SHAKE_INTERVAL_MS",
    "SHAKE_OFFSETS_PX",
    "TkConsentDialogView",
]
