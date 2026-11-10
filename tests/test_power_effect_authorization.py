"""Lock/power authorization without launching any native process."""

import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agetha.commands import system_commands
from agetha.commands.handlers import system
from agetha.commands.handlers.support import CAPABILITY_AUTHORIZATION, DispatchCtx
from agetha.core.capabilities import Capability, CapabilityController, CapabilityPolicy, CapabilityProfile


class PowerEffectAuthorization(unittest.TestCase):
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
        def spawn(command, **_kwargs):
            effects.append(('spawn', command))
            return self.process
        self.popen = self.enterContext(patch.object(system_commands.subprocess, 'Popen', side_effect=spawn))
        def run(command, *, timeout=None, check=False, **_kwargs):
            with self.popen(command) as process:
                try:
                    code = process.wait(timeout=timeout)
                except BaseException:
                    process.kill()
                    raise
                if check and code:
                    raise subprocess.CalledProcessError(code, command)
            return process
        self.run = self.enterContext(patch.object(system_commands.subprocess, 'run', side_effect=run))
        self.enterContext(patch.object(system_commands, 'IS_WINDOWS', True))
        self.enterContext(patch.object(system_commands, 'IS_LINUX', False))

    def invoke(self, handler, **response):
        handler(self.app, {CAPABILITY_AUTHORIZATION: self.token, **response}, self.ctx)

    def assert_no_launch(self):
        self.popen.assert_not_called()
        self.run.assert_not_called()

    def test_windows_success_preserves_commands_and_one_completion(self):
        cases = (
            (system.handle_lock_screen, {}, ['rundll32.exe', 'user32.dll,LockWorkStation']),
            (system.handle_shutdown, {'delay': 17}, ['shutdown', '/s', '/t', '17']),
            (system.handle_restart, {'delay': 17}, ['shutdown', '/r', '/t', '17']),
        )
        for handler, response, command in cases:
            with self.subTest(handler=handler.__name__):
                self.effects.clear()
                self.spoken.clear()
                self.invoke(handler, **response)
                self.assertEqual(self.effects, [('spawn', command), ('wait', 5), 'closed'])
                self.assertEqual(self.spoken, self.ctx.segments)
                self.assertEqual(self.errors, [])

    def test_missing_and_invalid_authorization_block_each_handler(self):
        for handler in (system.handle_lock_screen, system.handle_shutdown, system.handle_restart):
            for response in ({}, {CAPABILITY_AUTHORIZATION: object()}):
                with self.subTest(handler=handler.__name__, response=response):
                    self.popen.reset_mock()
                    self.run.reset_mock()
                    handler(self.app, response, self.ctx)
                    self.assert_no_launch()

    def test_expired_authorization_blocks_each_handler(self):
        self.controller.begin_compact_transition()
        for handler in (system.handle_lock_screen, system.handle_shutdown, system.handle_restart):
            with self.subTest(handler=handler.__name__):
                self.popen.reset_mock()
                self.run.reset_mock()
                self.invoke(handler)
                self.assert_no_launch()
        self.assertTrue(self.errors)
        self.assertNotIn('Done.', [segment['text'] for segment in self.spoken])

    def test_linux_lock_lookup_can_invalidate_authorization(self):
        def lookup(_command):
            self.controller.begin_compact_transition()
            return '/fake/loginctl'
        with patch.object(system_commands, 'IS_WINDOWS', False), \
             patch.object(system_commands, 'IS_LINUX', True), \
             patch.object(system_commands.shutil, 'which', side_effect=lookup):
            self.invoke(system.handle_lock_screen)
        self.assert_no_launch()

    def test_linux_scheduler_lookup_can_invalidate_authorization(self):
        def lookup(_command):
            self.controller.begin_compact_transition()
            return '/fake/systemd-run'
        with patch.object(system_commands, 'IS_WINDOWS', False), \
             patch.object(system_commands, 'IS_LINUX', True), \
             patch.object(system_commands.shutil, 'which', side_effect=lookup):
            self.invoke(system.handle_shutdown, delay=17)
        self.assert_no_launch()

    def test_linux_scheduling_preserves_existing_backend_arguments(self):
        cases = (
            (0, lambda _command: None, ['shutdown', '-h', 'now']),
            (17, lambda command: '/fake/systemd-run' if command == 'systemd-run' else None,
             ['systemd-run', '--on-active=17s', '--unit=agetha-shutdown', 'shutdown', '-h', 'now']),
            (17, lambda command: '/fake/at' if command == 'at' else None,
             ['bash', '-c', "echo 'shutdown -h now' | at now + 17 seconds"]),
            (61, lambda _command: None, ['shutdown', '-h', '+2']),
        )
        with patch.object(system_commands, 'IS_WINDOWS', False), \
             patch.object(system_commands, 'IS_LINUX', True):
            for delay, lookup, command in cases:
                with self.subTest(delay=delay, command=command), \
                     patch.object(system_commands.shutil, 'which', side_effect=lookup):
                    self.effects.clear()
                    self.invoke(system.handle_shutdown, delay=delay)
                    self.assertEqual(self.effects, [('spawn', command), ('wait', 5), 'closed'])

    def test_linux_restart_preserves_reboot_flag(self):
        with patch.object(system_commands, 'IS_WINDOWS', False), \
             patch.object(system_commands, 'IS_LINUX', True), \
             patch.object(system_commands.shutil, 'which', return_value=None):
            self.invoke(system.handle_restart, delay=0)
        self.assertEqual(self.effects[0], ('spawn', ['shutdown', '-r', 'now']))

    def test_admitted_power_command_wait_does_not_hold_capability_lock(self):
        finished = threading.Event()
        worker = threading.Thread(target=lambda: (self.controller.begin_compact_transition(), finished.set()))
        def wait(timeout=None):
            worker.start()
            self.effects.append(('transition_during_wait', finished.wait(1)))
            return 0
        self.process.wait = wait
        try:
            self.invoke(system.handle_shutdown, delay=17)
        finally:
            if worker.ident is not None:
                worker.join(2)
        self.assertIn(('transition_during_wait', True), self.effects)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.spoken, self.ctx.segments)

    def test_process_failure_is_reported_without_success_speech(self):
        self.process.returncode = 1
        self.invoke(system.handle_lock_screen)
        self.assertTrue(self.errors)
        self.assertNotIn('Done.', [segment['text'] for segment in self.spoken])
        self.assertEqual(self.effects[-1], 'closed')

    def test_timeout_kills_child_and_reports_failure(self):
        def wait(timeout=None):
            raise subprocess.TimeoutExpired('fake power command', timeout)
        self.process.wait = wait
        self.invoke(system.handle_restart)
        self.assertEqual(self.effects[-2:], ['killed', 'closed'])
        self.assertTrue(self.errors)
        self.assertNotIn('Done.', [segment['text'] for segment in self.spoken])

    def test_standalone_helpers_preserve_return_messages(self):
        self.assertEqual(system_commands.lock_screen(), '[screen locked]')
        self.assertEqual(system_commands.shutdown_system(-1), '[shutdown in 0s]')
        self.assertEqual(system_commands.restart_system(17), '[restart in 17s]')
