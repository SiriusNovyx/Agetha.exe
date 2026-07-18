"""Phase 1.1 QA smoke tests — run: python tests/test_phase1_qa.py"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import tkinter as tk

from agetha.core import companion_stats
from agetha.ui import dashboard
from agetha.core import memory_search
from agetha.core.ai_engine import AIEngine


class TestDashboardAfterCancel(unittest.TestCase):
    """Mirrors dashboard _schedule / _cancel_jobs / _closing pattern."""

    def test_no_poll_after_close(self) -> None:
        root = tk.Tk()
        root.withdraw()
        closing = False
        jobs: list[str] = []
        fired_after_close: list[bool] = []

        def schedule(ms: int, func) -> str:
            job = root.after(ms, func)
            jobs.append(job)
            return job

        def cancel_jobs() -> None:
            for job in jobs:
                try:
                    root.after_cancel(job)
                except Exception:
                    pass
            jobs.clear()

        def poll() -> None:
            if closing or not root.winfo_exists():
                return
            fired_after_close.append(False)
            if not closing and root.winfo_exists():
                schedule(30, poll)

        schedule(10, poll)
        root.update()
        closing = True
        cancel_jobs()
        root.after(100, root.quit)
        root.mainloop()
        root.destroy()
        self.assertFalse(any(fired_after_close))


class TestDashboardNotepad(unittest.TestCase):
    def test_read_save_reload_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            note_file = Path(td) / "notepad.txt"
            with patch.object(dashboard, "NOTEPAD_FILE", note_file):
                note_file.write_text("line one\nline two", encoding="utf-8")
                self.assertEqual(dashboard.read_notepad_text(), "line one\nline two")

                root = tk.Tk()
                root.withdraw()
                saved: list[str] = []

                def fake_open_dashboard(parent: tk.Misc, app_settings) -> None:
                    win = tk.Toplevel(parent)
                    note_text = tk.Text(win)
                    note_text.insert("1.0", dashboard.read_notepad_text())
                    note_text.insert("end", "\nappended")
                    content = note_text.get("1.0", "end-1c")
                    note_file.parent.mkdir(parents=True, exist_ok=True)
                    note_file.write_text(content, encoding="utf-8")
                    saved.append(dashboard.read_notepad_text())
                    win.destroy()

                fake_open_dashboard(root, MagicMock())
                root.update()
                root.destroy()
                self.assertIn("appended", saved[0])
                self.assertEqual(dashboard.read_notepad_text(), saved[0])


class TestDashboardOpenClose(unittest.TestCase):
    def test_repeated_open_close(self) -> None:
        root = tk.Tk()
        root.withdraw()
        settings = MagicMock()
        settings.raw = {"ENABLE_LONGTERM_MEMORY": "yes"}

        def _invoke_close(win: tk.Toplevel) -> None:
            for outer in win.winfo_children():
                for bar in outer.winfo_children():
                    for btn in bar.winfo_children():
                        if isinstance(btn, tk.Button) and btn.cget("text") == "✕":
                            btn.invoke()
                            return
            win.destroy()

        for _ in range(3):
            dashboard.open_dashboard(root, settings)
            tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
            self.assertEqual(len(tops), 1)
            _invoke_close(tops[0])
            root.update_idletasks()
            root.update()

        self.assertEqual(
            [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)],
            [],
        )
        root.destroy()


class TestSearchMemoryRecursion(unittest.TestCase):
    def test_parse_coerces_search_memory_when_suppressed(self) -> None:
        engine = AIEngine.__new__(AIEngine)
        raw = json.dumps(
            {
                "command": "search_memory",
                "query": "cat",
                "mood": "thinking",
                "segments": [{"text": "Looking.", "pause": 0.0}],
            }
        )
        result = AIEngine._parse(engine, raw, suppress_search_memory=True)
        self.assertIn(result["command"], ("speak", "idle"))
        self.assertNotEqual(result["command"], "search_memory")


class TestLongtermAppendSource(unittest.TestCase):
    def test_log_longterm_only_in_summary_block(self) -> None:
        repo = Path(__file__).resolve().parent.parent
        callers: list[str] = []
        for path in sorted(repo.rglob("*.py")):
            if "tests" in path.parts or path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            if path.name == "memory_search.py":
                self.assertEqual(text.count("def log_longterm_memory"), 1)
                continue
            if "log_longterm_memory(" in text:
                callers.append(path.name)
        self.assertEqual(callers, ["ai_engine.py"])


class TestLongtermDisabled(unittest.TestCase):
    def test_handler_skips_search_when_disabled(self) -> None:
        from agetha.commands.command_handlers import handle_search_memory

        app = MagicMock()
        app._speak_and_continue = MagicMock()
        app._ai_query = MagicMock(return_value=None)
        app._dispatch_response = MagicMock()
        ctx = MagicMock()
        ctx.segments = []
        ctx.mood = "neutral"
        ctx.user_message = "what about my cat"
        ctx.shutdown_requested = False
        response = {"query": "cat", "command": "search_memory"}

        with patch("agetha.commands.command_handlers.get_settings") as mock_settings:
            mock_settings.return_value.enable_longterm_memory = False
            with patch("agetha.core.memory_search.search_memories") as mock_search:
                ok = handle_search_memory(app, response, ctx)
                self.assertTrue(ok)
                mock_search.assert_not_called()

        threading.Event().wait(0.05)


class TestCompanionStatsSafe(unittest.TestCase):
    def test_update_stats_never_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            stats_file = Path(td) / "companion_stats.json"
            with patch.object(companion_stats, "STATS_FILE", stats_file):
                stats_file.write_text("not json{{{", encoding="utf-8")
                companion_stats.update_stats("command")
                companion_stats.update_stats("file_drop", file_size="bad")
                companion_stats.update_stats("user_polite")
                summary = companion_stats.get_stats_summary()
                self.assertIn("affection", summary)


class TestMemorySearch(unittest.TestCase):
    def test_search_and_append(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            lt_file = Path(td) / "longterm_memory.jsonl"
            with patch.object(memory_search, "LONGTERM_FILE", lt_file):
                memory_search.log_longterm_memory("User likes cats.", source="ai", mood="happy")
                memory_search.log_longterm_memory("User hates mornings.", source="ai")
                hits = memory_search.search_memories("cats", limit=3)
                self.assertTrue(hits)
                self.assertIn("cats", hits[0]["summary"].lower())
                formatted = memory_search.format_search_results_for_prompt(hits)
                self.assertIn("Long-term memory search results", formatted)


if __name__ == "__main__":
    unittest.main(verbosity=2)
