from __future__ import annotations

import unittest

try:
    from agetha.core import provider_protocol
except ImportError:
    provider_protocol = None


class ProviderProtocolTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(
            provider_protocol,
            "agetha.core.provider_protocol must define the provider policy",
        )


class TestGroqModelPolicy(ProviderProtocolTestCase):
    def test_retired_or_blank_configuration_selects_supported_default(self):
        for configured in (None, "", "  ", "llama-3.3-70b-versatile"):
            with self.subTest(configured=configured):
                self.assertEqual(
                    provider_protocol.normalize_groq_model(configured),
                    "openai/gpt-oss-120b",
                )

    def test_non_retired_custom_model_is_preserved(self):
        self.assertEqual(
            provider_protocol.normalize_groq_model(" custom/current-model "),
            "custom/current-model",
        )

    def test_reasoning_effort_follows_existing_request_profiles(self):
        expected = {
            "fast_ambient": "low",
            "fast_command": "low",
            "fast_user": "low",
            "normal": "medium",
            "fast_tool_result": "medium",
            "tool_continuation": "medium",
            "deep_analysis": "high",
        }
        for profile_name, effort in expected.items():
            with self.subTest(profile_name=profile_name):
                self.assertEqual(
                    provider_protocol.reasoning_effort_for_profile(
                        "openai/gpt-oss-120b", profile_name,
                    ),
                    effort,
                )

    def test_unknown_profile_defaults_to_medium_reasoning(self):
        self.assertEqual(
            provider_protocol.reasoning_effort_for_profile(
                "openai/gpt-oss-120b", "future_profile",
            ),
            "medium",
        )

    def test_gpt_oss_command_request_uses_json_object_mode(self):
        self.assertEqual(
            provider_protocol.groq_request_options(
                "openai/gpt-oss-120b", "deep_analysis",
            ),
            {
                "reasoning_effort": "high",
                "response_format": {"type": "json_object"},
            },
        )

    def test_non_gpt_oss_model_receives_no_gpt_oss_options(self):
        self.assertEqual(
            provider_protocol.groq_request_options(
                "custom/current-model", "deep_analysis",
            ),
            {},
        )
        self.assertIsNone(
            provider_protocol.reasoning_effort_for_profile(
                "custom/current-model", "deep_analysis",
            )
        )


class TestProviderErrorClassification(ProviderProtocolTestCase):
    def _http_error(self, status_code: int, message: str):
        return provider_protocol.ProviderHTTPError(status_code, message)

    def test_http_statuses_have_distinct_recovery_classes(self):
        cases = (
            (401, "invalid API key", provider_protocol.ProviderErrorKind.AUTHENTICATION),
            (403, "forbidden", provider_protocol.ProviderErrorKind.AUTHENTICATION),
            (429, "rate limit exceeded", provider_protocol.ProviderErrorKind.RATE_LIMIT),
            (503, "service unavailable", provider_protocol.ProviderErrorKind.TRANSIENT),
        )
        for status_code, message, expected in cases:
            with self.subTest(status_code=status_code):
                self.assertEqual(
                    provider_protocol.classify_provider_error(
                        self._http_error(status_code, message)
                    ),
                    expected,
                )

    def test_missing_or_retired_model_is_permanent_model_failure(self):
        for status_code, message in (
            (404, "model not found"),
            (400, "The model has been decommissioned"),
            (400, "The model 'legacy/model' has been decommissioned"),
            (400, "error code=model_decommissioned"),
            (400, "requested model does not exist"),
        ):
            with self.subTest(status_code=status_code, message=message):
                self.assertEqual(
                    provider_protocol.classify_provider_error(
                        self._http_error(status_code, message)
                    ),
                    provider_protocol.ProviderErrorKind.PERMANENT_MODEL,
                )

    def test_other_bad_request_is_permanent_request_failure(self):
        for status_code, message in (
            (400, "response_format is invalid"),
            (404, "endpoint not found"),
            (413, "request body is too large"),
            (422, "messages failed validation"),
        ):
            with self.subTest(status_code=status_code):
                self.assertEqual(
                    provider_protocol.classify_provider_error(
                        self._http_error(status_code, message)
                    ),
                    provider_protocol.ProviderErrorKind.PERMANENT_REQUEST,
                )

    def test_network_timeout_is_transient(self):
        self.assertEqual(
            provider_protocol.classify_provider_error(TimeoutError("timed out")),
            provider_protocol.ProviderErrorKind.TRANSIENT,
        )


if __name__ == "__main__":
    unittest.main()
