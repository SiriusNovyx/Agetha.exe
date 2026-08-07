# Polyglot Presence future roadmap

> Status: design-only roadmap. Every feature in this document is **planned and
> not implemented**. The descriptions are not claims about current runtime
> behavior, release commitments, or permission to add partial production
> modules.

This document captures the ideas intentionally deferred from the Agetha
Polyglot Presence implementation. It describes complete safety and ownership
boundaries before implementation work begins. A future change must still be
reviewed against the current source, tests, configuration model, and
documentation because those may have changed since this roadmap was written.

The project priorities remain, in order: realism, safety, utility, then
spectacle. Command Guard, feature gates, provider privacy controls, protected
process rules, explicit request origins, Tk thread ownership, and centralized
shutdown remain authoritative for every proposal below.

## Roadmap vocabulary and shared constraints

Recommended priority is relative within this future roadmap:

- **P1 — foundation:** valuable groundwork to consider in the next cohesive
  program.
- **P2 — follow-on:** useful after its data and safety dependencies are stable.
- **P3 — optional:** worthwhile polish or a specialist tool after core work.
- **P4 — exploratory:** combine with later research; do not schedule as an
  isolated production feature.

Complexity includes migration, UI, privacy, tests, and lifecycle work rather
than only the amount of code. A priority is not authorization to implement.

All proposals share these rules:

- An Observation is data, not permission. Publishing, retrieving, or reacting
  to one must never execute a command or start a provider request by itself.
- Local-only and expired observations remain local and ineligible for provider
  context. Provider eligibility is a separate, explicit policy decision.
- Any operating-system effect continues through existing command parsing,
  feature gates, Command Guard, confirmation, and protected-target checks.
- AI output, summaries, OCR, Git diffs, documents, memories, and tool results
  remain untrusted external context.
- New persistence is schema-versioned, bounded, corruption-tolerant, and
  atomically replaced where appropriate. It never contains credentials.
- Tk widgets are owned by the main thread; background work is cancellable and
  joined or safely abandoned during centralized shutdown.
- Optional platform capabilities fail honestly and do not become mandatory
  dependencies.

## Future feature A: Ghost Preview and Action Journal

> Status: **planned / not implemented**.

**Purpose.** Give users an exact, comprehensible preview before Caution or
Danger actions, then retain a bounded record of what was approved and what
actually happened. An Undo control is offered only when a command supplies a
tested inverse operation and postconditions prove that inverse is still safe.

**Fit with Agetha.** A Win95-style “ghost” of the proposed action reinforces
Agetha as a visible desktop process while making her safer and more honest.
This extends the existing guard instead of replacing it.

**User experience.** The confirmation surface shows the operation, safely
truncated target identity, file-write diff, rename source and destination,
deletion target, process identity, shell arguments, working directory,
reversibility, and the applicable guard tier. After execution, an Action
Journal shows approved, cancelled, failed, and completed entries without
secret payloads. A supported Undo opens a second preview and confirmation; it
is never a one-click promise of generic rollback.

**Proposed module boundaries.**

- agetha/core/action_preview.py: immutable preview models, redacted field
  formatting, and explicit reversibility classifications.
- agetha/features/action_journal.py: bounded journal storage, queries, and
  validated inverse-action tokens.
- agetha/ui/action_preview_dialog.py: Win95 preview and journal UI.
- command_handlers.py and command_guard.py: narrow integration points only;
  Command Guard remains the final authority.

**Persistence format.** A schema-versioned, bounded JSONL journal under the
application data directory. Records contain command type, tier, timestamps,
sanitized target metadata, outcome, and an opaque inverse token when supported.
Full file content, clipboard text, credentials, raw shell output, and secret
arguments are excluded. Inverse tokens expire and are invalidated when target
identity or state changes.

**Privacy risks.** Paths, process arguments, diffs, and window titles can reveal
names, projects, or secrets. Redaction must occur before both display and
persistence, with a more restrictive journal representation than the
interactive preview.

**Safety risks.** A misleading preview, stale target, or false “reversible”
label could cause harm. Execution must revalidate the preview inputs and target.
Undo is itself a guarded action and may be refused when files, process state, or
permissions have changed.

**Platform limitations.** Process identity, elevation, file metadata, and
reversible trash behavior differ by operating system and filesystem. Unsupported
details are marked unavailable rather than inferred.

**Configuration.** Proposed settings are ENABLE_GHOST_PREVIEW,
ENABLE_ACTION_JOURNAL, ACTION_JOURNAL_MAX_ENTRIES, and
ACTION_PREVIEW_DIFF_MAX_CHARS. Safe defaults keep previews enabled for guarded
actions, use a small journal bound, and never reduce an existing confirmation
tier.

**Testing strategy.** Unit-test redaction, truncation, tier retention,
reversibility classification, expiry, bounded storage, corruption fallback,
and compare-before-undo behavior. Integration tests verify preview-to-dispatch
target revalidation, Command Guard invocation, cancellation, shutdown, and
refusal of unsupported rollback.

**Implementation complexity.** High. Correct per-command preview schemas and
honest inverse operations require deliberate command-by-command work.

**Recommended priority.** P1 — foundation, beginning with read-only previews
and journal outcomes before any Undo support.

**Observation Bus dependencies.** It may consume sanitized guarded-action
lifecycle observations and publish journal-availability indicators. New action
observation kinds would carry metadata only. The bus neither confirms the
action nor holds executable arguments.

**What must never be automated.** Never auto-confirm, lower a guard tier, replay
an action, infer that an irreversible action is reversible, run Undo without a
fresh guard decision, or store enough secret material to recreate a sensitive
command.

## Future feature B: Agetha Lens

> Status: **planned / not implemented**.

**Purpose.** Let the user inspect the exact categories of sanitized context
prepared for a remote or local provider before it is sent.

**Fit with Agetha.** Agetha already assembles identity, message, OCR, memory,
task, emotion, dream, document, web, and tool context. The Lens makes that
boundary visible and controllable without pretending that model prompts are
harmless.

**User experience.** Before an eligible request, a compact inspector lists User
message, OCR excerpt, Relevant memories, Tasks, Emotion summary, Dream recall,
Document context, Web context, and Tool result. Each section shows its source,
bounded size, trust label, and destination. The user may remove optional
sections and then send or cancel. Required safety instructions and hard privacy
redaction cannot be disabled. The view shows the already-redacted
representation, never a hidden raw version.

**Proposed module boundaries.**

- agetha/core/context_manifest.py: typed section manifests, trust labels,
  required/optional flags, and size accounting.
- agetha/ui/agetha_lens.py: inspection and optional-section controls.
- AI request construction: exposes a manifest at the final pre-provider
  boundary without duplicating provider clients or prompt policy.

**Persistence format.** No context content is persisted by default. Optional
preferences may store only section names the user usually excludes, schema
version, and provider category. Per-request manifests live in memory and are
cleared after completion or cancellation.

**Privacy risks.** The inspector itself can expose sensitive context to shoulder
surfers or screenshots. It must reuse application redaction, avoid raw API keys
and paths, obscure sensitive excerpts, and close or clear promptly.

**Safety risks.** The UI could imply that unchecked optional sections remove
content they do not own, or that visible sections are completely risk-free.
The request builder remains authoritative and must fail closed if the manifest
no longer matches the payload.

**Platform limitations.** The core behavior is platform-neutral. Small displays
and screen readers require a scrollable, keyboard-accessible summary. Provider
token accounting may be approximate and must be labeled as such.

**Configuration.** Proposed settings are ENABLE_AGETHA_LENS and
AGETHA_LENS_MODE with values off, on-demand, or before-remote. No setting may
disable deterministic redaction.

**Testing strategy.** Verify every provider-bound section has a manifest entry,
optional removal changes the payload, required sections cannot be removed,
redaction precedes display, secrets never appear, cancellation sends nothing,
and stale manifests fail closed. Test keyboard accessibility and shutdown
cancellation without real provider calls.

**Implementation complexity.** High because prompt composition must expose
structured provenance without forking provider behavior.

**Recommended priority.** P1 — foundation for later privacy-routing and
provenance work.

**Observation Bus dependencies.** None for the core request inspector. A local
observation may announce that a request is awaiting review, but it contains no
prompt text and cannot cause sending.

**What must never be automated.** Never silently send after a timeout, reveal
pre-redaction content, allow hard-redaction opt-out, treat the preview as
provider consent for future requests, or persist full request payloads.

## Future feature C: Memory Receipts

> Status: **planned / not implemented**.

**Purpose.** Make each durable memory explain where it came from, how certain it
is, whether it was stated or inferred, and how it has been corrected.

**Fit with Agetha.** Agetha’s relationship realism depends on memory, while
trust depends on correction and forgetting. Receipts turn opaque recollection
into a user-auditable relationship record.

**User experience.** A memory card shows source category, first observed, last
confirmed, confidence, direct statement versus inference, temporary or
permanent status, and a bounded correction history. Controls are Confirm,
Correct, Forget, Show source, Mark temporary, and Never infer this category.
“Show source” reveals only the minimum safe supporting excerpt and explains
when the raw source was intentionally not retained.

**Proposed module boundaries.**

- agetha/core/memory_receipts.py: immutable provenance and correction models,
  confidence policy, and receipt validation.
- Existing memory stores: narrow adapters or schema migration, not a second
  competing memory system.
- agetha/ui/memory_receipts.py: browse, filter, correct, and forget surfaces.

**Persistence format.** Versioned receipt objects referenced by stable memory
IDs. Fields include source category and sanitized reference, timestamps,
bounded confidence, inference flag, retention class, and append-only correction
events. Raw OCR, complete chats, private documents, and credentials are not
copied into receipts. Rewrites and deletions are atomic.

**Privacy risks.** Provenance may reconstruct sensitive history even without
the memory text. Source labels and excerpts require redaction, retention limits,
and user deletion that removes both memory and receipt indexes.

**Safety risks.** Incorrect provenance or hidden inference can manipulate user
trust. Inferred memories must be visibly distinct and lower confidence.
“Never infer” preferences are authoritative policy, not prompt suggestions.

**Platform limitations.** Platform-neutral, but timestamps must use explicit
timezone-aware values and imports from older stores may have unknown provenance.
Unknown is displayed honestly rather than fabricated.

**Configuration.** Proposed settings include ENABLE_MEMORY_RECEIPTS,
MEMORY_RECEIPT_HISTORY_LIMIT, and MEMORY_INFERENCE_DEFAULT with a conservative
off or ask policy. Per-category “never infer” choices are local structured
preferences.

**Testing strategy.** Test schema migration, immutable history, confidence
clamping, direct-versus-inferred labels, correction and forget cascades,
temporary expiry, redaction, corrupted records, category opt-outs, concurrent
updates, and atomic recovery.

**Implementation complexity.** High because it changes durable schemas and
needs migration and deletion guarantees.

**Recommended priority.** P1 — establish before expanding long-term memory or
daybook features.

**Observation Bus dependencies.** It may consume explicit memory-candidate,
user-confirmation, and correction observations after dedicated kinds and
eligibility rules exist. Local-only observation payloads remain local and are
not copied wholesale into memory.

**What must never be automated.** Never convert low-confidence inference into a
direct statement, mark a memory confirmed without user action, ignore a
category opt-out, resurrect forgotten content from correction history, or
retain raw private history merely to support “Show source.”

## Future feature D: Memory SCANDISK

> Status: **planned / not implemented**.

**Purpose.** Provide a Win95-inspired maintenance tool that diagnoses memory
stores and applies only selected, previewed repairs.

**Fit with Agetha.** The SCANDISK metaphor suits Agetha’s desktop character and
makes invisible data hygiene understandable without turning personality into
an excuse for silent rewriting.

**User experience.** A read-only Scan classifies records as Valid, Parse error,
Duplicate, Near duplicate, Possible contradiction, Low-confidence memory, Old
observation, or Unused memory. The workflow is Scan, Preview, Backup, Confirm,
Apply selected changes, Verify. Each proposed repair explains which records it
touches and whether it can be restored from the backup.

**Proposed module boundaries.**

- agetha/features/memory_scandisk.py: scanners, typed findings, repair plans,
  backup manifests, and verification.
- Existing memory modules: store-specific read and validated-write adapters.
- agetha/ui/memory_scandisk.py: progress, selection, preview, and restore UI.

**Persistence format.** Scan findings are ephemeral unless the user explicitly
exports a sanitized report. Before changes, a versioned backup directory in the
application data location stores only the affected memory files plus checksums
and a manifest. Retention is bounded and backup deletion is explicit.

**Privacy risks.** A report can aggregate sensitive memories. UI details,
exports, and logs must be redacted and bounded; backup access should follow the
same local permissions as the original stores.

**Safety risks.** Similar text is not necessarily duplicate or contradictory.
Automated classification is advisory. Repairs use stable IDs, compare current
checksums before writing, create a backup first, and abort on concurrent change.

**Platform limitations.** Core scanning is platform-neutral. Atomic replace and
file-lock behavior varies by filesystem; network or read-only locations may
support scan but not repair.

**Configuration.** Proposed settings are ENABLE_MEMORY_SCANDISK,
MEMORY_SCANDISK_BACKUP_LIMIT, and MEMORY_SCANDISK_AUTO_SCAN. Auto-scan, if
offered, defaults off and remains diagnostic-only.

**Testing strategy.** Use fixtures for every category, false-positive near
duplicates, contradictory timestamps, invalid JSON/JSONL, legacy versions,
concurrent changes, backup failure, atomic replace failure, restore, verify,
retention, cancellation, and idempotent re-scan.

**Implementation complexity.** High due to multiple store formats, migrations,
backups, and repair correctness.

**Recommended priority.** P2 — after Memory Receipts supplies stable IDs and
provenance.

**Observation Bus dependencies.** Not required to scan. It may publish a
sanitized local-only “maintenance recommended/completed” observation and
consume memory-store health metadata, never memory bodies.

**What must never be automated.** Never silently rewrite the complete memory
store, merge a possible duplicate, resolve a contradiction, delete a backup,
or upload scan contents. Never let a provider-generated repair plan write
directly to storage.

## Future feature E: Workspace Capsules

> Status: **planned / not implemented**.

**Purpose.** Save named, bounded workspace profiles so the user can restore a
useful desktop context without capturing private session state.

**Fit with Agetha.** Capsules let Agetha “remember the room” around a task:
approved applications, folders, window positions, tasks, provider preference,
OCR policy, voice state, and presence mode.

**User experience.** The user creates or edits a capsule, reviews every included
field, and explicitly chooses Restore. Restoration first shows a Ghost Preview
of applications to open, folders to reveal, windows to move, and settings to
apply. Unsupported or missing items are skipped with honest status. Closing
existing applications is a separate, guarded request.

**Proposed module boundaries.**

- agetha/features/workspace_capsules.py: versioned capsule models, validation,
  capture policy, diffing, and restore plans.
- agetha/platform/workspace_restore.py: capability adapters for application
  launch and window placement.
- agetha/ui/workspace_capsules.py: editor, preview, and restore progress.

**Persistence format.** One schema-versioned JSON file per capsule under the
application data directory, named by an opaque ID rather than user content.
Records contain approved executable identities, user-selected folder
references, normalized display-relative geometry, task IDs, and policy names.
They never include browser session tokens, passwords, document contents,
unsaved buffers, secrets, raw provider keys, or executable command strings.

**Privacy risks.** Application names, folder references, and project titles can
reveal activity. The feature needs per-field inclusion, redacted previews,
export warnings, and full deletion.

**Safety risks.** Launching the wrong executable, opening a sensitive folder,
or moving windows during active work is disruptive. Resolve executable
identity safely, revalidate paths, apply guarded actions individually, and stop
on focus, drag, presentation, or shutdown conflicts.

**Platform limitations.** Window discovery and geometry restoration vary
widely. Wayland may forbid global placement; virtual desktops, DPI changes, and
removed monitors require best-effort clamping. Provider preference restores a
policy name only when currently configured; it never carries credentials.

**Configuration.** Proposed settings include ENABLE_WORKSPACE_CAPSULES,
WORKSPACE_CAPSULE_MAX_COUNT, WORKSPACE_CAPSULE_ALLOW_WINDOW_MOVE, and
WORKSPACE_CAPSULE_RESTORE_OCR_POLICY. Defaults require preview and confirmation.

**Testing strategy.** Test schema validation, forbidden-field rejection,
path/executable identity, missing apps, monitor and DPI changes, geometry
clamping, Wayland refusal, partial restore, guard invocation, cancellation,
concurrent edits, atomic persistence, and secret scanning.

**Implementation complexity.** High, especially cross-platform restoration.

**Recommended priority.** P2 — useful after Ghost Preview and stable capability
reporting exist.

**Observation Bus dependencies.** It may use APP_FOCUSED, APP_UNFOCUSED,
PRESENTATION_MODE, and FULLSCREEN_ACTIVE as local restore constraints.
Observations can pause a plan but never launch or move anything.

**What must never be automated.** Never capture tokens, passwords, document
contents, unsaved buffers, or secrets; never restore dangerous commands; never
close apps, move windows, switch providers, or change OCR privacy policy
without preview and the normal guarded path.

## Future feature F: Agetha Rituals

> Status: **planned / not implemented**.

**Purpose.** Offer a safe local event-rule system for small, understandable
habits without becoming a script runner or hidden automation engine.

**Fit with Agetha.** Rituals support the feeling that Agetha notices routines
and responds locally while keeping automation narrow, visible, and reversible.

**User experience.** A rule editor offers fixed trigger and action pickers,
plain-language previews, a test mode, cooldown, quiet-hours behavior, and a
recent-fire log. Allowed triggers are Time window, Application opened or
closed, Idle or active transition, Battery threshold, Task due, Provider
unavailable, Network category, Manual shortcut, and Agetha mood. Automatic
actions are limited to Speak, Show popup, Change mood, Display task, Enter
quiet mode, and Open dashboard.

**Proposed module boundaries.**

- agetha/features/rituals.py: typed trigger/action schema, validation,
  cooldowns, and deterministic local evaluator.
- agetha/ui/ritual_editor.py: constrained editor, simulator, and audit view.
- Existing presence and UI controllers: receive approved local reactions;
  command dispatch remains separate.

**Persistence format.** A schema-versioned JSON file containing rule IDs,
enumerated trigger/action types, bounded parameters, enabled state, cooldown,
and last-fired monotonic-safe metadata. No source code, shell strings, arbitrary
arguments, raw observation payloads, or credentials.

**Privacy risks.** Rule names and application triggers can reveal schedules or
habits. Keep them local, offer per-rule deletion/export, and avoid persisting
window titles unless the user explicitly selects a safe bounded pattern.

**Safety risks.** Trigger loops, notification spam, voice during quiet moments,
and disguised OS actions are the main risks. Validate against a closed schema,
apply global and per-rule cooldowns, cap cascades, consult Presence Etiquette,
and reject unknown triggers/actions.

**Platform limitations.** Application lifecycle and network category detection
are not uniformly available. Unsupported triggers remain disabled with an
explanation; no invasive monitor is added merely to satisfy a rule.

**Configuration.** Proposed settings are ENABLE_RITUALS, RITUAL_MAX_RULES,
RITUAL_GLOBAL_COOLDOWN_SEC, and RITUAL_ALLOW_VOICE. The feature and voice action
default off until explicitly enabled.

**Testing strategy.** Test every enumerated trigger/action, schema rejection,
deduplication, cooldown, quiet hours, presence suppression, clock changes,
restart recovery, loop caps, unknown fields, corruption, shutdown, and proof
that rules cannot reach command handlers or provider clients.

**Implementation complexity.** Medium to high because deterministic behavior
and anti-loop guarantees matter more than UI size.

**Recommended priority.** P2 — after the Observation Bus and Presence Etiquette
have stable contracts.

**Observation Bus dependencies.** The bus is the primary typed trigger source.
The ritual evaluator accepts only explicitly mapped kinds and sanitized
metadata. Expired, local-only, low-confidence, or ineligible observations are
filtered before rule matching.

**What must never be automated.** Never execute arbitrary Python, PowerShell,
shell, executable hooks, file operations, process control, provider calls, or
unlisted actions. Any future OS-affecting action must leave Rituals and enter
normal parsing, feature-gate, Command Guard, and confirmation flow.

## Future feature G: Spatial Body Language

> Status: **planned / not implemented**.

**Purpose.** Add restrained, local movement that communicates attention and
social awareness without obstructing the user.

**Fit with Agetha.** Movement is part of Agetha’s desktop-body metaphor. A
shared policy could let her rest beside an error, retreat during rapid typing,
move closer when addressed, and back away after dismissal.

**User experience.** Motion is brief, subtle, previewable in settings, and
bounded to visible work areas. Agetha remembers a preferred resting corner,
avoids known active text regions, and explains when movement is unavailable.
Dragging always wins. Presentation, game/fullscreen, minimized, sleeping, and
shutdown states suppress motion.

**Proposed module boundaries.**

- agetha/ui/spatial_body_language.py: candidate geometry, exclusion rectangles,
  cooldown policy, and typed move intents.
- Existing MoodMotionController: sole executor and geometry owner.
- Screen/window capability adapters: provide bounded rectangles and state, not
  raw OCR content.

**Persistence format.** Store only a display-relative resting corner,
per-display preference, intensity setting, and last-dismiss cooldown. Do not
store screenshots, text-region contents, or a history of user window titles.

**Privacy risks.** Window layout can reveal application use. Process geometry
should remain ephemeral, and logs should record only reason codes and success,
not titles or coordinates unless diagnostic logging is explicitly enabled and
sanitized.

**Safety risks.** Motion can cover controls, steal focus, cause nausea, fight
the user’s drag, or produce an endless geometry loop. One controller owns
geometry; cooldowns, distance caps, cancellation, stable-rest verification, and
reduced-motion settings are mandatory.

**Platform limitations.** Wayland and some window managers restrict placement;
multi-monitor DPI and reserved taskbar areas complicate geometry. Avoidance of
active text is best-effort and must never be advertised as perfect.

**Configuration.** Proposed settings include ENABLE_SPATIAL_BODY_LANGUAGE,
SPATIAL_MOTION_INTENSITY, SPATIAL_MOTION_COOLDOWN_SEC,
SPATIAL_PREFERRED_CORNER, and REDUCED_MOTION. Reduced motion and existing
presentation settings override all spectacle settings.

**Testing strategy.** Pure geometry tests cover screen edges, multiple
monitors, DPI, exclusion overlap, distance caps, and stable corners. Controller
tests cover drag, minimize, fullscreen, rapid typing, dismissal, cooldown,
concurrent move requests, focus retention, cancellation, and shutdown.

**Implementation complexity.** Medium, with high UI-lifecycle sensitivity.

**Recommended priority.** P3 — optional realism after Presence Etiquette is
proven nonintrusive.

**Observation Bus dependencies.** Consumes APP_FOCUSED, FULLSCREEN_ACTIVE,
PRESENTATION_MODE, RAPID_TYPING, activity, and explicit dismissal observations.
It converts eligible events to move intents only after local presence policy.

**What must never be automated.** Never move during presentation, active drag,
minimize, secure desktop, or shutdown; never click, type, focus-steal, inspect
text merely to enable movement, or fight another geometry owner.

## Future feature H: Agetha Daybook

> Status: **planned / not implemented**.

**Purpose.** Create a local daily narrative from safe structured events, giving
Agetha continuity without recording a surveillance diary.

**Fit with Agetha.** A restrained daybook supports relationship realism and
reflection while preserving the project’s privacy boundary between useful
memory and raw activity capture.

**User experience.** The user sees a dated entry built from completed tasks,
confirmed milestones, successful or failed guarded actions, broad interaction
counts, mood arc, errors resolved, and whether a dream occurred. Each item
shows its category and source receipt. Users may edit, delete, disable a source
category, or generate an optional reflective paragraph from already-sanitized
facts.

**Proposed module boundaries.**

- agetha/features/daybook.py: daily aggregation, safe event schema, deterministic
  summaries, retention, and export.
- agetha/ui/daybook.py: calendar, entry detail, source controls, edit/delete.
- Optional provider reflection: an explicit request through normal AI ownership
  and Agetha Lens, not part of background aggregation.

**Persistence format.** Versioned per-day JSON or JSONL records under the
application data directory. Store event categories, counts, safe labels,
receipt IDs, and user-edited narrative. Never store raw OCR, full
conversations, browser history, passwords, private document contents, secret
paths, complete diffs, or provider credentials.

**Privacy risks.** Even broad events can reveal routines when combined.
Retention controls, category opt-outs, local-only defaults, safe export, and
complete date-range deletion are required.

**Safety risks.** A generated narrative can overstate facts or turn an inference
into a milestone. Deterministic facts remain visibly separate from optional
reflection, which is labeled generated and never updates memory without an
explicit receipt flow.

**Platform limitations.** Platform-neutral. Clock and timezone changes require
stable event timestamps and deliberate day-boundary handling.

**Configuration.** Proposed settings are ENABLE_DAYBOOK,
DAYBOOK_RETENTION_DAYS, DAYBOOK_INCLUDED_CATEGORIES, and
DAYBOOK_ALLOW_PROVIDER_REFLECTION. Collection and provider reflection are
separate opt-ins.

**Testing strategy.** Verify category allowlists, day boundaries and timezone
changes, count aggregation, source receipts, retention, deletion, redaction,
forbidden-field rejection, corruption recovery, concurrent events, deterministic
output, provider opt-in, and no background provider requests.

**Implementation complexity.** Medium after safe event provenance exists.

**Recommended priority.** P2 — after Memory Receipts and action outcomes expose
sanitized structured sources.

**Observation Bus dependencies.** Consumes only explicitly daybook-eligible
observations after expiry, sensitivity, local-only, and dedup rules. It stores a
minimal projection rather than the observation payload. A nightly boundary
does not itself authorize a provider request.

**What must never be automated.** Never log raw screens, chats, browsing,
documents, secrets, or paths; never publish or upload the daybook; never treat
generated prose as confirmed memory; never send a daily provider request merely
because a date changed.

## Future feature I: Retro Git Companion

> Status: **planned / not implemented**.

**Purpose.** Explain real repository changes and draft useful Git or pull
request text from Git’s own data rather than OCR.

**Fit with Agetha.** A retro status surface is useful to developers and matches
the desktop aesthetic, while strict read-only behavior avoids repeating
destructive source-control surprises.

**User experience.** The user explicitly selects a repository. Agetha runs
read-only status and diff inspection, then offers a conventional commit
message, detailed commit message, Agetha-flavored commentary, PR summary, or
risk summary. The UI distinguishes working-tree, staged, untracked, and binary
changes and shows exactly which bounded diff was analyzed. Draft text is copied
or displayed; mutation stays in the user’s Git client.

**Proposed module boundaries.**

- agetha/platform/git_reader.py: validated repository root and fixed-argument
  read-only calls for git status --short, git diff, and git diff --cached.
- agetha/features/git_companion.py: bounded diff model, secret redaction,
  summary request preparation, and result types.
- agetha/ui/git_companion.py: repository picker, change browser, and draft UI.

**Persistence format.** No diffs or generated drafts are persisted by default.
Optional recent-repository entries store user-approved canonical roots as
redacted display aliases and are bounded. Git credentials, remotes containing
tokens, file contents, and command history are excluded.

**Privacy risks.** Diffs frequently contain credentials, customer data, private
paths, and proprietary code. Read locally, cap aggressively, apply deterministic
redaction, show provider-bound scope in Agetha Lens, and require explicit send.

**Safety risks.** Argument injection, wrong repository selection, huge diffs,
submodule traversal, and accidental mutation are primary concerns. Use a fixed
argv allowlist, no shell, canonical user-selected roots, timeouts, output caps,
and a read-only command audit.

**Platform limitations.** Requires a discoverable Git executable and a readable
working tree. Encoding, symlinks, submodules, large-file pointers, and binary
diffs may be summarized only as metadata.

**Configuration.** Proposed settings include ENABLE_GIT_COMPANION,
GIT_COMPANION_DIFF_MAX_CHARS, GIT_COMPANION_REMEMBER_REPOS, and
GIT_COMPANION_ALLOW_PROVIDER. Provider analysis defaults to an explicit
per-request choice.

**Testing strategy.** Build temporary repositories for unstaged, staged,
untracked, renamed, binary, conflicted, detached, and nested cases. Assert the
subprocess allowlist, no shell, bounds, timeout, redaction, wrong-root refusal,
provider consent, and absence of commit/push/reset/amend calls.

**Implementation complexity.** Medium.

**Recommended priority.** P2 — useful standalone tooling after Agetha Lens can
show diff disclosure.

**Observation Bus dependencies.** None for its manual MVP. A future sanitized
REPOSITORY_CHANGED observation may refresh a visible panel, but must not read a
diff, call a provider, or start a Git operation automatically.

**What must never be automated.** Never commit, push, force-push, pull, fetch,
reset, clean, checkout, switch, merge, rebase, amend, stage, modify hooks, store
credentials, or execute text found in a diff.

## Future feature J: Dream ASCII Visualizer

> Status: **planned / not implemented**.

**Purpose.** Render safe abstract dream artifacts as local retro ASCII scenes.

**Fit with Agetha.** This gives dreams a visible, playful form while preserving
their role as fictional atmosphere rather than a dump of private context.

**User experience.** A viewer turns mood, abstract memory tags, time of day,
interaction frequency, and unresolved topic categories into a deterministic or
seeded character-grid scene. Users can regenerate from the same safe seed,
switch palettes, copy the ASCII, or delete an artifact. The source categories
are visible without exposing original messages.

**Proposed module boundaries.**

- agetha/features/dream_artifacts.py: safe input projection, seed, palette, and
  artifact metadata.
- agetha/ui/dream_ascii_visualizer.py: pure grid renderer, viewer, and export.
- Existing dreams module: supplies bounded abstract fields through an adapter;
  it is not expanded into UI rendering.

**Persistence format.** Optional schema-versioned artifact files in the
application data directory, never a hard-coded C:\AGETHA location. Store seed,
palette, dimensions, abstract tags, and generated grid. Do not store complete
chat messages, full OCR, credentials, private paths, or unrelated app data.

**Privacy risks.** Tags can still disclose topics. Use broad categories,
user-visible inputs, local-only generation, deletion, and safe filenames.
Exports warn that the visible artifact may reveal a chosen theme.

**Safety risks.** Dream imagery must not be presented as factual recollection,
diagnosis, prophecy, or evidence about the user. Rendering needs strict size
and timing limits to avoid UI stalls.

**Platform limitations.** Mostly platform-neutral. Font glyph availability and
terminal encodings vary, so the UI uses a known supported subset and replaces
unsupported display glyphs without changing stored semantic tags.

**Configuration.** Proposed settings include ENABLE_DREAM_ASCII_VISUALIZER,
DREAM_ARTIFACT_PERSIST, DREAM_ARTIFACT_MAX_COUNT, and
DREAM_ASCII_REDUCED_FLASH. Persistence defaults off.

**Testing strategy.** Test safe field projection, forbidden-source rejection,
deterministic seeds, dimensions and output bounds, Unicode display fallback,
retention, deletion, corrupt files, export, reduced sensory behavior, and proof
that rendering makes no provider call.

**Implementation complexity.** Low to medium.

**Recommended priority.** P3 — optional spectacle after privacy-oriented memory
work.

**Observation Bus dependencies.** May consume a local-only DREAM_OCCURRED
summary or explicit “open artifact” request. It never consumes raw observation
metadata and never converts every dream event into an automatic provider turn.

**What must never be automated.** Never ingest raw chats, OCR, files,
credentials, or paths; never publish artifacts, diagnose the user, rewrite
memories, or generate continuously in the background.

## Future feature K: Virtual Profile Disk

> Status: **planned / not implemented**.

**Purpose.** Package a bounded persona, knowledge set, and safe preferences in a
portable .agetha-profile format represented in the UI as a floppy disk.

**Fit with Agetha.** Swappable profile disks match the retro identity and allow
deliberate modes without giving imported content control over the application.

**User experience.** Import first opens a manifest inspector showing persona,
knowledge files, requested settings, provenance, size, and rejected entries.
The user can mount a validated profile temporarily, preview its effective
changes, unmount it, or copy it into local storage. Permanent-memory import is a
separate, explicit receipt workflow. The UI uses a safe archive or directory,
not a mountable disk image.

**Proposed module boundaries.**

- agetha/core/profile_format.py: manifest schema, archive/path validation,
  content type allowlist, size limits, and signatures/checksums if later needed.
- agetha/features/profile_manager.py: temporary overlay lifecycle, import,
  export, and conflict resolution.
- agetha/ui/profile_disk.py: inspector, mount state, and preview.

**Persistence format.** A versioned .agetha-profile package may contain
manifest.json, persona.txt, a bounded knowledge directory, settings.json, and
README.txt. Import copies validated data into an application-data profile
directory. Reject absolute paths, traversal, links, devices, hidden executables,
oversized archives, unexpected types, and nested archives.

**Privacy risks.** Exports may include user-written persona or knowledge, and
imports may contain tracking text or prompt injection. Show exact contents and
source, sanitize filenames, keep remote loading out of the MVP, and never copy
secrets into a package.

**Safety risks.** A profile could try to bypass system rules through prompt
content or malicious archive structure. System safety policy always outranks
persona, imported text remains untrusted, and settings use an explicit allowlist
that excludes security and privacy controls.

**Platform limitations.** The logical format is platform-neutral, but path
rules, case sensitivity, archive behavior, and filename encodings differ.
Validation uses platform-independent package paths and safe extraction.

**Configuration.** Proposed settings are ENABLE_PROFILE_DISKS,
PROFILE_IMPORT_MAX_BYTES, PROFILE_KNOWLEDGE_MAX_FILES, and
PROFILE_ALLOW_PERSISTENT_IMPORT. Persistent import defaults off.

**Testing strategy.** Test valid round trips plus traversal, absolute paths,
symlinks/reparse points, devices, duplicate/case-colliding names, compression
bombs, oversized files, unknown manifest versions, executable disguises,
prompt injection, forbidden settings, corrupt packages, cleanup, and unmount.

**Implementation complexity.** High because safe archive parsing and policy
overlays are security-sensitive.

**Recommended priority.** P3 — only after a stable context inspector and memory
receipt model.

**Observation Bus dependencies.** Optional local-only PROFILE_MOUNTED and
PROFILE_UNMOUNTED lifecycle observations may refresh UI or presence state.
Imported package events do not authorize settings, memory, or commands.

**What must never be automated.** Profiles must never contain or execute code,
bypass Command Guard, change confirmation tiers or protected-process rules,
access API keys, silently enable screen/provider transmission, overwrite
permanent memory, install dependencies, or self-mount from an untrusted source.

## Future feature L: Retro Hardware Monitor

> Status: **planned / not implemented**.

**Purpose.** Show advisory CPU, RAM, battery, disk, and temperature health with
honest capability reporting and restrained notifications.

**Fit with Agetha.** A small retro monitor gives Agetha grounded awareness of
her host computer and useful, calm reactions without pretending to diagnose
hardware.

**User experience.** Gauges distinguish current value, sustained condition, and
Unavailable. Messages use evidence-based language such as “CPU usage has
remained high,” “Available memory is low,” or “Temperature unavailable on this
device.” A detail view names the source and sampling window. Notifications use
hysteresis, cooldown, and Presence Etiquette.

**Proposed module boundaries.**

- agetha/features/hardware_monitor.py: typed samples, reliability flags,
  rolling windows, thresholds, hysteresis, and cooldown.
- agetha/platform/hardware_metrics.py: optional platform adapters and capability
  detection.
- agetha/ui/hardware_monitor.py: gauges and history summaries.

**Persistence format.** Prefer bounded in-memory rolling samples. Persist only
last-notified category/time and user thresholds when needed. Do not retain
serial numbers, device IDs, process lists, raw sensor dumps, or long-term usage
history by default.

**Privacy risks.** Hardware identifiers and process-level usage can fingerprint
the device or expose activity. The proposal uses aggregate metrics only and
does not send them to providers by default.

**Safety risks.** Unreliable temperatures or transient spikes can cause alarm.
Every sample carries provenance and reliability; thresholds require sustained
windows; no claim of overheating, thermal throttling, battery failure, or disk
failure is made without reliable evidence.

**Platform limitations.** Temperature and battery data may be absent or require
unsupported APIs. A future optional library cannot become mandatory without
approval. Unsupported sensors remain unavailable; no privileged helper is
recommended.

**Configuration.** Proposed settings include ENABLE_HARDWARE_MONITOR,
HARDWARE_SAMPLE_INTERVAL_SEC, sustained CPU/RAM/disk/battery thresholds,
HARDWARE_NOTIFICATION_COOLDOWN_SEC, and HARDWARE_ALLOW_TEMPERATURE. Defaults
are advisory and conservative.

**Testing strategy.** Inject fake clocks and metric providers to test rolling
windows, reliability, unavailable values, hysteresis, cooldown, recovery,
clock changes, threshold clamping, provider failure, shutdown, and absence of
raw identifiers. UI tests verify “unavailable” is not rendered as zero.

**Implementation complexity.** Medium, with platform adapter uncertainty.

**Recommended priority.** P2 — useful once Observation Bus and Senses capability
reporting are stable.

**Observation Bus dependencies.** Publishes bounded BATTERY_LOW and future
resource-pressure observations only after sustained local confirmation.
Presence policy decides notification eligibility. Metrics are not provider
eligible unless an explicit user request asks about them.

**What must never be automated.** Never kill processes, change power settings,
clear caches, delete files, run privileged tools, infer thermal throttling from
CPU alone, upload metrics, or spam repeated notifications.

## Future feature M: Dual-Brain Privacy Router

> Status: **planned / not implemented**.

**Purpose.** Optionally use a local Ollama model to reduce already-redacted
context before a sanitized subset is sent to a remote provider.

**Fit with Agetha.** The design can reduce remote disclosure while retaining
the existing local/remote provider choices. It complements, but never replaces,
deterministic privacy boundaries.

**User experience.** Agetha Lens displays the route:

    Raw local context
    → deterministic redaction
    → optional local Ollama summary
    → deterministic re-redaction and validation
    → sanitized remote-provider context

The user sees which stages are available, what categories remain, and whether
the local stage failed. On failure, the application either uses the ordinary
deterministically redacted path with explicit consent or cancels according to
the configured policy; it never silently sends raw context.

**Proposed module boundaries.**

- agetha/core/privacy_router.py: route policy, typed stage results, bounds,
  failure behavior, and local-only enforcement.
- Existing external-context redaction: authoritative preprocessing and
  post-summary re-redaction.
- Existing provider clients: unchanged execution owners.
- Agetha Lens: route inspection and per-request consent.

**Persistence format.** Persist policy names and bounded audit metadata such as
stage, model identifier, sizes, and outcome. Raw inputs, local summaries,
remote payloads, credentials, and model transcripts remain in memory only and
are cleared after the request.

**Privacy risks.** A local summary can retain, reconstruct, or invent sensitive
details. Deterministic redaction happens before and after it, sensitivity and
local-only flags remain authoritative, and remote scope is visible before send.

**Safety risks.** Treating a model as a privacy filter creates false assurance.
Local output is untrusted, bounded, schema-checked, re-redacted, and unable to
change commands, system policy, request origin, or provider permissions.

**Platform limitations.** Ollama may be absent, stopped, slow, or unable to fit
the configured model. The feature remains optional, uses timeouts and
cancellation, and accurately reports unavailable capability.

**Configuration.** Proposed settings include ENABLE_DUAL_BRAIN_ROUTER,
DUAL_BRAIN_LOCAL_MODEL, DUAL_BRAIN_FAILURE_POLICY, and
DUAL_BRAIN_MAX_SUMMARY_CHARS. Model selection contains no credentials.
Remote provider and privacy settings remain separately authoritative.

**Testing strategy.** Test deterministic pre/post redaction, local unavailable
and timeout paths, malicious or oversized summaries, prompt injection,
local-only exclusion, manifest/payload parity, cancellation, clearing
ephemeral buffers, no secret logs, and assurance that the same command-safety
path applies after routing.

**Implementation complexity.** High.

**Recommended priority.** P2 — privacy-positive but only after Agetha Lens and
structured context manifests.

**Observation Bus dependencies.** The router may receive only observations
already marked provider-context eligible by a separate policy. It honors
local_only, sensitivity, expiry, confidence, and origin; event existence never
starts routing.

**What must never be automated.** Never make the local model the sole privacy
layer, weaken deterministic redaction, route local-only data remotely, switch
providers or enable remote sending, retry with raw context, accept model output
as a command, or install/start Ollama with elevated privileges.

## Future feature N: Dial-up and Floppy Soundscapes

> Status: **planned / not implemented**.

**Purpose.** Add optional, short local audio cues for memory search, explicit
web retrieval, and UI feedback.

**Fit with Agetha.** Subtle floppy, modem, and PC-speaker-inspired cues reinforce
the retro desktop identity when they communicate real state rather than delaying
work.

**User experience.** A small settings preview lets users hear each cue, choose
volume, disable categories, set reduced-sensory mode, and mute immediately.
A floppy tick may accompany a local memory search; a brief modem-style cue may
mark an explicit web connection; PC-speaker-style sounds may acknowledge UI
actions. Real operations begin immediately and never wait for audio.

**Proposed module boundaries.**

- agetha/ui/soundscape_controller.py: asset registry, category policy,
  cooldown, volume, cancellation, and single-owner playback.
- Existing audio coordination: arbitrates with voice, bleeps, mute, and
  shutdown rather than adding another unmanaged playback stack.

**Persistence format.** Configuration only: enabled categories, volume,
reduced-sensory choice, and mute state. No activity history or audio recording.
Bundled assets retain documented provenance and licensing.

**Privacy risks.** Audible cues can reveal that the user searched memory or
connected to the web. Categories default off, honor quiet/presentation state,
and can be disabled independently.

**Safety risks.** Sudden, loud, repetitive, or flashing-associated feedback can
harm or distress users. Enforce a volume ceiling, short duration, cooldown,
single playback, reduced-sensory mode, and immediate stop on mute/shutdown.

**Platform limitations.** Audio backends, device availability, and focus policy
vary. Missing audio is a silent no-op with honest status and never blocks the
underlying operation.

**Configuration.** Proposed settings are ENABLE_RETRO_SOUNDSCAPES,
RETRO_SOUNDS_VOLUME, RETRO_SOUNDS_REDUCED_SENSORY,
RETRO_SOUNDS_COOLDOWN_MS, and per-category toggles. All cues default disabled.

**Testing strategy.** Test disabled defaults, clamped volume, category mapping,
cooldown, concurrency, voice arbitration, quiet hours, reduced-sensory mode,
missing devices/assets, immediate operation start, stop, and shutdown. Verify
asset licensing metadata.

**Implementation complexity.** Low to medium.

**Recommended priority.** P3 — optional spectacle after functional and privacy
work.

**Observation Bus dependencies.** Optional sanitized lifecycle signals may
select a cue, but direct controller calls for known UI operations are simpler.
No cue needs raw event text, and an observation never enables audio against
settings or presence policy.

**What must never be automated.** Never record audio, play continuously or
excessively loudly, bypass mute/quiet/reduced-sensory settings, download
untrusted sounds, copy copyrighted audio without permission, announce sensitive
content, or delay a real operation for a sound.

## Future feature O: Expanded Dream and Ambient Presence

> Status: **planned / not implemented**.

**Purpose.** Define how future dream artifacts, Daybook, Presence Etiquette,
Spatial Body Language, and observations could produce a coherent ambient
presence without unnecessary provider requests.

**Fit with Agetha.** This is the long-term integration layer for a character who
appears aware of time and context while remaining quiet, local-first, and under
the user’s control.

**User experience.** Safe events first affect local state: a mood hint,
subtitles, a queued daybook fact, a dream-artifact seed, or a bounded movement
intent. Presence Etiquette chooses speak, wait, queue, quiet indicator, or drop.
The user can inspect the reason and source category. Provider-generated
reflection occurs only after an explicit request or a separately configured,
clearly disclosed schedule that opens a review surface before sending.

**Proposed module boundaries.**

- agetha/core/ambient_presence.py: typed local reaction eligibility and
  orchestration; it owns no provider and executes no command.
- Existing Observation Bus: event transport, deduplication, expiry, and
  shutdown.
- Presence Etiquette: sole local interruption policy.
- Planned Daybook, dream artifact, and spatial modules: consume minimal typed
  projections through their own feature gates.
- Existing AI request owner: handles only explicit eligible turns with a
  distinguishable origin.

**Persistence format.** No central “ambient transcript.” Each owning feature
persists only its safe projection: daybook categories, abstract dream seeds, or
preferences. The orchestrator may keep bounded in-memory queues and
non-content cooldown timestamps. Cross-feature references use opaque IDs.

**Privacy risks.** Combining harmless observations can reconstruct a detailed
behavior profile. Apply purpose limitation per consumer, minimize metadata,
keep local-only as the default, expire aggressively, provide per-source
controls, and avoid a unified long-term event archive.

**Safety risks.** Cascading reactions can create spam, focus theft, motion
conflicts, or provider cost. One observation has a bounded reaction budget;
deduplication, global cooldown, Presence Etiquette, feature gates, and
centralized lifecycle ownership apply before any surface appears.

**Platform limitations.** Fullscreen, media, idle, and window signals vary by
desktop environment. Missing capabilities reduce behavior rather than trigger
more invasive monitoring. Wayland restrictions and accessibility preferences
remain authoritative.

**Configuration.** Proposed settings include ENABLE_EXPANDED_AMBIENT_PRESENCE,
AMBIENT_REACTION_BUDGET_PER_HOUR, AMBIENT_EVENT_RETENTION_SEC, and separate
toggles for daybook, dream, motion, subtitle, voice, and provider reflection.
Local reaction does not imply provider consent.

**Testing strategy.** Scenario tests cover event storms, deduplication, expiry,
quiet hours, fullscreen, rapid typing, dismissal backoff, sleep, minimize,
offline provider, local-only and sensitive observations, reaction budgets,
cross-feature failure, cancellation, and shutdown. Assert zero provider calls
for ordinary ambient events and zero command dispatch from observations.

**Implementation complexity.** High and integration-heavy.

**Recommended priority.** P4 — exploratory umbrella after the individual
foundations are stable and measured.

**Observation Bus dependencies.** Fundamental. Every input is typed, bounded,
deduplicated, expiring, and filtered separately for local reaction,
notification, provider context, memory, and guarded action eligibility. The bus
is transport, not an orchestrator or authority.

**What must never be automated.** Never send a provider request merely because
an event occurred, create a permanent surveillance log, convert local-only
events into remote context, execute commands, modify files, steal focus, speak
through presentation/quiet policy, move during user-controlled geometry, or
allow one feature’s opt-in to enable another.

## Suggested dependency sequence

This sequence is design guidance, not an implementation plan:

| Stage | Features | Reason |
|---|---|---|
| Privacy and accountability foundations | A, B, C | Preview actions, expose provider context, and make memory provenance inspectable |
| Safe maintenance and local utility | D, E, F, H, I, L, M | Build on receipts, previews, capability reporting, and explicit context boundaries |
| Optional expression | G, J, N | Add movement, artifacts, and sound only after interruption and accessibility policy is proven |
| Integrated ambient research | O | Connect mature local features without creating provider, privacy, or automation shortcuts |

Before any feature moves out of this document, its implementation plan must name
the current owner for UI scheduling, workers, persistence, provider calls,
command dispatch, configuration, and shutdown. It must also include tests that
prove the “must never” statements, not only happy-path demonstrations.
