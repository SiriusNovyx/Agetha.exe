from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from agetha.providers.base import (
    ProviderErrorKind,
    ProviderHTTPError,
    classify_provider_error,
)


class _Reply:
    def __init__(self, payload: bytes | list[bytes]) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        assert isinstance(self._payload, bytes)
        return self._payload

    def __iter__(self):
        assert isinstance(self._payload, list)
        return iter(self._payload)


class GeminiProviderTests(unittest.TestCase):
    def test_message_conversion_separates_system_and_maps_assistant_role(self) -> None:
        from agetha.providers.gemini import GeminiClient

        system_instruction, contents = GeminiClient.convert_messages([
            {"role": "system", "content": "System one."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "system", "content": "System two."},
        ])

        self.assertEqual(
            system_instruction,
            {"parts": [{"text": "System one.\n\nSystem two."}]},
        )
        self.assertEqual(contents, [
            {"role": "user", "parts": [{"text": "Hello"}]},
            {"role": "model", "parts": [{"text": "Hi"}]},
        ])

    def test_normal_request_uses_header_key_json_mode_and_configured_model(self) -> None:
        from agetha.providers.gemini import GeminiClient

        captured = {}
        response = {
            "candidates": [{
                "content": {"parts": [{"text": '{"command":"idle"}'}]},
                "finishReason": "STOP",
            }],
            "usageMetadata": {
                "promptTokenCount": 11,
                "candidatesTokenCount": 7,
                "totalTokenCount": 18,
            },
        }

        def _open(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _Reply(json.dumps(response).encode("utf-8"))

        client = GeminiClient("secret-key", "gemini-2.5-flash", timeout=19)
        with patch("agetha.providers.gemini.urllib.request.urlopen", side_effect=_open):
            result = client.chat_completions_create(
                messages=[
                    {"role": "system", "content": "Return JSON."},
                    {"role": "user", "content": "Hello"},
                ],
                temperature=0.4,
                max_tokens=123,
                top_p=0.8,
            )

        request = captured["request"]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertTrue(request.full_url.endswith("/models/gemini-2.5-flash:generateContent"))
        self.assertNotIn("secret-key", request.full_url)
        self.assertEqual(request.headers["X-goog-api-key"], "secret-key")
        self.assertEqual(captured["timeout"], 19)
        self.assertEqual(payload["systemInstruction"]["parts"][0]["text"], "Return JSON.")
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 123)
        self.assertEqual(
            payload["generationConfig"]["thinkingConfig"],
            {"thinkingBudget": 0},
        )
        self.assertEqual(result.choices[0].message.content, '{"command":"idle"}')
        self.assertEqual(result.choices[0].finish_reason, "STOP")
        self.assertEqual(result.usage.prompt_tokens, 11)
        self.assertEqual(result.usage.completion_tokens, 7)
        self.assertEqual(result.usage.total_tokens, 18)

    def test_streaming_sse_converts_text_and_final_usage(self) -> None:
        from agetha.providers.gemini import GeminiClient

        first = {
            "candidates": [{"content": {"parts": [{"text": '{"command":'}]}}],
        }
        second = {
            "candidates": [{
                "content": {"parts": [{"text": '"idle"}'}]},
                "finishReason": "STOP",
            }],
            "usageMetadata": {
                "promptTokenCount": 3,
                "candidatesTokenCount": 2,
                "totalTokenCount": 5,
            },
        }
        lines = [
            b"event: message\n",
            b"data: not-json\n",
            f"data: {json.dumps(first)}\n".encode(),
            f"data: {json.dumps(second)}\n".encode(),
        ]
        client = GeminiClient("secret", "models/gemini-test")
        with patch(
            "agetha.providers.gemini.urllib.request.urlopen",
            return_value=_Reply(lines),
        ) as opened:
            chunks = list(client.chat_completions_create(
                messages=[{"role": "user", "content": "Hello"}],
                stream=True,
            ))

        request = opened.call_args.args[0]
        self.assertTrue(request.full_url.endswith(
            "/models/gemini-test:streamGenerateContent?alt=sse"
        ))
        self.assertEqual(
            "".join(chunk.choices[0].delta.content for chunk in chunks),
            '{"command":"idle"}',
        )
        self.assertIsNone(chunks[0].usage)
        self.assertEqual(chunks[-1].usage.total_tokens, 5)

    def test_malformed_success_response_is_empty_for_ai_engine_parser(self) -> None:
        from agetha.providers.gemini import GeminiClient

        client = GeminiClient("secret", "gemini-test")
        with patch(
            "agetha.providers.gemini.urllib.request.urlopen",
            return_value=_Reply(b'{"candidates":[]}'),
        ):
            result = client.chat_completions_create(
                messages=[{"role": "user", "content": "Hello"}],
            )

        self.assertEqual(result.choices[0].message.content, "")
        self.assertIsNone(result.usage)

    def test_http_errors_convert_without_leaking_key_and_classify(self) -> None:
        from agetha.providers.gemini import GeminiClient

        failure = urllib.error.HTTPError(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b'{"error":{"message":"quota temporarily unavailable"}}'),
        )
        client = GeminiClient("private-secret", "gemini-test")
        with patch(
            "agetha.providers.gemini.urllib.request.urlopen",
            side_effect=failure,
        ):
            with self.assertRaises(ProviderHTTPError) as raised:
                client.chat_completions_create(
                    messages=[{"role": "user", "content": "Hello"}],
                )

        self.assertEqual(raised.exception.status_code, 429)
        self.assertNotIn("private-secret", str(raised.exception))
        self.assertEqual(
            classify_provider_error(raised.exception),
            ProviderErrorKind.RATE_LIMIT,
        )

    def test_http_error_classes_cover_auth_permanent_and_transient(self) -> None:
        from agetha.providers.gemini import GeminiClient

        cases = (
            (401, "invalid API key", ProviderErrorKind.AUTHENTICATION),
            (400, "unsupported request option", ProviderErrorKind.PERMANENT_REQUEST),
            (404, "model gemini-retired not found", ProviderErrorKind.PERMANENT_MODEL),
            (503, "service unavailable", ProviderErrorKind.TRANSIENT),
        )
        client = GeminiClient("secret", "gemini-test")
        for status, message, expected in cases:
            with self.subTest(status=status):
                failure = urllib.error.HTTPError(
                    "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent",
                    status,
                    message,
                    {},
                    io.BytesIO(json.dumps({"error": {"message": message}}).encode()),
                )
                with patch(
                    "agetha.providers.gemini.urllib.request.urlopen",
                    side_effect=failure,
                ):
                    with self.assertRaises(ProviderHTTPError) as raised:
                        client.chat_completions_create(
                            messages=[{"role": "user", "content": "Hello"}],
                        )
                self.assertEqual(classify_provider_error(raised.exception), expected)

    def test_model_name_cannot_inject_a_url_path_or_query(self) -> None:
        from agetha.providers.gemini import GeminiClient

        for value in ("../other", "gemini?key=leak", "models/a/b", "bad\nname"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                GeminiClient("secret", value)

    def test_router_registers_gemini_without_groq_only_options(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from agetha.providers.router import ProviderRouter

        create = MagicMock(return_value=SimpleNamespace(choices=[]))
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        )
        router = ProviderRouter.for_existing_client(
            kind="gemini", client=client, model="gemini-test",
        )
        router.create(
            messages=[{"role": "user", "content": "Hello"}],
            profile_name="deep_analysis",
            temperature=0.2,
            max_tokens=64,
            top_p=0.8,
            timeout=30,
            stream=False,
        )

        self.assertEqual(router.kind, "gemini")
        self.assertNotIn("reasoning_effort", create.call_args.kwargs)
        self.assertNotIn("response_format", create.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
