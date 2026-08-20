from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agetha.core.ai_engine import AIEngine
from agetha.core.request_context import request_profile_for_origin


STATUS_KEY = "provider_response_status"


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        ai_temperature=0.65,
        ai_max_tokens=220,
        ai_top_p=0.90,
        enable_unicode_typing=True,
        enable_window_control=True,
        enable_web_rag=True,
        enable_glitch_effects=True,
        enable_longterm_memory=False,
    )


def _parser_engine() -> AIEngine:
    engine = AIEngine.__new__(AIEngine)
    engine._command_execution_enabled = True
    engine._app_settings = _settings()
    return engine


class _SequenceClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        index = len(self.calls) - 1
        if index >= len(self.responses):
            raise AssertionError("provider was called more times than the recovery budget")
        raw = self.responses[index]
        if isinstance(raw, BaseException):
            raise raw
        raw = str(raw)
        if kwargs.get("stream"):
            return iter([SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=raw))],
                usage=None,
            )])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=raw))],
            usage=None,
        )


def _query_engine(responses: list[object]) -> AIEngine:
    engine = AIEngine.__new__(AIEngine)
    engine._client = _SequenceClient(responses)
    engine._show_error_gif = False
    engine._use_local_ai = False
    engine._use_openrouter = True
    engine._openrouter_model = "test/model"
    engine._enable_groq = False
    engine._app_settings = _settings()
    engine._faster_mode = True
    engine._fast_profile_active = True
    engine._fast_mode_original_values = {"AI_MAX_TOKENS": "600"}
    engine._update_user_activity = lambda _message: None
    engine._track_tokens = lambda _usage: None
    engine._record = MagicMock()
    engine._save_memory = MagicMock()
    engine._build_prompt = lambda *_args, **_kwargs: (
        "system prompt",
        "User: direct request",
        [],
    )
    return engine


class TestProviderResponseStates(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _parser_engine()

    def test_intentional_idle_is_valid_not_a_parse_failure(self):
        result = self.engine._parse('{"command":"idle","segments":[]}')
        self.assertEqual(result["command"], "idle")
        self.assertEqual(result.get(STATUS_KEY), "ok")

    def test_fenced_json_is_repaired_without_changing_the_envelope(self):
        result = self.engine._parse(
            '```json\n{"command":"speak","segments":[{"text":"Hi","pause":0}]}\n```'
        )
        self.assertEqual(result["command"], "speak")
        self.assertEqual(result["segments"], [{"text": "Hi", "pause": 0.0}])
        self.assertEqual(result.get(STATUS_KEY), "repaired")

    def test_malformed_json_is_distinct_from_idle(self):
        result = self.engine._parse("this is not JSON")
        self.assertEqual(result["command"], "idle")
        self.assertEqual(result.get(STATUS_KEY), "malformed_json")

    def test_complete_partial_speak_is_the_only_safe_malformed_recovery(self):
        result = self.engine._parse(
            '{"command":"speak","segments":[{"text":"Recovered safely"'
        )
        self.assertEqual(result["command"], "speak")
        self.assertEqual(
            result["segments"],
            [{"text": "Recovered safely", "pause": 0.0}],
        )
        self.assertEqual(result.get(STATUS_KEY), "repaired")

    def test_malformed_capability_command_is_never_reconstructed(self):
        result = self.engine._parse(
            '{"command":"delete_file","path":"C:\\\\Users\\\\test\\\\notes.txt"'
        )
        self.assertEqual(result["command"], "idle")
        self.assertNotIn("path", result)
        self.assertEqual(result.get(STATUS_KEY), "malformed_json")

    def test_wrong_top_level_shape_is_schema_failure(self):
        result = self.engine._parse('[{"command":"idle"}]')
        self.assertEqual(result["command"], "idle")
        self.assertEqual(result.get(STATUS_KEY), "schema_failure")

    def test_missing_command_is_schema_failure_not_rescued_speech(self):
        result = self.engine._parse('{"response":"ordinary provider prose"}')
        self.assertEqual(result["command"], "idle")
        self.assertEqual(result.get(STATUS_KEY), "schema_failure")

    def test_unsupported_command_is_distinct_from_schema_failure(self):
        result = self.engine._parse('{"command":"launch_missiles"}')
        self.assertEqual(result["command"], "idle")
        self.assertEqual(result.get(STATUS_KEY), "unsupported_command")

    def test_empty_speak_is_schema_failure(self):
        for raw in (
            '{"command":"speak","segments":[]}',
            '{"command":"speak","segments":[{"text":"   ","pause":0}]}',
        ):
            with self.subTest(raw=raw):
                result = self.engine._parse(raw)
                self.assertEqual(result["command"], "idle")
                self.assertEqual(result.get(STATUS_KEY), "schema_failure")

    def test_missing_required_command_fields_are_schema_failures(self):
        for raw in (
            '{"command":"delete_file"}',
            '{"command":"delete_file","path":123}',
            '{"command":"read_document"}',
            '{"command":"read_document","path":false}',
            '{"command":"read_file"}',
            '{"command":"type_text"}',
            '{"command":"type_text","text":123}',
            '{"command":"open_browser"}',
            '{"command":"create_file","path":"C:/tmp"}',
            '{"command":"create_file","file_name":"notes.txt"}',
            '{"command":"target_window_move","target_app":"Notepad"}',
            '{"command":"target_window_move","target_app":"Notepad","x":"1","y":2}',
            '{"command":"target_window_resize","target_app":"Notepad","x":1,"y":2,"width":"800","height":600}',
        ):
            with self.subTest(raw=raw):
                result = self.engine._parse(raw)
                self.assertEqual(result["command"], "idle")
                self.assertEqual(result.get(STATUS_KEY), "schema_failure")

    def test_required_command_fields_preserve_valid_envelopes(self):
        for raw, expected in (
            ('{"command":"delete_file","path":"notes.txt"}', "delete_file"),
            ('{"command":"read_document","path":"notes.txt"}', "read_document"),
            ('{"command":"read_file","path":"notes.txt"}', "read_file"),
            ('{"command":"type_text","text":"   "}', "type_text"),
            ('{"command":"open_browser","search":"Agetha"}', "open_browser"),
            ('{"command":"create_file","file_path":"C:/tmp/notes.txt","content":""}', "create_file"),
            ('{"command":"create_file","path":"C:/tmp","file_name":"notes.txt"}', "create_file"),
            ('{"command":"target_window_move","target_app":"Notepad","x":1,"y":2}', "target_window_move"),
            ('{"command":"target_window_resize","target_app":"Notepad","x":1,"y":2,"width":800,"height":600}', "target_window_resize"),
        ):
            with self.subTest(raw=raw):
                result = self.engine._parse(raw)
                self.assertEqual(result["command"], expected)
                self.assertEqual(result.get(STATUS_KEY), "ok")


class ResponseRecoveryTestCase(unittest.TestCase):
    def _query(self, engine: AIEngine, **kwargs) -> dict:
        try:
            return engine.query(**kwargs)
        except TypeError as exc:
            self.fail(f"query must accept trusted request_origin: {exc}")

    def _query_streaming(self, engine: AIEngine, **kwargs) -> dict:
        try:
            return engine.query_streaming(**kwargs)
        except TypeError as exc:
            self.fail(f"query_streaming must accept trusted request_origin: {exc}")


class TestNonStreamingResponseRecovery(ResponseRecoveryTestCase):
    def test_direct_user_gets_one_repair_and_exactly_once_side_effects(self):
        malformed = '{"command":"speak","segments":['
        valid = json.dumps({
            "command": "speak",
            "mood": "neutral",
            "segments": [{"text": "Recovered", "pause": 0}],
            "summary_memory": "final response only",
        })
        engine = _query_engine([malformed, valid])

        with patch("agetha.core.ai_engine._MEMORY_SYSTEM_AVAILABLE", False):
            result = self._query(
                engine,
                user_message="hello",
                request_profile="fast_user",
                request_origin="user",
            )

        self.assertEqual(result["segments"][0]["text"], "Recovered")
        self.assertEqual(len(engine._client.calls), 2)
        self.assertNotIn("previous provider response failed", str(engine._client.calls[0]))
        self.assertIn("previous provider response failed", str(engine._client.calls[1]).lower())
        engine._save_memory.assert_called_once_with("final response only")
        engine._record.assert_called_once()
        recorded_assistant = engine._record.call_args.args[1]
        self.assertIn("Recovered", recorded_assistant)
        self.assertNotIn(malformed, recorded_assistant)

    def test_direct_user_failure_after_repair_is_deterministic(self):
        engine = _query_engine(["not JSON", '{"command":'])

        result = self._query(
            engine,
            user_message="hello",
            request_profile="fast_user",
            request_origin="user",
        )

        self.assertEqual(len(engine._client.calls), 2)
        self.assertEqual(result["command"], "speak")
        self.assertEqual(result.get(STATUS_KEY), "malformed_json")
        self.assertEqual(result["segments"], [{
            "text": "I couldn't interpret that response. Please try again.",
            "pause": 0.0,
        }])
        engine._save_memory.assert_not_called()
        engine._record.assert_called_once()
        recorded_assistant = engine._record.call_args.args[1]
        self.assertNotIn("not JSON", recorded_assistant)
        self.assertEqual(json.loads(recorded_assistant), result)

    def test_repair_provider_error_cannot_start_another_retry_cycle(self):
        engine = _query_engine(["not JSON", RuntimeError("provider outage")])

        result = self._query(
            engine,
            user_message="hello",
            request_profile="fast_user",
            request_origin="user",
        )

        self.assertEqual(len(engine._client.calls), 2)
        self.assertEqual(result["command"], "speak")
        self.assertEqual(result.get(STATUS_KEY), "malformed_json")
        engine._record.assert_called_once()
        engine._save_memory.assert_not_called()

    def test_direct_user_repairs_schema_and_unsupported_failures_once(self):
        valid = '{"command":"speak","segments":[{"text":"Fixed","pause":0}]}'
        for invalid in (
            '[{"command":"idle"}]',
            '{"command":"not_supported"}',
        ):
            with self.subTest(invalid=invalid):
                engine = _query_engine([invalid, valid])
                result = self._query(
                    engine,
                    user_message="hello",
                    request_profile="fast_user",
                    request_origin="user",
                )
                self.assertEqual(len(engine._client.calls), 2)
                self.assertEqual(result["segments"][0]["text"], "Fixed")
                engine._record.assert_called_once()
                engine._save_memory.assert_not_called()

    def test_untrusted_origins_never_receive_a_repair_cycle(self):
        for origin in (
            "ambient",
            "touch",
            "file_drop",
            "reminder",
            "tool_result",
            "terminal_sentinel",
        ):
            with self.subTest(origin=origin):
                engine = _query_engine(["not JSON"])
                result = self._query(
                    engine,
                    user_message="untrusted context text",
                    request_profile=request_profile_for_origin(origin),
                    request_origin=origin,
                )
                self.assertEqual(len(engine._client.calls), 1)
                self.assertEqual(result["command"], "idle")
                self.assertEqual(result.get(STATUS_KEY), "malformed_json")
                engine._save_memory.assert_not_called()
                engine._record.assert_not_called()

    def test_missing_or_invalid_origin_never_grants_repair(self):
        for origin_kwargs in ({}, {"request_origin": None}, {"request_origin": "forged"}):
            with self.subTest(origin_kwargs=origin_kwargs):
                engine = _query_engine(["not JSON"])
                result = self._query(
                    engine,
                    user_message="hello",
                    request_profile="fast_user",
                    **origin_kwargs,
                )
                self.assertEqual(len(engine._client.calls), 1)
                self.assertEqual(result["command"], "idle")
                self.assertEqual(result.get(STATUS_KEY), "malformed_json")
                engine._save_memory.assert_not_called()
                engine._record.assert_not_called()

    def test_valid_intentional_idle_does_not_trigger_repair(self):
        engine = _query_engine(['{"command":"idle","segments":[]}'])

        result = self._query(
            engine,
            user_message="hello",
            request_profile="fast_user",
            request_origin="user",
        )

        self.assertEqual(len(engine._client.calls), 1)
        self.assertEqual(result["command"], "speak")
        self.assertEqual(result.get(STATUS_KEY), "ok")


class TestStreamingResponseRecovery(ResponseRecoveryTestCase):
    def test_streaming_direct_user_gets_one_repair_and_one_history_write(self):
        valid = '{"command":"speak","segments":[{"text":"Stream recovered","pause":0}]}'
        engine = _query_engine([
            "not JSON",
            valid,
        ])
        published = []

        result = self._query_streaming(
            engine,
            user_message="hello",
            request_profile="fast_user",
            request_origin="user",
            on_token=published.append,
        )

        self.assertEqual(len(engine._client.calls), 2)
        self.assertEqual(result["segments"][0]["text"], "Stream recovered")
        self.assertEqual(published, [valid])
        engine._record.assert_called_once()
        engine._save_memory.assert_not_called()

    def test_streaming_double_failure_never_publishes_malformed_attempts(self):
        engine = _query_engine(["not JSON", '{"command":'])
        published = []

        result = self._query_streaming(
            engine,
            user_message="hello",
            request_profile="fast_user",
            request_origin="user",
            on_token=published.append,
        )

        self.assertEqual(len(engine._client.calls), 2)
        self.assertEqual(result["command"], "speak")
        self.assertEqual(result.get(STATUS_KEY), "malformed_json")
        self.assertEqual(published, [])

    def test_streaming_ambient_failure_never_retries(self):
        engine = _query_engine(["not JSON"])

        result = self._query_streaming(
            engine,
            screen_context="untrusted OCR",
            request_profile="fast_ambient",
            request_origin="ambient",
        )

        self.assertEqual(len(engine._client.calls), 1)
        self.assertEqual(result["command"], "idle")
        self.assertEqual(result.get(STATUS_KEY), "malformed_json")
        engine._record.assert_not_called()
        engine._save_memory.assert_not_called()

    def test_streaming_missing_or_invalid_origin_never_grants_repair(self):
        for origin_kwargs in ({}, {"request_origin": None}, {"request_origin": "forged"}):
            with self.subTest(origin_kwargs=origin_kwargs):
                engine = _query_engine(["not JSON"])
                result = self._query_streaming(
                    engine,
                    user_message="hello",
                    request_profile="fast_user",
                    **origin_kwargs,
                )
                self.assertEqual(len(engine._client.calls), 1)
                self.assertEqual(result["command"], "idle")
                self.assertEqual(result.get(STATUS_KEY), "malformed_json")
                engine._record.assert_not_called()
                engine._save_memory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
