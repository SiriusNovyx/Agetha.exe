"""Local parsing for explicit Computer Use activation.

Exact text payloads are separated before the provider request.  The provider
and planner receive only stable payload references; this module never logs or
persists the extracted values.
"""

from __future__ import annotations

import ntpath
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")
_SAFE_EXE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,119}\.exe$", re.I)
_TYPE_CUE_RE = re.compile(
    r"(?:^|\b(?:and|then)\s+|(?:แล้ว|และ)\s*)"
    r"(?:(?:can|could|would)\s+you\s+|please\s+)?"
    r"(?:type|enter|write|พิมพ์(?:คำว่า)?)\s+",
    re.I,
)
_SUBMIT_ACTION_RE = re.compile(
    r"\b(?:press|hit)\s+(?:the\s+)?enter\b|"
    r"(?:^|\b(?:and|then)\s+|\bplease\s+)"
    r"submit(?:\s+(?:it|this|the\s+form))?\b|"
    r"(?:กด|แล้วกด)\s*(?:ปุ่ม\s*)?(?:enter|เอนเทอร์)",
    re.I,
)
_EXPLICIT_COMPUTER_USE_RE = re.compile(
    r"\bcomputer\s+use\b|\b(?:use|control)\s+(?:my|the)\s+"
    r"(?:computer|desktop|screen)\b|(?:ใช้|ควบคุม)\s*"
    r"(?:คอมพิวเตอร์|คอม|หน้าจอ)",
    re.I,
)
_TRAILING_SUBMIT_RE = re.compile(
    r"\s+(?:and|then|แล้ว|และ)\s*(?:"
    r"(?:press|hit|กด)\s*(?:the\s+|ปุ่ม\s*)?(?:enter|เอนเทอร์)|"
    r"submit(?:\s+(?:it|this|the\s+form))?"
    r")\s*$",
    re.I,
)


@dataclass(frozen=True, slots=True)
class RequestedApplication:
    """Deterministic discovery/launch information, never model-generated."""

    process_name: str
    launch_command: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class LocalActivation:
    """Privacy-safe request plus an in-memory exact payload vault seed."""

    sanitized_request: str
    payloads: Mapping[str, str]
    requested_app: RequestedApplication | None
    typing_authorized: bool
    submit_authorized: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "payloads", MappingProxyType(dict(self.payloads)))

    def __repr__(self) -> str:
        return (
            "LocalActivation("
            f"payload_refs={tuple(sorted(self.payloads))!r}, "
            f"requested_app={self.requested_app!r}, "
            f"typing_authorized={self.typing_authorized!r}, "
            f"submit_authorized={self.submit_authorized!r})"
        )


_BUILTIN_APPS: dict[str, RequestedApplication] = {
    "notepad": RequestedApplication("notepad.exe", ("notepad.exe",)),
    "notepad.exe": RequestedApplication("notepad.exe", ("notepad.exe",)),
    "โน้ตแพด": RequestedApplication("notepad.exe", ("notepad.exe",)),
    "paint": RequestedApplication("mspaint.exe", ("mspaint.exe",)),
    "mspaint": RequestedApplication("mspaint.exe", ("mspaint.exe",)),
    "mspaint.exe": RequestedApplication("mspaint.exe", ("mspaint.exe",)),
    "calculator": RequestedApplication("calculatorapp.exe", ("calc.exe",)),
    "calc": RequestedApplication("calculatorapp.exe", ("calc.exe",)),
    "calc.exe": RequestedApplication("calculatorapp.exe", ("calc.exe",)),
}


def parse_configured_apps(value: object) -> tuple[RequestedApplication, ...]:
    """Parse trusted config basenames; paths and command arguments fail closed."""

    output: list[RequestedApplication] = []
    seen: set[str] = set()
    for raw in re.split(r"[,;\r\n]+", str(value or ""))[:128]:
        candidate = _CONTROL_RE.sub("", raw).strip()
        if not candidate or any(character in candidate for character in ("/", "\\", ":")):
            continue
        if re.search(r"\.exe\s+", candidate, re.I) or candidate.startswith(("-", "/")):
            continue
        if not candidate.casefold().endswith(".exe"):
            candidate += ".exe"
        if not _SAFE_EXE_RE.fullmatch(candidate):
            continue
        normalized = candidate.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        output.append(RequestedApplication(candidate, (candidate,)))
    return tuple(output)


def requested_application(
    message: object,
    *,
    configured_apps: object = "",
) -> RequestedApplication | None:
    """Resolve an app only from target-context grammar in direct user text."""

    text = _CONTROL_RE.sub(" ", str(message or ""))
    folded = text.casefold()
    for alias, application in _application_candidates(configured_apps):
        if not alias:
            continue
        escaped = re.escape(alias)
        target_patterns = (
            rf"\b(?:open|launch|start|use|control)\s+(?:the\s+)?"
            rf"{escaped}(?![\w.])",
            rf"\b(?:into|in|to|within|on)\s+(?:the\s+)?"
            rf"{escaped}(?![\w.])",
            rf"(?:เปิด|ใช้|ควบคุม)\s*{escaped}(?![\w.])",
            rf"(?:ใน|ที่)\s*{escaped}(?![\w.])",
        )
        if any(re.search(pattern, folded, re.I) for pattern in target_patterns):
            return application
    return None


def extract_local_activation(
    message: object,
    *,
    configured_apps: object = "",
) -> LocalActivation:
    """Extract at most one exact typing payload and replace it by a local ref."""

    original = _CONTROL_RE.sub(" ", str(message or ""))[:8_000]
    match = _TYPE_CUE_RE.search(original)
    payloads: dict[str, str] = {}
    sanitized = original
    application: RequestedApplication | None
    if match is not None:
        quoted_span = _quoted_payload_span(original, match.end())
        if quoted_span is not None:
            payload_start, payload_end = quoted_span
            outside_payload = (
                original[:payload_start]
                + "payload:user_text_1"
                + original[payload_end:]
            )
            application = requested_application(
                outside_payload,
                configured_apps=configured_apps,
            )
        else:
            application = _requested_application_for_typing(
                original,
                match.start(),
                match.end(),
                configured_apps=configured_apps,
            )
            payload_start, payload_end = _payload_span(
                original,
                match.end(),
                application,
            )
        if payload_end > payload_start:
            exact = original[payload_start:payload_end]
            if exact:
                payloads["user_text_1"] = exact
                sanitized = (
                    original[:payload_start]
                    + "payload:user_text_1"
                    + original[payload_end:]
                )
    else:
        application = requested_application(
            original,
            configured_apps=configured_apps,
        )
    sanitized = " ".join(sanitized.split())[:4_000]
    return LocalActivation(
        sanitized_request=sanitized,
        payloads=payloads,
        requested_app=application,
        typing_authorized=bool(payloads),
        # Submit words inside the exact local payload are data, not authority.
        submit_authorized=bool(_SUBMIT_ACTION_RE.search(sanitized)),
    )


def is_explicit_computer_use_request(
    message: object,
    activation: LocalActivation | None = None,
) -> bool:
    """Require a named app or an explicit Computer Use phrase for typed tasks."""

    parsed = activation or extract_local_activation(message)
    return bool(
        parsed.requested_app is not None
        or _EXPLICIT_COMPUTER_USE_RE.search(str(message or ""))
    )


def _payload_span(
    message: str,
    start: int,
    application: RequestedApplication | None,
) -> tuple[int, int]:
    while start < len(message) and message[start].isspace():
        start += 1
    if start >= len(message):
        return start, start

    quote = message[start] if message[start] in {'"', "'", "“", "‘"} else ""
    if quote:
        closing = {'"': '"', "'": "'", "“": "”", "‘": "’"}[quote]
        # Use the final closing delimiter so inner/nested quotes never leave a
        # suffix of the exact payload in provider-facing sanitized text.
        end = message.rfind(closing, start + 1)
        if end > start + 1:
            return start + 1, end

    end = len(message)
    submit = _TRAILING_SUBMIT_RE.search(message, start)
    if submit is not None:
        end = submit.start()

    # "type hello into <authorized app>" keeps target syntax out of payload.
    if application is not None:
        aliases = _aliases_for_application(application)
        alias_pattern = "|".join(
            re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)
        )
        suffix = re.search(
            r"\s+(?:into|in|to|ใน|ที่)\s+(?:the\s+)?"
            rf"(?:{alias_pattern})\s*$",
            message[start:end],
            re.I,
        )
        if suffix is not None:
            end = start + suffix.start()

    while end > start and message[end - 1].isspace():
        end -= 1
    return start, end


def _quoted_payload_span(message: str, start: int) -> tuple[int, int] | None:
    while start < len(message) and message[start].isspace():
        start += 1
    if start >= len(message) or message[start] not in {'"', "'", "“", "‘"}:
        return None
    closing = {'"': '"', "'": "'", "“": "”", "‘": "’"}[message[start]]
    end = message.rfind(closing, start + 1)
    return (start + 1, end) if end > start + 1 else None


def _application_candidates(
    configured_apps: object,
) -> list[tuple[str, RequestedApplication]]:
    candidates: list[tuple[str, RequestedApplication]] = list(_BUILTIN_APPS.items())
    for application in parse_configured_apps(configured_apps):
        basename = ntpath.basename(application.process_name).casefold()
        candidates.append((basename, application))
        candidates.append((basename.removesuffix(".exe"), application))
    candidates.sort(key=lambda item: len(item[0]), reverse=True)
    return candidates


def _aliases_for_application(application: RequestedApplication) -> frozenset[str]:
    basename = ntpath.basename(application.process_name).casefold()
    aliases = {basename, basename.removesuffix(".exe")}
    aliases.update(
        alias.casefold()
        for alias, candidate in _BUILTIN_APPS.items()
        if candidate.process_name.casefold() == application.process_name.casefold()
    )
    return frozenset(alias for alias in aliases if alias)


def _requested_application_for_typing(
    message: str,
    cue_start: int,
    payload_start: int,
    *,
    configured_apps: object,
) -> RequestedApplication | None:
    # An app named before the type cue is outside the payload by construction.
    prefix_match = requested_application(
        message[:cue_start],
        configured_apps=configured_apps,
    )
    if prefix_match is not None:
        return prefix_match

    tail = message[payload_start:]
    trailing_submit = _TRAILING_SUBMIT_RE.search(tail)
    if trailing_submit is not None:
        tail = tail[:trailing_submit.start()]
    for alias, application in _application_candidates(configured_apps):
        if re.search(
            rf"\s+(?:into|in|to|ใน|ที่)\s+(?:the\s+)?"
            rf"{re.escape(alias)}\s*$",
            tail,
            re.I,
        ):
            return application
    return None


__all__ = [
    "LocalActivation",
    "RequestedApplication",
    "extract_local_activation",
    "is_explicit_computer_use_request",
    "parse_configured_apps",
    "requested_application",
]
