# Agetha Mod — Overhaul Edition

> A modified fork of [Agetha.exe](https://chocolatebread.ddns.net/agetha.html) (v4.2.0) with enhanced desktop integration, spatial OCR, emotional AI, native safety confirmations, and expanded OS control.

**Version:** Overhaul v5.5.5 · **Medic_Checker:** v5.5.5 · **Original author:** @tomiszivacs

> **Asset notice:** The bundled files in [`assets/`](assets/) are provided so a
> normal clone or source download runs with the complete UI. They are not
> covered by this repository's GPLv3 license. See
> [`assets/README.md`](assets/README.md) for details.

---

## Developer documentation

For a code-map-first view of the architecture, runtime flows, module ownership,
configuration, and focused tests, start with [`docs/README.md`](docs/README.md).

## Platform support

Official release targets are **Windows 10/11 x64**, **Windows 11 ARM64 or
Snapdragon using x64 Python under Prism**, and **Linux desktop environments
covered by the existing Linux paths**. GitHub Actions validates the shared
Python code and focused Fast Mode recovery paths on Windows and Linux.

**macOS is unsupported as of v5.5.5.** Historical macOS fallback code may
remain under GPLv3, but it receives no release testing, compatibility fixes, or
support. Windows-only integrations such as native warning dialogs, Startup
shortcuts, and Windows Settings remain feature-gated on Linux.

## About

Agetha is a **desktop AI companion** — a small always-on-top Windows 95–style window with an animated character who lives on your machine. She chats with you, watches your screen via OCR, remembers context across sessions, and can execute real OS actions through a JSON command system powered by **Groq** (default), **OpenRouter** (optional), or **Ollama** (local).

This fork makes Agetha feel sharper, more autonomous, and more integrated with your desktop: spatial error detection, mood-driven window snapping, process monitoring, voice input, file drag-and-drop, Groq token usage in the UI, and a full command library with **native confirmation dialogs** before dangerous actions.

---

## Features

### Spatial OCR & Focused Window Scanning

- **Targeted scanning** — captures only the active foreground window (~4× faster than full-desktop OCR)
- **Spatial mapping** — maps words to desktop coordinates (e.g. `TypeError@(320,458)`); Agetha can move her window next to an on-screen error
- **Pattern registry** — regex detection for Python tracebacks, PowerShell errors, build failures, npm errors, security alerts, and more
- **Multi-monitor & DPI** — per-monitor DPI awareness and correct physical pixel coordinates

### Optional Deep OCR

Tesseract remains Agetha's standard, default OCR backend for foreground scanning,
ambient polling, pattern detection, and word coordinates. Advanced users can opt
into `analyze_screen_deep`, which sends one explicitly requested capture to a
separately hosted [Baidu Unlimited-OCR](https://github.com/baidu/Unlimited-OCR)
OpenAI-compatible service for complex documents, tables, layouts, and long text.

Deep OCR is disabled by default and is never used by automatic polling. If its
server is disabled or offline, standard Tesseract OCR continues normally. The
official Unlimited-OCR setup primarily targets NVIDIA CUDA environments; Windows
ARM/Snapdragon users should normally keep Tesseract local and connect to another
machine only when deep OCR is needed. A remote service receives screenshot
content, so remote URLs require explicit configuration and opt-in. See
[`docs/unlimited_ocr_server.md`](docs/unlimited_ocr_server.md).

### Screen Monitoring Reliability

Automatic monitoring uses one immutable capture record containing the image,
desktop origin, window title, window identity, and capture scope. This keeps
spatial word coordinates correct after high-resolution downscaling and on
multi-monitor desktops with negative origins. Standard scans are serialized;
explicit deep OCR holds the capture lock only while taking its own screenshot
and cannot overwrite or restore standard OCR state.

The local monitor skips Agetha's own window and configured exclusions, rejects
OCR results if the foreground window changes during recognition, and avoids
rerunning Tesseract for visually unchanged frames. A periodic forced refresh
and per-window state expiry prevent stale caches. Pattern events preserve OCR
line coordinates/confidence and are deduplicated separately from the compatible
`last_pattern_matches` current-state list. An active event does not retrigger
until it clears for the configured number of clean scans and its cooldown has
elapsed; a changed normalized snippet is a distinct event.

Sensitive-looking tokens, bearer credentials, private keys, passwords, session
values, and recovery-code forms are redacted only when screen text is prepared
for Groq, OpenRouter, or Ollama context. Local pattern matching still uses the
original OCR text. Use `OCR_EXCLUDED_APPS` and
`OCR_EXCLUDED_TITLE_PATTERNS` for windows that should never be captured
automatically; title exclusions accept plain text or a bounded `re:` prefix.

On Windows, focused capture and process names use Win32 APIs and MSS. Ubuntu
Xorg supports managed Tk windows and automatic OCR through validated optional
backends. GNOME Wayland supports the interactive GUI and normal minimize/restore
behavior, while screen capture remains compositor-dependent and automatic OCR
fails closed when unrestricted capture is unavailable. See
[Linux desktop support](docs/linux_support.md). Historical macOS fallbacks
remain unsupported as of v5.5.5.

Tesseract remains the default real-time backend; Unlimited-OCR is still used
only by an explicit deep-analysis command. `OCR_LANGUAGES = eng+tha` is supported
after both matching Tesseract language-data packages are installed locally.

### Dual-Layer Memory

| Layer | File | Purpose |
|-------|------|---------|
| Static identity | `memory/soul.md` | Personality, mood rules, triggers (editable Markdown) |
| Episodic memory | `memory/episodic_memory.json` | Timestamped interaction log (max 50 entries) |

### Presence & Realism (v4.0.0)

- **Circadian rhythm** — an internal clock (deep night / dawn / morning / afternoon / evening / night) flavors her energy and mood; drowsy whispers at 3 AM, sharp and smug in the morning
- **Dream journal** — during deep sleep she *dreams*: fragments of real episodic and long-term memories woven into surreal entries (`memory/dreams.jsonl`); on waking she remembers the dream once and may mention it — ask "did you dream?" (`view_dreams`)
- **Task keeper** — "remind me to…" stores tasks in `memory/tasks.json` (`add_task` / `complete_task` / `list_tasks`); pending tasks appear in her ambient context so she nags you about them in character
- All three are local-only (no network), never touch files outside `memory/`, and are config-gated (`ENABLE_CIRCADIAN_RHYTHM`, `ENABLE_DREAMS`, `ENABLE_TASKS`)

### Emotion Engine & Transparent Windows Integration (v5.0.0)

- **Deep emotion engine** — persistent valence / arousal / trust / loneliness in `memory/emotional_state.json` (inertia, decay, bounded events). A declined dangerous command causes mild disappointment only — never guilt or pressure.
- **Emotional history** — bounded relationship signals in `memory/emotional_history.jsonl`; viewable (`view_emotions`), removable, fully resettable (`clear_emotions`). Prompt injection is hardened: category templates + sanitized summaries labeled as untrusted historical data.
- **Start Agetha when I sign in** — optional Startup-folder shortcut (`set_autostart`); config-gated **off** by default + Danger confirmation; no service, scheduled task, or registry Run key. Audited in `memory/audit_log.jsonl`.
- **Safe Windows helpers** — `open_settings` (allowlisted `ms-settings:` pages), `set_theme` (HKCU light/dark only, with rollback backup), `recycle_bin_status` (aggregate count/size only).
- **Status providers** — coarse local observations (battery / disk / network), disabled by default, pausable.
- **Tray scaffold** — optional compatibility path if you install `pystray` yourself; not bundled, not a guaranteed runtime feature, silent when absent.

### Psychological Moods & Attention Snapping

- **Surface moods:** neutral, happy, excited, sad, surprised, thinking, whisper, angry
- **Deep moods:** manic, melancholic, paranoid, vulnerable, dominant
- **Snap mechanic** — if ignored too long in attention-seeking moods, Agetha snaps to screen center and pulls herself to the foreground
- **Native audio** — platform system sounds + mood-based ambient bleeps

### Command Guard (Safety System)

Before executing risky actions, Agetha shows a **native Windows MessageBox** with tier-appropriate icons:

| Tier | Icon | When |
|------|------|------|
| **Safe** | — | speak, open_url, screenshots, move own window… |
| **Caution** | ℹ Info | open files, clipboard, search, volume… |
| **Danger** | ⚠ Warning | delete, run_command, shutdown, lock screen… |

- **Process kills:** common user apps (Chrome, Notepad, etc.) close without a prompt; system processes (`explorer.exe`, `svchost.exe`, …) require confirmation
- **Denied actions:** Agetha responds *"Fine. I won't."*
- Toggle all OS execution via `ENABLE_COMMAND_EXECUTION` in `config.txt`

### Voice Input (optional)

- **Microphone button** (🎤) in the chat row when `ENABLE_VOICE = yes`
- **Google STT** (online) — default when `USE_LOCAL_STT = no`
- **faster-whisper** (offline) — when `USE_LOCAL_STT = yes` (~75 MB `tiny.en` model on first run)
- Mic choice saved in `memory/settings.json` (Win95-style picker on first use)
- Medic_Checker installs `SpeechRecognition` + `PyAudio` (and `faster-whisper` if needed)

### File Drag-and-Drop

- Drop files onto Agetha's GIF when `ENABLE_FILE_DRAG_DROP = yes`
- Requires `tkinterdnd2` on Windows (Medic_Checker installs it when enabled)
- Agetha receives a `[system] file_dragged: "name" (path: …)` message and can react

### AI Backend Options & Token UI

| Backend | Config | Keys in `.env` |
|---------|--------|----------------|
| **Groq** (default) | `ENABLE_GROQ = yes` | `GROQ_API_KEY_1` … `_10` |
| **OpenRouter** | `ENABLE_OPENROUTER = yes` | `OPENROUTER_API_KEY` |
| **Ollama** | `USE_LOCAL_AI = yes` | *(none — local)* |

- **Token %** — when using Groq, the input placeholder shows `key 1/3 • 87% tokens left` (estimated daily budget per key); status bar shows the same after each reply
- **Fast Mode 2.0** — `FASTER_MODE = yes` activates a reversible performance
  profile plus request-aware prompt budgets. Original managed values are kept in
  `memory/fast_mode_snapshot.json` and restored when Fast Mode is disabled.
  Unchanged ambient scans are handled locally instead of spending an AI request.
  Provider, permission, privacy, and security settings are never changed. See
  the [threat model and recovery guide](docs/fast_mode_security.md).

---

## Project Structure

```
Agetha_Mod/
├── main.py                 # Tkinter entry point (launch via Medic_Checker)
├── medic_helper.py         # Medic_Checker CLI helpers
├── config.txt              # User settings only — no API keys
├── .env.example            # API key template
├── requirements.txt
├── Medic_Checker.ps1       # Startup health check & launcher (v5.5.5)
├── Medic_Checker.bat
├── Run_Agetha_Admin.ps1
├── assets/                 # GIFs, fonts, icons
├── memory/                 # soul.md, episodic, stats, notepad
├── tests/                  # automated suites (full index in docs/module_reference.md)
│   ├── test_atomic_persistence.py
│   ├── test_fast_mode_profile.py
│   ├── test_fast_mode_runtime.py
│   ├── test_fast_mode_security.py
│   ├── test_fast_mode_ui_medic.py
│   ├── test_hybrid_ocr.py
│   ├── test_medic_arch.py
│   ├── test_phase1_qa.py
│   ├── test_phase2_tts.py
│   ├── test_phase3_web_rag.py
│   ├── test_phase3b_glitch.py
│   ├── test_phase4_realism.py
│   ├── test_phase5_v4.py
│   ├── test_phase6_v5.py
│   ├── test_screen_monitoring_reliability.py
│   └── test_time_ui_effects.py
└── agetha/                 # Python package
    ├── app_config.py       # config.txt loader & typed settings
    ├── utils.py            # logging, paths, .env loader
    ├── core/               # AI brain, memory, companion stats
    │   ├── ai_engine.py
    │   ├── fast_mode_profile.py # reversible Fast Mode snapshot/recovery
    │   ├── memory_system.py
    │   ├── memory_search.py
    │   ├── companion_stats.py
    │   ├── rhythm.py           # v4 — circadian clock
    │   ├── dreams.py           # v4 — dream journal
    │   ├── emotion_engine.py   # v5 — persistent emotions
    │   ├── emotional_history.py
    │   └── audit_log.py
    ├── commands/           # command guard, handlers, OS utilities
    │   ├── command_guard.py
    │   ├── command_handlers.py
    │   └── system_commands.py
    ├── platform/           # OCR, Win32, voice, autostart, integration
    │   ├── screen_reader.py
    │   ├── window_control.py
    │   ├── voice_input.py
    │   ├── autostart.py        # v5 — Startup-folder shortcut
    │   └── win_integration.py  # v5 — settings / theme / recycle bin
    ├── features/           # optional TTS, web RAG, tasks, status, tray
    │   ├── tts_player.py
    │   ├── web_rag.py
    │   ├── tasks.py            # v4 — task keeper
    │   ├── status_providers.py # v5 — coarse OS observations
    │   └── tray_scaffold.py    # v5 — optional pystray scaffold
    └── ui/                 # Win95 dashboards, overlays, minigames
        ├── dashboard.py
        ├── w95_window.py
        ├── glitch_overlay.py
        └── virus_trivia.py
```

---

## Commands

Agetha responds with JSON commands. The AI chooses actions based on context; you can also trigger them by asking naturally.

### Communication

| Command | Description |
|---------|-------------|
| `speak` | Talk with mood + subtitle segments |
| `idle` | Do nothing (common for ambient polls) |
| `wake_user` | Get user's attention |
| `popup` | Show multi-line Agetha popup |
| `change_mood` | Switch avatar mood without speaking |

### File System

| Command | Description |
|---------|-------------|
| `open_file` | Open file with OS default app |
| `open_folder` | Open folder in file explorer |
| `create_folder` | Create directory |
| `create_file` | Create file with content |
| `write_file` | Write/append to file (`mode`: overwrite \| append) |
| `delete_file` | Delete file or folder |
| `rename_file` | Rename/move file |
| `list_dir` / `list_directory` | List directory in popup |
| `read_document` | Read file into AI context |
| `search_files` | Search by glob pattern |
| `run_command` | Execute shell command ⚠ |

### Apps, Web & Processes

| Command | Description |
|---------|-------------|
| `open_app` | Launch application |
| `open_url` | Open URL in default browser |
| `open_browser` | Open URL or search (Google/DuckDuckGo/Bing) |
| `force_close` | Kill process (user apps auto-allowed; system apps confirmed) |
| `monitor_process` | Check if process is running |

### Window Control

| Command | Description |
|---------|-------------|
| `move_window` | Move Agetha's window (coords or direction) |
| `snap_to_center` | Force Agetha to screen center |
| `target_window_move` | Move another app's window ⚠ |
| `target_window_resize` | Resize another app's window ⚠ |
| `target_window_close` | Close another app's window ⚠ |

### System & Media

| Command | Description |
|---------|-------------|
| `take_screenshot` | Save PNG screenshot |
| `set_clipboard` / `copy_to_clipboard` | Copy text to clipboard |
| `get_clipboard` | Read clipboard into AI context |
| `system_info` | CPU/RAM/disk report (requires psutil) |
| `set_volume` | Volume set/mute/unmute |
| `set_wallpaper` | Change desktop wallpaper |
| `type_text` | Simulate keyboard typing |
| `lock_screen` | Lock computer ⚠ |
| `shutdown` / `restart` | Shutdown/restart with delay ⚠ |
| `set_reminder` | Timed reminder |
| `show_notification` | Native OS toast |
| `show_dialog` | Native info/warning/error/yesno dialog |
| `play_sound` / `play_emotion_sound` | Play sound or OS emotion sound |
| `show_error_gif` | Show error animation |
| `request_screen_read` | Force immediate OCR capture |
| `analyze_screen_deep` | Explicit complex screenshot/document analysis through optional Unlimited-OCR ⚠ |
| `search_memory` | BM25 search of long-term memory archive (`query`, optional `limit`) |
| `search_web` | DuckDuckGo web search (`query`, optional `limit`) — requires `ENABLE_WEB_RAG=yes` ⚠ |
| `fetch_webpage` | Fetch visible text from a URL (`url`) — requires `ENABLE_WEB_RAG=yes` ⚠ |
| `glitch_overlay` | Brief harmless CRT glitch overlay (`style`, `duration_ms`) — requires `ENABLE_GLITCH_EFFECTS=yes` |
| `read_notepad` | Read dashboard notepad (`memory/notepad.txt`) into AI context |
| `play_virus_trivia` | Open Win95 virus trivia minigame popup |
| `view_dreams` | Show dream journal popup (`limit` optional) — she dreams during deep sleep |
| `add_task` | Remember a task for the user (`text`) — requires `ENABLE_TASKS=yes` |
| `complete_task` | Mark a task done (`task` = id or text match) |
| `list_tasks` | Show the user's task list in a popup |
| `view_emotions` | Show emotional state + history popup |
| `clear_emotions` | Reset emotional state and/or history (`entry_id` or `all`) ⚠ |
| `set_autostart` | "Start Agetha when I sign in" — create/remove Startup shortcut (`enabled` true/false); requires `ENABLE_AUTOSTART_CONTROL=yes` ⚠ |
| `open_settings` | Open an allowlisted Windows Settings page (`page`) ⚠ |
| `set_theme` | Set current-user Windows light/dark theme (`mode`: light/dark/rollback; `scope`: apps/system/both); requires `ENABLE_THEME_CONTROL=yes` ⚠ |
| `recycle_bin_status` | Aggregate Recycle Bin item count + total size (no filenames) |
| `clear_memory` | Erase episodic memory (soul.md kept); `memory_scope`: all/recent/old/keep_5 |
| `view_memory` | Show recent episodic entries in popup |

⚠ = requires user confirmation (Danger or Caution tier)

---

## Quick Start

### Option A — Windows Medic Checker (recommended on Windows)

1. Place all project files in one folder
2. Double-click **`Medic_Checker.bat`** (or run **`Medic_Checker.ps1`** in PowerShell)
3. The script runs 7 health checks, installs missing packages, compiles modules, then launches Agetha

### Option B — Windows manual

```powershell
py -3.13 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Edit .env — add your Groq API key
python main.py
```

### Option C — Linux manual

Create a Python 3.13 virtual environment, install the distribution's Tk/native
Tesseract packages when those features are needed, then run:

```bash
python3.13 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Edit .env, then:
python main.py
```

Medic Checker is Windows-specific. Linux Fast Mode recovery commands are listed
in the [security and recovery guide](docs/fast_mode_security.md).

---

## Configuration

### API Keys (`.env` only)

**Do not put API keys in `config.txt`.** All secrets belong in `.env`:

```bash
copy .env.example .env
```

Edit `.env`:

```
GROQ_API_KEY_1=gsk_your_key_here
GROQ_API_KEY_2=
# … up to GROQ_API_KEY_10 for rate-limit rotation

# Optional — only if ENABLE_OPENROUTER = yes in config.txt
OPENROUTER_API_KEY=sk-or-v1-...

# Optional — only if the configured Unlimited-OCR server requires a key
UNLIMITED_OCR_API_KEY=
```

- Groq keys: [console.groq.com](https://console.groq.com)
- OpenRouter keys: [openrouter.ai/keys](https://openrouter.ai/keys)

`.env` overrides matching non-secret keys in `config.txt`, except `FASTER_MODE`
and all 13 managed profile keys, whose disk-backed transaction remains
authoritative. A validated active Fast Mode profile reapplies its approved
managed values afterward. Fast Mode never edits
`.env`. Never commit `.env` to git (already in `.gitignore`).

### config.txt

All **non-secret** settings live in `config.txt` and are loaded by `app_config.py`. Boolean values accept `yes`/`no`, `true`/`false`, `1`/`0`, or `on`/`off`.

#### AI backend

| Setting | Default | Description |
|---------|---------|-------------|
| `USE_LOCAL_AI` | `no` | Use Ollama instead of cloud APIs |
| `ENABLE_GROQ` | `yes` | Enable Groq (default cloud backend) |
| `ENABLE_OPENROUTER` | `no` | Use OpenRouter instead of Groq |
| `OPENROUTER_MODEL` | see `config.txt` | OpenRouter model slug |
| `FASTER_MODE` | `no` | Reversible AI/context/polling/OCR performance profile; restores prior managed values when disabled |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model name |
| `LOCAL_AI_MODEL` | *(empty)* | Ollama model (`ollama list`) |
| `LOCAL_AI_TIMEOUT` | `30` | Ollama request timeout (seconds) |

API keys (`GROQ_API_KEY_*`, `OPENROUTER_API_KEY`, `UNLIMITED_OCR_API_KEY`) → **`.env` only**, not `config.txt`.

#### AI tuning

| Setting | Default | Description |
|---------|---------|-------------|
| `AI_TEMPERATURE` | `0.85` | Response randomness (0–2) |
| `AI_MAX_TOKENS` | `400` | Max tokens per reply |
| `AI_TOP_P` | `0.95` | Nucleus sampling (0–1) |
| `ENABLE_STREAMING` | `yes` | Stream Groq responses to UI |
| `ENABLE_AMBIENT_POLLS` | `yes` | Periodic screen-context AI polls |
| `ENABLE_DATETIME_CONTEXT` | `yes` | Include compact local weekday/date/time in every AI prompt |
| `DATETIME_INCLUDE_SECONDS` | `no` | Include seconds in datetime context |
| `DATETIME_INCLUDE_TIMEZONE` | `yes` | Include local zone name and UTC offset |

#### OS permissions

| Setting | Default | Description |
|---------|---------|-------------|
| `ENABLE_COMMAND_EXECUTION` | `yes` | Master switch for all OS commands |
| `ENABLE_WINDOW_CONTROL` | `yes` | `target_window_*` move/resize/close |
| `ENABLE_COMMAND_CONFIRMATIONS` | `yes` | Native confirm dialogs for risky cmds |
| `FORCE_CLOSE_AUTO_ALLOW` | `yes` | Auto-allow `force_close` on user apps |
| `PROTECTED_PROCESSES` | *(empty)* | Extra comma-separated processes to protect |

#### Context & memory

| Setting | Default | Description |
|---------|---------|-------------|
| `MEMORY_CHARS` | `600` | Long-term memory chars per prompt |
| `HISTORY_LIMIT` | `6` | Recent conversation turns kept |
| `FILE_READ_CHARS` | `200` | Max chars when reading files into context |
| `EPISODIC_PROMPT_LIMIT` | `10` | Episodic memories injected per prompt |
| `EPISODIC_ENTRY_MAX_CHARS` | `300` | Max chars per episodic entry |
| `EPISODIC_MAX_ENTRIES` | `50` | Max episodic entries stored |
| `ENABLE_LONGTERM_MEMORY` | `yes` | Dual-write `summary_memory` to `memory/longterm_memory.jsonl` |
| `LONGTERM_MEMORY_MAX_RESULTS` | `5` | Max BM25 hits for `search_memory` |
| `LONGTERM_MEMORY_MAX_CHARS` | `2500` | Max chars of search results injected into prompt |
| `ENABLE_WEB_RAG` | `no` | Enable `search_web` / `fetch_webpage` (network access) |
| `WEB_FETCH_MAX_CHARS` | `8000` | Max chars of fetched page text injected into prompt |
| `WEB_TIMEOUT_SEC` | `10` | HTTP timeout for web search/fetch (seconds) |
| `WEB_SEARCH_MAX_RESULTS` | `5` | Max DuckDuckGo hits for `search_web` |
| `ENABLE_GLITCH_EFFECTS` | `no` | Enable harmless `glitch_overlay` visual effect |
| `GLITCH_MAX_DURATION_MS` | `2000` | Max overlay lifetime in ms (200–5000) |
| `GLITCH_DEFAULT_STYLE` | `scanlines` | Default style: `scanlines` \| `static` \| `rgb_split` \| `flicker` \| `bsod` \| `matrix` \| `tear` |
| `GLITCH_MOOD_AUTO` | `no` | Auto brief glitch on deep moods (`manic`, `angry`, `dominant`, `paranoid`) when glitches enabled |
| `GLITCH_FULLSCREEN` | `no` | Use fullscreen overlay instead of corner window |
| `ENABLE_COMPANION_STATS_CONTEXT` | `yes` | Inject virus-registry stats + CPU heat hints into AI prompt |

#### Presence & realism (v4.0.0)

| Setting | Default | Description |
|---------|---------|-------------|
| `ENABLE_CIRCADIAN_RHYTHM` | `yes` | Internal clock flavors her mood by time of day |
| `RHYTHM_NIGHT_START` | `23` | Hour (0–23) her "deep night" drowsy window begins |
| `RHYTHM_NIGHT_END` | `6` | Hour (0–23) it ends (window wraps midnight) |
| `ENABLE_DREAMS` | `yes` | She dreams during deep sleep → `memory/dreams.jsonl`; recalls on waking |
| `DREAMS_MAX_ENTRIES` | `40` | Max dream records kept (5–500) |
| `ENABLE_TASKS` | `yes` | `add_task` / `complete_task` / `list_tasks` → `memory/tasks.json` |
| `TASKS_MAX_ENTRIES` | `100` | Max stored tasks (10–1000); oldest completed pruned first |

#### Emotion & Windows integration (v5.0.0)

| Setting | Default | Description |
|---------|---------|-------------|
| `ENABLE_EMOTION_ENGINE` | `yes` | Persistent valence/arousal/trust/loneliness + emotional history |
| `EMOTION_BASELINE_VALENCE` | `0` | Resting valence (−100..100) |
| `EMOTION_BASELINE_AROUSAL` | `30` | Resting arousal (0..100) |
| `EMOTION_BASELINE_TRUST` | `50` | Resting trust (0..100) |
| `EMOTION_BASELINE_LONELINESS` | `25` | Resting loneliness (0..100) |
| `EMOTION_DECAY_PER_HOUR` | `0.10` | Fraction of distance-to-baseline recovered per hour |
| `EMOTION_HISTORY_MAX` | `200` | Max emotional-history records (20–1000) |
| `ENABLE_AUTOSTART_CONTROL` | `no` | Allow `set_autostart` ("Start Agetha when I sign in") |
| `ENABLE_THEME_CONTROL` | `no` | Allow `set_theme` (HKCU light/dark only) |
| `ENABLE_STATUS_PROVIDERS` | `no` | Coarse battery/disk/network observations |
| `STATUS_POLL_INTERVAL_SEC` | `300` | Status-provider poll interval (60–3600) |
| `ENABLE_TRAY` | `no` | Optional tray scaffold (requires user-installed `pystray`) |
| `TRAY_BACKGROUND_CLOSE` | `no` | Keep running in tray on close (only if tray is active) |

#### Web RAG security

Web search and page fetch are **disabled by default** (`ENABLE_WEB_RAG = no`). When enabled:

- Results are treated as **untrusted external data** and wrapped with prompt-injection warnings before the AI sees them.
- No JavaScript execution — only static HTML text extraction.
- Network errors degrade gracefully (empty results / error dicts); the app never crashes on fetch failure.
- `search_web` and `fetch_webpage` require user confirmation (Caution tier) when `ENABLE_COMMAND_CONFIRMATIONS=yes`.
- Anti-recursion: after one search/fetch per user request, the AI is told not to call `search_web` or `fetch_webpage` again.

#### Glitch overlay safety

The glitch effect is **disabled by default** (`ENABLE_GLITCH_EFFECTS = no`). When enabled:

- **Visual only** — a small borderless Tkinter overlay in the screen corner; no desktop, wallpaper, registry, file, or display-setting changes.
- Auto-closes within `GLITCH_MAX_DURATION_MS` (default 2000 ms, clamped 200–5000).
- Does not trap input for long; uses a corner overlay rather than fullscreen blocking.
- Failures are logged and never crash the app.
- `glitch_overlay` is Safe tier (no confirmation dialog when confirmations are enabled).

#### Behavior & timing

| Setting | Default | Description |
|---------|---------|-------------|
| `SCREEN_POLL_INTERVAL_SEC` | `120` | Ambient screen poll interval |
| `TOUCH_COOLDOWN_SEC` | `10` | Click-GIF touch cooldown |
| `WAKE_DELAY_SEC` | `8` | Delay before wake-from-sleep |
| `LOAF_TIMER_MIN` | `15` | Minutes idle before loaf animation |

#### Mood & attention snap

| Setting | Default | Description |
|---------|---------|-------------|
| `ENABLE_ATTENTION_SNAP` | `yes` | Auto-return to idle after mood timeout |
| `MOOD_SNAP_*_SEC` | varies | Per-mood snap threshold (see `config.txt`) |

#### Screen reader / OCR

| Setting | Default | Description |
|---------|---------|-------------|
| `ENABLE_SCREEN_READER` | `yes` | Enable OCR screen context |
| `OCR_MAX_DIMENSION` | `2560` | Max capture dimension (px) |
| `OCR_FOCUSED_WINDOW_ONLY` | `yes` | OCR focused window only |
| `OCR_CHANGE_DETECTION` | `yes` | Skip Tesseract while the captured target is visually unchanged |
| `OCR_CHANGE_THRESHOLD` | `0.025` | Normalized thumbnail-difference threshold (clamped 0-1) |
| `OCR_FORCE_REFRESH_SECONDS` | `20` | Re-run OCR periodically even without visible change |
| `OCR_STATE_EXPIRY_SECONDS` | `300` | Expire inactive per-window change state |
| `OCR_PATTERN_COOLDOWN_SECONDS` | `60` | Suppress repeated notifications for the same normalized event |
| `OCR_PATTERN_CONFIRM_SCANS` | `1` | Matching scans required before a normal-confidence event |
| `OCR_LOW_CONFIDENCE_CONFIRM_SCANS` | `2` | Matching scans required for lower-confidence OCR events |
| `OCR_PATTERN_CLEAR_SCANS` | `2` | Clean scans required before an event becomes inactive |
| `OCR_MIN_WORD_CONFIDENCE` | `30` | Minimum Tesseract confidence for spatial word output |
| `OCR_MIN_PATTERN_CONFIDENCE` | `45` | Minimum structured-line confidence for pattern matching |
| `OCR_PREPROCESSING` | `auto` | `basic` grayscale scaling or adaptive local preprocessing |
| `OCR_LANGUAGES` | `eng` | Installed Tesseract language codes, joined with `+` |
| `OCR_PSM` | `auto` | Tesseract page segmentation: `auto`, `3`, `6`, or `11` |
| `OCR_EXCLUDED_APPS` | *(empty)* | Comma-separated application names excluded from automatic capture |
| `OCR_EXCLUDED_TITLE_PATTERNS` | *(empty)* | Comma-separated title text or `re:regex` exclusions |
| `OCR_REDACT_SENSITIVE_TEXT` | `yes` | Redact common keys, tokens, and passwords before AI context |
| `INCLUDE_WINDOW_TITLE_IN_CONTEXT` | `yes` | Add window title to AI context |
| `TESSERACT_PATH` | *(empty)* | Custom path to `tesseract.exe` |
| `DEEP_OCR_BACKEND` | `none` | Optional explicit backend: `none` or `unlimited_ocr` |
| `UNLIMITED_OCR_SERVER_URL` | `http://127.0.0.1:10000` | Separate OpenAI-compatible service root |
| `UNLIMITED_OCR_MODEL` | `Unlimited-OCR` | Served model name |
| `UNLIMITED_OCR_TIMEOUT_SECONDS` | `180` | Explicit deep-analysis timeout (clamped 10–1200 seconds) |
| `UNLIMITED_OCR_ALLOW_REMOTE` | `no` | Allow a non-loopback service; screenshots may leave this PC |
| `DEEP_OCR_MAX_OUTPUT_CHARS` | `12000` | Maximum OCR text returned to AI context (clamped 1000–50000) |

#### UI

| Setting | Default | Description |
|---------|---------|-------------|
| `WINDOW_TOPMOST` | `yes` | Keep Agetha above other windows |
| `UI_SCALE` | `auto` | Scale the UI from display resolution, or set a manual value from `0.75` to `2.50` |
| `WINDOW_START_X` / `Y` | `80` | Initial window position |
| `SUBTITLE_CHAR_DELAY` | `0.035` | Typewriter subtitle speed (seconds) |
| `ANIMATION_SPEED` | `0.6` | GIF speed multiplier |
| `ENABLE_CRT_CLOSE_ANIMATION` | `yes` | Brief CRT collapse before graceful application exit |
| `REDUCED_MOTION` | `no` | Disable decorative window movement and animated glow |
| `ENABLE_MOOD_GLOW` | `no` | Enable a subtle mood-coloured GIF border |
| `MOOD_GLOW_ANIMATED` | `yes` | Pulse the enabled mood border; reduced motion makes it static |
| `MOOD_GLOW_INTERVAL_MS` | `150` | Glow refresh interval (clamped to 100-1000 ms) |
| `ENABLE_MOOD_MOTION` | `yes` | Allow guarded motion once per completed response |
| `MOOD_MOTION_COOLDOWN_SECONDS` | `4` | Motion cooldown (clamped to 1-60 seconds) |

Click the **📊** button in the title bar (beside minimize) to open the **Dashboard** — retro progress bars for CPU/RAM/disk/core heat, virus registry stats, notepad, and limited config toggles (safe yes/no keys).

#### Medic_Checker (launcher)

| Setting | Default | Description |
|---------|---------|-------------|
| `SKIP_TESSERACT_CHECK` | `no` | Skip Tesseract step in health check |
| `SKIP_ASSET_CHECK` | `no` | Skip asset file verification |
| `AUTO_PIP_INSTALL` | `yes` | Auto `pip install` missing packages |
| `CREATE_DESKTOP_SHORTCUT` | `no` | Create Desktop shortcut on Medic_Checker run |
| `CHECK_FOR_UPDATES` | `yes` | Compare `APP_VERSION` to GitHub release API |
| `APP_VERSION` | `5.5.5` | Shown in window title |
| `GITHUB_RELEASES_URL` | *(empty)* | GitHub API URL for update check |
| `TARGET_APP_ALIASES` | see `config.txt` | Map short names to window title fragments |
| `WINDOW_PICKER_ON_AMBIGUOUS` | `yes` | Dialog when multiple windows match |
| `DRY_RUN_MODE` | `no` | Confirm each command before executing |
| `OCR_CUSTOM_PATTERNS` | *(empty)* | `label:mood:regex` patterns (semicolon-separated) |
| `OCR_PAUSE_WHILE_TYPING_SEC` | `8` | Skip OCR for N seconds after keyboard/touch |

#### Voice & drag-and-drop

| Setting | Default | Description |
|---------|---------|-------------|
| `ENABLE_VOICE` | `no` | Show 🎤 microphone button |
| `USE_LOCAL_STT` | `no` | `yes` = faster-whisper offline; `no` = Google STT online |
| `ENABLE_FILE_DRAG_DROP` | `yes` | Drop files onto Agetha's GIF |

#### Voice output (retro bleeps + optional TTS)

| Setting | Default | Description |
|---------|---------|-------------|
| `VOICE_OUTPUT_MODE` | `bleeps_only` | `bleeps_only` (Undertale-style bleeps), `tts_only`, or `both` |
| `VOICE_TTS_ENGINE` | `pyttsx3` | `pyttsx3` (OS voices), `edge_tts` (free cloud neural), or `kokoro` (local neural) |
| `TTS_RATE` | `165` | Speech rate (80–300); mapped per engine |
| `TTS_VOLUME` | `0.8` | TTS volume (0.0–1.0) |
| `TTS_VOICE_NAME` | *(empty)* | Engine-specific voice id (see below) |

| `VOICE_TTS_ENGINE` | Install | `TTS_VOICE_NAME` examples | Notes |
|--------------------|---------|---------------------------|-------|
| `pyttsx3` | `pip install "pyttsx3>=2.90,<3.0.0"` | `Zira`, `David` | Offline OS voices |
| `edge_tts` | `pip install "edge-tts>=6.1.0,<8.0.0"` | `en-US-AvaNeural` | Needs internet; no API key |
| `kokoro` | `pip install "kokoro>=0.9.4" soundfile` | `af_heart`, `am_adam` | Offline; needs `espeak-ng` on PATH |

TTS is **optional**. The app falls back to bleeps if the chosen engine package is missing.

Subtitles and TTS are not perfectly synced in v1 — bleeps follow mood; TTS runs on a background worker thread.

### Local AI (Ollama)

```ini
USE_LOCAL_AI = yes
LOCAL_AI_MODEL = llama3
```

Run `ollama list` to see installed models.

### OpenRouter (optional)

```ini
ENABLE_OPENROUTER = yes
OPENROUTER_MODEL = deepseek/deepseek-v4-flash-0731
```

Add `OPENROUTER_API_KEY=…` to `.env`. Ignored when `USE_LOCAL_AI = yes`.

### Voice + drag-and-drop (optional)

```ini
ENABLE_VOICE = yes
USE_LOCAL_STT = no          # no = Google STT; yes = faster-whisper
ENABLE_FILE_DRAG_DROP = yes
```

### Voice output / TTS (optional)

```ini
VOICE_OUTPUT_MODE = both          # bleeps_only | tts_only | both
VOICE_TTS_ENGINE = edge_tts       # pyttsx3 | edge_tts | kokoro
TTS_RATE = 165
TTS_VOLUME = 0.8
TTS_VOICE_NAME = en-US-AvaNeural  # engine-specific; Zira for pyttsx3, af_heart for kokoro
```

```bash
pip install "edge-tts>=6.1.0,<8.0.0"   # when VOICE_TTS_ENGINE = edge_tts
# or: pip install "pyttsx3>=2.90,<3.0.0"
# or: pip install "kokoro>=0.9.4" soundfile
```

Run **Medic_Checker** after enabling — it installs the package for `VOICE_TTS_ENGINE` when `AUTO_PIP_INSTALL = yes`.

---

## Medic_Checker v5.5.5 (PowerShell)

Startup wrapper that validates your environment before launch:

| Step | Check |
|------|-------|
| Pre-flight | Current core modules + `requirements.txt` present |
| [A–D] | ARM64/Snapdragon x64 Python detection & auto-install |
| [1/7] | Python installed |
| [2/7] | Virtual environment create/activate |
| [3/7] | Packages from `requirements.txt`; optional voice/STT/DnD/**TTS** when enabled in `config.txt` |
| [4/7] | Tesseract OCR plus non-fatal optional deep-OCR configuration status |
| [5/7] | All 21 required assets in `assets\` |
| [6/7] | Config, `.env`, `memory\` (`soul.md`, episodic, long-term JSONL, stats, notepad); reports `ENABLE_LONGTERM_MEMORY` and `VOICE_OUTPUT_MODE` |
| [7/7] | `py_compile` all 35 checked Python modules; feature and reliability import checks |

**Color codes:** `[ OK ]` green · `[WARN]` yellow · `[FAIL]` red

On Snapdragon/ARM64 Windows, the checker ensures **x64 (AMD64) Python** is used so binary wheels (pygame-ce, pyautogui, mss) install correctly under Prism emulation.

---

## Requirements

- **Operating system:** Windows 10/11. Windows 11 ARM64/Snapdragon is supported
  through x64 Python running under Prism.
- **Python 3.13.x** recommended (3.14 may have compatibility issues)
- **Tesseract OCR** — [Windows installer](https://github.com/UB-Mannheim/tesseract/wiki) (optional, enables screen reading)
- **Assets** — included in this repository; keep the `assets` folder beside the application files
- **Groq API key** (in `.env`), **OpenRouter** (optional), or **Ollama** for AI responses
- **Microphone** — optional, for voice input (`ENABLE_VOICE = yes`)
- **PyAudio** — optional, required for microphone (installed by Medic_Checker)

### Python packages (`requirements.txt`)

**Core:**
```
pillow, numpy, requests, groq, pyautogui, pytesseract, mss, pygame-ce, psutil
```

**Optional** (installed by Medic_Checker when enabled in `config.txt`):
```
SpeechRecognition, PyAudio          # ENABLE_VOICE = yes
faster-whisper                    # USE_LOCAL_STT = yes
tkinterdnd2                       # ENABLE_FILE_DRAG_DROP = yes (Windows)
pyttsx3 / edge-tts / kokoro       # VOICE_OUTPUT_MODE = tts_only|both (per VOICE_TTS_ENGINE)
```

---

## Controls

| Input | Action |
|-------|--------|
| Text box + Enter | Send message to Agetha |
| Placeholder hint | Groq: `key N/M • X% tokens left`; OpenRouter/local shown when not on Groq |
| 🎤 button | Toggle microphone (`ENABLE_VOICE = yes`) — speak, pause ~1.2 s, text is sent |
| Drop file on GIF | File drag event (`ENABLE_FILE_DRAG_DROP = yes`) |
| Click GIF | Touch event (`__touch__`) — 10 s cooldown |
| **Escape** | Cancel in-flight AI request |
| Title bar | Drag window |

---

## Architecture Overview

```
User input / ambient poll (every ~2 min)
        ↓
screen_reader.py  →  OCR + pattern tags + window title
        ↓
ai_engine.py      →  Groq / OpenRouter / Ollama → JSON command
        ↓
command_guard.py  →  Native confirmation (if needed)
        ↓
command_handlers.py → Execute action + update UI
```

---

## Changelog (Overhaul)

### v5.5.5 — Reversible Fast Mode 2.0

- Official support covers Windows 10/11 x64, Windows 11 ARM64/Snapdragon through
  x64 Python under Prism, and Linux through the existing desktop paths. macOS is
  retired and unsupported.
- Atomic, schema-versioned Fast Mode snapshots preserve only the approved
  non-secret settings and restore them without replacing unrelated config.
- Startup reconciliation repairs managed drift idempotently, quarantines an
  invalid inactive snapshot, and fails closed when an active snapshot is invalid.
- Manual third-value edits are preserved as the post-Fast preference; comments,
  ordering, blank lines, unknown keys, and unmanaged changes survive every write.
- The dashboard uses one coordinated activation/restoration transaction and
  identifies managed fields. Medic Checker reports profile health and requires
  confirmation before recovery changes the configuration. If reconciliation is
  declined, Medic still launches Agetha but sends a one-launch signal that skips
  automatic Fast Mode reconciliation, so the declined change remains pending.
- Adaptive request profiles keep user/command/ambient prompts compact while
  allowing bounded tool and explicit deep-analysis requests to use the saved
  pre-Fast output ceiling and a complete-analysis segment rule. Their final
  answer stays available for follow-up while raw tool/OCR payloads are omitted
  from retained history. Groq, OpenRouter, and Ollama retain provider parity.
- Unchanged Fast Mode ambient scans now skip the provider call locally; meaningful
  OCR events and pending presence observations still reach the AI.

### v5.5.1 — Reliability, Windows ARM, high-DPI UI, and lifecycle polish

- Reliable focused-window OCR with immutable capture metadata, exact desktop
  coordinates, change detection, event deduplication, exclusions, redaction,
  and stale-window result rejection.
- Optional explicit Unlimited-OCR integration for complex layouts; Tesseract
  remains the automatic local backend and ambient turns cannot invoke deep OCR.
- Correct x64 Python detection and selection on ARM64/Snapdragon Windows hosts,
  including Prism-aware architecture reporting and virtual-environment repair.
- Resolution/DPI-aware companion and dashboard scaling for Surface-class
  2880x1920 displays.
- Compact local weekday/date/time/timezone prompt context across Groq,
  OpenRouter, and Ollama modes.
- Cancellable CRT shutdown, optional mood glow, centralized guarded mood motion,
  and idempotent graceful cleanup.
- Repository-wide architecture, runtime-flow, module, configuration, Windows ARM,
  and testing documentation under `docs/`.

### v5.0.0 — Emotion Engine & Transparent Windows Integration (Phase 6)

- **`emotion_engine.py`** — four-dimension persistent state with inertia, decay, milestone-based `long_absence` (once per stage), injectable UTC clock, RLock-guarded RMW
- **`emotional_history.py`** — bounded relationship_state; deterministic category templates; sanitized untrusted prompt labels; view/remove/reset; denials never become resentment
- **`audit_log.py`** — local append-only log for autostart/theme changes
- **`autostart.py`** — "Start Agetha when I sign in" via visible Startup-folder shortcut; path-normalized target+args validation; refuses foreign/malformed overwrite/delete; PowerShell env-var path passing
- **`win_integration.py`** — allowlisted `open_settings`, `set_theme` with existence-aware rollback chain, `recycle_bin_status` aggregates only
- **`status_providers.py`** — default-off coarse local observations; pausable
- **`tray_scaffold.py`** — optional pystray compatibility scaffold (not bundled; silent when absent)
- All gated Windows mutations are Danger/Caution + config-default-off where required; Medic/docs/tests updated (`tests/test_phase6_v5.py`)

### v4.0.0 — Presence & Realism (Phase 5)

- **`rhythm.py`** — circadian internal clock: six day-phases flavor her energy and mood (drowsy deep-night whispers, sharp mornings); compact `INTERNAL CLOCK` block injected into AI context
- **`dreams.py`** — dream journal: entering deep sleep weaves fragments of real episodic/long-term memories into surreal dream entries (`memory/dreams.jsonl`); one-shot `DREAM RECALL` on waking; new `view_dreams` command
- **`tasks.py`** — task keeper: `add_task` / `complete_task` / `list_tasks` persisted to `memory/tasks.json`; pending tasks injected into ambient context so she nags in character
- All new commands are **Safe tier** (they only touch `memory/`); features are config-gated and degrade gracefully when disabled
- **Config:** `ENABLE_CIRCADIAN_RHYTHM`, `RHYTHM_NIGHT_START/END`, `ENABLE_DREAMS`, `DREAMS_MAX_ENTRIES`, `ENABLE_TASKS`, `TASKS_MAX_ENTRIES`
- **Medic_Checker v4.0** — compiles 23 modules, imports Phase 1–5 extensions, reports `dreams.jsonl` / `tasks.json` status
- **Tests:** `tests/test_phase5_v4.py` (29 tests — rhythm phases, dream lifecycle, task CRUD, command wiring)

### v3.5.0 — Voice, OpenRouter & UX (tamsamas upstream patterns)

- **`voice_input.py`** — microphone input with Google STT or local faster-whisper
- **File drag-and-drop** — drop files onto the GIF (`tkinterdnd2`)
- **OpenRouter** — optional cloud backend (`ENABLE_OPENROUTER`, key in `.env`)
- **Token % UI** — Groq daily budget estimate in input placeholder + status bar
- **`FASTER_MODE`** — shorter prompts for lower token cost
- **Secrets** — API keys documented as `.env` only; `config.txt` has no key lines
- **Medic_Checker** — optional package install for voice/STT/DnD/TTS; 16-module compile check + Phase 1+2 import verify

### v3.0 — Quality & Safety Overhaul

- **`command_guard.py`** — 3-tier native confirmation dialogs with Windows warning icons
- **`command_handlers.py`** — command pattern refactor (43 handlers); `main.py` slimmed to ~1,650 lines
- **`system_commands.py`** — OS utilities extracted (volume, wallpaper, shutdown, clipboard…)
- **`utils.py`** — shared platform helpers, logging, `.env` loader
- **New commands:** `open_url`, `system_info`, `set_volume`, `set_wallpaper`, `search_files`, `type_text`, `lock_screen`, `shutdown`, `restart`, `set_reminder`, `get_clipboard`, `open_folder`, `target_window_close`, `change_mood`, `clear_memory`
- **UX:** Escape to abort AI; input stays enabled during ambient polls; subtitle errors on failed file ops
- **Reliability:** null guards, retry limits, config validation, OCR resolution cap
- **Medic_Checker.ps1 v3.6** — Phase 1+2 modules, TTS optional install, memory file status

### Phase 3 — Spatial OCR

- Focused window capture, spatial word coordinates, regex pattern registry, 4-layer context injection

### Phase 2 — Psychology & Windows

- Deep moods, attention snap, external window control via ctypes, native emotion sounds

### Phase 1 — Foundation

- Core command dispatch, OCR loop, Groq integration, memory system

### Screen-monitoring validation

The mock-based reliability suite does not need a display, Tesseract executable,
network service, CUDA, or platform desktop utilities:

```powershell
python -m unittest tests.test_screen_monitoring_reliability -v
python -m unittest discover -s tests
```

For manual acceptance, verify focused capture and coordinate placement on every
monitor; own-window and configured-exclusion skips; unchanged, forced-refresh,
and changed-frame statuses; repeated/cleared error events; rapid standard/deep
requests; shutdown during OCR; and external-context redaction. Linux desktop
fallbacks are additionally covered by mocked headless tests. macOS behavior is
outside the supported validation matrix as of v5.5.5.

---

## Warning & Disclaimer

**Agetha Mod is experimental software provided "as is".**

By using this software you accept full responsibility for any outcome. The author is not liable for data loss, system instability, unexpected behavior, or damages from OS commands Agetha executes (even with confirmation dialogs).

### Recommended usage

- Review source code before running
- Keep `ENABLE_COMMAND_EXECUTION = yes` only if you trust the AI + confirmation layer
- Store API keys in `.env`, never in committed files
- Test on a non-critical machine first

---

## License & Credits

**License:** GNU General Public License v3.0 (GPL-3.0) — see `LICENSE`

**Credits:**
- **Agetha Mod** — [SiriusNovyx](https://github.com/SiriusNovyx/Agetha.exe)
- **Original Agetha.exe** — [tamsamas](https://github.com/tamsamas/Agetha.exe)

Fork support and [issue reports](https://github.com/SiriusNovyx/Agetha.exe/issues) belong to SiriusNovyx. The original upstream project does not maintain or support this fork.

Feedback, bug reports, and pull requests welcome.

Have fun — and try not to make Agetha too angry.
