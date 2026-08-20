"""Provider-specific request and failure policy for Agetha's neutral envelope."""

from __future__ import annotations

from enum import Enum


DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
RETIRED_GROQ_MODELS = frozenset({"llama-3.3-70b-versatile"})
GPT_OSS_MODELS = frozenset({
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
})

REASONING_EFFORT_BY_PROFILE = {
    "fast_ambient": "low",
    "fast_command": "low",
    "fast_user": "low",
    "normal": "medium",
    "fast_tool_result": "medium",
    "tool_continuation": "medium",
    "deep_analysis": "high",
}


class ProviderErrorKind(str, Enum):
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    PERMANENT_MODEL = "permanent_model"
    PERMANENT_REQUEST = "permanent_request"
    TRANSIENT = "transient"


class ProviderResponseStatus(str, Enum):
    OK = "ok"
    REPAIRED = "repaired"
    MALFORMED_JSON = "malformed_json"
    SCHEMA_FAILURE = "schema_failure"
    UNSUPPORTED_COMMAND = "unsupported_command"


PROVIDER_RESPONSE_STATUS_KEY = "provider_response_status"
FAILED_RESPONSE_STATUSES = frozenset({
    ProviderResponseStatus.MALFORMED_JSON.value,
    ProviderResponseStatus.SCHEMA_FAILURE.value,
    ProviderResponseStatus.UNSUPPORTED_COMMAND.value,
})


def provider_response_failed(result: object) -> bool:
    return (
        isinstance(result, dict)
        and result.get(PROVIDER_RESPONSE_STATUS_KEY) in FAILED_RESPONSE_STATUSES
    )


class ProviderHTTPError(RuntimeError):
    """HTTP failure that keeps its status code across provider adapters."""

    def __init__(self, status_code: int, detail: str = "") -> None:
        self.status_code = int(status_code)
        self.code = self.status_code
        self.detail = str(detail or "").strip()
        message = f"HTTP {self.status_code}"
        if self.detail:
            message = f"{message}: {self.detail}"
        super().__init__(message)


def normalize_groq_model(value: object) -> str:
    candidate = str(value or "").strip()
    if not candidate or candidate.lower() in RETIRED_GROQ_MODELS:
        return DEFAULT_GROQ_MODEL
    return candidate


def reasoning_effort_for_profile(model: str, profile_name: str) -> str | None:
    normalized_model = str(model or "").strip().lower()
    if normalized_model not in GPT_OSS_MODELS:
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


def _status_code(exc: BaseException) -> int | None:
    candidates = (
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    )
    for candidate in candidates:
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def classify_provider_error(exc: BaseException) -> ProviderErrorKind:
    status_code = _status_code(exc)
    message = str(exc).lower()

    if (
        status_code == 429
        or "rate limit" in message
        or "rate_limit" in message
        or "too many requests" in message
    ):
        return ProviderErrorKind.RATE_LIMIT
    if status_code in {401, 403}:
        return ProviderErrorKind.AUTHENTICATION
    if status_code in {408, 409, 425} or (status_code is not None and status_code >= 500):
        return ProviderErrorKind.TRANSIENT
    if status_code in {400, 404}:
        model_failure_markers = (
            "decommission",
            "deprecated",
            "retired",
            "not found",
            "does not exist",
            "unknown",
            "unsupported",
        )
        if "model" in message and any(
            marker in message for marker in model_failure_markers
        ):
            return ProviderErrorKind.PERMANENT_MODEL
    if status_code is not None and 400 <= status_code < 500:
        return ProviderErrorKind.PERMANENT_REQUEST
    return ProviderErrorKind.TRANSIENT
