# Agetha Mod — Agent Guide

Guidance for humans and coding agents working on this fork.  
**Priorities (locked):** (1) realism — she feels like a living process on this PC; (2) safety — Command Guard / confirmations / no real harm. Spectacle is optional flavor.

**Version focus:** Overhaul ~v5.0.0 · Entry: `python main.py` (prefer `Medic_Checker.bat`)

---

## What this project is

Windows desktop AI companion: always-on-top Win95-style window, animated GIF avatar, Groq/OpenRouter/Ollama chat, spatial OCR, dual memory, OS commands with native confirmation tiers.

Original upstream: Agetha.exe (chocolatebread / @tomiszivacs). This repo is a **fork** that owns the companion / virus-registry direction.

---

## Repository map

```
Agetha_Mod/
├── main.py                 # Tk UI, GifPlayer, CompanionApp state machine
├── medic_helper.py         # CLI helpers for Medic_Checker (features/realism)
├── Medic_Checker.ps1/.bat  # Health check + launcher
├── config.txt              # User settings (no secrets)
├── .env / .env.example     # API keys
├── requirements.txt
├── assets/                 # Avatar GIFs, font, icons, BSOD art
├── memory/                 # soul.md, episodic, longterm, stats, notepad
├── tests/                  # Phase QA (incl. realism + GIF coverage)
└── agetha/
    ├── app_config.py       # Typed config loader
    ├── utils.py            # Logging, paths, platform helpers
    ├── core/               # AI, memory, companion_stats
    ├── commands/           # Guard + handlers + OS utils
    ├── platform/           # OCR, Win32 windows, voice STT
    ├── features/           # TTS, web RAG
    └── ui/                 # Dashboard, glitch, trivia, Win95 chrome
```

### Touch points when changing behavior

| Concern | Primary files |
|---------|----------------|
| Avatar / GIF moods | `main.py` (`EXTRA_GIFS`, `TALKING_MOOD_GIFS`, `_apply_state`) |
| Prompt / moods / commands | `agetha/core/ai_engine.py` |
| Safety confirmations | `agetha/commands/command_guard.py` |
| Command routing | `agetha/commands/command_handlers.py` |
| Host heat / stats | `agetha/core/companion_stats.py` |
| Session recap / BM25 | `agetha/core/memory_search.py` |
| OCR errors | `agetha/platform/screen_reader.py` |
| Launcher checks | `Medic_Checker.ps1`, `medic_helper.py` |

---

## Character visual identity (shared across mood GIFs)

- Geometric / paper-cutout Agetha on **solid black** background  
- Messy purple–blue hair, pink ahoge, often peach “horn” side tufts  
- Cyan / teal eyes, pink blush, pink necktie  
- Dark navy top, light shorts, **chunky striped boots/limbs** (pink / tan / blue)  
- Heavy **dither / CRT mesh** texture (intentional lo-fi virus aesthetic)  
- Display size in UI: ~340×300 (`GIF_W` / `GIF_H` in `main.py`)

---

## GIF asset catalog (all 19 must stay wired)

Frame counts from file metadata. Visual notes from direct asset inspection (multi-frame GIFs may include glitch/static frames — see warnings).

| File | Frames | ~Size | Visual / motion | Runtime trigger (current code) |
|------|--------|-------|-----------------|--------------------------------|
| `idle-1.gif` | 42 | 585 KB | Soft bounce idle, tired `:3` face | Neutral idle when affection high |
| `idle-2.gif` | 42 | 656 KB | Smug / playful blink + bob | Neutral idle mid affection |
| `idle-3.gif` | 42 | 504 KB | Belly-rub smug / satiated | Neutral idle when affection low or CPU hot |
| `talking-1.gif` | 102 | 619 KB | Talk / gesture loop | Neutral talk; angry/surprised/paranoid talk band |
| `talking-2.gif` | 102 | 619 KB | Shy — hand near mouth | Whisper / vulnerable / melancholic talk |
| `talking-3.gif` | 102 | 1.0 MB | **⚠ Inspect:** may present as heavy static/noise on some frames | Hype talk band (happy/excited/manic/dominant) + rotation |
| `happy.gif` | 102 | 4.8 MB | **⚠ Inspect:** large clip; some frames may be glitch/static before character | Mood `happy` (speak) |
| `happy-static.gif` | 1 | 64 KB | Held happy / grin pose | After happy; sticky idle |
| `sad.gif` | 102 | 710 KB | Soft sad bounce, heavy lids | `sad` / `melancholic` speak |
| `sad-static.gif` | 1 | 35 KB | Held soft expression | After sad; `vulnerable` idle |
| `angry.gif` | 102 | 910 KB | Wide grin / intense sway | `angry` / `dominant` speak |
| `angry-static.gif` | 1 | 36 KB | Held intense / teeth | After angry; dominant idle |
| `thinking.gif` | 102 | 487 KB | Processing bounce | State `thinking`; mood `thinking` |
| `thinking-static.gif` | 1 | 32 KB | Still thinking face | After thinking; `whisper` idle |
| `surprised.gif` | 102 | 755 KB | Bigger bounce / open expression | `surprised` / `paranoid`; wake-from-sleep |
| `want.gif` | 102 | 2.9 MB | Craving / focus loop (drag + hype moods) | `excited` / `manic`; **file drag-over** |
| `loaf.gif` | 42 | 365 KB | Compact “bean” / spin loaf form | After `LOAF_TIMER_MIN` idle |
| `sleeping.gif` | 102 | 1.4 MB | Sleep pose (plays slower in loader) | Boot wake + deep sleep after loaf |
| `error.gif` | 42 | 469 KB | Classic **XP-style BSOD** (`PAGE_FAULT_IN_NONPAGED_AREA`) | `show_error_gif`; user **denies** Caution/Danger |

### Non-GIF assets

| File | Role |
|------|------|
| `assets/barrio.ttf` | UI / subtitle font |
| `assets/icon.ico` | Window / shortcut icon |
| `assets/bsod.png` | Modern Win10-style BSOD art for **glitch overlay** (`ENABLE_GLITCH_EFFECTS`) — not the avatar GIF |

### Mood → GIF maps (source of truth: `main.py`)

- **Idle sticky / presence:** `EXTRA_GIFS` + `EXTRA_STATIC_GIFS`  
- **While speaking:** `TALKING_MOOD_GIFS` (prefer animated; whisper → `talking-2.gif`)  
- **Neutral talk bands:** `TALKING_BY_MOOD` → talking-1/2/3  
- **Always loaded extras:** `EXTRA_LOAD_GIFS` = `error.gif`, `want.gif`  
- **Coverage test:** `tests/test_phase4_realism.py` → `TestGifAssetCoverage`

**Rule for agents:** Do not leave any `assets/*.gif` unreferenced. If you add a GIF, map it and extend Medic’s asset list + the coverage test.

---

## App state machine (avatar)

```
SLEEPING → (wake) → IDLE ⇄ THINKING
                ↓
            TALKING → IDLE (sticky mood static/anim)
IDLE → (LOAF_TIMER) → loaf.gif → (LOAF_TIMER) → SLEEPING
```

- Ambient AI polls **skip** while `STATE_SLEEPING` (presence rest).  
- User chat / touch / keystroke wakes from loaf/sleep.  
- Deep moods from AI: `manic|melancholic|paranoid|vulnerable|dominant` (+ surface moods).

---

## Safety invariants (do not break)

1. Risky OS actions go through `CommandGuard` tiers + native Yes/No when `ENABLE_COMMAND_CONFIRMATIONS=yes`.  
2. No real persistence malware, credential theft, silent deletes, or “fake virus” that becomes real.  
3. Network (`ENABLE_WEB_RAG`), glitch (`ENABLE_GLITCH_EFFECTS`) stay config-gated.  
4. Session recap / OCR coding-assist are **read / speak** paths — must not auto-mutate OS.  
5. Denied action personality: flash `error.gif` + “Fine. I won’t.” (angry mood).

---

## Realism features (already in tree)

- Host mood from CPU + idle: `suggest_mood_from_host()` in `companion_stats.py`  
- Session recap once per boot: `format_session_recap_for_prompt()`  
- OCR coding assist prompt when error tags present (`_screen_has_error_pattern`)  
- Presence: idle → loaf → sleep  
- Circadian clock (v4): `agetha/core/rhythm.py` → `format_rhythm_for_prompt()`  
- Dream journal (v4): `agetha/core/dreams.py` — dream on deep sleep, one-shot recall on wake, `view_dreams`  
- Task keeper (v4): `agetha/features/tasks.py` — `add_task`/`complete_task`/`list_tasks`, pending nag context  
- Emotion engine (v5): `agetha/core/emotion_engine.py` + `emotional_history.py` — tone only; denials are mild; memories untrusted in prompts  
- Transparent Windows (v5): `autostart.py` (Startup shortcut only), `win_integration.py`, `status_providers.py`; tray is optional scaffold (`tray_scaffold.py`)  
- Medic: `medic_helper.py realism` → `REALISM_OK` (covers v4+v5 APIs)

---

## Engineering rules for agents

1. **Minimal diffs** — no whole-file rewrites of `main.py` / `ai_engine.py`.  
2. **Read before write** — map callers (Headroom / grep) before editing.  
3. **Python 3.13+ style** — `str | None`, `pathlib.Path`, no bare `except:`.  
4. **No new third-party deps** without explicit user approval.  
5. **Windows paths** via `pathlib`; never assume POSIX.  
6. **Secrets** only in `.env`, never commit keys.  
7. Prefer extending `agetha/` packages over growing `main.py` further.  
8. After GIF/mood changes: run `python tests/test_phase4_realism.py`.  
9. After launcher changes: keep `Medic_Checker.ps1` + `medic_helper.py` in sync.

---

## Config knobs agents care about

| Key | Default idea | Notes |
|-----|--------------|-------|
| `ENABLE_COMMAND_CONFIRMATIONS` | yes | Safety — warn if off |
| `ENABLE_COMMAND_EXECUTION` | yes | Master OS kill-switch |
| `ENABLE_COMPANION_STATS_CONTEXT` | yes | Heat/infection in prompts |
| `LOAF_TIMER_MIN` | 15 | Idle → loaf → sleep cadence |
| `ENABLE_GLITCH_EFFECTS` | no (safe default) | Cosmetic only |
| `ENABLE_WEB_RAG` | no (safe default) | Network |
| `ENABLE_LONGTERM_MEMORY` | yes | BM25 + session recap archive |
| `FASTER_MODE` | user | Shorter prompts / less personality |

---

## Known asset caveats

1. **`happy.gif` / `talking-3.gif`:** Large multi-frame files; inspection sometimes surfaces **TV-static / glitch** frames. Treat as intentional virus aesthetic unless user replaces them with clean character loops.  
2. **`error.gif` vs `bsod.png`:** Avatar error path uses the GIF (XP BSOD style); glitch overlay may use `bsod.png` (Win10 sad-face BSOD). Do not conflate.  
3. **Deep moods share files** where the pack has no unique clip (e.g. dominant → angry). Prefer new dedicated GIFs over breaking existing maps.  
4. **Do not delete `assets/`** — Medic and the avatar both hard-depend on the full set.

---

## Quick verification

```bat
Medic_Checker.bat
python tests/test_phase4_realism.py -v
python medic_helper.py realism
python medic_helper.py features
```

Manual GIF smoke:

1. Chat with varied moods → happy/sad/angry/thinking/want/surprised  
2. Drag file onto avatar → `want.gif`  
3. Deny a dangerous command → `error.gif`  
4. Leave idle ≥ `LOAF_TIMER_MIN` → loaf → sleep  

---

## Out of scope / avoid

- Competing with upstream feature-for-feature  
- Pure toy chaos that breaks immersion (spam BSOD, window flocks)  
- Rewriting the entire Tk shell “for cleanliness”  
- Turning cosplay virus into actual malware
