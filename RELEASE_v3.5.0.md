## Agetha Mod v3.5.0 — Voice, OpenRouter & Desktop UX

Feature release on top of **Overhaul v3.0**. Adds upstream-style voice input, file drag-and-drop, optional OpenRouter, Groq token usage in the UI, and `FASTER_MODE` — while keeping the modular architecture, Command Guard, spatial OCR, and dual-layer memory.

---

### Highlights

- **Voice input** — 🎤 button; Google STT (online) or **faster-whisper** (offline, `USE_LOCAL_STT = yes`)
- **File drag-and-drop** — drop files onto Agetha's GIF (`tkinterdnd2` on Windows)
- **OpenRouter** — optional cloud backend (`ENABLE_OPENROUTER = yes`, key in `.env`)
- **Token % UI** — Groq daily budget estimate in input placeholder + status bar (`key 1/3 • 87% tokens left`)
- **`FASTER_MODE`** — shorter prompts, fewer tokens, lower cost; title bar shows `FAST MODE`
- **Secrets in `.env` only** — `config.txt` no longer holds API key lines

---

### New module

| File | Role |
|------|------|
| `voice_input.py` | Microphone STT, Win95 mic picker, `memory/settings.json` device save |

### Updated modules

| File | Changes |
|------|---------|
| `ai_engine.py` | OpenRouter client, `FASTER_MODE` prompts, Groq token tracking |
| `main.py` | Mic toggle, DnD handlers, token placeholder, `TkinterDnD` root |
| `app_config.py` | `ENABLE_VOICE`, `USE_LOCAL_STT`, `ENABLE_OPENROUTER`, `FASTER_MODE`, `ENABLE_FILE_DRAG_DROP` |
| `medic_helper.py` | Voice/DnD dependency checks for Medic_Checker |
| `Medic_Checker.ps1` | Optional package install; 12-module compile check |
| `config.txt` / `README.md` | Documented new settings; keys → `.env` |

---

### Configuration (quick reference)

**`.env`** (secrets only):
```
GROQ_API_KEY_1=gsk_...
OPENROUTER_API_KEY=sk-or-...   # if using OpenRouter
```

**`config.txt`** (no keys):
```ini
ENABLE_VOICE = no
USE_LOCAL_STT = no
ENABLE_FILE_DRAG_DROP = yes
ENABLE_OPENROUTER = no
OPENROUTER_MODEL = nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free
FASTER_MODE = no
APP_VERSION = 3.5.0
```

Run **`Medic_Checker.bat`** after enabling voice or drag-and-drop so optional packages install when `AUTO_PIP_INSTALL = yes`.

---

### Optional Python packages

| Package | When |
|---------|------|
| `SpeechRecognition`, `PyAudio` | `ENABLE_VOICE = yes` |
| `faster-whisper` | `USE_LOCAL_STT = yes` |
| `tkinterdnd2` | `ENABLE_FILE_DRAG_DROP = yes` (Windows) |

---

### Upgrade from v3.0.1

1. Pull or download this release
2. Copy `.env.example` → `.env` if you have not already; move any keys out of `config.txt`
3. Merge new `config.txt` keys (or copy the new sections from the template in `app_config.py`)
4. Run **`Medic_Checker.bat`**
5. Restart Agetha

No migration needed for `memory/` or episodic data.

---

### Attribution

- **Original Agetha.exe** — [tamsamas](https://github.com/tamsamas/Agetha.exe) (character, assets, base concept)
- Voice / OpenRouter / token UI patterns adapted from upstream tamsamas releases into this modular fork
- **Overhaul fork** — @SiriusNovyx

**Assets** are not bundled. Download from [chocolatebread.ddns.net/agetha.html](https://chocolatebread.ddns.net/agetha.html).

---

### Safety notice

Agetha can execute real OS actions. Dangerous commands still use native confirmation dialogs. Use at your own risk.

**Full changelog**: https://github.com/SiriusNovyx/Agetha.exe/compare/v3.0.1...v3.5.0
