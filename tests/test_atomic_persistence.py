from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agetha import app_config
from agetha.core import companion_stats, dreams
from agetha.features import tasks
from agetha.platform import voice_input
from agetha.ui import dashboard
from agetha.utils import write_atomic


class TestAtomicWriter(unittest.TestCase):
    def test_replaces_text_and_bytes_without_leftover_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "nested" / "state.json"

            write_atomic(target, '{"value": 1}')
            self.assertEqual(target.read_text(encoding="utf-8"), '{"value": 1}')
            write_atomic(target, b'{"value": 2}')
            self.assertEqual(target.read_bytes(), b'{"value": 2}')
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_failed_replace_preserves_original_and_removes_temp(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "state.json"
            target.write_text("original", encoding="utf-8")

            with patch("agetha.utils.os.replace", side_effect=OSError("locked")):
                with self.assertRaises(OSError):
                    write_atomic(target, "replacement")

            self.assertEqual(target.read_text(encoding="utf-8"), "original")
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_failed_config_replace_also_preserves_original(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "config.txt"
            target.write_text("SAFE = yes\n", encoding="utf-8")

            with patch.object(
                app_config.os, "replace", side_effect=OSError("locked")
            ):
                with self.assertRaises(OSError):
                    app_config._write_atomic_config(target, "SAFE = no\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "SAFE = yes\n")
            self.assertEqual(list(target.parent.iterdir()), [target])


class TestCorruptStateRepair(unittest.TestCase):
    def test_companion_stats_corruption_is_repaired_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "companion_stats.json"
            target.write_text("{broken", encoding="utf-8")
            old_cache = companion_stats._cached_stats
            try:
                companion_stats._cached_stats = None
                with (
                    patch.object(companion_stats, "MEMORY_DIR", root),
                    patch.object(companion_stats, "STATS_FILE", target),
                ):
                    loaded = companion_stats.load_stats()
            finally:
                companion_stats._cached_stats = old_cache

            repaired = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(loaded["infection_level"], 0.0)
            self.assertEqual(repaired["infection_level"], 0.0)
            self.assertIn("last_updated", repaired)

    def test_tasks_corruption_is_repaired_to_empty_list_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "tasks.json"
            target.write_text("not-json", encoding="utf-8")
            with (
                patch.object(tasks, "MEMORY_DIR", root),
                patch.object(tasks, "TASKS_FILE", target),
            ):
                self.assertEqual(tasks.get_tasks(), [])

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), [])


class TestPersistenceCallSites(unittest.TestCase):
    def test_dreams_rewrite_uses_atomic_writer(self) -> None:
        entries = [{"ts": "now", "text": "dream"}]
        with patch.object(dreams, "write_atomic") as atomic:
            dreams._save_entries_unlocked(entries)
        atomic.assert_called_once()
        self.assertIn('"text": "dream"', atomic.call_args.args[1])

    def test_voice_settings_use_atomic_writer(self) -> None:
        with patch.object(voice_input, "write_atomic") as atomic:
            voice_input.save_mic_settings({"device": 2})
        atomic.assert_called_once()
        self.assertEqual(json.loads(atomic.call_args.args[1]), {"device": 2})

    def test_dashboard_notepad_uses_atomic_writer(self) -> None:
        with patch.object(dashboard, "write_atomic") as atomic:
            self.assertTrue(dashboard.write_notepad_text("remember this"))
        atomic.assert_called_once_with(dashboard.NOTEPAD_FILE, "remember this")

    def test_config_creation_and_patch_are_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "config.txt"
            app_config.create_default_config(target)
            self.assertEqual(target.read_text(encoding="utf-8"), app_config.DEFAULT_CONFIG)

            target.write_text("EXAMPLE = old\n", encoding="utf-8")
            with (
                patch.object(app_config, "CONFIG_PATH", target),
                patch.object(app_config, "_settings", None),
                patch.object(
                    app_config,
                    "_write_atomic_config",
                    wraps=app_config._write_atomic_config,
                ) as atomic,
            ):
                ok, failed = app_config.patch_config_keys(
                    {"EXAMPLE": "new", "SECOND": "value"}
                )

            self.assertTrue(ok)
            self.assertEqual(failed, [])
            atomic.assert_called_once()
            text = target.read_text(encoding="utf-8")
            self.assertIn("EXAMPLE = new", text)
            self.assertIn("SECOND = value", text)


if __name__ == "__main__":
    unittest.main()
