"""Headless Dashboard presentation tests for capability profiles."""

from __future__ import annotations

import unittest

from agetha.app_config import AppSettings
from agetha.core.capabilities import CapabilityProfile
from agetha.ui.dashboard import (
    build_dashboard_presentation,
    split_dashboard_profile_update,
)


def _settings(*, compact: bool) -> AppSettings:
    return AppSettings({"COMPACT_MODE": "yes" if compact else "no"})


class TestDashboardPresentation(unittest.TestCase):
    def test_compact_model_exposes_classic_surfaces_without_advanced_sections(self) -> None:
        model = build_dashboard_presentation(_settings(compact=True))

        self.assertEqual(model.profile, CapabilityProfile.COMPACT)
        self.assertTrue(model.compact_mode_on)
        self.assertEqual(
            model.tabs,
            ("Virus Registry", "Notepad", "About", "Settings"),
        )
        self.assertFalse(model.show_system_monitor)
        self.assertFalse(model.show_senses)
        self.assertIn("COMPACT_MODE", model.setting_keys)
        self.assertIn("AI_TEMPERATURE", model.setting_keys)
        self.assertIn("ENABLE_LONGTERM_MEMORY", model.setting_keys)
        self.assertIn("WINDOW_TOPMOST", model.setting_keys)
        self.assertNotIn("ENABLE_TERMINAL_SENTINEL", model.setting_keys)
        self.assertNotIn("ENABLE_PROCESS_AWARENESS", model.setting_keys)
        self.assertNotIn("ENABLE_COMPUTER_USE", model.setting_keys)
        self.assertNotIn("COMPUTER_USE_PLANNER_PROVIDER", model.setting_keys)

    def test_full_model_exposes_real_advanced_surfaces(self) -> None:
        model = build_dashboard_presentation(_settings(compact=False))

        self.assertEqual(model.profile, CapabilityProfile.FULL)
        self.assertFalse(model.compact_mode_on)
        self.assertEqual(model.tabs[0], "System Monitor")
        self.assertTrue(model.show_system_monitor)
        self.assertTrue(model.show_senses)
        self.assertIn("COMPACT_MODE", model.setting_keys)
        self.assertIn("ENABLE_TERMINAL_SENTINEL", model.setting_keys)
        self.assertIn("ENABLE_PROCESS_AWARENESS", model.setting_keys)
        self.assertIn("ENABLE_COMPUTER_USE", model.setting_keys)
        self.assertIn("COMPUTER_USE_PLANNER_PROVIDER", model.setting_keys)

    def test_profile_change_is_removed_from_generic_config_updates(self) -> None:
        split = split_dashboard_profile_update(
            {"COMPACT_MODE": "no", "AI_TEMPERATURE": "0.7"},
            current_compact_mode=True,
        )

        self.assertEqual(split.generic_updates, {"AI_TEMPERATURE": "0.7"})
        self.assertIs(split.requested_compact_mode, False)

    def test_full_to_compact_request_is_separate_from_generic_patch(self) -> None:
        split = split_dashboard_profile_update(
            {"COMPACT_MODE": "yes"},
            current_compact_mode=False,
        )

        self.assertEqual(split.generic_updates, {})
        self.assertIs(split.requested_compact_mode, True)

    def test_unchanged_profile_does_not_request_a_transition(self) -> None:
        split = split_dashboard_profile_update(
            {"COMPACT_MODE": "yes", "ENABLE_WEB_RAG": "yes"},
            current_compact_mode=True,
        )

        self.assertEqual(split.generic_updates, {"ENABLE_WEB_RAG": "yes"})
        self.assertIsNone(split.requested_compact_mode)


if __name__ == "__main__":
    unittest.main()
