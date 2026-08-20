"""Atomic, injected observation adapter for Computer Use Lite.

This module does not capture the screen itself.  The application supplies one
atomic source so frame metadata and OCR coordinates cannot be mixed across
captures.  Accessibility is an optional protocol and is honestly unavailable
by default.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from .models import (
    MAX_CONTROLS,
    ComputerObservation,
    ControlSource,
    ObservedControl,
    Rect,
    WindowIdentity,
    clamp_confidence,
    safe_text,
)


@dataclass(frozen=True, slots=True)
class RawControl:
    """A control returned by OCR or an accessibility adapter before IDs exist."""

    label: str
    bounds: Rect
    confidence: float
    source: ControlSource = ControlSource.OCR
    role: str = ""
    state: str = ""
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class AtomicScreenSnapshot:
    """One capture's inseparable window metadata and OCR result."""

    target: WindowIdentity | None
    foreground: bool
    screen_bounds: Rect
    cursor: tuple[int, int]
    ocr_controls: tuple[RawControl, ...]
    process_alive: bool
    captured_at: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "ocr_controls", tuple(self.ocr_controls))
        if len(self.cursor) != 2:
            raise ValueError("cursor must contain x and y")


@runtime_checkable
class AtomicObservationSource(Protocol):
    def capture(self, expected_target: WindowIdentity | None) -> AtomicScreenSnapshot:
        """Return a fresh atomic snapshot, optionally locked to a target."""


class AccessibilityUnavailable(RuntimeError):
    """Expected signal that native controls are unavailable on this platform."""


@runtime_checkable
class AccessibilityProvider(Protocol):
    @property
    def available(self) -> bool:
        """Whether this provider can currently return native controls."""

    def controls(self, snapshot: AtomicScreenSnapshot) -> tuple[RawControl, ...]:
        """Return controls belonging only to ``snapshot.target``."""


class UnavailableAccessibilityProvider:
    """Default provider: no dependency is present, so OCR remains the MVP."""

    @property
    def available(self) -> bool:
        return False

    def controls(self, snapshot: AtomicScreenSnapshot) -> tuple[RawControl, ...]:
        del snapshot
        return ()


class ComputerObserver:
    """Transform an atomic source capture into a compact immutable observation."""

    def __init__(
        self,
        source: AtomicObservationSource,
        *,
        accessibility: AccessibilityProvider | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        max_controls: int = MAX_CONTROLS,
        observation_id_factory: Callable[[int], str] | None = None,
    ) -> None:
        if max_controls <= 0 or max_controls > MAX_CONTROLS:
            raise ValueError(f"max_controls must be 1..{MAX_CONTROLS}")
        self._source = source
        self._accessibility = accessibility or UnavailableAccessibilityProvider()
        self._monotonic = monotonic
        self._max_controls = max_controls
        self._id_factory = observation_id_factory or (lambda number: f"cuobs:{number}")
        self._counter = 0
        self._counter_lock = threading.Lock()

    @property
    def accessibility_available(self) -> bool:
        return bool(self._accessibility.available)

    def observe(
        self,
        expected_target: WindowIdentity | None,
        *,
        previous_result: str = "",
    ) -> ComputerObservation:
        snapshot = self._source.capture(expected_target)
        captured_at = float(snapshot.captured_at)
        if captured_at < 0:
            captured_at = float(self._monotonic())

        native_controls: tuple[RawControl, ...] = ()
        accessibility_available = bool(self._accessibility.available)
        if accessibility_available:
            try:
                native_controls = tuple(self._accessibility.controls(snapshot))
            except AccessibilityUnavailable:
                accessibility_available = False

        controls = self._compact_controls(
            snapshot,
            native_controls=native_controls,
        )
        return ComputerObservation(
            observation_id=self._next_observation_id(),
            target=snapshot.target,
            foreground=bool(snapshot.foreground),
            screen_bounds=snapshot.screen_bounds,
            cursor=(int(snapshot.cursor[0]), int(snapshot.cursor[1])),
            controls=controls,
            previous_result=safe_text(previous_result),
            process_alive=bool(snapshot.process_alive),
            captured_at=captured_at,
            accessibility_available=accessibility_available,
        )

    def _next_observation_id(self) -> str:
        with self._counter_lock:
            self._counter += 1
            return self._id_factory(self._counter)

    def _compact_controls(
        self,
        snapshot: AtomicScreenSnapshot,
        *,
        native_controls: tuple[RawControl, ...],
    ) -> tuple[ObservedControl, ...]:
        target_bounds = snapshot.target.bounds if snapshot.target else None
        output: list[ObservedControl] = []
        seen: set[tuple[str, Rect]] = set()
        counts = {ControlSource.ACCESSIBILITY: 0, ControlSource.OCR: 0}

        ordered = tuple((raw, True) for raw in native_controls) + tuple(
            (raw, False) for raw in snapshot.ocr_controls
        )
        for raw, is_native in ordered:
            source = raw.source
            if is_native:
                source = ControlSource.ACCESSIBILITY
            elif source is ControlSource.ACCESSIBILITY:
                # OCR input cannot promote itself to native accessibility.
                source = ControlSource.OCR
            label = safe_text(raw.label)
            role = safe_text(raw.role, maximum=80)
            state = safe_text(raw.state, maximum=80)
            if not label and not role:
                continue
            if not snapshot.screen_bounds.contains_rect(raw.bounds):
                continue
            if target_bounds is None or not target_bounds.contains_rect(raw.bounds):
                continue
            dedup_key = (f"{label.casefold()}|{role.casefold()}", raw.bounds)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            counts[source] += 1
            prefix = "acc" if source is ControlSource.ACCESSIBILITY else "ocr"
            output.append(
                ObservedControl(
                    control_id=f"{prefix}:{counts[source]}",
                    source=source,
                    label=label,
                    bounds=raw.bounds,
                    confidence=clamp_confidence(raw.confidence),
                    role=role,
                    state=state,
                    enabled=bool(raw.enabled),
                )
            )
            if len(output) >= self._max_controls:
                break
        return tuple(output)
