from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agetha.app_config as facade


class ConfigBoundaryTests(unittest.TestCase):
    def test_atomic_io_compatibility_exports_are_canonical(self) -> None:
        from agetha.config import io

        self.assertIs(facade.AtomicWriteError, io.AtomicWriteError)
        self.assertIs(facade._fsync_parent_directory, io.fsync_parent_directory)
        self.assertIs(facade._write_atomic_config_impl, io.write_atomic_config)

    def test_document_algorithms_live_behind_facade(self) -> None:
        from agetha.config import transactions

        original = "# keep\nVALUE = old\nUNKNOWN = yes\n"
        expected = "# keep\nVALUE = new\nUNKNOWN = yes\nADDED = 2\n"
        self.assertEqual(
            facade.render_config_document(original, {"VALUE": "new", "ADDED": "2"}),
            expected,
        )
        self.assertEqual(
            transactions.render_config_document(
                original,
                {"VALUE": "new", "ADDED": "2"},
                is_secret_key=facade._is_secret_key,
            ),
            expected,
        )

    def test_facade_patch_still_observes_writer_monkeypatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.txt"
            path.write_text("VALUE = old\n", encoding="utf-8")
            with (
                patch.object(facade, "CONFIG_PATH", path),
                patch.object(facade, "ensure_config_file", return_value=path),
                patch.object(facade, "get_settings"),
                patch.object(facade, "_write_atomic_config") as writer,
            ):
                ok, failed = facade.patch_config_keys({"VALUE": "new"})
            self.assertTrue(ok)
            self.assertEqual(failed, [])
            writer.assert_called_once_with(path, "VALUE = new\n")


if __name__ == "__main__":
    unittest.main()
