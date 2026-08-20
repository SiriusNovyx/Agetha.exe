# Agetha developer documentation

Start here when changing the repository. These documents summarize the
project-owned source tree so routine work does not require rereading
`main.py`, `ai_engine.py`, or every supporting module.

Last code-map audit: 2026-08-13.

## Supported platform

Official targets are Windows 10/11 x64, Windows 11 ARM64/Snapdragon through x64
Python under Prism, and Linux desktop environments covered by the existing
Linux paths. macOS is retired and unsupported as of v5.5.5. Windows and Linux
share the unit/compile CI matrix; Surface/ARM Prism remains a separate manual
validation target because hosted Windows runners are x64.

## Read by task

| Need | Read first | Then inspect |
|---|---|---|
| Understand the whole application | [Architecture](architecture.md) | [Runtime flows](runtime_flows.md) |
| Find the file or symbol to edit | [Module reference](module_reference.md) | The named source file |
| Add or change an AI command | [Runtime flows — command dispatch](runtime_flows.md#ai-response-and-command-dispatch) | `ai_engine.py`, `command_handlers.py`, `command_guard.py` |
| Change startup, shutdown, timers, or threads | [Runtime flows](runtime_flows.md) | `main.py`, relevant controller |
| Change OCR or screen context | [Architecture — screen monitoring](architecture.md#screen-monitoring-and-ocr) | `screen_reader.py`, `screen_monitoring.py`, `ocr_backends/` |
| Change Unicode text entry | [Runtime flows — Unicode typing](runtime_flows.md#unicode-type_text-flow) | `unicode_typing.py`, `command_handlers.py`, `command_guard.py`, `typing_preview.py` |
| Change observations or interruption policy | [Architecture — local observation and presence](architecture.md#local-observation-and-presence) | `observation_bus.py`, `presence_etiquette.py`, `main.py` |
| Change bounded multi-message turns | [Continuation Engine](continuation_engine.md) | `continuation.py`, `read_only_tools.py`, request profiles, `main.py` |
| Change process/application awareness | [Continuation Engine — process-aware continuation](continuation_engine.md#process-aware-continuation) | `process_awareness.py`, Observation Bus integration |
| Change Computer Use Lite | [Computer Use Lite](computer_use.md) | `agetha/computer_use/`, guarded Unicode typing, status UI, `main.py` |
| Change Compact/Full policy or consent | [Compact and Full profiles](compact_full_mode.md) | `capabilities.py`, `capability_consent.py`, `full_mode_consent.py`, dashboard/Senses, `main.py` |
| Audit source/frozen behavior | [Compact and Full profiles — source and frozen behavior](compact_full_mode.md#source-and-frozen-application-behavior) | `app_config.py`, `self_identity.py`, `windows_notify.py`, existing `*.spec` files |
| Change Terminal Sentinel | [Runtime flows — Terminal Sentinel](runtime_flows.md#terminal-sentinel-flow) | `terminal_sentinel.py`, existing screen event path, popup UI |
| Change the Senses panel | [Architecture — UI](architecture.md#ui-architecture) | `senses_panel.py`, dashboard callback, capability tests |
| Diagnose Ubuntu Xorg/Wayland GUI or OCR | [Linux desktop support](linux_support.md) | `linux_session.py`, `screen_reader.py`, `w95_window.py` |
| Change moods, GIFs, glow, motion, or window chrome | [Module reference — UI](module_reference.md#agethaui) | `main.py`, `agetha/ui/`, root `agent.md` |
| Change memory, dreams, tasks, or emotions | [Architecture — persisted state](architecture.md#persisted-state) | `agetha/core/`, `agetha/features/tasks.py` |
| Change configuration | [Development guide](development.md#configuration-and-secrets) | `app_config.py`, `config.txt`, dashboard settings |
| Review Fast Mode security or recover it | [Fast Mode security and recovery](fast_mode_security.md) | `fast_mode_profile.py`, Medic/CLI, security tests |
| Fix Windows ARM or launcher behavior | [Development guide](development.md#launcher-and-windows-arm) | `Medic_Checker.ps1`, `medic_helper.py`, launcher tests |
| Choose and run tests | [Development guide — test map](development.md#test-map) | The closest `tests/test_*.py` file |
| Manually validate Polyglot Presence | [Polyglot Presence manual checklist](testing/polyglot_presence_manual.md) | Record platform and result for every performed item |
| Manually validate Continuation/Process/Computer Use | [Computer Use manual checklist](testing/computer_use_manual.md) | Leave every item unperformed until it is directly observed |
| Manually validate Compact/Full/frozen | [Compact/Full manual checklist](testing/compact_full_mode_manual.md) | All 34 items begin NOT PERFORMED; source mocks and builds do not count |
| Review deferred A–O concepts | [Polyglot Presence future roadmap](roadmap/polyglot_presence_roadmap.md) | All entries are planned / not implemented |
| Configure explicit Unlimited-OCR | [Unlimited-OCR service guide](unlimited_ocr_server.md) | `unlimited_ocr_backend.py` |
| Review the current release | [v5.7 release notes](releases/v5.7.md) | The linked implementation and test suites |

## Source-of-truth hierarchy

When documentation and code disagree, use these sources in order:

1. Runtime behavior and safety checks in source code.
2. Tests that lock down that behavior.
3. Built-in defaults in `agetha/app_config.py` (`DEFAULT_CONFIG`).
4. This technical documentation.
5. Root `README.md` for user-facing setup and feature descriptions.
6. Root `agent.md` for character identity, asset rules, and contributor intent.

`config.txt` is a user-editable runtime configuration, not a canonical list of
defaults. Never copy values from `.env` or runtime files under `memory/` into
documentation, tests, or logs.

## Non-negotiable invariants

- All risky commands continue through `CommandGuard`; unknown commands default
  to the danger tier.
- `ENABLE_COMMAND_EXECUTION` and feature-specific gates are never bypassed.
- Tk widgets are changed only on the Tk main thread. Workers marshal UI work
  through `root.after(...)`.
- Repeating `after` callbacks retain an ID and are cancellable during teardown.
- Shutdown is centralized and idempotent; resources stop before `root.destroy()`.
- Tesseract is the automatic OCR backend. Unlimited-OCR is explicit-only.
- External text (OCR, web pages, memories) remains labeled as untrusted prompt
  context and must not become automatic OS instructions.
- Language choice is presentation only: mirror the user's current language and
  approximate register without unnecessary translation, transliteration,
  gendered speech, honorifics, cultural particles, formality, or slang. Never
  globally rewrite exact user-provided, quoted, document, code, or command data.
- `type_text` remains Caution-gated, preserves its input string, revalidates its
  intended target, and never synthesizes Enter, Return, or Tab.
- Publishing an Observation never calls a provider, persists memory, opens UI,
  or grants command authority. Downstream eligibility is decided separately.
- Terminal Sentinel is disabled by default, watches nothing with empty
  allowlists, and makes no provider request before an explicit Explain action.
- Continuation sessions start only from direct user authority. `tool_result`
  remains untrusted and may select only the bounded read-only allowlist.
- Computer Use is disabled by default and requires an explicit direct-user
  session. Planner output cannot bypass deterministic policy, Command Guard, or
  PID/name/creation-time/HWND/bounds validation before each effect.
- Compact Mode is the default outer capability gate. Full-only services do not
  start or perform effects in Compact, even if an individual feature flag is on.
- Full Mode requires the final step of the explicit consent state machine. The
  fixed Notepad presentation cannot type arbitrary text, is not Computer Use,
  and calls no provider. Full never disables the existing safety boundaries.
- A Full-to-Compact transition invalidates effect/session generations before
  service cleanup. Late callbacks must not type, click, capture, or restart an
  advanced worker.
- Frozen code resolves mutable config/state beside the executable, not under
  `_MEIPASS` or from the process current directory. Existing specs are a
  packaging mechanism, not evidence of a successful current `.exe` build.
- Secrets live only in `.env`; `config.txt` rejects secret keys.
- Persistent JSON/state rewrites use atomic write/replace helpers where
  applicable.
- Do not grow `main.py` or `ai_engine.py` with reusable subsystems when a focused
  module already exists or can be added under `agetha/`.
- Every avatar GIF remains referenced by runtime mood maps and the asset coverage
  test.

## Repository boundaries

Project-owned source and documentation live in the root scripts, `agetha/`,
`tests/`, and `docs/`. The following are inputs or generated/private state and
should not be treated as source files:

- `venv/`, `__pycache__/`, and `*.pyc`
- `.env`
- runtime files under `memory/`
- binary media under `assets/` (their names and roles are documented in
  `agent.md`)
- `conversation.txt`

## Keeping these docs useful

When adding a module, command, persistence file, background worker, or config
category, update the matching table in this folder in the same change. Prefer
short responsibility descriptions and links to source over pasted code.
