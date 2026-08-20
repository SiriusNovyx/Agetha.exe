"""Fail-closed persistence tests for Compact Mode."""

from __future__ import annotations

import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agetha.app_config import (  # noqa: E402
    arm_compact_mode_fail_closed,
    clear_compact_mode_fail_closed,
    compact_mode_fail_closed_required,
    parse_config_file,
)
from agetha import app_config  # noqa: E402


class TestCompactModeFailClosedMarker(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(__file__).resolve().parent / (
            f".compact-mode-safety-{uuid.uuid4().hex}"
        )
        self.directory.mkdir()
        self.config_path = self.directory / "config.txt"
        self.config_path.write_text("COMPACT_MODE = no\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_marker_overrides_stale_full_config_on_restart(self) -> None:
        self.assertTrue(arm_compact_mode_fail_closed(self.config_path))
        self.assertTrue(compact_mode_fail_closed_required(self.config_path))

        settings = parse_config_file(self.config_path)

        self.assertEqual(settings["COMPACT_MODE"], "yes")

    def test_explicit_clear_allows_persisted_full_after_new_consent(self) -> None:
        self.assertTrue(arm_compact_mode_fail_closed(self.config_path))
        self.assertTrue(clear_compact_mode_fail_closed(self.config_path))

        settings = parse_config_file(self.config_path)

        self.assertFalse(compact_mode_fail_closed_required(self.config_path))
        self.assertEqual(settings["COMPACT_MODE"], "no")

    def test_user_state_fallback_survives_unwritable_install_directory(self) -> None:
        state_root = self.directory / "local-state"
        real_atomic_write = app_config._write_atomic_config

        def fail_primary(path: Path, content: str) -> None:
            if Path(path).parent == self.config_path.parent:
                raise OSError("install directory is read-only")
            real_atomic_write(Path(path), content)

        with patch.object(app_config, "CONFIG_PATH", self.config_path), patch.dict(
            app_config.os.environ,
            {"LOCALAPPDATA": str(state_root)},
        ), patch.object(app_config, "_write_atomic_config", side_effect=fail_primary):
            self.assertTrue(arm_compact_mode_fail_closed())
            self.assertTrue(compact_mode_fail_closed_required())
            self.assertEqual(parse_config_file()["COMPACT_MODE"], "yes")
            self.assertTrue(clear_compact_mode_fail_closed())
            self.assertFalse(compact_mode_fail_closed_required())


if __name__ == "__main__":
    unittest.main()
