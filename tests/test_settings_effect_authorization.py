"""Settings launches with real authorization and fake native effects."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agetha.commands.handlers import system
from agetha.commands.handlers.support import CAPABILITY_AUTHORIZATION, DispatchCtx
from agetha.core.capabilities import Capability, CapabilityController, CapabilityPolicy, CapabilityProfile
from agetha.platform import win_integration


class SettingsEffectAuthorization(unittest.TestCase):
    def setUp(self):
        self.controller = CapabilityController(CapabilityPolicy(
            CapabilityProfile.FULL, {'ENABLE_COMMAND_EXECUTION': True}))
        self.token = self.controller.authorize(Capability.ADVANCED_OS_INTEGRATION)
        self.errors, self.successes, self.spoken = [], [], []
        self.app = SimpleNamespace(
            _capabilities=self.controller,
            _show_op_error=self.errors.append, _show_op_success=self.successes.append,
            _speak_and_continue=lambda segments, *_args: self.spoken.extend(segments))
        self.ctx = DispatchCtx(None, 'neutral', [{'text': 'Done.'}], False)
        self.enterContext(patch.object(win_integration, 'IS_WINDOWS', True))
        self.launch = self.enterContext(patch.object(win_integration.os, 'startfile', create=True))
        self.enterContext(patch.object(system, '_schedule_app_ui', side_effect=lambda app, cb: cb()))

    def open(self, page='display', authorization=None):
        system.handle_open_settings(self.app, {
            'page': page,
            CAPABILITY_AUTHORIZATION: self.token if authorization is None else authorization}, self.ctx)

    def test_current_authorization_preserves_allowlisted_launch_and_success(self):
        self.open()
        self.launch.assert_called_once_with('ms-settings:display')
        self.assertTrue(self.successes)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.spoken, self.ctx.segments)

    def test_downgrade_during_page_normalization_blocks_native_launch(self):
        controller = self.controller
        class Page(str):
            def strip(self):
                controller.begin_compact_transition()
                return super().strip()
        self.open(Page('display'))
        self.launch.assert_not_called()
        self.assertTrue(self.errors)
        self.assertEqual(self.successes, [])
        self.assertNotIn('Done.', [segment['text'] for segment in self.spoken])

    def test_missing_authorization_blocks_native_launch(self):
        system.handle_open_settings(self.app, {'page': 'display'}, self.ctx)
        self.launch.assert_not_called()

    def test_invalid_authorization_blocks_native_launch(self):
        self.open(authorization=object())
        self.launch.assert_not_called()

    def test_unlisted_page_still_fails_without_native_launch(self):
        self.open('ms-settings:display;calc.exe')
        self.launch.assert_not_called()
        self.assertTrue(self.errors)

    def test_standalone_helper_retains_allowlist_behavior(self):
        ok, _message = win_integration.open_settings('display')
        self.assertTrue(ok)
        self.launch.assert_called_once_with('ms-settings:display')
