"""Phase 3B glitch overlay tests — run: python tests/test_phase3b_glitch.py"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import json
import py_compile
import unittest
from unittest.mock import MagicMock, patch

from agetha.core.ai_engine import AIEngine
from agetha.ui import glitch_overlay
from agetha.app_config import AppSettings

ROOT = Path(__file__).resolve().parent.parent


class TestGlitchImports(unittest.TestCase):
    def test_module_imports_without_mainloop(self) -> None:
        self.assertIn("scanlines", glitch_overlay.VALID_GLITCH_STYLES)
        for style in ("bsod", "matrix", "tear"):
            self.assertIn(style, glitch_overlay.VALID_GLITCH_STYLES)
        self.assertEqual(glitch_overlay.normalize_glitch_style(""), "scanlines")
        self.assertEqual(glitch_overlay.normalize_glitch_style("RGB_SPLIT"), "rgb_split")
        self.assertEqual(glitch_overlay.normalize_glitch_style("bogus"), "scanlines")

    def test_py_compile(self) -> None:
        for rel in (
            "agetha/ui/glitch_overlay.py",
            "agetha/core/ai_engine.py",
            "agetha/commands/command_handlers.py",
            "agetha/app_config.py",
            "agetha/commands/command_guard.py",
            "tests/test_phase3b_glitch.py",
        ):
            py_compile.compile(str(ROOT / rel), doraise=True)


class TestDurationClamp(unittest.TestCase):
    def test_clamp_respects_bounds(self) -> None:
        with patch("agetha.ui.glitch_overlay.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(glitch_max_duration_ms=2000)
            self.assertEqual(glitch_overlay.clamp_glitch_duration(5000), 2000)
            self.assertEqual(glitch_overlay.clamp_glitch_duration(50), 200)
            self.assertEqual(glitch_overlay.clamp_glitch_duration(None), 2000)
            self.assertEqual(glitch_overlay.clamp_glitch_duration("bad", max_ms=1500), 1500)

    def test_app_settings_style_validation(self) -> None:
        raw = {"GLITCH_DEFAULT_STYLE": "flicker", "GLITCH_MAX_DURATION_MS": "99999"}
        settings = AppSettings(raw)
        self.assertEqual(settings.glitch_default_style, "flicker")
        self.assertEqual(settings.glitch_max_duration_ms, 5000)
        bad = AppSettings({"GLITCH_DEFAULT_STYLE": "not_a_style"})
        self.assertEqual(bad.glitch_default_style, "scanlines")


class TestDisabledHandler(unittest.TestCase):
    def test_handler_skips_overlay_when_disabled(self) -> None:
        from agetha.commands.command_handlers import handle_glitch_overlay

        app = MagicMock()
        app._speak_and_continue = MagicMock()
        ctx = MagicMock()
        ctx.segments = [{"text": "Glitching.", "pause": 0.0}]
        ctx.mood = "paranoid"
        ctx.shutdown_requested = False
        response = {"command": "glitch_overlay", "style": "static"}

        with patch("agetha.commands.command_handlers.get_settings") as mock_settings:
            mock_settings.return_value.enable_glitch_effects = False
            with patch("agetha.ui.glitch_overlay.show_glitch_overlay") as mock_show:
                ok = handle_glitch_overlay(app, response, ctx)
                self.assertTrue(ok)
                mock_show.assert_not_called()
                app._speak_and_continue.assert_called_once()


class TestParseGate(unittest.TestCase):
    def test_parse_blocks_glitch_when_disabled(self) -> None:
        engine = AIEngine.__new__(AIEngine)
        engine._app_settings = MagicMock()
        engine._app_settings.enable_glitch_effects = False
        raw = json.dumps(
            {
                "command": "glitch_overlay",
                "style": "scanlines",
                "duration_ms": 1000,
                "mood": "paranoid",
                "segments": [{"text": "See it?", "pause": 0.0}],
            }
        )
        result = AIEngine._parse(engine, raw)
        self.assertEqual(result["command"], "speak")
        self.assertIn("disabled", result["segments"][0]["text"].lower())


class TestShowOverlayMockParent(unittest.TestCase):
    def test_show_skips_when_disabled(self) -> None:
        parent = MagicMock()
        with patch("agetha.ui.glitch_overlay.get_settings") as mock_settings:
            mock_settings.return_value.enable_glitch_effects = False
            glitch_overlay.show_glitch_overlay(parent, style="static", duration_ms=500)
            parent.after.assert_not_called()

    def test_show_schedules_on_parent_when_enabled(self) -> None:
        parent = MagicMock()
        with patch("agetha.ui.glitch_overlay.IS_WINDOWS", True), patch(
            "agetha.ui.glitch_overlay.get_settings",
        ) as mock_settings:
            mock_settings.return_value.enable_glitch_effects = True
            mock_settings.return_value.glitch_default_style = "scanlines"
            with patch("agetha.ui.glitch_overlay._GlitchOverlay") as mock_overlay:
                glitch_overlay.show_glitch_overlay(parent, style="scanlines", duration_ms=800)
                parent.after.assert_called_once()
                parent.after.call_args[0][1]()
                mock_overlay.assert_called_once()

    def test_show_skips_on_managed_linux_windows(self) -> None:
        parent = MagicMock()
        with patch("agetha.ui.glitch_overlay.IS_WINDOWS", False), patch(
            "agetha.ui.glitch_overlay.get_settings",
        ) as mock_settings:
            mock_settings.return_value.enable_glitch_effects = True
            glitch_overlay.show_glitch_overlay(parent, style="scanlines", duration_ms=800)
        parent.after.assert_not_called()


if __name__ == "__main__":
    unittest.main()
