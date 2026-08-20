# Compact and Full capability profiles

Compact Mode is Agetha's default safe experience. It keeps the companion,
ordinary chat, memory, emotion/personality, WebRAG, and bounded read-only
continuation available according to their normal settings. Full Mode allows the
mod's advanced desktop integrations to become eligible, but it does not disable
Command Guard, confirmations, target locking, feature flags, cancellation, or
any other safety boundary.

The two profiles apply equally to `python main.py` and the repository's frozen
Windows application path.

## Central capability boundary

[`core/capabilities.py`](../agetha/core/capabilities.py) is the deterministic,
provider-neutral policy boundary. `CapabilityPolicy` derives an effective
profile from typed settings and returns a decision and reason for each
capability. `CapabilityController` owns the current policy, transition state,
and generation-bound effect authorizations.

The conceptual matrix is:

| Capability | Compact | Full |
|---|---|---|
| Chat | allowed | allowed |
| Basic memory | allowed | allowed |
| Basic emotion/personality | allowed | allowed |
| WebRAG | allowed when its feature gate allows it | allowed when its feature gate allows it |
| Read-only continuation | allowed when its feature gate allows it | allowed when its feature gate allows it |
| Terminal Sentinel | denied by Compact Mode | configurable |
| Process Awareness | denied by Compact Mode | configurable |
| Computer Use | denied by Compact Mode | configurable |
| Computer Planner and recovery | denied by Compact Mode | configurable |
| OS typing and application control | denied by Compact Mode | configurable |
| Background sensing | denied by Compact Mode | configurable |
| Advanced OS integration | denied by Compact Mode | configurable |
| Advanced Dashboard/Senses UI | hidden/denied by Compact Mode | allowed when its UI feature exists/is configured |

Compact is the outer gate. For example, `ENABLE_COMPUTER_USE=yes` does not make
Computer Use effective while Compact Mode is on. Full is not an instruction to
turn every feature on: existing individual gates still apply. A model or
provider response cannot change the profile or override a capability decision.

Command dispatch classifies commands through the same policy before guard or OS
preflight work. Effectful callers can carry a generation-bound authorization to
the immediate effect boundary. Starting a downgrade invalidates old
authorizations before cleanup, so a late callback cannot regain Full authority.

## Configuration and dashboard

`COMPACT_MODE=yes` is the built-in default. A missing key therefore starts in
Compact. The typed config API and the existing structural atomic patch preserve
comments, blank lines, unknown keys, and unrelated settings. Fast Mode does not
manage or override `COMPACT_MODE`.

A persisted `COMPACT_MODE=no` represents a previously completed consent flow
and starts in Full without replaying the demonstration at every launch. The
setting is deliberate user-local state, not a tamper-proof security claim.

The Dashboard always exposes the Compact Mode switch. In Compact it uses the
existing classic/upstream-compatible presentation and hides Full-only settings,
the System Monitor, and the Senses entry. In Full it shows the advanced surfaces
that actually exist and remain applicable. Hiding those surfaces is presentation
only; the central policy and lifecycle gates are the enforcement boundary.

If Senses is available in Full, it reports the effective capability profile and
reasons such as `Disabled — Compact Mode`. Collecting a snapshot reads known
state only. It must not capture the screen, enumerate processes merely for the
panel, call a provider, or start a disabled feature.

## Deliberate Full Mode consent

Turning Compact Mode off does not immediately activate Full. The pure state
machine in [`core/capability_consent.py`](../agetha/core/capability_consent.py)
uses this sequence:

```text
COMPACT
  -> FIRST_CONFIRMATION
  -> CONSENT_DEMO
  -> FINAL_CONFIRMATION
  -> FULL or COMPACT
```

The Win95 presentation in
[`ui/full_mode_consent.py`](../agetha/ui/full_mode_consent.py) explains that Full
enables advanced OS integration while all safety restrictions remain active.
Its optional shake is short, bounded, cancellable, and owned by Tk.
Reduced-motion mode uses a static attention treatment instead. Escape, No,
Cancel, window close, shutdown, or a stale generation returns safely to
Compact.

After the first Yes, Full is still disabled. Windows may run the narrow helper
in [`platform/full_mode_consent.py`](../agetha/platform/full_mode_consent.py):

1. launch exactly `notepad.exe` without a shell;
2. identify the created Notepad process and window;
3. revalidate PID, executable name, creation time, HWND, bounds, foreground
   state, process liveness, window validity, cancellation, shutdown, timeout,
   and the current consent generation;
4. type exactly the compiled `CONSENT_DEMO_MESSAGE`; and
5. return to Agetha for the final decision.

Its public run method accepts no application or text argument. The warning does
not come from user input, clipboard, OCR, a webpage, configuration, or model
output. The helper has no Computer Planner, recovery, WebRAG, arbitrary command,
general launcher, or Python-helper route. The entire consent sequence needs zero
provider calls.

If Notepad cannot be launched or validated, or if the target changes, no text is
sent. Agetha shows the warning in-app and lets the user either cancel or proceed
to the same final confirmation. A failed presentation never enables Full and
does not trap the user.

Only **Enable Full Mode** at the final confirmation persists
`COMPACT_MODE=no`, commits the Full policy, and starts individually configured
advanced services. **Stay in Compact Mode** and every earlier cancellation keep
the Compact policy active.

## Full-to-Compact downgrade

Returning to Compact needs no warning. The ordering is fail-closed:

1. mark the profile as transitioning to Compact and invalidate the Full
   generation;
2. block new OS effects immediately;
3. cancel Computer Use plus planner/recovery work;
4. stop Terminal Sentinel, Process Awareness, advanced observation, workers,
   and timers;
5. discard stale advanced callbacks and close/hide Full-only UI;
6. persist `COMPACT_MODE=yes` atomically; and
7. publish the completed Compact presentation/state.

The block occurs before waiting for any provider or worker. Once transition
begins, no new keyboard, mouse, or application-control effect may occur. The
same owned cancellation routines are reused by shutdown; callbacks remain
generation-checked and all Tk work stays on the owner thread.

## Source and frozen application behavior

[`app_config.py`](../agetha/app_config.py) resolves source state beside the
project package and, when `sys.frozen` is true, resolves mutable `config.txt`,
`.env`, `memory/`, logs, and sibling assets beside `sys.executable`. It does not
write mutable state under a temporary `_MEIPASS` extraction directory, and new
code must not depend on the process current working directory.

The consent helper launches the fixed Windows executable directly. It never
runs a Python helper with `sys.executable`; in a frozen process that value is
the Agetha executable itself and such a pattern could recursively relaunch the
application.

[`platform/self_identity.py`](../agetha/platform/self_identity.py) recognizes
owned PIDs/HWNDs first and the exact source/frozen aliases second. It covers
source `main.py` and the existing `main.exe`/`Agetha.exe` artifact names without
treating every `python.exe` as Agetha in a frozen build. Unicode typing, window
control, and Computer Use must retain this self-target refusal.

### Packaging audit caveat

The repository contains PyInstaller-style `main.spec`,
`ci_compile_check.spec`, and `medic_helper.spec`. The current `main.spec`
produces a console executable named `main`, declares no bundled data files, and
does not by itself stage the external `assets/` directory. No canonical release
or installer command is documented by these spec files alone. Their presence is
therefore evidence of an existing packaging mechanism, not proof that a current
`Agetha.exe` was built or smoke-tested.

Use the existing specs if a local build is intentionally validated; do not add a
new packager merely for this profile work. Keep assets beside the executable as
required by the current `BASE_DIR` strategy, do not publish generated binaries,
and report build and manual `.exe` results separately from source/unit results.
Windows ARM64 support remains the documented x64/AMD64 process under Prism path;
this work does not claim a native ARM64 executable.

See the [34-item manual checklist](testing/compact_full_mode_manual.md). Every
item starts unperformed and must be updated only after direct observation.
