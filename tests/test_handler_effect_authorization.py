"""Effect-time authorization with real policies and fake external primitives."""

from __future__ import annotations

import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agetha.commands.handlers import files, system
from agetha.commands import system_commands
from agetha.commands.handlers.support import CAPABILITY_AUTHORIZATION, DispatchCtx
from agetha.core.capabilities import Capability, CapabilityController, CapabilityPolicy, CapabilityProfile


class FakeProcess:
    def __init__(self, effects, returncode=0):
        self.effects = effects
        self.returncode = returncode
        self.wait_error = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.effects.append("closed")

    def communicate(self, timeout=None):
        self.effects.append("wait")
        if self.wait_error is not None and timeout is not None:
            raise self.wait_error
        return "", ""

    def kill(self):
        self.effects.append("killed")

    def wait(self, timeout=None):
        self.effects.append("reaped" if timeout is None else "wait")
        if self.wait_error is not None and timeout is not None:
            raise self.wait_error
        return self.returncode


class InvalidatingText(str):
    def __new__(cls, value, controller):
        instance = super().__new__(cls, value)
        instance.controller = controller
        return instance

    def strip(self, *args):
        self.controller.begin_compact_transition()
        return super().strip(*args)


class TestHandlerEffectAuthorization(unittest.TestCase):
    def setUp(self):
        self.controller = CapabilityController(CapabilityPolicy(
            CapabilityProfile.FULL, {"ENABLE_COMMAND_EXECUTION": True},
        ))
        self.token = self.controller.authorize(Capability.ADVANCED_OS_INTEGRATION)
        self.effects, self.errors, self.successes, self.spoken = [], [], [], []
        self.app = SimpleNamespace(
            _capabilities=self.controller, root=object(),
            _show_op_error=self.errors.append, _show_op_success=self.successes.append,
            _speak_and_continue=lambda segments, *_args: self.spoken.extend(segments),
        )
        self.ctx = DispatchCtx(None, "happy", [{"text": "Done."}], False)
        self.process = FakeProcess(self.effects)

        def spawn(*_args, **_kwargs):
            self.effects.append("spawn")
            return self.process

        self.enterContext(patch.object(files.subprocess, "Popen", side_effect=spawn))
        self.enterContext(patch.object(files.subprocess, "run", side_effect=spawn))
        self.enterContext(patch.object(files.os, "startfile", create=True,
                                      side_effect=lambda *_args: self.effects.append("open")))
        self.enterContext(patch.object(system, "get_settings", return_value=SimpleNamespace(enable_theme_control=True)))
        self.enterContext(patch.object(system, "_schedule_app_ui", side_effect=lambda _app, cb: cb()))
        self.enterContext(patch("agetha.core.audit_log.log_audit"))
        self.enterContext(patch("agetha.platform.win_integration.set_theme",
                                side_effect=lambda *_args, **_kwargs: (self.effects.append("theme") or True, "changed")))
        self.enterContext(patch("agetha.platform.win_integration.rollback_theme",
                                side_effect=lambda: (self.effects.append("rollback") or True, "restored")))

    def test_downgrade_during_command_parsing_prevents_process_creation(self):
        def parse(*_args, **_kwargs):
            self.controller.begin_compact_transition()
            return ["echo", "safe"]

        with patch.object(files.shlex, "split", side_effect=parse):
            files.handle_run_command(self.app, {"cmd": "echo safe", CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
        self.assertEqual(self.effects, [])
        self.assertTrue(self.errors)
        self.assertNotIn("Done.", [s["text"] for s in self.spoken])

    def test_downgrade_during_path_preparation_prevents_file_launch(self):
        with patch.object(files, "IS_WINDOWS", True):
            files.handle_open_file(self.app, {
                "path": InvalidatingText("fake.txt", self.controller), CAPABILITY_AUTHORIZATION: self.token,
            }, self.ctx)
        self.assertEqual(self.effects, [])
        self.assertTrue(self.errors)
        self.assertNotIn("Done.", [s["text"] for s in self.spoken])

    def test_downgrade_during_theme_preparation_prevents_change(self):
        system.handle_set_theme(self.app, {
            "mode": InvalidatingText("dark", self.controller), CAPABILITY_AUTHORIZATION: self.token,
        }, self.ctx)
        self.assertEqual(self.effects, [])
        self.assertEqual(self.successes, [])
        self.assertTrue(self.errors)
        self.assertNotIn("Done.", [s["text"] for s in self.spoken])

    def test_downgrade_during_rollback_preparation_prevents_restore(self):
        system.handle_set_theme(self.app, {
            "mode": InvalidatingText("rollback", self.controller), CAPABILITY_AUTHORIZATION: self.token,
        }, self.ctx)
        self.assertEqual(self.effects, [])
        self.assertEqual(self.successes, [])
        self.assertTrue(self.errors)

    def test_missing_authorization_blocks_each_verified_effect_site(self):
        for handler, response in (
            (files.handle_run_command, {"cmd": "echo safe"}),
            (files.handle_open_file, {"path": "fake.txt"}),
            (system.handle_set_theme, {"mode": "dark"}),
        ):
            with self.subTest(handler=handler.__name__), patch.object(files, "IS_WINDOWS", True):
                self.effects.clear()
                handler(self.app, response, self.ctx)
                self.assertEqual(self.effects, [])

    def test_authorized_command_keeps_success_and_closes_process(self):
        files.handle_run_command(self.app, {"cmd": "echo safe", CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
        self.assertEqual(self.effects, ["spawn", "wait", "closed"])
        self.assertEqual(self.errors, [])
        self.assertEqual(self.spoken, self.ctx.segments)

    def test_command_wait_does_not_hold_capability_lock(self):
        completed = threading.Event()
        observed = []
        transition = threading.Thread(target=lambda: (self.controller.begin_compact_transition(), completed.set()), daemon=True)

        def wait(timeout=None):
            transition.start()
            observed.append(completed.wait(1))
            return "", ""

        self.process.communicate = wait
        try:
            files.handle_run_command(self.app, {"cmd": "echo safe", CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
        finally:
            if transition.ident is not None:
                transition.join(2)
        self.assertEqual(observed, [True], "profile downgrade was blocked by process wait")
        self.assertFalse(transition.is_alive())
        self.assertEqual(self.spoken, self.ctx.segments)

    def test_command_timeout_kills_reaps_and_reports_failure(self):
        self.process.wait_error = files.subprocess.TimeoutExpired("fake", 15)
        with patch.object(files, "IS_WINDOWS", True):
            files.handle_run_command(self.app, {"cmd": "echo safe", CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
        self.assertEqual(self.effects, ["spawn", "wait", "killed", "wait", "closed"])
        self.assertTrue(self.errors)
        self.assertNotIn("Done.", [s["text"] for s in self.spoken])

    def test_posix_timeout_does_not_wait_for_descendants_to_close_pipes(self):
        def communicate(timeout=None):
            self.effects.append("wait" if timeout is not None else "descendant pipe drain")
            if timeout is not None:
                raise files.subprocess.TimeoutExpired("fake", timeout)
            # A descendant still owns the pipe: an unbounded drain cannot finish.
            raise AssertionError("attempted to wait for inherited descendant pipes")

        self.process.communicate = communicate
        with patch.object(files, "IS_WINDOWS", False):
            files.handle_run_command(self.app, {"cmd": "echo safe", CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
        self.assertEqual(self.effects, ["spawn", "wait", "killed", "reaped", "closed"])
        self.assertTrue(self.errors)
        self.assertNotIn("Done.", [s["text"] for s in self.spoken])

    def test_nonzero_command_exit_still_reports_failure(self):
        self.process.returncode = 7
        files.handle_run_command(self.app, {"cmd": "echo safe", CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
        self.assertTrue(self.errors)
        self.assertNotIn("Done.", [s["text"] for s in self.spoken])

    def test_authorized_file_launch_keeps_each_platform_route(self):
        for windows, platform in ((True, "Windows"), (False, "Linux"), (False, "Darwin")):
            with self.subTest(platform=platform), patch.object(files, "IS_WINDOWS", windows), patch.object(files.platform, "system", return_value=platform):
                self.effects.clear()
                self.spoken.clear()
                files.handle_open_file(self.app, {"path": "fake.txt", CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
                self.assertEqual(self.effects, ["open"] if windows else ["spawn"])
                self.assertEqual(self.spoken, self.ctx.segments)

    def test_authorized_theme_change_and_rollback_keep_success(self):
        for mode in ("dark", "rollback"):
            with self.subTest(mode=mode):
                self.effects.clear()
                self.spoken.clear()
                system.handle_set_theme(self.app, {"mode": mode, CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
                self.assertEqual(self.effects, ["theme"] if mode == "dark" else ["rollback"])
                self.assertEqual(self.spoken, self.ctx.segments)
                self.assertTrue(self.successes)


class TestFolderWallpaperAuthorization(unittest.TestCase):
    def setUp(self):
        # All path preparation uses synthetic files, never an actual user path.
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.folder = Path(self.temp)
        self.wallpaper = self.folder / "synthetic.bmp"
        self.wallpaper.write_bytes(b"fake wallpaper")
        self.controller = CapabilityController(CapabilityPolicy(
            CapabilityProfile.FULL, {"ENABLE_COMMAND_EXECUTION": True},
        ))
        self.token = self.controller.authorize(Capability.ADVANCED_OS_INTEGRATION)
        self.effects, self.errors, self.spoken, self.targets = [], [], [], []
        self.app = SimpleNamespace(
            _capabilities=self.controller, _show_op_error=self.errors.append,
            _speak_and_continue=lambda segments, *_args: self.spoken.extend(segments),
        )
        self.ctx = DispatchCtx(None, "happy", [{"text": "Done."}], False)
        self.process = FakeProcess(self.effects)

        def launch(args, **_kwargs):
            self.effects.append("spawn")
            self.targets.append(args)
            return self.process

        def open_path(path):
            self.effects.append("folder")
            self.targets.append(path)

        def wallpaper(*args):
            self.effects.append("wallpaper")
            self.targets.append(args)
            return 1

        self.enterContext(patch.object(system_commands, "IS_WINDOWS", True))
        self.enterContext(patch.object(system_commands, "IS_MACOS", False))
        self.enterContext(patch.object(system_commands, "IS_LINUX", False))
        self.enterContext(patch.object(system_commands.os, "startfile", create=True, side_effect=open_path))
        self.enterContext(patch.object(system_commands.subprocess, "Popen", side_effect=launch))
        self.enterContext(patch.object(system_commands.subprocess, "run", side_effect=launch))
        self.enterContext(patch.object(system_commands.shutil, "which", return_value="fake-gsettings"))
        self.enterContext(patch("ctypes.windll", create=True,
                                new=SimpleNamespace(user32=SimpleNamespace(SystemParametersInfoW=wallpaper))))

    def _linux(self):
        self.enterContext(patch.object(system_commands, "IS_WINDOWS", False))
        self.enterContext(patch.object(system_commands, "IS_LINUX", True))

    def _assert_blocked(self):
        self.assertEqual(self.effects, [])
        self.assertTrue(self.errors)
        self.assertNotIn("Done.", [s["text"] for s in self.spoken])

    def test_folder_path_preparation_downgrade_blocks_launch(self):
        files.handle_open_folder(self.app, {
            "path": InvalidatingText(str(self.folder), self.controller), CAPABILITY_AUTHORIZATION: self.token,
        }, self.ctx)
        self._assert_blocked()

    def test_wallpaper_path_preparation_downgrade_blocks_change(self):
        system.handle_set_wallpaper(self.app, {
            "path": InvalidatingText(str(self.wallpaper), self.controller), CAPABILITY_AUTHORIZATION: self.token,
        }, self.ctx)
        self._assert_blocked()

    def test_folder_invalidation_inside_helper_prevents_effect(self):
        exists = Path.exists

        def invalidate(path):
            if path == self.folder:
                self.controller.begin_compact_transition()
            return exists(path)

        with patch.object(Path, "exists", invalidate):
            files.handle_open_folder(self.app, {"path": str(self.folder), CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
        self._assert_blocked()

    def test_wallpaper_invalidation_during_resolution_prevents_effect(self):
        resolve = Path.resolve

        def invalidate(path, *args, **kwargs):
            if path == self.wallpaper:
                self.controller.begin_compact_transition()
            return resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", invalidate):
            system.handle_set_wallpaper(self.app, {"path": str(self.wallpaper), CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
        self._assert_blocked()

    def test_missing_authorization_blocks_both_effects(self):
        for handler, path in ((files.handle_open_folder, self.folder), (system.handle_set_wallpaper, self.wallpaper)):
            with self.subTest(handler=handler.__name__):
                self.effects.clear()
                self.errors.clear()
                self.spoken.clear()
                handler(self.app, {"path": str(path)}, self.ctx)
                self._assert_blocked()

    def test_authorized_windows_folder_and_wallpaper_keep_routes(self):
        files.handle_open_folder(self.app, {"path": str(self.wallpaper), CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
        system.handle_set_wallpaper(self.app, {"path": str(self.wallpaper), CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
        self.assertEqual(self.effects, ["folder", "wallpaper"])
        self.assertEqual(self.targets, [str(self.folder), (20, 0, str(self.wallpaper.resolve()), 3)])
        self.assertEqual(self.errors, [])
        self.assertEqual(self.spoken, self.ctx.segments * 2)

    def test_authorized_linux_routes_keep_arguments_and_close_processes(self):
        self._linux()
        files.handle_open_folder(self.app, {"path": str(self.folder), CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
        system.handle_set_wallpaper(self.app, {"path": str(self.wallpaper), CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
        self.assertEqual(self.targets, [
            ["xdg-open", str(self.folder)],
            ["gsettings", "set", "org.gnome.desktop.background", "picture-uri", f"file://{self.wallpaper.resolve()}"],
        ])
        self.assertEqual(self.effects, ["spawn", "wait", "closed", "spawn", "wait", "closed"])
        self.assertEqual(self.errors, [])
        self.assertEqual(self.spoken, self.ctx.segments * 2)

    def test_linux_waits_do_not_hold_capability_lock(self):
        self._linux()
        for handler, path in ((files.handle_open_folder, self.folder), (system.handle_set_wallpaper, self.wallpaper)):
            with self.subTest(handler=handler.__name__):
                self.controller = CapabilityController(CapabilityPolicy(
                    CapabilityProfile.FULL, {"ENABLE_COMMAND_EXECUTION": True},
                ))
                self.app._capabilities = self.controller
                self.token = self.controller.authorize(Capability.ADVANCED_OS_INTEGRATION)
                completed, observed = threading.Event(), []
                transition = threading.Thread(target=lambda: (self.controller.begin_compact_transition(), completed.set()), daemon=True)

                def wait(timeout=None):
                    transition.start()
                    observed.append(completed.wait(1))
                    return 0

                self.process.wait = wait
                try:
                    handler(self.app, {"path": str(path), CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
                finally:
                    if transition.ident is not None:
                        transition.join(2)
                self.assertEqual(observed, [True], "profile transition was blocked by child wait")
                self.assertFalse(transition.is_alive())

    def test_linux_timeout_kills_and_closes_launched_process(self):
        self._linux()
        self.process.wait_error = system_commands.subprocess.TimeoutExpired("synthetic", 5)
        for handler, path in ((files.handle_open_folder, self.folder), (system.handle_set_wallpaper, self.wallpaper)):
            with self.subTest(handler=handler.__name__):
                self.effects.clear()
                self.errors.clear()
                handler(self.app, {"path": str(path), CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
                self.assertEqual(self.effects, ["spawn", "wait", "killed", "closed"])
                self.assertTrue(self.errors)

    def test_native_launch_errors_still_reach_ui(self):
        with patch.object(system_commands.os, "startfile", side_effect=OSError("synthetic launch failure")):
            files.handle_open_folder(self.app, {"path": str(self.folder), CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
        self.assertTrue(self.errors)

    def test_standalone_helpers_keep_existing_success_behavior(self):
        self.assertTrue(system_commands.open_folder(str(self.folder)).startswith("[opened folder:"))
        self.assertTrue(system_commands.set_wallpaper(str(self.wallpaper)).startswith("[wallpaper set:"))
        self.assertEqual(self.effects, ["folder", "wallpaper"])


if __name__ == "__main__":
    unittest.main()
