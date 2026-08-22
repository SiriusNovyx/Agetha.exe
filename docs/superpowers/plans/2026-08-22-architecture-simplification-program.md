# Architecture Simplification Implementation Plan

## Stage 1.1 checkpoint — complete

- Added registry-construction regressions for contradictory handler keys and
  invalid policy enum values.
- Moved those failures to `build_command_specs()` so malformed specifications
  cannot survive until handler import or documentation generation.
- Retained the tiny handler registration check and every procedural authority
  boundary.
- Evidence: 26 command-spec tests; 182 focused security/authority tests; 1,072
  full tests with 8 skips; compile, shared imports, generated matrix, and diff
  checks passed under the prepared x64 Python 3.13 environment.

## Stage 2 — provider boundary

1. Add failing adapter contract tests for Groq, OpenRouter, and Ollama request
   shaping, streaming, usage, and error conversion.
2. Move transport clients and Groq-specific request option construction behind
   explicit adapters while AIEngine retains routing and response semantics.
3. Add a small router only for route/key/fallback mechanics that remain
   duplicated after transport extraction.
4. Preserve provider authorization checks around every init, request, retry,
   key change, and streamed publication boundary.
5. Verify provider, repair, Compact, planner, continuation, and full suites.

### Checkpoint — complete

- Added explicit Groq, OpenRouter, and Ollama adapters plus a thin request
  router. Removed the two transport clients from `ai_engine.py`.
- Groq model normalization, GPT-OSS reasoning effort, JSON Object Mode, SDK
  construction, and OpenRouter/Ollama request transports now terminate in the
  provider package. Transport error policy moved there with compatibility
  re-exports from `core.provider_protocol`; response parsing status remains a
  core semantic.
- Retained `_LocalOllamaClient`, `_OpenRouterClient`, and provider-protocol
  imports as derived compatibility aliases. Retained retry/key/fallback control
  in AIEngine because its current termination paths are coupled to Agetha UI,
  repair, authorization, and exactly-once publication; moving them in this
  stage would make the router a semantic policy owner.
- Evidence: 5 new adapter contract tests; 111 focused provider, Compact,
  recovery, and planner tests; 1,077 full tests with 8 skips; compile, shared
  imports, generated matrix, and diff checks passed.

## Stage 3 — handler domain split

### Checkpoint — complete

- Moved handler registration and shared dispatch helpers into small explicit
  support modules, while preserving compatibility exports from
  `command_handlers.py`.
- Grouped file/local-OS, system, web-context, and memory/presentation handlers
  by dependency domain. Each domain registers directly with the one validated
  handler registry; duplicate and specification-mismatched bindings still fail
  during import.
- Kept continuation-requery handlers and dynamic window/typing safety flows in
  `command_handlers.py`. They share dispatch recursion, UI confirmation,
  target revalidation, and generation-bound effect checks; extracting them now
  would require a wider callback interface and obscure the security ordering.
- Reduced `command_handlers.py` by roughly one thousand lines without moving
  procedural safety into metadata or introducing a manager/decorator layer.
- Evidence: 166 focused handler, capability, Compact, generation, typing,
  Computer Use, and continuation tests; 1,081 full tests with 8 skips; compile,
  generated matrix, import, and diff checks passed.

## Stage 4 — config internal separation

### Checkpoint — complete

- Extracted durable same-directory atomic writes into `agetha.config.io` and
  pure comment/order/unknown-key-preserving document edits into
  `agetha.config.transactions`.
- Kept `app_config.py` as the compatibility facade and transaction coordinator.
  Its wrapper deliberately preserves established monkeypatch seams used to
  verify pre/post-replace failures and directory-fsync behavior.
- Compact fail-closed persistence and Fast Mode transactions remain separate
  procedural security domains.
- Evidence: 115 focused config, atomic persistence, Compact, Fast Mode, and
  capability tests with 5 skips; 1,084 full tests with 8 skips; compile and
  import checks passed.

## Stage 5 — conservative SettingSpec metadata

### Checkpoint — complete

- Added immutable `SettingSpec` metadata for eleven stable typed/ranged or enum
  settings. Runtime defaults, strict ranges, and enum validation for this subset
  now derive from the registry.
- Kept the human-readable default template and added consistency tests against
  it. All other settings remain under existing explicit validation/property
  logic until their machine facts are genuinely stable.
- Excluded Compact, Fast Mode, and secret settings so transactional and security
  behavior cannot migrate into metadata.
- Evidence: 180 focused settings, profile, Dashboard, OCR, typing, and security
  tests with 5 skips; 1,088 full tests with 8 skips.

## Stage 6 — Full-only lifecycle composition

### Checkpoint — intentionally skipped

- The post-Stage-5 boundary remains tightly coupled to owner-thread screen
  initialization, continuation target invalidation, Computer Use cancellation,
  observation cleanup, and capability-gated scheduling.
- A separate runtime would require a wide host interface and would hide the
  existing fail-closed order: publish transition/invalidate generation first,
  then stop services. The explicit `CompanionApp` methods are clearer.

## Stage 7 — small internal Protocols

### Checkpoint — intentionally skipped

- Handler registry/support modules already expose the small reusable scheduling
  and authorization helpers. Domain handlers still use legitimate domain state
  from `CompanionApp`; a useful Protocol would currently mirror too much of the
  class and reduce neither blast radius nor understanding cost.

## Stage 8 — generated facts and architecture documentation

### Checkpoint — complete

- Kept the command matrix downstream of `COMMAND_SPECS` and added a generated
  settings reference for the canonical SettingSpec subset. CI checks both files
  for drift; runtime imports neither document.
- Updated architecture, development workflow, runtime flow, and module ownership
  references for provider adapters, handler domains, config internals, and the
  conservative settings registry.
- Kept design rationale in hand-written documents and limited generated output
  to registry facts.

## Later stages

After each checkpoint, re-audit the new tree before following the approved
handler, config, setting metadata, lifecycle, Protocol, and documentation
stages. Skip any extraction whose interface would be larger or less explicit
than the coupling it replaces.
