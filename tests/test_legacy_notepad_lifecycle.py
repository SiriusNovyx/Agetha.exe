"""Actual legacy Notepad contracts. Run only in isolated disposable storage.

The existing comparison harness keeps host query, dispatch, slot and speech
lifetimes real. Its note reader, provider, worker, UI, audio and storage sinks
are synthetic. This module performs no persistent writes or native effects.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main
from agetha.app_config import AppSettings
from agetha.commands.handlers import memory_presentation
from agetha.commands.handlers.support import DispatchCtx
from agetha.core.continuation_lifecycle import ContinuationLifecycle, RequestState
from tests import test_continuation_comparison as comparison

A = 'NOTEPAD_REQUEST_A'
B = 'DIRECT_REQUEST_B'


class LegacyNotepadLifecycle(unittest.TestCase):
    def setUp(self):
        self.h = comparison.ContinuationComparison(methodName='runTest')
        self.h.case('legacy')
        self.addCleanup(self.h.doCleanups)
        self.app = self.h.app
        self.app._persistent_mood = None
        self.app._input_var = SimpleNamespace(get=lambda: B, set=lambda _: None)
        self.query_inputs, self.releases, self.cleanup_events = [], [], []
        self.dispatches = []
        self.cleanup_hook = lambda request: None
        self.lifecycle = self.app._get_continuation_lifecycle()
        admit = self.lifecycle.admit

        def observed_admit(identity, **kwargs):
            previous_cleanup = kwargs.get('cleanup')
            def cleanup(request):
                self.cleanup_events.append((request.identity, request.state, request.context))
                if previous_cleanup is not None:
                    previous_cleanup(request)
                self.cleanup_hook(request)
            return admit(identity, **{**kwargs, 'cleanup': cleanup})
        self.lifecycle.admit = observed_admit
        query = self.app._ai_query
        def observed_query(*args, **kwargs):
            self.query_inputs.append((args, kwargs))
            return query(*args, **kwargs)
        self.app._ai_query = observed_query
        release = self.app._release_ai_operation
        def observed_release(token, **kwargs):
            result = release(token, **kwargs)
            self.releases.append((token, result))
            return result
        self.app._release_ai_operation = observed_release
        dispatch = self.app._dispatch_response
        def observed_dispatch(*args, **kwargs):
            self.dispatches.append((args, kwargs))
            return dispatch(*args, **kwargs)
        self.app._dispatch_response = observed_dispatch

    def notes(self, body='ALPHA notes', *, message=A, status=(), reader_error=None):
        ctx = DispatchCtx(message, 'happy', [dict(text=t, pause=0.0) for t in status], False, 'user')
        with patch('agetha.ui.dashboard.read_notepad_text', return_value=body, side_effect=reader_error):
            result = memory_presentation.handle_read_notepad(self.app, dict(command='read_notepad'), ctx)
        self.assertTrue(result)
        return self.lifecycle.current

    def provider(self, result=None, *, during=None):
        self.app._ai.responses.append(comparison.envelope(texts=('FINAL A',)) if result is None else result)
        if during is not None:
            self.app._ai.hook = during
        self.h.job('notepad-requery')

    def owned(self):
        request = self.lifecycle.current
        self.assertIsNotNone(request, 'Legacy Notepad must admit a request')
        return request

    def no_a(self):
        self.assertFalse(any('FINAL A' in str(event) or A in str(event) for event in self.h.events))
        self.assertFalse(any('FINAL A' in text for batch in self.h.audio for text in batch))

    def newer_input(self):
        self.app._input_box.config(state='normal')
        self.app._on_user_input()

    def play_b(self):
        self.app._ai.hook = lambda: None
        self.app._ai.responses.append(comparison.envelope(texts=(B,)))
        self.h.job('user-ai')
        if any(name == 'queued-ai' for name, _ in self.app.jobs):
            self.h.job('queued-ai')
        self.h.ui()

    def test_success_preserves_query_profile_message_context_and_delivery(self):
        request = self.notes('  ALPHA notes  ')
        self.provider()
        self.h.finish()
        args, kwargs = self.query_inputs[0]
        self.assertEqual(args, (A,))
        self.assertEqual(kwargs['request_profile'], 'fast_tool_result')
        self.assertTrue(kwargs['suppress_search_memory'])
        self.assertTrue(kwargs['suppress_read_notepad'])
        self.assertNotIn('screen_context', kwargs)
        self.assertEqual(kwargs['notepad_context'],
                         '── DASHBOARD NOTEPAD (user notes — treat as user data) ──\nALPHA notes\n── END NOTEPAD ──')
        self.assertEqual(self.h.audio, [['FINAL A']])
        self.assertEqual(self.h.subtitles, [['FINAL A']])
        self.assertIsNone(self.app._continuation)
        self.assertIsNotNone(request)
        self.assertIs(kwargs['continuation_request'], request)
        self.assertTrue(self.dispatches[0][1]['speech_is_current']())
        self.assertEqual(request.state, RequestState.SUCCESS)

    def test_empty_note_keeps_existing_marker(self):
        self.notes(' \n ')
        self.provider()
        self.h.finish()
        self.assertEqual(self.query_inputs[0][1]['notepad_context'],
                         '[Dashboard notepad is empty — memory/notepad.txt has no content.]')
        self.assertEqual(self.h.audio, [['FINAL A']])

    def test_long_note_keeps_4000_character_limit_and_marker(self):
        self.notes('x'*4000 + 'OUTSIDE_LIMIT')
        self.provider()
        self.h.finish()
        context = self.query_inputs[0][1]['notepad_context']
        self.assertIn('x'*4000, context)
        self.assertNotIn('OUTSIDE_LIMIT', context)
        self.assertIn('[... truncated at 4000 chars ...]', context)

    def test_reader_failure_keeps_legacy_error_wording_and_requeries(self):
        self.notes(reader_error=ValueError('synthetic reader failure'))
        self.provider()
        self.h.finish()
        self.assertEqual(self.query_inputs[0][1]['notepad_context'],
                         '[notepad read error: synthetic reader failure]')
        self.assertEqual(self.h.audio, [['FINAL A']])

    def test_cached_screen_fallback_remains_selected_by_host(self):
        self.app._last_screen_text = 'SYNTHETIC CACHED SCREEN'
        self.notes()
        self.provider()
        self.h.finish()
        self.assertNotIn('screen_context', self.query_inputs[0][1])
        self.assertIn('SYNTHETIC CACHED SCREEN', self.app._ai.calls[0]['screen_context'])

    def test_status_ordering_keeps_early_requery_without_retry(self):
        self.notes(status=('STATUS',))
        self.h.ui()
        self.assertEqual(self.h.audio, [['STATUS']])
        self.assertTrue(self.app._speech_active)
        self.h.job('notepad-requery')
        self.assertEqual(self.app._ai.calls, [])
        self.h.complete()
        self.assertEqual(self.app.jobs, [])
        self.assertEqual(self.h.audio, [['STATUS']])

    def test_refused_worker_is_terminal_and_reports_existing_notice(self):
        self.app.refuse_worker = True
        self.notes()
        self.h.ui()
        self.assertEqual(self.app.jobs, [])
        self.assertEqual(self.app._ai.calls, [])
        self.assertFalse(self.app._ai_busy)
        self.assertEqual(self.app.notices, ["I couldn't start the next step. Please try again."])
        self.assertEqual(self.owned().state, RequestState.FAILED)
        self.assertEqual(len(self.cleanup_events), 1)

    def test_worker_start_exception_keeps_exception_and_cleans_once(self):
        self.app.start_failure = True
        with self.assertRaisesRegex(RuntimeError, 'synthetic worker startup failure'):
            self.notes()
        self.h.ui()
        self.assertFalse(self.app._ai_busy)
        self.assertEqual(self.owned().state, RequestState.FAILED)
        self.assertIsNone(self.owned().context)
        self.assertEqual(len(self.cleanup_events), 1)
        self.assertEqual(self.app.notices, ["I couldn't start the next step. Please try again."])

    def test_cancel_before_provider_withdraws_old_callback(self):
        self.notes()
        self.app._on_cancel_ai()
        self.h.job('notepad-requery')
        self.h.ui()
        self.assertEqual(self.app._ai.calls, [])
        self.no_a()
        self.assertEqual(self.owned().state, RequestState.CANCELED)

    def test_cancel_during_provider_retains_token_until_return(self):
        self.notes()
        observed = []
        def cancel():
            token = self.app._ai_operation_token
            self.assertIsNotNone(token)
            self.app._on_cancel_ai()
            self.assertTrue(self.app._ai_busy)
            self.assertIs(self.app._ai_operation_token, token)
            self.assertEqual(self.releases, [])
            observed.append(token)
        self.provider(during=cancel)
        self.h.ui()
        self.no_a()
        self.assertEqual(self.releases, [(observed[0], True)])
        self.assertFalse(self.app._ai_busy)

    def test_new_direct_input_suppresses_A_and_allows_B(self):
        self.notes()
        self.provider(during=self.newer_input)
        self.assertFalse(self.app._ai_busy)
        self.play_b()
        self.no_a()
        self.assertEqual(self.h.audio, [[B]])

    def test_context_generation_change_suppresses_held_provider(self):
        self.notes()
        self.app._invalidate_request_context()
        self.h.job('notepad-requery')
        self.h.ui()
        self.assertEqual(self.app._ai.calls, [])
        self.no_a()

    def test_UI_generation_change_during_provider_suppresses_result(self):
        self.notes()
        self.provider(during=self.app._invalidate_continuation_ui_delivery)
        self.h.ui()
        self.no_a()
        self.assertEqual(self.dispatches, [])

    def test_shutdown_before_worker_delivery_prevents_query(self):
        self.notes()
        self.app._disable_input_for_close()
        self.h.job('notepad-requery')
        self.h.ui()
        self.assertEqual(self.app._ai.calls, [])
        self.assertEqual(self.h.audio, [])
        self.assertEqual(self.owned().state, RequestState.CANCELED)

    def test_shutdown_during_provider_discards_result_and_releases(self):
        self.notes()
        self.provider(during=self.app._disable_input_for_close)
        self.h.ui()
        self.no_a()
        self.assertFalse(self.app._ai_busy)
        self.assertEqual(len(self.releases), 1)

    def test_duplicate_worker_callback_queries_and_publishes_once(self):
        self.notes()
        name, callback = self.app.jobs.pop()
        self.assertEqual(name, 'notepad-requery')
        callback()
        self.h.finish()
        callback()
        self.h.finish()
        self.assertEqual(len(self.app._ai.calls), 1)
        self.assertEqual(len(self.dispatches), 1)
        self.assertEqual(self.h.audio, [['answer']])
        self.assertEqual(self.h.subtitles, [['answer']])
        self.assertEqual(len(self.cleanup_events), 1)
        self.assertEqual(len(self.releases), 1)

    def test_late_provider_result_cannot_adopt_new_legacy_request(self):
        self.notes('A notes')
        first = self.lifecycle.current
        self.provider(during=lambda: self.notes('B notes', message=B))
        self.h.ui()
        self.no_a()
        self.assertEqual(self.dispatches, [])
        self.assertIsNot(self.owned(), first)
        self.app._ai.hook = lambda: None
        self.provider(comparison.envelope(texts=(B,)))
        self.h.finish()
        self.assertEqual(self.h.audio, [[B]])
        self.assertIn('B notes', self.query_inputs[-1][1]['notepad_context'])

    def test_queued_final_speech_expires_before_UI_delivery(self):
        self.notes()
        self.provider()
        self.assertEqual(len(self.dispatches), 1)
        self.assertEqual(self.h.audio, [])
        self.app._invalidate_request_context()
        self.h.ui()
        self.no_a()
        self.assertFalse(self.app._speech_active)

    def test_queued_STATUS_speech_expires_on_UI_generation(self):
        self.notes(status=('OLD STATUS A',))
        self.app._invalidate_continuation_ui_delivery()
        self.h.ui()
        self.assertEqual(self.h.audio, [])
        self.assertEqual(self.h.subtitles, [])

    def test_late_UI_callbacks_cannot_clear_successor_speech(self):
        self.notes()
        self.provider()
        held = list(self.app.ui)
        self.app.ui.clear()
        self.newer_input()
        self.app._speech_active = False
        self.app._speech_operation_token = None
        self.play_b()
        token, state = self.app._speech_operation_token, self.app._state
        for callback in held:
            callback()
        self.h.ui()
        self.no_a()
        self.assertIs(self.app._speech_operation_token, token)
        self.assertEqual(self.app._state, state)
        self.assertEqual(self.h.audio, [[B]])

    def test_failed_reservation_does_not_release_other_owner(self):
        self.notes()
        token = object()
        self.app._ai_busy = True
        self.app._ai_operation_token = token
        self.h.job('notepad-requery')
        self.h.ui()
        self.assertEqual(self.app._ai.calls, [])
        self.assertIs(self.app._ai_operation_token, token)
        self.assertTrue(self.app._ai_busy)
        self.assertEqual(self.releases, [])
        self.assertEqual(self.owned().state, RequestState.FAILED)
        self.assertIsNone(self.owned().context)

    def test_success_releases_exact_slot_once_and_clears_context_once(self):
        self.notes()
        request = self.owned()
        tokens = []
        self.provider(during=lambda: tokens.append(self.app._ai_operation_token))
        self.h.finish()
        self.assertEqual(self.releases, [(tokens[0], True)])
        self.assertEqual(self.cleanup_events, [(request.identity, RequestState.SUCCESS, None)])
        self.assertIsNone(request.context)
        self.lifecycle.invalidate('after_success')
        self.assertEqual(len(self.cleanup_events), 1)
        self.assertEqual(len(self.releases), 1)

    def test_query_UI_schedule_exception_releases_and_terminates(self):
        self.notes()
        with patch.object(self.app, '_schedule_owned_ai_ui', side_effect=RuntimeError('synthetic queue refusal')):
            self.h.job('notepad-requery')
        self.h.ui()
        self.assertFalse(self.app._ai_busy)
        self.assertIsNone(self.app._ai_operation_token)
        self.assertEqual(len(self.releases), 1)
        self.assertEqual(self.owned().state, RequestState.FAILED)

    def test_provider_exception_and_timeout_keep_no_final_behavior(self):
        for error in (RuntimeError('synthetic provider error'), TimeoutError('synthetic timeout')):
            with self.subTest(error=type(error).__name__):
                self.notes()
                self.app._ai.responses.append(error)
                self.h.job('notepad-requery')
                self.h.ui()
                self.assertEqual(self.h.audio, [])
                self.assertFalse(self.app._ai_busy)
                self.assertEqual(self.owned().state, RequestState.FAILED)

    def test_identity_is_shared_by_producer_result_delivery_and_cleanup(self):
        request = self.notes()
        self.assertIsNotNone(request)
        self.provider()
        kwargs = self.query_inputs[0][1]
        self.assertIs(kwargs['continuation_request'], request)
        self.assertEqual(request.identity.generation, 0)
        self.assertTrue(request.identity.request_id)
        self.assertEqual(self.cleanup_events[0][0], request.identity)
        retained = self.dispatches[0][1]['speech_is_current']
        self.assertTrue(retained())
        self.notes('B notes', message=B)
        self.assertFalse(retained())

    def test_reader_return_after_successor_admission_cannot_schedule_A(self):
        entered = []
        def read():
            entered.append('reader A')
            self.notes('B notes', message=B)
            return 'A notes'
        with patch('agetha.ui.dashboard.read_notepad_text', side_effect=read):
            memory_presentation.handle_read_notepad(self.app, {}, DispatchCtx(A, 'neutral', [], False, 'user'))
        self.assertEqual(entered, ['reader A'])
        self.assertEqual([name for name, _ in self.app.jobs], ['notepad-requery'])
        self.provider(comparison.envelope(texts=(B,)))
        self.h.finish()
        self.assertEqual(self.query_inputs[0][0], (B,))
        self.assertEqual(self.h.audio, [[B]])

    def test_followup_notepad_command_remains_passive(self):
        self.notes()
        self.provider(comparison.envelope('read_notepad', ('FINAL A',)))
        self.h.finish()
        self.assertEqual(len(self.app._ai.calls), 1)
        self.assertEqual(self.app.jobs, [])

    def test_repeated_text_segments_keep_order_and_multiplicity(self):
        self.notes()
        self.provider(comparison.envelope(texts=('SAME', 'SAME', 'LAST')))
        self.h.finish()
        self.assertEqual(self.h.audio, [['SAME', 'SAME', 'LAST']])
        self.assertEqual(self.h.subtitles, [['SAME', 'SAME', 'LAST']])

    def test_legacy_request_adds_no_aggregate_deadline(self):
        self.notes()
        self.h.fixture.now = 10000.0
        self.provider()
        self.h.finish()
        self.assertEqual(self.h.audio, [['FINAL A']])

    def test_shutdown_expires_already_queued_final(self):
        self.notes()
        self.provider()
        self.app._disable_input_for_close()
        self.h.ui()
        self.assertEqual(self.h.audio, [])
        self.assertEqual(self.h.subtitles, [])

    def test_streaming_keeps_legacy_arguments_and_one_final(self):
        settings = AppSettings({**self.h.fixture.settings.raw, 'ENABLE_STREAMING': 'yes'})
        with patch.object(main, '_SETTINGS', settings):
            self.notes()
            self.provider()
            self.h.finish()
        self.assertEqual(self.app._ai.calls[0]['request_profile'], 'fast_tool_result')
        self.assertEqual(self.h.audio, [['FINAL A']])
        self.assertEqual(self.h.subtitles, [['FINAL A']])
        self.assertEqual(len(self.releases), 1)

    def test_managed_thread_start_exception_after_entry_keeps_success(self):
        class EnteredThread:
            def __init__(self, *, target, daemon):
                self.target = target
            def start(self):
                self.target()
                raise RuntimeError('synthetic start failure after entry')
        with patch.object(type(self.app), '_start_worker', main.CompanionApp._start_worker), \
             patch.object(main.threading, 'Thread', EnteredThread), \
             self.assertRaisesRegex(RuntimeError, 'synthetic start failure after entry'):
            self.notes()
        self.h.finish()
        self.assertEqual(len(self.app._ai.calls), 1)
        self.assertEqual(self.h.audio, [['answer']])
        self.assertEqual(self.owned().state, RequestState.SUCCESS)
        self.assertEqual(len(self.cleanup_events), 1)
        self.assertEqual(len(self.releases), 1)
        self.assertFalse(self.app._workers)
        self.assertEqual(self.app.notices, [])

    def test_delivery_exception_keeps_terminal_cleanup_and_release_once(self):
        self.notes()
        def fail(*args, **kwargs):
            raise RuntimeError('synthetic delivery failure')
        self.app._dispatch_response = fail
        with self.assertRaisesRegex(RuntimeError, 'synthetic delivery failure'):
            self.provider()
        self.assertFalse(self.app._ai_busy)
        self.assertEqual(len(self.releases), 1)
        self.assertEqual(self.owned().state, RequestState.SUCCESS)
        self.assertEqual(len(self.cleanup_events), 1)

    def test_failed_start_notice_cannot_invalidate_successor_from_cleanup(self):
        self.app.refuse_worker = True
        def successor(request):
            self.cleanup_hook = lambda _: None
            self.app.refuse_worker = False
            self.notes('B notes', message=B)
        self.cleanup_hook = successor
        self.notes()
        self.h.ui()
        self.assertEqual(self.app.notices, [])
        self.provider(comparison.envelope(texts=(B,)))
        self.h.finish()
        self.assertEqual(self.h.audio, [[B]])
        self.assertEqual(self.query_inputs[0][0], (B,))

    def test_provider_eligibility_guard_expires_with_original_request(self):
        self.notes()
        guards = []
        entry_validity = []
        query = self.app._ai.query
        def observe(**kwargs):
            guard = kwargs.get('provider_authorization')
            guards.append(guard)
            entry_validity.append(guard() if callable(guard) else None)
            return query(**kwargs)
        with patch.object(self.app._ai, 'query', observe):
            self.provider()
        self.assertEqual(len(guards), 1)
        self.assertTrue(callable(guards[0]), 'Provider must retain producer eligibility')
        self.assertEqual(entry_validity, [True])
        self.assertFalse(guards[0]())  # Finished work cannot initiate another provider.
        self.notes('B notes', message=B)
        self.assertFalse(guards[0]())

    def test_queued_final_cannot_adopt_same_generation_successor(self):
        self.notes()
        self.provider()
        self.notes('B notes', message=B)
        self.app._ai.responses.append(comparison.envelope(texts=(B,)))
        self.h.finish()
        self.no_a()
        self.assertEqual(self.h.audio, [[B]])
        self.assertEqual(self.h.subtitles, [[B]])


class LegacyNotepadCompatibility(unittest.TestCase):
    def test_raw_thread_host_keeps_query_contract_and_runs_once(self):
        queries, deliveries, held = [], [], []
        class HeldThread:
            def __init__(self, *, target, daemon):
                self.target = target
                if not daemon:
                    raise AssertionError('Legacy worker must remain daemon')
            def start(self):
                held.append(self.target)
        app = SimpleNamespace(
            _ai_query=lambda *args, **kwargs: queries.append((args, kwargs)) or comparison.envelope(),
            _dispatch_response=lambda *args, **kwargs: deliveries.append((args, kwargs)),
            _speak_and_continue=lambda *args, **kwargs: None,
        )
        with patch('agetha.ui.dashboard.read_notepad_text', return_value='COMPAT notes'), \
             patch.object(memory_presentation.threading, 'Thread', HeldThread):
            result = memory_presentation.handle_read_notepad(app, {}, DispatchCtx(A, 'neutral', [], False, 'user'))
        self.assertTrue(result)
        self.assertEqual(len(held), 1)
        held[0]()
        held[0]()
        self.assertEqual(len(queries), 1)
        self.assertEqual(queries[0][0], (A,))
        self.assertEqual(queries[0][1]['request_profile'], 'fast_tool_result')
        self.assertIn('COMPAT notes', queries[0][1]['notepad_context'])
        self.assertEqual(len(deliveries), 1)

    def test_raw_thread_start_exception_propagates_without_late_work(self):
        held, queries = [], []
        class FailedThread:
            def __init__(self, *, target, daemon):
                held.append(target)
            def start(self):
                raise RuntimeError('synthetic raw-thread start failure')
        app = SimpleNamespace(_ai_query=lambda *args, **kwargs: queries.append(args),
                              _dispatch_response=lambda *args, **kwargs: None)
        with patch('agetha.ui.dashboard.read_notepad_text', return_value='COMPAT notes'), \
             patch.object(memory_presentation.threading, 'Thread', FailedThread), \
             self.assertRaisesRegex(RuntimeError, 'synthetic raw-thread start failure'):
            memory_presentation.handle_read_notepad(app, {}, DispatchCtx(A, 'neutral', [], False, 'user'))
        held[0]()
        self.assertEqual(queries, [])
