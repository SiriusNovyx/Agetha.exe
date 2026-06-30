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


def list_microphones() -> list[tuple[int, str]]:
    try:
        import speech_recognition as sr
        return [(i, name) for i, name in enumerate(sr.Microphone.list_microphone_names())]
    except Exception as exc:
        logger.warning(f"Could not list microphones: {exc}")
        return []


def check_voice_dependencies() -> tuple[bool, str]:
    """Return (ok, message) for Medic_Checker."""
    try:
        import speech_recognition  # noqa: F401
    except ImportError:
        return False, "SpeechRecognition not installed"
    try:
        import pyaudio  # noqa: F401
    except ImportError:
        return False, "PyAudio not installed"
    return True, "ok"


def check_local_stt_dependencies() -> tuple[bool, str]:
    try:
        import faster_whisper  # noqa: F401
        import numpy  # noqa: F401
    except ImportError as exc:
        return False, str(exc)
    return True, "ok"


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
    ):
        self._cb = on_text_callback
        self._device_index = device_index
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
                "Run Medic_Checker or: pip install SpeechRecognition pyaudio"
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

        mic_kwargs: dict = {}
        if self._device_index is not None:
            mic_kwargs["device_index"] = self._device_index

        with sr.Microphone(**mic_kwargs) as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            while not self._stop.is_set():
                try:
                    audio = recognizer.listen(source, timeout=None, phrase_time_limit=30)
                    if self._stop.is_set():
                        break
                    threading.Thread(
                        target=self._recognise,
                        args=(recognizer, audio),
                        daemon=True,
                    ).start()
                except Exception:
                    if not self._stop.is_set():
                        time.sleep(0.5)

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
        except Exception:
            pass
