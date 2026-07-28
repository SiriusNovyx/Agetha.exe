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

### Linux development launch

```bash
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python main.py
```

Tkinter and a native Tesseract executable may come from the OS package manager.
Screen capture also depends on the display server and installed screenshot
tools. Windows-only operations should report unsupported status rather than
preventing startup.

### Required local inputs

- Python 3.10-3.14; Medic currently recommends Python 3.13.
- The separately supplied `assets/` pack. It is ignored by Git in this checkout.
- At least one provider: Groq/OpenRouter credentials in `.env`, or a configured
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
- Put `GROQ_API_KEY_*`, `OPENROUTER_API_KEY`, and
  `UNLIMITED_OCR_API_KEY` only in `.env`.
- Boolean syntax accepts `yes`, `true`, `1`, or `on` and the corresponding
  disabled forms.
- Invalid typed values are discarded and safe defaults/clamps apply.
- `get_settings()` is cached. Several values are also copied to module constants
  during import, so do not promise hot reload unless the whole caller chain reads
  settings dynamically.
- Dashboard changes use `patch_config_keys()` and atomic replacement. It never
  edits API keys.

### Configuration groups

The following map includes every current built-in key family. Exact defaults are
in `DEFAULT_CONFIG`; user-facing descriptions are in the root README and
dashboard.

| Group | Keys |
|---|---|
| Provider and generation | `USE_LOCAL_AI`, `ENABLE_GROQ`, `ENABLE_OPENROUTER`, `OPENROUTER_MODEL`, `FASTER_MODE`, `GROQ_MODEL`, `LOCAL_AI_MODEL`, `LOCAL_AI_TIMEOUT`, `AI_TEMPERATURE`, `AI_MAX_TOKENS`, `AI_TOP_P`, `ENABLE_STREAMING`, `ENABLE_AMBIENT_POLLS` |
| Datetime context | `ENABLE_DATETIME_CONTEXT`, `DATETIME_INCLUDE_SECONDS`, `DATETIME_INCLUDE_TIMEZONE` |
| Command safety | `ENABLE_COMMAND_EXECUTION`, `ENABLE_WINDOW_CONTROL`, `ENABLE_COMMAND_CONFIRMATIONS`, `FORCE_CLOSE_AUTO_ALLOW`, `PROTECTED_PROCESSES`, `DRY_RUN_MODE` |
| Prompt/memory bounds | `MEMORY_CHARS`, `HISTORY_LIMIT`, `FILE_READ_CHARS`, `EPISODIC_PROMPT_LIMIT`, `EPISODIC_ENTRY_MAX_CHARS`, `EPISODIC_MAX_ENTRIES`, `ENABLE_LONGTERM_MEMORY`, `LONGTERM_MEMORY_MAX_RESULTS`, `LONGTERM_MEMORY_MAX_CHARS` |
| Web retrieval | `ENABLE_WEB_RAG`, `WEB_FETCH_MAX_CHARS`, `WEB_TIMEOUT_SEC`, `WEB_SEARCH_MAX_RESULTS` |
| Glitch visuals | `ENABLE_GLITCH_EFFECTS`, `GLITCH_MAX_DURATION_MS`, `GLITCH_DEFAULT_STYLE`, `GLITCH_MOOD_AUTO`, `GLITCH_FULLSCREEN` |
| Companion simulation | `ENABLE_COMPANION_STATS_CONTEXT`, `ENABLE_CIRCADIAN_RHYTHM`, `RHYTHM_NIGHT_START`, `RHYTHM_NIGHT_END`, `ENABLE_DREAMS`, `DREAMS_MAX_ENTRIES`, `ENABLE_TASKS`, `TASKS_MAX_ENTRIES` |
| Emotion model | `ENABLE_EMOTION_ENGINE`, `EMOTION_BASELINE_VALENCE`, `EMOTION_BASELINE_AROUSAL`, `EMOTION_BASELINE_TRUST`, `EMOTION_BASELINE_LONELINESS`, `EMOTION_DECAY_PER_HOUR`, `EMOTION_HISTORY_MAX` |
| Windows integrations/status/tray | `ENABLE_AUTOSTART_CONTROL`, `ENABLE_THEME_CONTROL`, `ENABLE_STATUS_PROVIDERS`, `STATUS_POLL_INTERVAL_SEC`, `ENABLE_TRAY`, `TRAY_BACKGROUND_CLOSE` |
| Presence and attention | `SCREEN_POLL_INTERVAL_SEC`, `TOUCH_COOLDOWN_SEC`, `WAKE_DELAY_SEC`, `LOAF_TIMER_MIN`, `ENABLE_ATTENTION_SNAP`, all `MOOD_SNAP_*_SEC` keys |
| Standard OCR | `ENABLE_SCREEN_READER`, `OCR_MAX_DIMENSION`, `OCR_FOCUSED_WINDOW_ONLY`, `OCR_CHANGE_DETECTION`, `OCR_CHANGE_THRESHOLD`, `OCR_FORCE_REFRESH_SECONDS`, `OCR_STATE_EXPIRY_SECONDS`, confirmation/cooldown/confidence keys, `OCR_PREPROCESSING`, `OCR_LANGUAGES`, `OCR_PSM`, exclusions/redaction, `INCLUDE_WINDOW_TITLE_IN_CONTEXT`, `TESSERACT_PATH`, `OCR_CUSTOM_PATTERNS`, `OCR_PAUSE_WHILE_TYPING_SEC` |
| Explicit deep OCR | `DEEP_OCR_BACKEND`, `UNLIMITED_OCR_SERVER_URL`, `UNLIMITED_OCR_MODEL`, `UNLIMITED_OCR_TIMEOUT_SECONDS`, `UNLIMITED_OCR_ALLOW_REMOTE`, `DEEP_OCR_MAX_OUTPUT_CHARS` |
| Window/UI | `WINDOW_TOPMOST`, `UI_SCALE`, `WINDOW_START_X`, `WINDOW_START_Y`, `SUBTITLE_CHAR_DELAY`, `ANIMATION_SPEED`, `WINDOW_MOVE_SMOOTH`, `WINDOW_MOVE_DURATION_MS` |
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

1. Add the documented default to `app_config.DEFAULT_CONFIG`.
2. Add the key to the relevant boolean/integer/float validation set.
3. Add or update one `AppSettings` property with enum validation and a sensible
   clamp. Missing/invalid input must not break startup.
4. Consume the typed property in the owning module.
5. Add the dashboard control only if user editing is useful; label restart
   behavior honestly.
6. Update `.env.example` only for a new secret. Ordinary settings belong in the
   config template and user documentation.
7. Add default, invalid-input, and clamp tests in the closest suite.
8. Update the configuration group above and root README if user-facing.

## Command and safety model

The tiers below are the current policy snapshot. `CommandGuard.TIER_MAP` is the
runtime source of truth.

### Safe

`add_task`, `change_animation_speed`, `change_mood`, `complete_task`,
`get_clipboard`, `glitch_overlay`, `idle`, `list_tasks`, `monitor_process`,
`move_window`, `open_browser`, `open_folder`, `open_url`, `play_emotion_sound`,
`play_virus_trivia`, `read_document`, `read_notepad`, `recycle_bin_status`,
`request_path`, `request_screen_read`, `search_memory`, `set_reminder`,
`show_error_gif`, `show_notification`, `snap_to_center`, `speak`, `system_info`,
`take_screenshot`, `view_dreams`, `view_emotions`, `view_memory`, `wake_user`.

### Caution

`analyze_screen_deep`, `clear_emotions`, `clear_memory`, `copy_to_clipboard`,
`fetch_webpage`, `list_dir`, `list_directory`, `open_app`, `open_file`,
`open_settings`, `play_sound`, `read_file`, `search_files`, `search_web`,
`set_clipboard`, `set_volume`, `set_wallpaper`, `show_dialog`,
`target_window_close`, `target_window_move`, `target_window_resize`, and
`type_text`.

### Danger

`create_file`, `create_folder`, `delete_file`, `force_close`, `lock_screen`,
`rename_file`, `restart`, `run_command`, `set_autostart`, `set_theme`, `shutdown`,
and `write_file`.

Confirmations deny after timeout. Unknown commands are Danger. Protected
processes include built-in critical Windows/Linux names plus Python and Agetha;
user additions extend rather than replace that set. `FORCE_CLOSE_AUTO_ALLOW`
never authorizes protected/self targets. Dry-run is an additional user-visible
decision path, not a way around confirmation or feature gates.

### Adding an AI command

A command is incomplete until all relevant steps agree:

1. Add the name to `ai_engine.VALID_COMMANDS`.
2. Update the system prompt JSON/command contract and few shots only as needed.
3. Teach `AIEngine._parse()` which fields are accepted, normalized, bounded, and
   gated. Never pass an arbitrary model dictionary straight to an OS helper.
4. Register exactly one handler in `command_handlers.py`.
5. Use a focused function in `system_commands`, `platform`, `features`, or
   `core`; do not embed a reusable subsystem in the handler or `main.py`.
6. Assign an explicit `CommandGuard` tier. If omitted, it will be Danger, but an
   intentional entry documents policy.
7. Apply `ENABLE_COMMAND_EXECUTION` and any feature-specific switch. Window
   commands also require `ENABLE_WINDOW_CONTROL`.
8. Keep Agetha/self/protected target checks and ambiguity picking intact.
9. For Tk work, schedule the UI portion with `root.after()`. Put blocking work in
   a worker and provide cancellation/close checks.
10. For a context-producing command, use the bounded pending-context/deferred
    re-query pattern and an anti-recursion flag.
11. Add registration, parser/gate, guard-tier, denial, and handler tests.
12. Update README command tables and this tier snapshot.

Do not make mood, affection, infection level, or emotional history influence
command authorization.

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

Run both OCR suites after any change; the 61-case reliability suite is the
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

Medic's compile/import checks are useful environment diagnostics, but they do not
replace the test suite.

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
2. Verify direct Groq, OpenRouter, and local Ollama modes that are actually
   configured; do not expose keys in logs/screenshots.
3. Inspect direct and ambient prompts for compact datetime and redacted screen
   context.
4. Exercise focused capture, unchanged-frame suppression, excluded/Agetha
   windows, and a focus change during OCR.
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
12. Start on Linux with unavailable Windows/alpha/window-control features and
    verify graceful degradation.

Record which steps were actually performed and the platform used.

## Platform limitations and review hotspots

| Area | Current limitation or caveat |
|---|---|
| Linux window control | Most external window geometry is implemented for Windows; handlers use limited `wmctrl`/`pkill` fallbacks where available. |
| Linux/Wayland capture | Depends on compositor permissions and installed Spectacle/grim/GNOME tools; focused-window geometry is less reliable than Win32. |
| Window alpha/chrome | CRT alpha and borderless native styling are best-effort off Windows; close falls back to immediate cleanup. |
| Tesseract | Python package alone is insufficient; the native executable and requested language data must exist. |
| Voice shutdown | Listener/recognition workers are daemon threads and stop by event/timeouts rather than a blocking UI-thread join. Keep operations bounded. |
| Screen own-window handle | Own-window exclusion is best when the native handle is cached/passed from Tk; avoid adding worker-side Tk calls to resolve it. |
| Dashboard | Multiple dashboards may open. Its tracked callback list is cleared on close but can grow during a long session; keep new pollers sparse. |
| Web fetch | Bounded but currently does not reject private/loopback HTTP targets at the transport layer; retain its disabled default and Caution confirmation. |
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
