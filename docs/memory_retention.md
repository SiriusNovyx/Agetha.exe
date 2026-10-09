# Conversation and memory retention

This document records the existing F12 contract. It distinguishes behavior
proved by code and tests from a proposed migration. It changes no runtime policy.

For the searchable archive, JSONL on disk owns durable records. Its cache must
include existing readable records when appending a new summary, including an
append before the first search after startup. `log_longterm_memory` invalidates
cache metadata under its existing lock before an append attempt. The next read
reconstructs the cache from disk through `_refresh_cache_unlocked`. This gives
one function ownership of populated cache entries and fixes hidden search
records, including after temporary read failure. It changes no file format,
expiry, or migration policy.

## Current ownership

| State | Owner | Retention | Restart behavior |
| --- | --- | --- | --- |
| `AIEngine._history` | `AIEngine._record`, `_build_history` | Bounded RAM turns; the configured history limit and request profile constrain context. | Constructing an engine starts empty. No disk transcript reconstructs it. |
| `conversation.txt` | `AIEngine._init`, `_record` | Current-engine diagnostic log of recorded turns; no within-session disk cap. | `_init` resets it; `_record` appends. The engine does not read it into prompts or memory. |
| `memory/soul.md` | `memory_system.load_soul` | Editable persistent identity; a modification-time cache avoids repeated reads. | The first normal memory prompt loads it. Missing/empty identity produces defaults. |
| `memory/episodic_memory.json` | `memory_system.log_memory` | Persistent recent events with an oldest-first cap and summary-length limit. | Normal prompts, recap, view commands, and dream generation read existing entries. |
| `memory/longterm_memory.jsonl` | `memory_search.log_longterm_memory` | Append-only searchable AI summaries when the long-term flag permits writing. | Search/recap load existing records; a modification-time/size cache accelerates reads. |
| `memory/memory.txt` | `AIEngine._save_memory`, `_load_memories` | Append-only compatibility summaries; prompts use a bounded tail when the memory module is unavailable. | Bytes survive. An available memory module does not inject legacy-only entries or migrate them. |

The engine initializes a session recap flag at startup. The first eligible
prompt reads recent episodic and archive summaries into the current request,
then consumes that flag. This reconstructs context from summaries, not prior
dialogue turns. Provider messages also contain few-shot examples and the current
request; they are not equivalent to `_history`.

`CompanionApp._graceful_shutdown` joins workers and destroys the UI. It does not
flush uncondensed `_history` to a durable transcript or convert memory stores.
Recorded files already contain the event-time writes. A restart can therefore
discard recent dialogue that never produced a summary. No full durable
conversation-history store exists in the current implementation.

The current-session interpretation of `conversation.txt` agrees with
`docs/module_reference.md` and its two write paths. A durable transcript would
change the product's privacy, size, retention, and history-reconstruction
contract. Do not make that change by substituting append for startup reset.

## Write contracts

`AIEngine._record` keeps bounded live turns. On eviction, it extracts up to six
user snippets into a condensed summary. It appends that summary to `memory.txt`
and, if the memory module is available, logs an episodic entry tagged `system`
with a `[condensed history]` prefix. It does not archive condensation in JSONL.
The source comment identifies the flat write as backward compatibility for
installations without `memory_system.py`.

`AIEngine._persist_profile_memory` permits summaries only for eligible direct
`normal`/`fast_user` requests, excluding the existing protected command types
and internal events. One eligible provider summary produces:

- One flat compatibility record.
- One episodic record when the memory module is available.
- One searchable archive record when `ENABLE_LONGTERM_MEMORY` permits it.

These related records have distinct consumers and length limits: the provider
candidate can retain 1,000 characters, the episodic default retains 300, and the
archive retains 500. Removing flat writes would lose compatibility context and
potentially unique suffixes. Disabling the archive retains the other two writes.

Repeated equal provider summaries produce repeated records. The implementation
supplies no stable event identity or cross-store transaction. Equal text does
not prove two requests represent the same event. Do not introduce text-based
deduplication as a maintenance shortcut.

The missing-module fallback catches import failure in `ai_engine`. It uses the
existing legacy tail for memory context and continues writing compatible
summaries. If the archive module remains available and enabled, its writes can
continue too. Test an actual import failure when assessing this contract;
patching only the availability Boolean leaves other imports such as recap able
to reach the installed module.

## Readers and commands

- `_build_prompt` and `_estimate_request_tokens` read the legacy tail only on
  the missing-module path. `_load_memories` reads the complete file before
  selecting its tail; the bound limits prompt context, not file I/O or storage.
- Normal memory prompts use `memory_system.build_system_prompt`; fast prompts
  use a bounded episodic subset. Request profiles can exclude memory/recap.
- `view_memory` and the continuation recent-memory reader use episodic entries.
  `clear_memory` clears all or selected episodic entries and keeps the soul.
  It does not clear the archive, legacy text, or conversation log.
- `search_memory` and its continuation reader search JSONL. Search-result
  formatting preserves the existing untrusted-context boundary.
- Session recap combines recent episodic entries with recent archive entries.
  A related fact can appear twice in recap and once in the system prompt;
  prompts do not merge or deduplicate them.
- Dream generation reads episodic entries and searches the archive, then writes
  a separate generated dream record. It is not a writer of these source stores.
- Senses and Medic expose metadata/status for memory boundaries. They do not
  migrate legacy text or make it searchable.

The `_extract_memory_from_user` helper has no runtime caller in this checkout.
Its presence does not prove direct user statements follow an additional write
path. The memory-module usage example mentions OCR writes; source inspection
found no separate production OCR caller of `log_memory`.

## Behavior classifications

| Classification | Behavior and reason |
| --- | --- |
| A: Preserve exactly | Bounded RAM history, session-log reset, profile restrictions, missing-module fallback, configurable episodic expiry, explicit episodic clear, and gated archive writes. Code, commands, and tests agree on their current contracts. |
| B: Preserve semantics, simplify implementation | Document the distinct roles. Any later owner changes must retain readers, restart behavior, context bounds, and failure handling. No runtime simplification has been approved by this document. |
| C: Deprecate with a compatibility period | Treat new flat writes as a future deprecation candidate after deciding support for old/module-missing installations. Preserve reads and current writes during that decision. |
| D: Remove only after migration/export | Existing `memory.txt` and unique text absent from capped episodic/archive records. Preserve original bytes; require a reviewed migration/export and rollback before removing storage or readers. |
| E: Unknown, needs a decision | Whether to retain diagnostic logs across sessions; whether condensation should enter the archive; treatment of repeated/related facts; eventual length limits; and whether legacy-only facts should join normal prompts. |

## Proposed canonical model, pending migration

Use RAM as the sole live dialogue-context owner. Keep the diagnostic log
session-scoped unless a separate transcript-retention decision changes it.
Keep `soul.md` as the identity owner.

For installations with the searchable archive enabled, prefer the existing
JSONL archive as the canonical durable AI-summary owner and episodic memory as
the bounded recent-context projection. Preserve module-missing legacy fallback
through a compatibility period. This is a recommendation, not the current
unified implementation: condensation never enters JSONL, limits differ, and
legacy text can contain unique information. An archive-disabled installation
also needs its existing recent/fallback retention semantics.

A migration must first inventory each installation's originals and decide
which facts/events belong in the archive. It must preserve original files and
text without guessing timestamps or sources, account for differing limits,
distinguish repeat events from duplicated mirrors, and provide rollback. Do not
silently funnel 1,000-character legacy text into a 500-character archive field.
No conversion or new migration format is defined here.

Retiring flat writes also requires a decision on the supported missing-module
fallback. Retiring episodic storage would change view/clear commands, prompt
recency, configured expiry, recap, and dreams. Preserve both boundaries until
those consumers have a reviewed transition.

The F12 investigation stops before those migrations and product decisions.
It neither removes persistent user data nor claims that the existing stores
have become one canonical archive.

## Verification

`tests/test_memory_retention_contract.py` contains 18 disposable contract tests
for the requested startup/restart/history/store-availability cases, plus
different summary limits, archive-disabled behavior, and appending to an existing
archive before its first search, including temporary read denial. It reuses the inspected
synthetic fixture from `tests/test_memory_retention_characterization.py` without
inheriting its tests. It verifies RAM/log expiry, actual on-disk record counts,
preserved bytes/metadata, and the relevant prompt/search owner.

The F12 engineering report outside the repository supplies exact executed
results, the reference inventory, protected-data verification, and remaining
risks. Existing characterization tests continue to record unresolved behavior,
including corruption followed by episodic replacement. This document does not
expand F08's task/stats repair to other stores.
