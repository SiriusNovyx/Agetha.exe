## Agetha Mod pre-3.6.0 — Companion Overhaul (pre-release)

**Pre-release** for the v3.6.0 overhaul: modular `agetha/` package, Win95 companion UI, web RAG, optional TTS/glitch effects, and screen-reader fixes for interactive chat.

> Upgrade from **v3.5.5**. This is a **draft pre-release** — expect API/layout shifts; report issues before the stable `v3.6.0` tag.

---

### Architecture

- **`agetha/` package** — code reorganized from flat modules into `core/`, `commands/`, `platform/`, `features/`, and `ui/`
- **`tests/`** — phase QA suites (`test_phase1_qa` … `test_phase4_realism`)
- **`main.py`** — slim Tkinter entry point; imports from `agetha.*`

---

### New companion features

- **Win95 dashboard** (`agetha/ui/dashboard.py`) — stats panel, notepad (`memory/notepad.txt`), companion registry UI
- **Companion stats** (`agetha/core/companion_stats.py`) — tracks bites, ticks, tone; injects `COMPANION STATS` into AI prompt when `ENABLE_COMPANION_STATS_CONTEXT=yes`
- **Web RAG** (`agetha/features/web_rag.py`) — `search_web` + `fetch_webpage` (DuckDuckGo + HTML text extract); **off by default** (`ENABLE_WEB_RAG=no`)
- **Long-term memory search** (`agetha/core/memory_search.py`) — BM25 over `memory/longterm_memory.jsonl` via `search_memory`
- **Optional TTS** (`agetha/features/tts_player.py`) — Piper/offline speech when `ENABLE_TTS=yes`
- **Glitch overlay** (`agetha/ui/glitch_overlay.py`) — harmless CRT-style effects; **off by default** (`ENABLE_GLITCH_EFFECTS=no`)
- **Virus trivia minigame** (`agetha/ui/virus_trivia.py`) — Win95 popup via `play_virus_trivia`
- **Win95 window chrome** (`agetha/ui/w95_window.py`) — shared borderless/dialog styling

### New commands

| Command | Description |
|---------|-------------|
| `search_memory` | BM25 search of long-term memory archive |
| `search_web` | DuckDuckGo search (requires `ENABLE_WEB_RAG=yes`) |
| `fetch_webpage` | Fetch page text from URL (requires `ENABLE_WEB_RAG=yes`) |
| `read_notepad` | Read dashboard notepad into AI context |
| `play_virus_trivia` | Open virus trivia minigame |
| `glitch_overlay` | Brief visual glitch (requires `ENABLE_GLITCH_EFFECTS=yes`) |

---

### Screen reader fixes (pre-release patch)

- **OCR on user messages** — `capture_text()` now runs when you chat, not only on ambient polls
- **Typing pause bypass** — user-initiated ticks skip the 8s OCR pause (you just typed; fresh context is expected)
- **`request_screen_read` race** — follow-up `_ai_query` deferred until `_ai_busy` clears (no silent drop)

---

### Medic_Checker v3.6

- Phase 1–4 module compile + import checks
- Optional installs: TTS, web RAG deps, voice/STT, drag-and-drop
- Memory file status reporting

---

### Upgrade from v3.5.5

1. Pull or download this pre-release (`pre-3.6.0` tag)
2. `pip install -r requirements.txt`
3. Copy new keys from `config.txt` / README if missing (`ENABLE_WEB_RAG`, `ENABLE_GLITCH_EFFECTS`, `ENABLE_COMPANION_STATS_CONTEXT`, etc.)
4. Set `APP_VERSION = 3.6.0` in `config.txt` (optional — window title)
5. Run **`Medic_Checker.bat`** then restart Agetha

No migration needed for `memory/` or `.env`. Imports changed — launch via `python main.py` (not old `ai_engine.py` paths).

---

### Files changed (high level)

| Area | Key files |
|------|-----------|
| Package layout | `agetha/**`, `tests/**` |
| Entry | `main.py`, `medic_helper.py`, `Medic_Checker.ps1` |
| AI + memory | `agetha/core/ai_engine.py`, `memory_system.py`, `memory_search.py`, `companion_stats.py` |
| Commands | `agetha/commands/command_handlers.py`, `command_guard.py` |
| Platform | `agetha/platform/screen_reader.py`, `voice_input.py`, `window_control.py` |
| UI / features | `agetha/ui/*`, `agetha/features/web_rag.py`, `tts_player.py` |
| Docs | `README.md`, `requirements.txt` |

**Full diff**: https://github.com/SiriusNovyx/Agetha.exe/compare/v3.5.5...pre-3.6.0

---

### Known pre-release notes

- Web RAG and glitch effects are **opt-in** (disabled by default for safety)
- `FASTER_MODE` still uses shorter prompts; screen context is truncated to 400 chars in the AI prompt
- Run phase tests: `python -m unittest discover -s tests -p "test_*.py"`
