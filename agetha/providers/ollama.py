"""Local Ollama transport and adapter."""

from __future__ import annotations

import json
import urllib.request
from types import SimpleNamespace

from .base import create_chat_completion


class LocalOllamaClient:
    """Minimal Ollama REST client with a chat-completions-compatible surface."""

    OLLAMA_URL = "http://localhost:11434/api/chat"
    TAGS_URL = "http://localhost:11434/api/tags"

    def __init__(self, model: str, timeout: int = 30):
        self.model = model
        self.timeout = timeout

    @staticmethod
    def list_models() -> set[str]:
        try:
            with urllib.request.urlopen(LocalOllamaClient.TAGS_URL, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception:
            return set()
        names: set[str] = set()
        for item in data.get("models", []):
            name = (item.get("name") or "").strip()
            if name:
                names.add(name)
                names.add(name.split(":")[0])
        return names

    @staticmethod
    def validate_model(model: str) -> tuple[bool, str]:
        model = model.strip()
        if not model:
            return False, "LOCAL_AI_MODEL is empty."
        available = LocalOllamaClient.list_models()
        if not available:
            return True, ""
        if model in available or model.split(":")[0] in available:
            return True, ""
        sample = ", ".join(sorted(available)[:8])
        return False, f"Model '{model}' not in Ollama. Installed: {sample or '(none listed)'}"

    def _generate(self, messages: list, *, temperature: float = 0.7,
                  max_tokens: int = 400, top_p: float = 0.95) -> str:
        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": float(temperature),
                "num_predict": max(1, int(max_tokens)),
                "top_p": float(top_p),
            },
        }).encode()
        request = urllib.request.Request(
            self.OLLAMA_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw_bytes = response.read()
        text = raw_bytes.decode("utf-8", errors="replace").strip()
        for line in text.splitlines():
            try:
                item = json.loads(line.strip())
                content = (item.get("message", {}).get("content") or item.get("response") or "").strip()
                if content:
                    return content
            except Exception:
                continue
        return text

    @staticmethod
    def _prepare_messages(messages) -> list:
        return [{
            "role": item.get("role") if isinstance(item, dict) else getattr(item, "role", "user"),
            "content": item.get("content") if isinstance(item, dict) else getattr(item, "content", ""),
        } for item in (messages or [])]

    def _generate_sync(self, messages=None, temperature=0.7, max_tokens=400,
                       top_p=0.95, **_kwargs):
        raw = self._generate(
            self._prepare_messages(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
        ) or ""
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw))])

    def _generate_stream(self, messages=None, temperature=0.7, max_tokens=400,
                         top_p=0.95, **_kwargs):
        raw = self._generate(
            self._prepare_messages(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
        ) or ""
        for chunk in ([raw[index:index + 120] for index in range(0, len(raw), 120)] or [raw]):
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=chunk))])

    def chat_completions_create(self, *, stream=False, **kwargs):
        if stream:
            return self._generate_stream(**kwargs)
        return self._generate_sync(**kwargs)


def wrap_ollama_client(client: LocalOllamaClient) -> object:
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=client.chat_completions_create),
        ),
    )


class OllamaProvider:
    kind = "ollama"

    def __init__(self, *, client: object, model: str) -> None:
        self.model = model
        self.client = client

    def create(self, *, messages, profile_name, temperature, max_tokens,
               top_p, timeout, stream):
        del profile_name
        return create_chat_completion(
            self.client,
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            timeout=timeout,
            stream=stream,
        )
