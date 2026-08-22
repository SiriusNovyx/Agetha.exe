# CommandSpec Stage 1 Design

## Objective

Make static command policy machine-readable in one canonical registry while
preserving every existing runtime safety boundary and command behavior.

Stage 1 consolidates command identity, base risk, capability classification,
origin eligibility, core-versus-handler dispatch, and mechanical feature-gate
metadata. It does not move implementations, parser rules, prompt prose,
dynamic security decisions, or provider behavior.

## Canonical model

`agetha.commands.specs` owns:

```python
@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    base_risk: RiskTier
    capability: Capability
    allowed_origins: frozenset[RequestOrigin]
    dispatch_kind: DispatchKind
    handler_key: str | None
    feature_gates: tuple[str, ...] = ()
```

`COMMAND_SPECS` is an immutable mapping built from an explicit tuple of one
specification per current command. Registry construction rejects empty names,
duplicate names, contradictory core/handler fields, unknown origins, and
duplicate feature-gate names.

`RiskTier` and `DispatchKind` are string enums. `DispatchKind.CORE` is used for
`idle`, `speak`, `wake_user`, and `popup`; these commands retain their explicit
central dispatch behavior and do not receive fake handlers.

## Derived compatibility views

- `ai_engine.VALID_COMMANDS` becomes a read-only view derived from
  `COMMAND_SPECS`.
- `CommandGuard.TIER_MAP` is derived from each specification's base risk while
  preserving the existing `SAFE`, `CAUTION`, and `DANGER` string exports.
- `capabilities.capability_for_command()` looks up the specification's explicit
  capability. Unknown input remains `ADVANCED_OS_INTEGRATION` and therefore
  fails closed at the outer capability boundary.
- Handler registration remains separate. Import-time duplicate handler
  registration raises deterministically, and invariant tests compare handlers
  bidirectionally with handler-backed specifications.

The obsolete `change_animation_speed` tier-only entry is not promoted into a
command. `popup` receives its previously implicit SAFE base tier. Commands that
previously relied on the capability fallback receive explicit
`ADVANCED_OS_INTEGRATION` classifications, preserving behavior.

## Origin authority

Origin policy remains layered:

1. The central request-origin policy continues to clamp or reject ambient,
   Terminal Sentinel, and tool-result output.
2. `CommandSpec.allowed_origins` may only narrow what survives that policy.
3. Capability policy, CommandGuard, feature gates, target validation,
   confirmation, and effect-time generation authorization still apply.

The specification registry never grants authority. Passive core responses are
eligible for all known origins. Ordinary commands retain their currently
effective trusted application-event eligibility. `computer_use` and
`analyze_screen_deep` remain direct-user-only. Ambient relevance is not part of
CommandSpec and cannot affect authorization.

## Static versus dynamic policy

CommandSpec contains only invocation-independent facts. These remain outside:

- target/process/protected/self identity checks;
- `force_close` target-sensitive auto-allow behavior;
- capability generation and transition state;
- user confirmation and timeout decisions;
- parser payload normalization, validation, and repair;
- handler preflight and effect-time revalidation;
- prompt descriptions and few-shot examples.

`feature_gates` records only mechanical configuration prerequisites already
associated with a command. It is not a replacement for capability or runtime
safety logic.

## Generated reference

`agetha.commands.generate_command_matrix` renders
`docs/generated/command_matrix.md` directly from `COMMAND_SPECS`. The document
contains only mechanical columns: command, base risk, capability, execution
requirement, allowed origins, dispatch kind, handler, and feature gates.

Runtime never reads the generated document. `--check` compares freshly rendered
content with the checked-in file and exits nonzero on drift. CI runs this check
on both existing operating-system jobs.

## Compatibility and risks

Public imports remain available during Stage 1. The main compatibility risks
are import cycles and accidental origin or capability changes. The registry
therefore depends only on the existing lightweight origin and capability enums;
`capability_for_command()` performs a lazy specification lookup to avoid a
module cycle. Literal behavior snapshots and dispatch regressions protect the
migration.

No command implementation or provider code moves in this stage.
