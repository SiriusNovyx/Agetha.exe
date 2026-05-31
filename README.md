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
py -3.13 -m pip install pillow pyautogui pytesseract numpy pygame requests groq tkextrafont mss

```
### 2. Install Tesseract OCR
Download and install Tesseract OCR. Ensure that Tesseract is properly added to your system's PATH.
https://github.com/UB-Mannheim/tesseract/wiki
### 3. Download Assets
Download the required visual/audio assets for the project here.
https://chocolatebread.ddns.net/agetha.html
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

---

## Changelog

AI Engine

Added

- "open_file"
- "write_file"
- "monitor_process"
- "play_emotion_sound"
- "show_dialog"

New Methods

- "write_file()"
- "monitor_process()"
- "check_ocr_keywords()"

Improved

- SYSTEM_PROMPT
- Command parsing
- JSON fallback handling
- Few-shot examples
- OCR-trigger awareness

---

## Main Application

Added

- "_play_emotion_sound()"
- Open file dispatcher
- Write file dispatcher
- Process monitor dispatcher
- Native dialog dispatcher

Improved

- OCR context injection
- Cross-platform support
- Background execution
- Non-blocking command handling

---

Screen Reader

Added

- Angry keyword detection system
- Trigger state tracking
- OCR alert integration

New Properties

- "has_angry_trigger"
- "last_angry_keywords"

Improved

- OCR scanning
- Keyword matching
- Context awareness

---

## Requirements

Python

Python 3.13.x is recommended.

Python 3.14 may not be fully compatible with all dependencies.

---

## Install Dependencies

py -3.13 -m pip install pillow pyautogui pytesseract numpy pygame requests groq tkextrafont mss

---

## Install Tesseract OCR

Download and install Tesseract OCR:

https://github.com/UB-Mannheim/tesseract/wiki

Make sure Tesseract is available in your system PATH.

---

## Assets

Download required assets:

https://chocolatebread.ddns.net/agetha.html

---

## Warning & Disclaimer

⚠️ Agetha Overhaul is an experimental project.

This software is provided "as is", without warranty of any kind.

By using this software, you agree that you do so entirely at your own risk.

The author shall not be held responsible for:

- Data loss
- System instability
- Software conflicts
- Hardware issues
- Security problems
- Corrupted files
- Unexpected behavior
- Any direct or indirect damages resulting from the use of this software

---

## Current Safety Notes

At the time of release:

- Agetha does not automatically access sensitive files or folders on its own
- Agetha does not automatically collect passwords or personal information
- Agetha does not automatically upload your files anywhere
- Agetha only performs actions through supported commands and user interaction

The most tested functionality currently is:

- Opening programs
- Opening files
- OCR reading

Other features such as:

- File writing
- Process monitoring
- Dialog interactions
- OCR-triggered reactions
- Future autonomous behaviors

have not been extensively tested across all hardware and software configurations.

Unexpected behavior may occur.

---

## Recommended Usage

Before running Agetha Overhaul:

- Review the source code
- Test inside a non-critical environment
- Avoid running on systems containing irreplaceable data
- Create backups of important files

If you do not understand what a feature does, do not enable or use it.

---

## License

This project is licensed under the GNU General Public License v3.0 (GPL-3.0).

You are free to:

- Use
- Study
- Modify
- Fork
- Redistribute

under the terms of the GPL-3.0 license.

See the LICENSE file for details.

---

## Credits

Original Project

Agetha.exe and its original assets belong to their respective creators.

Fork Development

Additional features, modifications, bug fixes, and maintenance by:

SiriusNovyx

---

Final Notes

This project is still under active experimentation and development.

Feedback, bug reports, pull requests, and improvements are welcome.

Have fun, and try not to make Agetha too angry :)
