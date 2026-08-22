from __future__ import annotations

import unittest

from agetha.commands.command_handlers import HANDLERS


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


if __name__ == "__main__":
    unittest.main()
