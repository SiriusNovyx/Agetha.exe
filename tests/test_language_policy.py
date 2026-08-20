"""Language-neutral multilingual personality and exact-text regressions."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agetha.app_config import AppSettings  # noqa: E402
from agetha.core.ai_engine import (  # noqa: E402
    AIEngine,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_FASTER,
)


SAMPLES = (
    "Hello",
    "สวัสดี",
    "こんにちは",
    "你好",
    "안녕하세요",
    "مرحبا",
    "Привет",
    "Bonjour",
    "Hello — สวัสดี — こんにちは — مرحبا — 👋",
)


def _engine() -> AIEngine:
    engine = AIEngine.__new__(AIEngine)
    engine._command_execution_enabled = True
    engine._app_settings = AppSettings({"ENABLE_UNICODE_TYPING": "yes"})
    return engine


class TestLanguageNeutralPrompt(unittest.TestCase):
    def test_prompts_define_one_general_multilingual_policy(self) -> None:
        for prompt in (SYSTEM_PROMPT, SYSTEM_PROMPT_FASTER):
            folded = prompt.casefold()
            with self.subTest(prompt=prompt[:24]):
                self.assertIn("mirror the user's current language", folded)
                self.assertIn("conversational register", folded)
                self.assertIn("do not translate or transliterate", folded)
                self.assertIn("exact user-provided text", folded)
                self.assertNotIn("thai voice contract", folded)
                self.assertNotIn("agetha's own thai", folded)

    def test_language_choice_is_never_authority(self) -> None:
        folded = SYSTEM_PROMPT.casefold()
        for boundary in (
            "command guard",
            "computer use authority",
            "provider authority",
            "continuation authority",
            "safety classification",
            "process permissions",
        ):
            with self.subTest(boundary=boundary):
                self.assertIn(boundary, folded)


class TestBalancedExactTextVectors(unittest.TestCase):
    def test_type_text_preserves_every_sample_exactly(self) -> None:
        for sample in SAMPLES:
            exact = f"  {sample}\u0301  "
            with self.subTest(sample=sample):
                parsed = _engine()._parse(json.dumps({
                    "command": "type_text",
                    "text": exact,
                    "mode": "auto",
                    "speed": "normal",
                    "restore_clipboard": True,
                    "segments": [],
                }, ensure_ascii=False))
                self.assertEqual(parsed["text"], exact)

    def test_spoken_user_text_is_not_postprocessed(self) -> None:
        for sample in (
            *SAMPLES,
            "I tought this exact quoted text should stay tought.",
            "I'm sorry — quote this exact sentence without deleting it.",
        ):
            with self.subTest(sample=sample):
                parsed = _engine()._parse(json.dumps({
                    "command": "speak",
                    "segments": [{"text": sample, "pause": 0}],
                }, ensure_ascii=False))
                self.assertEqual(parsed["segments"][0]["text"], sample)


if __name__ == "__main__":
    unittest.main()
