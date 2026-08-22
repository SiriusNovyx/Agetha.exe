from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock


def _client() -> tuple[SimpleNamespace, MagicMock]:
    create = MagicMock(return_value=SimpleNamespace(choices=[]))
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    return client, create


class ProviderAdapterContractTests(unittest.TestCase):
    def test_groq_adapter_owns_gpt_oss_request_options(self) -> None:
        from agetha.providers.groq import GroqProvider

        client, create = _client()
        provider = GroqProvider(client=client, model="openai/gpt-oss-120b")

        provider.create(
            messages=[{"role": "user", "content": "hello"}],
            profile_name="fast_ambient",
            temperature=0.2,
            max_tokens=64,
            top_p=0.8,
            timeout=30,
            stream=False,
        )

        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["model"], "openai/gpt-oss-120b")
        self.assertEqual(kwargs["reasoning_effort"], "low")
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})

    def test_non_groq_adapters_do_not_receive_groq_only_options(self) -> None:
        from agetha.providers.ollama import OllamaProvider
        from agetha.providers.openrouter import OpenRouterProvider

        for provider_type in (OllamaProvider, OpenRouterProvider):
            with self.subTest(provider=provider_type.__name__):
                client, create = _client()
                provider = provider_type(client=client, model="example/model")
                provider.create(
                    messages=[{"role": "user", "content": "hello"}],
                    profile_name="deep_analysis",
                    temperature=0.2,
                    max_tokens=64,
                    top_p=0.8,
                    timeout=30,
                    stream=True,
                )
                kwargs = create.call_args.kwargs
                self.assertNotIn("reasoning_effort", kwargs)
                self.assertNotIn("response_format", kwargs)
                self.assertTrue(kwargs["stream"])

    def test_router_delegates_without_owning_agetha_semantics(self) -> None:
        from agetha.providers.router import ProviderRouter

        client, create = _client()
        router = ProviderRouter.for_existing_client(
            kind="openrouter",
            client=client,
            model="example/model",
        )
        router.create(
            messages=[{"role": "system", "content": "system"}],
            profile_name="normal",
            temperature=0.1,
            max_tokens=80,
            top_p=0.9,
            timeout=15,
            stream=False,
        )
        self.assertEqual(create.call_count, 1)
        self.assertEqual(router.kind, "openrouter")
        self.assertIs(router.client, client)

    def test_ai_engine_transport_names_remain_compatibility_aliases(self) -> None:
        from agetha.core import ai_engine
        from agetha.providers.ollama import LocalOllamaClient
        from agetha.providers.openrouter import OpenRouterClient

        self.assertIs(ai_engine._LocalOllamaClient, LocalOllamaClient)
        self.assertIs(ai_engine._OpenRouterClient, OpenRouterClient)

    def test_core_provider_protocol_reexports_transport_error_policy(self) -> None:
        from agetha.core import provider_protocol
        from agetha.providers.base import (
            ProviderErrorKind,
            ProviderHTTPError,
            classify_provider_error,
        )

        self.assertIs(provider_protocol.ProviderErrorKind, ProviderErrorKind)
        self.assertIs(provider_protocol.ProviderHTTPError, ProviderHTTPError)
        self.assertIs(provider_protocol.classify_provider_error, classify_provider_error)


if __name__ == "__main__":
    unittest.main()
