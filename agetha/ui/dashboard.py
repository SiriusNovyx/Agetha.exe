"""
dashboard.py — Win95-style companion dashboard (no main.py import).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from pathlib import Path

from agetha.app_config import BASE_DIR
from agetha.utils import logger

# Duplicated Win95 palette (must not import main.py)
W95_BG = "#c0c0c0"
W95_TITLE_BG = "#000080"
W95_TITLE_FG = "#ffffff"
W95_TEXT = "#000000"
W95_SHADOW = "#808080"
W95_BTN_BG = "#c0c0c0"
W95_FONT = ("MS Sans Serif", 8)
W95_FONT_BOLD = ("MS Sans Serif", 8, "bold")

NOTEPAD_FILE = BASE_DIR / "memory" / "notepad.txt"

_POLL_MS = 2000
_SAFE_CONFIG_KEYS = (
    "ENABLE_LONGTERM_MEMORY",
    "LONGTERM_MEMORY_MAX_RESULTS",
    "LONGTERM_MEMORY_MAX_CHARS",
    "ENABLE_WEB_RAG",
    "ENABLE_GLITCH_EFFECTS",
    "GLITCH_MOOD_AUTO",
    "GLITCH_FULLSCREEN",
    "ENABLE_COMPANION_STATS_CONTEXT",
    "ENABLE_COMMAND_EXECUTION",
    "ENABLE_WINDOW_CONTROL",
    "ENABLE_SCREEN_READER",
    "ENABLE_AMBIENT_POLLS",
    "FASTER_MODE",
    "DRY_RUN_MODE",
    "VOICE_OUTPUT_MODE",
    "APP_VERSION",
)

_EDITABLE_TOGGLES = (
    "ENABLE_LONGTERM_MEMORY",
    "ENABLE_WEB_RAG",
    "ENABLE_GLITCH_EFFECTS",
    "GLITCH_MOOD_AUTO",
    "GLITCH_FULLSCREEN",
    "ENABLE_COMPANION_STATS_CONTEXT",
    "ENABLE_AMBIENT_POLLS",
    "ENABLE_SCREEN_READER",
)


def _w95_progress_row(parent: tk.Misc, label: str, pct_var: tk.DoubleVar, text_var: tk.StringVar) -> tk.Canvas:
    """Win95-style sunken progress bar with caption."""
    row = tk.Frame(parent, bg=W95_BG)
    row.pack(fill="x", padx=8, pady=4)
    tk.Label(row, text=f"{label}:", width=12, anchor="w", bg=W95_BG, fg=W95_TEXT, font=W95_FONT).pack(side="left")
    bar_frame = tk.Frame(row, bg=W95_SHADOW, bd=1, relief="sunken")
    bar_frame.pack(side="left", padx=(0, 6))
    canvas = tk.Canvas(bar_frame, width=180, height=14, bg="#ffffff", highlightthickness=0, bd=0)
    canvas.pack()
    tk.Label(row, textvariable=text_var, anchor="w", bg=W95_BG, fg=W95_TEXT, font=W95_FONT).pack(side="left", fill="x", expand=True)

    def _draw(_evt: tk.Event | None = None) -> None:
        canvas.delete("all")
        w = max(canvas.winfo_width(), 180)
        h = max(canvas.winfo_height(), 14)
        pct = max(0.0, min(100.0, float(pct_var.get())))
        fill_w = int((w - 2) * pct / 100.0)
        canvas.create_rectangle(1, 1, w - 1, h - 1, fill="#ffffff", outline="")
        if fill_w > 0:
            canvas.create_rectangle(1, 1, 1 + fill_w, h - 1, fill="#008080", outline="")

    pct_var.trace_add("write", lambda *_: _draw())
    canvas.bind("<Configure>", _draw)
    _draw()
    return canvas


def read_notepad_text() -> str:
    """Read memory/notepad.txt; returns empty string on failure."""
    try:
        NOTEPAD_FILE.parent.mkdir(parents=True, exist_ok=True)
        if NOTEPAD_FILE.exists():
            return NOTEPAD_FILE.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning(f"dashboard: notepad read failed: {exc}")
    return ""


def _process_count() -> str:
    try:
        import psutil
        return str(len(psutil.pids()))
    except Exception:
        return "N/A"


def _system_snapshot() -> dict[str, str | float]:
    cpu = ram = disk = "N/A"
    cpu_pct = ram_pct = disk_pct = 0.0
    try:
        import psutil
        cpu_pct = float(psutil.cpu_percent(interval=None))
        cpu = f"{cpu_pct:.1f}%"
        mem = psutil.virtual_memory()
        ram_pct = float(mem.percent)
        ram = f"{ram_pct:.1f}% ({mem.used // (1024 * 1024)} MB used)"
        try:
            import sys
            disk_path = "C:\\" if sys.platform == "win32" else "/"
            du = psutil.disk_usage(disk_path)
            disk_pct = float(du.percent)
            disk = f"{disk_pct:.1f}% ({du.free // (1024 ** 3)} GB free)"
        except Exception:
            disk = "N/A"
    except Exception:
        pass
    return {
        "cpu": cpu,
        "ram": ram,
        "disk": disk,
        "cpu_pct": cpu_pct,
        "ram_pct": ram_pct,
        "disk_pct": disk_pct,
        "processes": _process_count(),
    }


def open_dashboard(parent: tk.Misc, app_settings) -> None:
    """Open a Toplevel dashboard with System / Virus / Notepad / Settings tabs."""
    import sys
    from agetha.ui.w95_window import apply_borderless_win95, refresh_borderless, show_borderless

    win = tk.Toplevel(parent)
    apply_borderless_win95(win, parent, topmost=True)
    win.configure(bg=W95_BG)
    win.geometry("520x420")
    win.minsize(420, 320)

    _closing = False
    _after_jobs: list[str] = []

    def _schedule(ms: int, func) -> str:
        job = win.after(ms, func)
        _after_jobs.append(job)
        return job

    def _cancel_jobs() -> None:
        for job in _after_jobs:
            try:
                win.after_cancel(job)
            except Exception:
                pass
        _after_jobs.clear()

    # ── Outer raised bevel (whole window border) ────────────────────────────
    outer = tk.Frame(win, bg=W95_BG, relief="raised", bd=2)
    outer.pack(fill="both", expand=True)

    # ── Win95 title bar ─────────────────────────────────────────────────────
    title_bar = tk.Frame(outer, bg=W95_TITLE_BG, height=18)
    title_bar.pack(fill="x", padx=2, pady=(2, 0))
    title_bar.pack_propagate(False)

    title_lbl = tk.Label(
        title_bar, text="⚠  Agetha — Dashboard",
        bg=W95_TITLE_BG, fg=W95_TITLE_FG,
        font=W95_FONT_BOLD, anchor="w", padx=4,
    )
    title_lbl.pack(side="left", fill="y")

    _btn_font = ("MS Sans Serif", 7, "bold")
    _btn_kw = dict(
        bg=W95_BTN_BG, fg=W95_TEXT, font=_btn_font,
        relief="raised", bd=2, width=2,
        activebackground=W95_BTN_BG, activeforeground=W95_TEXT,
    )

    def _close_dashboard() -> None:
        nonlocal _closing
        _closing = True
        _cancel_jobs()
        _save_notepad()
        win.destroy()

    tk.Button(
        title_bar, text="✕", command=_close_dashboard, **_btn_kw,
    ).pack(side="right", padx=(0, 2), pady=1)

    def _minimize() -> None:
        if sys.platform == "win32":
            try:
                win.overrideredirect(False)
                win.iconify()
            except Exception:
                return

            def _bind_restore() -> None:
                def _on_map(_e: tk.Event | None = None) -> None:
                    try:
                        if win.winfo_exists():
                            refresh_borderless(win)
                            win.unbind("<Map>")
                    except Exception:
                        pass

                win.bind("<Map>", _on_map)

            _schedule(250, _bind_restore)
        else:
            try:
                win.withdraw()
            except Exception:
                return

            def _bind_restore() -> None:
                def _on_map(_e: tk.Event | None = None) -> None:
                    try:
                        if win.winfo_exists():
                            win.deiconify()
                            refresh_borderless(win)
                            win.unbind("<Map>")
                    except Exception:
                        pass

                win.bind("<Map>", _on_map)

            _schedule(250, _bind_restore)

    tk.Button(
        title_bar, text="─", command=_minimize, **_btn_kw,
    ).pack(side="right", padx=(0, 1), pady=1)

    _drag_x = _drag_y = 0
    _win_x = _win_y = 0

    def _drag_start(e: tk.Event) -> None:
        nonlocal _drag_x, _drag_y, _win_x, _win_y
        _drag_x, _drag_y = e.x_root, e.y_root
        _win_x, _win_y = win.winfo_x(), win.winfo_y()

    def _drag_motion(e: tk.Event) -> None:
        nonlocal _drag_x, _drag_y, _win_x, _win_y
        dx = e.x_root - _drag_x
        dy = e.y_root - _drag_y
        _win_x += dx
        _win_y += dy
        win.geometry(f"+{_win_x}+{_win_y}")
        _drag_x, _drag_y = e.x_root, e.y_root

    for w in (title_bar, title_lbl):
        w.bind("<ButtonPress-1>", _drag_start)
        w.bind("<B1-Motion>", _drag_motion)

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True, padx=6, pady=6)

    # ── System Monitor ────────────────────────────────────────────────────────
    sys_frame = tk.Frame(notebook, bg=W95_BG)
    notebook.add(sys_frame, text="System Monitor")

    sys_vars = {
        "cpu": tk.StringVar(value="…"),
        "ram": tk.StringVar(value="…"),
        "disk": tk.StringVar(value="…"),
        "processes": tk.StringVar(value="…"),
    }
    bar_vars = {
        "cpu": tk.DoubleVar(value=0.0),
        "ram": tk.DoubleVar(value=0.0),
        "disk": tk.DoubleVar(value=0.0),
        "heat": tk.DoubleVar(value=0.0),
    }
    heat_lbl = tk.StringVar(value="…")
    for label, key in (
        ("CPU", "cpu"), ("RAM", "ram"), ("Disk", "disk"),
    ):
        _w95_progress_row(sys_frame, label, bar_vars[key], sys_vars[key])
    _w95_progress_row(sys_frame, "Core heat", bar_vars["heat"], heat_lbl)
    row = tk.Frame(sys_frame, bg=W95_BG)
    row.pack(fill="x", padx=8, pady=4)
    tk.Label(row, text="Processes:", width=12, anchor="w", bg=W95_BG, fg=W95_TEXT, font=W95_FONT).pack(side="left")
    tk.Label(row, textvariable=sys_vars["processes"], anchor="w", bg=W95_BG, fg=W95_TEXT, font=W95_FONT).pack(side="left")

    def _poll_system() -> None:
        if _closing or not win.winfo_exists():
            return
        snap = _system_snapshot()
        for k in ("cpu", "ram", "disk", "processes"):
            sys_vars[k].set(str(snap.get(k, "N/A")))
        for k in ("cpu_pct", "ram_pct", "disk_pct"):
            try:
                bar_vars[k.replace("_pct", "")].set(float(snap.get(k, 0.0)))
            except (TypeError, ValueError):
                pass
        try:
            from agetha.core.companion_stats import get_stats_summary
            heat = float(get_stats_summary().get("core_heat", 0))
            bar_vars["heat"].set(heat)
            heat_lbl.set(f"{heat:.0f}% (host CPU)")
        except Exception:
            heat_lbl.set("N/A")
        if not _closing and win.winfo_exists():
            _schedule(_POLL_MS, _poll_system)

    _schedule(100, _poll_system)

    # ── Virus Registry ──────────────────────────────────────────────────────
    virus_frame = tk.Frame(notebook, bg=W95_BG)
    notebook.add(virus_frame, text="Virus Registry")

    virus_bars: dict[str, tk.DoubleVar] = {
        "infection_level": tk.DoubleVar(value=0.0),
        "entropy": tk.DoubleVar(value=0.0),
        "affection": tk.DoubleVar(value=0.0),
        "core_heat": tk.DoubleVar(value=0.0),
    }
    virus_lbls = {k: tk.StringVar(value="…") for k in virus_bars}
    for label, key in (
        ("Infection", "infection_level"),
        ("Entropy", "entropy"),
        ("Affection", "affection"),
        ("Core heat", "core_heat"),
    ):
        _w95_progress_row(virus_frame, label, virus_bars[key], virus_lbls[key])

    virus_text = tk.Text(virus_frame, wrap="word", height=8, font=W95_FONT, bg="#ffffff", fg=W95_TEXT)
    virus_text.pack(fill="both", expand=True, padx=8, pady=8)

    def _refresh_virus() -> None:
        if _closing or not win.winfo_exists():
            return
        lines: list[str] = []
        try:
            from agetha.core.companion_stats import get_stats_summary
            stats = get_stats_summary()
            for key in virus_bars:
                try:
                    val = float(stats.get(key, 0))
                    virus_bars[key].set(val)
                    virus_lbls[key].set(f"{val:.0f}%")
                except (TypeError, ValueError):
                    virus_lbls[key].set("?")
            lines.append("Companion stats:")
            for key in ("bytes_devoured", "last_feed_bytes", "max_infection_reached", "uptime_seconds", "last_updated"):
                lines.append(f"  {key}: {stats.get(key, '?')}")
        except Exception as exc:
            lines.append(f"Stats unavailable: {exc}")

        lines.append("")
        try:
            from agetha.core.memory_system import get_memory_stats
            ms = get_memory_stats()
            lines.append("Memory system:")
            soul = ms.get("soul", {})
            episodic = ms.get("episodic", {})
            lines.append(f"  soul exists: {soul.get('exists', '?')} ({soul.get('size_bytes', '?')} bytes)")
            lines.append(f"  episodic count: {episodic.get('count', '?')} / cap {episodic.get('hard_cap', '?')}")
        except Exception as exc:
            lines.append(f"Memory stats unavailable: {exc}")

        try:
            from agetha.core.memory_search import LONGTERM_FILE
            if LONGTERM_FILE.exists():
                count = sum(1 for ln in LONGTERM_FILE.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip())
                lines.append(f"  longterm entries: {count}")
            else:
                lines.append("  longterm entries: 0")
        except Exception:
            lines.append("  longterm entries: ?")

        virus_text.delete("1.0", "end")
        virus_text.insert("1.0", "\n".join(lines))
        if not _closing and win.winfo_exists():
            _schedule(_POLL_MS, _refresh_virus)

    _schedule(150, _refresh_virus)

    # ── Notepad ─────────────────────────────────────────────────────────────
    note_frame = tk.Frame(notebook, bg=W95_BG)
    notebook.add(note_frame, text="Notepad")

    note_text = tk.Text(note_frame, wrap="word", font=W95_FONT, bg="#ffffff", fg=W95_TEXT)
    note_text.pack(fill="both", expand=True, padx=8, pady=(8, 4))
    note_text.insert("1.0", read_notepad_text())

    btn_row = tk.Frame(note_frame, bg=W95_BG)
    btn_row.pack(fill="x", padx=8, pady=(0, 8))

    def _save_notepad() -> None:
        try:
            NOTEPAD_FILE.parent.mkdir(parents=True, exist_ok=True)
            NOTEPAD_FILE.write_text(note_text.get("1.0", "end-1c"), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"dashboard: notepad save failed: {exc}")

    tk.Button(btn_row, text="Save", font=W95_FONT, bg=W95_BTN_BG, command=_save_notepad).pack(side="right")
    tk.Button(
        btn_row, text="Reload", font=W95_FONT, bg=W95_BTN_BG,
        command=lambda: (note_text.delete("1.0", "end"), note_text.insert("1.0", read_notepad_text())),
    ).pack(side="right", padx=(0, 4))

    # ── Settings (limited edit) ───────────────────────────────────────────────
    settings_frame = tk.Frame(notebook, bg=W95_BG)
    notebook.add(settings_frame, text="Settings")

    scroll_outer = tk.Frame(settings_frame, bg=W95_BG)
    scroll_outer.pack(fill="both", expand=True, padx=8, pady=8)

    tk.Label(
        scroll_outer, text="Toggle safe options (writes config.txt):",
        bg=W95_BG, fg=W95_TEXT, font=W95_FONT_BOLD, anchor="w",
    ).pack(fill="x", pady=(0, 6))

    raw_cfg = getattr(app_settings, "raw", {}) or {}

    def _cfg_yes(key: str) -> bool:
        return str(raw_cfg.get(key, "no")).strip().lower() in ("yes", "true", "1", "on")

    def _make_toggle(parent: tk.Misc, key: str) -> None:
        var = tk.BooleanVar(value=_cfg_yes(key))

        def _on_toggle() -> None:
            try:
                from agetha.app_config import patch_config_key
                val = "yes" if var.get() else "no"
                if patch_config_key(key, val):
                    raw_cfg[key] = val
                else:
                    var.set(not var.get())
            except Exception as exc:
                logger.warning(f"dashboard: toggle {key} failed: {exc}")
                var.set(not var.get())

        row = tk.Frame(parent, bg=W95_BG)
        row.pack(fill="x", pady=2)
        tk.Checkbutton(
            row, text=key, variable=var, command=_on_toggle,
            bg=W95_BG, fg=W95_TEXT, font=W95_FONT, activebackground=W95_BG,
            selectcolor=W95_BG,
        ).pack(side="left")

    for key in _EDITABLE_TOGGLES:
        _make_toggle(scroll_outer, key)

    tk.Label(scroll_outer, text="\nRead-only snapshot:", bg=W95_BG, fg=W95_TEXT, font=W95_FONT_BOLD, anchor="w").pack(fill="x", pady=(8, 4))
    settings_text = tk.Text(scroll_outer, wrap="word", height=8, font=W95_FONT, bg="#ffffff", fg=W95_TEXT, state="disabled")
    settings_text.pack(fill="both", expand=True)

    cfg_lines: list[str] = []
    try:
        for key in _SAFE_CONFIG_KEYS:
            cfg_lines.append(f"{key} = {raw_cfg.get(key, '(default)')}")
    except Exception as exc:
        cfg_lines.append(f"(config unavailable: {exc})")

    settings_text.configure(state="normal")
    settings_text.delete("1.0", "end")
    settings_text.insert("1.0", "\n".join(cfg_lines))
    settings_text.configure(state="disabled")

    # Position near parent and show without native title bar
    win.update_idletasks()
    try:
        px, py = parent.winfo_x(), parent.winfo_y()
        pw = parent.winfo_width()
        ww = win.winfo_width()
        x = max(0, px + pw + 12)
        y = max(0, py)
        win.geometry(f"+{x}+{y}")
    except Exception:
        pass
    show_borderless(win)

    win.protocol("WM_DELETE_WINDOW", _close_dashboard)
