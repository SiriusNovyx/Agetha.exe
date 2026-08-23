# Polyglot Presence manual validation

This is the release-facing desktop smoke checklist for the implemented
Polyglot Presence scope: language-neutral multilingual mirroring, exact Unicode
typing, Observation Bus, Presence Etiquette, opt-in Terminal Sentinel, and the
Senses Control Panel. Language samples are test vectors, not Agetha's preferred
language or a claim of equal model quality across languages.

**Current execution status (2026-08-11): PARTIALLY PERFORMED ON WINDOWS.** Two
Windows items were observed with Computer Use; 18 Windows items and all Xorg and
Wayland checks remain unperformed. Unit tests, compile checks, mocks, and code
inspection do not count as manual execution.

The Windows Computer Use run launched the source with the process-scoped
`AGETHA_TEST_MODE=1` environment variable. This retains native window framing
only for automation targetability. With the variable absent, production keeps
the existing borderless `overrideredirect(True)` behavior. The test variable is
not written to `config.txt` or persisted by Agetha.

When a human performs an item, replace only that item's status, record the
platform/build and date, and add concise evidence without screenshots or logs
that reveal typed secrets, OCR contents, clipboard contents, file paths, or API
keys. Use disposable text and a nonprivileged test target. Do not test against
password managers, banking software, security dialogs, elevated terminals, or
real credentials.

## Windows checklist (20 items)

Test on a supported Windows 10/11 desktop using the same interpreter and
launcher intended for normal use. Windows ARM/Snapdragon should use the
supported x64 Python-under-Prism path; record it separately from native OS
architecture.

1. [x] **Current-language mirroring sample (Thai vector) — PASSED 2026-08-11.**
   Computer Use submitted a disposable Thai greeting request. Agetha visibly replied
   `สวัสดี กลับมาแล้วเหรอ`, using `สวัสดี` without adding an unrequested
   `ครับ` or `ค่ะ`. This remains a sampling check of prompt
   behavior, not proof that all future model output is guaranteed and not a
   language preference. This observation predates the generalized prompt and
   therefore does not by itself validate the new policy.

2. [ ] **Exact requested Thai text — NOT PERFORMED.** Ask Agetha to type
   `ขอบคุณครับ` into a disposable target. Confirm the exact string is preserved;
   the language policy must not remove `ครับ` from user-provided data.

3. [ ] **Balanced additional scripts in Notepad — NOT PERFORMED.** Type
   disposable Thai combining marks, Chinese `你好`, Korean `안녕하세요`, Russian
   `Привет`, and French `Bonjour`. Compare exact code points or copy the results
   back for a byte/code-point comparison.

4. [ ] **Japanese in Notepad — NOT PERFORMED.** Type `こんにちは` plus Japanese
   punctuation and confirm the exact requested string appears without keyboard
   layout changes or transliteration.

5. [ ] **Arabic directionality — NOT PERFORMED.** Type `مرحباً` into Notepad and
   inspect right-to-left rendering. Distinguish the application's visual bidi
   rendering from preservation of the underlying string.

6. [ ] **Mixed scripts and emoji — NOT PERFORMED.** Type
   `Agetha สวัสดี こんにちは مرحباً 👋` plus a ZWJ/variation-selector emoji sample.
   Confirm ordering, combining marks, modifiers, and emoji are preserved.

7. [ ] **Focus change during paced mode — NOT PERFORMED.** Start a sufficiently
   long `paced` entry into a disposable target, change focus before completion,
   and confirm Agetha aborts instead of continuing into the new window.

8. [ ] **Native failure and clipboard fallback — NOT PERFORMED.** In a
   controlled test harness or target that rejects direct Unicode before any
   character is sent, confirm `auto` uses the guarded clipboard fallback and
   reports the fallback honestly. Do not induce a partial native send that
   could duplicate text.

9. [ ] **User clipboard wins — NOT PERFORMED.** During a controlled paced or
   fallback operation, copy a new disposable value after Agetha places its
   temporary clipboard text. Confirm restoration does not overwrite the user's
   newer value.

10. [ ] **No automatic Enter — NOT PERFORMED.** Type a harmless command-looking
    string into a non-elevated terminal or editor preview and confirm no Enter,
    Return, or Tab event is appended. Nothing should execute merely because
    `type_text` completed.

11. [ ] **Terminal warning/preview — NOT PERFORMED.** Target a non-elevated,
    disposable terminal and confirm `type_text` remains Caution and opens the
    Win95 preview with safely truncated destination, counts, method, fallback,
    and reversibility information. Confirm guard/log text does not echo the
    payload.

12. [ ] **Fullscreen suppression — NOT PERFORMED.** Enter a safe fullscreen or
    presentation state, produce a nonurgent eligible reaction, and confirm
    Presence Etiquette prevents popup, voice, focus stealing, and window
    motion. Verify no dramatic repeated indicator is substituted.

13. [ ] **Rapid-input deferral — NOT PERFORMED.** Type rapidly in Agetha's
    existing input path, then produce a nonurgent eligible notification. Confirm
    it is queued/delayed for the configured bounded cooldown and does not steal
    focus.

14. [ ] **Sentinel disabled default — NOT PERFORMED.** With
    `ENABLE_TERMINAL_SENTINEL = no`, trigger a safe confirmed test error in an
    otherwise eligible window. Confirm there is no Sentinel popup, provider
    request, command, file write, or fix attempt.

15. [ ] **Explicit Sentinel allowlist — NOT PERFORMED.** Enable Terminal
    Sentinel and allowlist only the exact disposable VS Code or Windows Terminal
    process/title under test. Confirm unrelated windows and an empty allowlist
    remain unwatched.

16. [ ] **Safe traceback detection — NOT PERFORMED.** In the allowlisted window,
    display a harmless deterministic Python traceback or test/build failure and
    allow the existing OCR confirmation/change rules to produce one new event.
    Confirm the local no-activation notification offers Explain, Dismiss, and
    Ignore Pattern.

17. [ ] **Explain is the provider boundary — NOT PERFORMED.** Before clicking
    Explain, confirm the notification caused no Groq/Gemini/OpenRouter/Ollama
    request.
    Click Explain and confirm exactly then a bounded, redacted request with
    origin `terminal_sentinel` may begin. Confirm its response can explain but
    cannot execute a model-suggested command or modify a file.

18. [ ] **Duplicate error does not spam — NOT PERFORMED.** Leave the same active
    error visible through repeated scans and confirm no repeated popup. Clear it
    and respect both confirmation and cooldown before checking a legitimately
    new event. Test Ignore Pattern with disposable output and confirm raw OCR
    text is not persisted.

19. [x] **Senses reports real state — PASSED 2026-08-11.** Computer Use opened
    Dashboard → Senses and inspected Vision, Hearing, Memory, Network & AI,
    Actions, and Presence. The panel visibly distinguished available,
    disabled, not configured, degraded, and unknown states; exposed no provider
    key or private path; and retained `no network checks run`. Reopening the
    existing panel refreshed it and visibly advanced the update timestamp. This
    observation predates the Compact/Full profile phase and does not count as a
    pass for the profile-aware Senses behavior in the separate checklist.

20. [ ] **Shutdown during owned workers — NOT PERFORMED.** Separately request
    final close while Unicode typing, Senses refresh, and Terminal Sentinel
    Explain/provider work are active. Confirm typing cancellation, stale-refresh
    rejection, popup/panel closure, queue shutdown, and one idempotent final
    teardown with no Tk-after-destroy error or continued text entry.

### Windows execution record

| Field | Value |
|---|---|
| Tester | Codex Computer Use |
| Date/time zone | 2026-08-11, Asia/Bangkok |
| Windows edition/build | Windows 11, 10.0.26100 |
| Native OS architecture | ARM64 |
| Python/process architecture | CPython 3.13.15, win-amd64 (x64 under Prism) |
| Launcher | Source: `AGETHA_TEST_MODE=1`, `py -3 main.py` |
| Config deviations | Test-mode environment only; no persisted config change |
| Items passed/failed/skipped | 2 / 0 / 0; 18 unperformed |

## Linux Xorg notes

**Current status: NOT PERFORMED.** Record desktop environment, display server,
clipboard utilities, and `xdotool` availability before testing.

- Xorg automatic entry is clipboard-and-paste, not Win32 native Unicode. Target
  identity/focus and paste activation use optional `xdotool`; clipboard access
  uses optional `xclip` or `xsel`. None is a mandatory Python dependency.
- With the tools available, repeat balanced English, Thai, Japanese, Chinese,
  Korean, Arabic, Russian, French, mixed-script, and emoji exact-text checks,
  plus focus-change, compare-and-restore clipboard, preview, and no-Enter checks.
- If target discovery, clipboard access, or paste synthesis is unavailable,
  expect a truthful copy-only or unavailable result. Do not record automatic
  typing as passed from clipboard contents alone.
- Exercise the Senses panel and confirm it distinguishes an available Xorg
  capture path from runtime-unverified Tesseract or missing utilities.
- Terminal Sentinel still requires its explicit allowlist and the existing
  confirmed Tesseract event path; do not infer support merely from a matching
  string in an editor.

### Xorg execution record

| Field | Value |
|---|---|
| Tester/date | Not performed |
| Distribution/desktop | Not performed |
| `XDG_SESSION_TYPE` | Not performed |
| `xdotool` | Not checked |
| `xclip` / `xsel` | Not checked |
| Result/limitations | Not performed |

## Linux Wayland notes

**Current status: NOT PERFORMED.** Record compositor and portal/tool
availability. Wayland's restriction on global synthetic input is an expected
security boundary, not a defect to bypass.

- `auto`, `paste`, and `paced` do not synthesize global typing on Wayland. When
  `wl-copy` is available, Agetha copies the exact text and reports a copy-only
  partial outcome so the user can press `Ctrl+V` manually.
- `unicode` native input is unavailable and must fail honestly. Missing
  `wl-copy` means clipboard copy can also be unavailable.
- Confirm that Agetha does not suggest `xhost +`, elevated execution, disabling
  Wayland security, a hard-coded `XAUTHORITY`, or any compositor bypass.
- Automatic OCR/capture can be compositor-dependent. The Senses panel should
  report explicit-only, degraded, unavailable, or unknown capability based on
  detected state; it must not turn absence into “available.”
- Terminal Sentinel cannot function without existing automatic confirmed OCR
  events. It must remain silent when the compositor prevents that path, and it
  must not create a second capture mechanism.

### Wayland execution record

| Field | Value |
|---|---|
| Tester/date | Not performed |
| Distribution/desktop/compositor | Not performed |
| `XDG_SESSION_TYPE` | Not performed |
| `wl-copy` / `wl-paste` | Not checked |
| Automatic capture status | Not checked |
| Result/limitations | Not performed |

## Result summary

Do not change this summary to “passed” based solely on automated validation.

| Platform | Performed | Passed | Failed | Skipped/unavailable | Unperformed |
|---|---:|---:|---:|---:|---:|
| Windows checklist | Yes | 2 | 0 | 0 | 18 |
| Xorg notes | No | 0 | 0 | 0 | All |
| Wayland notes | No | 0 | 0 | 0 | All |
