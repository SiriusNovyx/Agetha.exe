from __future__ import annotations

from pathlib import Path
import unittest

from agetha import __version__
from agetha.app_config import AppSettings, default_config_dict


ROOT = Path(__file__).resolve().parents[1]


class ReleaseVersionOwnershipTests(unittest.TestCase):
    def test_persisted_app_version_cannot_override_build_version(self) -> None:
        settings = AppSettings({"APP_VERSION": "5.7"})

        self.assertEqual(settings.app_version, __version__)

    def test_default_config_does_not_persist_build_version(self) -> None:
        self.assertNotIn("APP_VERSION", default_config_dict())

    def test_medic_reads_source_owned_package_version(self) -> None:
        source = (ROOT / "Medic_Checker.ps1").read_text(encoding="utf-8")
        start = source.index("function Get-AppVersion")
        end = source.index("\nfunction Write-Line", start)
        version_function = source[start:end]

        self.assertIn("agetha\\__init__.py", version_function)
        self.assertNotIn("Get-ConfigValue", version_function)


if __name__ == "__main__":
    unittest.main()
