"""Small provider contract; Agetha semantics intentionally stay outside it."""

from __future__ import annotations

from enum import Enum
from typing import Protocol


class ProviderErrorKind(str, Enum):
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    PERMANENT_MODEL = "permanent_model"
    PERMANENT_REQUEST = "permanent_request"
    TRANSIENT = "transient"


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
        markers = (
            "decommission", "deprecated", "retired", "not found",
            "does not exist", "unknown", "unsupported",
        )
        if "model" in message and any(marker in message for marker in markers):
            return ProviderErrorKind.PERMANENT_MODEL
    if status_code is not None and 400 <= status_code < 500:
        return ProviderErrorKind.PERMANENT_REQUEST
    return ProviderErrorKind.TRANSIENT


class ProviderAdapter(Protocol):
    kind: str
    model: str
    client: object

    def create(
        self,
        *,
        messages: list[dict[str, str]],
        profile_name: str,
        temperature: float,
        max_tokens: int,
        top_p: float,
        timeout: int,
        stream: bool,
    ) -> object: ...


def create_chat_completion(
    client: object,
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    top_p: float,
    timeout: int,
    stream: bool,
    request_options: dict[str, object] | None = None,
) -> object:
    """Call the provider-neutral chat-completions subset used by Agetha."""
    return client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        timeout=timeout,
        stream=stream,
        **(request_options or {}),
    )
