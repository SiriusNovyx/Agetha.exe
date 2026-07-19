"""
tts_player.py — Optional TTS (pyttsx3 / edge_tts / kokoro) + voice output routing.

TTS is optional: the app runs without any TTS package installed.
All public methods are non-raising; failures are logged as warnings.
"""

from __future__ import annotations

import os
import queue
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any

from agetha.utils import logger

try:
    import pyttsx3  # type: ignore[import-untyped]
    PYTTSX3_OK = True
except ImportError:
    pyttsx3 = None  # type: ignore[assignment,misc]
    PYTTSX3_OK = False

try:
    import edge_tts  # type: ignore[import-untyped]
    EDGE_TTS_OK = True
except ImportError:
    edge_tts = None  # type: ignore[assignment,misc]
    EDGE_TTS_OK = False

try:
    from kokoro import KPipeline  # type: ignore[import-untyped]
    KOKORO_OK = True
except ImportError:
    KPipeline = None  # type: ignore[assignment,misc]
    KOKORO_OK = False

_VALID_MODES = frozenset({"bleeps_only", "tts_only", "both"})
_VALID_ENGINES = frozenset({"pyttsx3", "edge_tts", "kokoro"})

_DEFAULT_EDGE_VOICE = "en-US-AvaNeural"
_DEFAULT_KOKORO_VOICE = "af_heart"
_KOKORO_SAMPLE_RATE = 24000


def _segments_to_text(segments: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        chunk = str(seg.get("text", "")).strip()
        if chunk:
            parts.append(chunk)
    return " ".join(parts)


def _normalize_engine(engine: str) -> str:
    raw = (engine or "pyttsx3").strip().lower()
    return raw if raw in _VALID_ENGINES else "pyttsx3"


def _engine_package_ok(engine: str) -> bool:
    name = _normalize_engine(engine)
    if name == "edge_tts":
        return EDGE_TTS_OK
    if name == "kokoro":
        return KOKORO_OK
    return PYTTSX3_OK


def _edge_rate_str(rate: int) -> str:
    pct = int((rate - 165) / 165 * 100)
    pct = max(-50, min(100, pct))
    return f"{pct:+d}%"


def _edge_volume_str(volume: float) -> str:
    pct = int((max(0.0, min(1.0, volume)) - 1.0) * 100)
    pct = max(-50, min(50, pct))
    return f"{pct:+d}%"


def _kokoro_speed(rate: int) -> float:
    return max(0.5, min(2.0, float(rate) / 165.0))


def _resolve_edge_voice(voice_name: str) -> str:
    name = (voice_name or "").strip()
    if not name:
        return _DEFAULT_EDGE_VOICE
    lower = name.lower()
    if "neural" in lower or name.count("-") >= 2:
        return name
    logger.warning(
        f"TTSPlayer: TTS_VOICE_NAME={name!r} is not an edge-tts id; "
        f"using {_DEFAULT_EDGE_VOICE}"
    )
    return _DEFAULT_EDGE_VOICE


def _resolve_kokoro_voice(voice_name: str) -> str:
    name = (voice_name or "").strip()
    if name and "_" in name:
        return name
    if name:
        logger.warning(
            f"TTSPlayer: TTS_VOICE_NAME={name!r} is not a kokoro voice id; "
            f"using {_DEFAULT_KOKORO_VOICE}"
        )
    return _DEFAULT_KOKORO_VOICE


def _play_audio_file(path: str, stop_event: threading.Event) -> None:
    """Play an audio file via pygame mixer (already used by BleepPlayer)."""
    try:
        import pygame
    except ImportError as exc:
        raise RuntimeError("pygame required for edge_tts/kokoro playback") from exc

    if not pygame.mixer.get_init():
        pygame.mixer.init()
    sound = pygame.mixer.Sound(path)
    channel = sound.play()
    if channel is None:
        return
    while channel.get_busy() and not stop_event.is_set():
        time.sleep(0.05)
    if stop_event.is_set():
        try:
            channel.stop()
        except Exception:
            pass


def _write_wav_int16(path: Path, samples: Any, sample_rate: int) -> None:
    import numpy as np

    arr = np.asarray(samples, dtype=np.float32).reshape(-1)
    clipped = np.clip(arr, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


class TTSPlayer:
    """Queue-based TTS worker; engine init runs on the worker thread."""

    def __init__(
        self,
        rate: int = 165,
        volume: float = 0.8,
        voice_name: str = "",
        engine: str = "pyttsx3",
    ) -> None:
        self._engine_name = _normalize_engine(engine)
        self._rate = rate
        self._volume = max(0.0, min(1.0, volume))
        self._voice_name = (voice_name or "").strip()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._shutdown = threading.Event()
        self._paused = threading.Event()
        self._engine: Any = None
        self._engine_ready = False
        self._worker: threading.Thread | None = None
        self._package_ok = _engine_package_ok(self._engine_name)

        if self._package_ok:
            self._worker = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker.start()

    @property
    def engine_name(self) -> str:
        return self._engine_name

    @property
    def package_ok(self) -> bool:
        return self._package_ok

    @property
    def available(self) -> bool:
        return self._package_ok and self._engine_ready

    def _init_engine(self) -> None:
        if self._engine is not None:
            return
        try:
            if self._engine_name == "pyttsx3":
                self._init_pyttsx3()
            elif self._engine_name == "edge_tts":
                self._init_edge_tts()
            elif self._engine_name == "kokoro":
                self._init_kokoro()
            else:
                self._engine_ready = False
        except Exception as exc:
            logger.warning(f"TTSPlayer: engine init failed ({self._engine_name}): {exc}")
            self._engine = None
            self._engine_ready = False

    def _init_pyttsx3(self) -> None:
        if not PYTTSX3_OK or pyttsx3 is None:
            return
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

    def _init_edge_tts(self) -> None:
        if not EDGE_TTS_OK or edge_tts is None:
            return
        # No persistent client — mark ready and synthesize per utterance.
        self._engine = {
            "voice": _resolve_edge_voice(self._voice_name),
            "rate": _edge_rate_str(self._rate),
            "volume": _edge_volume_str(self._volume),
        }
        self._engine_ready = True

    def _init_kokoro(self) -> None:
        if not KOKORO_OK or KPipeline is None:
            return
        voice = _resolve_kokoro_voice(self._voice_name)
        lang = "b" if voice.startswith(("bf_", "bm_")) else "a"
        pipeline = KPipeline(lang_code=lang)
        self._engine = {
            "pipeline": pipeline,
            "voice": voice,
            "speed": _kokoro_speed(self._rate),
        }
        self._engine_ready = True

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
                if self._engine_name == "pyttsx3":
                    self._speak_pyttsx3(text)
                elif self._engine_name == "edge_tts":
                    self._speak_edge_tts(text)
                elif self._engine_name == "kokoro":
                    self._speak_kokoro(text)
            except Exception as exc:
                logger.warning(f"TTSPlayer: speak failed ({self._engine_name}): {exc}")

    def _speak_pyttsx3(self, text: str) -> None:
        self._engine.say(text)
        self._engine.runAndWait()

    def _speak_edge_tts(self, text: str) -> None:
        if edge_tts is None:
            return
        cfg = self._engine
        communicate = edge_tts.Communicate(
            text,
            voice=cfg["voice"],
            rate=cfg["rate"],
            volume=cfg["volume"],
        )
        tmp_path: str | None = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            communicate.save_sync(tmp_path)
            if not self._shutdown.is_set():
                _play_audio_file(tmp_path, self._shutdown)
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    def _speak_kokoro(self, text: str) -> None:
        import numpy as np

        cfg = self._engine
        pipeline = cfg["pipeline"]
        chunks: list[Any] = []
        for _gs, _ps, audio in pipeline(
            text,
            voice=cfg["voice"],
            speed=cfg["speed"],
        ):
            if self._shutdown.is_set():
                return
            chunks.append(np.asarray(audio, dtype=np.float32))
        if not chunks:
            return
        audio = np.concatenate(chunks) * float(self._volume)
        tmp_path: str | None = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            _write_wav_int16(Path(tmp_path), audio, _KOKORO_SAMPLE_RATE)
            if not self._shutdown.is_set():
                _play_audio_file(tmp_path, self._shutdown)
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    def speak_text(self, text: str) -> None:
        try:
            if not text or not text.strip():
                return
            if not self._package_ok:
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
            if self._engine_name == "pyttsx3" and self._engine is not None:
                try:
                    self._engine.stop()
                except Exception:
                    pass
            else:
                try:
                    import pygame
                    if pygame.mixer.get_init():
                        pygame.mixer.stop()
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
                engine=str(getattr(settings, "voice_tts_engine", "pyttsx3")),
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

            if use_tts and (self._tts is None or not self._tts.package_ok):
                eng = "pyttsx3"
                if self._tts is not None:
                    eng = self._tts.engine_name
                else:
                    eng = str(getattr(self._settings, "voice_tts_engine", "pyttsx3"))
                logger.warning(
                    f"VoiceOutputCoordinator: TTS engine {eng!r} unavailable — falling back to bleeps"
                )
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
