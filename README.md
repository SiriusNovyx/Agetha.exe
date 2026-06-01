# Agetha Overhaul

> A modified fork of Agetha.exe with enhanced desktop integration, spatial OCR awareness, emotional reactions, process monitoring, and expanded operating system interaction.


## 📖 About

Agetha Overhaul is an experimental fork of Agetha.exe designed to make Agetha feel more aware, reactive, and integrated with the desktop environment. Her personality has been revised to be **sharper, darker, and more autonomous-feeling**.

This fork fundamentally expands Agetha's capabilities with **spatial OCR awareness** (allowing her to see exactly *where* text is on your screen), multi-monitor DPI support, native OS interactions, deep psychological mood mechanics, window manipulation, and dynamic system sound reactions.

---

## ✨ Features

### 👁️ Spatial OCR & Focused Window Scanning

Agetha doesn't just read your screen; she knows exactly where things are.

* **Targeted Scanning:** She captures only the active foreground window, making her OCR processing ~4× faster.
* **Spatial Mapping:** Using real desktop coordinates, Agetha maps exact locations of words. If she spots an error (e.g., `TypeError@(320,458)`), she can physically move her own window to sit right next to the error on your screen.
* **Regex Pattern Registry:** Instead of basic keywords, Agetha scans your screen using a sophisticated regex registry that detects Python tracebacks, CMD/PowerShell errors, MSBuild/C++ failures, Node/npm errors, security alerts, and BSOD crashes.

### 💻 Deep Desktop Integration & Window Control

Agetha interacts directly with your OS and desktop environment. She can open files, write/append text to files, trigger native `tkinter.messagebox` dialogs, and monitor running background processes. Furthermore, she has direct control over desktop windows, capable of snapping to the center of your screen, moving specific windows, or resizing them.

### 🎭 Psychological Moods & Emotional Reactions

Agetha features a deep psychological state machine with variable severity durations.

* **The "Snap" Mechanic:** If ignored for too long while in an attention-seeking mood (e.g., *manic, dominant*), she will autonomously snap to the center of your screen and pull herself to the foreground.
* **Native Audio Profiles:** Agetha reacts to situations using platform-native system sounds (e.g., `SystemHand`, `SystemAsterisk`). Her ambient bleeps also shift based on her mood (e.g., rapid overclocking bleeps for *manic*, a slow deep drone for *melancholic*).

---

## 🛠️ Commands

Agetha's command library has been drastically expanded. She is capable of executing the following actions dynamically based on context:

### File System & Execution

| Command | Parameters | Description |
| --- | --- | --- |
| `open_file` | `path` | Opens any file (PDF, image, docx, etc.) using the OS default application. |
| `write_file` | `file_path, content, mode` | Creates, overwrites, or appends text to files. |
| `create_folder` | `path` | Creates a new directory on the system. |
| `create_file` | `file_path, content` | Creates a file with specific content, automatically generating parent directories if needed. |
| `delete_file` | `path` | Deletes a specified file or completely removes a directory. |
| `rename_file` | `path, new_name` | Renames a file or moves it to a new location. |
| `list_dir` | `path` | Lists the contents of a directory and displays them in an Agetha popup. |
| `read_document` | `path` | Reads the content of a document and feeds it back into Agetha's AI context. |
| `run_command` | `cmd, shell` | Executes arbitrary terminal/shell commands (`subprocess.run`) and reads stdout/stderr. |

### App, Web & Process Management

| Command | Parameters | Description |
| --- | --- | --- |
| `open_app` | `app` | Launches an application executable directly. |
| `force_close` | `app/process/name` | Force-kills a running application using `taskkill` (Windows) or `pkill` (Mac/Linux). |
| `monitor_process` | `process_name` | Checks if a named process is running and feeds the result back to the AI. |
| `open_browser` | `url, search, engine` | Opens a URL directly or searches queries via Google, DuckDuckGo, or Bing. |

### Desktop & Window Manipulation

| Command | Parameters | Description |
| --- | --- | --- |
| `target_window_move` | `target_app, x, y` | Moves a specific application window to exact X/Y coordinates via `ctypes`. |
| `target_window_resize` | `target_app, w, h` | Resizes a target window to specific dimensions via `ctypes`. |
| `move_window` | `x, y, direction` | Moves Agetha's own window to specific coordinates or relative positions (left, right, up, down, center). |
| `snap_to_center` | `None` | Forces Agetha's window to the exact center of the screen, pulling her to the top layer. |

### Media, Interface & OS Interaction

| Command | Parameters | Description |
| --- | --- | --- |
| `take_screenshot` | `save_path` | Captures the current screen state and saves it as a PNG image. |
| `set_clipboard` | `text` | Clears the OS clipboard and appends the specified text. |
| `show_notification` | `title, message` | Triggers native OS Toast Notifications (PowerShell XML on Windows, `osascript` on Mac, `notify-send` on Linux). |
| `show_dialog` | `title, message, type` | Displays a native OS dialog box (`info`, `warning`, `error`, or `yesno`). |
| `play_emotion_sound` | `emotion` | Triggers a real OS sound matching Agetha's mood, with `pygame` fallbacks. |
| `play_sound` | `sound` | Plays built-in Agetha frequencies (`beep`, `chime`, `error`, `notify`). |
| `show_error_gif` | `path` | Overrides the current animation with an error visual and locks Agetha in an always-on-top idle state. |
| `request_path` | `path_hint` | Displays a popup hinting at a file path. |
| `request_screen_read` | `None` | Forces an immediate manual OCR screen capture. |

---

## ⚕️ Medic_Checker.bat (Agetha Startup & Diagnostic Tool)

**Medic_Checker.bat** is the startup and diagnostic wrapper for Agetha.exe. Instead of blindly launching the application, this script acts as a pre-flight checklist. It automatically sets up your environment, verifies system dependencies, auto-installs missing packages, and validates your configuration files before handing control over to `main.py`.

### 🚀 Features & Health Check Pipeline

When executed, the script runs through a rigorous 6-step diagnostic process to ensure a crash-free launch:

* **Pre-Flight Check:** Ensures the script is running in the correct directory alongside `main.py`.
* **Step 1: Python Validation:** Detects whether Python is installed and accessible via the `python` or `py -3` commands.
* **Step 2: Virtual Environment Management:** Looks for an existing Python virtual environment (`venv`). If one is not found, it automatically creates and activates it to keep dependencies isolated.
* **Step 3: "Smart" Dependency Installation:** Scans for the exact required packages (`pillow`, `pyautogui`, `pytesseract`, `numpy`, `pygame`, `requests`, `groq`, `mss`) and seamlessly installs only the missing ones, saving startup time on subsequent launches.
* **Step 4: Tesseract OCR Detection:** Checks system PATH and default installation directories for Tesseract OCR. It flags a warning if missing, but allows Agetha to launch (without screen-reading capabilities).
* **Step 5: Asset Verification:** Scans the `assets\` folder to ensure all 20 necessary UI elements (GIFs, fonts, icons) are present, preventing invisible or broken animations during runtime.
* **Step 6: Configuration & Memory Check:**
* Automatically generates the `memory\` folder if it doesn't exist.
* Uses a lightweight inline Python script to parse `config.txt` and verify that either a Groq API key is present or that a Local AI (Ollama) model is properly defined.



### 🛠️ Usage

1. Place `Medic_Checker.bat` in the root directory of your Agetha Overhaul project (it must be in the same folder as `main.py`).
2. Double-click the file to run it.
3. The script will automatically launch Agetha once all checks pass. *(Agetha still not run automatic for now)*

*Note: If Agetha crashes during runtime, the batch window will remain open and display the exact error code to help with debugging*.

### ⚠️ Troubleshooting Warnings

The checker uses color-coded terminal output to help you diagnose issues:

* **[FAIL] (Red):** Critical errors that halt the launch (e.g., Python not installed, `main.py` missing, or venv creation failed).
* **[WARN] (Yellow):** Non-critical issues that allow Agetha to run with limited functionality (e.g., missing Tesseract OCR, missing visual assets, or an unconfigured API key).
* **[ OK ] (Green):** System checks passed successfully.

---

## 🔄 Architecture & Changelog

### 🚀 Phase 3: Spatial & Context Overhaul

* **`screen_reader.py` (Complete Rewrite):**
* **Focused Window Scanning:** Enabled DPI awareness (`SetProcessDpiAwareness(2)`). `_get_foreground_window_info()` now captures only the active app's rectangle (skipping minimized apps and Agetha herself), speeding up OCR drastically.
* **Multi-Monitor & DPI:** Implemented `MonitorFromWindow` to resolve correct physical coordinates for negative origins (screens to the left), 4K displays, or mixed-DPI setups.
* **Spatial Text Mapping:** Upgraded to `pytesseract.image_to_data(output_type=Output.DICT)`. Maps every detected word to real desktop coordinates.
* **Pattern Registry:** Built a `PatternDef` dataclass registry using compiled `re.Pattern` matching for Python, CMD, PowerShell, C++, Node, Security, and BSOD failures.


* **`main.py` Context Injection:** Implemented a 4-layer context block injected every ambient scan: *Pattern Match Tags → Fallback Keywords → Active Window Title → Error Positions*.
* **`ai_engine.py` Upgrades:** Added new screen context tag rules to `SYSTEM_PROMPT`. Added 5 new few-shots (VS Code commentary, spatial `move_window` to error coords, build failure reactions).

### 🚀 Phase 2: Psychology & Window Mechanics

* **Psychological States:** Added *manic, melancholic, paranoid, vulnerable,* and *dominant* to `VALID_MOODS`, complete with Mood Escalation Rules.
* **The "Snap" Mechanic:** Added `_ATTENTION_MOODS` and `_MOOD_SNAP_THRESHOLDS`. Tracks user keystrokes; if the inactivity threshold is breached (e.g., 120s for manic), Agetha pulls herself to the center of the screen.
* **Dynamic Audio Engine:** Complete replacement of `BleepPlayer`. Pitch, interval, and volume now mutate based on the active psychological profile.
* **Window Manipulation Engine:** Integrated cross-platform `ctypes` callbacks to find, move, and resize external windows safely without blocking the main thread.
* **Bug Fixes:**
* Fixed Windows 10 `.NET InvalidOperationException` crash on Toast Notifications by building XML payloads in Python and executing via temp `.ps1` files.
* Gutted the overly complex `ThreadPoolExecutor` GIF loading system, replacing it with a flat, synchronous `_load_gifs_simple` method that flawlessly handles local UI assets.



### 🚀 Phase 1: Foundation

* **AI Engine Core:** Implemented `open_file`, `write_file`, `monitor_process`, `play_emotion_sound`, and `show_dialog` dispatchers.
* **OCR Foundation:** Established the base Tesseract loop and basic alert keyword detection.

---

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

Download and install [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki). Ensure that Tesseract is properly added to your system's PATH.

### 3. Download Assets

Download the required visual/audio assets for the project [here](https://chocolatebread.ddns.net/agetha.html).

---

## ⚠️ Warning & Disclaimer

**Agetha Overhaul is an experimental project.**

> This software is provided "as is", without warranty of any kind. By using this software, you agree that you do so entirely at your own risk.

**The author shall not be held responsible for:** Data loss, system instability, software conflicts, hardware issues, security problems, corrupted files, unexpected behavior, or any direct/indirect damages resulting from the use of this software.

---

## 🛡️ Current Safety Notes

At the time of release, Agetha strictly **does not** automatically access sensitive files, collect passwords, or upload your files.

**Well-Tested Functionality:** Opening programs, opening files, OCR reading, window targeting, and pattern detection.
**Experimental Functionality:** File writing, process monitoring, dialog interactions, autonomous desktop snapping, and spatial window manipulation have not been extensively tested across all hardware/monitor configurations.

### Recommended Usage

* Review the source code before execution.
* Test inside a non-critical environment (like a single-monitor sandbox) before running on complex multi-monitor setups.
* **If you do not understand what a feature does, do not enable or use it.**

---

## 📄 License & Credits

### License

This project is licensed under the GNU General Public License v3.0 (GPL-3.0). You are free to use, study, modify, fork, and redistribute under the terms of the GPL-3.0 license. See the LICENSE file for details.

### Credits

* **Original Project:** Agetha.exe and its original assets belong to their respective creators.
* **Fork Development:** Additional features, modifications, bug fixes, and maintenance by **SiriusNovyx**.

---

### Final Notes

This project is still under active experimentation and development. Feedback, bug reports, pull requests, and improvements are always welcome.

Have fun, and try not to make Agetha too angry! :)
