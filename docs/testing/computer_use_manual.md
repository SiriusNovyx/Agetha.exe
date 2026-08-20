# Continuation, Process Awareness, and Computer Use manual validation

This is the Windows desktop smoke checklist for the bounded Continuation
Engine, Process Awareness, and opt-in Computer Use Lite phase.

**Current execution status (2026-08-11): NOT PERFORMED.** None of the 25 items
below has been observed for this checklist. Automated tests, compile checks,
mocks, code inspection, or results from the separate Polyglot Presence
checklist do not count as manual execution.

Use a supported non-elevated Windows 10/11 desktop and disposable test data.
`AGETHA_TEST_MODE=1` may be set only for the launched diagnostic process when a
targetable native frame is useful. Production behavior remains unchanged when
the variable is absent. Do not persist the variable in `config.txt`.

Compact Mode is on and Computer Use is individually disabled by default. If a
check requires Computer Use, first complete the separate deliberate Full Mode
consent flow, explicitly enable `ENABLE_COMPUTER_USE=yes`, use a disposable
allowlisted application, and restore Compact afterward. Never perform refusal
checks against a real
password manager, bank account, elevated console, credential, payment, or
security dialog; use a controlled mock/title/control fixture.

When a person performs an item, replace only that item's status and record the
platform/build, date, configuration deviations, and privacy-safe evidence. Do
not include screenshots or logs containing OCR, exact typing payloads,
clipboard data, full paths, provider prompts, or API keys.

## Windows checklist (25 items)

1. [ ] **Normal user chat — NOT PERFORMED.** With Computer Use left disabled,
   send a normal harmless chat message and confirm the ordinary startup,
   response, subtitle/speech, history, and idle flow still work.

2. [ ] **Status then final — NOT PERFORMED.** Start a bounded read-only goal and
   observe one user-visible status message followed later by a distinct final
   message from the same logical goal. Confirm the status does not close the
   session.

3. [ ] **WebRAG continuation — NOT PERFORMED.** With WebRAG explicitly enabled,
   ask a harmless search question. Observe “Let me search” or equivalent,
   bounded search/fetch work, and a later final answer. Confirm raw page text is
   not displayed as a tool-result message.

4. [ ] **Escape cancels continuation — NOT PERFORMED.** Press Escape while a
   continuation provider or read-only tool step is active. Confirm no later
   tool, final callback, or state-changing command occurs.

5. [ ] **New message preempts continuation — NOT PERFORMED.** Submit a second
   direct user message during a continuation. Confirm the old generation is
   cancelled and late old results are discarded while the newer request keeps
   its direct-user origin.

6. [ ] **Senses phase status — NOT PERFORMED.** Open Dashboard → Senses and
   confirm Continuation Engine, Process Awareness/mode, Computer Use
   enabled/active state, planner route/model, recovery model, active target,
   step, and last result are truthful and contain no raw OCR, payload, key, or
   full path. Confirm opening Senses makes no paid provider request.

7. [ ] **Foreground application — NOT PERFORMED.** Switch between two ordinary
   applications and confirm Process Awareness identifies the current foreground
   application without leaking full executable paths or private titles.

8. [ ] **Visible apps exclude services — NOT PERFORMED.** Request the visible
   application list and confirm interactive windows appear while background
   services are not presented as visible apps.

9. [ ] **Notepad lifecycle — NOT PERFORMED.** Open and close a disposable
   Notepad instance and confirm the visible/start/exit transition is detected
   once without provider calls or service-churn spam.

10. [ ] **Computer Use default off — NOT PERFORMED.** Start with the distributable
    default and request a desktop action. Confirm Computer Use does not start,
    no status session appears, and no pointer/keyboard effect occurs.

11. [ ] **Explicit Full plus feature enablement — NOT PERFORMED.** Complete Full
    consent, set `ENABLE_COMPUTER_USE=yes`, configure only the disposable allowed
    app if needed, restart when indicated, and confirm Senses reports the opt-in
    state without initiating a session.

12. [ ] **Open Notepad — NOT PERFORMED.** Explicitly ask Agetha to open Notepad.
    Confirm Computer Use Caution approval and feature gates remain in force,
    the deterministic shell-free launcher uses only the explicitly named app,
    and the resulting Notepad PID, basename, creation time, HWND, and bounds
    become the locked target.

13. [ ] **Request multilingual payload — NOT PERFORMED.** In the authorized
    disposable Notepad session, ask Agetha to type one balanced mixed-script
    vector such as `Hello — สวัสดี — こんにちは — مرحبا — 👋`. Confirm the planner
    and status surfaces expose only a payload reference, never the exact value.

14. [ ] **Exact multilingual output — NOT PERFORMED.** Observe the completed
    Notepad contents and confirm the exact requested vector appears, with no
    translation, transliteration, normalization, missing combining marks, or
    extra characters. Treat every language as a test vector, not a preference.

15. [ ] **No automatic Enter — NOT PERFORMED.** After the multilingual payload step,
    confirm no Enter, Return, or Tab was appended and no submission or command
    occurred.

16. [ ] **Focus change aborts click — NOT PERFORMED.** Arrange a harmless planned
    click, Alt-Tab before the effect boundary, and confirm target revalidation
    aborts the click. Confirm Agetha does not silently steal focus back.

17. [ ] **Target exit aborts action — NOT PERFORMED.** Close the target process
    after observation but before an effect. Confirm the action is prevented and
    the session reports target exit/change rather than using stale coordinates
    or a reused PID.

18. [ ] **STOP prevents late effects — NOT PERFORMED.** Press the Computer Use
    status window's STOP button while work or a provider call is active. Confirm
    cancellation is immediate and no later click, keypress, focus, scroll, or
    typing effect occurs when the worker returns.

19. [ ] **Escape stops Computer Use — NOT PERFORMED.** Start another harmless
    session, move focus into the target app, and press Escape. Confirm the
    session-scoped Windows hotkey uses the same cancellation owner as STOP,
    unregisters afterward, and prevents later effects. If registration is
    unavailable, record the limitation and use STOP; do not mark this passed.

20. [ ] **Low-confidence recovery — NOT PERFORMED.** Use a controlled fixture
    that deterministically produces an ambiguous/low-confidence control.
    Confirm the session reobserves before escalating, uses the primary recovery
    route only within its budget, and stops/asks the user rather than guessing
    a coordinate if ambiguity remains.

21. [ ] **Password-manager handoff — NOT PERFORMED.** Use a controlled mock
    process/title/control fixture labeled as a password manager. Confirm policy
    hands control to the user before any effect. Do not target real credentials
    or a real password manager.

22. [ ] **Elevated/security handoff — NOT PERFORMED.** Use a safe controlled
    elevated/admin/UAC/security fixture and confirm the session refuses or hands
    back before input. Do not attempt to automate the real secure desktop.

23. [ ] **Sentinel remains local — NOT PERFORMED.** Produce a harmless confirmed
    error in an explicitly allowlisted disposable terminal. Confirm Terminal
    Sentinel makes no provider request and starts no Computer Use session until
    the user explicitly selects Explain.

24. [ ] **Sentinel Explain has no Computer Use authority — NOT PERFORMED.** Select
    Explain for the disposable event and confirm the explanation may speak or
    show a popup but cannot start Computer Use, type, click, run a command, or
    grant authority. A later direct user “Fix it” is a separate request.

25. [ ] **Shutdown during planner work — NOT PERFORMED.** Close Agetha while a
    planner/recovery request is in flight. Confirm cancellation blocks late
    effects, the status window closes, Continuation/Process/Computer Use owners
    shut down idempotently, and Tk exits without an after-destroy error.

### Windows execution record

| Field | Value |
|---|---|
| Tester/date | Not performed |
| Windows edition/build | Not performed |
| Native OS architecture | Not performed |
| Python/process architecture | Not performed |
| Launcher and `AGETHA_TEST_MODE` | Not performed |
| Provider/planner route | Not performed |
| Config deviations restored | Not performed |
| Items passed/failed/skipped | 0 / 0 / 0; 25 unperformed |

## Linux Xorg notes

**Current status: NOT PERFORMED / DEGRADED.** Computer Use Lite cannot promise
the Windows target-lock guarantees on Xorg. Record the desktop, window manager,
`XDG_SESSION_TYPE`, `xdotool`, `wmctrl`, `xclip`/`xsel`, capture backend, and OCR
availability before testing.

- Process Awareness is best-effort. Foreground discovery may use `xdotool` and
  visible-window enumeration may use `wmctrl`; missing tools must produce an
  honest degraded/unavailable state.
- Unicode entry depends on the existing optional Xorg focus and clipboard
  tools. Clipboard success alone is not proof of automatic typing.
- Treat Computer Use as unavailable whenever PID/basename/creation-time,
  window identity, bounds, foreground state, capture, or effect-boundary
  revalidation cannot be established safely.
- If a development build reports the Xorg path available, repeat only harmless
  status, target-change, STOP, Escape, exact-payload, no-Enter, and shutdown
  checks against a disposable editor. Record every degraded or unavailable
  result; do not convert it to a pass based on mocks.

### Xorg execution record

| Field | Value |
|---|---|
| Tester/date | Not performed |
| Distribution/desktop/window manager | Not performed |
| `XDG_SESSION_TYPE` | Not performed |
| `xdotool` / `wmctrl` | Not checked |
| Clipboard and capture tools | Not checked |
| Result/limitations | Not performed; expected degraded |

## Linux Wayland notes

**Current status: NOT PERFORMED / UNAVAILABLE.** Autonomous Computer Use Lite is
unavailable on Wayland because unrestricted global capture, focus control, and
synthetic input cannot meet the same target-lock guarantees. This is an
expected compositor security boundary.

- Do not use `xhost +`, guessed Xauthority files, elevated launch, compositor
  security changes, hidden XWayland workarounds, or another bypass.
- Existing Unicode behavior may copy exact text with `wl-copy` for a manual
  `Ctrl+V`; that partial copy-only result is not a Computer Use session or a
  passed automatic-typing check.
- Process Awareness may report coarse process-only degradation, but it cannot
  substitute for foreground HWND/window/bounds validation.
- Senses should report Computer Use unavailable/degraded honestly and must not
  probe a paid provider simply to determine capability.

### Wayland execution record

| Field | Value |
|---|---|
| Tester/date | Not performed |
| Distribution/desktop/compositor | Not performed |
| `XDG_SESSION_TYPE` | Not performed |
| `wl-copy` and capture/portal status | Not checked |
| Result/limitations | Not performed; autonomous Computer Use unavailable |

## Result summary

Do not change this table to “passed” based solely on automated validation.

| Platform | Performed | Passed | Failed | Skipped/unavailable | Unperformed |
|---|---:|---:|---:|---:|---:|
| Windows checklist | No | 0 | 0 | 0 | 25 |
| Linux Xorg notes | No | 0 | 0 | 0 | All |
| Linux Wayland notes | No | 0 | 0 | 0 | All |
