"""
tts_player.py — Optional retro TTS via pyttsx3 + voice output routing.

TTS is optional: the app runs without pyttsx3 installed.
All public methods are non-raising; failures are logged as warnings.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any

from utils import logger

try:
    import pyttsx3  # type: ignore[import-untyped]
    PYTTSX3_OK = True
except ImportError:
    pyttsx3 = None  # type: ignore[assignment,misc]
    PYTTSX3_OK = False

_VALID_MODES = frozenset({"bleeps_only", "tts_only", "both"})


def _segments_to_text(segments: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        chunk = str(seg.get("text", "")).strip()
        if chunk:
            parts.append(chunk)
    return " ".join(parts)


class TTSPlayer:
    """Queue-based TTS worker; engine init runs on the worker thread."""

    def __init__(
        self,
        rate: int = 165,
        volume: float = 0.8,
        voice_name: str = "",
    ) -> None:
        self._rate = rate
        self._volume = max(0.0, min(1.0, volume))
        self._voice_name = (voice_name or "").strip()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._shutdown = threading.Event()
        self._paused = threading.Event()
        self._engine: Any = None
        self._engine_ready = False
        self._worker: threading.Thread | None = None

        if PYTTSX3_OK:
            self._worker = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker.start()

    @property
    def available(self) -> bool:
        return PYTTSX3_OK and self._engine_ready

    def _init_engine(self) -> None:
        if not PYTTSX3_OK or self._engine is not None:
            return
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", self._rate)
            engine.setProperty("volume", self._volume)
            if self._voice_name:
                needle = self._voice_name.lower()
                for voice in engine.getProperty("voices") or []:
                    vid = str(getattr(voice, "id", "") or "")
                    name = str(getattr(voice, "name", "") or "")
                    if needle in vid.lower() or needle in name.lower():
                        engine.setProperty("voice", voice.id)
                        break
            self._engine = engine
            self._engine_ready = True
        except Exception as exc:
            logger.warning(f"TTSPlayer: engine init failed: {exc}")
            self._engine = None
            self._engine_ready = False

    def _worker_loop(self) -> None:
        self._init_engine()
        while not self._shutdown.is_set():
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                break
            text = item.strip()
            if not text or not self._engine_ready or self._engine is None:
                continue
            try:
                while self._paused.is_set() and not self._shutdown.is_set():
                    time.sleep(0.05)
                if self._shutdown.is_set():
                    continue
                self._engine.say(text)
                self._engine.runAndWait()
            except Exception as exc:
                logger.warning(f"TTSPlayer: speak failed: {exc}")

    def speak_text(self, text: str) -> None:
        try:
            if not text or not text.strip():
                return
            if not PYTTSX3_OK:
                return
            self._queue.put(str(text))
        except Exception as exc:
            logger.warning(f"TTSPlayer.speak_text failed: {exc}")

    def speak_segments(self, segments: list[dict[str, Any]]) -> None:
        try:
            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                chunk = str(seg.get("text", "")).strip()
                if chunk:
                    self.speak_text(chunk)
        except Exception as exc:
            logger.warning(f"TTSPlayer.speak_segments failed: {exc}")

    def pause(self) -> None:
        try:
            self._paused.set()
        except Exception as exc:
            logger.warning(f"TTSPlayer.pause failed: {exc}")

    def resume(self) -> None:
        try:
            self._paused.clear()
        except Exception as exc:
            logger.warning(f"TTSPlayer.resume failed: {exc}")

    def stop(self) -> None:
        try:
            self._shutdown.set()
            try:
                self._queue.put_nowait(None)
            except Exception:
                pass
            if self._engine is not None:
                try:
                    self._engine.stop()
                except Exception:
                    pass
            if self._worker and self._worker.is_alive() and self._worker is not threading.current_thread():
                self._worker.join(timeout=0.5)
        except Exception as exc:
            logger.warning(f"TTSPlayer.stop failed: {exc}")


class VoiceOutputCoordinator:
    """Routes speech audio per VOICE_OUTPUT_MODE: bleeps_only | tts_only | both."""

    def __init__(self, bleep_player: Any, settings: Any) -> None:
        self._bleep = bleep_player
        self._settings = settings
        raw_mode = str(getattr(settings, "voice_output_mode", "bleeps_only")).strip().lower()
        self._mode = raw_mode if raw_mode in _VALID_MODES else "bleeps_only"
        self._tts: TTSPlayer | None = None
        if self._mode in ("tts_only", "both"):
            self._tts = TTSPlayer(
                rate=int(getattr(settings, "tts_rate", 165)),
                volume=float(getattr(settings, "tts_volume", 0.8)),
                voice_name=str(getattr(settings, "tts_voice_name", "")),
            )

    @property
    def mode(self) -> str:
        return self._mode

    def uses_bleeps(self) -> bool:
        return self._mode in ("bleeps_only", "both")

    def uses_tts(self) -> bool:
        return self._mode in ("tts_only", "both") and self._tts is not None

    def start_speech(self, segments: list[dict[str, Any]], mood: str) -> None:
        try:
            use_bleeps = self.uses_bleeps()
            use_tts = self._mode in ("tts_only", "both")

            if use_tts and self._tts is not None and not PYTTSX3_OK:
                logger.warning("VoiceOutputCoordinator: pyttsx3 missing — falling back to bleeps")
                use_bleeps = True
                use_tts = False

            if use_bleeps and self._bleep is not None:
                try:
                    self._bleep.start_talking(tone=mood)
                except Exception as exc:
                    logger.warning(f"VoiceOutputCoordinator: bleep start failed: {exc}")

            # TTS is driven per subtitle segment via speak_segment() for realistic sync.
            if use_tts and self._mode == "tts_only" and self._tts is not None:
                try:
                    self._tts.speak_segments(segments)
                except Exception as exc:
                    logger.warning(f"VoiceOutputCoordinator: TTS batch failed: {exc}")
                    if self._bleep is not None:
                        try:
                            self._bleep.start_talking(tone=mood)
                        except Exception:
                            pass
        except Exception as exc:
            logger.warning(f"VoiceOutputCoordinator.start_speech failed: {exc}")

    def speak_segment(self, text: str) -> None:
        """Queue one subtitle segment for TTS (used in both/tts_only sync)."""
        try:
            if not text or not text.strip():
                return
            if self._mode not in ("tts_only", "both") or self._tts is None:
                return
            self._tts.speak_text(text.strip())
        except Exception as exc:
            logger.warning(f"VoiceOutputCoordinator.speak_segment failed: {exc}")

    def pause(self) -> None:
        try:
            if self.uses_bleeps() and self._bleep is not None:
                self._bleep.pause()
            if self._tts is not None:
                self._tts.pause()
        except Exception as exc:
            logger.warning(f"VoiceOutputCoordinator.pause failed: {exc}")

    def resume(self) -> None:
        try:
            if self.uses_bleeps() and self._bleep is not None:
                self._bleep.resume()
            if self._tts is not None:
                self._tts.resume()
        except Exception as exc:
            logger.warning(f"VoiceOutputCoordinator.resume failed: {exc}")

    def stop_bleeps(self) -> None:
        try:
            if self._bleep is not None:
                self._bleep.stop()
        except Exception as exc:
            logger.warning(f"VoiceOutputCoordinator.stop_bleeps failed: {exc}")

    def stop(self) -> None:
        try:
            self.stop_bleeps()
            if self._tts is not None:
                self._tts.stop()
        except Exception as exc:
            logger.warning(f"VoiceOutputCoordinator.stop failed: {exc}")
