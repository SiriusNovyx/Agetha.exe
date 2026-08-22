"""Google Gemini REST/SSE transport and provider adapter."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from types import SimpleNamespace

from .base import ProviderHTTPError, create_chat_completion


GEMINI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def normalize_gemini_model(value: object) -> str:
    candidate = str(value or "").strip()
    if candidate.startswith("models/"):
        candidate = candidate[len("models/"):]
    if not candidate:
        return DEFAULT_GEMINI_MODEL
    if not _MODEL_NAME_RE.fullmatch(candidate):
        raise ValueError("Invalid Gemini model name")
    return candidate


def _text_from_candidate(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    content = item.get("content")
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    return "".join(
        str(part.get("text") or "")
        for part in parts
        if isinstance(part, dict)
    )


def _usage_namespace(value: object):
    if not isinstance(value, dict):
        return None
    prompt = int(value.get("promptTokenCount") or 0)
    completion = int(value.get("candidatesTokenCount") or 0)
    total = int(value.get("totalTokenCount") or prompt + completion)
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )


class GeminiClient:
    """Minimal Gemini client exposing Agetha's chat-completions subset."""

    def __init__(self, api_key: str, model: str, timeout: int = 30) -> None:
        self.api_key = str(api_key or "").strip()
        if not self.api_key:
            raise ValueError("Gemini API key is required")
        self.model = normalize_gemini_model(model)
        self.timeout = int(timeout)

    @staticmethod
    def convert_messages(
        messages: object,
    ) -> tuple[dict[str, list[dict[str, str]]] | None, list[dict[str, object]]]:
        system_parts: list[str] = []
        contents: list[dict[str, object]] = []
        for item in messages or []:
            if isinstance(item, dict):
                role = str(item.get("role") or "user").strip().lower()
                content = str(item.get("content") or "")
            else:
                role = str(getattr(item, "role", "user") or "user").strip().lower()
                content = str(getattr(item, "content", "") or "")
            if role == "system":
                if content:
                    system_parts.append(content)
                continue
            contents.append({
                "role": "model" if role == "assistant" else "user",
                "parts": [{"text": content}],
            })
        system_instruction = None
        if system_parts:
            system_instruction = {"parts": [{"text": "\n\n".join(system_parts)}]}
        return system_instruction, contents

    @staticmethod
    def _raise_http(exc: BaseException) -> None:
        if not isinstance(exc, urllib.error.HTTPError):
            raise exc
        code = int(getattr(exc, "code", 0) or 0)
        detail = str(getattr(exc, "reason", "") or exc)
        try:
            body = exc.read().decode("utf-8", errors="replace")[:400]
            parsed = json.loads(body)
            message = ((parsed.get("error") or {}).get("message") or "").strip()
            if message:
                detail = message
        except Exception:
            pass
        raise ProviderHTTPError(code, detail) from exc

    def _request(
        self,
        payload: dict[str, object],
        *,
        model: str,
        stream: bool,
    ) -> urllib.request.Request:
        action = "streamGenerateContent?alt=sse" if stream else "generateContent"
        model = urllib.parse.quote(model, safe="-._")
        return urllib.request.Request(
            f"{GEMINI_API_ROOT}/models/{model}:{action}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )

    def chat_completions_create(
        self,
        *,
        model=None,
        messages=None,
        temperature=0.7,
        max_tokens=400,
        top_p=0.95,
        timeout=None,
        stream=False,
    ):
        selected_model = normalize_gemini_model(model or self.model)
        system_instruction, contents = self.convert_messages(messages)
        payload: dict[str, object] = {
            "contents": contents,
            "generationConfig": {
                "temperature": float(temperature),
                "maxOutputTokens": int(max_tokens),
                "topP": float(top_p),
                "responseMimeType": "application/json",
            },
        }
        if system_instruction is not None:
            payload["systemInstruction"] = system_instruction
        request = self._request(
            payload, model=selected_model, stream=bool(stream),
        )
        request_timeout = int(timeout or self.timeout)
        if stream:
            return self._stream(request, request_timeout)
        try:
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
                raw_bytes = response.read()
        except Exception as exc:
            self._raise_http(exc)
        obj = json.loads(raw_bytes.decode("utf-8", errors="replace"))
        candidates = obj.get("candidates") if isinstance(obj, dict) else None
        first = candidates[0] if isinstance(candidates, list) and candidates else {}
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=_text_from_candidate(first)),
                finish_reason=(first.get("finishReason") if isinstance(first, dict) else None),
            )],
            usage=_usage_namespace(obj.get("usageMetadata") if isinstance(obj, dict) else None),
        )

    def _stream(self, request: urllib.request.Request, timeout: int):
        def chunks():
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    for line_bytes in response:
                        line = line_bytes.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        try:
                            item = json.loads(data)
                        except Exception:
                            continue
                        candidates = item.get("candidates") if isinstance(item, dict) else None
                        first = (
                            candidates[0]
                            if isinstance(candidates, list) and candidates
                            else {}
                        )
                        yield SimpleNamespace(
                            choices=[SimpleNamespace(
                                delta=SimpleNamespace(content=_text_from_candidate(first)),
                                finish_reason=(
                                    first.get("finishReason")
                                    if isinstance(first, dict) else None
                                ),
                            )],
                            usage=_usage_namespace(
                                item.get("usageMetadata") if isinstance(item, dict) else None
                            ),
                        )
            except Exception as exc:
                self._raise_http(exc)
        return chunks()


def wrap_gemini_client(client: GeminiClient) -> object:
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=client.chat_completions_create),
        ),
    )


class GeminiProvider:
    kind = "gemini"

    def __init__(self, *, client: object, model: str) -> None:
        self.model = normalize_gemini_model(model)
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
