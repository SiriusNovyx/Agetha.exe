from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import main
from agetha.core.ai_engine import AIEngine


class ProviderStatusTruthfulnessTests(unittest.TestCase):
    def test_groq_status_reports_key_identity_without_inferred_quota(self) -> None:
        engine = AIEngine.__new__(AIEngine)
        engine._use_local_ai = False
        engine._use_gemini = False
        engine._use_openrouter = False
        engine._groq_keys = ["one", "two", "three"]
        engine._current_groq_key_index = 1
        engine._groq_model = "openai/gpt-oss-120b"
        engine._groq_tokens_used = {1: 90000}

        status = engine.get_token_status()

        self.assertEqual(status["provider"], "groq")
        self.assertEqual(status["model"], "openai/gpt-oss-120b")
        self.assertEqual(status["key_index"], 2)
        self.assertEqual(status["key_count"], 3)
        self.assertNotIn("pct_left", status)
        self.assertNotIn("tokens_left", status)

    def test_placeholder_uses_truthful_groq_and_gemini_identity(self) -> None:
        app = SimpleNamespace(_ai=SimpleNamespace(get_token_status=lambda: {
            "using_groq": True,
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
            "key_index": 2,
            "key_count": 3,
        }))
        groq = main.CompanionApp._get_placeholder_text(app)
        app._ai = SimpleNamespace(get_token_status=lambda: {
            "using_groq": False,
            "provider": "gemini",
            "model": "gemini-2.5-flash",
        })
        gemini = main.CompanionApp._get_placeholder_text(app)

        self.assertIn("Groq", groq)
        self.assertIn("key 2/3", groq)
        self.assertNotIn("%", groq)
        self.assertIn("Gemini", gemini)
        self.assertIn("gemini-2.5-flash", gemini)

    def test_status_line_does_not_present_estimated_remaining_percentage(self) -> None:
        app = SimpleNamespace(
            _ai=SimpleNamespace(get_token_status=lambda: {
                "using_groq": True,
                "provider": "groq",
                "model": "openai/gpt-oss-120b",
                "key_index": 1,
                "key_count": 2,
            }),
            _status_var=SimpleNamespace(set=MagicMock()),
            _update_placeholder=MagicMock(),
        )

        main.CompanionApp._update_token_status(app)

        label = app._status_var.set.call_args.args[0]
        self.assertEqual(label, "Groq | Key 1/2")
        self.assertNotIn("%", label)

    def test_status_line_replaces_stale_groq_identity_after_cloud_fallback(self) -> None:
        for provider, model, expected in (
            ("gemini", "gemini-2.5-flash", "Gemini | gemini-2.5-flash"),
            ("openrouter", "vendor/model", "OpenRouter | vendor/model"),
        ):
            with self.subTest(provider=provider):
                status_var = SimpleNamespace(set=MagicMock())
                app = SimpleNamespace(
                    _ai=SimpleNamespace(get_token_status=lambda: {
                        "using_groq": False,
                        "provider": provider,
                        "model": model,
                    }),
                    _status_var=status_var,
                    _update_placeholder=MagicMock(),
                )

                main.CompanionApp._update_token_status(app)

                self.assertEqual(status_var.set.call_args.args[0], expected)


if __name__ == "__main__":
    unittest.main()
