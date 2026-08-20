# GPT-OSS Provider Migration and Bounded Response Recovery Implementation Plan

> **For Codex:** Follow test-driven development task by task. Do not stage or commit without explicit user authorization.

**Goal:** Replace the retired Groq model path and add a one-attempt, direct-user-only response repair while preserving the provider-neutral command envelope, `CommandGuard`, trust boundaries, and exactly-once side effects.

**Architecture:** Put provider-specific model/request/error policy in `agetha/core/provider_protocol.py`; keep the AI engine responsible for orchestration. Give parsing explicit local status metadata, propagate the trusted request origin from `main.py`, and defer all history/memory operations until the final response is known.

**Tech stack:** Python 3.10+, `unittest`, existing Groq/OpenRouter/Ollama adapters; no new dependencies.

---

## Task 1: Establish provider-policy regression tests

**Files:**

- Create: `tests/test_provider_protocol.py`
- Create: `agetha/core/provider_protocol.py`

- [ ] Add tests asserting the default is `openai/gpt-oss-120b`, a retired configured value normalizes to it, and a non-retired custom value is preserved.
- [ ] Add table-driven tests for the seven request-profile reasoning mappings.
- [ ] Assert GPT-OSS options include JSON Object Mode and reasoning effort, while a non-GPT-OSS model receives no provider-specific options.
- [ ] Add table-driven classification tests for 400 model/request failures, 401/403 authentication, 404 missing model, 429 rate limit, 5xx/timeouts transient failures.
- [ ] Run the new test file and confirm it fails because the policy module does not exist.
- [ ] Implement the minimum policy types and functions:

```python
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


def normalize_groq_model(value: object) -> str:
    candidate = str(value or "").strip()
    if not candidate or candidate.lower() in RETIRED_GROQ_MODELS:
        return DEFAULT_GROQ_MODEL
    return candidate


def groq_request_options(model: str, profile_name: str) -> dict[str, object]:
    if model not in GPT_OSS_MODELS:
        return {}
    return {
        "reasoning_effort": REASONING_EFFORT_BY_PROFILE.get(profile_name, "medium"),
        "response_format": {"type": "json_object"},
    }
```

- [ ] Run `tests.test_provider_protocol` and confirm it passes.

## Task 2: Integrate the P0 model and request policy

**Files:**

- Modify: `agetha/core/ai_engine.py`
- Modify: `tests/test_provider_protocol.py`
- Modify: `tests/test_fast_mode_runtime.py`

- [ ] Add failing engine-level tests proving retired configuration cannot remain active, key rotation does not rotate models, and GPT-OSS calls receive `reasoning_effort` plus JSON Object Mode.
- [ ] Add failing tests proving OpenRouter and Ollama calls receive no GPT-OSS-only arguments.
- [ ] Replace mutable Groq model-list insertion/rotation with an instance-level normalized model.
- [ ] Apply request options only when the active client is Groq and the active model is GPT-OSS:

```python
request_options = groq_request_options(self._groq_model, profile.name)
response = client.chat.completions.create(
    model=self._groq_model,
    messages=messages,
    max_tokens=output_limit,
    temperature=profile.temperature,
    **request_options,
)
```

- [ ] Update streaming, non-streaming, and structured-request Groq paths consistently.
- [ ] Run the two focused test modules and confirm they pass.

## Task 3: Integrate permanent/transient provider-error policy

**Files:**

- Modify: `agetha/core/provider_protocol.py`
- Modify: `agetha/core/ai_engine.py`
- Modify: `tests/test_provider_protocol.py`
- Modify: `tests/test_fast_mode_runtime.py`

- [ ] Add failing tests for typed OpenRouter HTTP errors and each Groq recovery class.
- [ ] Preserve HTTP status in a `ProviderHTTPError` raised by the OpenRouter adapter.
- [ ] Replace the rate-limit-only branch with classification-driven handling.
- [ ] Ensure permanent model/request failures bypass Groq key/model retry and enter the existing one-way provider fallback path.
- [ ] Keep key rotation bounded for authentication/rate limiting and keep transient retries bounded by the existing loop limits.
- [ ] Run focused provider and runtime tests.

## Task 4: Establish explicit parser-state regression tests

**Files:**

- Create: `tests/test_ai_response_recovery.py`
- Modify: `agetha/core/provider_protocol.py`
- Modify: `agetha/core/ai_engine.py`

- [ ] Add failing tests for intentional idle, fenced JSON, malformed JSON, wrong top-level type, missing/wrong command fields, unsupported commands, empty `speak`, and safe partial `speak` recovery.
- [ ] Add a regression proving malformed capability-bearing commands are not reconstructed.
- [ ] Add `ProviderResponseStatus` and a stable `provider_response_status` result key.
- [ ] Refactor `_parse()` so all outcomes retain their status and only `idle`/complete `speak` can be salvaged from malformed JSON.
- [ ] Ensure local command-normalization paths preserve the status key.
- [ ] Run the parser-focused tests.

## Task 5: Add the one-attempt direct-user repair lifecycle

**Files:**

- Modify: `agetha/core/ai_engine.py`
- Modify: `agetha/core/request_context.py`
- Modify: `main.py`
- Modify: `tests/test_ai_response_recovery.py`
- Modify: `tests/test_fast_mode_runtime.py`

- [ ] Add failing non-streaming tests: direct user malformed-then-valid uses two calls and one history/memory write; malformed-twice uses two calls, no memory write, and one deterministic history response.
- [ ] Add failing streaming equivalents.
- [ ] Add table-driven tests proving `ambient`, `touch`, `file_drop`, `reminder`, `tool_result`, and `terminal_sentinel` receive one provider call and no repair.
- [ ] Add a failing integration test proving `main.py` forwards normalized origin.
- [ ] Add `request_origin: RequestOrigin = "user"` to both AI-engine query entry points and forward it from the two main call sites.
- [ ] Parse before side effects, append one local repair system instruction for the eligible first failure, and retry once using the same provider attempt loop.
- [ ] Return a deterministic `speak` failure for a direct user and a non-executing status-bearing `idle` for ineligible origins.
- [ ] Persist/record only the successful final result, or one deterministic direct-user failure result.
- [ ] Confirm intentional idle alone still reaches the existing user personality fallback.
- [ ] Run response-recovery, fast-mode, request-context, and command-guard tests.

## Task 6: Update canonical configuration and documentation

**Files:**

- Modify: `agetha/app_config.py`
- Modify: `config.txt`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] Replace only the retired Groq model defaults/examples; preserve unrelated local `config.txt` settings.
- [ ] Document GPT-OSS as the Groq default, profile-based reasoning, JSON Object Mode, provider failure behavior, and the bounded direct-user repair policy.
- [ ] Search the runtime/docs for `llama-3.3-70b-versatile` and confirm it remains only in the explicit retired-model compatibility set or historical text that must name it.

## Task 7: Verification and handoff

**Files:**

- Verify all modified files.

- [ ] Run focused tests after each implementation task.
- [ ] Run existing fast-mode, quality-of-life, security/command-guard, request-context, and AI-engine suites.
- [ ] Run the full unit-test command using a writable temporary directory.
- [ ] Inspect `git diff --check`, the complete source diff, and `git status --short` to confirm unrelated user changes are untouched.
- [ ] Report exact commands/results and any pre-existing or environment-specific failures.
- [ ] Do not stage, commit, push, or open a pull request unless the user separately authorizes those actions.
