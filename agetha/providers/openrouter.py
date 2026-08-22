"""OpenRouter HTTP transport and adapter."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from types import SimpleNamespace

from .base import ProviderHTTPError, create_chat_completion


OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "google/gemma-4-31b-it:free"


class OpenRouterClient:
    """Minimal OpenRouter chat-completions client."""

    def __init__(self, api_key: str, model: str, timeout: int = 30):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @staticmethod
    def _messages(messages) -> list[dict[str, str]]:
        return [{
            "role": item.get("role") if isinstance(item, dict) else getattr(item, "role", "user"),
            "content": item.get("content") if isinstance(item, dict) else getattr(item, "content", ""),
        } for item in (messages or [])]

    @staticmethod
    def _raise_http(exc: BaseException) -> None:
        if not isinstance(exc, urllib.error.HTTPError):
            raise exc
        code = getattr(exc, "code", None)
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        detail = str(exc)
        if body:
            try:
                api_message = ((json.loads(body).get("error") or {}).get("message") or "").strip()
                if api_message:
                    detail = f"HTTP Error {code}: {api_message}"
            except Exception:
                pass
        raise ProviderHTTPError(code or 0, detail) from exc

    def chat_completions_create(self, *, model=None, messages=None,
                                temperature=0.7, max_tokens=400,
                                top_p=0.95, timeout=None, stream=False):
        payload = {
            "model": model or self.model,
            "messages": self._messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
            "stream": stream,
        }
        request = urllib.request.Request(
            OPENROUTER_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        request_timeout = timeout or self.timeout
        if stream:
            return self._stream(request, request_timeout)
        try:
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
                raw_bytes = response.read()
        except Exception as exc:
            self._raise_http(exc)
        obj = json.loads(raw_bytes.decode("utf-8", errors="replace"))
        content = ((obj.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        usage = obj.get("usage")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(**usage) if isinstance(usage, dict) else None,
        )

    def _stream(self, request, timeout):
        def chunks():
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    for line_bytes in response:
                        line = line_bytes.decode("utf-8", errors="replace").strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if data == "[DONE]":
                            break
                        try:
                            item = json.loads(data)
                        except Exception:
                            continue
                        choices = item.get("choices") or [{}]
                        delta = (choices[0] or {}).get("delta") or {}
                        usage = item.get("usage")
                        yield SimpleNamespace(
                            choices=[SimpleNamespace(delta=SimpleNamespace(content=delta.get("content") or ""))],
                            usage=SimpleNamespace(**usage) if isinstance(usage, dict) else None,
                        )
            except Exception as exc:
                self._raise_http(exc)
        return chunks()


def wrap_openrouter_client(client: OpenRouterClient) -> object:
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=client.chat_completions_create),
        ),
    )


class OpenRouterProvider:
    kind = "openrouter"

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
