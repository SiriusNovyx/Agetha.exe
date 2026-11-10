"""Autostart mutations use disposable files and a fake PowerShell child."""

import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agetha.commands.handlers import system
from agetha.commands.handlers.support import CAPABILITY_AUTHORIZATION, DispatchCtx
from agetha.core.capabilities import Capability, CapabilityController, CapabilityPolicy, CapabilityProfile
from agetha.platform import autostart


class AutostartEffectAuthorization(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.startup = Path(temporary) / 'Startup'
        self.shortcut = self.startup / 'Agetha.lnk'
        self.enterContext(patch.dict(autostart.os.environ, {'APPDATA': temporary}, clear=True))
        self.controller = CapabilityController(CapabilityPolicy(
            CapabilityProfile.FULL, {'ENABLE_COMMAND_EXECUTION': True}))
        self.token = self.controller.authorize(Capability.ADVANCED_OS_INTEGRATION)
        self.effects, self.errors, self.successes, self.spoken = [], [], [], []
        self.app = SimpleNamespace(
            _capabilities=self.controller, _show_op_error=self.errors.append,
            _show_op_success=self.successes.append,
            _speak_and_continue=lambda segments, *_args: self.spoken.extend(segments))
        self.ctx = DispatchCtx(None, 'neutral', [{'text': 'Done.'}], False)
        self.enterContext(patch.object(system, 'get_settings',
                                      return_value=SimpleNamespace(enable_autostart_control=True)))
        self.enterContext(patch.object(system, '_schedule_app_ui', side_effect=lambda app, cb: cb()))
        self.enterContext(patch('agetha.core.audit_log.log_audit'))
        self.enterContext(patch.object(autostart, 'IS_WINDOWS', True))
        self.enterContext(patch.object(autostart, 'startup_dir', return_value=self.startup))
        self.enterContext(patch.object(autostart, 'ICON_PATH', SimpleNamespace(is_file=lambda: False)))
        self.launcher = self.enterContext(patch.object(autostart, 'expected_launcher',
                                             return_value=('synthetic.exe', '', 'synthetic')))
        self.validate = self.enterContext(patch.object(autostart, 'validate', side_effect=lambda:
            autostart.STATUS_VALID if self.shortcut.is_file() else autostart.STATUS_MISSING))
        effects = self.effects
        class Process:
            args = ['fake PowerShell']
            returncode = 0
            stdout = 'OK'
            stderr = ''
            def __enter__(self): return self
            def __exit__(self, *_args): effects.append('closed')
            def communicate(self, timeout=None):
                effects.append('wait')
                return self.stdout, self.stderr
            def kill(self): effects.append('killed')
        self.process = Process()
        def spawn(*_args, **kwargs):
            effects.append('spawn')
            self.shortcut.write_text('synthetic shortcut', encoding='utf-8')
            self.child_env = kwargs['env']
            return self.process
        self.popen = self.enterContext(patch.object(autostart.subprocess, 'Popen', side_effect=spawn))
        def run(*args, timeout=None, **kwargs):
            with self.popen(*args, **kwargs) as process:
                try:
                    process.communicate(timeout=timeout)
                except BaseException:
                    process.kill()
                    raise
            return process
        self.run = self.enterContext(patch.object(autostart.subprocess, 'run', side_effect=run))

    def change(self, enabled):
        system.handle_set_autostart(self.app, {
            'enabled': enabled, CAPABILITY_AUTHORIZATION: self.token}, self.ctx)

    def seed_shortcut(self):
        self.startup.mkdir()
        self.shortcut.write_text('original synthetic shortcut', encoding='utf-8')

    def test_current_authorization_creates_one_shortcut_with_existing_environment(self):
        self.change(True)
        self.assertEqual(self.effects, ['spawn', 'wait', 'closed'])
        self.assertEqual(self.child_env['AGETHA_LNK'], str(self.shortcut))
        self.assertEqual(self.child_env['AGETHA_TARGET'], 'synthetic.exe')
        self.assertEqual(self.shortcut.read_text(encoding='utf-8'), 'synthetic shortcut')
        self.assertTrue(self.successes)
        self.assertEqual(self.spoken, self.ctx.segments)

    def test_current_authorization_removes_own_shortcut(self):
        self.seed_shortcut()
        self.change(False)
        self.assertFalse(self.shortcut.exists())
        self.assertEqual(self.effects, [])
        self.assertTrue(self.successes)

    def test_expired_enable_cannot_create_startup_directory_or_shortcut(self):
        self.controller.begin_compact_transition()
        self.change(True)
        self.assertFalse(self.startup.exists())
        self.popen.assert_not_called()
        self.assertTrue(self.errors)
        self.assertNotIn('Done.', [segment['text'] for segment in self.spoken])

    def test_missing_authorization_cannot_create_startup_directory(self):
        system.handle_set_autostart(self.app, {'enabled': True}, self.ctx)
        self.assertFalse(self.startup.exists())
        self.popen.assert_not_called()

    def test_expired_disable_preserves_existing_shortcut(self):
        self.seed_shortcut()
        self.controller.begin_compact_transition()
        self.change(False)
        self.assertTrue(self.shortcut.exists())
        self.assertEqual(self.shortcut.read_text(encoding='utf-8'), 'original synthetic shortcut')
        self.assertTrue(self.errors)
        self.assertNotIn('Done.', [segment['text'] for segment in self.spoken])

    def test_missing_authorization_cannot_remove_shortcut(self):
        self.seed_shortcut()
        system.handle_set_autostart(self.app, {'enabled': False}, self.ctx)
        self.assertTrue(self.shortcut.exists())

    def test_downgrade_during_launcher_preparation_blocks_shortcut_write(self):
        def launcher():
            self.controller.begin_compact_transition()
            return 'synthetic.exe', '', 'synthetic'
        self.launcher.side_effect = launcher
        self.change(True)
        self.popen.assert_not_called()
        self.assertFalse(self.shortcut.exists())
        self.assertTrue(self.errors)

    def test_downgrade_after_write_helper_entry_blocks_child_launch(self):
        write = autostart._write_lnk
        def delayed_write(*args, **kwargs):
            self.controller.begin_compact_transition()
            return write(*args, **kwargs)
        with patch.object(autostart, '_write_lnk', side_effect=delayed_write):
            self.change(True)
        self.popen.assert_not_called()
        self.assertFalse(self.shortcut.exists())

    def test_downgrade_during_validation_blocks_shortcut_deletion(self):
        self.seed_shortcut()
        def validate():
            self.controller.begin_compact_transition()
            return autostart.STATUS_VALID
        self.validate.side_effect = validate
        self.change(False)
        self.assertTrue(self.shortcut.exists())

    def test_child_wait_does_not_hold_capability_lock(self):
        finished = threading.Event()
        worker = threading.Thread(target=lambda: (self.controller.begin_compact_transition(), finished.set()))
        def communicate(timeout=None):
            worker.start()
            self.effects.append(('transition_during_wait', finished.wait(1)))
            return 'OK', ''
        self.process.communicate = communicate
        try:
            self.change(True)
        finally:
            if worker.ident is not None:
                worker.join(2)
        self.assertIn(('transition_during_wait', True), self.effects)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.successes)

    def test_timeout_kills_and_closes_child(self):
        def communicate(timeout=None):
            raise subprocess.TimeoutExpired('fake PowerShell', timeout)
        self.process.communicate = communicate
        self.change(True)
        self.assertEqual(self.effects, ['spawn', 'killed', 'closed'])
        self.assertTrue(self.errors)

    def test_foreign_or_malformed_shortcuts_remain_untouched(self):
        self.seed_shortcut()
        for status in (autostart.STATUS_FOREIGN, autostart.STATUS_MALFORMED):
            for enabled in (True, False):
                with self.subTest(status=status, enabled=enabled):
                    self.validate.side_effect = None
                    self.validate.return_value = status
                    self.change(enabled)
                    self.assertEqual(self.shortcut.read_text(encoding='utf-8'), 'original synthetic shortcut')
                    self.popen.assert_not_called()

    def test_existing_shortcut_enable_remains_idempotent(self):
        self.seed_shortcut()
        self.change(True)
        self.assertEqual(self.shortcut.read_text(encoding='utf-8'), 'original synthetic shortcut')
        self.popen.assert_not_called()
        self.assertTrue(self.successes)
