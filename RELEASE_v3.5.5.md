## Agetha Mod v3.5.5 — Stability, Security & Voice/Notification Fixes

Patch release addressing **45+ bugs** from a full codebase audit, plus follow-up fixes for microphone input, Windows notifications, and screen-read requery.

---

### Critical & security

- **Ollama local AI** — Split streaming/non-streaming client paths; `stream=False` no longer returns a broken generator.
- **Windows window enum** — `EnumWindows` ctypes callbacks kept alive to prevent GC segfaults.
- **Voice input** — Listen loop uses timeout so `stop()` can interrupt; PyAudio failure falls back to **sounddevice**.
- **`run_command`** — AI can no longer force `shell=True`; shell metacharacters rejected.
- **`pkill` / process kill** — Safer process-name validation; prefer exact match over `pkill -f`.
- **`search_files`** — Directory restricted to user home + app base path.
- **Notification PowerShell** — EncodedCommand / escaping hardening (prior release path).

### High-impact functional

- **`MB_TOPMOST`** — Correct flag in native error popups.
- **`read_file`** — Handler registered (was missing despite guard tier).
- **Config constants** — `TOUCH_COOLDOWN_SEC` and `LOAF_TIMER_MS` used instead of hardcoded values.
- **Unmute** — Sends mute-toggle key (173), not volume-down.
- **`_ai_busy` race** — Cleared after `_dispatch_response` completes (`try/finally`).
- **`request_screen_read`** — Fixed duplicate `user_message` argument crash on requery.

### Notifications (Windows)

- AI often returned empty `message`; text now falls back from **segments**.
- Replaced silent-failing toast (`CreateToastNotifier("Agetha")`) with **tray balloon** + MessageBox fallback.

### Voice / microphone

- Safe microphone open with device fallback (saved index → default).
- Mic picker validates saved device index; resets button on fatal error.
- **`sounddevice`** added to `requirements.txt` as PyAudio fallback on Windows.

### Other improvements

- URL search query encoding (`quote_plus`).
- Yes/no dialog AI requery moved off UI thread.
- `.env` quoted values stripped; config refresh helper.
- `wmctrl` / macOS window-op feedback; Linux `open_folder` zombie fix.
- Memory UTC timestamps; selective clear scope fix; medic paths via `__file__`.
- Screen reader: removed incorrect `PIL_OK` guard on foreground window info.
- Parser: segment text copied into `show_notification` / `show_dialog` `message` field.

---

### Upgrade from v3.5.1

1. Pull or download this release
2. Set `APP_VERSION = 3.5.5` in `config.txt` (optional — window title / update check)
3. Run **`pip install -r requirements.txt`** (adds `sounddevice` if missing)
4. **Windows mic**: Settings → Privacy → Microphone → allow desktop apps
5. Run **`Medic_Checker.bat`** if you use voice or drag-and-drop
6. Restart Agetha

No migration needed for `memory/` or `.env`.

---

### Files changed (12)

| File | Summary |
|------|---------|
| `ai_engine.py` | Ollama sync/stream split; Groq retry cap; `read_file` command; mood few-shots |
| `command_handlers.py` | Security, handlers, URL encode, screen-read fix, notifications |
| `command_guard.py` | `MB_TOPMOST`; dry-run timeout log; `_process_target` |
| `main.py` | Config constants, `_ai_busy`, `_set_state` marshaling, mic UX |
| `system_commands.py` | Notifications, search allowlist, unmute, shutdown precision |
| `voice_input.py` | Mic open fallback, sounddevice, stop timeout |
| `window_control.py` | ctypes GC fix, safe `pkill`, non-Windows guards |
| `utils.py` | `MB_TOPMOST`, `.env` quotes, `refresh_config_constants()` |
| `memory_system.py` | UTC format, selective clear, atomic suffix, soul cache lock |
| `screen_reader.py` | PIL guard removal; dead code cleanup |
| `medic_helper.py` | Paths relative to package dir |
| `requirements.txt` | `sounddevice` |

**Full diff**: https://github.com/SiriusNovyx/Agetha.exe/compare/v3.5.1...v3.5.5
