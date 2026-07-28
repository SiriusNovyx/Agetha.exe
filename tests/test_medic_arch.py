from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import medic_helper


class MedicArchitectureTests(unittest.TestCase):
    def test_normalizes_x64_aliases(self) -> None:
        for value in ("AMD64", "x86_64", "x64"):
            with self.subTest(value=value):
                self.assertEqual(medic_helper.normalize_python_architecture(value), "AMD64")

    def test_normalizes_arm64_aliases(self) -> None:
        for value in ("ARM64", "aarch64"):
            with self.subTest(value=value):
                self.assertEqual(medic_helper.normalize_python_architecture(value), "ARM64")

    def test_build_platform_distinguishes_amd64_from_arm_host(self) -> None:
        self.assertEqual(medic_helper.architecture_from_build_platform("win-amd64"), "AMD64")
        self.assertEqual(medic_helper.architecture_from_build_platform("win-arm64"), "ARM64")

    def test_prism_process_is_reported_as_x64_not_native_arm(self) -> None:
        with (
            patch.object(medic_helper.platform, "machine", return_value="ARM64"),
            patch.object(medic_helper.sysconfig, "get_platform", return_value="win-amd64"),
            patch.object(
                medic_helper,
                "_windows_process_architectures",
                return_value=("AMD64", "ARM64"),
            ),
        ):
            info = medic_helper.get_python_architecture_info()

        self.assertEqual(info["python_arch"], "AMD64")
        self.assertEqual(info["native_arch"], "ARM64")

    def test_native_arm_python_remains_arm64(self) -> None:
        with (
            patch.object(medic_helper.platform, "machine", return_value="ARM64"),
            patch.object(medic_helper.sysconfig, "get_platform", return_value="win-arm64"),
            patch.object(
                medic_helper,
                "_windows_process_architectures",
                return_value=("", "ARM64"),
            ),
        ):
            info = medic_helper.get_python_architecture_info()

        self.assertEqual(info["python_arch"], "ARM64")
        self.assertEqual(info["native_arch"], "ARM64")

    def test_python_arch_command_prints_machine_readable_json(self) -> None:
        expected = {
            "python_arch": "AMD64",
            "native_arch": "ARM64",
            "build_platform": "win-amd64",
            "reported_machine": "ARM64",
            "pointer_bits": 64,
        }
        output = StringIO()
        with patch.object(medic_helper, "get_python_architecture_info", return_value=expected):
            with redirect_stdout(output):
                medic_helper.cmd_python_arch()

        self.assertEqual(json.loads(output.getvalue()), expected)


if __name__ == "__main__":
    unittest.main()
