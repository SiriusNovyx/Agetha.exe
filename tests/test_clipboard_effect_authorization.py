"""Clipboard authorization with fake Tk primitives and controlled delivery."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agetha.commands.handlers import files
from agetha.commands.handlers.support import CAPABILITY_AUTHORIZATION, DispatchCtx
from agetha.core.capabilities import Capability, CapabilityController, CapabilityPolicy, CapabilityProfile


class ClipboardApp:
    def _call_ui_sync(self, callback):
        self.before_delivery()
        return callback()


class ClipboardEffectAuthorization(unittest.TestCase):
    def setUp(self):
        self.controller = CapabilityController(CapabilityPolicy(
            CapabilityProfile.FULL, {'ENABLE_COMMAND_EXECUTION': True}))
        self.token = self.controller.authorize(Capability.ADVANCED_OS_INTEGRATION)
        self.effects, self.errors, self.spoken = [], [], []
        self.app = ClipboardApp()
        self.app._capabilities = self.controller
        self.app.before_delivery = lambda: None
        self.app.root = SimpleNamespace(
            clipboard_clear=lambda: self.effects.append('clear'),
            clipboard_append=lambda text: self.effects.append(('append', text)),
            update=lambda: self.effects.append('update'))
        self.app._show_op_error = self.errors.append
        self.app._speak_and_continue = lambda segments, *_args: self.spoken.extend(segments)
        self.ctx = DispatchCtx('copy a synthetic value', 'neutral', [{'text': 'Done.'}], False)

    def copy(self, token=None):
        files.handle_clipboard_set(self.app, {
            'text': ' synthetic value ',
            CAPABILITY_AUTHORIZATION: self.token if token is None else token,
        }, self.ctx)

    def test_current_authorization_copies_once_with_existing_trim(self):
        self.copy()
        self.assertEqual(self.effects, ['clear', ('append', 'synthetic value'), 'update'])

    def test_downgrade_before_ui_delivery_blocks_clipboard_effect(self):
        self.app.before_delivery = self.controller.begin_compact_transition
        self.copy()
        self.assertEqual(self.effects, [])
        self.assertTrue(self.errors)
        self.assertNotIn('Done.', [s['text'] for s in self.spoken])

    def test_invalid_authorization_blocks_clipboard_effect(self):
        self.copy(object())
        self.assertEqual(self.effects, [])

    def test_missing_authorization_blocks_clipboard_effect(self):
        files.handle_clipboard_set(self.app, {'text': 'synthetic value'}, self.ctx)
        self.assertEqual(self.effects, [])

    def test_missing_tk_root_does_not_launch_unguarded_clipboard_fallback(self):
        self.app.root = None
        with patch('agetha.commands.system_commands.subprocess.run') as run:
            self.copy()
        run.assert_not_called()
        self.assertTrue(self.errors)

    def test_standalone_tk_helper_retains_existing_behavior(self):
        self.assertEqual(files.copy_to_clipboard('synthetic value', self.app.root),
                         '[copied to clipboard]')
        self.assertEqual(self.effects, ['clear', ('append', 'synthetic value'), 'update'])

    def test_ui_event_processing_holds_no_capability_lock(self):
        advanced = threading.Event()
        threads = []
        def update():
            def transition():
                self.controller.begin_compact_transition()
                advanced.set()
            worker = threading.Thread(target=transition)
            threads.append(worker)
            worker.start()
            self.effects.append(('transition_finished_during_update', advanced.wait(1)))
        self.app.root.update = update
        try:
            self.copy()
        finally:
            for worker in threads:
                worker.join(2)
        self.assertIn(('transition_finished_during_update', True), self.effects)
        self.assertTrue(all(not worker.is_alive() for worker in threads))
