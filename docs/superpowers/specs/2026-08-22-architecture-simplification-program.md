# Architecture Simplification Program

## Objective

Reduce change amplification and implicit coupling without changing Agetha's
behavior, trust boundaries, or platform contracts. Each stage must be useful on
its own, must retain compatibility at its public boundary, and must pass the
full test suite before the next boundary is reconsidered.

## Locked ownership

- `CommandSpec` owns invocation-independent command facts only.
- `AIEngine` owns Agetha prompts, origins and profiles, parsing and bounded
  repair, history/memory publication, continuation, and command envelopes.
- Provider adapters own transport, request shapes, provider options, model/key
  mechanics, and provider error conversion. A small router may coordinate
  routes and fallback but owns no UI, command policy, memory, or authority.
- Command dispatch owns ordering; domain handlers own effects; `CommandGuard`
  and `CapabilityController` retain dynamic and generation-bound authority.
- Configuration internals may move behind `agetha.app_config`, which remains a
  compatibility facade. Compact persistence and Fast Mode transactions remain
  separate security domains.
- A Full-only runtime, if still justified, composes lifecycles only. It is not a
  policy authority.

## Stages

1. Harden the completed command registry at construction boundaries.
2. Extract explicit provider adapters, then introduce routing only where it
   removes duplicated mechanics.
3. Split handlers by dependency domain while preserving dispatch ordering.
4. Split configuration internals behind the existing facade.
5. Canonicalize only stable mechanical setting facts with `SettingSpec`.
6. Reassess Full-only lifecycle composition and extract it only if clearer.
7. Add narrow Protocols only for remaining real implicit host coupling.
8. Regenerate mechanical references and synchronize human architecture docs.

## Stage checkpoint contract

Every stage records focused and security regressions, the full suite,
compile/import validation, generated-reference checks, diff inspection, sources
of truth removed, compatibility retained, and the reason the next stage still
makes sense. No stage may expand authority through metadata or move dynamic
security decisions into a declarative registry.
