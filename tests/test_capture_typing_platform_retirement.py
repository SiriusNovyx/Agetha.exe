"""Default platform eligibility without display, clipboard, or input access."""

import unittest
from unittest.mock import patch

from agetha.platform import screen_reader as screen
from agetha.platform import unicode_typing as typing


class CapturePlatformRetirement(unittest.TestCase):
    def reader(self, system):
        reader = screen.ScreenReader.__new__(screen.ScreenReader)
        reader._system = system
        return reader

    def test_retired_and_unknown_systems_have_no_display_even_with_linux_environment(self):
        for system in ('Darwin', 'OtherOS'):
            for environment in ({}, {'DISPLAY': ':99'}, {'WAYLAND_DISPLAY': 'synthetic'},
                                {'XDG_SESSION_TYPE': 'x11'}):
                with self.subTest(system=system, environment=environment), \
                     patch.object(screen.platform, 'system', return_value=system), \
                     patch.dict(screen.os.environ, environment, clear=True):
                    self.assertFalse(screen._has_display())

    def test_windows_and_linux_display_eligibility_remains(self):
        cases = [('Windows', {}, True), ('Linux', {}, False),
                 ('Linux', {'DISPLAY': ':99'}, True), ('Linux', {'WAYLAND_DISPLAY': 'synthetic'}, True)]
        for system, environment, expected in cases:
            with self.subTest(system=system, environment=environment), \
                 patch.object(screen.platform, 'system', return_value=system), \
                 patch.dict(screen.os.environ, environment, clear=True):
                self.assertEqual(screen._has_display(), expected)

    def test_retired_and_unknown_systems_have_no_capture_candidates(self):
        for system in ('Darwin', 'OtherOS'):
            for wayland in (False, True):
                for automatic in (False, True):
                    with self.subTest(system=system, wayland=wayland, automatic=automatic), \
                         patch.object(screen, '_is_wayland', return_value=wayland):
                        self.assertEqual(self.reader(system)._ordered_backends(automatic=automatic), [])

    def test_windows_keeps_mss_candidate(self):
        for automatic in (False, True):
            self.assertEqual([name for name, _callback in self.reader('Windows')._ordered_backends(
                automatic=automatic)], ['mss'])

    def test_linux_x11_keeps_candidate_order(self):
        with patch.object(screen, '_is_wayland', return_value=False):
            for automatic in (False, True):
                self.assertEqual([name for name, _callback in self.reader('Linux')._ordered_backends(
                    automatic=automatic)], ['mss', 'imagegrab', 'scrot', 'pyautogui'])

    def test_linux_wayland_keeps_explicit_only_candidates(self):
        with patch.object(screen, '_is_wayland', return_value=True):
            self.assertEqual(self.reader('Linux')._ordered_backends(), [])
            self.assertEqual([name for name, _callback in self.reader('Linux')._ordered_backends(
                automatic=False)], ['spectacle', 'grim'])


class TypingPlatformRetirement(unittest.TestCase):
    def test_retired_and_unknown_factories_do_not_discover_native_tools(self):
        for system in ('darwin', 'mac', 'macos', 'other-os'):
            with self.subTest(system=system), patch.object(typing.sys, 'platform', system), \
                 patch.object(typing.shutil, 'which', return_value='/synthetic/tool') as which:
                dependencies = typing.default_dependencies()
                which.assert_not_called()
                self.assertEqual(dependencies.session_type, 'unknown')
                self.assertIsNone(dependencies.get_focused_target())
                for callback in ('send_native_unicode', 'read_clipboard', 'write_clipboard',
                                 'send_paste_shortcut', 'activate_target'):
                    self.assertIsNone(getattr(dependencies, callback))

    def test_retired_default_typing_has_no_clipboard_or_input_effect(self):
        effects = []
        with patch.object(typing.sys, 'platform', 'darwin'), \
             patch.object(typing.shutil, 'which', return_value='/synthetic/tool'), \
             patch.object(typing, '_command_clipboard_writer',
                          side_effect=lambda *args: effects.append(args) or True), \
             patch.object(typing, '_command_clipboard_reader', return_value='synthetic previous'):
            result = typing.type_unicode_text('synthetic text', preview_approved=True)
        self.assertFalse(result.success)
        self.assertEqual(result.method, 'clipboard-unavailable')
        self.assertEqual(effects, [])

    def test_windows_default_factory_preserves_injected_callbacks(self):
        with patch.object(typing.sys, 'platform', 'win32'), \
             patch.object(typing, '_windows_target', return_value=None) as target, \
             patch.object(typing, '_windows_read_clipboard', return_value='synthetic clipboard') as read:
            dependencies = typing.default_dependencies()
            self.assertEqual(dependencies.platform_name, 'windows')
            self.assertEqual(dependencies.session_type, 'desktop')
            self.assertIsNone(dependencies.get_focused_target())
            self.assertEqual(dependencies.read_clipboard(), 'synthetic clipboard')
            self.assertTrue(callable(dependencies.send_native_unicode))
            target.assert_called_once_with()
            read.assert_called_once_with()

    def test_linux_default_factory_keeps_session_specific_callbacks(self):
        for session in ('x11', 'wayland'):
            with self.subTest(session=session), patch.object(typing.sys, 'platform', 'linux'), \
                 patch.object(typing, '_linux_session_type', return_value=session), \
                 patch.object(typing, '_linux_clipboard_commands', return_value=(['fake-read'], ['fake-write'])), \
                 patch.object(typing, '_x11_target', return_value=None) as target:
                dependencies = typing.default_dependencies()
                self.assertEqual(dependencies.platform_name, 'linux')
                self.assertEqual(dependencies.session_type, session)
                self.assertTrue(callable(dependencies.read_clipboard))
                self.assertTrue(callable(dependencies.write_clipboard))
                self.assertEqual(callable(dependencies.send_paste_shortcut), session == 'x11')
                self.assertEqual(callable(dependencies.activate_target), session == 'x11')
                self.assertIsNone(dependencies.get_focused_target())
                self.assertEqual(target.call_count, 1 if session == 'x11' else 0)
