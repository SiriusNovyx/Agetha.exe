"""Volume control uses fake children and the existing capability owner."""

import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agetha.commands import system_commands
from agetha.commands.handlers import system
from agetha.commands.handlers.support import CAPABILITY_AUTHORIZATION, DispatchCtx
from agetha.core.capabilities import Capability, CapabilityController, CapabilityPolicy, CapabilityProfile


class VolumeEffectAuthorization(unittest.TestCase):
    def setUp(self):
        self.controller = CapabilityController(CapabilityPolicy(
            CapabilityProfile.FULL, {'ENABLE_COMMAND_EXECUTION': True}))
        self.token = self.controller.authorize(Capability.ADVANCED_OS_INTEGRATION)
        self.effects, self.errors, self.spoken = [], [], []
        self.app = SimpleNamespace(
            _capabilities=self.controller, _show_op_error=self.errors.append,
            _speak_and_continue=lambda segments, *_args: self.spoken.extend(segments))
        self.ctx = DispatchCtx(None, 'neutral', [{'text': 'Done.'}], False)
        effects = self.effects
        class Process:
            returncode = 0
            def __enter__(self): return self
            def __exit__(self, *_args): effects.append('closed')
            def wait(self, timeout=None):
                effects.append(('wait', timeout))
                return self.returncode
            def kill(self): effects.append('killed')
        self.process = Process()
        def spawn(*_args, **_kwargs):
            effects.append('spawn')
            return self.process
        self.popen = self.enterContext(patch.object(system_commands.subprocess, 'Popen', side_effect=spawn))
        def run(command, *, timeout=None, **_kwargs):
            with self.popen(command) as process:
                try:
                    process.wait(timeout=timeout)
                except BaseException:
                    process.kill()
                    raise
            return process
        self.run = self.enterContext(patch.object(system_commands.subprocess, 'run', side_effect=run))
        self.enterContext(patch.object(system_commands, 'IS_WINDOWS', True))
        self.enterContext(patch.object(system_commands, 'IS_LINUX', False))

    def change(self, **response):
        system.handle_set_volume(self.app, {
            CAPABILITY_AUTHORIZATION: self.token, **response}, self.ctx)

    def assert_no_launch(self):
        self.popen.assert_not_called()
        self.run.assert_not_called()

    def test_windows_actions_preserve_existing_commands_and_success(self):
        for action, result, timeout in [('mute', '[muted]', 5), ('unmute', '[unmuted]', 5),
                                         ('set', '[volume set ~50%]', 10)]:
            with self.subTest(action=action):
                self.effects.clear()
                self.change(action=action, level=50)
                self.assertEqual(self.effects, ['spawn', ('wait', timeout), 'closed'])
                command = self.popen.call_args.args[0]
                self.assertEqual(command[:3], ['powershell', '-NoProfile', '-Command'])
                self.assertIn('[char]173' if action != 'set' else '1..25', command[3])
                self.assertEqual(self.spoken[-1], self.ctx.segments[0])
                self.assertEqual(self.errors, [])

    def test_downgrade_during_action_preparation_blocks_launch(self):
        controller = self.controller
        class Action(str):
            def lower(self):
                controller.begin_compact_transition()
                return super().lower()
        self.change(action=Action('mute'))
        self.assert_no_launch()
        self.assertTrue(self.errors)
        self.assertNotIn('Done.', [segment['text'] for segment in self.spoken])

    def test_missing_authorization_blocks_launch(self):
        system.handle_set_volume(self.app, {'level': 50}, self.ctx)
        self.assert_no_launch()

    def test_invalid_authorization_blocks_launch(self):
        system.handle_set_volume(self.app, {'level': 50, CAPABILITY_AUTHORIZATION: object()}, self.ctx)
        self.assert_no_launch()

    def test_linux_lookup_can_expire_authorization_before_launch(self):
        def lookup(_name):
            self.controller.begin_compact_transition()
            return '/fake/pactl'
        with patch.object(system_commands, 'IS_WINDOWS', False), \
             patch.object(system_commands, 'IS_LINUX', True), \
             patch.object(system_commands.shutil, 'which', side_effect=lookup):
            self.change(level=10)
        self.assert_no_launch()

    def test_linux_actions_preserve_command_arguments(self):
        with patch.object(system_commands, 'IS_WINDOWS', False), \
             patch.object(system_commands, 'IS_LINUX', True), \
             patch.object(system_commands.shutil, 'which', return_value='/fake/pactl'):
            for action, tail in [('mute', ['set-sink-mute', '@DEFAULT_SINK@', '1']),
                                 ('unmute', ['set-sink-mute', '@DEFAULT_SINK@', '0']),
                                 ('set', ['set-sink-volume', '@DEFAULT_SINK@', '18%'])]:
                with self.subTest(action=action):
                    self.change(action=action, level=18)
                    self.assertEqual(self.popen.call_args.args[0], ['pactl', *tail])

    def test_wait_releases_capability_lock(self):
        finished = threading.Event()
        worker = threading.Thread(target=lambda: (self.controller.begin_compact_transition(), finished.set()))
        def wait(timeout=None):
            worker.start()
            self.effects.append(('transition_during_wait', finished.wait(1)))
            return 0
        self.process.wait = wait
        try:
            self.change(level=50)
        finally:
            if worker.ident is not None:
                worker.join(2)
        self.assertIn(('transition_during_wait', True), self.effects)
        self.assertFalse(worker.is_alive())

    def test_timeout_kills_child_and_closes_process(self):
        def wait(timeout=None):
            raise subprocess.TimeoutExpired('fake volume', timeout)
        self.process.wait = wait
        result = system_commands.set_volume(50)
        self.assertTrue(result.startswith('[set_volume error:'))
        self.assertEqual(self.effects, ['spawn', 'killed', 'closed'])

    def test_standalone_helper_retains_level_clamping(self):
        self.assertEqual(system_commands.set_volume(150), '[volume set ~100%]')
        self.assertIn('1..50', self.popen.call_args.args[0][3])

    def test_unavailable_backend_still_does_not_launch(self):
        with patch.object(system_commands, 'IS_WINDOWS', False), \
             patch.object(system_commands, 'IS_LINUX', True), \
             patch.object(system_commands.shutil, 'which', return_value=None):
            self.assertEqual(system_commands.set_volume(50), '[set_volume not supported on this platform]')
        self.assert_no_launch()
