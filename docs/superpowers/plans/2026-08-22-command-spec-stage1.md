# CommandSpec Stage 1 Implementation Plan

## Scope and stop condition

Implement only Stage 1 from the approved architecture plan. Stop after local
verification and report the remaining compatibility shims and duplication. Do
not begin provider extraction.

## Task 1: Lock the desired registry contract with failing tests

Files:

- Add `tests/test_command_specs.py`.

Steps:

1. Test the wished-for `CommandSpec`, `RiskTier`, `DispatchKind`, immutable
   `COMMAND_SPECS`, and lookup APIs.
2. Assert the exact 69-command compatibility inventory using hand-authored
   literals.
3. Assert every command has explicit risk, capability, origin eligibility, and
   coherent core/handler fields.
4. Assert duplicate specifications fail deterministically.
5. Run the new module and observe RED because the registry does not exist.

## Task 2: Add the canonical static registry

Files:

- Add `agetha/commands/specs.py`.
- Update `tests/test_command_specs.py`.

Steps:

1. Implement immutable enums, dataclass, validation/indexing, lookup, and the
   explicit current-command specifications.
2. Preserve current base tiers and capabilities, resolving only the known
   registry drift documented in the design.
3. Run `tests.test_command_specs` to GREEN.

## Task 3: Derive compatibility views test-first

Files:

- Update `tests/test_command_specs.py`.
- Update `agetha/core/ai_engine.py`.
- Update `agetha/commands/command_guard.py`.
- Update `agetha/core/capabilities.py`.

Steps:

1. Add failing assertions that `VALID_COMMANDS`, `TIER_MAP`, and
   `capability_for_command()` derive from the canonical registry and that
   unknown commands remain fail-closed.
2. Replace the independent valid-command and tier maps with derived views.
3. Replace capability classification maps with a specification lookup while
   retaining the unknown-command fallback.
4. Preserve dynamic `force_close` guard behavior and test it directly.
5. Run command-spec, capability, guard-adjacent, parser, and Fast Mode tests.

## Task 4: Enforce dispatch and handler invariants test-first

Files:

- Update `tests/test_command_specs.py`.
- Update `agetha/commands/command_handlers.py`.

Steps:

1. Add failing tests for bidirectional handler/spec consistency and duplicate
   handler registration.
2. Make duplicate registration raise instead of overwrite.
3. Resolve dispatch through CommandSpec and preserve explicit core handling.
4. Apply specification origin eligibility only after the central origin policy;
   it may narrow but never broaden authority.
5. Preserve Computer Use/direct-user messaging and deep-OCR privacy behavior.
6. Run ambient, continuation, capability, Computer Use, Unicode typing, and
   generation/effect-time authorization regressions.

## Task 5: Generate mechanical documentation and CI drift check

Files:

- Add `agetha/commands/generate_command_matrix.py`.
- Add `docs/generated/command_matrix.md`.
- Update `.github/workflows/ci.yml`.
- Update `docs/development.md`.
- Update `docs/architecture.md`.
- Update `docs/module_reference.md`.
- Update `tests/test_command_specs.py`.

Steps:

1. Add a failing test for deterministic rendering and stale-file detection.
2. Implement renderer plus `--check` without third-party dependencies.
3. Generate and check in the matrix.
4. Replace the manual tier snapshot/add-command checklist with the new
   canonical workflow and generated-reference link.
5. Add the checker to CI without making runtime consume the document.

## Task 6: Verification and handoff

1. Run the focused command specification module.
2. Run capability, ambient, continuation, provider recovery, Fast Mode runtime,
   Computer Use integration, Unicode typing, and phase command-wiring suites.
3. Run the full test suite with the prepared x64 venv.
4. Run `python -m compileall -q agetha`, shared imports, generator `--check`,
   and `git diff --check`.
5. Inspect the scoped diff, file/line counts, and worktree status.
6. Stop and report; do not commit, push, open a PR, or begin Stage 2 without
   separate authorization.
