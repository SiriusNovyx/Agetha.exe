"""
voice_input.py — Microphone speech-to-text for Agetha (tamsamas-style).

STT backends (config.txt USE_LOCAL_STT):
  no  — Google Speech Recognition (online, via SpeechRecognition)
  yes — faster-whisper tiny.en (offline, local CPU)
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import tkinter as tk
from tkinter import font as tkfont

from app_config import BASE_DIR, get_settings
from utils import logger, native_error_popup

W95_BG = "#c0c0c0"
W95_TITLE_BG = "#000080"
W95_TITLE_FG = "#ffffff"
W95_TEXT = "#000000"
W95_INPUT_BG = "#ffffff"
W95_BTN_BG = "#c0c0c0"
W95_BTN_ACT = "#000080"
W95_BTN_AFG = "#ffffff"
W95_FONT = ("MS Sans Serif", 8)
W95_FONT_BOLD = ("MS Sans Serif", 8, "bold")

_SETTINGS_PATH = BASE_DIR / "memory" / "settings.json"

_whisper_model = None
_whisper_lock = threading.Lock()


def load_mic_settings() -> dict:
    if not _SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_mic_settings(data: dict) -> None:
    try:
        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SETTINGS_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"Microphone settings saved to {_SETTINGS_PATH}")
    except Exception as exc:
        logger.warning(f"Could not save mic settings: {exc}")


def _has_sounddevice() -> bool:
    try:
        import sounddevice  # noqa: F401
        return True
    except ImportError:
        return False


def _has_pyaudio() -> bool:
    try:
        import pyaudio  # noqa: F401
        return True
    except ImportError:
        return False


def _list_input_devices() -> list[tuple[int, str]]:
    """Enumerate input devices via sounddevice, falling back to SpeechRecognition."""
    devices: list[tuple[int, str]] = []
    if _has_sounddevice():
        try:
            import sounddevice as sd
            for i, info in enumerate(sd.query_devices()):
                if info.get("max_input_channels", 0) <= 0:
                    continue
                name = (info.get("name") or f"Device {i}").strip()
                lower = name.lower()
                if "loopback" in lower or "stereo mix" in lower:
                    continue
                devices.append((i, name))
        except Exception as exc:
            logger.warning(f"sounddevice device query failed: {exc}")
    if not devices:
        try:
            import speech_recognition as sr
            devices = [
                (i, name or f"Device {i}")
                for i, name in enumerate(sr.Microphone.list_microphone_names())
            ]
        except Exception as exc:
            logger.warning(f"Could not list microphones: {exc}")
    return devices


def list_microphones() -> list[tuple[int, str]]:
    """Return microphones that can actually be opened (probed)."""
    all_devices = _list_input_devices()
    if not all_devices:
        return []
    working: list[tuple[int, str]] = []
    for idx, name in all_devices:
        ok, _, _ = probe_microphone(idx)
        if ok:
            working.append((idx, name))
    return working or all_devices


def check_voice_dependencies() -> tuple[bool, str]:
    """Return (ok, message) for Medic_Checker."""
    try:
        import speech_recognition  # noqa: F401
    except ImportError:
        return False, "SpeechRecognition not installed"
    if _has_pyaudio() or _has_sounddevice():
        return True, "ok"
    return False, "PyAudio or sounddevice not installed"


def check_local_stt_dependencies() -> tuple[bool, str]:
    try:
        import faster_whisper  # noqa: F401
        import numpy  # noqa: F401
    except ImportError as exc:
        return False, str(exc)
    return True, "ok"


def coerce_device_index(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_mic_open_error(pyaudio_err: Exception, sd_err: Exception) -> str:
    parts = [f"PyAudio: {pyaudio_err}", f"sounddevice: {sd_err}"]
    err_text = " ".join(parts).lower()
    hint = ""
    if "-9999" in err_text or "unanticipated host" in err_text:
        hint = (
            "\n\nWindows may be blocking microphone access.\n"
            "Go to Settings → Privacy & security → Microphone and enable:\n"
            "  • Microphone access\n"
            "  • Let desktop apps access your microphone\n"
            "Then restart Agetha."
        )
    return "Could not open any microphone.\n" + "\n".join(parts) + hint


class _SounddeviceStream:
    """Frame reader backed by a sounddevice InputStream."""

    def __init__(self, sd_stream) -> None:
        self._sd = sd_stream

    def read(self, num_frames: int) -> bytes:
        data, _ = self._sd.read(num_frames)
        return data.tobytes()

    def close(self) -> None:
        pass


class _SounddeviceMic:
    """AudioSource-compatible microphone using sounddevice (PyAudio fallback)."""

    def __init__(self, device_index: int | None = None, chunk_size: int = 1024) -> None:
        import sounddevice as sd

        if device_index is None:
            info = sd.query_devices(kind="input")
            self.device_index = int(info["index"])
        else:
            info = sd.query_devices(device_index)
            self.device_index = device_index
        self.SAMPLE_RATE = int(info["default_samplerate"])
        self.SAMPLE_WIDTH = 2
        self.CHUNK = chunk_size
        self.stream: _SounddeviceStream | None = None
        self._sd_stream = None

    def __enter__(self):
        import sounddevice as sd

        self._sd_stream = sd.InputStream(
            device=self.device_index,
            channels=1,
            samplerate=self.SAMPLE_RATE,
            dtype="int16",
            blocksize=self.CHUNK,
        )
        self._sd_stream.start()
        self.stream = _SounddeviceStream(self._sd_stream)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._sd_stream is not None:
            try:
                self._sd_stream.stop()
            except Exception:
                pass
            try:
                self._sd_stream.close()
            except Exception:
                pass
        self._sd_stream = None
        self.stream = None


def probe_microphone(device_index: int | None = None) -> tuple[bool, str | None, str | None]:
    """Return (ok, backend_name, error_message)."""
    pyaudio_err: str | None = None
    sd_err: str | None = None

    if _has_pyaudio():
        try:
            import speech_recognition as sr
            mic = _open_pyaudio_mic(sr, device_index)
            _close_pyaudio_mic(mic)
            return True, "pyaudio", None
        except OSError as exc:
            pyaudio_err = str(exc)

    if _has_sounddevice():
        try:
            mic = _SounddeviceMic(device_index)
            with mic:
                if mic.stream is not None:
                    mic.stream.read(mic.CHUNK)
            return True, "sounddevice", None
        except Exception as exc:
            sd_err = str(exc)

    if pyaudio_err and sd_err:
        return False, None, f"PyAudio: {pyaudio_err}; sounddevice: {sd_err}"
    if pyaudio_err:
        return False, None, pyaudio_err
    if sd_err:
        return False, None, sd_err
    return False, None, "No audio backend available (install PyAudio or sounddevice)"


def _open_pyaudio_mic(sr, device_index: int | None):
    kwargs: dict = {}
    if device_index is not None:
        kwargs["device_index"] = device_index
    mic = sr.Microphone(**kwargs)
    mic.__enter__()
    if mic.stream is None:
        _close_pyaudio_mic(mic)
        label = f"device_index={device_index}" if device_index is not None else "default device"
        raise OSError(f"PyAudio could not open microphone ({label})")
    return mic


def _close_pyaudio_mic(mic) -> None:
    if mic is None:
        return
    try:
        if mic.stream is not None:
            mic.stream.close()
    except Exception:
        pass
    finally:
        mic.stream = None
        if getattr(mic, "audio", None) is not None:
            try:
                mic.audio.terminate()
            except Exception:
                pass
            mic.audio = None


def _open_microphone(sr, device_index: int | None):
    """Open microphone; PyAudio first, then sounddevice fallback."""
    pyaudio_err: OSError | None = None
    if _has_pyaudio():
        try:
            return _open_pyaudio_mic(sr, device_index)
        except OSError as exc:
            pyaudio_err = exc
            logger.warning(f"[Voice] PyAudio failed ({exc}); trying sounddevice…")

    if _has_sounddevice():
        try:
            mic = _SounddeviceMic(device_index)
            mic.__enter__()
            if mic.stream is None:
                mic.__exit__(None, None, None)
                raise OSError("sounddevice stream not started")
            logger.info(
                f"[Voice] Using sounddevice backend (device_index={mic.device_index})"
            )
            return mic
        except Exception as sd_exc:
            if pyaudio_err is not None:
                raise OSError(_format_mic_open_error(pyaudio_err, sd_exc)) from sd_exc
            raise OSError(f"sounddevice could not open microphone: {sd_exc}") from sd_exc

    if pyaudio_err is not None:
        raise pyaudio_err
    raise OSError("No audio backend available (install PyAudio or sounddevice)")


def _close_microphone(mic) -> None:
    if mic is None:
        return
    if isinstance(mic, _SounddeviceMic):
        mic.__exit__(None, None, None)
        return
    _close_pyaudio_mic(mic)


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            try:
                from faster_whisper import WhisperModel
                logger.info("[STT] Loading faster-whisper model (tiny.en) — first run only…")
                _whisper_model = WhisperModel("tiny.en", device="cpu", compute_type="int8")
                logger.info("[STT] faster-whisper ready.")
            except Exception as exc:
                logger.warning(f"[STT] faster-whisper load failed: {exc}")
                _whisper_model = None
    return _whisper_model


class MicPickerDialog:
    """Win95-style dialog to choose a microphone device."""

    def __init__(self, parent: tk.Tk, mics: list[tuple[int, str]]):
        self.result: int | None = None
        self._mics = mics

        self._win = tk.Toplevel(parent)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._win.configure(bg=W95_BG)
        self._win.resizable(False, False)
        self._drag_x = self._drag_y = 0

        outer = tk.Frame(self._win, bg=W95_BG, relief="raised", bd=2)
        outer.pack(fill="both", expand=True)

        title_bar = tk.Frame(outer, bg=W95_TITLE_BG, height=18)
        title_bar.pack(fill="x", padx=2, pady=(2, 0))
        title_bar.pack_propagate(False)
        tk.Label(
            title_bar, text="Select Microphone",
            bg=W95_TITLE_BG, fg=W95_TITLE_FG,
            font=W95_FONT_BOLD, anchor="w", padx=4,
        ).pack(side="left", fill="y")
        for w in title_bar.winfo_children():
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_motion)
        title_bar.bind("<ButtonPress-1>", self._drag_start)
        title_bar.bind("<B1-Motion>", self._drag_motion)

        body = tk.Frame(outer, bg=W95_BG, padx=12, pady=10)
        body.pack(fill="both", expand=True)
        tk.Label(
            body, text="Choose which microphone Agetha should use:",
            fg=W95_TEXT, bg=W95_BG, font=W95_FONT,
            wraplength=260, justify="left",
        ).pack(anchor="w", pady=(0, 6))

        list_frame = tk.Frame(body, bg=W95_BG, relief="sunken", bd=2)
        list_frame.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical")
        self._listbox = tk.Listbox(
            list_frame, font=W95_FONT, bg=W95_INPUT_BG, fg=W95_TEXT,
            selectbackground=W95_TITLE_BG, selectforeground=W95_TITLE_FG,
            relief="flat", bd=0, height=min(len(mics), 8),
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self._listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self._listbox.pack(side="left", fill="both", expand=True)
        for idx, name in mics:
            self._listbox.insert("end", f"[{idx}]  {name}")
        if mics:
            self._listbox.selection_set(0)

        btn_row = tk.Frame(outer, bg=W95_BG, pady=6)
        btn_row.pack(fill="x")
        tk.Button(
            btn_row, text="OK", font=W95_FONT_BOLD,
            bg=W95_BTN_BG, fg=W95_TEXT, activebackground=W95_BTN_ACT,
            activeforeground=W95_BTN_AFG, relief="raised", bd=2, width=8, pady=2,
            command=self._ok,
        ).pack(side="left", padx=(16, 4))
        tk.Button(
            btn_row, text="Cancel", font=W95_FONT_BOLD,
            bg=W95_BTN_BG, fg=W95_TEXT, activebackground=W95_BTN_ACT,
            activeforeground=W95_BTN_AFG, relief="raised", bd=2, width=8, pady=2,
            command=self._cancel,
        ).pack(side="left", padx=4)

        self._win.update_idletasks()
        px, py = parent.winfo_x(), parent.winfo_y()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        ww, wh = self._win.winfo_width(), self._win.winfo_height()
        x = max(0, px + (pw - ww) // 2)
        y = max(0, py + (ph - wh) // 2)
        self._win.geometry(f"+{x}+{y}")
        self._win.bind("<Return>", lambda _: self._ok())
        self._win.bind("<Escape>", lambda _: self._cancel())

    def _ok(self) -> None:
        sel = self._listbox.curselection()
        if sel:
            self.result = self._mics[sel[0]][0]
        self._win.destroy()

    def _cancel(self) -> None:
        self.result = None
        self._win.destroy()

    def _drag_start(self, e) -> None:
        self._drag_x, self._drag_y = e.x_root, e.y_root

    def _drag_motion(self, e) -> None:
        dx = e.x_root - self._drag_x
        dy = e.y_root - self._drag_y
        self._win.geometry(f"+{self._win.winfo_x() + dx}+{self._win.winfo_y() + dy}")
        self._drag_x, self._drag_y = e.x_root, e.y_root

    def wait(self) -> int | None:
        self._win.wait_window()
        return self.result


class VoiceInput:
    """Continuous microphone listener; fires callback after pause_threshold silence."""

    SILENCE_SECONDS = 1.2

    def __init__(
        self,
        on_text_callback: Callable[[str], None],
        device_index: int | None = None,
        *,
        use_local_stt: bool | None = None,
        on_fatal_error: Callable[[], None] | None = None,
    ):
        self._cb = on_text_callback
        self._device_index = coerce_device_index(device_index)
        self._on_fatal_error = on_fatal_error
        self._use_local_stt = (
            get_settings().use_local_stt if use_local_stt is None else use_local_stt
        )
        self._active = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._sr_ok = False
        self._error: str | None = None

        try:
            import speech_recognition as sr  # noqa: F401
            self._sr_ok = True
        except ImportError:
            self._error = (
                "SpeechRecognition not installed.\n"
                "Run Medic_Checker or: pip install SpeechRecognition pyaudio sounddevice"
            )

    @property
    def available(self) -> bool:
        return self._sr_ok

    @property
    def error(self) -> str | None:
        return self._error

    def start(self) -> None:
        if not self._sr_ok or self._active:
            return
        self._active = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._active = False
        self._stop.set()

    def _run(self) -> None:
        import speech_recognition as sr

        recognizer = sr.Recognizer()
        recognizer.pause_threshold = self.SILENCE_SECONDS
        recognizer.energy_threshold = 300
        recognizer.dynamic_energy_threshold = True

        candidates: list[int | None] = []
        if self._device_index is not None:
            candidates.append(self._device_index)
        candidates.append(None)

        mic = None
        opened_index: int | None = None
        last_err: Exception | None = None
        for idx in candidates:
            if self._stop.is_set():
                return
            try:
                mic = _open_microphone(sr, idx)
                opened_index = idx
                break
            except OSError as exc:
                last_err = exc
                if idx is not None:
                    logger.warning(
                        f"[Voice] Microphone [{idx}] failed ({exc}); trying default device…"
                    )
                continue

        if mic is None:
            msg = str(last_err) if last_err else "Could not open any microphone."
            logger.error(f"[Voice] {msg}")
            self._active = False
            native_error_popup("Agetha — Microphone", msg)
            if self._on_fatal_error:
                try:
                    self._on_fatal_error()
                except Exception as exc:
                    logger.warning(f"[Voice] on_fatal_error callback failed: {exc}")
            return

        if (
            self._device_index is not None
            and opened_index is None
        ):
            logger.info("[Voice] Using default microphone (saved device could not be opened)")

        try:
            recognizer.adjust_for_ambient_noise(mic, duration=0.5)
            while not self._stop.is_set():
                try:
                    audio = recognizer.listen(mic, timeout=5, phrase_time_limit=30)
                    if self._stop.is_set():
                        break
                    threading.Thread(
                        target=self._recognise,
                        args=(recognizer, audio),
                        daemon=True,
                    ).start()
                except sr.WaitTimeoutError:
                    continue
                except Exception as exc:
                    if not self._stop.is_set():
                        logger.warning(f"[Voice] listen loop error: {exc}")
                        time.sleep(0.5)
        finally:
            _close_microphone(mic)

    def _recognise(self, recognizer, audio) -> None:
        try:
            if self._use_local_stt:
                model = _get_whisper_model()
                if model is None:
                    return
                import numpy as np
                raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
                arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                segments, _ = model.transcribe(
                    arr, language="en", beam_size=1,
                    vad_filter=True, vad_parameters={"min_silence_duration_ms": 300},
                )
                text = " ".join(s.text for s in segments).strip()
            else:
                text = recognizer.recognize_google(audio)
            if text:
                logger.info(f"[Voice] Recognised: {text}")
                self._cb(text)
        except Exception as exc:
            logger.warning(f"[Voice] recognition failed: {exc}")
