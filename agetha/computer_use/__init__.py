"""Deterministic foundations for the opt-in Computer Use Lite subsystem."""

from .executor import ComputerExecutor, ExecutorDependencies
from .models import (
    ComputerAction,
    ComputerActionKind,
    ComputerObservation,
    ComputerUseAuditEvent,
    ExecutionResult,
    ExecutionStatus,
    ObservedControl,
    PlannerRequest,
    PolicyDecision,
    ProcessIdentity,
    Rect,
    SessionOutcome,
    SessionSnapshot,
    SessionState,
    WindowIdentity,
    process_identities_match,
)
from .observer import (
    AtomicScreenSnapshot,
    ComputerObserver,
    RawControl,
    UnavailableAccessibilityProvider,
)
from .planner import ComputerPlanner, StructuredPlannerClient
from .policy import ComputerUsePolicy, PolicyContext
from .session import ComputerUseManager, ComputerUseSessionSpec, SessionAlreadyActive
from .verifier import ComputerVerifier

__all__ = [
    "AtomicScreenSnapshot",
    "ComputerAction",
    "ComputerActionKind",
    "ComputerExecutor",
    "ComputerObservation",
    "ComputerObserver",
    "ComputerUseAuditEvent",
    "ComputerPlanner",
    "ComputerUseManager",
    "ComputerUsePolicy",
    "ComputerUseSessionSpec",
    "ComputerVerifier",
    "ExecutionResult",
    "ExecutionStatus",
    "ExecutorDependencies",
    "ObservedControl",
    "PlannerRequest",
    "PolicyContext",
    "PolicyDecision",
    "ProcessIdentity",
    "RawControl",
    "Rect",
    "SessionOutcome",
    "SessionAlreadyActive",
    "SessionSnapshot",
    "SessionState",
    "StructuredPlannerClient",
    "UnavailableAccessibilityProvider",
    "WindowIdentity",
    "process_identities_match",
]
