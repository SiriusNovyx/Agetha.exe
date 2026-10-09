from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agetha.commands.command_handlers import HANDLERS
from agetha.commands.handlers import files, memory_presentation, system
from agetha.commands.handlers.support import CAPABILITY_AUTHORIZATION, DispatchCtx
from agetha.core.capabilities import Capability, CapabilityController, CapabilityPolicy, CapabilityProfile


class HandlerDomainOwnershipTests(unittest.TestCase):
    def test_memory_presentation_handlers_have_one_domain_owner(self) -> None:
        expected = {
            "change_mood",
            "clear_memory",
            "view_memory",
            "search_memory",
            "glitch_overlay",
            "read_notepad",
            "play_virus_trivia",
            "view_dreams",
            "add_task",
            "complete_task",
            "list_tasks",
            "view_emotions",
            "clear_emotions",
        }
        self.assertEqual(
            {HANDLERS[name].__module__ for name in expected},
            {"agetha.commands.handlers.memory_presentation"},
        )

    def test_web_context_handler_has_one_domain_owner(self) -> None:
        self.assertEqual(
            {HANDLERS[name].__module__ for name in {"search_web", "fetch_webpage"}},
            {"agetha.commands.handlers.web_context"},
        )

    def test_file_and_local_os_handlers_have_one_domain_owner(self) -> None:
        expected = {
            "request_path", "create_folder", "create_file", "delete_file",
            "rename_file", "list_dir", "list_directory", "set_clipboard",
            "copy_to_clipboard", "play_sound", "take_screenshot",
            "show_notification", "run_command", "open_file", "open_folder",
            "write_file",
        }
        self.assertEqual(
            {HANDLERS[name].__module__ for name in expected},
            {"agetha.commands.handlers.files"},
        )

    def test_system_handlers_have_one_domain_owner(self) -> None:
        expected = {
            "set_volume", "set_wallpaper", "search_files", "lock_screen",
            "shutdown", "restart", "set_autostart", "open_settings",
            "set_theme", "recycle_bin_status",
        }
        self.assertEqual(
            {HANDLERS[name].__module__ for name in expected},
            {"agetha.commands.handlers.system"},
        )


class DeferredErrorCallbackTests(unittest.TestCase):
    def test_operation_errors_survive_deferred_ui_delivery(self) -> None:
        settings = SimpleNamespace(
            enable_autostart_control=True, enable_theme_control=True,
            enable_tasks=True, enable_emotion_engine=True,
        )
        cases = (
            (system, "set_autostart", "agetha.platform.autostart.enable", {}),
            (system, "open_settings", "agetha.platform.win_integration.open_settings", {}),
            (system, "set_theme", "agetha.platform.win_integration.set_theme", {"mode": "dark"}),
            (system, "recycle_bin_status", "agetha.platform.win_integration.recycle_bin_status", {}),
            (memory_presentation, "add_task", "agetha.features.tasks.add_task", {"text": "buy milk"}),
            (memory_presentation, "clear_emotions", "agetha.core.emotion_engine.reset_state", {}),
        )
        for domain, command, dependency, response in cases:
            with self.subTest(command=command):
                callbacks, errors = [], []
                controller = CapabilityController(CapabilityPolicy(
                    CapabilityProfile.FULL, {"ENABLE_COMMAND_EXECUTION": True},
                ))
                app = SimpleNamespace(
                    _capabilities=controller,
                    _show_op_error=errors.append,
                    _speak_and_continue=lambda *args: None,
                )
                with (
                    patch.object(domain, "get_settings", return_value=settings),
                    patch.object(domain, "_schedule_app_ui", side_effect=lambda app, cb: callbacks.append(cb)),
                    patch("agetha.core.emotional_history.clear_history"),
                    patch(dependency, side_effect=OSError("synthetic failure")),
                ):
                    HANDLERS[command](app, {
                        **response, CAPABILITY_AUTHORIZATION: controller.authorize(Capability.ADVANCED_OS_INTEGRATION),
                    }, DispatchCtx(None, "neutral", [], False))
                self.assertEqual(errors, [])
                self.assertEqual(len(callbacks), 1)
                try:
                    callbacks[0]()
                except NameError as exc:
                    self.fail(f"Deferred error reporting lost its exception: {exc}")
                self.assertEqual(len(errors), 1)
                self.assertIn("synthetic failure", errors[0])


class ExclusiveFileCreationTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(folder)
        self.errors, self.spoken = [], []
        self.controller = CapabilityController(CapabilityPolicy(
            CapabilityProfile.FULL, {"ENABLE_COMMAND_EXECUTION": True},
        ))
        self.token = self.controller.authorize(Capability.ADVANCED_OS_INTEGRATION)
        self.app = SimpleNamespace(
            _capabilities=self.controller,
            _show_op_error=self.errors.append,
            _speak_and_continue=lambda segments, *args: self.spoken.extend(segments),
        )
        self.ctx = DispatchCtx(None, "neutral", [{"text": "File created."}], False)

    def test_existing_file_is_preserved_for_both_path_forms(self) -> None:
        target = self.root / "existing.txt"
        original = b"existing private content\r\n"
        responses = (
            {"file_path": str(target)},
            {"path": str(self.root), "file_name": target.name},
        )
        for response in responses:
            with self.subTest(response=response):
                target.write_bytes(original)
                self.errors.clear()
                self.spoken.clear()
                files.handle_create_file(self.app, {
                    **response, "content": "replacement",
                    CAPABILITY_AUTHORIZATION: self.token,
                }, self.ctx)
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(len(self.errors), 1)
                self.assertNotIn("File created.", [segment["text"] for segment in self.spoken])

    def test_destination_created_before_the_effect_is_not_truncated(self) -> None:
        target = self.root / "raced.txt"
        perform = self.controller.perform_authorized

        def create_competing_file(token, effect):
            target.write_bytes(b"competing content")
            return perform(token, effect)

        with patch.object(self.controller, "perform_authorized", side_effect=create_competing_file):
            files.handle_create_file(self.app, {
                "file_path": str(target), "content": "replacement",
                CAPABILITY_AUTHORIZATION: self.token,
            }, self.ctx)
        self.assertEqual(target.read_bytes(), b"competing content")
        self.assertEqual(len(self.errors), 1)

    def test_new_file_still_creates_parents_and_preserves_content(self) -> None:
        target = self.root / "new" / "note.txt"
        files.handle_create_file(self.app, {
            "file_path": str(target), "content": "hello สวัสดี\n",
            CAPABILITY_AUTHORIZATION: self.token,
        }, self.ctx)
        self.assertEqual(target.read_text(encoding="utf-8"), "hello สวัสดี\n")
        self.assertEqual(self.errors, [])
        self.assertEqual(self.spoken, [{"text": "File created."}])


if __name__ == "__main__":
    unittest.main()
