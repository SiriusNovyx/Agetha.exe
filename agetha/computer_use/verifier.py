"""Local post-action verification without provider calls."""

from __future__ import annotations

from .models import (
    ComputerAction,
    ComputerActionKind,
    ComputerObservation,
    ExecutionResult,
    ExecutionStatus,
    VerificationResult,
    VerificationStatus,
)


class ComputerVerifier:
    """Compare two immutable observations and report only deterministic facts."""

    def verify(
        self,
        before: ComputerObservation,
        after: ComputerObservation,
        action: ComputerAction,
        execution: ExecutionResult,
    ) -> VerificationResult:
        if execution.status is ExecutionStatus.CANCELLED:
            return VerificationResult(VerificationStatus.CANCELLED, "session was cancelled")
        if execution.status is ExecutionStatus.TARGET_CHANGED:
            return VerificationResult(VerificationStatus.TARGET_CHANGED, "target changed before effect")
        if not execution.succeeded:
            return VerificationResult(VerificationStatus.FAILED, execution.safe_reason)
        if before.target is None or after.target is None:
            return VerificationResult(VerificationStatus.TARGET_CHANGED, "target is unavailable after action")
        if not before.target.matches(after.target, require_same_bounds=False) or not after.process_alive:
            return VerificationResult(VerificationStatus.TARGET_CHANGED, "target identity changed after action")

        kind = action.action
        if kind in {
            ComputerActionKind.OBSERVE_AGAIN,
            ComputerActionKind.WAIT,
        }:
            if before.observation_id != after.observation_id:
                return VerificationResult(VerificationStatus.VERIFIED, "fresh observation captured")
        if kind is ComputerActionKind.FOCUS_WINDOW:
            return VerificationResult(
                VerificationStatus.VERIFIED if after.foreground else VerificationStatus.FAILED,
                "target is foreground" if after.foreground else "target did not become foreground",
            )
        if kind in {
            ComputerActionKind.CLICK_CONTROL,
            ComputerActionKind.DOUBLE_CLICK_CONTROL,
        }:
            old = before.control(action.target_id)
            new = after.control(action.target_id)
            if old is not None and (new is None or (old.label, old.role, old.state) != (new.label, new.role, new.state)):
                return VerificationResult(VerificationStatus.VERIFIED, "target control changed")
        if kind is ComputerActionKind.FINISH:
            return VerificationResult(VerificationStatus.VERIFIED, "planner marked goal complete")

        return VerificationResult(
            VerificationStatus.UNVERIFIED,
            "no deterministic state change was available; planner must inspect the fresh observation",
        )

