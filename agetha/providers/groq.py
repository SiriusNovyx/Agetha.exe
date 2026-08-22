"""Groq transport construction and GPT-OSS request mechanics."""

from __future__ import annotations

from .base import create_chat_completion

try:
    from groq import Groq as _GroqSDK
    GROQ_AVAILABLE = True
except ImportError:
    _GroqSDK = None
    GROQ_AVAILABLE = False


DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
RETIRED_GROQ_MODELS = frozenset({"llama-3.3-70b-versatile"})
GPT_OSS_MODELS = frozenset({"openai/gpt-oss-20b", "openai/gpt-oss-120b"})
REASONING_EFFORT_BY_PROFILE = {
    "fast_ambient": "low",
    "fast_command": "low",
    "fast_user": "low",
    "normal": "medium",
    "fast_tool_result": "medium",
    "tool_continuation": "medium",
    "deep_analysis": "high",
}


def normalize_groq_model(value: object) -> str:
    candidate = str(value or "").strip()
    if not candidate or candidate.lower() in RETIRED_GROQ_MODELS:
        return DEFAULT_GROQ_MODEL
    return candidate


def reasoning_effort_for_profile(model: str, profile_name: str) -> str | None:
    if str(model or "").strip().lower() not in GPT_OSS_MODELS:
        return None
    return REASONING_EFFORT_BY_PROFILE.get(str(profile_name or ""), "medium")


def groq_request_options(model: str, profile_name: str) -> dict[str, object]:
    effort = reasoning_effort_for_profile(model, profile_name)
    if effort is None:
        return {}
    return {
        "reasoning_effort": effort,
        "response_format": {"type": "json_object"},
    }


def create_groq_client(api_key: str) -> object:
    if _GroqSDK is None:
        raise RuntimeError("Groq SDK is unavailable")
    return _GroqSDK(api_key=api_key)


class GroqProvider:
    kind = "groq"

    def __init__(self, *, client: object, model: str) -> None:
        self.model = normalize_groq_model(model)
        self.client = client

    def create(self, *, messages, profile_name, temperature, max_tokens,
               top_p, timeout, stream):
        return create_chat_completion(
            self.client,
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            timeout=timeout,
            stream=stream,
            request_options=groq_request_options(self.model, profile_name),
        )
