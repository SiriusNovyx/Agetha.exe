"""Safe, dependency-injected Unicode text entry.

The command layer remains responsible for feature gates, Command Guard, and UI
confirmation.  This module owns only the platform operation.  In particular it
never presses Enter/Return/Tab, never logs the supplied text, and never treats a
captured target as stable without revalidating it immediately before input.

The public :class:`UnicodeTypingEngine` accepts injected platform callbacks so
tests and callers do not need to synthesize real keyboard or clipboard events.
``default_dependencies()`` supplies conservative Windows and Linux adapters.
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Collection, Iterator

from agetha.platform.self_identity import (
    is_self_process_identity,
    process_id_from_stable_target_id,
)
from agetha.platform.screen_monitoring import redact_sensitive_text
from agetha.utils import logger


class TypingMode(str, Enum):
    AUTO = "auto"
    UNICODE = "unicode"
    PASTE = "paste"
    PREVIEW = "preview"
    PACED = "paced"


class TypingSpeed(str, Enum):
    INSTANT = "instant"
    FAST = "fast"
    NORMAL = "normal"
    SLOW = "slow"


@dataclass(frozen=True)
class UnicodeTypeResult:
    """Privacy-safe outcome of one text-entry request.

    ``characters_sent`` counts Python string characters that were completely
    submitted.  A clipboard-only fallback therefore reports zero even though
    the requested text was copied successfully.
    """

    success: bool
    method: str
    characters_requested: int
    characters_sent: int
    target_identity: str | None
    clipboard_restored: bool | None
    message: str


@dataclass(frozen=True)
class TypingTarget:
    """Stable window identity plus limited display/classification metadata."""

    stable_id: str
    title: str = ""
    process_name: str = ""
    window_handle: int | None = None
    is_own_window: bool = False

    @property
    def safe_identity(self) -> str:
        process = Path(self.process_name).name.strip()
        if process:
            return process[:64]
        return "target-window" if self.stable_id else "unknown-target"


@dataclass(frozen=True)
class TypingPreview:
    target_application: str
    target_window_title: str
    character_count: int
    line_count: int
    method: str
    clipboard_fallback_may_be_used: bool
    reversible: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ClipboardSnapshot:
    """A readable clipboard text snapshot.

    ``available=False`` means the previous value could not be captured, so it
    must never be guessed or blindly restored.
    """

    available: bool
    value: str | None = None


@dataclass(frozen=True)
class NativeSendResult:
    success: bool
    characters_sent: int
    utf16_units_sent: int


TargetProvider = Callable[[], TypingTarget | None]
NativeUnicodeSender = Callable[[str], NativeSendResult]
ClipboardReader = Callable[[], ClipboardSnapshot | str | None]
ClipboardWriter = Callable[[str], bool]
PasteSender = Callable[[], bool]
StopPredicate = Callable[[], bool]
TargetActivator = Callable[[TypingTarget], bool]


def _never() -> bool:
    return False


def _always() -> bool:
    return True


def _same_target(left: TypingTarget, right: TypingTarget) -> bool:
    return bool(left.stable_id) and left.stable_id == right.stable_id


@dataclass
class UnicodeTypingDependencies:
    """All effectful operations used by :class:`UnicodeTypingEngine`."""

    platform_name: str
    session_type: str
    get_focused_target: TargetProvider
    send_native_unicode: NativeUnicodeSender | None = None
    read_clipboard: ClipboardReader | None = None
    write_clipboard: ClipboardWriter | None = None
    send_paste_shortcut: PasteSender | None = None
    activate_target: TargetActivator | None = None
    targets_match: Callable[[TypingTarget, TypingTarget], bool] = _same_target
    effect_authorized: StopPredicate = _always
    sleep: Callable[[float], None] = time.sleep
    cancel_requested: StopPredicate = _never
    shutdown_requested: StopPredicate = _never


_SPEED_DELAYS = {
    TypingSpeed.INSTANT: 0.0,
    TypingSpeed.FAST: 0.005,
    TypingSpeed.NORMAL: 0.02,
    TypingSpeed.SLOW: 0.06,
}

_TERMINAL_PROCESSES = frozenset(
    {
        "cmd.exe",
        "conhost.exe",
        "powershell.exe",
        "pwsh.exe",
        "windowsterminal.exe",
        "wt.exe",
        "terminal",
        "gnome-terminal",
        "konsole",
        "xterm",
        "alacritty",
        "kitty",
        "bash",
        "zsh",
        "fish",
    }
)
_TERMINAL_TITLE_RE = re.compile(
    r"(?i)(?:\b(?:powershell|command prompt|terminal|console|bash|zsh|shell)\b)"
)
_RESTRICTED_TARGET_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:1password|bitwarden|keepass|dashlane|lastpass|password manager)\b"
    r"|\b(?:user account control|windows security|credential|secure desktop)\b"
    r"|\b(?:online banking|internet banking|banking app)\b"
    r"|\b(?:antivirus|endpoint security|security center)\b"
    r")"
)
_ADMIN_TITLE_RE = re.compile(r"(?i)(?:\badministrator\b|\belevated\b|\broot@)")
_SHELL_LIKE_RE = re.compile(
    r"(?im)^\s*(?:sudo\s+|rm\s+-|del\s+/|format\s+|shutdown\b|reboot\b|"
    r"git\s+(?:reset|clean|push\s+--force)\b|(?:cmd|powershell|pwsh|bash|sh)\s+[-/])"
)
_SENSITIVE_TEXT_RE = re.compile(
    r"(?is)(?:"
    r"-----BEGIN\s+[^\r\n-]*PRIVATE KEY-----"
    r"|\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"
    r"|\b(?:sk-[A-Za-z0-9_-]{12,}|gsk_[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{12,})\b"
    r"|\b(?:password|passwd|api[_-]?key|client[_-]?secret|auth[_-]?token)\s*[:=]"
    r")"
)


def parse_mode(value: str | TypingMode) -> TypingMode:
    if isinstance(value, TypingMode):
        return value
    return TypingMode(str(value).strip().lower())


def parse_speed(value: str | TypingSpeed) -> TypingSpeed:
    if isinstance(value, TypingSpeed):
        return value
    return TypingSpeed(str(value).strip().lower())


def utf16_code_units(text: str) -> tuple[int, ...]:
    """Return exact UTF-16LE code units, including surrogate pairs.

    ``surrogatepass`` also keeps an explicitly supplied surrogate component
    intact; no normalization or replacement is performed.
    """

    encoded = text.encode("utf-16-le", errors="surrogatepass")
    return tuple(
        encoded[index] | (encoded[index + 1] << 8)
        for index in range(0, len(encoded), 2)
    )


def _is_combining_or_extender(character: str) -> bool:
    value = ord(character)
    return (
        unicodedata.combining(character) != 0
        or unicodedata.category(character) in {"Mn", "Mc", "Me"}
        or 0xFE00 <= value <= 0xFE0F
        or 0xE0100 <= value <= 0xE01EF
        or 0x1F3FB <= value <= 0x1F3FF
    )


def _is_regional_indicator(character: str) -> bool:
    return 0x1F1E6 <= ord(character) <= 0x1F1FF


def _is_high_surrogate(character: str) -> bool:
    return 0xD800 <= ord(character) <= 0xDBFF


def _is_low_surrogate(character: str) -> bool:
    return 0xDC00 <= ord(character) <= 0xDFFF


def iter_safe_clusters(text: str) -> Iterator[str]:
    """Yield conservative grapheme-like clusters using only the stdlib.

    This is deliberately not advertised as full Unicode grapheme breaking.  It
    covers the boundaries relevant to paced input and keeps uncertain sequences
    together rather than splitting aggressively.
    """

    index = 0
    length = len(text)
    while index < length:
        start = index

        if (
            _is_high_surrogate(text[index])
            and index + 1 < length
            and _is_low_surrogate(text[index + 1])
        ):
            index += 2
        else:
            first = text[index]
            index += 1
            if _is_regional_indicator(first) and index < length and _is_regional_indicator(text[index]):
                index += 1

        while index < length and _is_combining_or_extender(text[index]):
            index += 1

        while index < length and ord(text[index]) == 0x200D:
            index += 1
            if index >= length:
                break
            if (
                _is_high_surrogate(text[index])
                and index + 1 < length
                and _is_low_surrogate(text[index + 1])
            ):
                index += 2
            else:
                index += 1
            while index < length and _is_combining_or_extender(text[index]):
                index += 1

        yield text[start:index]


def iter_safe_chunks(text: str, max_utf16_units: int = 16) -> Iterator[str]:
    """Pack safe clusters into chunks without changing the input string."""

    limit = max(1, min(4096, int(max_utf16_units)))
    pending: list[str] = []
    pending_units = 0
    for cluster in iter_safe_clusters(text):
        cluster_units = len(utf16_code_units(cluster))
        if pending and pending_units + cluster_units > limit:
            yield "".join(pending)
            pending = []
            pending_units = 0
        pending.append(cluster)
        pending_units += cluster_units
        if pending_units >= limit:
            yield "".join(pending)
            pending = []
            pending_units = 0
    if pending:
        yield "".join(pending)


def is_terminal_target(target: TypingTarget) -> bool:
    process = Path(target.process_name).name.casefold()
    return process in _TERMINAL_PROCESSES or bool(_TERMINAL_TITLE_RE.search(target.title))


def is_restricted_target(target: TypingTarget) -> bool:
    label = f"{Path(target.process_name).name} {target.title}"
    return bool(_RESTRICTED_TARGET_RE.search(label)) or (
        is_terminal_target(target) and bool(_ADMIN_TITLE_RE.search(target.title))
    )


def _looks_like_own_window(target: TypingTarget) -> bool:
    if target.is_own_window:
        return True
    if is_self_process_identity(
        process_name=target.process_name,
        process_id=process_id_from_stable_target_id(target.stable_id),
    ):
        return True
    title = target.title.strip().casefold()
    return title in {"agetha", "agetha mod"} or title.startswith("agetha —")


def _safe_title(title: str, limit: int = 80) -> str:
    value = " ".join(str(title or "").split())
    if not value:
        return "Unknown"
    if _SENSITIVE_TEXT_RE.search(value) or redact_sensitive_text(value) != value:
        return "[sensitive title hidden]"
    if len(value) > limit:
        return f"{value[: max(1, limit - 1)]}…"
    return value


def _planned_method(platform_name: str, session_type: str, mode: TypingMode) -> str:
    platform_key = platform_name.casefold()
    session_key = session_type.casefold()
    if mode == TypingMode.PREVIEW:
        return "preview"
    if platform_key == "windows" and mode in {TypingMode.AUTO, TypingMode.UNICODE, TypingMode.PACED}:
        return "windows-sendinput-unicode"
    if platform_key == "linux" and session_key == "wayland":
        return "clipboard-copy-only"
    return "clipboard-paste"


def build_typing_preview(
    text: str,
    target: TypingTarget | None,
    *,
    mode: str | TypingMode = TypingMode.AUTO,
    platform_name: str = "",
    session_type: str = "",
    preview_threshold: int = 300,
) -> TypingPreview:
    parsed_mode = parse_mode(mode)
    reasons: list[str] = []
    if parsed_mode == TypingMode.PREVIEW:
        reasons.append("explicit-preview")
    if len(text) >= max(1, int(preview_threshold)):
        reasons.append("long-text")
    if "\n" in text or "\r" in text:
        reasons.append("multiline-text")
    if _SHELL_LIKE_RE.search(text):
        reasons.append("shell-like-text")
    if _SENSITIVE_TEXT_RE.search(text) or redact_sensitive_text(text) != text:
        reasons.append("potentially-sensitive-text")
    if target is not None:
        if is_terminal_target(target):
            reasons.append("terminal-target")
        if is_restricted_target(target):
            reasons.append("restricted-target")

    return TypingPreview(
        target_application=target.safe_identity if target is not None else "Unknown",
        target_window_title=_safe_title(target.title) if target is not None else "Unknown",
        character_count=len(text),
        line_count=0 if not text else text.count("\n") + 1,
        method=_planned_method(platform_name, session_type, parsed_mode),
        clipboard_fallback_may_be_used=(
            parsed_mode in {TypingMode.AUTO, TypingMode.PASTE, TypingMode.PACED}
        ),
        reversible=False,
        reasons=tuple(dict.fromkeys(reasons)),
    )


_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
_HGLOBAL = wintypes.HANDLE


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    # Include every Win32 INPUT union member so ctypes computes the real ABI
    # size on both 32-bit and 64-bit Python.  SendInput rejects a short cbSize.
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004


def _unicode_events(text: str) -> ctypes.Array[_INPUT]:
    units = utf16_code_units(text)
    events = (_INPUT * (len(units) * 2))()
    for unit_index, unit in enumerate(units):
        down = unit_index * 2
        events[down].type = _INPUT_KEYBOARD
        events[down].ki = _KEYBDINPUT(0, unit, _KEYEVENTF_UNICODE, 0, 0)
        events[down + 1].type = _INPUT_KEYBOARD
        events[down + 1].ki = _KEYBDINPUT(
            0, unit, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP, 0, 0
        )
    return events


def _send_input_batch(events: ctypes.Array[_INPUT]) -> int:
    if sys.platform != "win32":
        return 0
    send_input = ctypes.windll.user32.SendInput
    send_input.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
    send_input.restype = wintypes.UINT
    return int(send_input(len(events), events, ctypes.sizeof(_INPUT)))


def _characters_for_complete_units(text: str, complete_units: int) -> int:
    used = 0
    characters = 0
    for character in text:
        width = len(utf16_code_units(character))
        if used + width > complete_units:
            break
        used += width
        characters += 1
    return characters


def send_windows_unicode(
    text: str,
    *,
    stop_requested: StopPredicate | None = None,
) -> NativeSendResult:
    """Send Unicode through Win32 ``SendInput(KEYEVENTF_UNICODE)``.

    Text is encoded into UTF-16 code units.  Every unit is submitted as a
    key-down/key-up pair with ``wVk=0``; no virtual Enter, Return, or Tab key is
    generated.  Batches use safe sequence boundaries.
    """

    if sys.platform != "win32" or not text:
        return NativeSendResult(False, 0, 0)

    total_characters = 0
    total_units = 0
    try:
        for chunk in iter_safe_chunks(text, max_utf16_units=128):
            if stop_requested is not None:
                try:
                    if stop_requested():
                        return NativeSendResult(False, total_characters, total_units)
                except Exception as exc:
                    logger.debug(
                        "Windows Unicode lifecycle check failed: %s",
                        type(exc).__name__,
                    )
                    return NativeSendResult(False, total_characters, total_units)
            events = _unicode_events(chunk)
            inserted = _send_input_batch(events)
            complete_units = min(len(utf16_code_units(chunk)), inserted // 2)
            complete_characters = _characters_for_complete_units(chunk, complete_units)
            total_characters += complete_characters
            total_units += complete_units
            if inserted != len(events):
                return NativeSendResult(False, total_characters, total_units)
    except (AttributeError, OSError, TypeError, ValueError):
        return NativeSendResult(False, total_characters, total_units)
    return NativeSendResult(True, len(text), len(utf16_code_units(text)))


class UnicodeTypingEngine:
    """Perform one conservative Unicode text-entry operation."""

    def __init__(
        self,
        dependencies: UnicodeTypingDependencies,
        *,
        own_target_ids: Collection[str] = (),
        own_window_handles: Collection[int] = (),
        preview_threshold: int = 300,
        paced_chunk_utf16_units: int = 12,
        paced_delay_ms: int = 20,
        clipboard_settle_seconds: float = 0.08,
    ) -> None:
        self._dependencies = dependencies
        self._own_target_ids = frozenset(str(item) for item in own_target_ids)
        self._own_window_handles = frozenset(int(item) for item in own_window_handles)
        self._preview_threshold = max(1, min(100_000, int(preview_threshold)))
        self._paced_chunk_units = max(1, min(512, int(paced_chunk_utf16_units)))
        self._paced_delay = max(0.0, min(0.5, int(paced_delay_ms) / 1000.0))
        self._clipboard_settle = max(0.0, min(0.5, float(clipboard_settle_seconds)))

    def _result(
        self,
        *,
        success: bool,
        method: str,
        text: str,
        sent: int,
        target: TypingTarget | None,
        restored: bool | None,
        message: str,
    ) -> UnicodeTypeResult:
        return UnicodeTypeResult(
            success=success,
            method=method,
            characters_requested=len(text),
            characters_sent=max(0, min(len(text), int(sent))),
            target_identity=target.safe_identity if target is not None else None,
            clipboard_restored=restored,
            message=message,
        )

    def _stop_message(self) -> str | None:
        try:
            if self._dependencies.shutdown_requested():
                return "Text entry stopped because Agetha is shutting down."
        except Exception as exc:
            logger.debug("Unicode typing shutdown check failed: %s", type(exc).__name__)
            return "Text entry stopped because shutdown state is unavailable."
        try:
            if self._dependencies.cancel_requested():
                return "Text entry was cancelled."
        except Exception as exc:
            logger.debug("Unicode typing cancellation check failed: %s", type(exc).__name__)
            return "Text entry stopped because cancellation state is unavailable."
        return None

    def _current_target(self) -> TypingTarget | None:
        try:
            return self._dependencies.get_focused_target()
        except Exception as exc:
            logger.debug("Unicode typing focus lookup failed: %s", type(exc).__name__)
            return None

    def _is_own_target(self, target: TypingTarget) -> bool:
        return (
            _looks_like_own_window(target)
            or target.stable_id in self._own_target_ids
            or (
                target.window_handle is not None
                and target.window_handle in self._own_window_handles
            )
        )

    def _target_is_current(self, expected: TypingTarget) -> bool:
        try:
            if not self._dependencies.effect_authorized():
                return False
        except Exception as exc:
            logger.debug(
                "Unicode typing effect authorization failed: %s",
                type(exc).__name__,
            )
            return False
        current = self._current_target()
        if current is None:
            return False
        try:
            return bool(self._dependencies.targets_match(expected, current))
        except Exception as exc:
            logger.debug("Unicode typing target comparison failed: %s", type(exc).__name__)
            return False

    def _activate_and_validate_intended_target(self, target: TypingTarget) -> bool:
        """Activate one pre-confirmation target, then verify its stable identity.

        Confirmation dialogs can temporarily focus Agetha.  Callers may capture
        the intended target *before* showing Command Guard and pass it to
        :meth:`type_text`.  Only that explicitly supplied target is eligible for
        activation; later focus changes always abort rather than fighting the
        user for focus.
        """

        if self._target_is_current(target):
            return True
        activator = self._dependencies.activate_target
        if activator is None:
            return False
        try:
            if not activator(target):
                return False
        except Exception as exc:
            logger.debug("Unicode typing target activation failed: %s", type(exc).__name__)
            return False
        return self._target_is_current(target)

    def type_text(
        self,
        text: str,
        *,
        mode: str | TypingMode = TypingMode.AUTO,
        speed: str | TypingSpeed = TypingSpeed.NORMAL,
        restore_clipboard: bool = True,
        preview_approved: bool = False,
        allow_restricted_target: bool = False,
        intended_target: TypingTarget | None = None,
    ) -> UnicodeTypeResult:
        """Enter ``text`` exactly, or return an honest no-input result."""

        requested = text if isinstance(text, str) else str(text)
        try:
            parsed_mode = parse_mode(mode)
        except (TypeError, ValueError):
            return self._result(
                success=False,
                method="validation",
                text=requested,
                sent=0,
                target=None,
                restored=None,
                message="Unknown Unicode typing mode.",
            )
        try:
            parsed_speed = parse_speed(speed)
        except (TypeError, ValueError):
            return self._result(
                success=False,
                method="validation",
                text=requested,
                sent=0,
                target=None,
                restored=None,
                message="Unknown Unicode typing speed.",
            )
        if not requested:
            return self._result(
                success=False,
                method="validation",
                text=requested,
                sent=0,
                target=None,
                restored=None,
                message="No text was supplied.",
            )

        stopped = self._stop_message()
        if stopped:
            return self._result(
                success=False,
                method="stopped",
                text=requested,
                sent=0,
                target=None,
                restored=None,
                message=stopped,
            )

        # ``intended_target`` must be captured before any owned confirmation
        # dialog.  Without it, current focus at call time remains authoritative.
        target = intended_target if intended_target is not None else self._current_target()
        preview = build_typing_preview(
            requested,
            target,
            mode=parsed_mode,
            platform_name=self._dependencies.platform_name,
            session_type=self._dependencies.session_type,
            preview_threshold=self._preview_threshold,
        )
        if parsed_mode == TypingMode.PREVIEW:
            return self._result(
                success=True,
                method="preview",
                text=requested,
                sent=0,
                target=target,
                restored=None,
                message="Typing preview is ready; no text was entered.",
            )

        if target is not None and self._is_own_target(target):
            return self._result(
                success=False,
                method="target-rejected",
                text=requested,
                sent=0,
                target=target,
                restored=None,
                message="Agetha's own window was rejected as the typing target.",
            )

        if "restricted-target" in preview.reasons and not allow_restricted_target:
            return self._result(
                success=False,
                method="target-rejected",
                text=requested,
                sent=0,
                target=target,
                restored=None,
                message="This target requires a stronger explicit confirmation.",
            )

        reasons_requiring_preview = set(preview.reasons) - {"restricted-target"}
        if reasons_requiring_preview and not preview_approved:
            return self._result(
                success=False,
                method="preview-required",
                text=requested,
                sent=0,
                target=target,
                restored=None,
                message="Review the typing preview before continuing.",
            )

        platform_key = self._dependencies.platform_name.strip().casefold()
        session_key = self._dependencies.session_type.strip().casefold()
        if target is None:
            if parsed_mode == TypingMode.UNICODE:
                return self._result(
                    success=False,
                    method="target-unavailable",
                    text=requested,
                    sent=0,
                    target=None,
                    restored=None,
                    message="No stable target window could be identified.",
                )
            return self._copy_only(requested, target, reason="target-unavailable")

        if intended_target is not None:
            try:
                authorized = bool(self._dependencies.effect_authorized())
            except Exception as exc:
                logger.debug(
                    "Unicode typing effect authorization failed: %s",
                    type(exc).__name__,
                )
                authorized = False
            if not authorized:
                return self._result(
                    success=False,
                    method="target-unavailable",
                    text=requested,
                    sent=0,
                    target=target,
                    restored=None,
                    message="The approved target is no longer authorized.",
                )
        if intended_target is not None and not self._activate_and_validate_intended_target(target):
            return self._result(
                success=False,
                method="target-unavailable",
                text=requested,
                sent=0,
                target=target,
                restored=None,
                message="The approved target could not be safely restored and revalidated.",
            )

        if platform_key == "linux" and session_key == "wayland":
            return self._copy_only(requested, target, reason="wayland")

        if parsed_mode == TypingMode.PASTE:
            return self._paste(
                requested,
                target,
                restore_clipboard=bool(restore_clipboard),
                chunks=(requested,),
                delay=0.0,
            )

        if parsed_mode == TypingMode.PACED:
            chunks = tuple(iter_safe_chunks(requested, self._paced_chunk_units))
            delay = (
                self._paced_delay
                if parsed_speed is TypingSpeed.NORMAL
                else max(0.0, min(0.25, _SPEED_DELAYS[parsed_speed]))
            )
            if platform_key == "windows" and self._dependencies.send_native_unicode is not None:
                return self._native(requested, target, chunks=chunks, delay=delay, allow_fallback=False)
            return self._paste(
                requested,
                target,
                restore_clipboard=bool(restore_clipboard),
                chunks=chunks,
                delay=delay,
            )

        if parsed_mode == TypingMode.UNICODE:
            if platform_key != "windows" or self._dependencies.send_native_unicode is None:
                return self._result(
                    success=False,
                    method="unicode-unavailable",
                    text=requested,
                    sent=0,
                    target=target,
                    restored=None,
                    message="Native Unicode input is unavailable on this platform.",
                )
            return self._native(
                requested,
                target,
                chunks=(requested,),
                delay=0.0,
                allow_fallback=False,
            )

        # AUTO: native Unicode first on Windows; otherwise use guarded paste.
        if platform_key == "windows" and self._dependencies.send_native_unicode is not None:
            return self._native(
                requested,
                target,
                chunks=(requested,),
                delay=0.0,
                allow_fallback=True,
                restore_clipboard=bool(restore_clipboard),
            )
        return self._paste(
            requested,
            target,
            restore_clipboard=bool(restore_clipboard),
            chunks=(requested,),
            delay=0.0,
        )

    def _native(
        self,
        text: str,
        target: TypingTarget,
        *,
        chunks: tuple[str, ...],
        delay: float,
        allow_fallback: bool,
        restore_clipboard: bool = True,
    ) -> UnicodeTypeResult:
        sender = self._dependencies.send_native_unicode
        if sender is None:
            if allow_fallback:
                return self._paste(
                    text,
                    target,
                    restore_clipboard=restore_clipboard,
                    chunks=(text,),
                    delay=0.0,
                )
            return self._result(
                success=False,
                method="unicode-unavailable",
                text=text,
                sent=0,
                target=target,
                restored=None,
                message="Native Unicode input is unavailable.",
            )

        sent = 0
        for index, chunk in enumerate(chunks):
            stopped = self._stop_message()
            if stopped:
                return self._result(
                    success=False,
                    method="windows-sendinput-unicode",
                    text=text,
                    sent=sent,
                    target=target,
                    restored=None,
                    message=stopped,
                )
            if not self._target_is_current(target):
                return self._result(
                    success=False,
                    method="windows-sendinput-unicode",
                    text=text,
                    sent=sent,
                    target=target,
                    restored=None,
                    message="The focused window changed, so text entry stopped.",
                )
            try:
                outcome = sender(chunk)
            except Exception as exc:
                logger.debug("Unicode typing native send failed: %s", type(exc).__name__)
                outcome = NativeSendResult(False, 0, 0)
            chunk_sent = max(0, min(len(chunk), int(outcome.characters_sent)))
            sent += chunk_sent
            if not outcome.success or chunk_sent != len(chunk):
                # Falling back after a partial native send would duplicate text.
                if allow_fallback and sent == 0:
                    fallback = self._paste(
                        text,
                        target,
                        restore_clipboard=restore_clipboard,
                        chunks=(text,),
                        delay=0.0,
                    )
                    if fallback.success:
                        return UnicodeTypeResult(
                            success=True,
                            method="clipboard-paste-fallback",
                            characters_requested=fallback.characters_requested,
                            characters_sent=fallback.characters_sent,
                            target_identity=fallback.target_identity,
                            clipboard_restored=fallback.clipboard_restored,
                            message="Direct Unicode input was unavailable; clipboard paste was used.",
                        )
                    return fallback
                return self._result(
                    success=False,
                    method="windows-sendinput-unicode",
                    text=text,
                    sent=sent,
                    target=target,
                    restored=None,
                    message="Native Unicode input stopped before the request completed.",
                )
            if delay > 0.0 and index + 1 < len(chunks):
                self._dependencies.sleep(delay)

        return self._result(
            success=True,
            method="windows-sendinput-unicode",
            text=text,
            sent=sent,
            target=target,
            restored=None,
            message="Unicode text entry completed.",
        )

    def _read_clipboard(self) -> ClipboardSnapshot:
        reader = self._dependencies.read_clipboard
        if reader is None:
            return ClipboardSnapshot(False, None)
        try:
            result = reader()
        except Exception as exc:
            logger.debug("Unicode typing clipboard read failed: %s", type(exc).__name__)
            return ClipboardSnapshot(False, None)
        if isinstance(result, ClipboardSnapshot):
            return result
        if isinstance(result, str):
            return ClipboardSnapshot(True, result)
        return ClipboardSnapshot(False, None)

    def _write_clipboard(self, value: str) -> bool:
        writer = self._dependencies.write_clipboard
        if writer is None:
            return False
        try:
            return bool(writer(value))
        except Exception as exc:
            logger.debug("Unicode typing clipboard write failed: %s", type(exc).__name__)
            return False

    def _restore_if_unchanged(
        self,
        previous: ClipboardSnapshot,
        placed: str,
        *,
        restore_requested: bool,
    ) -> bool | None:
        if not restore_requested:
            return None
        current = self._read_clipboard()
        if not current.available or current.value != placed:
            return False
        if not previous.available or previous.value is None:
            return False
        if previous.value == placed:
            return True
        return self._write_clipboard(previous.value)

    def _paste(
        self,
        text: str,
        target: TypingTarget,
        *,
        restore_clipboard: bool,
        chunks: tuple[str, ...],
        delay: float,
    ) -> UnicodeTypeResult:
        if self._dependencies.write_clipboard is None or self._dependencies.send_paste_shortcut is None:
            return self._copy_only(text, target, reason="paste-unavailable")

        previous = self._read_clipboard()
        placed: str | None = None
        sent = 0
        user_changed_clipboard = False

        for index, chunk in enumerate(chunks):
            stopped = self._stop_message()
            if stopped:
                restored = (
                    self._restore_if_unchanged(previous, placed, restore_requested=restore_clipboard)
                    if placed is not None and not user_changed_clipboard
                    else (False if restore_clipboard else None)
                )
                return self._result(
                    success=False,
                    method="clipboard-paste",
                    text=text,
                    sent=sent,
                    target=target,
                    restored=restored,
                    message=stopped,
                )

            if placed is not None:
                current = self._read_clipboard()
                if not current.available or current.value != placed:
                    user_changed_clipboard = True
                    return self._result(
                        success=False,
                        method="clipboard-paste",
                        text=text,
                        sent=sent,
                        target=target,
                        restored=False if restore_clipboard else None,
                        message="The clipboard changed, so paced text entry stopped.",
                    )

            if not self._target_is_current(target):
                restored = (
                    self._restore_if_unchanged(previous, placed, restore_requested=restore_clipboard)
                    if placed is not None
                    else None
                )
                return self._result(
                    success=False,
                    method="clipboard-paste",
                    text=text,
                    sent=sent,
                    target=target,
                    restored=restored,
                    message="The focused window changed, so text entry stopped.",
                )

            if not self._write_clipboard(chunk):
                restored = (
                    self._restore_if_unchanged(previous, placed, restore_requested=restore_clipboard)
                    if placed is not None
                    else None
                )
                return self._result(
                    success=False,
                    method="clipboard-paste",
                    text=text,
                    sent=sent,
                    target=target,
                    restored=restored,
                    message="Clipboard text entry is unavailable.",
                )
            placed = chunk

            # Revalidate after setting the clipboard and immediately before the
            # only synthesized shortcut used here: normal paste.
            if not self._target_is_current(target):
                restored = self._restore_if_unchanged(
                    previous, placed, restore_requested=restore_clipboard
                )
                return self._result(
                    success=False,
                    method="clipboard-paste",
                    text=text,
                    sent=sent,
                    target=target,
                    restored=restored,
                    message="The focused window changed, so text entry stopped.",
                )
            try:
                pasted = bool(self._dependencies.send_paste_shortcut())
            except Exception as exc:
                logger.debug("Unicode typing paste shortcut failed: %s", type(exc).__name__)
                pasted = False
            if not pasted:
                restored = self._restore_if_unchanged(
                    previous, placed, restore_requested=restore_clipboard
                )
                return self._result(
                    success=False,
                    method="clipboard-paste",
                    text=text,
                    sent=sent,
                    target=target,
                    restored=restored,
                    message="The target did not accept the paste shortcut.",
                )
            sent += len(chunk)
            if index + 1 < len(chunks) and delay > 0.0:
                self._dependencies.sleep(delay)

        if self._clipboard_settle > 0.0:
            self._dependencies.sleep(self._clipboard_settle)
        restored = self._restore_if_unchanged(
            previous,
            placed or "",
            restore_requested=restore_clipboard,
        )
        if not restore_clipboard and len(chunks) > 1 and placed is not None:
            current = self._read_clipboard()
            if current.available and current.value == placed:
                self._write_clipboard(text)

        return self._result(
            success=True,
            method="clipboard-paste",
            text=text,
            sent=sent,
            target=target,
            restored=restored,
            message="Unicode text was pasted into the validated target.",
        )

    def _copy_only(
        self,
        text: str,
        target: TypingTarget | None,
        *,
        reason: str,
    ) -> UnicodeTypeResult:
        copied = self._write_clipboard(text)
        if not copied:
            return self._result(
                success=False,
                method="clipboard-unavailable",
                text=text,
                sent=0,
                target=target,
                restored=None,
                message="Automatic text entry and clipboard copy are unavailable.",
            )
        if reason == "wayland":
            message = "Wayland blocked automatic typing; the text was copied for manual paste."
        elif reason == "target-unavailable":
            message = "No stable target was found; the text was copied for manual paste."
        else:
            message = "Automatic paste is unavailable; the text was copied for manual paste."
        return self._result(
            success=False,
            method="clipboard-copy-only",
            text=text,
            sent=0,
            target=target,
            restored=False,
            message=message,
        )


def _normalize_platform_name(value: str) -> str:
    key = value.strip().casefold()
    if key.startswith("win"):
        return "windows"
    if key.startswith("linux"):
        return "linux"
    if key in {"darwin", "mac", "macos"}:
        return "macos"
    return key or "unknown"


def _linux_session_type() -> str:
    explicit = os.environ.get("XDG_SESSION_TYPE", "").strip().casefold()
    if explicit in {"x11", "wayland"}:
        return explicit
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def _windows_process_name(pid: int) -> str:
    if sys.platform != "win32" or not pid:
        return ""
    process_query_limited_information = 0x1000
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(
        process_query_limited_information, False, int(pid)
    )
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
        return Path(buffer.value).name if ok else ""
    except (AttributeError, OSError, ValueError):
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _windows_target() -> TypingTarget | None:
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowThreadProcessId.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        )
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        hwnd = int(user32.GetForegroundWindow())
        if not hwnd:
            return None
        length = int(user32.GetWindowTextLengthW(hwnd))
        buffer = ctypes.create_unicode_buffer(max(1, length + 1))
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return TypingTarget(
            stable_id=f"win:{hwnd}:{int(pid.value)}",
            title=buffer.value,
            process_name=_windows_process_name(int(pid.value)),
            window_handle=hwnd,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _windows_read_clipboard() -> ClipboardSnapshot:
    if sys.platform != "win32":
        return ClipboardSnapshot(False, None)
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = (wintypes.UINT,)
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalLock.argtypes = (_HGLOBAL,)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = (_HGLOBAL,)
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    cf_unicode_text = 13
    if not user32.OpenClipboard(0):
        return ClipboardSnapshot(False, None)
    try:
        handle = user32.GetClipboardData(cf_unicode_text)
        if not handle:
            return ClipboardSnapshot(False, None)
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ClipboardSnapshot(False, None)
        try:
            return ClipboardSnapshot(True, ctypes.wstring_at(pointer))
        finally:
            kernel32.GlobalUnlock(handle)
    except (AttributeError, OSError, ValueError):
        return ClipboardSnapshot(False, None)
    finally:
        user32.CloseClipboard()


def _windows_write_clipboard(text: str) -> bool:
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    kernel32.GlobalAlloc.restype = _HGLOBAL
    kernel32.GlobalLock.argtypes = (_HGLOBAL,)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = (_HGLOBAL,)
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = (_HGLOBAL,)
    kernel32.GlobalFree.restype = _HGLOBAL
    cf_unicode_text = 13
    gmem_moveable = 0x0002
    raw = text.encode("utf-16-le", errors="surrogatepass") + b"\x00\x00"
    memory = kernel32.GlobalAlloc(gmem_moveable, len(raw))
    if not memory:
        return False
    transferred = False
    try:
        pointer = kernel32.GlobalLock(memory)
        if not pointer:
            return False
        try:
            ctypes.memmove(pointer, raw, len(raw))
        finally:
            kernel32.GlobalUnlock(memory)
        if not user32.OpenClipboard(0):
            return False
        try:
            if not user32.EmptyClipboard():
                return False
            if not user32.SetClipboardData(cf_unicode_text, memory):
                return False
            transferred = True
            return True
        finally:
            user32.CloseClipboard()
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    finally:
        if not transferred:
            kernel32.GlobalFree(memory)


def _windows_paste_shortcut() -> bool:
    if sys.platform != "win32":
        return False
    vk_control = 0x11
    vk_v = 0x56
    events = (_INPUT * 4)()
    for index, (virtual_key, flags) in enumerate(
        (
            (vk_control, 0),
            (vk_v, 0),
            (vk_v, _KEYEVENTF_KEYUP),
            (vk_control, _KEYEVENTF_KEYUP),
        )
    ):
        events[index].type = _INPUT_KEYBOARD
        events[index].ki = _KEYBDINPUT(virtual_key, 0, flags, 0, 0)
    try:
        return _send_input_batch(events) == len(events)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _windows_activate_target(target: TypingTarget) -> bool:
    if sys.platform != "win32" or target.window_handle is None:
        return False
    try:
        user32 = ctypes.windll.user32
        user32.IsWindow.argtypes = (wintypes.HWND,)
        user32.IsWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        user32.SetForegroundWindow.restype = wintypes.BOOL
        hwnd = wintypes.HWND(target.window_handle)
        if not user32.IsWindow(hwnd):
            return False
        return bool(user32.SetForegroundWindow(hwnd))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _x11_target() -> TypingTarget | None:
    executable = shutil.which("xdotool")
    if not executable:
        return None
    try:
        active = subprocess.run(
            [executable, "getactivewindow"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        window_id = active.stdout.strip()
        if active.returncode != 0 or not window_id.isdigit():
            return None
        title_result = subprocess.run(
            [executable, "getwindowname", window_id],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        pid_result = subprocess.run(
            [executable, "getwindowpid", window_id],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        process_name = ""
        pid_text = pid_result.stdout.strip()
        if pid_result.returncode == 0 and pid_text.isdigit():
            try:
                process_name = Path(f"/proc/{pid_text}/comm").read_text(
                    encoding="utf-8", errors="replace"
                ).strip()
            except (OSError, UnicodeError):
                process_name = ""
        return TypingTarget(
            stable_id=f"x11:{window_id}:{pid_text}",
            title=title_result.stdout.strip() if title_result.returncode == 0 else "",
            process_name=process_name,
            window_handle=int(window_id),
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _command_clipboard_reader(command: list[str]) -> ClipboardSnapshot:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return ClipboardSnapshot(False, None)
    if result.returncode != 0:
        return ClipboardSnapshot(False, None)
    return ClipboardSnapshot(True, result.stdout)


def _command_clipboard_writer(command: list[str], text: str) -> bool:
    try:
        result = subprocess.run(
            command,
            input=text,
            text=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return False


def _linux_clipboard_commands(session_type: str) -> tuple[list[str] | None, list[str] | None]:
    if session_type == "wayland":
        reader = shutil.which("wl-paste")
        writer = shutil.which("wl-copy")
        return ([reader] if reader else None, [writer] if writer else None)
    xclip = shutil.which("xclip")
    if xclip:
        return (
            [xclip, "-selection", "clipboard", "-o"],
            [xclip, "-selection", "clipboard"],
        )
    xsel = shutil.which("xsel")
    if xsel:
        return ([xsel, "--clipboard", "--output"], [xsel, "--clipboard", "--input"])
    return None, None


def _x11_paste_shortcut() -> bool:
    executable = shutil.which("xdotool")
    if not executable:
        return False
    try:
        result = subprocess.run(
            [executable, "key", "--clearmodifiers", "ctrl+v"],
            timeout=3,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _x11_activate_target(target: TypingTarget) -> bool:
    executable = shutil.which("xdotool")
    if not executable or target.window_handle is None:
        return False
    try:
        result = subprocess.run(
            [executable, "windowactivate", "--sync", str(target.window_handle)],
            timeout=3,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _mac_clipboard_dependencies() -> tuple[ClipboardReader | None, ClipboardWriter | None]:
    reader_exe = shutil.which("pbpaste")
    writer_exe = shutil.which("pbcopy")
    reader = (
        (lambda: _command_clipboard_reader([reader_exe])) if reader_exe else None
    )
    writer = (
        (lambda text: _command_clipboard_writer([writer_exe], text)) if writer_exe else None
    )
    return reader, writer


def default_dependencies() -> UnicodeTypingDependencies:
    """Create conservative platform callbacks without optional dependencies."""

    platform_name = _normalize_platform_name(sys.platform)
    if platform_name == "windows":
        dependencies = UnicodeTypingDependencies(
            platform_name="windows",
            session_type="desktop",
            get_focused_target=_windows_target,
            read_clipboard=_windows_read_clipboard,
            write_clipboard=_windows_write_clipboard,
            send_paste_shortcut=_windows_paste_shortcut,
            activate_target=_windows_activate_target,
        )
        dependencies.send_native_unicode = lambda text: send_windows_unicode(
            text,
            stop_requested=lambda: (
                dependencies.cancel_requested()
                or dependencies.shutdown_requested()
                or not dependencies.effect_authorized()
            ),
        )
        return dependencies
    if platform_name == "linux":
        session = _linux_session_type()
        reader_command, writer_command = _linux_clipboard_commands(session)
        reader = (
            (lambda: _command_clipboard_reader(reader_command))
            if reader_command is not None
            else None
        )
        writer = (
            (lambda text: _command_clipboard_writer(writer_command, text))
            if writer_command is not None
            else None
        )
        return UnicodeTypingDependencies(
            platform_name="linux",
            session_type=session,
            get_focused_target=_x11_target if session == "x11" else (lambda: None),
            read_clipboard=reader,
            write_clipboard=writer,
            send_paste_shortcut=_x11_paste_shortcut if session == "x11" else None,
            activate_target=_x11_activate_target if session == "x11" else None,
        )
    if platform_name == "macos":
        reader, writer = _mac_clipboard_dependencies()
        return UnicodeTypingDependencies(
            platform_name="macos",
            session_type="desktop",
            get_focused_target=lambda: None,
            read_clipboard=reader,
            write_clipboard=writer,
        )
    return UnicodeTypingDependencies(
        platform_name=platform_name,
        session_type="unknown",
        get_focused_target=lambda: None,
    )


def type_unicode_text(
    text: str,
    *,
    mode: str | TypingMode = TypingMode.AUTO,
    speed: str | TypingSpeed = TypingSpeed.NORMAL,
    restore_clipboard: bool = True,
    preview_approved: bool = False,
    allow_restricted_target: bool = False,
    intended_target: TypingTarget | None = None,
    dependencies: UnicodeTypingDependencies | None = None,
    own_target_ids: Collection[str] = (),
    own_window_handles: Collection[int] = (),
    preview_threshold: int = 300,
    paced_delay_ms: int = 20,
) -> UnicodeTypeResult:
    """Convenience wrapper around :class:`UnicodeTypingEngine`."""

    engine = UnicodeTypingEngine(
        dependencies or default_dependencies(),
        own_target_ids=own_target_ids,
        own_window_handles=own_window_handles,
        preview_threshold=preview_threshold,
        paced_delay_ms=paced_delay_ms,
    )
    return engine.type_text(
        text,
        mode=mode,
        speed=speed,
        restore_clipboard=restore_clipboard,
        preview_approved=preview_approved,
        allow_restricted_target=allow_restricted_target,
        intended_target=intended_target,
    )


def capture_intended_target(
    dependencies: UnicodeTypingDependencies | None = None,
) -> TypingTarget | None:
    """Capture a target before an owned guard/preview dialog takes focus."""

    selected = dependencies or default_dependencies()
    try:
        return selected.get_focused_target()
    except Exception as exc:
        logger.debug("Unicode typing target capture failed: %s", type(exc).__name__)
        return None


# An explicit alias keeps call sites readable while avoiding the old
# ``system_commands.type_text`` implementation inside this platform module.
type_text = type_unicode_text


__all__ = [
    "ClipboardSnapshot",
    "NativeSendResult",
    "TypingMode",
    "TypingPreview",
    "TypingSpeed",
    "TypingTarget",
    "UnicodeTypeResult",
    "UnicodeTypingDependencies",
    "UnicodeTypingEngine",
    "build_typing_preview",
    "capture_intended_target",
    "default_dependencies",
    "is_restricted_target",
    "is_terminal_target",
    "iter_safe_chunks",
    "iter_safe_clusters",
    "parse_mode",
    "parse_speed",
    "send_windows_unicode",
    "type_text",
    "type_unicode_text",
    "utf16_code_units",
]
