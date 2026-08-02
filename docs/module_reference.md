# Module and file reference

This is the repository-wide lookup table. Start here to identify the smallest
file to inspect or edit. It covers project-owned source, tests, launchers,
documentation, asset categories, and generated-state contracts as audited on
2026-07-28.

Private/runtime values from `.env`, `memory/`, and `conversation.txt` are not
reproduced here.

## Root files

| File | Responsibility and important entry points |
|---|---|
| [main.py](../main.py) | Composition root. `BleepPlayer`, `GifPlayer`, `SubtitleRenderer`, `AgethaPopup`, and `CompanionApp`; owns Tk lifecycle, AI turns, state/GIF selection, polling, input, controller wiring, and graceful shutdown. Read [Runtime flows](runtime_flows.md) before inspecting it. |
| [agent.md](../agent.md) | Maintainer intent, character visual identity, all 19 avatar GIFs, mood/state mapping, safety invariants, known asset caveats, and quick checks. Some inventory/version prose may lag code; runtime maps remain in `main.py`. |
| [README.md](../README.md) | User-facing installation, configuration, features, commands, troubleshooting, credits, and support links. Do not use its historical module/test counts as a code-generation source. |
| [config.txt](../config.txt) | User-editable active/sample settings. It is not the canonical defaults file and may contain locally enabled features. Canonical defaults and clamps are in `app_config.DEFAULT_CONFIG`. |
| [.env.example](../.env.example) | Secret-name template for Groq keys 1-10, OpenRouter, and optional Unlimited-OCR authorization. Actual `.env` is private. |
| [.gitignore](../.gitignore) | Excludes secrets, runtime state, conversations, assets, virtual environments, caches, and editor data. Assets therefore require separate distribution/licensing care. |
| [requirements.txt](../requirements.txt) | Python package pins for UI/image/OCR/network/system features and installed voice/DnD stack; some TTS engines remain commented optional dependencies. Feature flags can still make an installed package runtime-optional. |
| [Medic_Checker.bat](../Medic_Checker.bat) | PowerShell 5.1 wrapper that invokes `Medic_Checker.ps1` with execution-policy bypass and pauses on failure. |
| [Medic_Checker.ps1](../Medic_Checker.ps1) | Mutating Windows setup/health launcher. Detects native/interpreter architecture, selects x64 Python under ARM64 Prism, creates/rebuilds `venv`, installs packages, checks Tesseract/assets/config, compiles modules, creates optional shortcuts, and launches Agetha. |
| [medic_helper.py](../medic_helper.py) | Machine-readable helper for Medic. Commands: `platform`, `python_arch`, `env`, `config`, `config_secrets`, `voice`, `dnd`, `tts`, `features`, `realism`, `deep_ocr`, `openrouter`, `toast_shortcut`, and `autostart`. Architecture output separates interpreter, native OS, build tag, reported machine, and pointer bits. |
| [Run_Agetha_Admin.ps1](../Run_Agetha_Admin.ps1) | Explicit UAC-confirmed wrapper for controlling elevated windows. Elevation broadens OS authority but does not bypass command guards. |
| [LICENSE](../LICENSE) | GNU GPL version 3 license text. Preserve it and upstream/fork attribution. |
| `conversation.txt` | Generated AI-turn log, truncated when a new `AIEngine` is constructed and then appended during the session. It is private runtime data, not source. |

### Launcher side effects

The least-mutating post-setup launch is `python main.py`. Medic is intentionally
more active: it may contact release/package endpoints, install Python and Python
packages, create a virtual environment and runtime files, repair shortcuts, and
then launch the app. Its architecture selection must stay synchronized with
`medic_helper.py` and `tests/test_medic_arch.py`.

## `agetha/` package root

| File | Responsibility and important symbols |
|---|---|
| [agetha/__init__.py](../agetha/__init__.py) | Package marker and package `__version__`. Do not bump it merely to mirror an unrelated upstream release. |
| [agetha/app_config.py](../agetha/app_config.py) | Authoritative `DEFAULT_CONFIG`, `FAST_MODE_OVERRIDES`, parser diagnostics, `AppSettings`, cached runtime overlays, structural/atomic config updates, secret filtering, type validation, and clamps. |
| [agetha/utils.py](../agetha/utils.py) | Logging, `write_atomic()`, icon/native dialog helpers, simple env loading, compatibility config creation, platform flags, and refreshable legacy timing/window constants. |

### Configuration symbols

`ConfigLoadResult` records parse diagnostics. `default_config_dict()` converts
the built-in template to key/value defaults. `AppSettings` owns typed properties;
callers should not independently reinterpret a setting. `get_settings()` is
cached, and some `main.py` constants are captured at import, so many dashboard
changes intentionally require restart.

Importing `agetha.utils` loads settings and can create a missing `config.txt`.
Keep that side effect in mind in isolated tests.

## `agetha/commands`

| File | Responsibility and important symbols |
|---|---|
| [commands/__init__.py](../agetha/commands/__init__.py) | Package marker only; no eager imports. |
| [command_guard.py](../agetha/commands/command_guard.py) | `CommandGuard`: Safe/Caution/Danger classification, dry-run descriptions, native/Tk confirmations, timeout-deny behavior, protected-process checks, and force-close policy. Unknown commands default to Danger. |
| [command_handlers.py](../agetha/commands/command_handlers.py) | `DispatchCtx`, `HANDLERS`, `register()`, and `dispatch()`. Contains registered handlers for UI, files, clipboard, process/window, browser/web, system, memory, effects, tasks/emotions, Windows integration, and OCR. Coordinates feature gates, guard calls, stats/emotion updates, deferred re-query, Tk handoffs, and results. |
| [system_commands.py](../agetha/commands/system_commands.py) | Guarded URL/clipboard/folder access, system info, volume/wallpaper, scoped file search, typing, lock/shutdown/restart, reminders, notifications, and screenshot paths. Uses supported Windows and existing Linux paths where implemented; Windows-only operations degrade safely on Linux. |

The complete command change contract is in
[Adding an AI command](development.md#adding-an-ai-command). Safety policy belongs
outside low-level OS helpers; never invoke a destructive helper directly from
model output.

## `agetha/core`

| File | Responsibility and important symbols |
|---|---|
| [core/__init__.py](../agetha/core/__init__.py) | Package marker only; callers import concrete modules. |
| [ai_engine.py](../agetha/core/ai_engine.py) | `_LocalOllamaClient`, `_OpenRouterClient`, `AIEngine`, `VALID_MOODS`, `VALID_COMMANDS`, system prompts/few shots, screen-context wrapper, provider/retry flow, `_build_prompt()`, `_parse()`, `query()`, and `query_streaming()`. |
| [external_context.py](../agetha/core/external_context.py) | `PreparedExternalContext` and `prepare_external_context()`; shared fail-closed redaction and truncation for untrusted provider context. |
| [file_drop.py](../agetha/core/file_drop.py) | `PreparedFileDrop` and `prepare_file_drop()`; bounded local file validation, sensitive/binary policy, and path-safe provider metadata. |
| [request_context.py](../agetha/core/request_context.py) | Structured request origins plus compact prompt rendering and request-profile selection. |
| [fast_mode_profile.py](../agetha/core/fast_mode_profile.py) | Schema/profile-versioned Fast Mode validation, no-follow cross-process locking, post-lock path revalidation, activation/restoration transactions, drift/conflict recovery, structured audits, health inspection, cached original/forced-value access, and portable status/reconcile/restore CLI. |
| [time_context.py](../agetha/core/time_context.py) | `local_now(clock)` and `build_datetime_context(...)`; injectable local clock, weekday/ISO date, optional seconds, timezone name fallback, and UTC-offset formatting without network calls. |
| [memory_system.py](../agetha/core/memory_system.py) | Static soul plus bounded episodic memory. `load_soul()`, `log_memory()`, recent/selective clear/display/stat/prompt helpers, and `build_system_prompt()`. Uses a lock and atomic rewrites. |
| [memory_search.py](../agetha/core/memory_search.py) | Append-only long-term JSONL, mtime/size cache, pure-Python BM25-like search, one-shot session recap, and untrusted bounded prompt formatting. |
| [companion_stats.py](../agetha/core/companion_stats.py) | Fictional registry/relationship counters, host tone/CPU suggestions, infection perk, prompt summary, event updates, cache and atomic JSON persistence. Optional `psutil`. |
| [rhythm.py](../agetha/core/rhythm.py) | Stateless circadian phases, mood suggestion, and compact prompt context; configurable night boundary including midnight wrap. |
| [dreams.py](../agetha/core/dreams.py) | Builds surreal bounded dream entries from memory fragments, persists JSONL, and provides a process-local one-shot wake-recall queue. |
| [emotion_engine.py](../agetha/core/emotion_engine.py) | Persistent valence/arousal/trust/loneliness model with baselines, inertia, wall-time decay, absence milestones, derived mood/stage, injectable clock, history integration, and atomic state. Never changes permissions. |
| [emotional_history.py](../agetha/core/emotional_history.py) | Sanitized, weighted relationship events; deterministic summaries, age decay, bounded compaction, remove/clear/relevance/display APIs, and atomic JSONL rewrites. Raw user text is not copied as a trusted summary. |
| [audit_log.py](../agetha/core/audit_log.py) | Local bounded append-only audit entries for user-visible system changes and secret-free Fast Mode transitions. `log_audit()` accepts an optional owned audit path and returns success rather than raising; `read_audit()` skips malformed rows and returns newest first. |

### `AIEngine` entry points

- `query(...)` and `query_streaming(...)` are the normal provider-independent
  interfaces.
- `get_token_status()` supplies the placeholder/status estimate.
- `read_document()`, `write_file()`, and `monitor_process()` remain compatibility
  helpers used by handlers.
- `_build_prompt()` is the single place to add bounded AI context.
- `_parse()` is the model-output trust boundary. It does not replace the command
  guard.

`AIEngine` does not serialize concurrent queries. `CompanionApp` owns that
serialization.

## `agetha/features`

| File | Responsibility and important symbols |
|---|---|
| [features/__init__.py](../agetha/features/__init__.py) | Package marker; no eager optional imports. |
| [tasks.py](../agetha/features/tasks.py) | Locked, bounded `memory/tasks.json` CRUD. `add_task()`, `complete_task()`, `get_tasks()`, pending count, display and prompt formatting. Corrupt state repairs safely. |
| [status_providers.py](../agetha/features/status_providers.py) | Default-off coarse battery/disk/network observations; runtime pause, poll throttling, edge-triggered pending queue, and one-shot prompt output. It does not capture content. |
| [tray_scaffold.py](../agetha/features/tray_scaffold.py) | Lazy optional `pystray` integration. Start/stop/availability/background-close functions; Open, pause status, Settings, and Exit actions all marshal Tk work with `root.after()`. |
| [tts_player.py](../agetha/features/tts_player.py) | `TTSPlayer` queue worker and `VoiceOutputCoordinator`. Supports pyttsx3, edge-tts, and Kokoro normalization/fallback plus pygame playback, temporary-file cleanup, pause/resume/stop, and bleep/TTS routing. |
| [web_rag.py](../agetha/features/web_rag.py) | Gated DuckDuckGo HTML search and bounded static-page fetch, HTML-to-text extraction, time/result/character/byte caps, and explicit untrusted prompt wrappers. Network failures return structured safe results. |

Notes:

- edge-tts uses a network service; pyttsx3 is local; Kokoro needs its own runtime
  and voice assets.
- `web_rag` accepts HTTP(S) and follows redirects. It does not itself reject
  private/loopback destinations, so the feature gate and Caution confirmation
  remain important boundaries.
- Tray background close is honored only when a tray is actually running.

## `agetha/platform`

| File | Responsibility and important symbols |
|---|---|
| [platform/__init__.py](../agetha/platform/__init__.py) | Package marker only. |
| [linux_session.py](../agetha/platform/linux_session.py) | Side-effect-free X11/Wayland environment and screenshot-capability policy; never connects to X or exposes Xauthority data during import. |
| [screen_reader.py](../agetha/platform/screen_reader.py) | `PatternDef`, `PatternMatch`, pattern registry, focused-window/monitor discovery, capture fallback order, `ScreenReader`, standard/deep OCR orchestration, current matches vs new events, state publication, redaction, stale-result rejection, and stop lifecycle. |
| [screen_monitoring.py](../agetha/platform/screen_monitoring.py) | Pure reliability helpers: immutable `CapturedFrame`, `ProcessedOCRImage`, per-window/event state, preprocessing/scales, thumbnail difference, `ScreenChangeDetector`, `PatternEventTracker`, exclusions, and secret redaction. |
| [voice_input.py](../agetha/platform/voice_input.py) | Microphone settings/discovery/probe, PyAudio-to-sounddevice fallback, Win95 `MicPickerDialog`, `VoiceInput` listener, Google STT, and locked singleton faster-whisper loading. |
| [window_control.py](../agetha/platform/window_control.py) | External-window matching/ranking/picking and move/resize/close/kill operations through Windows Win32 and existing Linux process/window-tool paths. Synchronous geometry animation must remain on a worker; unavailable Linux tools fail safely. |
| [autostart.py](../agetha/platform/autostart.py) | Visible current-user Startup-folder `.lnk` management. Validates ownership/target containment and refuses foreign/malformed shortcut mutation. No service, task, or Run-key persistence. |
| [win_integration.py](../agetha/platform/win_integration.py) | Allowlisted Windows Settings pages, current-user light/dark theme with atomic backup/rollback, and aggregate Recycle Bin status. Command gates/audit remain in callers. |
| [windows_notify.py](../agetha/platform/windows_notify.py) | AppUserModelID, Start Menu shortcut registration, trusted icon, XML-safe WinRT toast. Distinct from autostart. Non-Windows returns false. |

### ScreenReader public surface

- `capture_text(max_chars=3000, focused_only=True)`
- `capture_image(focused_only=True)`
- `capture_deep_text(...)` and `preserve_external_target()`
- `get_active_window_title()` and `redact_for_external_context()`
- `last_pattern_matches` for current direct context
- `last_new_pattern_events` for deduplicated ambient reactions
- `last_word_positions`, `last_capture_metadata`, and `last_monitor_status`
- `dominant_mood()` and the trigger properties
- idempotent `stop()`

Normal OCR has capture, standard-scan, and state locks. Deep OCR shares only
screenshot acquisition and otherwise preserves standard OCR state. This module
does not modify Tk widgets.

### `agetha/platform/ocr_backends`

| File | Responsibility and important symbols |
|---|---|
| [ocr_backends/__init__.py](../agetha/platform/ocr_backends/__init__.py) | Re-exports `OCRWord`, `OCRLine`, `OCRResult`, prompt formatter, and both backend classes. |
| [ocr_backends/base.py](../agetha/platform/ocr_backends/base.py) | Frozen structured OCR contracts and `format_deep_ocr_for_prompt()`, which removes a forged closing marker and wraps results as untrusted data. |
| [ocr_backends/tesseract_backend.py](../agetha/platform/ocr_backends/tesseract_backend.py) | Local `TesseractOCRBackend.analyze()`: structured word/line extraction, confidence filtering, exact scale/origin transformation, fallback plain text, and character cap. |
| [ocr_backends/unlimited_ocr_backend.py](../agetha/platform/ocr_backends/unlimited_ocr_backend.py) | `UnlimitedOCRBackend`; URL/locality validation, explicit remote opt-in, proxy-environment isolation, optional bearer auth, bounded JSON/SSE parsing, categorized errors, temp PNG lifecycle, and session close. |

## `agetha/ui`

| File | Responsibility and important symbols |
|---|---|
| [ui/__init__.py](../agetha/ui/__init__.py) | Package marker only. |
| [display_scale.py](../agetha/ui/display_scale.py) | `resolve_ui_scale()` and `scale_px()`. Manual `UI_SCALE` clamps to 0.75-2.50; automatic scale considers display/DPI and clamps to 1.0-2.0. |
| [dashboard.py](../agetha/ui/dashboard.py) | `open_dashboard()`, trusted `open_project_link()`, notepad read/write. Owns System Monitor, Virus Registry, Notepad, About, and typed Settings tabs plus per-window callback cleanup. |
| [w95_window.py](../agetha/ui/w95_window.py) | Borderless Win95 Toplevel helpers, Windows caption stripping, and cross-platform map/deiconify refresh/fallback behavior. |
| [mood_effects.py](../agetha/ui/mood_effects.py) | `MOOD_COLOURS`, `mood_colour()`, and `MoodGlowController`: disabled/static/one-job pulse modes, subtle interpolation, slow manic color path, reduced-motion behavior, cancel/close lifecycle. |
| [motion_effects.py](../agetha/ui/motion_effects.py) | `MOTION_STEPS`, `MOOD_MOTION_MAP`, and `MoodMotionController`: named response-level geometry, probability/cooldown, drag/minimize/close/owner guards, one active job chain, monitor clamp, exact restoration. |
| [window_effects.py](../agetha/ui/window_effects.py) | `CRTCloseController`: duplicate guard, input/geometry cancellation, centered widen/vertical collapse/horizontal collapse/fade, tracked callbacks, reduced-motion/immediate fallback, exactly-once shutdown callback. |
| [glitch_overlay.py](../agetha/ui/glitch_overlay.py) | Gated visual-only Canvas effects, style/duration normalization, rare mood trigger, topmost transient overlay, bounded optional NumPy/Pillow static generation, and Windows-supported transparency behavior. |
| [virus_trivia.py](../agetha/ui/virus_trivia.py) | Five-question draggable Win95 trivia Toplevel; visual/gameplay only, no network/system mutation, and no repeating timer. |

Dashboard caveats: multiple instances are possible; its callback-ID list grows
until window close, although teardown cancellation is safe. Keep system polling
and optional voice enumeration bounded because they currently execute on Tk.

Glitch-overlay callbacks are owned by the transient Toplevel rather than a
separate controller ID collection; destroying the Toplevel ends them. New
long-lived effects should use the explicit tracked-controller pattern.

## Tests

The normal suite spans shared runtime, platform, UI, persistence, Fast Mode, and
security files. Counts change as regressions are added; use the coverage
descriptions to select a focused suite and report the number from the command
that was actually run.

| File | Current count | Coverage |
|---|---:|---|
| [tests/__init__.py](../tests/__init__.py) | - | Test package marker. |
| [test_atomic_persistence.py](../tests/test_atomic_persistence.py) | 9 | Atomic replacement/failure cleanup and corrupt-state repair/call-site use. |
| [test_fast_mode_profile.py](../tests/test_fast_mode_profile.py) | 34 | Disabled read-only startup, activation/restoration transactions, durable cleanup-only retry, crash/cache recovery, drift/CAS preference resets, structural preservation, `.env` isolation, migration, validation, permissions, and path safety. |
| [test_fast_mode_runtime.py](../tests/test_fast_mode_runtime.py) | 31 | Adaptive history/output profiles, complete tool/deep analysis with payload-safe retained answers, all-profile safety kernels, direct-only deep OCR, provider parity, bounded ambient events, Ollama options, pending presence context, and unchanged-ambient local skip. |
| [test_fast_mode_security.py](../tests/test_fast_mode_security.py) | 32 | Override/profile invariants, strict snapshot schema, no-follow lock opening, descriptor/path identity, TOCTOU injection, bounded locking, atomic-write states, disk-truth recovery, secret-free audits, and portable CLI behavior. |
| [test_fast_mode_ui_medic.py](../tests/test_fast_mode_ui_medic.py) | 23 | Managed dashboard state, coordinated save/close/transition/failure lifecycle, fresh-state consent, secret-free Medic JSON/conflict counts, cleanup-state wording, one-launch declined-action skips, and Medic required-file coverage. |
| [test_hybrid_ocr.py](../tests/test_hybrid_ocr.py) | 35 | OCR settings, Tesseract coordinates/confidence, Unlimited client security/errors/temp cleanup, deep integration and ambient block. |
| [test_medic_arch.py](../tests/test_medic_arch.py) | 6 | Architecture aliases, build-platform priority, ARM64-native/x64-Prism distinction, JSON output. |
| [test_phase1_qa.py](../tests/test_phase1_qa.py) | 8 | Dashboard jobs/notepad, memory dual-write/recursion gates, stats/memory basics. |
| [test_phase2_tts.py](../tests/test_phase2_tts.py) | 17 | TTS settings/clamps, coordinator modes/fallback, player non-raising lifecycle. |
| [test_phase3_web_rag.py](../tests/test_phase3_web_rag.py) | 9 | Web gates, bounded parsing, untrusted wrappers, mocked search/fetch and anti-recursion. |
| [test_phase3b_glitch.py](../tests/test_phase3b_glitch.py) | 8 | Imports/compile, style/duration clamps, disabled gate, parent scheduling. |
| [test_phase4_realism.py](../tests/test_phase4_realism.py) | 16 | Command wiring, stats/notepad/trivia, prompt suppression, mood/session recap, coding-assist safety, GIF coverage, compile. |
| [test_phase5_v4.py](../tests/test_phase5_v4.py) | 29 | Config, rhythm, dreams, tasks, prompt/guard/command wiring. |
| [test_phase6_v5.py](../tests/test_phase6_v5.py) | 87 | Emotion/history/concurrency, audit, autostart, Windows integration/rollback, status, tray, command wiring. |
| [test_quality_of_life.py](../tests/test_quality_of_life.py) | 39 | File-drop privacy, request origins, context sanitization, worker/AI arbitration, minimize recovery, picker lifecycle, and command safety. |
| [test_screen_monitoring_reliability.py](../tests/test_screen_monitoring_reliability.py) | 75 | Coordinate/origin/capture/concurrency/change/event/pattern/privacy/stale-result/backward-compatibility matrix. |
| [test_time_ui_effects.py](../tests/test_time_ui_effects.py) | 25 | Datetime context, new settings, display scale, glow/motion/CRT lifecycle, shutdown idempotence, optional real-Tk smoke. |

## Documentation

| File | Purpose |
|---|---|
| [docs/README.md](README.md) | Documentation entry point, task routing, source hierarchy, and non-negotiable invariants. |
| [docs/architecture.md](architecture.md) | Layer ownership, dependency boundaries, prompt/safety/OCR/UI/persistence architecture. |
| [docs/runtime_flows.md](runtime_flows.md) | Step-by-step startup, user/ambient AI turns, OCR, dispatch, speech, tray, and shutdown. |
| [docs/module_reference.md](module_reference.md) | This repository-wide file and symbol map. |
| [docs/development.md](development.md) | Setup, configuration, change checklists, validation commands, and platform limits. |
| [docs/unlimited_ocr_server.md](unlimited_ocr_server.md) | Explicit deep-OCR service configuration, loopback mock, deployment, privacy, and controlled failures. |
| [docs/releases/v5.7.md](releases/v5.7.md) | Quality-of-life privacy boundaries, request origins, lifecycle coordination, command hardening, and validation results. |
| [docs/releases/v5.5.5.md](releases/v5.5.5.md) | Fast Mode 2.0 profile, recovery, adaptive request budgets, safety boundaries, and v5.5.5 upgrade guidance. |
| [docs/fast_mode_security.md](fast_mode_security.md) | Fast Mode threat model, platform lock behavior, post-lock validation, durability, Windows ACL limits, ambiguous-write recovery, audit events, CLI exit codes, and CI coverage. |
| [docs/releases/v5.5.1.md](releases/v5.5.1.md) | Historical reliability, Windows ARM, high-DPI, UI lifecycle, and attribution notes for v5.5.1. |

External implementation plans informed the screen-monitoring and Fast Mode 2.0
work, but they are not checked into this repository and are not canonical
ongoing sources. The implemented contracts are documented here and locked by
the focused screen/OCR and Fast Mode test suites above.

## Assets

Binary assets are runtime inputs rather than Python source. The authoritative
current behavior maps are in `main.py`; the visual catalog and caveats are in
[agent.md](../agent.md#gif-asset-catalog-all-19-must-stay-wired).

| Files | Role |
|---|---|
| `idle-1.gif`, `idle-2.gif`, `idle-3.gif` | Neutral idle variants selected using companion state. |
| `talking-1.gif`, `talking-2.gif`, `talking-3.gif` | Neutral/intense, soft, and hype speech bands. |
| `happy.gif`, `happy-static.gif` | Happy animated and held states. |
| `sad.gif`, `sad-static.gif` | Sad/melancholic/vulnerable animated and held states. |
| `angry.gif`, `angry-static.gif` | Angry/dominant animated and held states. |
| `thinking.gif`, `thinking-static.gif` | Thinking and whisper/held states. |
| `surprised.gif` | Surprised/paranoid and wake transition. |
| `want.gif` | Excited/manic and drag-over state. |
| `loaf.gif`, `sleeping.gif` | Presence-rest progression. |
| `error.gif` | Avatar denial/error presentation (XP-style BSOD art). |
| `barrio.ttf` | Primary custom display/subtitle font. |
| `icon.ico` | Window, launcher, and notification icon. |
| `bsod.png` | Separate modern BSOD image used by a glitch-overlay style. |

There are 19 GIFs and 3 non-GIF assets in the current tree. Medic checks 21
required asset entries because its required list and optional/role policy are
not identical to a raw file count. Do not infer health-check requirements from a
generic directory count: update Medic and the GIF coverage test deliberately
when assets change.

Assets are ignored by Git in this checkout and have their own provenance/license
boundary. A fresh clone may need the separately distributed asset pack.

## Runtime/private state

| Path | Owner |
|---|---|
| `.env` | Private API secrets loaded by `app_config`; never document values. |
| `memory/soul.md` | `memory_system`; editable persona. |
| `memory/episodic_memory.json` | `memory_system`; bounded recent memories. |
| `memory/longterm_memory.jsonl` | `memory_search`; searchable append log. |
| `memory/companion_stats.json` | `companion_stats`. |
| `memory/emotional_state.json` | `emotion_engine`. |
| `memory/emotional_history.jsonl` | `emotional_history`. |
| `memory/dreams.jsonl` | `dreams`. |
| `memory/tasks.json` | `features.tasks`. |
| `memory/audit_log.jsonl` | `audit_log`. |
| `memory/settings.json` | `voice_input` microphone selection. |
| `memory/theme_backup.json` | `win_integration` theme rollback. |
| `memory/notepad.txt` | Dashboard notepad. |
| `memory/memory.txt` | Legacy condensed AI summary. |
| `conversation.txt` | Current-session diagnostic conversation log. |

Missing files are created by their owner when needed. A repository snapshot may
show only a subset. Preserve user data during development and never treat its
contents as fixtures unless a test explicitly creates an isolated temporary
store.
