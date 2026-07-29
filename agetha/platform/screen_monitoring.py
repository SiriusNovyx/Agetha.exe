"""Lightweight state and image helpers for local Tesseract monitoring."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from PIL import Image, ImageFilter, ImageOps, ImageStat


@dataclass(frozen=True)
class CapturedFrame:
    image: Image.Image
    left: int
    top: int
    title: str
    hwnd: int | None
    scope: str
    process_name: str = ""
    process_id: int | None = None

    @property
    def key(self) -> tuple[object, ...]:
        if self.hwnd is not None:
            return ("hwnd", int(self.hwnd), self.process_id, self.scope)
        return ("fallback", self.scope, self.title.strip().casefold()[:120])


@dataclass(frozen=True)
class ProcessedOCRImage:
    image: Image.Image
    scale_x: float
    scale_y: float


@dataclass
class WindowScanState:
    thumbnail: bytes
    image_size: tuple[int, int]
    last_ocr_time: float
    last_text_hash: str
    last_seen_time: float
    unchanged_scans: int = 0
    last_text: str = ""


@dataclass
class PatternEventState:
    last_seen: float
    last_triggered: float | None = None
    consecutive_scans: int = 0
    missing_scans: int = 0
    active: bool = False


def preprocess_ocr_image(
    image: Image.Image,
    *,
    max_dimension: int,
    mode: str = "auto",
    upscale: int = 2,
) -> ProcessedOCRImage:
    """Prepare one OCR image and retain the complete source-to-OCR transform."""
    original_width, original_height = image.size
    resized = image
    if max(original_width, original_height) > max_dimension:
        ratio = float(max_dimension) / max(original_width, original_height)
        resized = image.resize(
            (
                max(1, round(original_width * ratio)),
                max(1, round(original_height * ratio)),
            ),
            Image.Resampling.LANCZOS,
        )
    processed = resized.resize(
        (max(1, resized.width * upscale), max(1, resized.height * upscale)),
        Image.Resampling.LANCZOS,
    ).convert("L")
    if mode == "auto":
        processed = ImageOps.autocontrast(processed)
        try:
            if float(ImageStat.Stat(processed).mean[0]) < 70.0:
                processed = ImageOps.invert(processed)
        except Exception:
            pass
        processed = processed.filter(ImageFilter.SHARPEN)
    return ProcessedOCRImage(
        image=processed,
        scale_x=processed.width / max(1, original_width),
        scale_y=processed.height / max(1, original_height),
    )


def make_thumbnail(image: Image.Image, size: tuple[int, int] = (64, 36)) -> bytes:
    return image.convert("L").resize(size, Image.Resampling.BILINEAR).tobytes()


def thumbnail_difference(previous: bytes, current: bytes) -> float:
    if len(previous) != len(current) or not previous:
        return 1.0
    return sum(abs(a - b) for a, b in zip(previous, current)) / (255.0 * len(previous))


class ScreenChangeDetector:
    def __init__(
        self,
        *,
        enabled: bool = True,
        threshold: float = 0.025,
        force_refresh_seconds: float = 20.0,
        state_expiry_seconds: float = 300.0,
    ):
        self.enabled = bool(enabled)
        self.threshold = max(0.0, min(float(threshold), 1.0))
        self.force_refresh_seconds = max(0.0, float(force_refresh_seconds))
        self.state_expiry_seconds = max(1.0, float(state_expiry_seconds))
        self.states: dict[tuple[object, ...], WindowScanState] = {}

    def cleanup(self, now: float) -> None:
        expired = [
            key for key, state in self.states.items()
            if now - state.last_seen_time > self.state_expiry_seconds
        ]
        for key in expired:
            self.states.pop(key, None)

    def should_scan(
        self, frame: CapturedFrame, now: float,
    ) -> tuple[bool, str, bytes, WindowScanState | None]:
        self.cleanup(now)
        thumbnail = make_thumbnail(frame.image)
        state = self.states.get(frame.key)
        if not self.enabled:
            return True, "change_detection_disabled", thumbnail, state
        if state is None:
            return True, "new_target", thumbnail, None
        state.last_seen_time = now
        if state.image_size != frame.image.size:
            return True, "resized", thumbnail, state
        if now - state.last_ocr_time >= self.force_refresh_seconds:
            return True, "forced_refresh", thumbnail, state
        difference = thumbnail_difference(state.thumbnail, thumbnail)
        if difference < self.threshold:
            state.unchanged_scans += 1
            return False, "unchanged", thumbnail, state
        return True, "changed", thumbnail, state

    def record(
        self,
        frame: CapturedFrame,
        *,
        thumbnail: bytes,
        text: str,
        text_hash: str,
        now: float,
    ) -> None:
        self.states[frame.key] = WindowScanState(
            thumbnail=thumbnail,
            image_size=frame.image.size,
            last_ocr_time=now,
            last_text_hash=text_hash,
            last_seen_time=now,
            unchanged_scans=0,
            last_text=text,
        )


_TRANSIENT_TIME_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}[ T])?(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?\b"
)


def normalize_event_snippet(value: str) -> str:
    text = _TRANSIENT_TIME_RE.sub("<time>", str(value or "").casefold())
    return " ".join(text.split())[:160]


class PatternEventTracker:
    def __init__(self):
        self.states: dict[tuple[object, ...], PatternEventState] = {}

    def update(
        self,
        matches: list,
        *,
        window_key: tuple[object, ...],
        now: float,
        cooldown_seconds: float,
        confirm_scans: int,
        low_confidence_confirm_scans: int,
        clear_scans: int,
        minimum_confidence: float,
        confidence_threshold: Callable[[object], float] | None = None,
    ) -> list:
        seen: set[tuple[object, ...]] = set()
        triggered: list = []
        for match in matches:
            key = (
                *window_key,
                str(getattr(match, "category", "")),
                normalize_event_snippet(getattr(match, "snippet", "")),
            )
            seen.add(key)
            state = self.states.get(key)
            if state is None:
                state = PatternEventState(last_seen=now)
                self.states[key] = state
            state.consecutive_scans = (
                state.consecutive_scans + 1 if state.missing_scans == 0 else 1
            )
            state.missing_scans = 0
            state.last_seen = now

            confidence = getattr(match, "confidence", None)
            required_confidence = (
                confidence_threshold(match)
                if confidence_threshold is not None
                else minimum_confidence
            )
            is_low = confidence is not None and float(confidence) < required_confidence
            required_scans = (
                max(1, int(low_confidence_confirm_scans))
                if is_low else max(1, int(confirm_scans))
            )
            cooldown = getattr(match, "cooldown_seconds", None)
            cooldown = float(cooldown if cooldown is not None else cooldown_seconds)
            ready = state.consecutive_scans >= required_scans
            cooldown_elapsed = (
                state.last_triggered is None
                or now - state.last_triggered >= max(0.0, cooldown)
            )
            if ready and not state.active and cooldown_elapsed:
                state.active = True
                state.last_triggered = now
                triggered.append(match)

        for key, state in list(self.states.items()):
            if key in seen or key[:len(window_key)] != window_key:
                continue
            state.missing_scans += 1
            state.consecutive_scans = 0
            if state.missing_scans >= max(1, int(clear_scans)):
                state.active = False
        expiry = max(300.0, float(cooldown_seconds) * 5.0)
        for key, state in list(self.states.items()):
            if now - state.last_seen > expiry:
                self.states.pop(key, None)
        return triggered


def parse_csv_values(value: str) -> tuple[str, ...]:
    values = []
    for item in str(value or "").split(","):
        clean = " ".join(item.strip().split())[:120]
        if clean:
            values.append(clean)
    return tuple(values[:100])


def compile_title_exclusions(value: str) -> tuple[tuple[str, ...], tuple[re.Pattern, ...]]:
    plain: list[str] = []
    regexes: list[re.Pattern] = []
    for item in parse_csv_values(value):
        if item.casefold().startswith("re:"):
            expression = item[3:].strip()
            if not expression or len(expression) > 200:
                continue
            try:
                regexes.append(re.compile(expression, re.IGNORECASE))
            except re.error:
                continue
        else:
            plain.append(item.casefold())
    return tuple(plain), tuple(regexes)


def is_capture_excluded(
    *,
    title: str,
    process_name: str,
    excluded_apps: tuple[str, ...],
    title_exclusions: tuple[tuple[str, ...], tuple[re.Pattern, ...]],
) -> bool:
    title_folded = str(title or "").casefold()
    process_folded = str(process_name or "").casefold()
    process_bare = (
        process_folded[:-4] if process_folded.endswith(".exe") else process_folded
    )
    for app in excluded_apps:
        token = app.casefold()
        bare = token[:-4] if token.endswith(".exe") else token
        if (
            token == process_folded
            or (bare and bare == process_bare)
            or (bare and bare in title_folded)
        ):
            return True
    plain, regexes = title_exclusions
    return any(item in title_folded for item in plain) or any(
        expression.search(title or "") for expression in regexes
    )


_REDACTIONS = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"), "Bearer [REDACTED]"),
    (re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gsk_[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{12,}|AKIA[A-Z0-9]{16})\b"), "[REDACTED API KEY]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "[REDACTED TOKEN]"),
    (re.compile(r"(?is)-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----"), "[REDACTED PRIVATE KEY]"),
    (
        re.compile(
            r"(?i)\b(password|passwd|sessionid|session|auth_token|cookie"
            r"|api_key|apikey|client_secret|secret|token)"
            r"\s*([:=])\s*[^\s,;]+"
        ),
        r"\1\2 [REDACTED]",
    ),
    (re.compile(r"\b(?:[A-Z0-9]{4}-){2,}[A-Z0-9]{4}\b", re.IGNORECASE), "[REDACTED RECOVERY CODE]"),
)


def redact_sensitive_text(value: str) -> str:
    text = str(value or "")
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text
