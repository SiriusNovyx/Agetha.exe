# Agetha Overhaul

A modified fork of Agetha.exe with expanded autonomy, desktop integration, OCR awareness, emotional reactions, and system interaction capabilities.

## About

Agetha Overhaul extends the original Agetha experience by adding deeper operating system interaction, enhanced OCR-based awareness, emotion-driven responses, and improved command handling.

Original project copyright belongs to the original author.

Modifications and maintenance by SiriusNovyx.

This project is licensed under the GNU General Public License v3.0 (GPL-3.0). See the LICENSE file for details.

---

## Features

### Enhanced AI Engine

* Open files directly from AI commands
* Create, overwrite, and append text files
* Monitor running processes
* Display native operating system dialog boxes
* Trigger emotion-based system sounds
* Improved command parsing and fallback handling
* Expanded few-shot examples for better reliability

### OCR Awareness

Agetha continuously reads on-screen text and can react to specific trigger phrases.

Examples include:

* Access Denied
* Virus Detected
* Account Suspended
* Security Warning
* Critical Error

When detected, these phrases are injected into the AI context, allowing Agetha to react naturally and emotionally.

### Emotional Audio System

Agetha can now express emotions using native operating system sounds.

Supported platforms:

* Windows
* macOS
* Linux

If platform-specific sounds fail, the system automatically falls back to the built-in pygame audio feedback.

### Process Monitoring

Agetha can check whether applications are running.

Examples:

* Discord
* Chrome
* Steam
* Minecraft
* Any custom executable

Results are automatically fed back into conversation context for natural responses.

### Native Desktop Integration

Supported actions:

* Open files
* Write files
* Show information dialogs
* Show warning dialogs
* Show error dialogs
* Show Yes/No confirmation dialogs

---

## New Commands

| Command            | Description                                                 |
| ------------------ | ----------------------------------------------------------- |
| open_file          | Opens a file using the operating system default application |
| write_file         | Creates, overwrites, or appends to files                    |
| monitor_process    | Checks whether a process is running                         |
| play_emotion_sound | Plays an emotion-based operating system sound               |
| show_dialog        | Displays a native dialog box                                |

---

## Changelog

### AI Engine

Added:

* open_file
* write_file
* monitor_process
* play_emotion_sound
* show_dialog

New methods:

* write_file()
* monitor_process()
* check_ocr_keywords()

Updated:

* SYSTEM_PROMPT
* VALID_COMMANDS
* JSON fallback parser
* Few-shot examples

---

### Main Application

Added:

* _play_emotion_sound()
* open_file dispatcher
* write_file dispatcher
* monitor_process dispatcher
* show_dialog dispatcher

Improved:

* OCR trigger handling
* Cross-platform sound support
* Background-thread execution for non-blocking actions

---

### Screen Reader

Added:

* ANGRY_KEYWORDS trigger system
* has_angry_trigger property
* last_angry_keywords tracking

Improved:

* OCR scanning
* Trigger detection
* Context awareness

---

## Requirements

### Python

Python 3.13.x is recommended.

Python 3.14 may not be fully compatible with all dependencies.

### Python Packages

Install dependencies:

```bash
py -3.13 -m pip install pillow pyautogui pytesseract numpy pygame requests groq tkextrafont mss
```

### Tesseract OCR

Download and install Tesseract OCR:

https://github.com/UB-Mannheim/tesseract/wiki

After installation, ensure Tesseract is available in your system PATH.

---

## Assets

Download required assets:

https://chocolatebread.ddns.net/agetha.html

---

## Credits

Original Agetha.exe project and assets belong to their respective creators.

Fork modifications, additional features, and maintenance by SiriusNovyx.

---

## License

This project is distributed under the GNU General Public License v3.0.

You are free to:

* Use
* Study
* Modify
* Fork
* Redistribute

under the terms of the GPL-3.0 license.

See the LICENSE file for full details.

---

Have fun, and don't make Agetha too angry. :)
