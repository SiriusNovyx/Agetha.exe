from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agetha.app_config as app_config
from agetha.config.io import AtomicWriteError
from agetha.config.schema import SETTING_SPECS


class ConfigDiscoverabilityTests(unittest.TestCase):
    def test_normal_startup_runs_migration_before_settings_reload(self) -> None:
        import main

        order: list[str] = []
        with patch.object(
            app_config, "ensure_config_file", side_effect=lambda *_a, **_k: order.append("ensure"),
        ), patch.object(
            app_config, "migrate_missing_setting_specs", side_effect=lambda *_a, **_k: order.append("migrate"),
        ), patch.object(
            app_config, "get_settings", side_effect=lambda *_a, **_k: order.append("load"),
        ), patch.object(
            main, "refresh_config_constants", side_effect=lambda: order.append("refresh"),
        ), patch.object(main, "_warn_if_no_api_key", return_value=None):
            main._early_config_check()

        self.assertEqual(order, ["ensure", "migrate", "load", "refresh"])

    def test_missing_canonical_settings_are_added_in_registry_order(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.txt"
            path.write_text(
                "# existing comment\n"
                "UNKNOWN_FUTURE_KEY = keep\n"
                "COMPACT_MODE = no\n",
                encoding="utf-8",
            )

            ok, added = app_config.migrate_missing_setting_specs(path)

            self.assertTrue(ok)
            self.assertEqual(added, tuple(SETTING_SPECS))
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith(
                "# existing comment\nUNKNOWN_FUTURE_KEY = keep\nCOMPACT_MODE = no\n"
            ))
            positions = [text.index(f"{key} = ") for key in SETTING_SPECS]
            self.assertEqual(positions, sorted(positions))

    def test_existing_values_comments_unknowns_and_duplicates_are_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.txt"
            original = (
                "# retain this\n"
                "AI_MAX_TOKENS = malformed-on-purpose\n"
                "UNKNOWN_SETTING = value\n"
                "AI_MAX_TOKENS=second-copy\n"
                "GEMINI_MODEL = gemini-custom\n"
            )
            path.write_text(original, encoding="utf-8")

            ok, added = app_config.migrate_missing_setting_specs(path)

            self.assertTrue(ok)
            self.assertNotIn("AI_MAX_TOKENS", added)
            self.assertNotIn("GEMINI_MODEL", added)
            text = path.read_text(encoding="utf-8")
            self.assertIn(original, text)
            self.assertEqual(text.count("AI_MAX_TOKENS"), 2)
            self.assertIn("GEMINI_MODEL = gemini-custom", text)
            self.assertIn("UNKNOWN_SETTING = value", text)

    def test_migration_is_idempotent_and_never_inserts_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.txt"
            path.write_text("COMPACT_MODE = yes\n", encoding="utf-8")

            first = app_config.migrate_missing_setting_specs(path)
            after_first = path.read_bytes()
            second = app_config.migrate_missing_setting_specs(path)

            self.assertTrue(first[0])
            self.assertEqual(second, (True, ()))
            self.assertEqual(path.read_bytes(), after_first)
            self.assertNotIn(b"GEMINI_API_KEY", after_first)
            self.assertNotIn(b"OPENROUTER_API_KEY", after_first)

    def test_atomic_failure_leaves_original_document_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.txt"
            original = b"# old config\nCOMPACT_MODE = yes\n"
            path.write_bytes(original)

            with patch.object(
                app_config,
                "_write_atomic_config",
                side_effect=AtomicWriteError("write_not_applied", "blocked"),
            ):
                result = app_config.migrate_missing_setting_specs(path)

            self.assertEqual(result, (False, ()))
            self.assertEqual(path.read_bytes(), original)

    def test_compact_marker_and_fast_mode_snapshot_are_outside_migration(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "config.txt"
            marker = root / app_config.COMPACT_MODE_FAIL_CLOSED_MARKER
            snapshot = root / "memory" / "fast_mode_snapshot.json"
            snapshot.parent.mkdir()
            path.write_text("COMPACT_MODE = no\nFASTER_MODE = yes\n", encoding="utf-8")
            marker.write_text("compact-required\n", encoding="utf-8")
            snapshot.write_text('{"state":"active"}', encoding="utf-8")

            ok, _added = app_config.migrate_missing_setting_specs(path)

            self.assertTrue(ok)
            self.assertIn("COMPACT_MODE = no", path.read_text(encoding="utf-8"))
            self.assertIn("FASTER_MODE = yes", path.read_text(encoding="utf-8"))
            self.assertEqual(marker.read_text(encoding="utf-8"), "compact-required\n")
            self.assertEqual(snapshot.read_text(encoding="utf-8"), '{"state":"active"}')


if __name__ == "__main__":
    unittest.main()
