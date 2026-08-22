# Development and validation guide

This guide turns the architecture map into concrete edit and test checklists.
Use the narrowest relevant section, then inspect only the files named there.

## Local setup and launch

### Windows guided setup

Run:

```powershell
.\Medic_Checker.bat
```

Medic is a setup/repair launcher, not merely a read-only test. It can install a
compatible Python, create or rebuild `venv`, install packages, create local
state and shortcuts, check releases, and launch Agetha. Review its prompts when
you do not want those changes.

### Direct launch after setup

```powershell
.\venv\Scripts\python.exe .\main.py
```

or, with an already prepared interpreter:

```powershell
python .\main.py
```

Direct launch still creates missing config/runtime state, but it does not run the
full Medic repair workflow.

### Linux direct setup

Use a Python 3.13 virtual environment and install the project dependencies with
the package manager/tooling appropriate for the distribution. Tk and native
Tesseract are distribution packages on many Linux systems. Then launch:

```bash
python main.py
```

Linux window management and Xorg/Wayland capture policy are documented in
[Linux desktop support](linux_support.md). Run without `sudo`; never use
`xhost +` or hardcode a GNOME XWayland authority filename.

Medic Checker is Windows-specific. Linux Fast Mode recovery uses the Python CLI
documented in [Fast Mode security and recovery](fast_mode_security.md).

### Platform support policy

Supported development/release targets are Windows 10/11 x64, Windows 11
ARM64/Snapdragon through x64 Python under Prism, and Linux desktop environments
through the existing Linux paths. Shared and platform-focused tests run on
Windows and Linux in CI. Windows-only integrations must stay feature-gated and
Linux must degrade safely when an optional desktop utility is absent.

macOS is retired and unsupported as of v5.5.5. Historical macOS branches may
remain in source control, but macOS failures are outside release acceptance and
must not be described as tested or supported.

### Required local inputs

- Python 3.10-3.14; Medic currently recommends Python 3.13.
- The separately supplied `assets/` pack. It is ignored by Git in this checkout.
- At least one provider: Groq/Gemini/OpenRouter credentials in `.env`, or a configured
  local Ollama model.
- Native Tesseract for standard screen reading unless that feature/check is
  intentionally disabled.

Do not commit `.env`, generated memory, conversation logs, or third-party media.

## Configuration and secrets

### Precedence and ownership

```text
app_config.DEFAULT_CONFIG
        ↓ overridden by
config.txt (non-secret user settings)
        ↓ overridden by
.env (allowed nonempty secrets only)
        ↓ exposed as
AppSettings typed/clamped properties
```

Rules:

- Use `get_settings()`; do not parse `config.txt` from each feature.
- Treat `config.txt` as active user state, not documentation of defaults.
- Put `GROQ_API_KEY_*`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, and
  `UNLIMITED_OCR_API_KEY` only in `.env`.
- Boolean syntax accepts `yes`, `true`, `1`, or `on` and the corresponding
  disabled forms.
- Invalid typed values are discarded and safe defaults/clamps apply.
- `get_settings()` is cached. Several values are also copied to module constants
  during import, so do not promise hot reload unless the whole caller chain reads
  settings dynamically.
- Dashboard changes use `patch_config_keys()` and atomic replacement. It never
  edits API keys.
- `COMPACT_MODE` defaults to `yes`, is not a Fast Mode managed key, and may be
  persisted as `no` only after the final Full Mode confirmation. Missing means
  Compact; an existing `no` is respected across restart without replaying the
  demonstration.

### Configuration groups

The following map includes every current built-in key family. Exact defaults are
in `DEFAULT_CONFIG`; user-facing descriptions are in the root README and
dashboard.

| Group | Keys |
|---|---|
| Capability profile | `COMPACT_MODE` |
| Provider and generation | `USE_LOCAL_AI`, `ENABLE_GROQ`, `ENABLE_GEMINI`, `GEMINI_MODEL`, `ENABLE_OPENROUTER`, `OPENROUTER_MODEL`, `FASTER_MODE`, `GROQ_MODEL`, `LOCAL_AI_MODEL`, `LOCAL_AI_TIMEOUT`, `AI_TEMPERATURE`, `AI_MAX_TOKENS`, `AI_TOP_P`, `ENABLE_STREAMING`, `ENABLE_AMBIENT_POLLS` |
| Datetime context | `ENABLE_DATETIME_CONTEXT`, `DATETIME_INCLUDE_SECONDS`, `DATETIME_INCLUDE_TIMEZONE` |
| Agent continuation | `ENABLE_AGENT_CONTINUATION`, `AGENT_MAX_STEPS`, `AGENT_MAX_DURATION_SEC`, `AGENT_MAX_TOOL_RESULT_CHARS` |
| Process awareness | `ENABLE_PROCESS_AWARENESS`, `PROCESS_CONTEXT_MODE`, `PROCESS_MAX_VISIBLE_APPS`, `PROCESS_CONTEXT_EXCLUDED_APPS` |
| Computer Use Lite | `ENABLE_COMPUTER_USE`, `COMPUTER_USE_MAX_STEPS`, `COMPUTER_USE_TIMEOUT_SEC`, `COMPUTER_USE_PLANNER_PROVIDER`, `COMPUTER_USE_PLANNER_MODEL`, `COMPUTER_USE_PLANNER_CONFIDENCE_MIN`, `COMPUTER_USE_RECOVERY_AFTER_FAILURES`, `COMPUTER_USE_MAX_RECOVERY_CALLS`, `COMPUTER_USE_ALLOWED_APPS` |
| Command safety | `ENABLE_COMMAND_EXECUTION`, `ENABLE_WINDOW_CONTROL`, `ENABLE_COMMAND_CONFIRMATIONS`, `FORCE_CLOSE_AUTO_ALLOW`, `PROTECTED_PROCESSES`, `DRY_RUN_MODE` |
| Unicode typing | `ENABLE_UNICODE_TYPING`, `UNICODE_TYPING_MODE`, `UNICODE_TYPING_DELAY_MS`, `UNICODE_TYPING_PREVIEW_THRESHOLD`, `UNICODE_TYPING_RESTORE_CLIPBOARD` |
| Prompt/memory bounds | `MEMORY_CHARS`, `HISTORY_LIMIT`, `FILE_READ_CHARS`, `EPISODIC_PROMPT_LIMIT`, `EPISODIC_ENTRY_MAX_CHARS`, `EPISODIC_MAX_ENTRIES`, `ENABLE_LONGTERM_MEMORY`, `LONGTERM_MEMORY_MAX_RESULTS`, `LONGTERM_MEMORY_MAX_CHARS` |
| Web retrieval | `ENABLE_WEB_RAG`, `WEB_FETCH_MAX_CHARS`, `WEB_TIMEOUT_SEC`, `WEB_SEARCH_MAX_RESULTS` |
| Glitch visuals | `ENABLE_GLITCH_EFFECTS`, `GLITCH_MAX_DURATION_MS`, `GLITCH_DEFAULT_STYLE`, `GLITCH_MOOD_AUTO`, `GLITCH_FULLSCREEN` |
| Companion simulation | `ENABLE_COMPANION_STATS_CONTEXT`, `ENABLE_CIRCADIAN_RHYTHM`, `RHYTHM_NIGHT_START`, `RHYTHM_NIGHT_END`, `ENABLE_DREAMS`, `DREAMS_MAX_ENTRIES`, `ENABLE_TASKS`, `TASKS_MAX_ENTRIES` |
| Emotion model | `ENABLE_EMOTION_ENGINE`, `EMOTION_BASELINE_VALENCE`, `EMOTION_BASELINE_AROUSAL`, `EMOTION_BASELINE_TRUST`, `EMOTION_BASELINE_LONELINESS`, `EMOTION_DECAY_PER_HOUR`, `EMOTION_HISTORY_MAX` |
| Windows integrations/status/tray | `ENABLE_AUTOSTART_CONTROL`, `ENABLE_THEME_CONTROL`, `ENABLE_STATUS_PROVIDERS`, `STATUS_POLL_INTERVAL_SEC`, `ENABLE_TRAY`, `TRAY_BACKGROUND_CLOSE` |
| Presence and attention | `SCREEN_POLL_INTERVAL_SEC`, `TOUCH_COOLDOWN_SEC`, `WAKE_DELAY_SEC`, `LOAF_TIMER_MIN`, `ENABLE_ATTENTION_SNAP`, all `MOOD_SNAP_*_SEC` keys |
| Presence Etiquette | `ENABLE_PRESENCE_ETIQUETTE`, `PRESENCE_FULLSCREEN_SILENT`, `PRESENCE_DISMISS_COOLDOWN_SEC`, `PRESENCE_RAPID_TYPING_COOLDOWN_SEC`, `QUIET_HOURS_START`, `QUIET_HOURS_END` |
| Terminal Sentinel | `ENABLE_TERMINAL_SENTINEL`, `TERMINAL_SENTINEL_APPS`, `TERMINAL_SENTINEL_TITLE_PATTERNS`, `TERMINAL_SENTINEL_COOLDOWN_SEC` |
| Standard OCR | `ENABLE_SCREEN_READER`, `ENABLE_PRINTWINDOW_FALLBACK`, `OCR_MAX_DIMENSION`, `OCR_FOCUSED_WINDOW_ONLY`, `OCR_CHANGE_DETECTION`, `OCR_CHANGE_THRESHOLD`, `OCR_FORCE_REFRESH_SECONDS`, `OCR_STATE_EXPIRY_SECONDS`, confirmation/cooldown/confidence keys, `OCR_PREPROCESSING`, `OCR_LANGUAGES`, `OCR_PSM`, exclusions/redaction, `INCLUDE_WINDOW_TITLE_IN_CONTEXT`, `TESSERACT_PATH`, `OCR_CUSTOM_PATTERNS`, `OCR_PAUSE_WHILE_TYPING_SEC` |
| Explicit deep OCR | `DEEP_OCR_BACKEND`, `UNLIMITED_OCR_SERVER_URL`, `UNLIMITED_OCR_MODEL`, `UNLIMITED_OCR_TIMEOUT_SECONDS`, `UNLIMITED_OCR_ALLOW_REMOTE`, `DEEP_OCR_MAX_OUTPUT_CHARS` |
| Window/UI | `WINDOW_TOPMOST`, `UI_SCALE`, `WINDOW_START_X`, `WINDOW_START_Y`, `SUBTITLE_CHAR_DELAY`, `ANIMATION_SPEED`, `WINDOW_MOVE_SMOOTH`, `WINDOW_MOVE_DURATION_MS`, `ENABLE_SENSES_PANEL` |
| Close/glow/motion | `ENABLE_CRT_CLOSE_ANIMATION`, `REDUCED_MOTION`, `ENABLE_MOOD_GLOW`, `MOOD_GLOW_ANIMATED`, `MOOD_GLOW_INTERVAL_MS`, `ENABLE_MOOD_MOTION`, `MOOD_MOTION_COOLDOWN_SECONDS` |
| Medic/project metadata | `SKIP_TESSERACT_CHECK`, `SKIP_ASSET_CHECK`, `AUTO_PIP_INSTALL`, `CREATE_DESKTOP_SHORTCUT`, `CHECK_FOR_UPDATES`, `APP_VERSION`, `GITHUB_RELEASES_URL` |
| External window control | `TARGET_APP_ALIASES`, `WINDOW_PICKER_ON_AMBIGUOUS` |
| Input/speech | `ENABLE_VOICE`, `USE_LOCAL_STT`, `ENABLE_FILE_DRAG_DROP`, `VOICE_OUTPUT_MODE`, `VOICE_TTS_ENGINE`, `TTS_RATE`, `TTS_VOLUME`, `TTS_VOICE_NAME` |

`UI_SCALE=auto` is the recommended high-DPI mode. Manual values clamp to
`0.75-2.50`; automatic scale preserves historical size on smaller displays and
uses display/DPI information up to a bounded `2.0`. The dashboard inherits the
resolved scale through the root. Test both the companion and dashboard when
changing this path.

### Adding a setting

1. For a stable machine-validated setting, add one `SettingSpec` with its
   default, type, range or enum, group, and restart state. Keep transactional,
   secret, and security decisions out of the registry.
2. Add the documented key and comment to `app_config.DEFAULT_CONFIG`. Tests
   require canonical SettingSpec defaults to match the human template.
3. For settings outside the canonical subset, add the key to the relevant
   boolean/integer/float validation set.
4. Add or update one `AppSettings` property with enum validation and a sensible
   clamp. Missing/invalid input must not break startup.
5. Consume the typed property in the owning module.
6. Add the dashboard control only if user editing is useful; label restart
   behavior honestly.
7. Update `.env.example` only for a new secret. Ordinary settings belong in the
   config template and user documentation.
8. Add default, invalid-input, and clamp tests in the closest suite.
9. Update the configuration group above and root README if user-facing.
10. If the key has a SettingSpec, regenerate
    `docs/generated/settings_reference.md`.

At normal startup, missing non-secret `SettingSpec` keys are appended through
the structural atomic writer. This makes new stable settings discoverable
without overwriting existing values or involving Fast Mode/Compact state.

### Adding a provider

1. Add one explicit adapter/transport under `agetha/providers/` that implements
   the existing provider-neutral `create(...)` contract.
2. Register the kind in `ProviderRouter`.
3. Add non-secret typed settings and an `.env.example` secret name as needed.
4. Wire selection/fallback and isolated `request_structured()` construction in
   AIEngine without moving prompt, repair, history, origin, or command policy.
5. Add deterministic transport, error, fallback, repair-boundary, planner, and
   authority-neutrality tests. Never use a live paid endpoint in tests.

## Command and safety model

`agetha.commands.specs.COMMAND_SPECS` is the canonical static policy registry.
The [generated command matrix](generated/command_matrix.md) lists each command's
base risk, capability, execution requirement, allowed origins, dispatch kind,
handler, and command-specific feature gates. CI regenerates it in memory and
fails if the checked-in reference has drifted.

Confirmations deny after timeout. Unknown commands are Danger. Protected
processes include built-in critical Windows names plus Python and Agetha;
legacy Linux names remain defensively recognized in retained fallback code.
User additions extend rather than replace that set. `FORCE_CLOSE_AUTO_ALLOW`
never authorizes protected/self targets. Dry-run is an additional user-visible
decision path, not a way around confirmation or feature gates.

### Adding an AI command

A normal handler-backed command requires:

1. Add one explicit `CommandSpec` with intentional base risk, capability,
   allowed origins, handler dispatch, and any mechanical command-specific
   feature gates.
2. Register exactly one handler with the matching key. Duplicate, unknown, and
   core-command registrations fail during import.
3. Teach `AIEngine._parse()` which payload fields are accepted, normalized,
   bounded, and gated when the command needs new fields. Never pass an arbitrary
   model dictionary straight to an OS helper.
4. Update human-authored prompt descriptions/few shots only when needed.
5. Use a focused function in `system_commands`, `platform`, `features`, or
   `core`; do not embed a reusable subsystem in the handler or `main.py`.
6. Apply `ENABLE_COMMAND_EXECUTION` and any feature-specific switch. Window
   commands also require `ENABLE_WINDOW_CONTROL`.
7. Keep Agetha/self/protected target checks and ambiguity picking intact.
8. For Tk work, schedule the UI portion with `root.after()`. Put blocking work in
   a worker and provide cancellation/close checks.
9. For a context-producing command, use the bounded pending-context/deferred
    re-query pattern and an anti-recursion flag.
10. Add registry, parser/gate, denial, dynamic-policy, handler, and effect-time
    authorization tests as applicable.
11. Regenerate the mechanical reference with
    `python -m agetha.commands.generate_command_matrix` and update user-facing
    command documentation only when behavior changed.

Do not make mood, affection, infection level, or emotional history influence
command authorization.

### Compact/Full capability and consent change contract

Read [Compact and Full profiles](compact_full_mode.md) before changing profile
behavior. Preserve these boundaries:

1. `core.capabilities` is the central deterministic outer policy; do not scatter
   UI-only `if compact_mode` checks or let model output select a profile.
2. Compact permits core chat/memory/emotion plus configured WebRAG and read-only
   continuation, but denies Sentinel, Process Awareness, Computer Use and its
   planners, OS typing/control, background sensing, and advanced OS integration.
3. Full makes advanced capabilities eligible only when each ordinary feature
   gate and safety boundary also allows them.
4. Dashboard visibility is a presentation model. Enforce decisions before
   service startup, capture/polling, provider planning, command preflight, and
   every effect boundary.
5. Compact-to-Full follows the pure generation-bound sequence first warning →
   consent demo/fallback → final confirmation. Full is inactive until final Yes.
6. The demo API accepts no app or text. It may launch only fixed Notepad and type
   only its compiled warning after strict PID/name/creation-time/HWND/bounds/
   foreground/liveness/generation validation. It has no provider, Computer Use,
   planner/recovery, OCR, web, clipboard, shell, or Python-helper route.
7. All consent UI and shake callbacks stay on Tk, are bounded/owned/cancellable,
   and use a non-motion cue under reduced motion.
8. Full-to-Compact publishes the deny boundary and invalidates generations
   before waiting for Computer Use/planner cancellation or stopping advanced
   services. No later keyboard, mouse, or app-control effect is allowed.
9. Persist profile changes with the existing structural atomic config patch;
   preserve comments/unknown lines and never add `COMPACT_MODE` to Fast Mode.
10. Senses/dashboard state must be truthful and passive; reading it must not
    enumerate, capture, probe a provider, or start an advanced feature.

### Unicode typing safety contract

`type_text` remains Caution. Preserve the entire input string; never trim,
normalize, translate, transliterate, change punctuation/case, remove words or
language-specific particles, split uncertain Unicode clusters, or append
Enter/Return/Tab. Keep all reusable platform behavior in
`platform/unicode_typing.py`. Compact additionally denies this OS effect at the
central capability boundary.

The dispatch path must continue to:

1. reject the request before target/clipboard work when either
   `ENABLE_COMMAND_EXECUTION` or `ENABLE_UNICODE_TYPING` is off;
2. capture the intended external target before Command Guard or preview UI;
3. refuse Agetha and conservative protected/elevated targets;
4. give Command Guard only privacy-safe target/method/count metadata;
5. require the Win95 preview for long, multiline, terminal,
   administrator-related, shell-like, sensitive-looking, and explicit-preview
   requests;
6. require an internal dispatch approval token before the handler starts the
   worker; and
7. recheck gates, focus, cancellation, and shutdown at the effect boundary.

Windows native entry uses UTF-16 `SendInput(KEYEVENTF_UNICODE)`. A partial
native send must never fall back and duplicate already-entered text. Clipboard
fallback captures the previous value and restores it only if the clipboard
still equals Agetha's temporary value. Xorg optional utilities must remain
optional. Wayland restrictions produce an honest copy-only/manual-paste result,
not a security bypass.

### Continuation and process-awareness change contract

Read [Continuation Engine](continuation_engine.md) before changing a
multi-message turn. Preserve one explicit session owner, direct-user-only
activation, session/generation checks, application-owned provider reservation,
non-recursive transitions, bounded clocks/steps/history/results, and the exact
read-only allowlist. A `ToolOutcome` is untrusted observation data; it cannot
dispatch a state-changing command, write normal memory/history, or activate
Computer Use.

New read-only adapters must apply their existing feature gate, bound and redact
provider context, accept injected dependencies for tests, and declare
sensitivity. Resource-bearing commands must match the original session's exact
authorized paths/process names/URLs or a bounded discovered URL. Network fetch
adapters must reject non-public destinations and validate every redirect.

Process-awareness changes must keep foreground, visible applications, and
background inventory distinct. Never use PID alone for an effect lock; compare
executable basename and creation time when available. The provider view remains
minimized even in local `all_processes` mode, and sensitive titles/paths must not
enter prompts or observations. Process observations are facts only and cannot
call a provider or authorize actions. The owner must stay inactive while
Compact denies `PROCESS_AWARENESS`, irrespective of the individual flag.

### Computer Use Lite change contract

Read [Computer Use Lite](computer_use.md) before editing the package. Preserve:

1. Compact denial plus `ENABLE_COMPUTER_USE=no` as the individual default, with
   direct `user` origin as the only activation authority; the consent demo is
   never a Computer Use origin;
2. one immutable observation and exactly one planner action per call;
3. no personality/memory/history/raw screenshot/full process inventory/exact
   payload in planner or recovery context;
4. deterministic Policy → existing gates/Command Guard → sole Executor order;
5. PID + basename + creation time + HWND + validity + bounds + authorization
   checks before every effect;
6. temporary control IDs as the primary abstraction and coordinates only as a
   validated in-bounds fallback;
7. local payload references resolved only by guarded Unicode typing, with no
   synthetic Enter/Return/Tab and no payload logging;
8. immediate STOP/Escape generation invalidation and late-result discard;
9. bounded cheap-planner/reobserve/primary-recovery routing with no model swarm
   or infinite loop; and
10. conservative handoff for credentials, banking/payment, CAPTCHA,
    password-manager, elevated/secure-desktop, and security-software contexts.

Observer, Policy, Executor, Verifier, process locking, cancellation, and step
limits remain deterministic components, not AI agents. The accessibility
abstraction must report unavailable until a real dependency-free implementation
exists. Do not add a full-vision model or new input/UI-automation dependency
without a separately authorized phase.

## Adding prompt context

Add context through `AIEngine._build_prompt()` and preserve these constraints:

- make it optional through typed settings when it has meaningful cost/privacy;
- bound item count and characters;
- label OCR, web, documents, memory, and status text as external/untrusted data;
- do not expose secrets or full environment/config dumps;
- consume one-shot context only when intended (dream recall, session recap,
  queued status);
- use an injectable clock/provider for deterministic tests;
- exercise both direct and ambient turns and all provider modes through the
  shared prompt builder.

Datetime context is the example to follow: `core.time_context` owns local
timezone formatting, while the engine only decides whether to include the
compact result.

### Character voice versus exact data

Language behavior is one general system-prompt/few-shot contract: Agetha replies
primarily in the user's current language, preserves mixed-language conversation,
and approximates the user's register without unnecessarily adding translation,
transliteration, gendered speech, honorifics, cultural particles, formality, or
slang. This presentation choice never changes command, provider, continuation,
process, or safety authority.

Do not implement a language-specific subsystem, global regex, or post-provider
word/suffix filter. Exact user-provided text, quotations, requested translations,
documents, code, file and clipboard content, and command payloads must remain
unchanged. Use a balanced deterministic vector set—English, Thai, Japanese,
Chinese, Korean, Arabic, Russian, another Latin-script language, mixed scripts,
and emoji—without presenting one as Agetha's preference. Prompt tests can prove
the policy is present; they cannot claim equal model quality across languages or
guarantee every future response.

## Tk, workers, and timers

Before adding asynchronous or visual behavior:

- identify its owner (`CompanionApp`, a Toplevel, or a controller);
- never update a widget from an AI/OCR/voice/audio/network worker;
- use `root.after(0, ...)` for the UI handoff;
- store the ID for every repeating callback and geometry sequence;
- prevent duplicate loops;
- cancel it on close and on minimize/teardown when it should not run invisibly;
- check closing/stop state in callbacks and before publishing worker results;
- keep main-thread callbacks short; no `time.sleep()` or blocking joins;
- make stop/cancel/close idempotent.

For geometry, only one owner may move the companion at a time. Coordinate with
dragging, `animate_geometry`, attention snap, mood motion, minimize, and CRT
close. Temporary movement must clamp to the active monitor work area and restore
an exact stable final position.

Polyglot Presence ownership is specific:

- `CompanionApp` owns `ObservationBus`, optional `PresenceEtiquette`, Terminal
  Sentinel, `ContinuationEngine`, `ProcessAwareness`, optional
  `ComputerUseManager`, active Unicode/Computer Use cancellation, the Senses
  panel, and owned status/Sentinel popups.
- It also owns the capability controller, consent flow generation, consent
  dialogs/demo cancellation, and every bounded shake/fallback callback. Public
  consent UI methods remain Tk-owner-thread-only.
- Unicode entry and Senses collection use application workers; previews,
  status application, and popups are scheduled onto Tk.
- Observation and Presence queues are in memory and shut down idempotently.
- Final shutdown invalidates capability/consent, Continuation, and Computer Use
  generations, signals consent/Unicode entry, closes child panels/popups, then
  shuts down Process Awareness, Sentinel, Presence, and the bus before root
  destruction.

## UI, moods, and assets

Current supported moods are:

```text
neutral happy excited sad surprised thinking whisper angry sleeping manic
melancholic paranoid vulnerable dominant
```

When changing mood behavior:

1. Keep `AIEngine.VALID_MOODS`, emotion derivation, `main.py` GIF maps, speech
   maps, `MOOD_COLOURS`, and `MOOD_MOTION_MAP` consistent.
2. State changes choose visuals; response-level movement must not be added to
   `_set_state()`/`_apply_state()`.
3. Reduced motion disables decorative geometry/pulsing while preserving a static
   usable UI.
4. Keep manic effects slow and bounded; do not add flashing.
5. If adding a GIF, wire it into runtime maps, add it to Medic's intended asset
   policy, and extend `TestGifAssetCoverage`.
6. Decode image frames off-thread, but create Tk images on the main thread. Do
   not rebuild images continuously for a color effect.
7. Check `agent.md` for visual identity and known glitch/static frames before
   replacing an asset.

For scaling changes, operate on base dimensions through `_px()`/`scale_px()` and
test manual and automatic scale. Avoid hardcoded pixel fixes in a single
dashboard tab.

## Persistence changes

Each state module owns its file and lock. For a new store:

1. Put private generated data under `memory/`.
2. Define a small versioned/validated schema and safe missing/corrupt fallback.
3. Bound entry size and total count.
4. Serialize access with `Lock`/`RLock` when workers may overlap.
5. Use `agetha.utils.write_atomic()` for rewrites or an equivalent
   temp+flush+fsync+`os.replace` path. Append-only JSONL still needs a lock.
6. Never persist API keys, unredacted OCR, raw private documents, or unlimited
   command output.
7. Make cleanup/reset selective where user-facing history is involved.
8. Test write failure, temporary-file cleanup, corrupt recovery, and concurrency
   where relevant.

## Screen monitoring and OCR changes

Keep the following separations:

- capture policy and state publication: `screen_reader.py`;
- pure change/event/privacy helpers: `screen_monitoring.py`;
- OCR data contract/prompt wrapper: `ocr_backends/base.py`;
- local automatic extraction: `tesseract_backend.py`;
- explicit optional transport: `unlimited_ocr_backend.py`.

Reliability checklist:

- carry image, origin, window identity, scope, and title in one immutable frame;
- preserve exact X/Y preprocessing scales and convert OCR coordinates back to
  physical desktop space;
- support negative multi-monitor origins;
- serialize normal scans and protect screenshot acquisition;
- discard a result if the focused HWND changes during OCR;
- keep current pattern matches distinct from newly triggered ambient events;
- expire per-window state and apply confirmation/cooldown/clear logic;
- run local patterns before outbound redaction, then redact provider context;
- automatic capture respects own/excluded windows;
- deep OCR stays explicit-only, Caution-confirmed, remote-opt-in, and isolated
  from standard state;
- no network timezone or automatic Unlimited-OCR lookup.

Run both OCR suites after any change; the 75-case reliability suite is the
contract for the external screen-monitoring plan.

## Launcher and Windows ARM

Windows on ARM has two independent architectures:

- native OS architecture, commonly ARM64 on Snapdragon;
- interpreter/process architecture, which should be AMD64/x64 for this
  dependency stack and runs under Windows Prism.

An x64 Python on an ARM64 host is expected and must not be mislabeled as ARM64
solely because `platform.machine()` reports the host. The current detection
priority is:

1. `sysconfig.get_platform()` build tag such as `win-amd64`;
2. pointer/process architecture and `IsWow64Process2` metadata;
3. host/native environment values only for native-OS reporting.

Medic scans all PATH candidates, `py -0p`, Python registry entries, and standard
install directories. It selects a compatible x64 interpreter and rebuilds an
ARM/incompatible virtual environment because a venv remains tied to the Python
that created it.

Validation after launcher changes:

```powershell
python .\medic_helper.py python_arch
python -m unittest tests.test_medic_arch -v
```

Also run Medic manually on the target Windows machine and inspect both
`interpreter_architecture` and `native_windows_architecture`. Keep
`Medic_Checker.ps1`, `medic_helper.py`, the candidate rules, wording, and tests in
sync. Do not require the administrator launcher for normal operation.

### Frozen executable audit and validation

The repository already contains PyInstaller-style `main.spec`,
`ci_compile_check.spec`, and `medic_helper.spec`; use that mechanism when a local
build is explicitly in scope. Do not introduce another packager or dependency
solely for Compact/Full support.

Important current caveat: `main.spec` names a console executable `main`, has an
empty `datas` list, and does not by itself stage the ignored/external `assets/`
directory. There is no canonical release/installer command encoded by a spec
file alone. Finding the spec, `build/`, `dist/`, or an old binary does not prove a
current build or manual smoke test passed.

When touching source/frozen behavior:

- keep source paths independent of the process current working directory;
- under `sys.frozen`, keep mutable config, `.env`, `memory/`, and logs at the
  existing writable directory beside `sys.executable`, never `_MEIPASS`;
- keep required sibling assets aligned with that `BASE_DIR` strategy unless the
  existing spec is intentionally updated and validated;
- never invoke a Python helper with `sys.executable` in frozen mode, because it
  is the Agetha executable;
- keep the Full-consent helper as a direct fixed `notepad.exe` launch, not a
  general executable launcher;
- recognize owned PID/HWND first and exact `main.py`, `main.exe`, and
  `Agetha.exe` aliases second for self-target refusal; do not broadly exclude
  unrelated `python.exe` processes; and
- preserve Windows ARM64 as the x64/AMD64-under-Prism path. Do not claim a native
  ARM64 executable without a separately built and observed artifact.

If existing build dependencies are available, direct output to a disposable
local location, stage required external assets without modifying tracked build
outputs, and do not publish or stage the binary. Report separately: frozen
compatibility audit, build attempted/result, source smoke, `.exe` smoke, and
unperformed manual items. The Full Notepad/keyboard demonstration requires an
explicitly safe GUI environment and is never implied by a build.

## Fast Mode recovery tooling

Read [Fast Mode security and recovery](fast_mode_security.md) before changing
the profile, snapshot schema, locking, or transaction paths. The Windows Medic
commands and portable Python commands intentionally keep status, reconciliation,
and restoration separate. Status must remain read-only; recovery operations may
write only after their existing operator confirmation boundary.

Linux/direct-Python smoke commands:

```bash
python -m agetha.core.fast_mode_profile status
python -m agetha.core.fast_mode_profile reconcile
python -m agetha.core.fast_mode_profile restore
```

Do not add profile settings without extending the forbidden-key invariant,
typed/range validation, strict snapshot tests, Dashboard transaction tests, and
recovery documentation.

## Test map

### Full suite

```powershell
python -m unittest discover -s tests -v
```

With the project venv:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

### Focused suites

| Change | Command |
|---|---|
| Atomic storage/state recovery | `python -m unittest tests.test_atomic_persistence -v` |
| Fast Mode snapshot/config transactions | `python -m unittest tests.test_fast_mode_profile -v` |
| Fast Mode locking/path/audit/CLI security | `python -m unittest tests.test_fast_mode_security -v` |
| Fast Mode adaptive request/runtime behavior | `python -m unittest tests.test_fast_mode_runtime -v` |
| Fast Mode dashboard and Medic integration | `python -m unittest tests.test_fast_mode_ui_medic -v` |
| OCR backend/deep service | `python -m unittest tests.test_hybrid_ocr -v` |
| Windows ARM/Medic architecture | `python -m unittest tests.test_medic_arch -v` |
| Dashboard/memory/stats | `python -m unittest tests.test_phase1_qa -v` |
| TTS/audio coordination | `python -m unittest tests.test_phase2_tts -v` |
| Web retrieval | `python -m unittest tests.test_phase3_web_rag -v` |
| Glitch overlay | `python -m unittest tests.test_phase3b_glitch -v` |
| GIFs/notepad/stats/realism | `python -m unittest tests.test_phase4_realism -v` |
| Rhythm/dreams/tasks | `python -m unittest tests.test_phase5_v4 -v` |
| Emotions/autostart/Windows/status/tray | `python -m unittest tests.test_phase6_v5 -v` |
| Screen reliability/privacy/concurrency | `python -m unittest tests.test_screen_monitoring_reliability -v` |
| Datetime/scaling/glow/motion/CRT/shutdown | `python -m unittest tests.test_time_ui_effects -v` |
| Exact Unicode typing/platform fallbacks | `python -m unittest tests.test_unicode_typing -v` |
| Typed Observation Bus | `python -m unittest tests.test_observation_bus -v` |
| Presence Etiquette | `python -m unittest tests.test_presence_etiquette -v` |
| Opt-in Terminal Sentinel | `python -m unittest tests.test_terminal_sentinel -v` |
| Senses capability model/UI lifecycle | `python -m unittest tests.test_senses_panel -v` |
| Polyglot Presence integration | `python -m unittest tests.test_polyglot_presence_integration -v` |
| Language-neutral multilingual policy | `python -m unittest tests.test_language_policy -v` |
| Compact/Full capability matrix and dispatch boundary | `python -m unittest tests.test_capabilities -v` |
| Compact provider deferral and ambient generation boundary | `python -m unittest tests.test_compact_provider_gate -v` |
| Compact/Full application lifecycle and downgrade | `python -m unittest tests.test_capability_main_integration -v` |
| Compact/Full Dashboard presentation model | `python -m unittest tests.test_dashboard_profiles -v` |
| Pure Full-consent state machine | `python -m unittest tests.test_full_mode_consent_state -v` |
| Fixed Notepad consent bootstrap | `python -m unittest tests.test_full_mode_consent_demo -v` |
| Win95 consent UI/shake lifecycle | `python -m unittest tests.test_full_mode_consent_ui -v` |
| Frozen paths/launcher/self identity | `python -m unittest tests.test_frozen_runtime -v` |
| Continuation state machine | `python -m unittest tests.test_continuation -v` |
| Continuation read-only adapters/SSRF | `python -m unittest tests.test_read_only_tools -v` |
| Process/application awareness | `python -m unittest tests.test_process_awareness -v` |
| Computer Use action models | `python -m unittest tests.test_computer_use_models -v` |
| Computer Use local activation/payload parsing | `python -m unittest tests.test_computer_use_activation -v` |
| Computer Use observer/policy | `python -m unittest tests.test_computer_use_observer_policy -v` |
| Computer Use executor/verifier | `python -m unittest tests.test_computer_use_executor_verifier -v` |
| Computer Use planner/session | `python -m unittest tests.test_computer_use_planner_session -v` |
| Computer Use platform runtime bridge | `python -m unittest tests.test_computer_use_runtime -v` |
| Computer Use session Escape hotkey | `python -m unittest tests.test_computer_use_escape_hotkey -v` |
| Computer Use app composition/target bootstrap | `python -m unittest tests.test_computer_use_integration -v` |

Medic's compile/import checks are useful environment diagnostics, but they do not
replace the test suite.

### GitHub Actions

`.github/workflows/ci.yml` runs Python 3.13 on `windows-latest` and
`ubuntu-latest`. Both jobs compile shared modules, run the full suite and focused
Fast Mode suites, and import shared platform modules without provider keys, a
display, Tesseract, or remote OCR. Windows additionally parses Medic and runs
reparse/path/lock smokes. Linux runs actual symlink/POSIX permission/`fcntl`
coverage, portable CLI transactions, and mocked X11 desktop-path tests.

GitHub-hosted Windows is x64. It does not replace a manual Surface Pro or other
Windows ARM64 test of x64 Python selection and Prism execution.

### Basic compile check

```powershell
python -m py_compile main.py medic_helper.py
python -m compileall -q agetha
```

Do not report a command as passing unless it was actually run in the current
worktree. Real-Tk tests may be skipped on headless systems; report skips.

## Manual validation matrix

Automated tests mock provider, OS, and Tk boundaries. Before a release, cover the
paths relevant to the change:

1. Launch with optional visual/voice/tray/web/status/deep-OCR features disabled.
2. Verify direct Groq, Gemini, OpenRouter, and local Ollama modes that are actually
   configured; do not expose keys in logs/screenshots.
3. Inspect direct and ambient prompts for compact datetime and redacted screen
   context.
4. Exercise focused capture, blank-frame PrintWindow fallback on Windows,
   unchanged-frame suppression, excluded/Agetha windows, and a focus change
   during OCR.
5. Confirm deep OCR is never ambient and asks before transmission.
6. Test dashboard tabs and config apply at automatic and manual scale, including
   a 2880x1920/high-DPI Windows display when available.
7. Rapidly change moods; confirm one glow loop and no overlapping motion.
8. Drag/minimize/restore while a motion is requested.
9. Close from title button, window manager, and tray exit; repeat the request
   while voice/TTS/geometry is active.
10. Enable reduced motion; confirm static glow and immediate/decorative-motion
    suppression.
11. Deny Caution/Danger operations and verify no side effect; test protected/self
    window targets.
12. Enable Fast Mode from the dashboard, verify the 13 managed values and local
    snapshot, restart, then disable it and compare the restored values.
13. While Fast Mode is active, manually change one managed and one unmanaged
    setting; restart, confirm the forced value is repaired, then confirm both new
    preferences survive restoration as documented.
14. Run Medic against active, restoration-pending, and deliberately corrupt
    temporary snapshots; confirm it reports status and asks before mutation.
15. Observe an unchanged Fast ambient scan and confirm no provider request is
    made; then produce a meaningful OCR event and confirm one bounded request.
16. On Linux, run the status-only CLI and a temporary activation/restore cycle;
    verify unavailable desktop capture facilities fail safely without a display.
17. On Surface/Windows ARM when available, confirm Medic selects x64 Python and
    Agetha starts under Prism without offering to replace that interpreter.

Record which steps were actually performed and the platform used.

The feature-specific twenty-item Windows checklist and Xorg/Wayland notes are
in [Polyglot Presence manual validation](testing/polyglot_presence_manual.md).
The separate twenty-five-item Continuation/Process/Computer Use checklist is in
[Computer Use manual validation](testing/computer_use_manual.md). Every new item
starts as **NOT PERFORMED** and must remain so until directly observed.
The 34-item source/Compact/Full/frozen checklist is in
[Compact/Full Mode manual validation](testing/compact_full_mode_manual.md); all
34 entries also begin **NOT PERFORMED**.
Automated tests, compile checks, and mocked platform adapters do not count as a
performed manual item; leave each entry marked unperformed until a human runs
it on the named desktop and records the result.

## Platform limitations and review hotspots

| Area | Current limitation or caveat |
|---|---|
| Compact/Full profiles | Compact is the default master gate. Full enables eligibility, not every feature, and does not weaken safety. The Notepad demonstration is Windows-only presentation; failure uses an in-app fallback and final consent remains required. |
| Frozen executable | Existing specs provide a PyInstaller-style mechanism, but `main.spec` currently names `main.exe`, declares no data files, and is not evidence of a current build or smoke test. External assets must be staged beside the executable under the current path strategy. |
| Platform scope | Windows and existing Linux desktop paths are supported. macOS is retired. Hosted CI cannot validate Windows ARM/Prism or every Linux compositor/desktop utility. |
| Window alpha/chrome | Windows alpha support can vary by graphics stack; close falls back to immediate cleanup if the effect is unavailable. |
| Tesseract | Python package alone is insufficient; the native executable and requested language data must exist. |
| Voice shutdown | Listener/recognition workers are daemon threads and stop by event/timeouts rather than a blocking UI-thread join. Keep operations bounded. |
| Screen own-window handle | Own-window exclusion is best when the native handle is cached/passed from Tk; avoid adding worker-side Tk calls to resolve it. |
| Unicode typing | Windows native behavior still depends on target-app support for `KEYEVENTF_UNICODE`. Xorg entry needs optional `xdotool` plus `xclip` or `xsel`; missing tools fall back honestly. Wayland permits clipboard copy through `wl-copy` when installed but blocks global automatic typing by design. Secure/elevated desktops are refused rather than bypassed. |
| Terminal Sentinel | It sees only confirmed Tesseract events from explicitly allowlisted windows. OCR can misread terminal output; notifications are advisory, never an automatic fix. Empty allowlists watch nothing. |
| Senses panel | Capability status is a local snapshot, not a live health guarantee. Provider availability remains unknown unless runtime already knows it; opening the panel deliberately performs no paid/network probe. |
| Process awareness | Windows has the strongest native foreground/window identity. Xorg uses existing optional tools and may be degraded; generic Wayland can be process-only. Provider context stays minimized even when local inspection is broader. |
| Computer Use Lite | Disabled by default and Windows-first. The accessibility provider is an honest unavailable abstraction, so OCR is the MVP. Xorg is degraded and must stop when strict locking/input prerequisites are absent; autonomous Computer Use is unavailable on Wayland. Full visual/vision planning is future work. |
| Dashboard | Multiple dashboards may open. Its tracked callback list is cleared on close but can grow during a long session; keep new pollers sparse. |
| Fast Mode snapshot permissions | POSIX permission bits are forced to user read/write. On Windows, the file inherits the current user's directory ACL because portable `chmod` cannot create a new Windows ACL. Reparse-point targets are refused. |
| Fast Mode same-user threat | Lock/path hardening resists practical substitution and races but is not a privilege boundary against a fully compromised process running as the same user. |
| Web fetch | Continuation fetches use the public-only DNS-validating, address-pinned, redirect-revalidating adapter with one cancellation-aware deadline. The separate legacy WebRAG helper retains its disabled default and Caution confirmation. |
| Glitch overlay | Short-lived callbacks rely on Toplevel destruction; use explicit tracked IDs for any new persistent loop. |
| Dependencies | Voice/DnD packages are installed by current `requirements.txt` even though the features are config-optional; avoid describing package installation and feature enablement as the same thing. |
| Versions | Package, config, Medic, and historical README labels are not one automatic release field. Change versions only as part of an intentional fork release. |
| Assets | The binary asset directory is Git-ignored and has a separate provenance/license boundary; fresh clones may not be visually runnable without it. |

## Documentation maintenance

When a change adds a module, command, setting family, persistence file, worker,
timer/controller, platform dependency, or test suite:

1. Update [Module reference](module_reference.md).
2. Update [Architecture](architecture.md) if ownership or a boundary changes.
3. Update [Runtime flows](runtime_flows.md) if sequencing, threading, or teardown
   changes.
4. Update this guide's config/test/checklist section.
5. Update user-facing README/`agent.md` only where their audience needs it.

Prefer links and contracts over pasted implementation. Documentation should let
the next developer locate the code, not create a second copy of it.

Future Polyglot Presence ideas live only in the
[design roadmap](roadmap/polyglot_presence_roadmap.md). Features A–O are all
**planned / not implemented**; do not add them to current feature lists or
partial production modules until a separate authorized implementation includes
its full safety, privacy, ownership, persistence, platform, and test design.
