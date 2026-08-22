from __future__ import annotations

import unittest

import agetha.app_config as app_config


class SettingSpecTests(unittest.TestCase):
    def test_stable_specs_are_canonical_runtime_metadata(self) -> None:
        from agetha.config.schema import SETTING_SPECS, SettingKind

        tokens = SETTING_SPECS["AI_MAX_TOKENS"]
        self.assertEqual(tokens.kind, SettingKind.INT)
        self.assertEqual(tokens.default, "400")
        self.assertEqual((tokens.minimum, tokens.maximum), (64, 8192))
        self.assertEqual(tokens.group, "ai")

        typing = SETTING_SPECS["UNICODE_TYPING_MODE"]
        self.assertEqual(typing.kind, SettingKind.ENUM)
        self.assertEqual(
            typing.choices,
            frozenset({"auto", "unicode", "paste", "preview", "paced"}),
        )

    def test_restart_metadata_matches_dashboard_and_runtime_ownership(self) -> None:
        from agetha.config.schema import SETTING_SPECS
        from agetha.ui.dashboard import _SETTING_SECTIONS

        expected = {
            "ENABLE_GEMINI": True,
            "GEMINI_MODEL": True,
            "ENABLE_PRINTWINDOW_FALLBACK": True,
            "AI_MAX_TOKENS": True,
            "HISTORY_LIMIT": True,
            "MEMORY_CHARS": True,
            "EPISODIC_PROMPT_LIMIT": True,
            "AI_TEMPERATURE": True,
            "AI_TOP_P": True,
            "SCREEN_POLL_INTERVAL_SEC": False,
            "OCR_MAX_DIMENSION": True,
            "OCR_FORCE_REFRESH_SECONDS": True,
            "OCR_PREPROCESSING": True,
            "UNICODE_TYPING_MODE": False,
        }
        self.assertEqual(
            {key: spec.restart_required for key, spec in SETTING_SPECS.items()},
            expected,
        )

        dashboard_restart = {
            key: needs_restart
            for _title, items in _SETTING_SECTIONS
            for key, _kind, needs_restart, _choices in items
        }
        for key in SETTING_SPECS.keys() & dashboard_restart.keys():
            with self.subTest(key=key):
                self.assertEqual(
                    SETTING_SPECS[key].restart_required,
                    dashboard_restart[key],
                )

    def test_legacy_views_derive_from_setting_specs(self) -> None:
        from agetha.config.schema import SETTING_SPECS

        expected_ranges = {
            key: (spec.minimum, spec.maximum)
            for key, spec in SETTING_SPECS.items()
            if spec.minimum is not None and spec.maximum is not None
        }
        self.assertEqual(app_config._CONFIG_VALUE_RANGES, expected_ranges)
        template_defaults, _invalid = app_config._parse_config_text(app_config.DEFAULT_CONFIG)
        for key, spec in SETTING_SPECS.items():
            self.assertEqual(template_defaults[key], spec.default)

    def test_specs_do_not_encode_transactional_or_secret_policy(self) -> None:
        from agetha.config.schema import SETTING_SPECS

        self.assertNotIn("GROQ_API_KEY", SETTING_SPECS)
        self.assertNotIn("COMPACT_MODE", SETTING_SPECS)
        self.assertNotIn("FASTER_MODE", SETTING_SPECS)

    def test_spec_validation_preserves_existing_strict_behavior(self) -> None:
        self.assertTrue(app_config.validate_config_value("AI_MAX_TOKENS", "900", enforce_range=True))
        self.assertFalse(app_config.validate_config_value("AI_MAX_TOKENS", "9000", enforce_range=True))
        self.assertTrue(app_config.validate_config_value("UNICODE_TYPING_MODE", "paced", enforce_range=True))
        self.assertFalse(app_config.validate_config_value("UNICODE_TYPING_MODE", "magic", enforce_range=True))

    def test_checked_in_reference_is_current(self) -> None:
        from agetha.config.generate_settings_reference import settings_reference_matches

        self.assertTrue(settings_reference_matches())


if __name__ == "__main__":
    unittest.main()
