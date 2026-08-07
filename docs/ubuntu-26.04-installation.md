# Installing Agetha on Ubuntu 26.04 LTS

This guide installs Agetha as a desktop application on Ubuntu 26.04 LTS. The
Windows launchers (`Medic_Checker.bat`, `Medic_Checker.ps1`, and
`Run_Agetha_Admin.ps1`) do not run on Linux; launch Agetha directly with Python.

Ubuntu Desktop or another graphical Linux desktop is required. A headless
Ubuntu Server installation needs a separately configured X11 or Wayland display
and is not covered here.

## 1. Install native packages

Open a terminal in the logged-in desktop session and run:

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-dev python3-tk \
  build-essential \
  portaudio19-dev libasound2-dev \
  tesseract-ocr tesseract-ocr-eng \
  xdotool wmctrl xclip \
  gnome-screenshot xauth x11-utils
```

These packages provide the Python virtual-environment and Tkinter support,
native build headers, optional microphone support, Tesseract OCR, and Linux
desktop/capture helpers.

For Thai OCR, also install:

```bash
sudo apt install -y tesseract-ocr-tha
```

Then set the following in `config.txt`:

```ini
OCR_LANGUAGES = eng+tha
```

## 2. Create a Linux virtual environment

Change to the extracted or cloned project directory:

```bash
cd ~/Downloads/Agetha.exe-agent-release-v5.5.1
```

Adjust that path if the project is stored elsewhere. Do not reuse a `venv/`
folder copied from Windows; virtual environments are platform-specific.

Ubuntu 26.04 uses Python 3.14 by default. Agetha supports Python 3.10 through
3.14, but Python 3.13 is currently recommended when every optional package is
enabled.

Using Ubuntu's default Python:

```bash
python3 -m venv .venv-linux
source .venv-linux/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

If Python 3.14 encounters an optional binary-package compatibility problem,
install and use Python 3.13 instead:

```bash
sudo apt install -y \
  python3.13 python3.13-venv python3.13-dev python3.13-tk

python3.13 -m venv .venv-linux313
source .venv-linux313/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

## 3. Configure an AI provider

Create `.env` only when it does not already exist:

```bash
test -f .env || cp .env.example .env
nano .env
```

For the default Groq backend, add at least one key:

```dotenv
GROQ_API_KEY_1=gsk_your_actual_key
```

OpenRouter and local Ollama are alternatives. Match the selected provider with
`ENABLE_GROQ`, `ENABLE_OPENROUTER`, and `USE_LOCAL_AI` in `config.txt`. Never put
API keys in `config.txt`, commit `.env`, or paste real keys into logs.

Confirm that the separately supplied `assets/` directory is present before
launching.

## 4. Launch Agetha

With the virtual environment activated:

```bash
python main.py
```

Or launch without activating it:

```bash
.venv-linux/bin/python main.py
```

For a Python 3.13 environment, use:

```bash
.venv-linux313/bin/python main.py
```

Run Agetha as the logged-in desktop user. Do not use `sudo`: it gives the
application unnecessary system privileges and commonly prevents access to the
user's graphical display.

## Troubleshooting

### PyAutoGUI cannot connect to display `:0`

A failure ending with the following text is an X11/XWayland authorization
problem, not a pygame problem:

```text
Xlib.error.DisplayConnectionError: Can't connect to display ":0":
Authorization required, but no authorization protocol specified
```

First, open a terminal from inside the Ubuntu desktop and inspect the inherited
session variables:

```bash
echo "user=$USER display=$DISPLAY xauth=$XAUTHORITY session=$XDG_SESSION_TYPE"
xdpyinfo >/dev/null && echo "Display access works"
```

Do not manually force `DISPLAY=:0`. If `XAUTHORITY` is empty under GNOME on
Wayland, locate the current XWayland authority file and retry:

```bash
export XAUTHORITY="$(find "/run/user/$(id -u)" -maxdepth 1 \
  -name '.mutter-Xwaylandauth.*' -print -quit)"
python main.py
```

If `xdpyinfo` works but Python Xlib still rejects the connection, grant access
only to the current local user:

```bash
xhost +SI:localuser:"$USER"
python main.py
```

Do not use unrestricted `xhost +`, which permits any client to connect to the X
server. If the error persists, log out and back in, then launch Agetha from a new
desktop terminal rather than SSH, a TTY, a root shell, or a system service.

Some releases catch only `ImportError` around the optional PyAutoGUI import.
That does not catch Python Xlib's `DisplayConnectionError`, so the optional
integration can prevent the entire application from starting. Open
`agetha/platform/screen_reader.py` in an editor:

```bash
nano agetha/platform/screen_reader.py
```

Find the PyAutoGUI import block and change it to:

```python
try:
    import pyautogui
    PYAUTOGUI_OK = True
except Exception as exc:
    pyautogui = None
    PYAUTOGUI_OK = False
    logger.warning("pyautogui unavailable: %s", exc)
```

This is Python source that belongs in `screen_reader.py`; do not paste it
directly at the Bash prompt. Save the file and run `python main.py` again. Agetha
will then start without PyAutoGUI, while supported alternative screenshot
backends remain available.

### Wayland feature limitations

The Agetha window and core chat can run under Wayland through XWayland. Depending
on the compositor and its permission policy, focused-window discovery,
screenshots, synthetic mouse/keyboard input, and external window positioning may
be limited. `gnome-screenshot`, `grim`, or `spectacle` can provide capture
fallbacks when available.

To run without OCR monitoring, set:

```ini
ENABLE_SCREEN_READER = no
```

This disables screen monitoring after startup. It does not repair missing
graphical-session authorization needed by other desktop automation libraries.

### Tesseract is installed but OCR fails

Check the native executable and installed languages:

```bash
tesseract --version
tesseract --list-langs
```

Leave `TESSERACT_PATH` empty on Ubuntu when `tesseract` is available on `PATH`.
Every language named by `OCR_LANGUAGES` must appear in `tesseract --list-langs`.

### Microphone or PyAudio installation fails

Confirm the PortAudio headers are installed, reactivate the environment, and
retry the Python dependencies:

```bash
sudo apt install -y portaudio19-dev libasound2-dev
source .venv-linux313/bin/activate
python -m pip install -r requirements.txt
```

Voice input is optional and can remain disabled with `ENABLE_VOICE = no`.

## Linux-specific limitations

- Windows Settings, theme, Recycle Bin, toast, and Startup-folder integrations
  are unavailable.
- The PowerShell Medic Checker does not install, validate, update, or launch the
  Linux environment.
- External window geometry control is more limited than on Windows and depends
  on `wmctrl`, the window manager, and the display protocol.
- File drag-and-drop support is primarily maintained for Windows; basic startup
  must not depend on it.
