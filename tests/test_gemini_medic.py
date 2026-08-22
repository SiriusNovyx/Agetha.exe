from __future__ import annotations

import io
import builtins
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import medic_helper


def _output(function) -> str:
    stream = io.StringIO()
    with redirect_stdout(stream):
        function()
    return stream.getvalue().strip()


class GeminiMedicTests(unittest.TestCase):
    def test_gemini_only_configuration_is_reported_as_ready(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".env").write_text(
                "GEMINI_API_KEY=gemini-key-long-enough-for-medic\n",
                encoding="utf-8",
            )
            (root / "config.txt").write_text(
                "USE_LOCAL_AI = no\n"
                "ENABLE_GEMINI = yes\n"
                "ENABLE_OPENROUTER = no\n",
                encoding="utf-8",
            )

            with patch.object(medic_helper, "_MEDIC_DIR", root):
                self.assertEqual(_output(medic_helper.cmd_env_status), "GEMINI")
                self.assertEqual(_output(medic_helper.cmd_config_status), "GEMINI")

    def test_gemini_secret_in_config_is_reported_for_removal(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config.txt").write_text(
                "GEMINI_API_KEY = secret-must-move\n",
                encoding="utf-8",
            )

            with patch.object(medic_helper, "_MEDIC_DIR", root):
                result = _output(medic_helper.cmd_config_secrets)

            self.assertEqual(result, "KEYS_IN_CONFIG:GEMINI_API_KEY")

    def test_feature_import_check_includes_gemini_adapter(self) -> None:
        real_import = builtins.__import__

        def checked_import(name, *args, **kwargs):
            if name == "agetha.providers.gemini":
                raise ImportError("diagnostic gemini failure")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=checked_import):
            result = _output(medic_helper.cmd_feature_modules)

        self.assertIn(
            "agetha.providers.gemini:diagnostic gemini failure",
            result,
        )


if __name__ == "__main__":
    unittest.main()
