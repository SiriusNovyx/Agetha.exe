"""Thin provider selection over explicit adapters."""

from __future__ import annotations

from .base import ProviderAdapter
from .gemini import GeminiProvider
from .groq import GroqProvider
from .ollama import OllamaProvider
from .openrouter import OpenRouterProvider


class ProviderRouter:
    """Delegate one request to the selected adapter without Agetha policy."""

    def __init__(self, provider: ProviderAdapter) -> None:
        self._provider = provider

    @classmethod
    def for_existing_client(cls, *, kind: str, client: object,
                            model: str) -> "ProviderRouter":
        providers = {
            "gemini": GeminiProvider,
            "groq": GroqProvider,
            "openrouter": OpenRouterProvider,
            "ollama": OllamaProvider,
        }
        try:
            provider_type = providers[str(kind).strip().lower()]
        except KeyError as exc:
            raise ValueError(f"Unknown provider kind: {kind}") from exc
        return cls(provider_type(client=client, model=model))

    @property
    def kind(self) -> str:
        return self._provider.kind

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def client(self) -> object:
        return self._provider.client

    def create(self, **request):
        return self._provider.create(**request)
