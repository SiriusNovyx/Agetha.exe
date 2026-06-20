# Task Checklist — Agetha Quality & Feature Overhaul

## Phase 1: Critical Security & Crash Fixes
- `[x]` Create `.gitignore`
- `[x]` Create `.env.example` with API key template
- `[x]` Sanitize `config.txt` (remove hardcoded API keys)
- `[x]` Fix BUG-1: Guard `self._bleep.stop()` in `_shutdown()`
- `[x]` Fix BUG-2: Guard `self._screen.capture_text()` in `_ai_tick()`
- `[x]` Fix BUG-3: Guard `self._ai.query_streaming()` in `_ai_tick()`
- `[x]` Fix BUG-4: Guard `self._bleep.start_talking()` in `_set_state()`
- `[x]` Fix BUG-5: Max retries in `query_streaming()` (ai_engine.py)
- `[x]` Fix BUG-6: Max retries in `query()` (ai_engine.py)
- `[x]` Fix ERR-2: Wrap segment parsing in try/except (ai_engine.py)
- `[x]` Fix ERR-3: Move `show_dialog` to main thread (command_handlers.py)
- `[x]` Fix ERR-4: Temp file cleanup in screen_reader.py
- `[x]` Create `command_guard.py` (3-tier danger system + native Windows icons)

## Phase 2: Architecture Refactoring
- `[x]` Create `utils.py` (shared utilities)
- `[x]` Replace duplicates in main.py with utils imports
- `[x]` Replace duplicates in ai_engine.py with utils imports
- `[x]` Refactor `_dispatch_response()` into command pattern
- `[x]` Extract each command into individual handler method (`command_handlers.py`)
- `[x]` Integrate `CommandGuard` into dispatch
- `[x]` Add thread safety lock for `self._state`

## Phase 3: New Commands (13+)
- `[x]` `open_url` — open browser URL
- `[x]` `open_app` — launch application
- `[x]` `copy_to_clipboard` — copy text
- `[x]` `system_info` — CPU/RAM/disk report
- `[x]` `set_volume` — volume control
- `[x]` `set_wallpaper` — change wallpaper
- `[x]` `search_files` — file search by pattern
- `[x]` `type_text` — simulate keyboard
- `[x]` `lock_screen` — lock computer
- `[x]` `shutdown` — shutdown with delay
- `[x]` `restart` — restart with delay
- `[x]` `set_reminder` — timed reminder
- `[x]` `play_sound` — play audio file
- `[x]` `take_screenshot` — capture screen
- `[x]` Bonus: `get_clipboard`, `open_folder`, `target_window_close`, `change_mood`, `clear_memory`
- `[x]` Update ai_engine.py system prompt with new commands
- `[x]` Add few-shot examples for new commands

## Phase 4: Performance, UX & Polish
- `[x]` PERF-1: OCR resolution cap (screen_reader.py)
- `[x]` PERF-2: Lazy-load GIF frames (main.py)
- `[x]` PERF-3: Cache Font objects by size (main.py)
- `[x]` PERF-4: Lazy-test screenshot backends (screen_reader.py)
- `[x]` PERF-5: Remove redundant `import re` in `_draw()`
- `[x]` CQ-5: Remove unused `BLEEP_TONES` dict
- `[x]` UX-2: Keep input enabled during ambient polls
- `[x]` UX-3: Show subtitle on failed file operations
- `[x]` UX-4: Abort mechanism (Escape key)
- `[x]` UX-5: Capture `yesno` dialog results
- `[~]` Replace bare `except` blocks with logging (core paths done; some UI fallbacks remain)
- `[x]` Update `requirements.txt` with version pins
- `[x]` Code quality cleanup (duplicate imports, magic numbers in utils)
- `[x]` Config value validation (ai_engine.py)

## Verification
- `[x]` py_compile all files
- `[-]` Launch test (manual — run `python main.py`)
