# Ambient Relevance and Read-Only Context Dependencies Design

**Date:** 2026-08-20

**Status:** Approved for implementation

## Problem

Agetha currently treats `origin=ambient` conservatively at several independent layers. The combined behavior is more restrictive than intended: Presence Etiquette can prevent a new ambient event from reaching the provider, the `fast_ambient` profile supplies only a mundane-to-`idle` example, and the parsed response has no validated importance signal for presentation policy. Ambient origin must remain a lower-trust, higher-threshold origin, but meaningful events must be able to produce short comments and important events must not be semantically discarded.

Screen-dependent direct-user requests have a separate lifecycle defect. The initial direct turn often sees Agetha's own focused window, the model returns `request_screen_read`, and the legacy handler performs another immediate capture and ad-hoc requery. The handler is not part of the bounded continuation owner, does not expose a structured acquisition outcome, and cannot retain an unresolved screen objective for a later semantic follow-up.

The same change set also addresses a confirmed Computer Use authorization bug: `build_runtime_bundle()` currently wraps the entire synchronous `guarded_type` flow in `CapabilityController.perform_authorized()`. Command Guard and typing-preview dialogs may wait for user input while the controller lock is held, preventing Compact Mode transition and cancellation.

## Goals

1. Preserve the distinction between direct-user, ambient, tool-result, and internal origins.
2. Let ambient events be classified as mundane, interesting, or important without changing their authority.
3. Turn passive screen acquisition into a typed dependency of the current direct-user continuation.
4. Preserve the original direct-user objective across one bounded acquisition and provider continuation.
5. Support a short-lived semantic follow-up after a failed screen acquisition without durable persistence.
6. Record one logical user turn and one final assistant turn, with no intermediary dependency command in normal history.
7. Keep all state-changing operations on the existing capability, dispatch, and CommandGuard paths.
8. Ensure Computer Use confirmation UI never runs while the capability-controller lock is held.

## Non-goals

- No autonomous clicking, typing, launching, shell execution, or file mutation is added.
- No cloud/deep OCR is invoked automatically.
- No new planner or parallel agent subsystem is introduced.
- No phrase list is used to classify screen questions or `What now?` follow-ups.
- No durable memory is created from dependency observations or unresolved objectives.
- No promise is made that audio always plays for important ambient responses.

## Root causes

### Ambient behavior

- `main.CompanionApp._ai_tick()` applies a nonurgent Presence Etiquette decision before provider execution. A new event can therefore be discarded before its relevance is known.
- `request_profile_for_origin("ambient")` always selects `fast_ambient`. That security-scoped profile is honored even when Fast Mode is off.
- `fast_ambient` uses zero history and one ambient few-shot, and that example always returns `idle`.
- Presence Etiquette already has urgency types, but the model response envelope has no validated ambient relevance metadata to select one.
- Ambient history and memory suppression are intentional privacy/noise controls and are not the cause.

### Screen acquisition

- The direct user types while Agetha owns foreground focus, so ordinary focused capture often yields `skipped_own_window`.
- The model is explicitly shown a `what's on my screen -> request_screen_read` example.
- `handle_request_screen_read()` captures synchronously, does not force refresh, and starts a special requery outside `ContinuationEngine`.
- `request_screen_read` is not an automatic continuation command, so the generic bounded owner is cancelled before the legacy handler runs.
- Screen cache state is a bare string in `CompanionApp`; it has no dependency identity, outcome, or unresolved-objective lifecycle.

### Computer Use typing lock

- `_gate_effect_dependencies()` wraps `dependencies.guarded_type` with the same primitive effect wrapper used for clicks and keypresses.
- The wrapper calls `CapabilityController.perform_authorized()`, which intentionally holds the controller lock from authorization check through the callback.
- `guarded_type_for_computer_use()` includes Command Guard and a typing-preview wait of up to 120 seconds, so Compact transition can block on that lock.
- The normal Unicode typing path demonstrates the correct pattern: dialogs execute without the capability lock; individual focus, native-input, clipboard, paste, and activation primitives are atomically authorized.

## Architecture

### 1. Typed context dependencies

Create `agetha/core/context_dependencies.py` as a pure, side-effect-free contract module.

```python
class ContextKind(str, Enum):
    SCREEN = "screen"

@dataclass(frozen=True, slots=True)
class ContextRequest:
    kind: ContextKind

@dataclass(frozen=True, slots=True)
class ContextOutcome:
    kind: ContextKind
    success: bool
    status: str
    provider_context: str
    sensitivity: str = "private"
```

The module also owns an in-memory `UnresolvedContextObjectiveStore`. It retains at most one direct-user objective, one context kind, and an absolute monotonic expiry. It exposes bounded prompt context and explicit `remember`, `clear`, and expiry behavior. It has no filesystem, memory-system, provider, screen, or command dependencies.

`request_screen_read` remains accepted in the provider command schema for compatibility. `main.py` translates that response immediately to `ContextRequest(ContextKind.SCREEN)`. `ContinuationEngine` never imports or compares the legacy command name.

### 2. Continuation state machine

Extend `ContinuationEngine` with a distinct context path:

- `DecisionKind.RUN_CONTEXT`
- `ContinuationDecision.context_request`
- `accept_context_request(session_id: str, generation: int, request: ContextRequest, *, request_origin: str) -> ContinuationDecision`
- `accept_context_outcome(session_id: str, generation: int, outcome: ContextOutcome) -> ContinuationDecision`

The session records context fingerprints separately from tool fingerprints. A context request increments the existing bounded step counter. The first unique request becomes `RUN_CONTEXT`. A repeated equivalent request in the same session becomes a deterministic stopped decision with reason `repeated_context_dependency`; it never starts another acquisition.

Only sessions started from direct-user authority may acquire context. A `tool_result`, ambient, terminal, or standalone response cannot start or borrow a context session.

A successful or failed `ContextOutcome` produces one constrained continuation provider call. Failure context contains only a bounded safe status, not exception text or implementation details. The provider is asked to give a useful final answer based on the available information. If it requests the same dependency again, the state machine stops and `main.py` supplies a local useful fallback.

### 3. Main-owned screen provider

`main.py` remains the composition and lifecycle owner. Its context adapter performs exactly one call to standard `ScreenReader.capture_text()` per `RUN_CONTEXT` decision.

For a screen dependency it:

1. Rechecks session ownership, cancellation, capability policy, shutdown, and screen availability.
2. Before the normal direct-user OCR preflight can clear an own-window result,
   obtains a previously validated external target through
   `ScreenReader.preserve_external_target()` and holds it as a private,
   single-use continuation lease.
3. Calls local Tesseract capture once with `force_refresh=True` and the preserved target when available.
4. Redacts and labels the resulting OCR through the existing untrusted-context boundary.
5. Converts capture status into a bounded `ContextOutcome`.
6. Rechecks continuation ownership before publishing the result.

`ScreenReader.capture_text()` gains a backward-compatible keyword-only `capture_target=None`. Targeted passive capture revalidates the HWND/PID/geometry before and after OCR but does not require that background target to become foreground. Exclusion and sensitive-window rules remain active. Ordinary ambient capture keeps its current foreground requirement and defaults.

Automatic dependency acquisition never calls `capture_deep_text()` or Unlimited OCR.

### 4. Unresolved objective lifecycle

Only a failed direct-user context acquisition may call `remember()`.

The store is cleared when:

- context acquisition succeeds and the continuation completes;
- a subsequent direct-user response does not request the same context kind, which is treated as semantic topic change;
- its monotonic TTL expires;
- Escape, cancellation, shutdown, or direct preemption occurs.

On the next direct turn within the TTL, the prior objective is supplied to the provider in a bounded trusted context block that explicitly says it is context for resolving anaphora, not new action authority. The current model decides whether the new message is a follow-up by either requesting the same typed dependency or answering the new topic. No phrase matching is added.

The store is process-local and never written to conversation files, memory files, episodic memory, logs, settings, or audit records.

### 5. Exactly-once conversation state

Direct-user requests that own a continuation use deferred history recording:

- the initial provider response is parsed and policy-checked but not immediately written to normal history;
- an intermediary context/tool request is never recorded as the assistant turn;
- an immediate final response or delegated state-changing command is committed once;
- a final continuation response is committed once against the original user message;
- cancellation drops the pending history transaction;
- dependency observations are never passed to memory persistence.

`AIEngine` exposes a narrow app-owned completion method that accepts only the original direct-user message and the final sanitized response envelope. It writes the same bounded history/conversation representation used by normal turns. The context request path suppresses `summary_memory`; the continuation profile continues to disallow summary memory and durable history.

### 6. Ambient relevance

Add a validated enum:

```python
class AmbientRelevance(str, Enum):
    MUNDANE = "mundane"
    INTERESTING = "interesting"
    IMPORTANT = "important"
```

`ambient_relevance` is accepted only as presentation metadata. Missing, malformed, or unknown values normalize to `mundane`.

The ambient prompt and few-shots describe the three-way threshold:

- mundane/unchanged/irrelevant -> `idle`;
- interesting/relevant -> optional short `speak`;
- important/urgent/safety-relevant -> short `speak` with `important` metadata.

The local unchanged/repeated-event shortcut remains. Presence Etiquette is no longer used to prevent a genuinely new ambient event from being semantically classified. After parsing, the dispatcher maps interesting to nonurgent presence and important to important presence.

Important means the semantic response is retained and surfaced when policy allows. Existing presentation, fullscreen, active-game, media, quiet-hour, minimized, sleeping, and shutdown rules continue to decide popup/audio delivery. An important response may therefore be queued or shown without audio.

For `fast_ambient`, profile safety clamps commands to `speak` or `idle`, forces `shutdown=False`, removes popup and summary-memory fields, and preserves only normalized relevance. Relevance never changes origin, capability authorization, CommandGuard tier, tool permissions, or action policy.

### 7. Computer Use typing authorization scope

`_gate_effect_dependencies()` stops wrapping the whole `guarded_type` callback in `effect_runner`.

`guarded_type_for_computer_use()` instead receives the Computer Use capability primitive runner and binds it to the Unicode dependencies' effectful callbacks using the same denied values as the direct typing path:

- focused-target lookup where it participates in the effect boundary;
- native Unicode send;
- clipboard read/write;
- paste shortcut;
- target activation.

Command Guard, preview creation, preview waiting, and target validation execute without the capability-controller lock. Immediately before every platform primitive, `perform_authorized()` atomically rechecks the original generation. A Compact transition can therefore acquire the controller lock, invalidate the token, set session cancellation, and cause all later primitives to fail closed.

## Failure behavior

Context acquisition returns safe stable statuses such as `screen_unavailable`, `target_unavailable`, `capture_blocked`, or `ocr_empty`. Provider-visible context is bounded and contains no raw exception or internal lifecycle wording.

If the follow-up provider is unavailable or repeats the dependency, the user sees a local response such as: "I couldn't get a current view of the other window. Bring it forward and ask me again." This response is committed once as the assistant turn when the original user turn was not cancelled.

## Security invariants

- Ambient relevance is presentation metadata only.
- Origin is immutable and never inferred from text.
- Screen, OCR, document, web, memory, and tool content remains untrusted data.
- Context acquisition starts only inside a live direct-user continuation.
- A context outcome cannot dispatch a state-changing command.
- Direct state-changing responses continue through capability classification, dispatch, CommandGuard, and immediate effect revalidation.
- One context request causes at most one local acquisition attempt.
- No dependency observation or unresolved objective enters durable memory.
- Important presentation never overrides privacy/presence audio policy.
- Capability locks cover only immediate platform primitives, never user dialogs.

## Test strategy

Tests use fake clocks, fake providers, fake screens, fake UI queues, and injected primitive runners. No test performs real OCR, network access, process inspection, input injection, clipboard mutation, or GUI interaction.

Focused regressions cover:

1. natural direct screen question -> one acquisition -> one final response in one turn;
2. original objective preserved across context acquisition;
3. recent unresolved objective supplied for semantic `What now?` follow-up;
4. failed acquisition and repeated request stop deterministically;
5. meaningful ambient response survives classification;
6. mundane ambient response remains idle;
7. ambient context cannot select privileged commands;
8. direct protected commands still reach CommandGuard;
9. one history entry and zero duplicate memory writes;
10. ordinary direct-user behavior remains compatible;
11. stale/missing screen targets fail closed after one capture attempt;
12. unresolved objective expiry, success, topic change, cancellation, and shutdown clearing;
13. Compact transition completes while Guard/preview waits;
14. after transition, native typing and clipboard primitives are not called.

## Compatibility risks and mitigations

- A screen-dependent question can add one provider round trip. Only an explicit model dependency triggers it.
- Background-window capture may be partially occluded. It is best-effort and reports failure honestly.
- Wayland, missing Tesseract, excluded targets, and sensitive targets remain unavailable and fail closed.
- History changes from an intermediary command entry to one final answer entry. This is intentional and covered by exact-count tests.
- More genuinely new ambient events may reach the provider. Existing unchanged/repeated local suppression, output limits, and post-classification presence policy bound chatter and cost.
- Old providers that omit `ambient_relevance` default to mundane and remain quiet until they follow the updated prompt.
