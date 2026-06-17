# Task Checklist — Agetha Quality & Feature Overhaul

## Phase 1: Critical Security & Crash Fixes
- `[ ]` Create `.gitignore`
- `[ ]` Create `.env.example` with API key template
- `[ ]` Sanitize `config.txt` (remove hardcoded API keys)
- `[ ]` Delete duplicate `config.txt.txt`
- `[ ]` Fix BUG-1: Guard `self._bleep.stop()` in `_shutdown()`
- `[ ]` Fix BUG-2: Guard `self._screen.capture_text()` in `_ai_tick()`
- `[ ]` Fix BUG-3: Guard `self._ai.query_streaming()` in `_ai_tick()`
- `[ ]` Fix BUG-4: Guard `self._bleep.start_talking()` in `_set_state()`
- `[ ]` Fix BUG-5: Max retries in `query_streaming()` (ai_engine.py)
- `[ ]` Fix BUG-6: Remove unreachable code in `query()` (ai_engine.py)
- `[ ]` Fix ERR-2: Wrap segment parsing in try/except (ai_engine.py)
- `[ ]` Fix ERR-3: Move `show_dialog` to main thread (main.py)
- `[ ]` Fix ERR-4: Temp file cleanup in screen_reader.py
- `[ ]` Create `command_guard.py` (3-tier danger system)

## Phase 2: Architecture Refactoring
- `[ ]` Create `utils.py` (shared utilities)
- `[ ]` Replace duplicates in main.py with utils imports
- `[ ]` Replace duplicates in ai_engine.py with utils imports
- `[ ]` Refactor `_dispatch_response()` into command pattern
- `[ ]` Extract each command into individual handler method
- `[ ]` Integrate `CommandGuard` into dispatch
- `[ ]` Add thread safety lock for `self._state`

## Phase 3: New Commands (13)
- `[ ]` `open_url` — open browser URL
- `[ ]` `open_app` — launch application
- `[ ]` `copy_to_clipboard` — copy text
- `[ ]` `system_info` — CPU/RAM/disk report
- `[ ]` `set_volume` — volume control
- `[ ]` `set_wallpaper` — change wallpaper
- `[ ]` `search_files` — file search by pattern
- `[ ]` `type_text` — simulate keyboard
- `[ ]` `lock_screen` — lock computer
- `[ ]` `shutdown` — shutdown with delay
- `[ ]` `restart` — restart with delay
- `[ ]` `set_reminder` — timed reminder
- `[ ]` `play_sound` — play audio file
- `[ ]` `take_screenshot` — capture screen
- `[ ]` Update ai_engine.py system prompt with new commands
- `[ ]` Add few-shot examples for new commands

## Phase 4: Performance, UX & Polish
- `[ ]` PERF-1: OCR resolution cap (screen_reader.py)
- `[ ]` PERF-2: Lazy-load GIF frames (main.py)
- `[ ]` PERF-3: Cache Font objects by size (main.py)
- `[ ]` PERF-4: Lazy-test screenshot backends (screen_reader.py)
- `[ ]` PERF-5: Remove redundant `import re` in `_draw()` 
- `[ ]` CQ-5: Remove unused `BLEEP_TONES` dict
- `[ ]` UX-2: Keep input enabled during ambient polls
- `[ ]` UX-3: Show subtitle on failed file operations
- `[ ]` UX-4: Abort mechanism (Escape key)
- `[ ]` UX-5: Capture `yesno` dialog results
- `[ ]` Replace bare `except` blocks with logging
- `[ ]` Update `requirements.txt` with version pins
- `[ ]` Code quality cleanup (duplicate imports, magic numbers)

## Verification
- `[ ]` py_compile all files
- `[ ]` Launch test
