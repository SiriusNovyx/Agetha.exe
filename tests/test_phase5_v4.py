"""Phase 5 (v4.0.0) tests — circadian rhythm, dream journal, task keeper.

Run: python tests/test_phase5_v4.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import py_compile
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from agetha.app_config import AppSettings

ROOT = Path(__file__).resolve().parent.parent
MODULES = (
    "agetha/core/rhythm.py",
    "agetha/core/dreams.py",
    "agetha/features/tasks.py",
    "agetha/core/ai_engine.py",
    "agetha/commands/command_guard.py",
    "agetha/commands/command_handlers.py",
    "agetha/app_config.py",
    "main.py",
    "medic_helper.py",
    "tests/test_phase5_v4.py",
)

_PLAIN_SETTINGS = AppSettings({})


class TestPyCompile(unittest.TestCase):
    def test_touched_modules_compile(self) -> None:
        for name in MODULES:
            with self.subTest(module=name):
                py_compile.compile(str(ROOT / name), doraise=True)


class TestAppSettingsV4(unittest.TestCase):
    def test_defaults(self) -> None:
        s = AppSettings({})
        self.assertTrue(s.enable_circadian_rhythm)
        self.assertEqual(s.rhythm_night_start, 23)
        self.assertEqual(s.rhythm_night_end, 6)
        self.assertTrue(s.enable_dreams)
        self.assertEqual(s.dreams_max_entries, 40)
        self.assertTrue(s.enable_tasks)
        self.assertEqual(s.tasks_max_entries, 100)

    def test_clamping(self) -> None:
        s = AppSettings({
            "RHYTHM_NIGHT_START": "99",
            "DREAMS_MAX_ENTRIES": "2",
            "TASKS_MAX_ENTRIES": "999999",
        })
        self.assertEqual(s.rhythm_night_start, 23)   # out of range → default
        self.assertEqual(s.dreams_max_entries, 5)    # clamped to min
        self.assertEqual(s.tasks_max_entries, 1000)  # clamped to max

    def test_disable_flags(self) -> None:
        s = AppSettings({
            "ENABLE_CIRCADIAN_RHYTHM": "no",
            "ENABLE_DREAMS": "off",
            "ENABLE_TASKS": "0",
        })
        self.assertFalse(s.enable_circadian_rhythm)
        self.assertFalse(s.enable_dreams)
        self.assertFalse(s.enable_tasks)


class TestRhythmPhases(unittest.TestCase):
    def _phase(self, hour: int, **kwargs) -> str:
        from agetha.core.rhythm import get_rhythm_phase
        return get_rhythm_phase(datetime(2026, 7, 19, hour, 30), **kwargs)

    def test_deep_night_wraps_midnight(self) -> None:
        self.assertEqual(self._phase(23), "deep_night")
        self.assertEqual(self._phase(2), "deep_night")
        self.assertEqual(self._phase(5), "deep_night")

    def test_dawn_after_night_end(self) -> None:
        self.assertEqual(self._phase(6), "dawn")
        self.assertEqual(self._phase(7), "dawn")

    def test_day_phases(self) -> None:
        self.assertEqual(self._phase(9), "morning")
        self.assertEqual(self._phase(13), "afternoon")
        self.assertEqual(self._phase(18), "evening")
        self.assertEqual(self._phase(22), "night")

    def test_custom_night_window(self) -> None:
        self.assertEqual(self._phase(1, night_start=0, night_end=4), "deep_night")
        self.assertEqual(self._phase(4, night_start=0, night_end=4), "dawn")

    def test_invalid_hours_fall_back(self) -> None:
        # Bad bounds fall back to defaults instead of raising
        self.assertEqual(self._phase(2, night_start=99, night_end=-3), "deep_night")

    def test_phase_moods_are_valid(self) -> None:
        from agetha.core.rhythm import _PHASE_MOOD
        from agetha.core.ai_engine import VALID_MOODS
        for phase, mood in _PHASE_MOOD.items():
            with self.subTest(phase=phase):
                self.assertIn(mood, VALID_MOODS)

    def test_prompt_block_disabled(self) -> None:
        from agetha.core import rhythm
        s = AppSettings({"ENABLE_CIRCADIAN_RHYTHM": "no"})
        with patch("agetha.app_config.get_settings", return_value=s):
            self.assertEqual(rhythm.format_rhythm_for_prompt(), "")

    def test_prompt_block_enabled(self) -> None:
        from agetha.core import rhythm
        with patch("agetha.app_config.get_settings", return_value=_PLAIN_SETTINGS):
            block = rhythm.format_rhythm_for_prompt(datetime(2026, 7, 19, 9, 0))
            self.assertIn("INTERNAL CLOCK", block)
            self.assertIn("morning", block)


class TestDreams(unittest.TestCase):
    def setUp(self) -> None:
        from agetha.core import dreams
        self._dreams = dreams
        self._tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp.name)
        self._old_dir = dreams.MEMORY_DIR
        self._old_file = dreams.DREAMS_FILE
        dreams.MEMORY_DIR = tmp_path
        dreams.DREAMS_FILE = tmp_path / "dreams.jsonl"
        dreams._pending_recall = None

    def tearDown(self) -> None:
        self._dreams.MEMORY_DIR = self._old_dir
        self._dreams.DREAMS_FILE = self._old_file
        self._dreams._pending_recall = None
        self._tmp.cleanup()

    def test_generate_and_read_back(self) -> None:
        with patch("agetha.app_config.get_settings", return_value=_PLAIN_SETTINGS):
            record = self._dreams.generate_dream()
        self.assertIsNotNone(record)
        self.assertTrue(record["text"])
        dreams = self._dreams.get_recent_dreams(limit=5)
        self.assertEqual(len(dreams), 1)
        self.assertEqual(dreams[0]["text"], record["text"])

    def test_disabled_returns_none(self) -> None:
        s = AppSettings({"ENABLE_DREAMS": "no"})
        with patch("agetha.app_config.get_settings", return_value=s):
            self.assertIsNone(self._dreams.generate_dream())
        self.assertEqual(self._dreams.get_recent_dreams(), [])

    def test_max_entries_cap(self) -> None:
        s = AppSettings({"DREAMS_MAX_ENTRIES": "5"})
        with patch("agetha.app_config.get_settings", return_value=s):
            for _ in range(8):
                self._dreams.generate_dream()
        self.assertEqual(len(self._dreams.get_recent_dreams(limit=50)), 5)

    def test_wake_recall_is_one_shot(self) -> None:
        with patch("agetha.app_config.get_settings", return_value=_PLAIN_SETTINGS):
            self._dreams.generate_dream()
        self._dreams.mark_wake_recall()
        first = self._dreams.pop_wake_recall_for_prompt()
        self.assertIn("DREAM RECALL", first)
        self.assertEqual(self._dreams.pop_wake_recall_for_prompt(), "")

    def test_recall_empty_without_dreams(self) -> None:
        self._dreams.mark_wake_recall()
        self.assertEqual(self._dreams.pop_wake_recall_for_prompt(), "")

    def test_display_formatting(self) -> None:
        lines = self._dreams.format_dreams_for_display([])
        self.assertTrue(lines[0].startswith("[no dreams"))


class TestTasks(unittest.TestCase):
    def setUp(self) -> None:
        from agetha.features import tasks
        self._tasks = tasks
        self._tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp.name)
        self._old_dir = tasks.MEMORY_DIR
        self._old_file = tasks.TASKS_FILE
        tasks.MEMORY_DIR = tmp_path
        tasks.TASKS_FILE = tmp_path / "tasks.json"

    def tearDown(self) -> None:
        self._tasks.MEMORY_DIR = self._old_dir
        self._tasks.TASKS_FILE = self._old_file
        self._tmp.cleanup()

    def test_add_and_list(self) -> None:
        with patch("agetha.app_config.get_settings", return_value=_PLAIN_SETTINGS):
            first = self._tasks.add_task("buy milk")
            second = self._tasks.add_task("email the report")
        self.assertEqual(first["id"], 1)
        self.assertEqual(second["id"], 2)
        pending = self._tasks.get_tasks(include_done=False)
        self.assertEqual(len(pending), 2)
        self.assertEqual(self._tasks.get_pending_count(), 2)

    def test_add_empty_rejected(self) -> None:
        with patch("agetha.app_config.get_settings", return_value=_PLAIN_SETTINGS):
            self.assertIsNone(self._tasks.add_task("   "))

    def test_complete_by_id_and_text(self) -> None:
        with patch("agetha.app_config.get_settings", return_value=_PLAIN_SETTINGS):
            self._tasks.add_task("buy milk")
            self._tasks.add_task("email the report")
        done = self._tasks.complete_task(1)
        self.assertTrue(done["done"])
        done2 = self._tasks.complete_task("EMAIL")
        self.assertTrue(done2["done"])
        self.assertEqual(self._tasks.get_pending_count(), 0)

    def test_complete_missing_returns_none(self) -> None:
        self.assertIsNone(self._tasks.complete_task("nonexistent"))

    def test_prompt_block_lists_pending_only(self) -> None:
        with patch("agetha.app_config.get_settings", return_value=_PLAIN_SETTINGS):
            self._tasks.add_task("buy milk")
            self._tasks.add_task("email the report")
            self._tasks.complete_task("milk")
            block = self._tasks.format_tasks_for_prompt()
        self.assertIn("USER TASKS", block)
        self.assertIn("email the report", block)
        self.assertNotIn("buy milk", block)

    def test_prompt_block_empty_when_no_pending(self) -> None:
        with patch("agetha.app_config.get_settings", return_value=_PLAIN_SETTINGS):
            self.assertEqual(self._tasks.format_tasks_for_prompt(), "")

    def test_display_formatting(self) -> None:
        with patch("agetha.app_config.get_settings", return_value=_PLAIN_SETTINGS):
            self._tasks.add_task("buy milk")
            self._tasks.complete_task("milk")
        lines = self._tasks.format_tasks_for_display(self._tasks.get_tasks())
        self.assertTrue(any(line.startswith("[x]") for line in lines))


class TestCommandWiring(unittest.TestCase):
    NEW_COMMANDS = ("view_dreams", "add_task", "complete_task", "list_tasks")

    def test_valid_commands_registered(self) -> None:
        from agetha.core.ai_engine import VALID_COMMANDS
        for cmd in self.NEW_COMMANDS:
            with self.subTest(command=cmd):
                self.assertIn(cmd, VALID_COMMANDS)

    def test_guard_tiers_are_safe(self) -> None:
        from agetha.commands.command_guard import CommandGuard
        for cmd in self.NEW_COMMANDS:
            with self.subTest(command=cmd):
                self.assertEqual(CommandGuard.TIER_MAP.get(cmd), CommandGuard.SAFE)

    def test_handlers_registered(self) -> None:
        from agetha.commands.command_handlers import HANDLERS
        for cmd in self.NEW_COMMANDS:
            with self.subTest(command=cmd):
                self.assertIn(cmd, HANDLERS)

    def test_system_prompt_documents_commands(self) -> None:
        from agetha.core.ai_engine import SYSTEM_PROMPT, SYSTEM_PROMPT_FASTER
        for cmd in self.NEW_COMMANDS:
            with self.subTest(command=cmd):
                self.assertIn(cmd, SYSTEM_PROMPT)
                self.assertIn(cmd, SYSTEM_PROMPT_FASTER)


if __name__ == "__main__":
    unittest.main(verbosity=2)
