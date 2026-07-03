"""Phase 2 TTS / voice output tests — run: python tests/test_phase2_tts.py"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import py_compile
import unittest
from unittest.mock import MagicMock, patch

from agetha.app_config import AppSettings
from agetha.features.tts_player import PYTTSX3_OK, TTSPlayer, VoiceOutputCoordinator

ROOT = Path(__file__).resolve().parent.parent
MODULES = (
    "agetha/features/tts_player.py",
    "main.py",
    "agetha/app_config.py",
    "tests/test_phase2_tts.py",
)


class TestPyCompile(unittest.TestCase):
    def test_touched_modules_compile(self) -> None:
        for name in MODULES:
            with self.subTest(module=name):
                py_compile.compile(str(ROOT / name), doraise=True)


class TestAppSettingsVoiceOutput(unittest.TestCase):
    def test_voice_output_mode_valid(self) -> None:
        s = AppSettings({"VOICE_OUTPUT_MODE": "both"})
        self.assertEqual(s.voice_output_mode, "both")

    def test_voice_output_mode_invalid_falls_back(self) -> None:
        s = AppSettings({"VOICE_OUTPUT_MODE": "nonsense"})
        self.assertEqual(s.voice_output_mode, "bleeps_only")

    def test_tts_rate_clamped(self) -> None:
        s = AppSettings({"TTS_RATE": "999"})
        self.assertEqual(s.tts_rate, 300)

    def test_tts_volume_clamped(self) -> None:
        s = AppSettings({"TTS_VOLUME": "2.5"})
        self.assertEqual(s.tts_volume, 1.0)


class TestVoiceOutputCoordinator(unittest.TestCase):
    def test_bleeps_only_no_tts_init(self) -> None:
        bleep = MagicMock()
        settings = AppSettings({"VOICE_OUTPUT_MODE": "bleeps_only"})
        coord = VoiceOutputCoordinator(bleep, settings)
        self.assertEqual(coord.mode, "bleeps_only")
        self.assertTrue(coord.uses_bleeps())
        self.assertIsNone(coord._tts)

    def test_invalid_mode_falls_back(self) -> None:
        bleep = MagicMock()
        settings = AppSettings({"VOICE_OUTPUT_MODE": "invalid"})
        coord = VoiceOutputCoordinator(bleep, settings)
        self.assertEqual(coord.mode, "bleeps_only")

    def test_start_speech_bleeps_only(self) -> None:
        bleep = MagicMock()
        settings = AppSettings({"VOICE_OUTPUT_MODE": "bleeps_only"})
        coord = VoiceOutputCoordinator(bleep, settings)
        segments = [{"text": "hello world"}]
        coord.start_speech(segments, "happy")
        bleep.start_talking.assert_called_once_with(tone="happy")

    def test_tts_only_without_pyttsx3_falls_back_to_bleeps(self) -> None:
        bleep = MagicMock()
        settings = AppSettings({"VOICE_OUTPUT_MODE": "tts_only"})
        with patch("agetha.features.tts_player.PYTTSX3_OK", False):
            coord = VoiceOutputCoordinator(bleep, settings)
            coord.start_speech([{"text": "hi"}], "neutral")
        bleep.start_talking.assert_called_once_with(tone="neutral")


class TestTTSPlayerGraceful(unittest.TestCase):
    def test_speak_text_without_pyttsx3(self) -> None:
        with patch("agetha.features.tts_player.PYTTSX3_OK", False):
            player = TTSPlayer()
        player.speak_text("hello")  # must not raise

    def test_speak_segments_without_pyttsx3(self) -> None:
        with patch("agetha.features.tts_player.PYTTSX3_OK", False):
            player = TTSPlayer()
        player.speak_segments([{"text": "a"}, {"text": "b"}])  # must not raise

    def test_stop_pause_resume_never_raise(self) -> None:
        with patch("agetha.features.tts_player.PYTTSX3_OK", False):
            player = TTSPlayer()
        player.pause()
        player.resume()
        player.stop()

    def test_segments_to_text_joins(self) -> None:
        with patch("agetha.features.tts_player.PYTTSX3_OK", False):
            player = TTSPlayer()
        with patch.object(player, "speak_text") as mock_speak:
            player.speak_segments([{"text": "one"}, {"text": "two"}])
        mock_speak.assert_any_call("one")
        mock_speak.assert_any_call("two")
        self.assertEqual(mock_speak.call_count, 2)


if __name__ == "__main__":
    print(f"PYTTSX3_OK={PYTTSX3_OK}")
    unittest.main(verbosity=2)
