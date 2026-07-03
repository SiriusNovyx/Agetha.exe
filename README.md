# Agetha Mod — Overhaul Edition

> A modified fork of [Agetha.exe](https://chocolatebread.ddns.net/agetha.html) (v4.2.0) with enhanced desktop integration, spatial OCR, emotional AI, native safety confirmations, and expanded OS control.

**Version:** Overhaul v3.6.0 · **Medic_Checker:** v3.6 · **Original author:** @tomiszivacs

---

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

### Dual-Layer Memory

| Layer | File | Purpose |
|-------|------|---------|
| Static identity | `memory/soul.md` | Personality, mood rules, triggers (editable Markdown) |
| Episodic memory | `memory/episodic_memory.json` | Timestamped interaction log (max 50 entries) |

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
- **FASTER_MODE** — `FASTER_MODE = yes` uses shorter prompts (less personality, fewer tokens, cheaper). Title bar shows `FAST MODE`

---

## Project Structure

```
Agetha_Mod/
├── main.py              # Tkinter UI, GIF player, app lifecycle
├── ai_engine.py         # Groq/Ollama brain, JSON command parsing
├── screen_reader.py     # OCR, pattern matching, window capture
├── memory_system.py     # soul.md + episodic memory
├── command_guard.py     # 3-tier native confirmation dialogs
├── command_handlers.py  # Command pattern dispatch (43 handlers)
├── system_commands.py   # OS utilities (volume, wallpaper, shutdown…)
├── window_control.py    # Win32 window find/move/resize/close
├── app_config.py        # Central config.txt loader & typed settings
├── voice_input.py       # Microphone STT (Google / faster-whisper)
├── utils.py             # Shared helpers, logging, .env loader
├── config.txt           # User settings only — no API keys
├── .env.example         # API key template
├── requirements.txt     # Pinned Python dependencies
├── Medic_Checker.ps1    # Startup health check & launcher (v3.6)
├── Medic_Checker.bat    # Thin launcher → runs Medic_Checker.ps1
├── Run_Agetha_Admin.ps1 # Optional elevated launch for protected windows
├── assets/              # GIFs, fonts, icons
└── memory/              # soul.md, episodic_memory.json
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
| `search_memory` | BM25 search of long-term memory archive (`query`, optional `limit`) |
| `search_web` | DuckDuckGo web search (`query`, optional `limit`) — requires `ENABLE_WEB_RAG=yes` ⚠ |
| `fetch_webpage` | Fetch visible text from a URL (`url`) — requires `ENABLE_WEB_RAG=yes` ⚠ |
| `glitch_overlay` | Brief harmless CRT glitch overlay (`style`, `duration_ms`) — requires `ENABLE_GLITCH_EFFECTS=yes` |
| `read_notepad` | Read dashboard notepad (`memory/notepad.txt`) into AI context |
| `play_virus_trivia` | Open Win95 virus trivia minigame popup |
| `clear_memory` | Erase episodic memory (soul.md kept); `memory_scope`: all/recent/old/keep_5 |
| `view_memory` | Show recent episodic entries in popup |

⚠ = requires user confirmation (Danger or Caution tier)

---

## Quick Start

### Option A — Medic_Checker (recommended)

1. Place all project files in one folder
2. Double-click **`Medic_Checker.bat`** (or run **`Medic_Checker.ps1`** in PowerShell)
3. The script runs 7 health checks, installs missing packages, compiles modules, then launches Agetha

### Option B — Manual

```powershell
py -3.13 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Edit .env — add your Groq API key
python main.py
```

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
```

- Groq keys: [console.groq.com](https://console.groq.com)
- OpenRouter keys: [openrouter.ai/keys](https://openrouter.ai/keys)

`.env` overrides any matching key in `config.txt`. Never commit `.env` to git (already in `.gitignore`).

### config.txt

All **non-secret** settings live in `config.txt` and are loaded by `app_config.py`. Boolean values accept `yes`/`no`, `true`/`false`, `1`/`0`, or `on`/`off`.

#### AI backend

| Setting | Default | Description |
|---------|---------|-------------|
| `USE_LOCAL_AI` | `no` | Use Ollama instead of cloud APIs |
| `ENABLE_GROQ` | `yes` | Enable Groq (default cloud backend) |
| `ENABLE_OPENROUTER` | `no` | Use OpenRouter instead of Groq |
| `OPENROUTER_MODEL` | see `config.txt` | OpenRouter model slug |
| `FASTER_MODE` | `no` | Shorter prompts, less personality, cheaper |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model name |
| `LOCAL_AI_MODEL` | *(empty)* | Ollama model (`ollama list`) |
| `LOCAL_AI_TIMEOUT` | `30` | Ollama request timeout (seconds) |

API keys (`GROQ_API_KEY_*`, `OPENROUTER_API_KEY`) → **`.env` only**, not `config.txt`.

#### AI tuning

| Setting | Default | Description |
|---------|---------|-------------|
| `AI_TEMPERATURE` | `0.85` | Response randomness (0–2) |
| `AI_MAX_TOKENS` | `400` | Max tokens per reply |
| `AI_TOP_P` | `0.95` | Nucleus sampling (0–1) |
| `ENABLE_STREAMING` | `yes` | Stream Groq responses to UI |
| `ENABLE_AMBIENT_POLLS` | `yes` | Periodic screen-context AI polls |

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
| `INCLUDE_WINDOW_TITLE_IN_CONTEXT` | `yes` | Add window title to AI context |
| `TESSERACT_PATH` | *(empty)* | Custom path to `tesseract.exe` |

#### UI

| Setting | Default | Description |
|---------|---------|-------------|
| `WINDOW_TOPMOST` | `yes` | Keep Agetha above other windows |
| `WINDOW_START_X` / `Y` | `80` | Initial window position |
| `SUBTITLE_CHAR_DELAY` | `0.035` | Typewriter subtitle speed (seconds) |
| `ANIMATION_SPEED` | `0.6` | GIF speed multiplier |

Click the **📊** button in the title bar (beside minimize) to open the **Dashboard** — retro progress bars for CPU/RAM/disk/core heat, virus registry stats, notepad, and limited config toggles (safe yes/no keys).

#### Medic_Checker (launcher)

| Setting | Default | Description |
|---------|---------|-------------|
| `SKIP_TESSERACT_CHECK` | `no` | Skip Tesseract step in health check |
| `SKIP_ASSET_CHECK` | `no` | Skip asset file verification |
| `AUTO_PIP_INSTALL` | `yes` | Auto `pip install` missing packages |
| `CREATE_DESKTOP_SHORTCUT` | `no` | Create Desktop shortcut on Medic_Checker run |
| `CHECK_FOR_UPDATES` | `yes` | Compare `APP_VERSION` to GitHub release API |
| `APP_VERSION` | `3.6.0` | Shown in window title |
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
| `TTS_RATE` | `165` | pyttsx3 speech rate (80–300) |
| `TTS_VOLUME` | `0.8` | TTS volume (0.0–1.0) |
| `TTS_VOICE_NAME` | *(empty)* | Partial match on installed voice id/name; empty = system default |

TTS is **optional**. The app runs without `pyttsx3`; install only when using `tts_only` or `both`:

```bash
pip install "pyttsx3>=2.90,<3.0.0"
```

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
OPENROUTER_MODEL = nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free
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
VOICE_OUTPUT_MODE = bleeps_only   # bleeps_only | tts_only | both
TTS_RATE = 165
TTS_VOLUME = 0.8
TTS_VOICE_NAME =                  # partial match, e.g. Zira or David
```

```bash
pip install "pyttsx3>=2.90,<3.0.0"   # only needed for tts_only / both
```

Run **Medic_Checker** after enabling — it installs optional packages when `AUTO_PIP_INSTALL = yes`.

---

## Medic_Checker v3.6 (PowerShell)

Startup wrapper that validates your environment before launch:

| Step | Check |
|------|-------|
| Pre-flight | All 16 core modules + `requirements.txt` present |
| [A–D] | ARM64/Snapdragon x64 Python detection & auto-install |
| [1/7] | Python installed |
| [2/7] | Virtual environment create/activate |
| [3/7] | Packages from `requirements.txt`; optional voice/STT/DnD/**TTS** when enabled in `config.txt` |
| [4/7] | Tesseract OCR (optional — enables screen reading) |
| [5/7] | All 20 assets in `assets\` |
| [6/7] | Config, `.env`, `memory\` (`soul.md`, episodic, long-term JSONL, stats, notepad); reports `ENABLE_LONGTERM_MEMORY` and `VOICE_OUTPUT_MODE` |
| [7/7] | `py_compile` all 16 Python modules; import check for Phase 1+2 extensions |

**Color codes:** `[ OK ]` green · `[WARN]` yellow · `[FAIL]` red

On Snapdragon/ARM64 Windows, the checker ensures **x64 (AMD64) Python** is used so binary wheels (pygame, pyautogui, mss) install correctly under Prism emulation.

---

## Requirements

- **Python 3.13.x** recommended (3.14 may have compatibility issues)
- **Tesseract OCR** — [Windows installer](https://github.com/UB-Mannheim/tesseract/wiki) (optional, enables screen reading)
- **Assets** — download from [chocolatebread.ddns.net/agetha.html](https://chocolatebread.ddns.net/agetha.html)
- **Groq API key** (in `.env`), **OpenRouter** (optional), or **Ollama** for AI responses
- **Microphone** — optional, for voice input (`ENABLE_VOICE = yes`)
- **PyAudio** — optional, required for microphone (installed by Medic_Checker)

### Python packages (`requirements.txt`)

**Core:**
```
pillow, numpy, requests, groq, pyautogui, pytesseract, mss, pygame, psutil
```

**Optional** (installed by Medic_Checker when enabled in `config.txt`):
```
SpeechRecognition, PyAudio          # ENABLE_VOICE = yes
faster-whisper                    # USE_LOCAL_STT = yes
tkinterdnd2                       # ENABLE_FILE_DRAG_DROP = yes (Windows)
pyttsx3                           # VOICE_OUTPUT_MODE = tts_only | both
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
- **Original Agetha.exe** — @tomiszivacs
- **Fork / Overhaul** — SiriusNovyx

Feedback, bug reports, and pull requests welcome.

Have fun — and try not to make Agetha too angry.
