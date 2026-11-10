"""Routine command confirmation policy with synthetic settings and modal UI."""

import unittest
from unittest.mock import patch

from agetha.app_config import AppSettings
from agetha.commands import command_guard, command_handlers
from agetha.commands.command_guard import CommandGuard
from agetha.core.capabilities import CapabilityController, CapabilityPolicy
from tests import test_command_specs as fixtures


ROUTINE_COMMANDS = (
    'list_dir', 'list_directory', 'read_file', 'search_files',
    'set_volume', 'open_settings', 'play_sound', 'show_dialog', 'search_web',
)
OS_COMMANDS = ('search_files', 'set_volume', 'open_settings', 'play_sound', 'show_dialog')


def settings(**changes):
    return AppSettings({
        'COMPACT_MODE': 'no', 'ENABLE_COMMAND_EXECUTION': 'yes',
        'ENABLE_COMMAND_CONFIRMATIONS': 'yes', 'ENABLE_AGENT_CONTINUATION': 'yes',
        'ENABLE_WEB_RAG': 'yes',
        **changes,
    })


class RoutineConfirmationPolicy(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(command_guard, 'get_settings', return_value=settings()))
        self.guard = CommandGuard()
        self.modal = self.enterContext(patch.object(CommandGuard, '_native_confirm', return_value=False))

    def test_routine_commands_proceed_without_modal_confirmation(self):
        for command in ROUTINE_COMMANDS:
            with self.subTest(command=command):
                self.modal.reset_mock()
                self.assertTrue(self.guard.check(command, {}))
                self.modal.assert_not_called()

    def test_read_file_alias_has_same_confirmation_behavior_as_read_document(self):
        self.assertTrue(self.guard.check('read_document', {'path': 'synthetic.txt'}))
        self.assertTrue(self.guard.check('read_file', {'path': 'synthetic.txt'}))
        self.modal.assert_not_called()

    def test_destructive_and_unknown_commands_still_require_confirmation(self):
        for command in ('delete_file', 'write_file', 'run_command', 'shutdown', 'restart', 'unknown-command'):
            with self.subTest(command=command):
                self.modal.reset_mock()
                self.assertFalse(self.guard.check(command, {}))
                self.modal.assert_called_once()

    def test_remaining_caution_commands_still_require_confirmation(self):
        for command in ('open_app', 'open_file', 'copy_to_clipboard', 'set_clipboard', 'set_wallpaper', 'type_text'):
            with self.subTest(command=command):
                self.modal.reset_mock()
                self.assertFalse(self.guard.check(command, {}))
                self.modal.assert_called_once()

    def test_canceled_destructive_request_does_not_open_confirmation(self):
        self.assertFalse(self.guard.check('delete_file', {}, cancel_check=lambda: True))
        self.modal.assert_not_called()


class RoutineDispatchPolicy(unittest.TestCase):
    def app(self, current_settings):
        app = fixtures._dispatch_test_app()
        app._capabilities = CapabilityController(CapabilityPolicy.from_settings(current_settings))
        app._show_op_error = lambda *_args: None
        app._guard = CommandGuard()
        return app

    def test_show_dialog_opens_only_the_requested_dialog(self):
        current_settings = settings()
        with patch.object(command_guard, 'get_settings', return_value=current_settings), \
             patch.object(command_handlers, 'get_settings', return_value=current_settings), \
             patch.object(CommandGuard, '_native_confirm', return_value=True) as modal, \
             patch('agetha.core.companion_stats.update_stats'), \
             patch('agetha.core.emotion_engine.note'):
            command_handlers.dispatch(self.app(current_settings), {
                'command': 'show_dialog', 'dialog_type': 'info', 'title': 'Synthetic title',
                'message': 'Synthetic message', 'mood': 'neutral', 'segments': [],
            }, 'show this message', origin='user')
        modal.assert_called_once()
        self.assertEqual(modal.call_args.args[:2], ('Synthetic title', 'Synthetic message'))

    def test_compact_and_execution_gates_still_precede_routine_effect_handlers(self):
        for current_settings in (settings(COMPACT_MODE='yes'), settings(ENABLE_COMMAND_EXECUTION='no')):
            called = []
            handlers = {name: lambda *_args: called.append('effect') or True for name in OS_COMMANDS}
            with patch.object(command_guard, 'get_settings', return_value=current_settings), \
                 patch.object(command_handlers, 'get_settings', return_value=current_settings), \
                 patch.dict(command_handlers.HANDLERS, handlers), \
                 patch.object(CommandGuard, '_native_confirm') as modal:
                app = self.app(current_settings)
                for command in OS_COMMANDS:
                    with self.subTest(command=command, compact=current_settings.compact_mode):
                        command_handlers.dispatch(app, {'command': command, 'segments': []},
                                                  'synthetic request', origin='user')
                        self.assertEqual(called, [])
                modal.assert_not_called()

    def test_search_web_feature_gate_controls_search_without_os_execution(self):
        for compact in ('yes', 'no'):
            for enabled in ('yes', 'no'):
                with self.subTest(compact=compact, enabled=enabled):
                    current_settings = settings(COMPACT_MODE=compact,
                        ENABLE_COMMAND_EXECUTION='no', ENABLE_WEB_RAG=enabled)
                    called = []
                    with patch.object(command_guard, 'get_settings', return_value=current_settings), \
                         patch.object(command_handlers, 'get_settings', return_value=current_settings), \
                         patch.dict(command_handlers.HANDLERS, {
                             'search_web': lambda *_args: called.append('search') or True,
                         }), \
                         patch.object(CommandGuard, '_native_confirm', return_value=False) as modal, \
                         patch('agetha.core.companion_stats.update_stats'), \
                         patch('agetha.core.emotion_engine.note'):
                        command_handlers.dispatch(self.app(current_settings), {
                            'command': 'search_web', 'query': 'synthetic topic', 'segments': [],
                        }, 'search this topic', origin='user')
                    self.assertEqual(called, ['search'] if enabled == 'yes' else [])
                    modal.assert_not_called()

    def test_ambient_and_tool_results_cannot_enter_routine_effect_handlers(self):
        current_settings = settings()
        called = []
        handlers = {name: lambda *_args: called.append('effect') or True for name in ROUTINE_COMMANDS}
        with patch.object(command_guard, 'get_settings', return_value=current_settings), \
             patch.object(command_handlers, 'get_settings', return_value=current_settings), \
             patch.dict(command_handlers.HANDLERS, handlers), \
             patch.object(CommandGuard, '_native_confirm') as modal:
            app = self.app(current_settings)
            for origin in ('ambient', 'tool_result'):
                for command in ROUTINE_COMMANDS:
                    with self.subTest(command=command, origin=origin):
                        command_handlers.dispatch(app, {'command': command,
                            'ambient_relevance': 'important', 'segments': []}, None, origin=origin)
                        self.assertEqual(called, [])
            modal.assert_not_called()
