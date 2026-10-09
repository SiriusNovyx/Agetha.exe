from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agetha import app_config
from agetha.commands.handlers import memory_presentation
from agetha.commands.handlers.support import DispatchCtx
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
    """Reads now preserve originals; replacement is not a load-time repair."""

    def test_companion_stats_corruption_is_preserved_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "companion_stats.json"
            target.write_text("{broken", encoding="utf-8")
            with (
                patch.object(companion_stats, "MEMORY_DIR", root),
                patch.object(companion_stats, "STATS_FILE", target),
            ):
                loaded = companion_stats.load_stats()

            self.assertEqual(loaded["infection_level"], 0.0)
            self.assertEqual(target.read_text(encoding="utf-8"), "{broken")

    def test_tasks_corruption_is_preserved_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "tasks.json"
            target.write_text("not-json", encoding="utf-8")
            with (
                patch.object(tasks, "MEMORY_DIR", root),
                patch.object(tasks, "TASKS_FILE", target),
            ):
                self.assertEqual(tasks.get_tasks(), [])

            self.assertEqual(target.read_text(encoding="utf-8"), "not-json")


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


class TestFailedTaskAndDreamSaves(unittest.TestCase):
    def setUp(self) -> None:
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(folder)
        self.task_file = self.root / "tasks.json"
        self.dream_file = self.root / "dreams.jsonl"
        self.original_tasks = '[{"id": 1, "text": "buy milk", "done": false}]'
        self.task_file.write_text(self.original_tasks, encoding="utf-8")
        self.enterContext(patch.object(tasks, "MEMORY_DIR", self.root))
        self.enterContext(patch.object(tasks, "TASKS_FILE", self.task_file))
        self.enterContext(patch.object(dreams, "MEMORY_DIR", self.root))
        self.enterContext(patch.object(dreams, "DREAMS_FILE", self.dream_file))
        settings = SimpleNamespace(
            tasks_max_entries=100, enable_tasks=True,
            dreams_max_entries=100, enable_dreams=True,
        )
        self.enterContext(patch.object(app_config, "get_settings", return_value=settings))
        self.enterContext(patch.object(memory_presentation, "get_settings", return_value=settings))

    def test_failed_add_returns_none_and_preserves_saved_tasks(self) -> None:
        with patch.object(tasks, "write_atomic", side_effect=OSError("disk full")):
            result = tasks.add_task("email report")
        self.assertIsNone(result)
        self.assertEqual(self.task_file.read_text(encoding="utf-8"), self.original_tasks)

    def test_failed_completion_returns_none_and_keeps_task_pending(self) -> None:
        with patch.object(tasks, "write_atomic", side_effect=OSError("disk full")):
            result = tasks.complete_task(1)
        self.assertIsNone(result)
        self.assertEqual(self.task_file.read_text(encoding="utf-8"), self.original_tasks)

    def test_failed_dream_save_returns_none_and_preserves_journal(self) -> None:
        original = '{"text": "existing dream"}\n'
        self.dream_file.write_text(original, encoding="utf-8")
        with (
            patch.object(dreams, "_collect_fragments", return_value=["a", "b"]),
            patch.object(dreams, "write_atomic", side_effect=OSError("disk full")),
        ):
            result = dreams.generate_dream()
        self.assertIsNone(result)
        self.assertEqual(self.dream_file.read_text(encoding="utf-8"), original)

    def test_task_handlers_do_not_confirm_failed_persistence(self) -> None:
        cases = (
            (memory_presentation.handle_add_task, {"text": "email report"}),
            (memory_presentation.handle_complete_task, {"task_id": 1}),
        )
        for handler, response in cases:
            with self.subTest(handler=handler.__name__):
                callbacks, successes, errors, spoken = [], [], [], []
                app = SimpleNamespace(
                    _show_op_success=successes.append,
                    _show_op_error=errors.append,
                    _speak_and_continue=lambda segments, *args: spoken.extend(segments),
                )
                ctx = DispatchCtx(None, "neutral", [{"text": "Task saved successfully."}], False)
                with (
                    patch.object(tasks, "write_atomic", side_effect=OSError("disk full")),
                    patch.object(memory_presentation, "_schedule_app_ui", side_effect=lambda app, cb: callbacks.append(cb)),
                ):
                    handler(app, response, ctx)
                for callback in callbacks:
                    callback()
                self.assertEqual(successes, [])
                self.assertEqual(len(errors), 1)
                self.assertTrue(spoken)
                self.assertNotIn("Task saved successfully.", [segment["text"] for segment in spoken])
                self.assertEqual(self.task_file.read_text(encoding="utf-8"), self.original_tasks)


if __name__ == "__main__":
    unittest.main()
