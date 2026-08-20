from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agetha.app_config import AppSettings
from agetha.commands.command_handlers import HANDLERS, dispatch
from agetha.core.ai_engine import AIEngine, REQUEST_PROFILES
from agetha.core.capabilities import CapabilityController, CapabilityPolicy
from agetha.core.request_context import (
    AmbientRelevance,
    normalize_ambient_relevance,
)


class AmbientRelevancePolicyTests(unittest.TestCase):
    def test_relevance_normalization_fails_closed_to_mundane(self) -> None:
        self.assertIs(normalize_ambient_relevance("interesting"), AmbientRelevance.INTERESTING)
        self.assertIs(normalize_ambient_relevance("IMPORTANT"), AmbientRelevance.IMPORTANT)
        self.assertIs(normalize_ambient_relevance("urgent"), AmbientRelevance.MUNDANE)
        self.assertIs(normalize_ambient_relevance(None), AmbientRelevance.MUNDANE)

    def test_parser_retains_only_normalized_relevance_metadata(self) -> None:
        engine = AIEngine.__new__(AIEngine)
        important = engine._parse(
            '{"command":"speak","ambient_relevance":"important",'
            '"segments":[{"text":"Warning","pause":0}]}'
        )
        invalid = engine._parse(
            '{"command":"idle","ambient_relevance":"urgent","segments":[]}'
        )

        self.assertEqual(important["ambient_relevance"], "important")
        self.assertEqual(invalid["ambient_relevance"], "mundane")

    def test_ambient_few_shots_cover_mundane_interesting_and_important(self) -> None:
        examples = " ".join(
            str(item.get("content", ""))
            for item in AIEngine._few_shots_for_profile(REQUEST_PROFILES["fast_ambient"])
        )
        for relevance in ("mundane", "interesting", "important"):
            self.assertIn(f'"ambient_relevance":"{relevance}"', examples)

    def test_mundane_ambient_is_forced_idle(self) -> None:
        engine = AIEngine.__new__(AIEngine)
        result = engine._enforce_profile_response_safety(
            {
                "command": "speak",
                "ambient_relevance": "mundane",
                "segments": [{"text": "The desktop is still here.", "pause": 0.0}],
            },
            REQUEST_PROFILES["fast_ambient"],
            "",
        )

        self.assertEqual(result["command"], "idle")
        self.assertEqual(result["segments"], [])
        self.assertEqual(result["ambient_relevance"], "mundane")

    def test_interesting_and_important_ambient_may_preserve_short_speech(self) -> None:
        engine = AIEngine.__new__(AIEngine)
        for relevance in ("interesting", "important"):
            with self.subTest(relevance=relevance):
                result = engine._enforce_profile_response_safety(
                    {
                        "command": "speak",
                        "ambient_relevance": relevance,
                        "segments": [{"text": "The build just failed.", "pause": 0.0}],
                        "shutdown": True,
                        "summary_memory": "untrusted",
                    },
                    REQUEST_PROFILES["fast_ambient"],
                    "",
                )
                self.assertEqual(result["command"], "speak")
                self.assertEqual(result["ambient_relevance"], relevance)
                self.assertFalse(result["shutdown"])
                self.assertNotIn("summary_memory", result)

    def test_important_metadata_cannot_grant_ambient_command_authority(self) -> None:
        engine = AIEngine.__new__(AIEngine)
        result = engine._enforce_profile_response_safety(
            {
                "command": "run_command",
                "cmd": "whoami",
                "ambient_relevance": "important",
                "segments": [{"text": "Running it.", "pause": 0.0}],
            },
            REQUEST_PROFILES["fast_ambient"],
            "",
        )

        self.assertEqual(result["command"], "idle")
        self.assertEqual(result["segments"], [])
        self.assertNotIn("cmd", result)


class AmbientDispatchIsolationTests(unittest.TestCase):
    @staticmethod
    def app():
        class App:
            STATE_IDLE = "idle"
            _ATTENTION_MOODS = set()

            def _presence_decision(self, *, urgency="nonurgent"):
                self.presence_urgencies.append(urgency)
                return SimpleNamespace(
                    allow_popup=True,
                    allow_voice=False,
                    queue_nonurgent=False,
                )

        app = App()
        app.presence_urgencies = []
        app._capabilities = CapabilityController(
            CapabilityPolicy.from_settings(AppSettings({"COMPACT_MODE": "no"})),
        )
        app._guard = MagicMock()
        app._try_short_mood_speak = MagicMock(return_value=False)
        app._speak_and_continue = MagicMock()
        app._play_response_motion = MagicMock()
        app._set_state = MagicMock()
        app._reschedule_screen_poll = MagicMock()
        app._maybe_snap_to_center = MagicMock()
        app._presence = None
        return app

    def test_ambient_state_changing_command_never_reaches_guard_or_handler(self) -> None:
        app = self.app()
        handler = MagicMock()
        with patch.dict(HANDLERS, {"run_command": handler}), patch(
            "agetha.commands.command_handlers.get_settings",
            return_value=AppSettings({"COMPACT_MODE": "no", "ENABLE_COMMAND_EXECUTION": "yes"}),
        ):
            dispatch(
                app,
                {
                    "command": "run_command",
                    "cmd": "whoami",
                    "ambient_relevance": "important",
                    "segments": [],
                },
                None,
                origin="ambient",
            )

        handler.assert_not_called()
        app._guard.check.assert_not_called()

    def test_important_surfaces_semantics_but_not_forced_audio(self) -> None:
        app = self.app()
        dispatch(
            app,
            {
                "command": "speak",
                "ambient_relevance": "important",
                "mood": "neutral",
                "segments": [{"text": "The build failed.", "pause": 0.0}],
            },
            None,
            origin="ambient",
        )

        self.assertEqual(app.presence_urgencies, ["important"])
        app._speak_and_continue.assert_called_once()
        self.assertFalse(app._speak_and_continue.call_args.kwargs["allow_audio"])


if __name__ == "__main__":
    unittest.main()
