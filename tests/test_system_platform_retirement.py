"""Retired-platform utility refusal and retained Windows/Linux behavior."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agetha.commands import system_commands


class SystemPlatformRetirement(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.folder = Path(temporary)
        self.wallpaper = self.folder / 'synthetic.png'
        self.wallpaper.write_bytes(b'fake image')
        self.enterContext(patch.object(system_commands, 'IS_WINDOWS', False))
        self.enterContext(patch.object(system_commands, 'IS_LINUX', False))
        self.enterContext(patch.object(system_commands, 'IS_MACOS', True, create=True))
        self.enterContext(patch.object(system_commands.shutil, 'which', return_value='/fake/tool'))
        class Process:
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def wait(self, timeout=None): return 0
            def kill(self): pass
        self.popen = self.enterContext(patch.object(system_commands.subprocess, 'Popen', return_value=Process()))
        self.run = self.enterContext(patch.object(system_commands.subprocess, 'run',
                                                  return_value=SimpleNamespace(stdout=' synthetic text ')))
        self.startfile = self.enterContext(patch.object(system_commands.os, 'startfile', create=True))

    def assert_no_native_effect(self):
        self.popen.assert_not_called()
        self.run.assert_not_called()
        self.startfile.assert_not_called()

    def test_retired_clipboard_write_has_no_native_fallback(self):
        self.assertEqual(system_commands.copy_to_clipboard('synthetic'), '[clipboard not supported]')
        self.assert_no_native_effect()

    def test_retired_clipboard_read_has_no_native_fallback(self):
        self.assertEqual(system_commands.get_clipboard(), '[clipboard not supported]')
        self.assert_no_native_effect()

    def test_retired_folder_open_has_no_native_fallback(self):
        self.assertEqual(system_commands.open_folder(str(self.folder)), '[open_folder not supported]')
        self.assert_no_native_effect()

    def test_retired_volume_has_no_native_fallback(self):
        self.assertEqual(system_commands.set_volume(50), '[set_volume not supported on this platform]')
        self.assert_no_native_effect()

    def test_retired_wallpaper_has_no_native_fallback(self):
        self.assertEqual(system_commands.set_wallpaper(str(self.wallpaper)), '[set_wallpaper not supported]')
        self.assert_no_native_effect()

    def test_retired_lock_has_no_native_fallback(self):
        self.assertEqual(system_commands.lock_screen(), '[lock_screen not supported]')
        self.assert_no_native_effect()

    def test_retired_power_has_no_native_fallback(self):
        for helper, name in ((system_commands.shutdown_system, 'shutdown'),
                             (system_commands.restart_system, 'restart')):
            for delay in (0, 17):
                with self.subTest(name=name, delay=delay):
                    self.assertEqual(helper(delay), f'[{name} not supported]')
                    self.assert_no_native_effect()

    def test_retired_private_scheduler_has_no_native_fallback(self):
        for delay in (0, 17):
            with self.subTest(delay=delay):
                self.assertFalse(system_commands._schedule_delayed_shutdown(delay))
                self.assert_no_native_effect()

    def test_retired_notification_has_no_native_fallback(self):
        self.assertEqual(system_commands.show_notification('Synthetic title', 'Synthetic message'),
                         '[notification not supported]')
        self.assert_no_native_effect()

    def test_generic_tk_clipboard_path_remains_available(self):
        effects = []
        root = SimpleNamespace(clipboard_clear=lambda: effects.append('clear'),
            clipboard_append=lambda text: effects.append(text), update=lambda: effects.append('update'),
            clipboard_get=lambda: 'synthetic Tk text')
        self.assertEqual(system_commands.copy_to_clipboard('synthetic', root), '[copied to clipboard]')
        self.assertEqual(system_commands.get_clipboard(root), 'synthetic Tk text')
        self.assertEqual(effects, ['clear', 'synthetic', 'update'])
        self.assert_no_native_effect()

    def test_windows_clipboard_commands_remain_unchanged(self):
        with patch.object(system_commands, 'IS_WINDOWS', True):
            self.assertEqual(system_commands.copy_to_clipboard('synthetic'), '[copied via clip]')
            self.run.assert_called_once_with(['clip'], input='synthetic', text=True, check=True, timeout=5)
            self.run.reset_mock()
            self.assertEqual(system_commands.get_clipboard(), 'synthetic text')
            self.run.assert_called_once_with(
                ['powershell', '-NoProfile', '-Command', 'Get-Clipboard'],
                capture_output=True, text=True, timeout=5)

    def test_linux_clipboard_commands_remain_unchanged(self):
        with patch.object(system_commands, 'IS_LINUX', True), \
             patch.object(system_commands, 'IS_MACOS', False, create=True):
            self.assertEqual(system_commands.copy_to_clipboard('synthetic'), '[copied via xclip]')
            self.run.assert_called_once_with(['xclip', '-selection', 'clipboard'],
                                             input='synthetic', text=True, check=True, timeout=5)
            self.run.reset_mock()
            self.assertEqual(system_commands.get_clipboard(), 'synthetic text')
            self.run.assert_called_once_with(['xclip', '-selection', 'clipboard', '-o'],
                                             capture_output=True, text=True, timeout=5)

    def test_linux_folder_command_remains_unchanged(self):
        with patch.object(system_commands, 'IS_LINUX', True), \
             patch.object(system_commands, 'IS_MACOS', False, create=True):
            self.assertEqual(system_commands.open_folder(str(self.folder)), f'[opened folder: {self.folder}]')
        self.popen.assert_called_once_with(['xdg-open', str(self.folder)], start_new_session=True)

    def test_linux_wallpaper_command_remains_unchanged(self):
        with patch.object(system_commands, 'IS_LINUX', True), \
             patch.object(system_commands, 'IS_MACOS', False, create=True):
            self.assertEqual(system_commands.set_wallpaper(str(self.wallpaper)), f'[wallpaper set: {self.wallpaper}]')
        self.popen.assert_called_once_with(['gsettings', 'set', 'org.gnome.desktop.background',
                                           'picture-uri', f'file://{self.wallpaper.resolve()}'])

    def test_linux_lock_command_remains_unchanged(self):
        with patch.object(system_commands, 'IS_LINUX', True), \
             patch.object(system_commands, 'IS_MACOS', False, create=True):
            self.assertEqual(system_commands.lock_screen(), '[screen locked]')
        self.popen.assert_called_once_with(['loginctl', 'lock-session'])

    def test_linux_notification_command_remains_unchanged(self):
        with patch.object(system_commands, 'IS_LINUX', True), \
             patch.object(system_commands, 'IS_MACOS', False, create=True):
            self.assertEqual(system_commands.show_notification('title', 'message'), '[notification sent]')
        self.popen.assert_called_once_with(['notify-send', 'title', 'message'])

    def test_windows_notification_keeps_toast_path(self):
        with patch.object(system_commands, 'IS_WINDOWS', True), \
             patch('agetha.platform.windows_notify.show_toast', return_value=True) as toast:
            self.assertEqual(system_commands.show_notification('title', 'message'), '[notification sent]')
        toast.assert_called_once_with('title', 'message')
        self.assert_no_native_effect()
