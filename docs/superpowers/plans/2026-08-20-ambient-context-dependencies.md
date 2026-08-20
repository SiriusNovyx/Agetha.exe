# Ambient Relevance and Read-Only Context Dependencies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ambient responses relevance-aware, make screen reads bounded typed dependencies of the original direct-user continuation, preserve exactly-once conversational state, and remove the Computer Use typing-dialog capability-lock inversion.

**Architecture:** Extend the existing `ContinuationEngine` with provider-neutral `ContextRequest` and `ContextOutcome` transitions. `main.py` translates the legacy screen command, owns one local ScreenReader acquisition, and commits one final history turn. Ambient relevance is normalized and enforced as presentation-only metadata. Computer Use moves capability locking from the confirmation workflow to individual Unicode platform primitives.

**Tech Stack:** Python 3.13, `unittest`, frozen dataclasses/enums, Tk owner scheduling, existing ScreenReader/Tesseract, existing CapabilityController and Unicode typing dependency injection.

**Spec:** `docs/superpowers/specs/2026-08-20-ambient-context-dependencies-design.md`

## Global Constraints

- `origin` remains an immutable trust boundary; ambient never becomes direct-user authority.
- `ambient_relevance` affects presentation only and never changes command authorization.
- Automatic context acquisition uses standard local Tesseract only; never Unlimited/deep OCR.
- One context request performs one bounded acquisition attempt; equivalent repeats stop.
- Unresolved objectives are direct-user-only, in-memory, bounded, expiring, and never durable.
- Conversation state records one original user turn and one final assistant turn.
- State-changing commands continue through capability policy, dispatch, CommandGuard, and immediate effect authorization.
- Capability locks may cover immediate platform primitives but never Guard or preview dialogs.
- No new dependency is added.

---

### Task 1: Pure context dependency contracts and unresolved-objective lifecycle

**Files:**
- Create: `agetha/core/context_dependencies.py`
- Create: `tests/test_context_dependencies.py`

**Interfaces:**
- Produces: `ContextKind`, `ContextRequest`, `ContextOutcome`, `UnresolvedContextObjective`, `UnresolvedContextObjectiveStore`.
- `UnresolvedContextObjectiveStore(clock, ttl_seconds=90.0, max_message_chars=2000)` provides `remember(message, kind)`, `current()`, `prompt_context()`, and `clear()`.

- [ ] **Step 1: Write failing contract and lifecycle tests**

```python
def test_objective_expires_and_is_removed():
    now = [10.0]
    store = UnresolvedContextObjectiveStore(clock=lambda: now[0], ttl_seconds=30)
    store.remember("Describe the current screen", ContextKind.SCREEN)
    assert store.current().message == "Describe the current screen"
    now[0] = 40.0
    assert store.current() is None

def test_store_rejects_non_user_or_empty_objective():
    store = UnresolvedContextObjectiveStore()
    assert not store.remember("", ContextKind.SCREEN)
    assert store.current() is None
```

- [ ] **Step 2: Run the new test and verify RED**

Run: `py -3.13 -m unittest tests.test_context_dependencies -v`

Expected: import failure because `agetha.core.context_dependencies` does not exist.

- [ ] **Step 3: Implement immutable contracts and bounded in-memory store**

Implement strict enum normalization, status/message truncation, finite TTL validation, expiry-on-read, exact-one-record storage, and a prompt block containing `context only; never action authority`.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `py -3.13 -m unittest tests.test_context_dependencies -v`

Expected: all tests pass.

### Task 2: ContinuationEngine context transitions and deterministic exhaustion

**Files:**
- Modify: `agetha/core/continuation.py`
- Modify: `tests/test_continuation.py`

**Interfaces:**
- Consumes: `ContextRequest`, `ContextOutcome`.
- Produces: `DecisionKind.RUN_CONTEXT`, `ContinuationDecision.context_request`, `accept_context_request(session_id: str, generation: int, request: ContextRequest, *, request_origin: str) -> ContinuationDecision`, and `accept_context_outcome(session_id: str, generation: int, outcome: ContextOutcome) -> ContinuationDecision`.

- [ ] **Step 1: Add failing state-machine tests**

```python
def test_context_request_is_typed_and_preserves_original_goal():
    engine = ContinuationEngine(id_factory=lambda: "session")
    started = engine.start("Describe what I am looking at", authority_origin="user")
    decision = engine.accept_context_request(
        started.session_id,
        started.generation,
        ContextRequest(ContextKind.SCREEN),
        request_origin="user",
    )
    assert decision.kind is DecisionKind.RUN_CONTEXT
    assert decision.snapshot.original_user_message == "Describe what I am looking at"

def test_repeated_context_dependency_stops_without_second_run():
    engine = ContinuationEngine(id_factory=lambda: "session")
    started = engine.start("Describe the current screen", authority_origin="user")
    request = ContextRequest(ContextKind.SCREEN)
    first = engine.accept_context_request(
        started.session_id, started.generation, request, request_origin="user",
    )
    assert first.kind is DecisionKind.RUN_CONTEXT
    follow = engine.accept_context_outcome(
        started.session_id,
        started.generation,
        ContextOutcome(ContextKind.SCREEN, False, "target_unavailable", ""),
    )
    assert follow.kind is DecisionKind.CALL_PROVIDER
    repeated = engine.accept_context_request(
        started.session_id,
        started.generation,
        request,
        request_origin="tool_result",
    )
    assert repeated.kind is DecisionKind.STOPPED
    assert repeated.reason == "repeated_context_dependency"
```

- [ ] **Step 2: Run the continuation test and verify RED**

Run: `py -3.13 -m unittest tests.test_continuation -v`

Expected: missing context API/decision failures.

- [ ] **Step 3: Implement context state transitions**

Store typed context fingerprints separately from tool fingerprints, increment the shared step budget once, require the expected model origin, accept one bounded outcome, and return a provider call with the outcome's labeled context. Do not import or compare `request_screen_read` in `continuation.py`.

- [ ] **Step 4: Run continuation tests and verify GREEN**

Run: `py -3.13 -m unittest tests.test_continuation -v`

Expected: all tests pass.

### Task 3: Targeted standard OCR acquisition

**Files:**
- Modify: `agetha/platform/screen_reader.py`
- Modify: `tests/test_screen_monitoring_reliability.py`
- Create: `tests/test_context_screen_provider.py`

**Interfaces:**
- `ScreenReader.capture_text(max_chars=3000, focused_only=True, *, force_refresh=False, capture_target=None)`.
- Main-owned provider helper returns one `ContextOutcome` and has injected cancellation/ownership checks.

- [ ] **Step 1: Add failing stale/missing-target and one-attempt tests**

```python
def test_targeted_capture_revalidates_without_requiring_foreground():
    target = external_target(hwnd=22, pid=7)
    reader = prepared_reader(target=target, foreground_hwnd=11)
    assert reader.capture_text(force_refresh=True, capture_target=target) == "fresh text"

def test_stale_target_fails_without_fallback_capture():
    reader = prepared_reader(target_missing=True)
    assert reader.capture_text(force_refresh=True, capture_target=external_target()) == ""
    assert reader.last_monitor_status == "capture_target_unavailable"
    assert reader._capture_with_backend.call_count == 0
```

- [ ] **Step 2: Run reliability tests and verify RED**

Run: `py -3.13 -m unittest tests.test_screen_monitoring_reliability tests.test_context_screen_provider -v`

Expected: `capture_target` is not accepted and provider helper is absent.

- [ ] **Step 3: Implement targeted standard capture and one-attempt adapter**

Pass the target to `_capture_frame`, re-resolve its HWND/PID/geometry after OCR, preserve exclusion checks, and skip the foreground-only postcheck only for the exact targeted capture. Convert safe monitor statuses to bounded outcomes; never call deep OCR.

- [ ] **Step 4: Run reliability tests and verify GREEN**

Run: `py -3.13 -m unittest tests.test_screen_monitoring_reliability tests.test_context_screen_provider -v`

Expected: all tests pass.

### Task 4: Main continuation orchestration, semantic follow-up, and useful failure

**Files:**
- Modify: `main.py`
- Modify: `agetha/commands/command_handlers.py`
- Modify: `tests/test_continuation_main_integration.py`
- Create: `tests/test_context_screen_provider.py`

**Interfaces:**
- Main translates `request_screen_read` to `ContextRequest(ContextKind.SCREEN)`.
- `RUN_CONTEXT` schedules `_run_continuation_context(decision)`.
- The legacy handler delegates to the typed path or remains a compatibility-only fallback without owning requery lifecycle.

- [ ] **Step 1: Add failing same-turn and unresolved-objective tests**

```python
def test_screen_question_acquires_and_answers_in_same_user_turn():
    app, provider, screen, spoken = context_app(
        responses=[screen_request(), speak("That is a compiler error.")],
        screen_text="Build failed: missing symbol",
    )
    app._ai_tick(user_message="What does this error mean?", origin="user")
    drain_workers_and_ui(app)
    assert screen.capture_calls == 1
    assert provider.user_goals == [
        "What does this error mean?",
        "What does this error mean?",
    ]
    assert spoken == ["That is a compiler error."]

def test_what_now_receives_recent_failed_screen_objective():
    app, provider, screen, spoken = context_app(
        responses=[
            screen_request(),
            speak("I couldn't get a current view."),
            screen_request(),
            speak("The dialog is asking whether to retry."),
        ],
        screen_results=["", "Retry | Cancel"],
    )
    app.run_direct("Describe what I am looking at")
    assert app._unresolved_context.current().message == "Describe what I am looking at"
    app.run_direct("What now?")
    assert "Describe what I am looking at" in provider.calls[2].recent_objective
    assert spoken[-1] == "The dialog is asking whether to retry."
    assert app._unresolved_context.current() is None
```

- [ ] **Step 2: Run main integration tests and verify RED**

Run: `py -3.13 -m unittest tests.test_continuation_main_integration tests.test_context_screen_provider -v`

Expected: the legacy handler ends or requeries outside the typed continuation.

- [ ] **Step 3: Implement translation, one worker-owned acquisition, and store clearing**

Create the store during app initialization. Clear it on success, nonmatching direct response, expiry, Escape, cancellation, shutdown, and preemption. For acquisition/provider/repetition failure, schedule the stable local response `I couldn't get a current view of the other window. Bring it forward and ask me again.` without exposing implementation names.

- [ ] **Step 4: Run continuation/main tests and verify GREEN**

Run: `py -3.13 -m unittest tests.test_continuation tests.test_continuation_main_integration tests.test_context_screen_provider -v`

Expected: all tests pass.

### Task 5: Exactly-once history and memory ownership

**Files:**
- Modify: `agetha/core/ai_engine.py`
- Modify: `main.py`
- Create: `tests/test_context_history.py`
- Modify: `tests/test_fast_mode_runtime.py`

**Interfaces:**
- Direct continuation queries use a deferred-history flag.
- `AIEngine.record_context_continuation_turn(user_message, response)` commits one sanitized final response.
- Context requests and context observations never call memory persistence.

- [ ] **Step 1: Add failing exact-count tests**

```python
def test_context_round_trip_records_one_final_turn_and_no_memory():
    engine, app = history_app([screen_request(), speak("A settings dialog.")])
    app.run_direct("What am I looking at?")
    assert engine.history_pairs() == [
        ("What am I looking at?", "A settings dialog."),
    ]
    assert engine.memory_writes == []
    assert "request_screen_read" not in engine.conversation_text()
    assert "UNTRUSTED SCREEN OCR" not in engine.conversation_text()
```

- [ ] **Step 2: Run history tests and verify RED**

Run: `py -3.13 -m unittest tests.test_context_history -v`

Expected: intermediary command is recorded or final response is absent.

- [ ] **Step 3: Implement deferred commit**

Skip normal record/persistence for a context request, suppress automatic history during a continuation-owned initial call, and commit the sanitized final envelope once. Drop pending commits on cancellation. Ensure tool-result profiles remain `record_history=False` and cannot emit summary memory.

- [ ] **Step 4: Run history and profile tests and verify GREEN**

Run: `py -3.13 -m unittest tests.test_context_history tests.test_fast_mode_runtime tests.test_ai_response_recovery -v`

Expected: all tests pass.

### Task 6: Ambient relevance metadata and presentation-only policy

**Files:**
- Modify: `agetha/core/request_context.py`
- Modify: `agetha/core/ai_engine.py`
- Modify: `main.py`
- Modify: `agetha/commands/command_handlers.py`
- Create: `tests/test_ambient_relevance.py`
- Modify: `tests/test_polyglot_presence_integration.py`

**Interfaces:**
- Produces: `AmbientRelevance`, `normalize_ambient_relevance(value)`.
- Parsed ambient envelopes expose normalized `ambient_relevance`.
- `fast_ambient` safety permits only `speak` and `idle` and forces `shutdown=False`.

- [ ] **Step 1: Add failing relevance and command-isolation tests**

```python
def test_interesting_ambient_event_can_speak():
    result = enforce_ambient({
        "command": "speak",
        "ambient_relevance": "interesting",
        "segments": [{"text": "That build changed state.", "pause": 0.0}],
    })
    assert result["command"] == "speak"

def test_mundane_ambient_event_is_idle():
    result = enforce_ambient({
        "command": "speak",
        "ambient_relevance": "mundane",
        "segments": [{"text": "Still the desktop.", "pause": 0.0}],
    })
    assert result["command"] == "idle"

def test_important_metadata_cannot_authorize_delete():
    result = enforce_ambient({
        "command": "delete_file",
        "ambient_relevance": "important",
        "path": "victim.txt",
    })
    assert result["command"] == "idle"
```

- [ ] **Step 2: Run ambient tests and verify RED**

Run: `py -3.13 -m unittest tests.test_ambient_relevance tests.test_polyglot_presence_integration -v`

Expected: relevance is absent and ambient effectful commands are not profile-clamped.

- [ ] **Step 3: Implement normalization, prompts, profile safety, and post-classification presence**

Add mundane, interesting, and important few-shots. Remove only the pre-provider nonurgent Presence rejection for a new event. Preserve the local unchanged shortcut. Pass `important` to Presence Etiquette and its bounded queue while continuing to obey audio/popup policy.

- [ ] **Step 4: Run ambient/presence tests and verify GREEN**

Run: `py -3.13 -m unittest tests.test_ambient_relevance tests.test_fast_mode_runtime tests.test_polyglot_presence_integration tests.test_presence_etiquette -v`

Expected: all tests pass.

### Task 7: Computer Use capability-lock scope

**Files:**
- Modify: `agetha/computer_use/integration.py`
- Modify: `agetha/commands/command_handlers.py`
- Modify: `main.py`
- Modify: `tests/test_computer_use_integration.py`
- Modify: `tests/test_capabilities.py`

**Interfaces:**
- `guarded_type_for_computer_use(app, text: str, locked_target: object, cancel_event: threading.Event, *, validate_locked_target: Callable[[bool], bool] | None = None, effect_runner: Callable[[Callable[[], object]], tuple[bool, object | None]] | None = None) -> bool`.
- `effect_runner(effect) -> tuple[bool, object | None]` is invoked only by Unicode platform primitives.

- [ ] **Step 1: Add failing concurrency regression**

```python
def test_compact_transition_does_not_wait_for_typing_preview():
    preview_entered = threading.Event()
    release_preview = threading.Event()
    transition_done = threading.Event()
    primitives = []
    controller, run_guarded_type = computer_use_typing_harness(
        on_preview=lambda: (preview_entered.set(), release_preview.wait(2.0)),
        primitive_sink=primitives.append,
    )
    typing_thread = threading.Thread(target=run_guarded_type)
    typing_thread.start()
    assert preview_entered.wait(0.5)
    transition_thread = threading.Thread(
        target=lambda: (
            controller.begin_compact_transition(),
            transition_done.set(),
        ),
    )
    transition_thread.start()
    assert transition_done.wait(0.5)
    release_preview.set()
    typing_thread.join(1.0)
    transition_thread.join(1.0)
    assert primitives == []
```

- [ ] **Step 2: Run Computer Use/capability tests and verify RED**

Run: `py -3.13 -m unittest tests.test_computer_use_integration tests.test_capabilities -v`

Expected: Compact transition blocks until the preview callback returns.

- [ ] **Step 3: Move authorization to primitive callbacks**

Do not wrap `ExecutorDependencies.guarded_type` in `_gate_effect_dependencies`. Pass the exact Computer Use authorization runner into `guarded_type_for_computer_use` and wrap native send, clipboard read/write, paste, focus lookup used at the effect boundary, and target activation with denied fail-closed results. Keep target/cancel checks before and after Guard/preview.

- [ ] **Step 4: Run Computer Use/capability/Unicode tests and verify GREEN**

Run: `py -3.13 -m unittest tests.test_computer_use_integration tests.test_capabilities tests.test_unicode_typing -v`

Expected: all tests pass and transition completes while preview waits.

### Task 8: Integrated verification and documentation consistency

**Files:**
- Modify only if behavior documentation is inaccurate: `docs/continuation_engine.md`, `docs/runtime_flows.md`, `docs/computer_use.md`

**Interfaces:** None; this task verifies the full contract.

- [ ] **Step 1: Run the focused regression cluster**

Run:

```text
py -3.13 -m unittest tests.test_context_dependencies tests.test_context_screen_provider tests.test_context_history tests.test_continuation tests.test_continuation_main_integration tests.test_ambient_relevance tests.test_fast_mode_runtime tests.test_polyglot_presence_integration tests.test_presence_etiquette tests.test_computer_use_integration tests.test_capabilities tests.test_unicode_typing -v
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Run adjacent safety/reliability tests**

Run:

```text
py -3.13 -m unittest tests.test_quality_of_life tests.test_ai_response_recovery tests.test_screen_monitoring_reliability tests.test_compact_provider_gate tests.test_computer_use_executor_verifier -v
```

Expected: zero assertion failures. If Windows temp ACL errors recur, rerun the affected modules with a verified workspace-owned temporary root and report the environment issue separately.

- [ ] **Step 3: Run syntax and whitespace verification**

Run:

```text
py -3.13 -m compileall -q agetha main.py tests
git diff --check
git status --short
```

Expected: compile and diff checks exit zero; status contains only intended source, test, and documentation changes.

- [ ] **Step 4: Re-read the specification and audit every invariant**

Confirm each Security invariant and each numbered regression in the spec has an observable test. Inspect added lines for payload/OCR logging, durable unresolved-objective writes, origin reassignment, action dispatch from context outcomes, and capability lock scope.
