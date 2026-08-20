# Compact/Full Mode and frozen application manual validation

This checklist covers the Compact default, deliberate Full consent, immediate
downgrade, and the existing frozen Windows application path. Automated tests,
mocked adapters, source inspection, compile checks, and a successful build do
not count as manual observation. Do not mark an item passed unless its behavior
was directly observed on the named instance.

Use a disposable Notepad document and an environment where synthetic keyboard
input is explicitly safe. Do not run the external demonstration on a machine or
session where changing foreground focus could affect private or important work.
Record the executable, platform, architecture, config state, and result for each
performed item. Preserve all unchecked entries as **NOT PERFORMED**.

## Source/profile flow

1. [ ] **Fresh config launches Compact — NOT PERFORMED.** Start Agetha without a
   `COMPACT_MODE` override and observe Compact Mode on.
2. [ ] **Classic/upstream-compatible presentation — NOT PERFORMED.** Confirm the
   visible companion and Compact Dashboard use the existing basic presentation;
   do not claim pixel-identical upstream behavior.
3. [ ] **Computer Use is not running — NOT PERFORMED.** With advanced feature
   flags configured either way, confirm no Computer Use owner/session starts.
4. [ ] **Process Awareness is not running — NOT PERFORMED.** Confirm it does not
   poll or publish lifecycle observations while Compact is active.
5. [ ] **Terminal Sentinel is not running — NOT PERFORMED.** Confirm it does not
   consume OCR events or show Sentinel notifications in Compact.
6. [ ] **No planner provider call — NOT PERFORMED.** Observe provider accounting
   and confirm Compact startup/settings activity schedules no planner or recovery
   request.
7. [ ] **Open Dashboard — NOT PERFORMED.** Confirm the Compact Mode switch is on
   and Full-only sections are hidden.
8. [ ] **Toggle Compact off — NOT PERFORMED.** Request off and confirm Full
   capabilities remain disabled while consent begins.
9. [ ] **First warning appears — NOT PERFORMED.** Confirm accurate Win95-style
   wording: advanced OS integration may become available, but safety remains on.
10. [ ] **Reduced motion disables shake — NOT PERFORMED.** With reduced motion
    enabled, confirm the dialog/window does not shake and uses a non-motion cue.
11. [ ] **No keeps Compact — NOT PERFORMED.** Choose No (also exercise Escape or
    close if safe) and confirm the switch/profile remains Compact.
12. [ ] **Toggle off again — NOT PERFORMED.** Re-enter the flow and confirm a new
    consent generation starts without activating Full.
13. [ ] **First Yes enters demo only — NOT PERFORMED.** Choose Yes and confirm
    Full remains disabled during the demonstration.
14. [ ] **Notepad opens — NOT PERFORMED.** Confirm the fixed Windows Notepad
    target is launched without another arbitrary application.
15. [ ] **Only the fixed warning is typed — NOT PERFORMED.** Confirm the exact
    built-in consent warning appears, with no user/clipboard/OCR/web/model text
    and no automatic Enter/submit action.
16. [ ] **Focus change aborts typing — NOT PERFORMED.** On a safe disposable run,
    change focus before the effect boundary and confirm zero text reaches the new
    foreground window. Continue through the in-app fallback only if desired.
17. [ ] **Final confirmation appears — NOT PERFORMED.** After success or allowed
    fallback, confirm **Stay in Compact Mode** and **Enable Full Mode** are shown.
18. [ ] **Stay Compact leaves advanced features off — NOT PERFORMED.** Choose the
    Compact option and confirm no advanced service partially starts.
19. [ ] **Enable Full requires the complete flow — NOT PERFORMED.** Repeat the
    sequence and choose **Enable Full Mode** only at the final confirmation.
20. [ ] **Full Dashboard appears — NOT PERFORMED.** Confirm applicable advanced
    sections, System Monitor, and Senses become visible without inventing absent
    features.
21. [ ] **Full config persists — NOT PERFORMED.** Confirm the existing atomic
    config patch writes `COMPACT_MODE=no` while preserving comments and unrelated
    lines.
22. [ ] **Restart preserves Full — NOT PERFORMED.** Restart and confirm Full is
    restored without replaying the Notepad ceremony.
23. [ ] **Enable Compact during active Computer Use — NOT PERFORMED.** In a safe
    disposable Full session, start a harmless Computer Use task and switch
    Compact on while work is active.
24. [ ] **Computer Use stops before another effect — NOT PERFORMED.** Confirm
    transition blocks effects first, cancels the session/planner/recovery, and
    late results cause zero keyboard or mouse input.
25. [ ] **Compact presentation returns — NOT PERFORMED.** Confirm Full-only UI is
    hidden and Process Awareness, Terminal Sentinel, and advanced observers stop.
26. [ ] **Restart preserves Compact — NOT PERFORMED.** Restart and confirm
    `COMPACT_MODE=yes` remains effective.

## Frozen `.exe` flow

The checked-in `main.spec` currently names its output `main.exe`, not
`Agetha.exe`, and declares no bundled data files. Use the actual locally built
artifact name and stage required sibling assets/config according to the existing
packaging layout. Merely finding a spec or an old binary is not a performed
test. On ARM64 Windows, record x64/AMD64-under-Prism separately from native
ARM64; do not label the former native.

27. [ ] **Launch packaged executable from another working directory — NOT PERFORMED.**
    Start the frozen app while the process current directory is not
    the executable directory.
28. [ ] **Frozen assets load — NOT PERFORMED.** Confirm required UI assets resolve
    through the packaged/executable layout rather than the current directory.
29. [ ] **Frozen config reads and writes correctly — NOT PERFORMED.** Confirm
    mutable config/state is beside the executable at the existing writable
    location and not in `_MEIPASS` or the launch directory.
30. [ ] **Frozen Compact default — NOT PERFORMED.** With a fresh frozen config,
    confirm Compact Mode is on and advanced systems remain inactive.
31. [ ] **Frozen Dashboard — NOT PERFORMED.** Open it and confirm the switch and
    Compact/Full presentation models behave as in source mode.
32. [ ] **Frozen consent UI — NOT PERFORMED.** Confirm both warnings/fallback and
    final decision work without Python, repository helper scripts, or provider
    calls. Perform the Notepad typing portion only in an explicitly safe session.
33. [ ] **Frozen self-target detection — NOT PERFORMED.** Confirm Unicode typing,
    window control, and Computer Use refuse the running `main.exe`/`Agetha.exe`
    process and do not broadly refuse unrelated Python applications.
34. [ ] **Clean frozen exit — NOT PERFORMED.** Close during ordinary Compact
    operation and, if safely exercised, during consent/Full activity; confirm
    timers, dialogs, workers, and the process terminate cleanly.

## Run record

| Field | Value |
|---|---|
| Date | Not performed |
| Tester | Not performed |
| Source commit/build | Not performed |
| Launch type and executable name | Not performed |
| OS / desktop session | Not performed |
| Interpreter/process architecture | Not performed |
| Native OS architecture | Not performed |
| Config/profile precondition | Not performed |
| Items performed | None |
| Result/limitations | All 34 items NOT PERFORMED |
