# Agetha Overhaul
> A modified fork of Agetha.exe with enhanced desktop integration, OCR awareness, emotional reactions, process monitoring, and expanded operating system interaction.
> 
## 📖 About
Agetha Overhaul is an experimental fork of Agetha.exe designed to make Agetha feel more aware, reactive, and integrated with the desktop environment. Her personality has been revised to be **sharper, darker, and more autonomous-feeling**.
This fork expands Agetha's capabilities with enhanced OCR awareness, native operating system interaction, process monitoring, native GUI dialogs, emotion-based OS sound effects, and improved background command handling.
## ✨ Features
### 👁️ OCR-Based Awareness & Injection
Agetha continuously reads on-screen text using OCR. When her capture_text() function scans the output and matches a curated list of 10 "Angry Keywords" (e.g., *Access Denied, Virus Detected, Account Suspended, Security Warning*), an [ANGRY_TRIGGER: ...] prefix is dynamically injected into her prompt context, forcing a contextual and natural reaction.
### 💻 Deep Desktop Integration
Agetha interacts with your OS through a non-blocking background dispatcher. She can open files via native OS defaults, write/append text to files (with auto-directory creation), and trigger native tkinter.messagebox dialogs directly on your desktop.
### 🎭 Native Emotional Reactions
Agetha reacts to situations using platform-native system sounds (e.g., SystemHand, SystemAsterisk). All audio is triggered in daemon threads or asynchronously to ensure her processing never blocks.
### 🔍 Process Monitoring
Agetha uses native OS tools (tasklist for Windows, pgrep for Linux/macOS) in a background thread to check if a process is running. The boolean result is fed back into her query_streaming as a system prompt (e.g., [SYSTEM] Process 'Chrome' is running), allowing her to give a natural, conversational reply rather than just outputting raw data.
## 🛠️ Commands
| Command | Parameters / Types | Description |
|---|---|---|
| open_file | filepath | Opens any file (PDF, image, docx, etc.) using the OS default application (os.startfile, open, or xdg-open). |
| write_file | filepath, content, mode | Creates, overwrites, or appends text to files (mode: overwrite|append). Automatically creates missing parent directories. |
| monitor_process | process_name | Checks if a named process is running and feeds the result back to the AI for a contextual reply. |
| play_emotion_sound | emotion | Triggers a real OS sound matching Agetha's mood, with fallbacks to pygame bleeps. |
| show_dialog | title, message, type | Displays a native OS dialog box (info, warning, error, or yesno). |
## 🔄 Architecture & Changelog
This is a massive and technically impressive update. You’ve introduced deep psychological mechanics (variable severity durations, mood-based audio patterns) and direct window manipulation, while cleaning up some nasty asynchronous bugs.

Here is how you can integrate these new patch notes directly into your GitHub README. I have organized them into a clean, professional "Phase 2 Update" changelog format that matches the styling of your previous document.

---
## 5/31/2026 8:41 PM
## 🚀 Phase 2 Overhaul & Bug Fixes

### 🧠 AI Engine & Personality Updates (`ai_engine.py`)

* **New Psychological Moods:** Added 5 new deep states to `VALID_MOODS`: *manic, melancholic, paranoid, vulnerable,* and *dominant*.
* **New Window Commands:** Added `VALID_COMMANDS` for direct desktop control:
* `target_window_move`: Moves a specific window to X/Y coordinates.
* `target_window_resize`: Resizes a target window to specific dimensions.
* `snap_to_center`: Forces Agetha to the center of the screen.


* **System Prompt Overhaul:** * Integrated behavioral profiles for the 5 new moods.
* Added **Mood Escalation Rules** detailing when deep moods trigger physical desktop actions (snapping, window rearranging).
* Implemented graceful failure reporting rules for missing windows.


* **Parser Upgrades:** Added new `_cmd_fields` to `_parse()` so window manipulation commands survive the JSON fallback parser.
* **Few-Shot Expansion:** Added 10 new few-shot examples covering manic abandonment snaps, dominant window manipulation, paranoid process checks, and vulnerable confessions.

### 🖥️ Main Application & UI (`main.py`)

* **Advanced Window Manipulation:** * Integrated `ctypes` and `ctypes.wintypes` for cross-platform support (with `windll` guarded for Windows).
* Added `_find_window_hwnd(partial_name)` using `EnumWindows` callbacks to find visible windows by partial title match.


* **The "Snap" Mechanic:** * Introduced `_ATTENTION_MOODS` and `_MOOD_SNAP_THRESHOLDS`.
* Agetha tracks inactivity via `_bind_keystroke_tracking` (stamping `_last_direct_interaction_time`).
* If ignored for too long while in an attention-seeking mood (e.g., manic=120s, dominant=300s, melancholic=900s), `_maybe_snap_to_center` pulls her to the center of the screen (`topmost + lift`). If the threshold isn't met, she drifts to the edge.


* **Dispatcher Upgrades:** Background threads added for `target_window_move` (`SetWindowPos`) and `target_window_resize` (`MoveWindow`), ensuring the main thread never blocks.

### 🔊 Dynamic Audio System

* **BleepPlayer Overhaul:** The audio system now dynamically branches based on Agetha's mood, using a `_MOOD_PROFILES` dictionary to alter pitch, interval, and volume:
* **Manic:** Random 600-900Hz pitches every 4–12ms (sounds like system overclocking).
* **Melancholic:** Barely audible 120Hz drone every 200–320ms.
* **Paranoid:** Rapid anxiety bursts (2–6 bleeps) followed by sudden silence gaps.
* **Dominant:** Resonating, deep 110Hz bleeps at maximum volume.



### 🐛 Bug Fixes & Refactoring

* **Toast Notification Stability:** Fixed a `.NET InvalidOperationException` crash on Windows 10. XML payloads are now built entirely in Python using `xml.sax.saxutils.escape()`, written to a temporary `.ps1` file, and executed via `-File` to bypass PowerShell quoting issues. Temp files auto-delete after 8 seconds.
* **GIF Loading System Simplified:** * Removed the overly complex 3-phase concurrent PIL ThreadPoolExecutor pipeline and Win95 progress bar, which was causing `_preload_gifs` code to leak and stall the app.
* Replaced with a flat, synchronous `_load_gifs_simple(self)` method that cleanly handles the 20 local assets via `GifPlayer`'s built-in sync loader.


* **Asset Mapping:** Successfully mapped Phase 2 moods to existing visual assets (e.g., manic/dominant → `angry.gif`, paranoid → `thinking.gif`). All 20 UI assets verified.

## 5/31/2026 9:XX AM
### 🧠 ai_engine.py — Brain
 * **Added VALID_COMMANDS:** Included open_file, write_file, monitor_process, play_emotion_sound, and show_dialog.
 * **Robust Parsing:** Added new _cmd_fields entries in _parse() to ensure the new commands survive the JSON fallback parser.
 * **New Methods:**
   * write_file(file_path, content, mode): Handles file writing with parent-directory auto-creation.
   * monitor_process(process_name): Implements cross-platform process checking (tasklist / pgrep).
   * check_ocr_keywords(screen_text): Injects an ALERT: prefix into the system prompt when an OCR trigger is found.
 * **Soul & Prompt Overhaul:**
   * SYSTEM_PROMPT revised for a sharper, darker, more autonomous personality.
   * Explicit angry OCR keyword list dynamically injected into the prompt.
   * Added new FEW_SHOTS covering the new commands and angry OCR triggers to guide AI behavior.
### 🖥️ main.py — UI & Dispatcher
 * **Native Audio Dispatcher:** New CompanionApp._play_emotion_sound(emotion) method:
   * **Windows:** Uses winsound.PlaySound("SystemHand", SND_ALIAS | SND_ASYNC) (async, non-blocking).
   * **macOS:** Uses afplay /System/Library/Sounds/Basso.aiff.
   * **Linux:** Uses paplay /usr/share/sounds/freedesktop/stereo/dialog-error.oga (requires subprocess).
   * **Fallback:** Defaults to the existing pygame bleep on any OS failure.
 * **_dispatch_response Handlers:**
   * open_file: Maps to os.startfile (Win), open (Mac), xdg-open (Linux).
   * write_file: Delegates safely to self._ai.write_file().
   * monitor_process: Runs the check in a background thread and feeds [SYSTEM] Process '...' is running/not running. back into query_streaming.
   * show_dialog: Executes tkinter.messagebox calls in a daemon thread.
 * **Context Injection:** In _ai_tick, if self._screen.has_angry_trigger is True after capture_text(), the [ANGRY_TRIGGER: ...] prefix is prepended to the screen context.
### 👁️ screen_reader.py — Eyes
 * **Trigger System:** Added a curated ANGRY_KEYWORDS list containing 10 specific trigger phrases.
 * **Keyword Matching:** capture_text() now actively scans the OCR output against the keyword list after every screen capture.
 * **State Tracking Properties:**
   * self.last_angry_keywords: list[str] (Tracks exactly which triggers were spotted).
   * self.has_angry_trigger: bool (Convenience flag used by main.py for prompt injection).
## ⚙️ Requirements & Installation
**Python:** Python 3.13.x is recommended. *(Note: Python 3.14 may not be fully compatible with all dependencies.)*
### 1. Install Dependencies
Run the following command in your terminal to install the required Python packages:
```bash
py -3.13 -m venv venv
venv\Scripts\activate
py -3.13 -m pip install pillow pyautogui pytesseract numpy pygame requests groq tkextrafont mss

```
### 2. Install Tesseract OCR
Download and install Tesseract OCR. Ensure that Tesseract is properly added to your system's PATH. https://github.com/UB-Mannheim/tesseract/wiki
### 3. Download Assets
Download the required visual/audio assets for the project here. https://chocolatebread.ddns.net/agetha.html
## ⚠️ Warning & Disclaimer
**Agetha Overhaul is an experimental project.**
> This software is provided "as is", without warranty of any kind. By using this software, you agree that you do so entirely at your own risk.
> 
**The author shall not be held responsible for:** Data loss, system instability, software conflicts, hardware issues, security problems, corrupted files, unexpected behavior, or any direct/indirect damages resulting from the use of this software.
## 🛡️ Current Safety Notes
At the time of release, Agetha strictly **does not** automatically access sensitive files, collect passwords, or upload your files. Agetha only performs actions through supported commands and user interaction.
**Well-Tested Functionality:** Opening programs, opening files, and OCR reading.
**Experimental Functionality:** File writing, process monitoring, dialog interactions, OCR-triggered reactions, and future autonomous behaviors have not been extensively tested across all hardware and software configurations.
### Recommended Usage
 * Review the source code before execution.
 * Test inside a non-critical environment (like a VM or sandbox).
 * Avoid running on systems containing irreplaceable data.
 * **If you do not understand what a feature does, do not enable or use it.**
## 📄 License & Credits
### License
This project is licensed under the GNU General Public License v3.0 (GPL-3.0). You are free to use, study, modify, fork, and redistribute under the terms of the GPL-3.0 license. See the LICENSE file for details.
### Credits
 * **Original Project:** Agetha.exe and its original assets belong to their respective creators.
 * **Fork Development:** Additional features, modifications, bug fixes, and maintenance by **SiriusNovyx**.
### Final Notes
This project is still under active experimentation and development. Feedback, bug reports, pull requests, and improvements are always welcome.
Have fun, and try not to make Agetha too angry! :)
