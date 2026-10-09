"""Lifecycle boundary contracts; disposable config and fake services required."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import main
from agetha.core.continuation import ContinuationState, DecisionKind
from agetha.core.continuation_lifecycle import LegacyContinuationAdapter, RequestState
from agetha.core.context_dependencies import ContextKind, ContextRequest
from agetha.core.read_only_tools import ReadOnlyToolExecutor
from tests import test_continuation_comparison as comparison

envelope = comparison.envelope


class OrchestrationIntegration(comparison.ContinuationComparison):
    # Reuse reviewed fake queues and external sinks; do not inherit comparison tests.
    def bounded_admission(self):
        self.case('bounded')
        self.app._ai.responses.append(envelope('read_notepad', ()))
        self.app._ai_tick('A', origin='user')
        self.ui()

    def test_duplicate_final_callback_delivers_once(self):
        self.bounded_admission()
        self.job('continuation-tool')
        self.ui()
        self.app._ai.responses.append(envelope(texts=('FINAL A',)))
        original = self.app._handle_continuation_decision
        retained = []
        self.app._handle_continuation_decision = lambda decision, **kw: retained.append((decision, kw))
        self.job('continuation-provider')
        self.app._handle_continuation_decision = original
        self.assertEqual(len(retained), 1)
        decision, kw = retained[0]
        original(decision, **kw)
        self.finish()
        original(decision, **kw)
        self.finish()
        self.assertEqual(self.audio, [['FINAL A']])
        self.assertEqual(self.subtitles, [['FINAL A']])

    def test_deadline_before_tool_delivery_terminates_without_effect(self):
        self.bounded_admission()
        self.fixture.now = 120.0
        self.job('continuation-tool')
        self.ui()
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertEqual(len(self.app._ai.calls), 1)
        self.assertEqual(self.audio, [])

    def test_profile_invalidation_terminates_held_work_without_resurrection(self):
        self.bounded_admission()
        self.app._invalidate_continuation_ui_delivery()
        self.job('continuation-tool')
        self.finish()
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertEqual(len(self.app._ai.calls), 1)
        self.assertEqual(self.audio, [])

    def test_legacy_duplicate_worker_delivery_queries_and_speaks_once(self):
        self.case('legacy')
        self.provider_ready('search_memory')
        name, retained = self.app.jobs.pop()
        self.assertEqual(name, 'memory-requery')
        retained()
        self.finish()
        retained()
        self.finish()
        self.assertEqual(len(self.app._ai.calls), 1)
        self.assertEqual(self.audio, [['answer']])
        self.assertEqual(self.subtitles, [['answer']])

    def admit_adapter(self, path, feature='read_notepad', *, streaming=False, texts=(), allow_voice=True):
        self.case(path, streaming=streaming)
        self.app._presence_decision = lambda: SimpleNamespace(allow_voice=allow_voice)
        response = envelope(feature, texts, query='synthetic query')
        self.app._last_screen_text = 'CACHED screen'
        if path == 'bounded':
            started = self.app._continuation.start('A', authority_origin='user')
            context_current = self.app._capture_context_validity()
            epoch = self.app._continuation_ui_epoch
            request = self.app._admit_bounded_continuation(started, validity=lambda: (
                context_current() and epoch == self.app._continuation_ui_epoch
            ))
            decision = self.app._continuation.accept_initial_model_response(
                started.session_id, started.generation, response)
            self.app._handle_continuation_decision(decision)
        elif feature == 'search_memory':
            self.start(feature, texts)
            request = self.app._continuation_lifecycle.current
        else:
            # Exercise unmigrated legacy query contracts behind the adapter.
            # Keep the real query/prompt/dispatch/speech boundaries; readers are
            # synthetic. Production web/Notepad handlers remain unchanged.
            adapter = LegacyContinuationAdapter(self.app._get_continuation_lifecycle())
            current = self.app._capture_context_validity()
            epoch = self.app._continuation_ui_epoch
            request = adapter.admit(generation=self.app._context_request_epoch,
                validity=lambda: current() and epoch == self.app._continuation_ui_epoch)
            if feature == 'search_web':
                context, flags = 'ALPHA web', dict(suppress_web_rag=True)
                field = 'web_rag_context'
            else:
                context, flags = 'ALPHA notes', dict(suppress_search_memory=True, suppress_read_notepad=True)
                field = 'notepad_context'
            request.set_context(context)
            adapter.start(request,
                lambda run, failed: self.app._start_worker(run, name='legacy-adapter', on_start_failure=failed),
                lambda value: self.app._ai_query('A', request_profile='fast_tool_result',
                    result_is_current=request.work_is_current, continuation_request=request,
                    **{field: value}, **flags),
                lambda value, valid: self.app._dispatch_response(value, 'A', origin='tool_result',
                    speech_is_current=valid))
        self.ui()
        self.assertIsNotNone(request)
        return request

    def ready_adapter_provider(self, path, feature='read_notepad', **kw):
        request = self.admit_adapter(path, feature, **kw)
        if path == 'bounded':
            self.job('continuation-tool')
            self.ui()
        return request

    def test_successful_web_notepad_and_memory_preserve_profiles_context_and_delivery(self):
        for path in ('legacy', 'bounded'):
            for feature, marker in (('search_web', 'ALPHA web'),
                    ('read_notepad', 'ALPHA notes'), ('search_memory', 'ALPHA memory')):
                with self.subTest(path=path, feature=feature):
                    request = self.ready_adapter_provider(path, feature)
                    self.app._ai.responses.append(envelope(texts=('FINAL A',)))
                    self.finish()
                    self.assertEqual(self.audio, [['FINAL A']])
                    self.assertEqual(self.subtitles, [['FINAL A']])
                    self.assertEqual(len(self.app._ai.calls), 1)
                    call = self.app._ai.calls[0]
                    self.assertIn(marker, call['turn'])
                    self.assertEqual(call['request_profile'],
                        'fast_tool_result' if path == 'legacy' else 'tool_continuation')
                    self.assertEqual(call['screen_context'], 'CACHED screen' if path == 'legacy' else '')
                    self.assertEqual(request.state, RequestState.SUCCESS)
                    self.assertIsNone(request.context)
                    self.assertFalse(self.app._ai_busy)

    def test_streaming_and_nonstreaming_preserve_final_and_drop_released_preview(self):
        for path in ('legacy', 'bounded'):
            for streaming in (False, True):
                with self.subTest(path=path, streaming=streaming):
                    request = self.ready_adapter_provider(path, 'search_memory', streaming=streaming)
                    self.app._ai.responses.append(envelope(texts=('A', 'B')))
                    self.finish()
                    self.assertEqual(self.audio, [['A', 'B']])
                    self.assertEqual(self.subtitles, [['A', 'B']])
                    self.assertNotIn('synthetic chunk', self.app.notices)
                    self.assertEqual(request.state, RequestState.SUCCESS)

    def test_live_stream_preview_requires_owned_reservation(self):
        for path in ('legacy', 'bounded'):
            self.ready_adapter_provider(path, 'search_memory', streaming=True)
            def streaming(on_token=None, **kw):
                on_token('LIVE PREFIX')
                self.ui()
                on_token('HELD PREFIX')
                return self.app._ai.query(**kw)
            self.app._ai.query_streaming = streaming
            self.app._ai.responses.append(envelope(texts=('FINAL',)))
            self.finish()
            self.assertIn('LIVE PREFIX', self.app.notices)
            self.assertNotIn('HELD PREFIX', self.app.notices)
            self.assertEqual(self.audio, [['FINAL']])

    def test_segment_caps_order_and_repeated_text_remain_distinct(self):
        segments = ('A', 'B', 'A', *(f'S{i}' for i in range(14)))
        for path in ('legacy', 'bounded'):
            self.ready_adapter_provider(path, 'search_memory')
            self.app._ai.responses.append(envelope(texts=segments))
            self.finish()
            expected = list(segments if path == 'legacy' else segments[:16])
            self.assertEqual(self.audio, [expected])
            self.assertEqual(self.subtitles, [expected])

    def test_bounded_multiple_operations_keep_engine_policy_and_latest_context(self):
        request = self.ready_adapter_provider('bounded', 'search_web')
        self.app._ai.responses.extend([
            envelope('search_memory', (), query='second'),
            envelope('read_notepad', ()), envelope(texts=('FINAL',))])
        self.finish()
        calls = self.app._ai.calls
        self.assertEqual(len(calls), 3)
        self.assertIn('ALPHA web', calls[0]['doc_content'])
        self.assertIn('ALPHA memory', calls[1]['doc_content'])
        self.assertNotIn('ALPHA web', calls[1]['doc_content'])
        self.assertIn('ALPHA notes', calls[2]['doc_content'])
        self.assertNotIn('ALPHA memory', calls[2]['doc_content'])
        self.assertEqual(len(self.app._continuation.last_snapshot().history), 3)
        self.assertEqual(self.audio, [['FINAL']])
        self.assertEqual(request.state, RequestState.SUCCESS)

    def test_legacy_followup_tool_remains_passive(self):
        request = self.ready_adapter_provider('legacy', 'search_memory')
        self.app._ai.responses.append(envelope('search_web', ('PASSIVE',), query='second'))
        self.finish()
        self.assertEqual(len(self.app._ai.calls), 1)
        self.assertEqual(self.app.jobs, [])
        self.assertEqual(self.audio, [['PASSIVE']])
        self.assertEqual(request.state, RequestState.SUCCESS)

    def test_context_reader_failure_requeries_and_releases(self):
        self.admit_adapter('bounded')
        def broken():
            raise ValueError('SYNTHETIC private marker')
        self.app._continuation_tools = ReadOnlyToolExecutor(settings=self.fixture.settings,
            functions={'read_notepad': broken})
        self.app._ai.responses.append(envelope(texts=('RECOVERED',)))
        self.finish()
        self.assertEqual(len(self.app._ai.calls), 1)
        self.assertNotIn('SYNTHETIC private marker', self.app._ai.calls[0]['turn'])
        self.assertEqual(self.audio, [['RECOVERED']])
        self.assertFalse(self.app._ai_busy)

    def test_typed_context_after_notepad_remains_request_owned(self):
        request = self.ready_adapter_provider('bounded')
        captured = []
        def capture(**kw):
            captured.append(kw)
            return 'SYNTHETIC SCREEN A'
        target = dict(left=0, top=0, width=600, height=400, title='synthetic',
            hwnd=77, process_name='synthetic.exe', process_id=321)
        self.app._screen = SimpleNamespace(preserve_external_target=lambda: target,
            capture_text=capture, redact_for_external_context=lambda value: value,
            last_monitor_status='ocr_complete')
        self.app._ai.responses.extend([envelope('request_screen_read', ()), envelope(texts=('FINAL',))])
        self.finish()
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]['capture_target'], target)
        self.assertIn('UNTRUSTED SCREEN OCR', self.app._ai.calls[-1]['doc_content'])
        self.assertIn('SYNTHETIC SCREEN A', self.app._ai.calls[-1]['doc_content'])
        self.assertNotIn('ALPHA notes', self.app._ai.calls[-1]['doc_content'])
        self.assertEqual(len(self.app._continuation.last_snapshot().context_history), 1)
        self.assertEqual(request.state, RequestState.SUCCESS)
        self.assertEqual(self.audio, [['FINAL']])
        self.assertEqual(self.app._context_capture_targets, {})

    def test_provider_failure_and_timeout_have_one_failure_owner_and_release(self):
        for path in ('legacy', 'bounded'):
            for error in (RuntimeError('synthetic failure'), TimeoutError('synthetic timeout')):
                with self.subTest(path=path, error=type(error).__name__):
                    request = self.ready_adapter_provider(path, 'search_memory')
                    self.app._ai.responses.append(error)
                    self.finish()
                    self.assertEqual(request.state, RequestState.FAILED)
                    self.assertEqual(self.audio, [])
                    self.assertIsNone(self.app._ai_operation_token)
                    self.assertFalse(self.app._ai_busy)

    def test_explicit_cancel_before_worker_does_not_query_or_resurrect(self):
        for path in ('legacy', 'bounded'):
            request = self.admit_adapter(path, 'search_memory')
            self.app._on_cancel_ai()
            self.finish()
            self.assertEqual(request.state, RequestState.CANCELED)
            self.assertEqual(self.app._ai.calls, [])
            self.assertEqual(self.audio, [])
            self.assertFalse(self.app._ai_busy)

    def test_cancel_during_provider_retains_exact_token_until_physical_return(self):
        for path in ('legacy', 'bounded'):
            request = self.ready_adapter_provider(path, 'search_memory')
            retained, released = [], []
            release = self.app._release_ai_operation
            def release_token(token, **kw):
                released.append(token)
                return release(token, **kw)
            self.app._release_ai_operation = release_token
            def cancel():
                token = self.app._ai_operation_token
                self.app._on_cancel_ai()
                self.assertTrue(self.app._ai_busy)
                self.assertIs(self.app._ai_operation_token, token)
                self.assertEqual(released, [])
                retained.append(token)
            self.app._ai.hook = cancel
            self.app._ai.responses.append(envelope(texts=('OLD A',)))
            self.finish()
            self.assertEqual(released, retained)
            self.assertEqual(request.state, RequestState.CANCELED)
            self.assertEqual(self.audio, [])
            self.assertFalse(self.app._ai_busy)

    def test_new_direct_request_discards_A_and_delivers_B_without_context_leak(self):
        for path in ('legacy', 'bounded'):
            request = self.ready_adapter_provider(path, 'search_memory')
            def newer():
                self.app._ai.hook = lambda: None
                self.app._on_user_input()
            self.app._ai.hook = newer
            self.app._ai.responses.extend([envelope(texts=('OLD A',)), envelope(texts=('NEW B',))])
            self.finish()
            self.assertEqual(request.state, RequestState.INVALIDATED)
            self.assertEqual(self.audio, [['NEW B']])
            self.assertEqual(self.subtitles, [['NEW B']])
            self.assertNotIn('ALPHA memory', self.app._ai.calls[-1]['turn'])
            self.assertIsNone(self.app._ai_operation_token)

    def test_sequential_requests_do_not_share_identity_or_context(self):
        self.bounded_admission()
        original_state = self.app._set_state
        def state(value, *args):
            original_state(value, *args)
            if value == self.app.STATE_IDLE:
                self.app._input_box.config(state='normal')
        self.app._set_state = state
        first = self.app._continuation_lifecycle.current
        self.app._ai.responses.append(envelope(texts=('A',)))
        self.finish()
        self.app._ai.responses.extend([envelope('read_notepad', ()), envelope(texts=('B',))])
        self.app._on_user_input()
        self.finish()
        second = self.app._continuation_lifecycle.current
        self.assertIsNot(first, second)
        self.assertNotEqual(first.identity, second.identity)
        self.assertEqual(self.audio, [['A'], ['B']])
        self.assertEqual(second.state, RequestState.SUCCESS)
        self.assertIsNone(first.context)
        self.assertIsNone(second.context)

    def test_queued_final_UI_is_suppressed_after_generation_invalidation(self):
        for path in ('legacy', 'bounded'):
            request = self.ready_adapter_provider(path, 'search_memory')
            self.app._ai.responses.append(envelope(texts=('OLD A',)))
            self.job()
            self.app._invalidate_request_context()
            self.ui()
            self.assertEqual(self.audio, [])
            self.assertEqual(self.subtitles, [])
            self.assertFalse(request.delivery_is_current())

    def test_retained_speech_predicate_and_subtitle_completion_expire(self):
        for path in ('legacy', 'bounded'):
            request = self.ready_adapter_provider(path, 'search_memory')
            predicates = []
            def voice(segments, mood, **kw):
                predicates.append(kw['result_is_current'])
                self.voice(segments, mood, **kw)
            self.app._voice_out.start_speech = voice
            self.app._ai.responses.append(envelope(texts=('A',)))
            self.job()
            self.ui()
            retained = self.completions.pop()
            self.assertTrue(predicates[0]())
            self.app._invalidate_request_context()
            self.assertFalse(predicates[0]())
            retained()
            self.ui()
            self.assertEqual(self.audio, [['A']], 'Started output is not retroactively canceled')
            self.assertFalse(request.delivery_is_current())
            self.assertFalse(self.app._speech_active)

    def test_close_before_callback_delivery_cancels_held_work(self):
        for path in ('legacy', 'bounded'):
            request = self.admit_adapter(path, 'search_memory')
            self.app._disable_input_for_close()
            self.finish()
            self.assertEqual(request.state, RequestState.CANCELED)
            self.assertEqual(self.app._ai.calls, [])
            self.assertEqual(self.audio, [])

    def test_shutdown_during_provider_discards_result_and_releases(self):
        for path in ('legacy', 'bounded'):
            request = self.ready_adapter_provider(path, 'search_memory')
            self.app._ai.hook = self.app._graceful_shutdown
            self.finish()
            self.assertEqual(request.state, RequestState.CANCELED)
            self.assertEqual(self.audio, [])
            self.assertFalse(self.app._ai_busy)

    def test_compact_transition_during_provider_invalidates_and_terminates(self):
        for path in ('legacy', 'bounded'):
            request = self.ready_adapter_provider(path, 'search_memory')
            self.app._ai.hook = self.fixture.transition
            self.finish()
            self.assertEqual(request.state, RequestState.INVALIDATED)
            self.assertEqual(self.audio, [])
            self.assertFalse(self.app._ai_busy)
            if path == 'bounded':
                self.assertIsNone(self.app._continuation.active_snapshot())

    def test_bounded_status_waits_for_completion_and_keeps_same_owner(self):
        request = self.admit_adapter('bounded', texts=('STATUS',), allow_voice=False)
        self.assertEqual(self.app.jobs, [])
        self.assertEqual(self.subtitles, [['STATUS']])
        self.assertEqual(self.audio, [])  # Presence mutes STATUS only.
        self.complete()
        self.app._ai.responses.append(envelope(texts=('FINAL',)))
        self.finish()
        self.assertEqual(self.subtitles, [['STATUS'], ['FINAL']])
        self.assertEqual(self.audio, [['FINAL']])
        self.assertIs(self.app._continuation_lifecycle.current, request)
        self.assertEqual(request.state, RequestState.SUCCESS)

    def test_status_profile_invalidation_terminates_instead_of_stranding_wait(self):
        request = self.admit_adapter('bounded', texts=('STATUS',))
        self.app._invalidate_continuation_ui_delivery()
        self.complete()
        self.assertEqual(request.state, RequestState.INVALIDATED)
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertEqual(self.app.jobs, [])
        self.assertEqual(self.audio, [['STATUS']], 'Already-started STATUS remains observable')

    def test_adapted_worker_refusal_terminates_without_reservation(self):
        self.case('bounded')
        self.app.refuse_worker = True
        self.app._ai.responses.append(envelope('read_notepad', ()))
        self.app._ai_tick('A', origin='user')
        self.finish()
        request = self.app._continuation_lifecycle.current
        self.assertEqual(request.state, RequestState.FAILED)
        self.assertEqual(request.reason, 'worker_start_failed')
        self.assertEqual(self.app.jobs, [])
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertFalse(self.app._ai_busy)
        self.assertEqual(self.audio, [])

    def test_deadline_during_provider_discards_result_and_cleans_up(self):
        request = self.ready_adapter_provider('bounded')
        self.app._ai.hook = lambda: setattr(self.fixture, 'now', 120.0)
        self.app._ai.responses.append(envelope(texts=('LATE',)))
        self.finish()
        self.assertEqual(request.state, RequestState.FAILED)
        self.assertEqual(request.reason, 'deadline_exceeded')
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertFalse(self.app._ai_busy)
        self.assertEqual(self.audio, [])

    def test_final_accepted_before_deadline_delivers_after_deadline(self):
        request = self.ready_adapter_provider('bounded')
        self.fixture.now = 119.0
        self.app._ai.responses.append(envelope(texts=('ON TIME',)))
        self.job('continuation-provider')
        self.fixture.now = 121.0
        self.finish()
        self.assertEqual(request.state, RequestState.SUCCESS)
        self.assertEqual(self.audio, [['ON TIME']])

    def test_legacy_adapter_has_no_new_aggregate_deadline(self):
        request = self.ready_adapter_provider('legacy', 'search_memory')
        self.fixture.now = 10000.0
        self.app._ai.responses.append(envelope(texts=('VALID LEGACY',)))
        self.finish()
        self.assertEqual(request.state, RequestState.SUCCESS)
        self.assertEqual(self.audio, [['VALID LEGACY']])

    def test_final_delivery_exception_preserves_successor_ownership(self):
        request = self.ready_adapter_provider('legacy', 'search_memory')
        self.app._ai.responses.append(envelope(texts=('A',)))
        def broken(*args, **kw):
            self.app._invalidate_request_context()
            successor = LegacyContinuationAdapter(self.app._get_continuation_lifecycle()).admit(
                generation=self.app._context_request_epoch, validity=lambda: True)
            self.successor = successor
            raise ValueError('synthetic final dispatch exception')
        self.app._dispatch_response = broken
        with self.assertRaises(ValueError):
            self.job('memory-requery')
        self.assertEqual(request.state, RequestState.SUCCESS)
        self.assertIs(self.app._continuation_lifecycle.current, self.successor)
        self.assertTrue(self.successor.work_is_current())
        self.assertFalse(self.app._ai_busy)

    def test_empty_final_queued_idle_cannot_reset_successor_UI(self):
        request = self.ready_adapter_provider('bounded')
        self.app._ai.responses.append(envelope('idle', ()))
        self.job('continuation-provider')
        while request.state is not RequestState.SUCCESS:
            self.assertTrue(self.app.ui)
            self.app.ui.pop(0)()
        held = list(self.app.ui)
        self.app.ui.clear()
        self.app._invalidate_request_context()
        self.app._state = self.app.STATE_TALKING
        before = list(self.events)
        for callback in held:
            callback()
        self.assertEqual(self.app._state, self.app.STATE_TALKING)
        self.assertEqual(self.events, before)

    def test_provider_UI_queue_exception_does_not_strand_owned_reservation(self):
        request = self.ready_adapter_provider('legacy', 'search_memory')
        schedule = self.app._schedule_ui
        def broken_once(callback, *args, **kw):
            self.app._schedule_ui = schedule
            raise RuntimeError('synthetic UI scheduling failure')
        self.app._schedule_ui = broken_once
        errors = []
        try:
            self.job('memory-requery')
        except RuntimeError as error:
            errors.append(type(error).__name__)
        self.ui()
        self.assertFalse(self.app._ai_busy)
        self.assertIsNone(self.app._ai_operation_token)
        self.assertEqual(request.state, RequestState.FAILED)
        self.assertEqual(self.audio, [])
        self.assertEqual(self.app._ai.calls, [])

    def test_unmigrated_context_entry_invalidates_owner_before_starting_successor(self):
        self.bounded_admission()
        request = self.app._continuation_lifecycle.current
        self.assertTrue(self.app._request_read_only_context_dependency(
            ContextRequest(ContextKind.SCREEN), 'B', origin='user'))
        successor = self.app._continuation.active_snapshot()
        self.assertEqual(request.state, RequestState.INVALIDATED)
        self.job('continuation-tool')
        request.finish(RequestState.CANCELED, 'late cleanup')
        self.assertEqual(self.app._continuation.active_snapshot().session_id, successor.session_id)

    def test_failed_context_terminal_preserves_existing_unresolved_objective_TTL(self):
        request = self.ready_adapter_provider('bounded')
        self.app._ai.responses.extend([envelope('request_screen_read', ()), envelope(texts=('RECOVERED',))])
        self.finish()
        pending = self.app._unresolved_context_objectives.current()
        self.assertIsNotNone(pending)
        self.assertEqual(pending.message, 'A')
        self.assertEqual(pending.expires_at_monotonic, 90.0)
        self.assertEqual(request.state, RequestState.SUCCESS)
        self.assertEqual(self.audio, [['RECOVERED']])

    def test_status_completion_at_deadline_preserves_failure_reason_and_cleanup(self):
        request = self.admit_adapter('bounded', texts=('STATUS',), allow_voice=False)
        self.fixture.now = 120.0
        self.complete()
        self.assertEqual(request.state, RequestState.FAILED)
        self.assertEqual(request.reason, 'deadline_exceeded')
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertEqual(self.app._context_capture_targets, {})
        self.assertEqual(self.app.jobs, [])
        self.assertEqual(self.audio, [])

    def test_failed_handoff_consumes_owned_capture_target_once(self):
        self.case('bounded')
        consumed = []
        take = self.app._take_context_capture_target
        def consume(owner):
            consumed.append(owner)
            return take(owner)
        self.app._take_context_capture_target = consume
        self.app.refuse_worker = True
        self.app._ai.responses.append(envelope('read_notepad', ()))
        self.app._ai_tick('A', origin='user')
        self.finish()
        request = self.app._continuation_lifecycle.current
        self.assertEqual(consumed, [(request.identity.request_id, request.identity.generation)])
        self.assertEqual(request.state, RequestState.FAILED)

    def test_retained_bounded_final_cannot_fall_back_after_legacy_successor_admission(self):
        self.bounded_admission()
        self.job('continuation-tool')
        self.ui()
        self.app._ai.responses.append(envelope(texts=('A',)))
        original, retained = self.app._handle_continuation_decision, []
        self.app._handle_continuation_decision = lambda decision, **kw: retained.append((decision, kw))
        self.job('continuation-provider')
        self.app._handle_continuation_decision = original
        decision, kw = retained[0]
        original(decision, **kw)
        self.finish()
        successor = LegacyContinuationAdapter(self.app._get_continuation_lifecycle()).admit(
            generation=self.app._context_request_epoch, validity=lambda: True)
        original(decision, **kw)
        self.finish()
        self.assertEqual(self.audio, [['A']])
        self.assertIs(self.app._continuation_lifecycle.current, successor)
        self.assertTrue(successor.work_is_current())

    def test_retained_bounded_failure_cannot_deliver_after_legacy_successor_admission(self):
        self.bounded_admission()
        self.job('continuation-tool')
        self.ui()
        self.app._ai.responses.append(RuntimeError('synthetic provider failure'))
        original, retained = self.app._handle_continuation_decision, []
        self.app._handle_continuation_decision = lambda decision, **kw: retained.append((decision, kw))
        self.job('continuation-provider')
        self.app._handle_continuation_decision = original
        self.assertEqual(len(retained), 1)
        decision, kw = retained[0]
        original(decision, **kw)
        self.finish()
        notices = list(self.app.notices)
        self.assertEqual(len(notices), 1)
        successor = LegacyContinuationAdapter(self.app._get_continuation_lifecycle()).admit(
            generation=self.app._context_request_epoch, validity=lambda: True)
        original(decision, **kw)
        self.finish()
        self.assertEqual(self.app.notices, notices)
        self.assertIs(self.app._continuation_lifecycle.current, successor)
        self.assertTrue(successor.work_is_current())


# Importing the helper above must not run its characterization inventory again.
for name in tuple(dir(comparison.ContinuationComparison)):
    if name.startswith('test_'):
        setattr(OrchestrationIntegration, name, None)
