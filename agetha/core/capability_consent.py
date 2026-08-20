"""Pure Compact/Full consent state transitions.

The flow deliberately owns no Tk widgets, configuration writes, provider
calls, or operating-system effects.  UI callbacks carry the returned
generation so cancellation, close, downgrade, and shutdown can invalidate
late work deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ConsentState(str, Enum):
    COMPACT = "compact"
    FIRST_CONFIRMATION = "first_confirmation"
    CONSENT_DEMO = "consent_demo"
    FINAL_CONFIRMATION = "final_confirmation"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class ConsentSnapshot:
    state: ConsentState
    generation: int
    shutdown: bool = False

    @property
    def full_active(self) -> bool:
        return not self.shutdown and self.state is ConsentState.FULL


class CapabilityConsentFlow:
    """Generation-bound state machine for deliberate Full Mode activation."""

    def __init__(self, *, initial_full: bool = False) -> None:
        self._state = ConsentState.FULL if initial_full else ConsentState.COMPACT
        self._generation = 0
        self._shutdown = False

    @property
    def snapshot(self) -> ConsentSnapshot:
        return ConsentSnapshot(
            state=self._state,
            generation=self._generation,
            shutdown=self._shutdown,
        )

    def begin_enable(self) -> ConsentSnapshot:
        if self._shutdown or self._state is not ConsentState.COMPACT:
            return self.snapshot
        self._generation += 1
        self._state = ConsentState.FIRST_CONFIRMATION
        return self.snapshot

    def confirm_first(self, generation: int) -> ConsentSnapshot:
        return self._advance(
            generation,
            expected=ConsentState.FIRST_CONFIRMATION,
            destination=ConsentState.CONSENT_DEMO,
        )

    def finish_demo(self, generation: int) -> ConsentSnapshot:
        """Advance after either the external demo or its in-app fallback."""

        return self._advance(
            generation,
            expected=ConsentState.CONSENT_DEMO,
            destination=ConsentState.FINAL_CONFIRMATION,
        )

    def confirm_final(self, generation: int) -> ConsentSnapshot:
        return self._advance(
            generation,
            expected=ConsentState.FINAL_CONFIRMATION,
            destination=ConsentState.FULL,
        )

    def cancel(self, generation: int | None = None) -> ConsentSnapshot:
        if self._shutdown:
            return self.snapshot
        if generation is not None and generation != self._generation:
            return self.snapshot
        return self._reset_to_compact()

    def close(self, generation: int | None = None) -> ConsentSnapshot:
        return self.cancel(generation)

    def downgrade_to_compact(self) -> ConsentSnapshot:
        if self._shutdown:
            return self.snapshot
        if self._state is ConsentState.COMPACT:
            return self.snapshot
        return self._reset_to_compact()

    def shutdown(self) -> ConsentSnapshot:
        if self._shutdown:
            return self.snapshot
        self._shutdown = True
        self._generation += 1
        self._state = ConsentState.COMPACT
        return self.snapshot

    def is_current(self, generation: int, state: ConsentState) -> bool:
        return bool(
            not self._shutdown
            and generation == self._generation
            and state is self._state
        )

    def _advance(
        self,
        generation: int,
        *,
        expected: ConsentState,
        destination: ConsentState,
    ) -> ConsentSnapshot:
        if not self.is_current(generation, expected):
            return self.snapshot
        self._state = destination
        return self.snapshot

    def _reset_to_compact(self) -> ConsentSnapshot:
        self._generation += 1
        self._state = ConsentState.COMPACT
        return self.snapshot
