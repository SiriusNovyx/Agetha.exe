# Linux desktop support

Agetha uses native window-manager-managed Tk windows on Linux. Windows keeps
the custom borderless Win95 chrome behavior. macOS remains unsupported.

| Feature | Ubuntu Xorg | GNOME Wayland |
|---|---|---|
| Chat/UI | Supported | Supported |
| Settings/toggles | Supported | Supported |
| Minimize/restore | Supported | Supported |
| Automatic OCR | Supported | Capability-dependent / may be disabled |
| Explicit screenshot | Supported | Portal/compositor-tool dependent |
| Active-window metadata | Supported with X11 tools | Limited by the compositor |
| Global window control | X11-dependent | Restricted |

## Session and capture behavior

At startup, the screen reader reports a concise capability line without
connecting to X11 during module import or exposing authority data. For example:

```text
[Agetha] INFO: [Linux] session=wayland x11_bridge=yes screenshot_backend=unavailable automatic_ocr=no
```

On Xorg, Agetha probes optional backends lazily and caches the first backend
that actually returns a valid positive-size image. The order is MSS, Pillow
ImageGrab, `scrot`, then PyAutoGUI. A real backend failure removes that backend
from the session candidate set so automatic polling does not spawn it
repeatedly.

On Wayland, `DISPLAY` may describe an XWayland bridge; it does not grant
unrestricted desktop capture. Agetha therefore does not run
`gnome-screenshot`, PyAutoGUI, or X11 fallbacks for automatic background OCR.
If a compositor-native `grim` or `spectacle` installation is detected, it is
reserved for explicit capture. Agetha does not currently implement a persistent
`xdg-desktop-portal` session, so GNOME Wayland normally reports capture as
unavailable and continues without OCR. Chat, settings, and window controls stay
available.

Xorg is currently the recommended Ubuntu session for full automatic OCR.
Wayland GUI support must not be confused with unrestricted Wayland capture.

## Safe setup and diagnostics

Run Agetha as the signed-in desktop user, not with `sudo`. Never use `xhost +`,
and do not copy or hardcode a `.mutter-Xwaylandauth.*` filename. GNOME creates a
session-specific authority path which changes between logins. Agetha only
records whether `XAUTHORITY` is readable; it does not log, copy, or modify it.

Inspect the current session without exposing authentication material:

```bash
printf 'session=%s display=%s wayland=%s\n' \
  "${XDG_SESSION_TYPE:-unknown}" \
  "${DISPLAY:+present}" \
  "${WAYLAND_DISPLAY:+present}"
```

Test Tesseract independently:

```bash
tesseract --version
tesseract --list-langs
```

The selected screenshot backend appears in `[ScreenReader]` log lines. To
disable screen OCR without disabling chat, set:

```ini
ENABLE_SCREEN_READER = no
```

`ENABLE_AMBIENT_POLLS = no` additionally disables ambient AI polling; direct
chat remains available.

## Capture safety

Every focused capture rejects minimized, unmapped, malformed, zero-size, and
fully off-screen targets. Partially off-screen rectangles are clipped to the
virtual desktop and validated again before MSS, Pillow, PyAutoGUI, an external
command, or OCR receives them. A skipped capture clears stale OCR coordinates
and metadata, so another window's text is not reused as current context.

External screenshot commands use argument lists, timeouts, private temporary
files, positive image-dimension checks, and cleanup. Their stdout/stderr is
captured rather than copied into normal logs.

## Validation boundary

GitHub Actions runs a managed Tk lifecycle and MSS capture smoke under Xvfb and
Openbox, plus mocked Wayland capability tests. Xvfb is X11, not Wayland. Real
GNOME Wayland permission and portal behavior must still be validated manually
on Ubuntu hardware and must not be inferred from CI.
