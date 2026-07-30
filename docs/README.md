# Agetha developer documentation

Start here when changing the repository. These documents summarize the
project-owned source tree so routine work does not require rereading
`main.py`, `ai_engine.py`, or every supporting module.

Last code-map audit: 2026-07-29.

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
| Diagnose Ubuntu Xorg/Wayland GUI or OCR | [Linux desktop support](linux_support.md) | `linux_session.py`, `screen_reader.py`, `w95_window.py` |
| Change moods, GIFs, glow, motion, or window chrome | [Module reference — UI](module_reference.md#agethaui) | `main.py`, `agetha/ui/`, root `agent.md` |
| Change memory, dreams, tasks, or emotions | [Architecture — persisted state](architecture.md#persisted-state) | `agetha/core/`, `agetha/features/tasks.py` |
| Change configuration | [Development guide](development.md#configuration-and-secrets) | `app_config.py`, `config.txt`, dashboard settings |
| Review Fast Mode security or recover it | [Fast Mode security and recovery](fast_mode_security.md) | `fast_mode_profile.py`, Medic/CLI, security tests |
| Fix Windows ARM or launcher behavior | [Development guide](development.md#launcher-and-windows-arm) | `Medic_Checker.ps1`, `medic_helper.py`, launcher tests |
| Choose and run tests | [Development guide — test map](development.md#test-map) | The closest `tests/test_*.py` file |
| Configure explicit Unlimited-OCR | [Unlimited-OCR service guide](unlimited_ocr_server.md) | `unlimited_ocr_backend.py` |
| Review the current release | [v5.5.5 release notes](releases/v5.5.5.md) | The linked implementation and test suites |

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
