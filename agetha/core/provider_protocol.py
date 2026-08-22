"""Provider-specific request and failure policy for Agetha's neutral envelope."""

from __future__ import annotations

from enum import Enum

from agetha.providers.base import (
    ProviderErrorKind,
    ProviderHTTPError,
    classify_provider_error,
)

from agetha.providers.groq import (
    DEFAULT_GROQ_MODEL,
    GPT_OSS_MODELS,
    REASONING_EFFORT_BY_PROFILE,
    RETIRED_GROQ_MODELS,
    groq_request_options,
    normalize_groq_model,
    reasoning_effort_for_profile,
)


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
