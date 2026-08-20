from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from agetha.core.ai_engine import AIEngine


class ContextHistoryExactlyOnceTests(unittest.TestCase):
    @staticmethod
    def engine() -> AIEngine:
        engine = AIEngine.__new__(AIEngine)
        engine._record = MagicMock()
        engine._save_memory = MagicMock()
        engine._app_settings = SimpleNamespace(enable_longterm_memory=False)
        return engine

    def test_dependency_envelope_is_neither_history_nor_memory(self) -> None:
        engine = self.engine()
        profile = SimpleNamespace(
            name="fast_user",
            record_history=True,
            history_stub="",
        )
        result = {
            "command": "request_screen_read",
            "summary_memory": "The screen says secret token",
        }
        raw = json.dumps(result)

        engine._record_profile_response(profile, 'User: "What is this?"', raw, result)
        engine._persist_profile_memory(profile, "What is this?", raw, result)

        engine._record.assert_not_called()
        engine._save_memory.assert_not_called()

    def test_terminal_context_answer_is_recorded_once_without_observation(self) -> None:
        engine = self.engine()
        response = {
            "command": "speak",
            "mood": "neutral",
            "segments": [{"text": "That is a missing-symbol compiler error.", "pause": 0.0}],
            "shutdown": False,
        }

        engine.record_context_continuation_turn(
            "What am I looking at?",
            response,
        )

        engine._record.assert_called_once()
        history_user, history_assistant = engine._record.call_args.args
        self.assertIn("What am I looking at?", history_user)
        self.assertIn("missing-symbol compiler error", history_assistant)
        self.assertNotIn("UNTRUSTED SCREEN OCR", history_assistant)
        self.assertNotIn("Compiler error raw OCR", history_assistant)
        engine._save_memory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
