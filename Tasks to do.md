# Task Checklist — Agetha Quality & Feature Overhaul

`FASTER_MODE` saves tokens mainly by **shrinking what gets sent every request** — not by changing `AI_MAX_TOKENS` (still **400** for both modes) or history size (`HISTORY_LIMIT` = **6**).

## What changes

| Area | Normal | `FASTER_MODE` |
|------|--------|----------------|
| System prompt | Full `SYSTEM_PROMPT` (~10k chars) + **`soul.md`** via `build_system_prompt()` | Tiny `SYSTEM_PROMPT_FASTER` (~650 chars), **no soul.md** |
| Episodic memory | Up to **10** formatted entries | Max **5** plain `- summary` lines |
| Few-shot examples | **44** pairs (`FEW_SHOTS`) | **6** pairs (`FEW_SHOTS_FASTER`) |
| `characters.txt` | Injected | **Skipped** |
| Output style | Full personality, longer segments | **1–8 words** per segment |

Token math in the app uses `len(text) // 4` (see `_estimate_tokens` in `ai_engine.py`).

## Estimated token savings

### Fixed per-request overhead (system + few-shots)

Rough character counts from the source strings:

| Component | Normal (~tokens) | Faster (~tokens) | Reduction |
|-----------|------------------|------------------|-----------|
| System core | ~2,550 | ~160 | **~94%** |
| `soul.md` | ~1,125 | 0 | **100%** |
| Episodic (typical) | ~250–400 | ~60–100 | **~70–80%** |
| Few-shots | ~2,800–3,200 | ~200–230 | **~92–93%** |
| **Fixed overhead total** | **~6,700–7,300** | **~420–490** | **~93–94%** |

So the **static prompt alone** uses about **94% fewer tokens** in fast mode.

### Full request (typical active session)

Shared on both modes: conversation history (~500–800 tokens at 6 turns), current user turn (~50–100), screen context (up to 400 chars), and up to 400 output tokens.

| Scenario | Normal (input ≈) | Faster (input ≈) | **Less input** |
|----------|------------------|------------------|----------------|
| Fresh chat (no history) | ~10,000–11,000 | ~700–900 | **~91–93%** |
| Mature session (6-turn history) | ~11,000–12,000 | ~1,500–1,800 | **~84–86%** |
| Heavy history (if you raised limit) | savings shrink as history dominates | | **~70–75%** at ~20 turns |

**Overall:** expect roughly **85–93% less input tokens per API call** in typical use, with the biggest win on **ambient screen polls** (every ~2 min) where the huge normal prompt is resent repeatedly.

### Output (completion) tokens

`AI_MAX_TOKENS` is unchanged, but fast mode instructs shorter replies (`1–8 words` per segment). In practice:

- **`idle` polls:** ~same (~15–30 tokens)
- **`speak` replies:** often **~30–50% shorter** JSON (e.g. `"Hey."` vs multi-segment personality lines)

So **total** tokens (input + output) are often **~80–90% lower** per call when fast mode is on, with input doing most of the work.

## UI token % caveat

The status bar estimate in `get_token_status()` / `_estimate_request_tokens()` compares `SYSTEM_PROMPT` vs `SYSTEM_PROMPT_FASTER` and few-shots, but **does not include `soul.md` or full episodic formatting** when the memory system is enabled. So the UI may show something like **~75%** savings while real API usage is closer to **~85–90%** less in normal vs fast.

## Tradeoffs (not just tokens)

Fast mode is cheaper because it sends less context. You also lose:

- Full personality / soul depth  
- Most few-shot examples (weaker command behavior)  
- Several commands **not listed** in `SYSTEM_PROMPT_FASTER` (e.g. `shutdown`, `system_info`, `view_memory`, `clear_memory`, …)

**Bottom line:** `FASTER_MODE` is very effective for token cost — on the order of **~90% less fixed prompt overhead** and **~85%+ less input per request** in normal use — at the cost of personality and some command coverage. Best for heavy ambient polling or Groq budget pressure; worse when you want full Agetha behavior.

Here’s the gap between what the app **actually supports** (`VALID_COMMANDS` in `ai_engine.py`) and what **`FASTER_MODE` tells the model** (`SYSTEM_PROMPT_FASTER` line 381).

## Commands in normal mode but **not** named in fast mode

| Category | Missing in `FASTER_MODE` |
|----------|--------------------------|
| **Paths / files** | `request_path`, `list_directory`, `open_folder`, `search_files` |
| **Dialogs & sounds** | `show_dialog`, `play_emotion_sound` |
| **Window control** | `target_window_resize` |
| **Clipboard** | `copy_to_clipboard`, `get_clipboard` |
| **System** | `system_info`, `set_volume`, `set_wallpaper`, `type_text`, `lock_screen` |
| **Power** | `shutdown`, `restart` |
| **Memory** | `view_memory`, `clear_memory` |
| **Reminders** | `set_reminder` |

That’s **18 commands** omitted from the fast prompt list.

## What fast mode *does* include (29 commands)

`idle`, `speak`, `popup`, `open_app`, `open_browser`, `open_url`, `request_screen_read`, `wake_user`, `create_folder`, `create_file`, `write_file`, `delete_file`, `rename_file`, `set_clipboard`, `play_sound`, `take_screenshot`, `show_notification`, `read_document`, `list_dir`, `run_command`, `force_close`, `show_error_gif`, `move_window`, `snap_to_center`, `monitor_process`, `open_file`, `target_window_move`, `target_window_close`, `change_mood`

## Important nuance

- **`shutdown`** — not listed as a command in fast mode, but the fast rules say `shutdown:true` on the **`speak`** response when the user wants to exit. That’s different from the full `{"command":"shutdown", ...}` or `{"command":"restart", ...}` commands in normal mode.
- **`list_directory`** — alias of `list_dir` at runtime; fast mode only mentions `list_dir`.
- The model can still **emit** any command the parser accepts, but with fast mode it’s **less likely** to use omitted commands because it never sees examples or explicit rules for them (and the few-shot set is much smaller).

So if you rely on things like `system_info`, `lock_screen`, `view_memory`, `show_dialog`, or `target_window_resize`, normal mode is much better aligned than `FASTER_MODE`.
