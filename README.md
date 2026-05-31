Agetha Overhaul

«A modified fork of Agetha.exe with enhanced desktop integration, OCR awareness, emotional reactions, process monitoring, and expanded operating system interaction.»

"License" (https://img.shields.io/badge/License-GPLv3-blue.svg)
"Python" (https://img.shields.io/badge/Python-3.13-blue.svg)
"Status" (https://img.shields.io/badge/Status-Experimental-orange.svg)

---

About

Agetha Overhaul is an experimental fork of Agetha.exe designed to make Agetha feel more aware, reactive, and integrated with the desktop environment.

This fork expands Agetha's capabilities with:

- Enhanced OCR awareness
- Operating system interaction
- Process monitoring
- Native dialog support
- Emotion-based sound effects
- Improved command handling
- More autonomous-feeling behavior

---

Features

OCR-Based Awareness

Agetha continuously reads on-screen text using OCR and can react to specific trigger phrases.

Examples:

- Access Denied
- Virus Detected
- Account Suspended
- Security Warning
- Critical Error

Detected keywords are automatically injected into Agetha's context, allowing more natural emotional responses.

---

Desktop Integration

Agetha can interact with your operating system through supported commands.

Current capabilities include:

- Opening files
- Launching applications
- Writing text files
- Monitoring processes
- Displaying native dialogs
- Playing operating system sounds

---

Emotional Reactions

Agetha can react to situations using platform-native sound effects.

Supported platforms:

- Windows
- Linux
- macOS

Fallback audio is provided through pygame when native sounds are unavailable.

---

Process Monitoring

Agetha can check whether a process is currently running.

Examples:

- Chrome
- Discord
- Steam
- Minecraft
- Custom applications

Results are fed back into the AI conversation for contextual responses.

---

Commands

Command| Description
"open_file"| Opens a file using the default operating system application
"write_file"| Creates, overwrites, or appends text to files
"monitor_process"| Checks whether a process is currently running
"play_emotion_sound"| Plays a native operating system sound
"show_dialog"| Displays a native dialog window

---

Changelog

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

Main Application

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

Requirements

Python

Python 3.13.x is recommended.

Python 3.14 may not be fully compatible with all dependencies.

---

Install Dependencies

py -3.13 -m pip install pillow pyautogui pytesseract numpy pygame requests groq tkextrafont mss

---

Install Tesseract OCR

Download and install Tesseract OCR:

https://github.com/UB-Mannheim/tesseract/wiki

Make sure Tesseract is available in your system PATH.

---

Assets

Download required assets:

https://chocolatebread.ddns.net/agetha.html

---

Warning & Disclaimer

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

Current Safety Notes

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

Recommended Usage

Before running Agetha Overhaul:

- Review the source code
- Test inside a non-critical environment
- Avoid running on systems containing irreplaceable data
- Create backups of important files

If you do not understand what a feature does, do not enable or use it.

---

License

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

Credits

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
