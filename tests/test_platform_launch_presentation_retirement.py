"""Retired platform launch/font/sound paths, using fake effects and storage."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main
from agetha.commands import command_handlers
from agetha.commands.handlers import files
from agetha.commands.handlers.support import CAPABILITY_AUTHORIZATION, DispatchCtx
from agetha.core.capabilities import Capability, CapabilityController, CapabilityPolicy, CapabilityProfile


class LaunchPlatformRetirement(unittest.TestCase):
    def setUp(self):
        self.controller = CapabilityController(CapabilityPolicy(
            CapabilityProfile.FULL, {'ENABLE_COMMAND_EXECUTION': True}))
        self.token = self.controller.authorize(Capability.ADVANCED_OS_INTEGRATION)
        self.errors, self.spoken = [], []
        self.app = SimpleNamespace(_capabilities=self.controller, _show_op_error=self.errors.append,
            _speak_and_continue=lambda segments, *_args: self.spoken.extend(segments),
            _set_state=lambda *_args: None, STATE_IDLE='idle', _reschedule_screen_poll=lambda: None)
        self.ctx = DispatchCtx('open a synthetic target', 'neutral', [{'text': 'Done.'}], False)
        self.popen = self.enterContext(patch.object(files.subprocess, 'Popen'))
        self.startfile = self.enterContext(patch.object(files.os, 'startfile', create=True))
        self.launch = self.enterContext(patch.object(command_handlers, 'guarded_launch_application', return_value=True))
        self.enterContext(patch.object(command_handlers, '_schedule_app_ui', side_effect=lambda app, cb: cb()))

    def platform(self, module, name):
        self.enterContext(patch.object(module, 'IS_WINDOWS', name == 'Windows'))
        self.enterContext(patch.object(module, 'IS_LINUX', name == 'Linux', create=True))
        self.enterContext(patch('platform.system', return_value=name))

    def test_supported_file_routes_preserve_native_arguments(self):
        for name in ('Windows', 'Linux'):
            with self.subTest(platform=name):
                self.platform(files, name)
                self.popen.reset_mock()
                self.startfile.reset_mock()
                files.handle_open_file(self.app, {'path': 'synthetic.txt',
                    CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
                if name == 'Windows':
                    self.startfile.assert_called_once_with('synthetic.txt')
                    self.popen.assert_not_called()
                else:
                    self.popen.assert_called_once_with(['xdg-open', 'synthetic.txt'])
                    self.startfile.assert_not_called()

    def test_retired_and_unknown_file_routes_refuse_launch(self):
        for name in ('Darwin', 'OtherOS'):
            with self.subTest(platform=name):
                self.platform(files, name)
                self.spoken.clear()
                files.handle_open_file(self.app, {'path': 'synthetic.txt',
                    CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
                self.popen.assert_not_called()
                self.startfile.assert_not_called()
                self.assertTrue(self.errors)
                self.assertNotIn('Done.', [segment['text'] for segment in self.spoken])

    def test_supported_app_routes_preserve_guarded_launcher(self):
        for name in ('Windows', 'Linux'):
            with self.subTest(platform=name):
                self.platform(command_handlers, name)
                self.launch.reset_mock()
                command_handlers.handle_open_app(self.app, {'app': 'SyntheticApp',
                    CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
                self.launch.assert_called_once_with(self.app, ('SyntheticApp',),
                    guard_approved=True, capability_authorization=self.token)

    def test_retired_and_unknown_app_routes_refuse_launch(self):
        for name in ('Darwin', 'OtherOS'):
            with self.subTest(platform=name):
                self.platform(command_handlers, name)
                self.spoken.clear()
                command_handlers.handle_open_app(self.app, {'app': 'SyntheticApp',
                    CAPABILITY_AUTHORIZATION: self.token}, self.ctx)
                self.launch.assert_not_called()
                self.assertTrue(self.errors)
                self.assertNotIn('Done.', [segment['text'] for segment in self.spoken])


class PresentationPlatformRetirement(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.home = Path(temporary) / 'synthetic-home'
        self.home.mkdir()
        self.font = Path(temporary) / 'synthetic.ttf'
        self.font.write_bytes(b'synthetic font')
        self.enterContext(patch.object(main, 'FONT_PATH', self.font))
        self.enterContext(patch.object(Path, 'home', return_value=self.home))
        self.enterContext(patch.dict(sys.modules, {'tkextrafont': None}))
        self.run = self.enterContext(patch.object(main.subprocess, 'run'))
        self.popen = self.enterContext(patch.object(main.subprocess, 'Popen'))
        self.enterContext(patch.object(main, 'PYGAME_OK', False))
        self.app = SimpleNamespace(_bleep=None)

    def test_retired_font_path_does_not_install_into_user_fonts(self):
        with patch('platform.system', return_value='Darwin'):
            self.assertFalse(main._register_barrio_font())
        self.assertEqual(list(self.home.iterdir()), [])
        self.run.assert_not_called()
        self.popen.assert_not_called()

    def test_linux_font_installation_retains_directory_and_cache_command(self):
        with patch('platform.system', return_value='Linux'):
            self.assertTrue(main._register_barrio_font())
        self.assertEqual((self.home / '.local/share/fonts/barrio.ttf').read_bytes(), b'synthetic font')
        self.run.assert_called_once_with(['fc-cache', '-f'], capture_output=True)

    def test_optional_portable_font_loader_remains_available(self):
        loaded = []
        with patch.dict(sys.modules, {'tkextrafont': SimpleNamespace(load=loaded.append)}):
            self.assertTrue(main._register_barrio_font())
        self.assertEqual(loaded, [str(self.font)])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_windows_sound_route_preserves_winsound(self):
        played = []
        sound = SimpleNamespace(PlaySound=lambda *args: played.append(args), SND_ALIAS=1, SND_ASYNC=2)
        with patch.object(main, 'IS_WINDOWS', True), patch.dict(sys.modules, {'winsound': sound}):
            main.CompanionApp._play_emotion_sound(self.app, 'happy')
        self.assertEqual(played, [('SystemAsterisk', 3)])
        self.popen.assert_not_called()

    def test_linux_sound_route_preserves_paplay(self):
        with patch.object(main, 'IS_WINDOWS', False), patch.object(main, 'IS_LINUX', True), \
             patch('platform.system', return_value='Linux'):
            main.CompanionApp._play_emotion_sound(self.app, 'happy')
        self.popen.assert_called_once_with(['paplay', '/usr/share/sounds/freedesktop/stereo/bell.oga'],
            stdout=main.subprocess.DEVNULL, stderr=main.subprocess.DEVNULL)

    def test_retired_and_unknown_sound_paths_do_not_launch_native_players(self):
        with patch.object(main, 'IS_WINDOWS', False), patch.object(main, 'IS_LINUX', False):
            for name in ('Darwin', 'OtherOS'):
                with self.subTest(platform=name), patch('platform.system', return_value=name):
                    main.CompanionApp._play_emotion_sound(self.app, 'happy')
                    self.popen.assert_not_called()

    def test_shared_bleep_fallback_remains_available(self):
        bleeps = []
        self.app._bleep = SimpleNamespace(start_talking=lambda **kw: bleeps.append(kw), stop=lambda: None)
        with patch.object(main, 'IS_WINDOWS', False), patch.object(main, 'IS_LINUX', False), \
             patch.object(main, 'PYGAME_OK', True), patch('platform.system', return_value='Darwin'), \
             patch.object(main.threading, 'Timer') as timer:
            main.CompanionApp._play_emotion_sound(self.app, 'happy')
        self.assertEqual(bleeps, [{'tone': 'excited'}])
        timer.return_value.start.assert_called_once_with()
        self.popen.assert_not_called()
