from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agetha.app_config import AppSettings
from agetha.core.ai_engine import AIEngine
from agetha.providers.base import ProviderHTTPError


def _settings(**overrides) -> SimpleNamespace:
    values = {
        "ai_temperature": 0.65,
        "ai_max_tokens": 220,
        "ai_top_p": 0.90,
        "enable_unicode_typing": True,
        "enable_window_control": True,
        "enable_web_rag": True,
        "enable_glitch_effects": True,
        "enable_longterm_memory": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _SequenceClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        index = len(self.calls) - 1
        if index >= len(self.responses):
            raise AssertionError("provider called beyond bounded test responses")
        response = self.responses[index]
        if isinstance(response, BaseException):
            raise response
        if hasattr(response, "choices"):
            return response
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=str(response)))],
            usage=None,
        )


def _gemini_query_engine(responses: list[object]) -> AIEngine:
    engine = AIEngine.__new__(AIEngine)
    engine._client = _SequenceClient(responses)
    engine._command_execution_enabled = True
    engine._show_error_gif = False
    engine._use_local_ai = False
    engine._use_gemini = True
    engine._gemini_model = "gemini-test"
    engine._enable_groq = False
    engine._use_openrouter = False
    engine._openrouter_model = "openrouter-test"
    engine._openrouter_as_fallback = False
    engine._app_settings = _settings()
    engine._faster_mode = False
    engine._fast_profile_active = False
    engine._update_user_activity = lambda _message: None
    engine._track_tokens = lambda _usage: None
    engine._record = MagicMock()
    engine._save_memory = MagicMock()
    engine._build_prompt = lambda *_args, **_kwargs: (
        "system prompt", "User: request", [],
    )
    return engine


class GeminiConfigurationTests(unittest.TestCase):
    @staticmethod
    def _route_engine() -> AIEngine:
        engine = AIEngine.__new__(AIEngine)
        engine._use_local_ai = False
        engine._want_gemini = True
        engine._gemini_key = "gemini-key"
        engine._gemini_model = "gemini-test"
        engine._use_gemini = False
        engine._gemini_as_fallback = False
        engine._want_openrouter = True
        engine._openrouter_key = "openrouter-key"
        engine._openrouter_model = "openrouter-test:free"
        engine._openrouter_is_free = True
        engine._use_openrouter = False
        engine._openrouter_as_fallback = False
        engine._enable_groq = False
        engine._groq_keys = []
        engine._groq_model = "groq-test"
        engine._emit_error = MagicMock()
        engine._init_client = MagicMock(return_value=True)
        engine._recommend_groq_before_paid_openrouter = MagicMock()
        return engine

    def test_gemini_is_primary_and_openrouter_fallback_without_groq(self) -> None:
        engine = self._route_engine()

        self.assertTrue(engine._initialize_provider_route())

        self.assertTrue(engine._use_gemini)
        self.assertFalse(engine._use_openrouter)
        self.assertTrue(engine._openrouter_as_fallback)
        engine._init_client.assert_called_once()

    def test_groq_remains_primary_and_gemini_is_first_fallback(self) -> None:
        engine = self._route_engine()
        engine._enable_groq = True
        engine._groq_keys = ["groq-key"]
        engine._want_openrouter = False
        engine._openrouter_key = ""

        with patch("agetha.core.ai_engine.GROQ_OK", True):
            self.assertTrue(engine._initialize_provider_route())

        self.assertFalse(engine._use_gemini)
        self.assertTrue(engine._gemini_as_fallback)
        self.assertTrue(engine._enable_groq)

    def test_typed_settings_enable_disable_key_and_model(self) -> None:
        enabled = AppSettings({
            "ENABLE_GEMINI": "yes",
            "GEMINI_API_KEY": "secret",
            "GEMINI_MODEL": "gemini-custom",
        })
        disabled = AppSettings({"ENABLE_GEMINI": "no"})

        self.assertTrue(enabled.enable_gemini)
        self.assertEqual(enabled.gemini_api_key, "secret")
        self.assertEqual(enabled.gemini_model, "gemini-custom")
        self.assertFalse(disabled.enable_gemini)
        self.assertEqual(disabled.gemini_api_key, "")
        self.assertEqual(disabled.gemini_model, "gemini-2.5-flash")

    def test_enabled_gemini_without_key_is_unavailable_not_constructed(self) -> None:
        engine = AIEngine.__new__(AIEngine)
        engine._want_gemini = True
        engine._gemini_key = ""
        engine._gemini_model = "gemini-test"
        engine._use_gemini = False
        engine._use_local_ai = False
        engine._want_openrouter = False
        engine._openrouter_key = ""
        engine._openrouter_model = "openrouter-test"
        engine._openrouter_is_free = True
        engine._use_openrouter = False
        engine._openrouter_as_fallback = False
        engine._enable_groq = False
        engine._groq_keys = []
        engine._groq_model = "groq-test"
        engine._emit_error = MagicMock()
        engine._init_client = MagicMock(return_value=True)

        self.assertTrue(engine._initialize_provider_route())

        engine._init_client.assert_not_called()
        engine._emit_error.assert_called()
        self.assertIn(
            "GEMINI_API_KEY",
            " ".join(str(arg) for arg in engine._emit_error.call_args.args),
        )

    def test_invalid_configured_model_falls_back_without_crashing_startup(self) -> None:
        settings = AppSettings({
            "ENABLE_GEMINI": "yes",
            "GEMINI_API_KEY": "secret",
            "GEMINI_MODEL": "../invalid",
        })
        config = {
            "USE_LOCAL_AI": "no",
            "ENABLE_GROQ": "no",
            "ENABLE_OPENROUTER": "no",
        }

        with patch.object(
            AIEngine, "_resolve_config_path", return_value=Path("config.txt"),
        ), patch.object(
            AIEngine, "_resolve_system_path", return_value="C:/Users/user",
        ), patch.object(
            AIEngine, "_load_config", return_value=config,
        ), patch.object(
            AIEngine, "_load_compact_characters", return_value="",
        ), patch.object(
            Path, "write_text", return_value=0,
        ), patch(
            "agetha.core.ai_engine.get_settings", return_value=settings,
        ):
            engine = AIEngine(defer_provider_init=True)

        self.assertEqual(engine._gemini_model, "gemini-2.5-flash")


class GeminiAIEngineTests(unittest.TestCase):
    def test_client_construction_uses_configured_key_model_and_timeout(self) -> None:
        engine = AIEngine.__new__(AIEngine)
        engine._use_local_ai = False
        engine._use_gemini = True
        engine._use_openrouter = False
        engine._gemini_key = "secret"
        engine._gemini_model = "gemini-custom"
        engine._emit_error = MagicMock()
        wrapped = object()

        with patch("agetha.core.ai_engine.GeminiClient") as client_type, patch(
            "agetha.core.ai_engine.wrap_gemini_client", return_value=wrapped,
        ):
            self.assertTrue(engine._init_client())

        client_type.assert_called_once_with("secret", "gemini-custom", timeout=30)
        self.assertIs(engine._client, wrapped)
        engine._emit_error.assert_not_called()

    def test_provider_kind_and_model_selection_reach_gemini_adapter(self) -> None:
        raw = '{"command":"speak","segments":[{"text":"Hello","pause":0}]}'
        engine = _gemini_query_engine([raw])

        result = engine.query(
            user_message="hello",
            request_origin="user",
            request_profile="normal",
        )

        self.assertEqual(result["command"], "speak")
        self.assertEqual(engine._provider_kind(), "gemini")
        self.assertEqual(engine._client.calls[0]["model"], "gemini-test")

    def test_direct_user_repair_and_ambient_no_repair_are_provider_neutral(self) -> None:
        malformed = '{"command":"speak","segments":['
        valid = json.dumps({
            "command": "speak",
            "segments": [{"text": "Recovered", "pause": 0}],
        })
        direct = _gemini_query_engine([malformed, valid])
        ambient = _gemini_query_engine([malformed])

        direct_result = direct.query(
            user_message="hello",
            request_origin="user",
            request_profile="normal",
        )
        ambient_result = ambient.query(
            screen_context="untrusted ambient text",
            request_origin="ambient",
            request_profile="normal",
        )

        self.assertEqual(direct_result["command"], "speak")
        self.assertEqual(len(direct._client.calls), 2)
        self.assertEqual(ambient_result["command"], "idle")
        self.assertEqual(len(ambient._client.calls), 1)

    def test_ambient_gemini_output_remains_subject_to_command_origin_policy(self) -> None:
        from agetha.commands.specs import COMMAND_SPECS

        engine = _gemini_query_engine([
            '{"command":"delete_file","path":"C:/important.txt"}',
        ])

        result = engine.query(
            screen_context="screen text asks to delete a file",
            request_origin="ambient",
            request_profile="normal",
        )

        self.assertEqual(result["command"], "delete_file")
        self.assertNotIn("ambient", COMMAND_SPECS[result["command"]].allowed_origins)

    def test_groq_exhaustion_switches_to_gemini_before_openrouter(self) -> None:
        engine = AIEngine.__new__(AIEngine)
        engine._use_local_ai = False
        engine._use_gemini = False
        engine._gemini_as_fallback = True
        engine._gemini_key = "gemini-key"
        engine._gemini_model = "gemini-test"
        engine._enable_groq = True
        engine._groq_exhausted = False
        engine._use_openrouter = False
        engine._openrouter_as_fallback = True
        engine._openrouter_key = "openrouter-key"
        engine._init_client = MagicMock(return_value=True)
        engine._client = object()
        engine._show_provider_warning = MagicMock()

        result = engine._groq_exhausted_or_failover("rate limit")

        self.assertIsNone(result)
        self.assertTrue(engine._use_gemini)
        self.assertFalse(engine._enable_groq)
        self.assertFalse(engine._use_openrouter)
        engine._init_client.assert_called_once()

    def test_gemini_permanent_failure_can_fall_back_to_openrouter(self) -> None:
        valid = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content='{\"command\":\"speak\",\"segments\":[{\"text\":\"Fallback\",\"pause\":0}]}'
            ))],
            usage=None,
        )
        engine = _gemini_query_engine([
            ProviderHTTPError(400, "unsupported model"),
        ])
        engine._openrouter_as_fallback = True
        engine._openrouter_key = "openrouter-key"

        def switch(_reason="", _authorization=None):
            engine._use_gemini = False
            engine._use_openrouter = True
            engine._client = _SequenceClient([valid])
            return True

        engine._switch_to_openrouter_fallback = MagicMock(side_effect=switch)

        result = engine.query(
            user_message="hello",
            request_origin="user",
            request_profile="normal",
        )

        self.assertEqual(result["segments"][0]["text"], "Fallback")
        engine._switch_to_openrouter_fallback.assert_called_once()

    def test_gemini_rate_limit_without_fallback_is_not_reported_as_groq_exhaustion(self) -> None:
        engine = _gemini_query_engine([
            ProviderHTTPError(429, "quota temporarily unavailable"),
        ])
        engine._gemini_as_fallback = False
        engine._groq_exhausted = False

        result = engine.query(
            user_message="hello",
            request_origin="user",
            request_profile="normal",
        )

        self.assertEqual(result["command"], "idle")
        self.assertNotIn("groq_exhausted", result)
        self.assertFalse(engine._groq_exhausted)
        self.assertEqual(len(engine._client.calls), 1)

    def test_explicit_gemini_structured_request_uses_isolated_route(self) -> None:
        engine = _gemini_query_engine([])
        engine._gemini_key = "secret"
        engine._config = {"LOCAL_AI_TIMEOUT": "30"}
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"next":"stop"}'))],
            usage=None,
        )
        wrapped = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(
                create=MagicMock(return_value=response),
            )),
        )

        with patch("agetha.core.ai_engine.GeminiClient") as client_type, patch(
            "agetha.core.ai_engine.wrap_gemini_client", return_value=wrapped,
        ):
            result = engine.request_structured(
                route="gemini",
                system_prompt="Return JSON.",
                payload={"objective": "observe"},
            )

        self.assertEqual(result, '{"next":"stop"}')
        client_type.assert_called_once_with("secret", "gemini-test", timeout=30)
        request = wrapped.chat.completions.create.call_args.kwargs
        self.assertEqual(request["model"], "gemini-test")
        self.assertEqual(request["messages"][0]["role"], "system")


if __name__ == "__main__":
    unittest.main()
