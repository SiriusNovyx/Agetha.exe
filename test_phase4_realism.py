"""Phase 4 realism integration tests — run: python test_phase4_realism.py"""

from __future__ import annotations

import json
import py_compile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_engine import AIEngine, VALID_COMMANDS
import companion_stats
import dashboard

ROOT = Path(__file__).resolve().parent


class TestCommandsRegistered(unittest.TestCase):
    def test_new_commands_in_valid_set(self) -> None:
        for cmd in ("read_notepad", "play_virus_trivia"):
            self.assertIn(cmd, VALID_COMMANDS)

    def test_companion_stats_prompt_block(self) -> None:
        block = companion_stats.format_stats_for_prompt()
        self.assertIn("COMPANION STATS", block)

    def test_notepad_context_formatter(self) -> None:
        from command_handlers import _format_notepad_context
        empty = _format_notepad_context("")
        self.assertIn("empty", empty.lower())
        filled = _format_notepad_context("hello notes")
        self.assertIn("hello notes", filled)
        self.assertIn("NOTEPAD", filled)


class TestReadNotepadHandler(unittest.TestCase):
    def test_handler_requeries_with_pending_notepad(self) -> None:
        from command_handlers import handle_read_notepad

        app = MagicMock()
        app._ai = MagicMock()
        app._speak_and_continue = MagicMock()
        ctx = MagicMock()
        ctx.segments = [{"text": "Reading.", "pause": 0.0}]
        ctx.mood = "thinking"
        ctx.user_message = "what is in my notepad"
        response = {"command": "read_notepad"}

        with patch("dashboard.read_notepad_text", return_value="buy milk"):
            with patch("command_handlers.threading.Thread") as mock_thread:
                ok = handle_read_notepad(app, response, ctx)
                self.assertTrue(ok)
                self.assertIn("buy milk", app._ai._pending_notepad_context)
                mock_thread.assert_called_once()


class TestPlayTriviaHandler(unittest.TestCase):
    def test_handler_opens_trivia(self) -> None:
        from command_handlers import handle_play_virus_trivia

        app = MagicMock()
        app.root = MagicMock()
        app._speak_and_continue = MagicMock()
        ctx = MagicMock()
        ctx.segments = [{"text": "Quiz.", "pause": 0.0}]
        ctx.mood = "happy"
        ctx.shutdown_requested = False

        with patch("virus_trivia.open_virus_trivia") as mock_open:
            ok = handle_play_virus_trivia(app, {}, ctx)
            self.assertTrue(ok)
            mock_open.assert_called_once_with(app.root)


class TestNotepadPromptInjection(unittest.TestCase):
    def test_build_prompt_includes_notepad_and_suppress(self) -> None:
        engine = AIEngine.__new__(AIEngine)
        engine._faster_mode = True
        engine._app_settings = MagicMock()
        engine._app_settings.enable_companion_stats_context = False
        engine._app_settings.episodic_prompt_limit = 5
        engine._system_path = "C:\\Users\\test"
        engine._compact_chars = ""
        engine._get_inactivity_seconds = lambda: 0
        engine._load_memories = lambda: ""
        engine._history = []
        engine._build_history = lambda: []

        system, user_turn, _msgs = engine._build_prompt(
            "", "hi", "",
            notepad_context="── DASHBOARD NOTEPAD ──\ntest\n── END NOTEPAD ──",
            suppress_read_notepad=True,
        )
        self.assertIn("read_notepad again", system.lower())
        self.assertIn("DASHBOARD NOTEPAD", user_turn)


class TestPyCompile(unittest.TestCase):
    def test_modules_compile(self) -> None:
        for name in (
            "companion_stats.py",
            "dashboard.py",
            "virus_trivia.py",
            "command_handlers.py",
            "ai_engine.py",
            "glitch_overlay.py",
            "test_phase4_realism.py",
        ):
            py_compile.compile(str(ROOT / name), doraise=True)


if __name__ == "__main__":
    unittest.main()
