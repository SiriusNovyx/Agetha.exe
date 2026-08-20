from __future__ import annotations

import importlib
import inspect
import os
import shutil
import sys
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import agetha
import agetha.app_config as live_app_config
import agetha.utils as live_utils
from agetha.platform import unicode_typing
from agetha.platform import windows_notify
from agetha.platform import window_control
from agetha.core.capabilities import CapabilityPolicy, CapabilityProfile
from agetha.core.capability_consent import CapabilityConsentFlow, ConsentState
from agetha.platform.self_identity import (
    is_self_process_identity,
    is_self_window_identity,
    self_executable_names,
)


_MISSING = object()
_TEST_DIR = Path(__file__).resolve().parent


@contextmanager
def _fresh_frozen_config_modules(
    executable: Path,
    *,
    include_utils: bool = False,
):
    """Import frozen path owners, then restore canonical modules exactly."""

    names = ("agetha.app_config", "agetha.utils")
    saved_modules = {name: sys.modules.get(name, _MISSING) for name in names}
    saved_attributes = {
        name.rsplit(".", 1)[-1]: getattr(
            agetha,
            name.rsplit(".", 1)[-1],
            _MISSING,
        )
        for name in names
    }
    try:
        for name in reversed(names):
            sys.modules.pop(name, None)
        for attribute in saved_attributes:
            if hasattr(agetha, attribute):
                delattr(agetha, attribute)
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", str(executable)),
        ):
            config = importlib.import_module("agetha.app_config")
            utils = importlib.import_module("agetha.utils") if include_utils else None
            yield SimpleNamespace(config=config, utils=utils)
    finally:
        for name in reversed(names):
            sys.modules.pop(name, None)
        for name in names:
            saved = saved_modules[name]
            if saved is not _MISSING:
                sys.modules[name] = saved
        for attribute, saved in saved_attributes.items():
            if saved is _MISSING:
                if hasattr(agetha, attribute):
                    delattr(agetha, attribute)
            else:
                setattr(agetha, attribute, saved)


@contextmanager
def _fresh_module(name: str):
    """Import one child module without leaking its module/package globals."""

    package_name, attribute = name.rsplit(".", 1)
    package = importlib.import_module(package_name)
    saved_module = sys.modules.get(name, _MISSING)
    saved_attribute = getattr(package, attribute, _MISSING)
    try:
        sys.modules.pop(name, None)
        if hasattr(package, attribute):
            delattr(package, attribute)
        yield importlib.import_module(name)
    finally:
        sys.modules.pop(name, None)
        if saved_module is not _MISSING:
            sys.modules[name] = saved_module
        if saved_attribute is _MISSING:
            if hasattr(package, attribute):
                delattr(package, attribute)
        else:
            setattr(package, attribute, saved_attribute)


def _owned_runtime_directory() -> Path:
    path = _TEST_DIR / f".frozen-runtime-{uuid.uuid4().hex}"
    path.mkdir()
    return path


def _remove_owned_runtime_directory(path: Path) -> None:
    resolved = path.resolve()
    if resolved.parent != _TEST_DIR.resolve() or not resolved.name.startswith(
        ".frozen-runtime-"
    ):
        raise AssertionError(f"refusing to clean unexpected test path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


class TestFrozenLauncher(unittest.TestCase):
    def test_frozen_shortcut_launches_the_executable_without_source_arguments(self) -> None:
        install_dir = (_TEST_DIR / "frozen-install").resolve()
        executable = install_dir / "Agetha.exe"

        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", str(executable)),
            patch.object(windows_notify, "BASE_DIR", install_dir),
            patch.object(Path, "is_file", return_value=True),
        ):
            target, arguments, working_dir = windows_notify._launcher_paths()

        self.assertEqual(target, str(executable))
        self.assertEqual(arguments, "")
        self.assertEqual(working_dir, str(install_dir))
        self.assertNotIn("main.py", f"{target} {arguments}")

    def test_source_launcher_is_independent_of_the_current_working_directory(self) -> None:
        source_root = (_TEST_DIR / "source-install").resolve()
        interpreter = source_root / "python.exe"
        previous_cwd = Path.cwd()

        try:
            os.chdir(Path(previous_cwd.anchor))
            with (
                patch.object(sys, "frozen", False, create=True),
                patch.object(sys, "executable", str(interpreter)),
                patch.object(windows_notify, "BASE_DIR", source_root),
                patch.object(Path, "is_file", return_value=False),
            ):
                target, arguments, working_dir = windows_notify._launcher_paths()
        finally:
            os.chdir(previous_cwd)

        self.assertEqual(target, str(interpreter))
        self.assertEqual(arguments, f'"{source_root / "main.py"}"')
        self.assertEqual(working_dir, str(source_root))

    def test_sys_frozen_patch_is_restored(self) -> None:
        sentinel = object()
        before = getattr(sys, "frozen", sentinel)
        with patch.object(sys, "frozen", True, create=True):
            self.assertIs(getattr(sys, "frozen"), True)
        self.assertIs(getattr(sys, "frozen", sentinel), before)


class TestFrozenPathsAndConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime_dir = _owned_runtime_directory()
        self.addCleanup(_remove_owned_runtime_directory, self.runtime_dir)
        self.executable = self.runtime_dir / "Agetha.exe"

    def test_frozen_import_resolves_config_and_resources_beside_executable(
        self,
    ) -> None:
        (self.runtime_dir / "config.txt").write_text(
            "COMPACT_MODE = yes\n",
            encoding="utf-8",
        )
        before_executable = sys.executable
        before_config = sys.modules["agetha.app_config"]
        before_utils = sys.modules["agetha.utils"]

        with _fresh_frozen_config_modules(
            self.executable,
            include_utils=True,
        ) as frozen:
            self.assertIsNot(frozen.config, before_config)
            self.assertIsNot(frozen.utils, before_utils)
            self.assertEqual(frozen.config.BASE_DIR, self.runtime_dir)
            self.assertEqual(
                frozen.config.CONFIG_PATH,
                self.runtime_dir / "config.txt",
            )
            self.assertEqual(frozen.config.ENV_PATH, self.runtime_dir / ".env")
            self.assertEqual(frozen.utils.BASE_DIR, self.runtime_dir)
            self.assertEqual(frozen.utils.ASSETS, self.runtime_dir / "assets")
            self.assertEqual(
                frozen.utils.FONT_PATH,
                self.runtime_dir / "assets" / "barrio.ttf",
            )
            self.assertEqual(
                frozen.utils.ICON_PATH,
                self.runtime_dir / "assets" / "icon.ico",
            )

        self.assertEqual(sys.executable, before_executable)
        self.assertIs(sys.modules["agetha.app_config"], before_config)
        self.assertIs(sys.modules["agetha.utils"], before_utils)
        self.assertIs(agetha.app_config, live_app_config)
        self.assertIs(agetha.utils, live_utils)

    def test_frozen_compact_default_and_full_setting_survive_module_restart(
        self,
    ) -> None:
        config_path = self.runtime_dir / "config.txt"

        with _fresh_frozen_config_modules(self.executable) as frozen:
            first = frozen.config.get_settings(reload=True)
            self.assertTrue(first.compact_mode)
            self.assertEqual(
                CapabilityPolicy.from_settings(first).profile,
                CapabilityProfile.COMPACT,
            )
            self.assertTrue(config_path.is_file())
            self.assertIn(
                "COMPACT_MODE = yes",
                config_path.read_text(encoding="utf-8"),
            )

            self.assertTrue(
                frozen.config.patch_config_key("COMPACT_MODE", "no")
            )
            persisted = frozen.config.get_settings(reload=True)
            self.assertFalse(persisted.compact_mode)

        # A fresh module import models process-level settings initialization on
        # the next frozen launch; no cached AppSettings object is reused.
        with _fresh_frozen_config_modules(self.executable) as restarted:
            loaded = restarted.config.get_settings()
            flow = CapabilityConsentFlow(initial_full=not loaded.compact_mode)

            self.assertFalse(loaded.compact_mode)
            self.assertEqual(
                CapabilityPolicy.from_settings(loaded).profile,
                CapabilityProfile.FULL,
            )
            self.assertEqual(flow.snapshot.state, ConsentState.FULL)
            self.assertIn(
                "COMPACT_MODE = no",
                config_path.read_text(encoding="utf-8"),
            )

    def test_frozen_consent_message_needs_no_source_or_asset_file_at_runtime(
        self,
    ) -> None:
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", str(self.executable)),
            _fresh_module("agetha.platform.full_mode_consent") as consent,
        ):
            # Frozen Python modules are bundled code, not sibling .py files.
            # Removing this metadata and forbidding file access catches a
            # regression that tries to load the warning from source/assets.
            consent.__dict__.pop("__file__", None)
            sent: list[tuple[object, str]] = []
            target = consent.ConsentDemoTarget(
                pid=42,
                process_name="notepad.exe",
                created_at=10.0,
                hwnd=77,
                bounds=(0, 0, 640, 480),
                foreground_hwnd=77,
                process_alive=True,
                window_valid=True,
            )
            typer = consent.FixedConsentTyper(
                send_static=lambda locked, text: sent.append((locked, text)) or True,
                authorized=lambda locked: locked == target,
            )

            with (
                patch("builtins.open", side_effect=AssertionError("unexpected file read")),
                patch.object(
                    Path,
                    "read_text",
                    side_effect=AssertionError("unexpected resource read"),
                ),
                patch.object(
                    Path,
                    "is_file",
                    side_effect=AssertionError("unexpected resource probe"),
                ),
            ):
                self.assertTrue(typer(target))
                message = consent.CONSENT_DEMO_MESSAGE

            self.assertEqual(sent, [(target, message)])
            self.assertTrue(
                message.startswith("ARE YOU REALLY SURE YOU WANT TO CONTINUE THIS?")
            )
            self.assertIn("Safety restrictions will remain enabled.", message)
            self.assertEqual(
                tuple(
                    inspect.signature(
                        consent.FullModeConsentDemo.run_full_mode_consent_demo
                    ).parameters
                ),
                ("self",),
            )


class TestFrozenSelfIdentity(unittest.TestCase):
    def test_frozen_distribution_names_are_exact_and_case_insensitive(self) -> None:
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", r"C:\Program Files\Agetha\Agetha.exe"),
        ):
            names = self_executable_names()
            self.assertIn("agetha.exe", names)
            self.assertIn("main.exe", names)
            self.assertTrue(is_self_process_identity(process_name="AGETHA.EXE"))
            self.assertTrue(is_self_process_identity(process_name="main.exe"))
            self.assertFalse(is_self_process_identity(process_name="agetha-helper.exe"))
            self.assertFalse(is_self_process_identity(process_name="not-main.exe"))
            self.assertFalse(is_self_process_identity(process_name="python.exe"))

    def test_current_pid_and_explicit_own_hwnd_are_authoritative(self) -> None:
        self.assertTrue(
            is_self_process_identity(process_name="notepad.exe", process_id=os.getpid())
        )
        self.assertFalse(
            is_self_process_identity(process_name="notepad.exe", process_id=os.getpid() + 1)
        )
        self.assertFalse(
            is_self_process_identity(process_name="main.exe", process_id=os.getpid() + 1)
        )
        self.assertTrue(
            is_self_window_identity(
                process_name="notepad.exe",
                process_id=os.getpid() + 1,
                window_handle=4242,
                own_window_handles=(4242,),
            )
        )

    def test_window_control_does_not_use_broad_frozen_name_matching(self) -> None:
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", r"C:\Apps\main.exe"),
        ):
            self.assertTrue(window_control.is_self_process_target("main.exe"))
            self.assertTrue(window_control.is_self_process_target("Agetha.exe"))
            self.assertFalse(window_control.is_self_process_target("main.exe.backup"))
            self.assertFalse(window_control.is_self_process_target("Agetha Updater.exe"))
            self.assertFalse(window_control.is_self_process_target("python.exe"))

    def test_window_effects_refuse_the_current_pid_before_calling_windows(self) -> None:
        calls: list[tuple] = []
        fake_user32 = SimpleNamespace(
            IsWindow=lambda _hwnd: True,
            PostMessageW=lambda *args: calls.append(args),
        )
        with (
            patch.object(window_control, "IS_WINDOWS", True),
            patch.object(window_control, "_user32", fake_user32, create=True),
            patch.object(window_control, "_window_pid", return_value=os.getpid()),
            patch.object(window_control.subprocess, "run") as run,
        ):
            close_ok, _close_message = window_control.close_window(1234)
            kill_ok, _kill_message = window_control.kill_process_by_hwnd(1234)

        self.assertFalse(close_ok)
        self.assertFalse(kill_ok)
        self.assertEqual(calls, [])
        run.assert_not_called()

    def test_unicode_typing_rejects_current_pid_in_frozen_main_exe(self) -> None:
        target = unicode_typing.TypingTarget(
            stable_id=f"win:9876:{os.getpid()}",
            title="Agetha",
            process_name="main.exe",
            window_handle=9876,
        )
        native_calls: list[str] = []
        dependencies = unicode_typing.UnicodeTypingDependencies(
            platform_name="windows",
            session_type="desktop",
            get_focused_target=lambda: target,
            send_native_unicode=lambda text: native_calls.append(text),
        )

        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", r"C:\Apps\main.exe"),
        ):
            result = unicode_typing.UnicodeTypingEngine(dependencies).type_text("safe text")

        self.assertFalse(result.success)
        self.assertEqual(result.method, "target-rejected")
        self.assertEqual(native_calls, [])


if __name__ == "__main__":
    unittest.main()
