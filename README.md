# Agetha Mod — Overhaul Edition

> A modified fork of [Agetha.exe](https://chocolatebread.ddns.net/agetha.html) (v4.2.0) with enhanced desktop integration, spatial OCR, emotional AI, native safety confirmations, and expanded OS control.

**Version:** Overhaul v3.0 · **Medic_Checker:** v3.0 · **Original author:** @tamsamas · **Modified:** @SiriusNovyx

---

## About

Agetha is a **desktop AI companion** — a small always-on-top Windows 95–style window with an animated character who lives on your machine. She chats with you, watches your screen via OCR, remembers context across sessions, and can execute real OS actions through a JSON command system powered by **Groq** (default) or **Ollama** (local).

This fork makes Agetha feel sharper, more autonomous, and more integrated with your desktop: spatial error detection, mood-driven window snapping, process monitoring, and a full command library with **native confirmation dialogs** before dangerous actions.

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

---

## Project Structure

```
Agetha_Mod/
├── main.py              # Tkinter UI, GIF player, app lifecycle
├── ai_engine.py         # Groq/Ollama brain, JSON command parsing
├── screen_reader.py     # OCR, pattern matching, window capture
├── memory_system.py     # soul.md + episodic memory
├── command_handlers.py  # Command pattern dispatch (43 handlers)
├── command_guard.py     # 3-tier native confirmation dialogs
├── system_commands.py   # OS utilities (volume, wallpaper, shutdown…)
├── utils.py             # Shared helpers, logging, .env loader
├── config.txt           # User settings (no secrets — use .env)
├── .env.example         # API key template
├── requirements.txt     # Pinned Python dependencies
├── Medic_Checker.ps1    # Startup health check & launcher (v3.0)
├── Medic_Checker.bat    # Thin launcher → runs Medic_Checker.ps1
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
| `clear_memory` | Erase episodic memory (soul.md kept) |

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

### API Keys (`.env` — recommended)

```bash
copy .env.example .env
```

Edit `.env` and add up to 10 Groq keys (rotates on rate limits):

```
GROQ_API_KEY_1=gsk_your_key_here
```

Get free keys at [console.groq.com](https://console.groq.com).

`.env` takes priority over `config.txt`. Never commit `.env` to git (already in `.gitignore`).

### config.txt

| Setting | Description |
|---------|-------------|
| `USE_LOCAL_AI` | `yes` to use Ollama instead of Groq |
| `GROQ_MODEL` | Default: `llama-3.3-70b-versatile` |
| `ENABLE_COMMAND_EXECUTION` | `yes`/`no` — master switch for OS commands |
| `MEMORY_CHARS` | Long-term memory chars injected per prompt |
| `HISTORY_LIMIT` | Recent conversation turns kept |
| `ANIMATION_SPEED` | GIF speed multiplier (default `0.6`) |

### Local AI (Ollama)

```ini
USE_LOCAL_AI = yes
LOCAL_AI_MODEL = llama3
```

Run `ollama list` to see installed models.

---

## Medic_Checker v3.0 (PowerShell)

Startup wrapper that validates your environment before launch:

| Step | Check |
|------|-------|
| Pre-flight | All 9 core modules + `requirements.txt` present |
| [A–D] | ARM64/Snapdragon x64 Python detection & auto-install |
| [1/7] | Python installed |
| [2/7] | Virtual environment create/activate |
| [3/7] | Packages from `requirements.txt` (+ optional tkextrafont) |
| [4/7] | Tesseract OCR (optional — enables screen reading) |
| [5/7] | All 20 assets in `assets\` |
| [6/7] | Config, `.env`, `memory\`, `soul.md` |
| [7/7] | `py_compile` all 8 Python modules |

**Color codes:** `[ OK ]` green · `[WARN]` yellow · `[FAIL]` red

On Snapdragon/ARM64 Windows, the checker ensures **x64 (AMD64) Python** is used so binary wheels (pygame, pyautogui, mss) install correctly under Prism emulation.

---

## Requirements

- **Python 3.13.x** recommended (3.14 may have compatibility issues)
- **Tesseract OCR** — [Windows installer](https://github.com/UB-Mannheim/tesseract/wiki) (optional, enables screen reading)
- **Assets** — download from [chocolatebread.ddns.net/agetha.html](https://chocolatebread.ddns.net/agetha.html)
- **Groq API key** or **Ollama** for AI responses

### Python packages (`requirements.txt`)

```
pillow, numpy, requests, groq, pyautogui, pytesseract, mss, pygame, psutil
```

---

## Controls

| Input | Action |
|-------|--------|
| Text box + Enter | Send message to Agetha |
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
ai_engine.py      →  Groq/Ollama → JSON command
        ↓
command_guard.py  →  Native confirmation (if needed)
        ↓
command_handlers.py → Execute action + update UI
```

---

## Changelog (Overhaul)

### v3.0 — Quality & Safety Overhaul

- **`command_guard.py`** — 3-tier native confirmation dialogs with Windows warning icons
- **`command_handlers.py`** — command pattern refactor (43 handlers); `main.py` slimmed to ~1,650 lines
- **`system_commands.py`** — OS utilities extracted (volume, wallpaper, shutdown, clipboard…)
- **`utils.py`** — shared platform helpers, logging, `.env` loader
- **New commands:** `open_url`, `system_info`, `set_volume`, `set_wallpaper`, `search_files`, `type_text`, `lock_screen`, `shutdown`, `restart`, `set_reminder`, `get_clipboard`, `open_folder`, `target_window_close`, `change_mood`, `clear_memory`
- **UX:** Escape to abort AI; input stays enabled during ambient polls; subtitle errors on failed file ops
- **Reliability:** null guards, retry limits, config validation, OCR resolution cap
- **Medic_Checker.ps1 v3.0** — PowerShell health check; `.bat` is a thin launcher

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

This fork is released under the **GNU General Public License v3.0 (GPL-3.0)**.

This means:

* You are free to **use, study, share, and modify** this software.
* Any modified version you distribute **must also be released under GPL-3.0**.
* You **must include** the original copyright notice and licence text.
* You **cannot** distribute this software under a more restrictive licence.
* There is **no warranty** of any kind.

The full licence text is in the `LICENSE` file in the repository root.

> **Note on the original project:** This fork's licence applies only to the code changes introduced here (Phases 1–3, memory system, tooling). The original Agetha.exe codebase by tamsamas is subject to its own licence terms. The GIF and font assets remain the property of tamsamas and are **not** covered by this fork's GPL-3.0 licence.

---

**Credits:**
- **Original Agetha.exe** — @tomiszivacs
- **Fork / Overhaul** — SiriusNovyx

Feedback, bug reports, and pull requests welcome.

Have fun — and try not to make Agetha too angry! :)
