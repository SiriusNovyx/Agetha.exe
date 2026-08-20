# GPT-OSS Provider Migration and Bounded Response Recovery

**Date:** 2026-08-20  
**Issues:** [#31](https://github.com/SiriusNovyx/Agetha.exe/issues/31), [#33](https://github.com/SiriusNovyx/Agetha.exe/issues/33)  
**Status:** Approved

## Goals

1. Replace the decommissioned Groq `llama-3.3-70b-versatile` runtime path with `openai/gpt-oss-120b` without changing Agetha's provider-neutral JSON command envelope or `CommandGuard` authority boundary.
2. Apply GPT-OSS request features deliberately: profile-based reasoning effort and JSON Object Mode for command-envelope responses.
3. Classify provider failures so permanent model/request failures do not enter retry storms, while authentication, rate-limit, and transient failures keep bounded, appropriate recovery.
4. Keep intentional `idle`, malformed JSON, schema failure, and unsupported command outcomes distinguishable.
5. Permit exactly one format-repair request only for an explicit direct-user request, with no duplicated conversation history, episodic memory, or command side effects.

## Non-goals

- Replacing the JSON command envelope with provider-native tools.
- Weakening or bypassing `CommandGuard`.
- Adding repair cycles for ambient, OCR, web/document, file-drop, reminder, terminal-sentinel, tool-result, or other untrusted-context-only traffic.
- Adding dependencies or changing OpenRouter/Ollama request semantics.

## Current-system evidence

- `agetha/core/ai_engine.py` currently contains the retired model in `GROQ_MODELS` and mutates that global list with a configured model. Model rotation therefore can retain or revisit a retired runtime identifier.
- Groq failures are primarily divided into rate-limit versus everything else. Model retirement, invalid requests, authentication failures, and transient transport/service failures can therefore take the same rotation path.
- `_parse()` currently maps malformed JSON, invalid top-level shapes, missing commands, unsupported commands, and intentional `idle` to indistinguishable `idle` results.
- `query()` and `query_streaming()` persist/record provider output before applying the user-facing idle fallback. A repair attempt inserted after those side effects would duplicate state.
- Request origin already exists in `agetha/core/request_context.py`, but the origin is not forwarded into the AI engine. Repair eligibility must use this trusted local origin instead of guessing from prompt text.

## P0 design: provider migration

### Model selection

Add a small provider-policy module with:

- `DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"`.
- A frozen set of known retired Groq model identifiers.
- `normalize_groq_model()`, which maps a blank or known-retired configured value to the default and preserves an explicitly configured, non-retired model.

The AI engine holds one normalized Groq model per instance. Groq API-key rotation rotates keys only; it does not rotate through a process-global model list. This removes the retired runtime fallback while preserving explicit custom model configuration.

Canonical configuration and documentation are updated to GPT-OSS. Runtime normalization remains necessary for existing user configurations that still name the retired model.

### Request profile mapping

For GPT-OSS models, command-envelope calls use JSON Object Mode and map existing request profiles to reasoning effort:

| Profile | Reasoning effort |
|---|---|
| `fast_ambient` | `low` |
| `fast_command` | `low` |
| `fast_user` | `low` |
| `normal` | `medium` |
| `fast_tool_result` | `medium` |
| `tool_continuation` | `medium` |
| `deep_analysis` | `high` |

Unknown profiles default to `medium`. Non-GPT-OSS Groq models, OpenRouter, and Ollama receive no GPT-OSS-specific options.

JSON Object Mode is used instead of Groq Strict Structured Outputs because the existing command schema is intentionally broad and Strict Structured Outputs does not support the streaming/tool combination required by all current paths. Local schema validation and `CommandGuard` remain authoritative.

### Error classification and retry policy

Provider errors are classified using a preserved HTTP status code plus bounded message inspection:

| Kind | Examples | Recovery |
|---|---|---|
| Rate limit | HTTP 429 | Existing bounded backoff/key rotation |
| Authentication | HTTP 401/403 | Move to another configured key; never retry the same key indefinitely |
| Permanent model | retired/missing model, model-specific HTTP 400/404 | Fail over once to the configured alternate provider; no Groq model/key retry storm |
| Permanent request | other HTTP 400 | Deterministic failure/failover; no unchanged retry |
| Transient | timeout, connection error, HTTP 408/409/425/5xx | Existing bounded retry/failover |

OpenRouter's adapter preserves its HTTP status in a typed exception rather than flattening it into an opaque string.

## P1 design: response lifecycle and repair

### Parse states

Every parsed result carries an internal `provider_response_status` value:

- `ok`: valid envelope, including intentional `{"command":"idle"}`.
- `repaired`: safely normalized transport formatting, such as a valid JSON object inside a Markdown fence.
- `malformed_json`: invalid JSON with no safe complete conversational recovery.
- `schema_failure`: valid JSON with the wrong top-level/envelope shape or invalid required fields.
- `unsupported_command`: a syntactically valid envelope naming a command outside the supported set.

Malformed-output salvage is deliberately narrow. A complete `idle` or a complete `speak` with usable text may be recovered for compatibility. Mutating or capability-bearing commands are never reconstructed from malformed JSON.

### Trusted repair eligibility

`main.py` forwards the normalized `RequestOrigin` into `query()` and `query_streaming()`. A format repair is eligible only when `request_origin == "user"`. The displayed message, context contents, request profile, and OCR/web/document text never grant repair authority.

### One-attempt state machine

1. Perform the normal provider call.
2. Parse locally before any history, memory, dispatch, or other side effect.
3. If parsing succeeds, continue the existing safety and dispatch lifecycle.
4. If parsing fails and this is an explicit user request and no repair has been attempted, append a local system repair instruction and make exactly one more provider call.
5. If that second response fails, or the request origin is not `user`, return a deterministic explicit failure result. Direct-user failures speak a short retry message; non-user failures remain non-executing `idle` results carrying the failure status.
6. Record/persist only the final successful response, or one deterministic direct-user failure entry. Never record the malformed intermediate response.

The repair system instruction identifies the local validation class and asks for one valid JSON object in the existing envelope. It does not quote untrusted context again, broaden authority, or request a different user action.

### Side-effect invariants

- Provider calls: at most two for an eligible direct-user format failure; one for ineligible origins.
- Conversation history: one logical assistant response at most.
- Episodic memory: one successful final response at most; zero for final parse failures.
- Command dispatch: only after a successful final parse and existing safety checks.
- Intentional idle: retains the existing personality fallback behavior for direct user traffic.
- Failure states: never enter the random idle fallback.

## Test strategy

Regression tests are written and observed failing before production changes.

- Pure provider-policy tests cover retired-model normalization, reasoning options, JSON Object Mode, and error classification.
- AI-engine tests cover Groq request arguments and non-Groq neutrality.
- Parser tests cover valid idle, fenced JSON, safe partial conversational recovery, malformed mutating output, wrong schema, and unsupported commands.
- Query lifecycle tests cover direct-user success-after-repair, failure-after-one-repair, no repair for every untrusted origin class, and exactly-once history/memory behavior in streaming and non-streaming paths.
- Existing fast-mode, command-guard, quality-of-life, and broader unit suites provide regression coverage.

## Operational references

- Groq deprecation notice: <https://console.groq.com/docs/deprecations>
- GPT-OSS 120B model capabilities: <https://console.groq.com/docs/model/openai/gpt-oss-120b>
- Groq API reasoning and response format: <https://console.groq.com/docs/api-reference>
- Groq Structured Outputs constraints: <https://console.groq.com/docs/structured-outputs>
- Groq error guidance: <https://console.groq.com/docs/errors>
