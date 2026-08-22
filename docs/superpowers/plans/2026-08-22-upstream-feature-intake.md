# Upstream feature intake implementation plan

1. Add failing provider tests for Gemini registration, request conversion,
   streaming, usage, errors, model/config behavior, structured requests,
   fallback, repair boundaries, and authority neutrality.
2. Implement the Gemini transport/adapter and the smallest explicit AIEngine
   route integration. Run provider, repair, planner, Compact, and authority
   suites.
3. Add failing config migration tests for preservation, idempotence, secrets,
   atomic failure, Compact state, and Fast Mode ownership. Implement startup-only
   canonical setting insertion and regenerate the settings reference.
4. Add failing UI status tests, remove inferred quota wording, and retain
   truthful provider/key/model status.
5. Benchmark `PrintWindow` against the existing MSS path on available Windows
   targets without changing production capture. Completed: blank/uniform MSS
   frames were reproduced while `PrintWindow` returned visible detail, so a
   bounded optional fallback was added behind existing target policy.
6. Update architecture, development, module, runtime, README, and environment
   documentation for implemented behavior.
7. Run focused provider/config/security tests, generated-reference checks,
   Windows/Linux-compatible suites, full unittest discovery, compile checks,
   diff checks, and artifact/status audit. Do not commit, push, merge, or create
   a PR.
