"""F11 startup contracts. Disposable copy only; workers and effects are fake.

Real app admission, reservation, engine, context store, and speech ownership.
The Thread double runs no native threads. Test queues control callback entry.
"""
from __future__ import annotations

import threading
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main
from agetha.commands import command_handlers as handlers
from agetha.core.context_dependencies import ContextKind, ContextRequest
from agetha.core.continuation import ContinuationState, DecisionKind
from agetha.commands.handlers import memory_presentation, web_context
from agetha.commands.handlers.support import DispatchCtx
from tests import test_followup_characterization as fixtures


class FakeThread:
    """Inject failures at construction/start/entry without timing."""
    def __init__(self, harness, target, **kwargs):
        self.harness = harness
        self.target = target
        self.ident = None
        self.name = 'fake'
        self.alive = False
        harness.threads.append(self)
    def start(self):
        self.harness.at_start()
        if self.harness.mode == 'raise':
            raise RuntimeError('synthetic start failure')
        self.alive = True
        if self.harness.mode == 'enter_then_raise':
            self.deliver()
            raise RuntimeError('synthetic exception after entry')
    def deliver(self):
        self.ident = 42
        try:
            self.target()
        finally:
            self.alive = False
    def is_alive(self):
        return self.alive
    def join(self, *_):
        pass


class WorkerStartLifetime(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FollowupCharacterization(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app = self.fixture.app
        self.app._worker_lock = threading.Lock()
        self.app._workers = set()
        self.app._context_request_epoch = 0
        self.app._pending_request_context_epoch = None
        self.app._speech_operation_token = None
        self.threads = []
        self.mode = 'raise'
        self.at_start = lambda: None
        self.at_construct = lambda: None
        self.audio = []
        self.app._voice_out = SimpleNamespace(
            start_speech=lambda segments, mood, **kw: self.audio.append([s['text'] for s in segments]),
            stop=lambda: None,
        )
        self.enterContext(patch.object(fixtures.FakeApp, '_start_worker', main.CompanionApp._start_worker))
        self.enterContext(patch('main.threading.Thread', side_effect=self.construct))

    def construct(self, **kwargs):
        self.at_construct()
        if self.mode == 'construct_raise':
            raise RuntimeError('synthetic construction failure')
        return FakeThread(self, **kwargs)

    def admit(self, kind='tool'):
        engine = self.app._continuation
        started = engine.start('A', authority_origin='user')
        owner = (started.session_id, started.generation)
        self.app._context_capture_targets[owner] = {'hwnd': 7}
        self.app._unresolved_context_objectives.remember('A', ContextKind.SCREEN, origin='user', owner=owner)
        if kind == 'context':
            return engine.accept_context_request(*owner, ContextRequest(ContextKind.SCREEN), request_origin='user')
        decision = engine.accept_initial_model_response(*owner, {'command': 'read_notepad'})
        if kind == 'provider':
            from agetha.core.continuation import ToolOutcome
            return engine.accept_tool_outcome(*owner, ToolOutcome('read_notepad', True, 'notes', 'A notes'))
        return decision

    def handoff(self, decision):
        # RED keeps the old exception visible while testing its stranded state.
        try:
            self.app._handle_continuation_decision(decision)
        except RuntimeError as exc:
            self.assertIn('synthetic', str(exc))

    def fail_and_assert(self, kind, mode):
        self.mode = mode
        decision = self.admit(kind)
        if mode == 'refuse':
            self.at_construct = lambda: setattr(self.app, '_closing', True)
        self.handoff(decision)
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertEqual(self.app._continuation.last_snapshot().state, ContinuationState.STOPPED)
        self.assertFalse(self.app._continuation.is_current(decision.session_id, decision.generation))
        self.assertIsNone(self.app._unresolved_context_objectives.current())
        self.assertEqual(self.app._context_capture_targets, {})
        self.assertFalse(self.app._ai_busy)
        self.assertIsNone(self.app._ai_operation_token)
        self.assertFalse(self.app._speech_active)
        self.assertEqual(self.app._workers, set())
        self.app._closing = False  # Fake refusal only; real shutdown is separate.
        self.fixture.flush()
        self.assertEqual(self.audio, [])
        for worker in self.threads[:]:
            worker.deliver()  # Simulate impossible/late native entry as an adversarial boundary.
        self.fixture.flush()
        self.assertEqual(self.audio, [])
        self.assertEqual(self.app._ai.calls, [])
        self.assertEqual(self.app._workers, set())
        self.assert_next_request()

    def assert_next_request(self):
        self.mode = 'normal'
        self.at_start = self.at_construct = lambda: None
        self.app._input_box.config(state='normal')
        self.app._ai.responses.append(fixtures.answer('B'))
        self.app._on_user_input()
        self.threads[-1].deliver()
        self.fixture.flush()
        self.assertEqual(self.audio, [['B']])
        self.assertFalse(self.app._ai_busy)

    def test_start_exception_terminates_admitted_session_and_next_request_proceeds(self):
        decision = self.admit()
        self.handoff(decision)
        self.assertIsNone(self.app._continuation.active_snapshot(), 'A remains stranded awaiting_tool')
        last = self.app._continuation.last_snapshot()
        self.assertEqual(last.state, ContinuationState.STOPPED)
        self.assertTrue(last.cancelled)
        self.assertFalse(self.app._ai_busy)
        self.assertIsNone(self.app._ai_operation_token)
        self.assertIsNone(self.app._unresolved_context_objectives.current())
        self.assertEqual(self.app._context_capture_targets, {})
        self.assertEqual(self.app._workers, set())
        self.assert_next_request()

    def test_tool_refusal_is_terminal_and_next_request_proceeds(self):
        self.fail_and_assert('tool', 'refuse')

    def test_tool_construction_exception_is_terminal_and_next_request_proceeds(self):
        self.fail_and_assert('tool', 'construct_raise')

    def test_context_start_exception_is_terminal_and_next_request_proceeds(self):
        self.fail_and_assert('context', 'raise')

    def test_context_refusal_is_terminal_and_next_request_proceeds(self):
        self.fail_and_assert('context', 'refuse')

    def test_context_construction_exception_is_terminal_and_next_request_proceeds(self):
        self.fail_and_assert('context', 'construct_raise')

    def test_provider_start_exception_is_terminal_and_next_request_proceeds(self):
        self.fail_and_assert('provider', 'raise')

    def test_provider_refusal_is_terminal_and_next_request_proceeds(self):
        self.fail_and_assert('provider', 'refuse')

    def test_provider_construction_exception_is_terminal_and_next_request_proceeds(self):
        self.fail_and_assert('provider', 'construct_raise')

    def test_successful_bounded_tool_provider_final_keeps_speech_eligible(self):
        self.mode = 'normal'
        self.app._handle_continuation_decision(self.admit())
        self.threads[0].deliver()
        self.app._ai.responses.append(fixtures.answer('A'))
        self.threads[1].deliver()
        self.fixture.flush()
        self.assertEqual(self.audio, [['A']])
        self.assertEqual(len(self.app._ai.calls), 1)
        self.assertEqual(self.app._continuation.last_snapshot().state, ContinuationState.COMPLETED)
        self.assertIsNone(self.app._ai_operation_token)
        self.assertEqual(self.app._workers, set())

    def test_successful_context_provider_final_keeps_speech_eligible(self):
        from agetha.core.context_dependencies import ContextOutcome
        self.mode = 'normal'
        self.app._acquire_read_only_context = lambda *_a, **_k: ContextOutcome(ContextKind.SCREEN, True, 'captured', 'synthetic screen')
        self.app._handle_continuation_decision(self.admit('context'))
        self.threads[0].deliver()
        self.app._ai.responses.append(fixtures.answer('A'))
        self.threads[1].deliver()
        self.fixture.flush()
        self.assertEqual(self.audio, [['A']])
        self.assertEqual(len(self.app._ai.recorded), 1)
        self.assertEqual(self.app._workers, set())

    def test_failure_cleans_context_and_session_once_and_never_releases_successor(self):
        decision = self.admit('context')
        stopped, cleared, leases, released = [], [], [], []
        engine = self.app._continuation
        original_stop = engine.provider_failed
        original_clear = self.app._unresolved_context_objectives.clear
        original_lease = self.app._take_context_capture_target
        original_release = self.app._release_ai_operation
        def stop(*a, **kw):
            result = original_stop(*a, **kw)
            if result.kind is DecisionKind.STOPPED: stopped.append(result)
            return result
        def clear(**kw):
            result = original_clear(**kw)
            if result: cleared.append(kw)
            return result
        def take(owner):
            result = original_lease(owner)
            if result is not None: leases.append(owner)
            return result
        def release(token, **kw):
            result = original_release(token, **kw)
            if result: released.append(token)
            return result
        engine.provider_failed = stop
        self.app._unresolved_context_objectives.clear = clear
        self.app._take_context_capture_target = take
        self.app._release_ai_operation = release
        self.handoff(decision)
        self.assertEqual((len(stopped), len(cleared), len(leases)), (1, 1, 1))
        token = self.app._reserve_ai_operation(direct=True, user_message='B', origin='user')
        self.assertIsNotNone(token)
        for worker in self.threads: worker.deliver()
        self.app._handle_continuation_decision(decision)
        self.fixture.flush()
        self.assertEqual((len(stopped), len(cleared), len(leases)), (1, 1, 1))
        self.assertIs(self.app._ai_operation_token, token)
        self.assertEqual(released, [])  # No provider slot existed for the unstarted tool.

    def test_partial_registration_is_removed_after_start_exception(self):
        registered = []
        self.at_start = lambda: registered.append(self.threads[-1] in self.app._workers)
        self.handoff(self.admit())
        self.assertEqual(registered, [True])
        self.assertEqual(self.app._workers, set())

    def test_late_provider_callback_cannot_consume_failed_context_or_speak(self):
        self.handoff(self.admit('provider'))
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.app._ai.responses.append(fixtures.answer('OLD'))
        self.threads[-1].deliver()
        self.fixture.flush()
        self.assertEqual(self.app._ai.calls, [])
        self.assertEqual(self.audio, [])
        self.assertIsNone(self.app._ai_operation_token)
        self.assertEqual(self.app._workers, set())

    def test_failed_start_does_not_change_newer_session_context_or_reservation(self):
        decision = self.admit('context')
        successor = []
        def replace():
            self.app._invalidate_request_context()
            started = self.app._continuation.start('B', authority_origin='user')
            owner = (started.session_id, started.generation)
            self.app._context_capture_targets[owner] = {'hwnd': 8}
            self.app._unresolved_context_objectives.remember('B', ContextKind.SCREEN, origin='user', owner=owner)
            successor.append(self.app._reserve_ai_operation(direct=True, user_message='B', origin='user'))
        self.at_start = replace
        self.handoff(decision)
        self.assertEqual(self.app._continuation.active_snapshot().original_user_message, 'B')
        self.assertEqual(self.app._unresolved_context_objectives.current().message, 'B')
        self.assertIs(self.app._ai_operation_token, successor[0])
        self.fixture.flush()
        self.assertEqual(self.audio, [])
        self.assertEqual(self.app.notices, [])

    def test_cancel_before_handoff_does_not_create_worker(self):
        decision = self.admit()
        self.app._on_cancel_ai()
        self.handoff(decision)
        self.assertEqual(self.threads, [])
        self.assertIsNone(self.app._continuation.active_snapshot())

    def test_cancel_during_construction_cannot_revive_request(self):
        self.at_construct = self.app._on_cancel_ai
        self.handoff(self.admit())
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertEqual(self.app._continuation.last_snapshot().state, ContinuationState.CANCELLED)
        for worker in self.threads: worker.deliver()
        self.fixture.flush()
        self.assertEqual(self.app._ai.calls, [])
        self.assertEqual(self.audio, [])
        self.assertEqual(self.app._workers, set())

    def test_shutdown_before_handoff_does_not_create_worker(self):
        decision = self.admit()
        self.app._graceful_shutdown()
        self.handoff(decision)
        self.assertEqual(self.threads, [])
        self.assertIsNone(self.app._continuation.active_snapshot())

    def test_shutdown_during_construction_releases_registration_and_context(self):
        self.at_construct = self.app._graceful_shutdown
        self.handoff(self.admit('context'))
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertEqual(self.app._context_capture_targets, {})
        self.assertIsNone(self.app._unresolved_context_objectives.current())
        self.assertEqual(self.app._workers, set())
        for worker in self.threads: worker.deliver()
        self.assertEqual(self.audio, [])

    def test_invalid_generation_before_handoff_preserves_new_session(self):
        decision = self.admit()
        self.app._continuation.start('B', authority_origin='user')
        self.handoff(decision)
        self.assertEqual(self.threads, [])
        self.assertEqual(self.app._continuation.active_snapshot().original_user_message, 'B')

    def test_profile_transition_before_provider_start_keeps_basic_notes_success(self):
        self.mode = 'normal'
        decision = self.admit('provider')
        self.fixture.transition()
        self.app._handle_continuation_decision(decision)
        self.app._ai.responses.append(fixtures.answer('A'))
        self.threads[-1].deliver()
        self.fixture.flush()
        self.assertEqual(self.audio, [['A']])

    def test_profile_transition_during_failed_start_does_not_leave_session_waiting(self):
        self.at_start = self.fixture.transition
        self.handoff(self.admit('provider'))
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertEqual(self.app._continuation.last_snapshot().state, ContinuationState.STOPPED)
        self.fixture.flush()
        self.assertEqual(self.audio, [])

    def test_started_worker_exception_does_not_run_failure_cleanup(self):
        self.mode = 'enter_then_raise'
        self.app._ai.responses.append(fixtures.answer('A'))
        self.handoff(self.admit('provider'))
        self.fixture.flush()
        self.assertEqual(self.app._continuation.last_snapshot().state, ContinuationState.COMPLETED)
        self.assertEqual(self.audio, [['A']])
        self.assertFalse(any('start' in text.lower() for text in self.app.notices))

    def test_repeated_failed_starts_leave_no_active_or_registered_state(self):
        for _ in range(10):
            self.handoff(self.admit())
            self.assertIsNone(self.app._continuation.active_snapshot())
            self.assertEqual(self.app._workers, set())
            self.assertEqual(self.app._context_capture_targets, {})
        for worker in self.threads: worker.deliver()
        self.fixture.flush()
        self.assertEqual(self.app._ai.calls, [])
        self.assert_next_request()

    def legacy(self, kind='notes', segments=None):
        self.app._continuation = None
        ctx = DispatchCtx('A', 'neutral', segments or [], False, 'user')
        try:
            if kind == 'notes':
                memory_presentation.handle_read_notepad(self.app, {}, ctx)
            elif kind == 'memory':
                memory_presentation.handle_search_memory(self.app, {'query': 'A'}, ctx)
            else:
                web_context.handle_search_web(self.app, {'query': 'A'}, ctx)
        except RuntimeError as exc:
            self.assertIn('synthetic', str(exc))

    def assert_legacy_failure(self, kind, mode):
        self.mode = mode
        if mode == 'refuse':
            self.at_construct = lambda: setattr(self.app, '_closing', True)
        self.legacy(kind, [{'text': 'old status', 'pause': 0.0}])
        if mode == 'refuse':
            self.fixture.flush()  # Refusal is shutdown; deliver retained UI while closed.
        self.app._closing = False
        for worker in self.threads: worker.deliver()
        self.fixture.flush()
        self.assertEqual(self.audio, [], 'Failed legacy follow-up revived queued speech')
        self.assertEqual(self.app._ai.calls, [], 'Failed legacy worker consumed context later')
        self.assertEqual(self.app._workers, set())
        self.assertFalse(self.app._ai_busy)
        self.assertFalse(self.app._speech_active)
        self.assertIsNone(self.app._ai_operation_token)
        self.assert_next_request()

    def test_legacy_notes_start_exception_withdraws_work_and_speech(self):
        self.assert_legacy_failure('notes', 'raise')

    def test_legacy_notes_refusal_withdraws_work_and_speech(self):
        self.assert_legacy_failure('notes', 'refuse')

    def test_legacy_notes_construction_exception_withdraws_work_and_speech(self):
        self.assert_legacy_failure('notes', 'construct_raise')

    def test_legacy_web_start_exception_withdraws_work_and_speech(self):
        self.assert_legacy_failure('web', 'raise')

    def test_legacy_memory_start_exception_withdraws_work_and_speech(self):
        self.assert_legacy_failure('memory', 'raise')

    def test_legacy_success_preserves_provider_context_and_one_final(self):
        self.mode = 'normal'
        self.legacy()
        self.app._ai.responses.append(fixtures.answer('A'))
        self.threads[-1].deliver()
        self.fixture.flush()
        self.assertEqual(self.audio, [['A']])
        self.assertIn('A notes', self.app._ai.calls[0]['notepad_context'])
        self.assertIsNone(self.app._ai_operation_token)
        self.assertEqual(self.app._workers, set())

    def test_legacy_failed_start_leaves_thinking_with_truthful_failure_notice(self):
        self.app._state = self.app.STATE_THINKING
        self.legacy()
        self.fixture.flush()
        self.assertEqual(self.app._state, self.app.STATE_IDLE)
        self.assertTrue(any("couldn't start" in text for text in self.app.notices))

    def test_legacy_failed_start_with_queued_status_leaves_thinking(self):
        self.app._state = self.app.STATE_THINKING
        self.legacy(segments=[{'text': 'old status', 'pause': 0.0}])
        self.fixture.flush()
        self.assertEqual(self.app._state, self.app.STATE_IDLE)
        self.assertEqual(self.audio, [])

    def test_failed_legacy_status_releases_speech_once_and_cannot_release_new_speech(self):
        releases = []
        original = self.app._on_speech_done
        def complete(*a, **kw):
            token = self.app._speech_operation_token
            original(*a, **kw)
            if token is not None and kw.get('operation_token') is token and self.app._speech_operation_token is None:
                releases.append(token)
        self.app._on_speech_done = complete
        self.legacy(segments=[{'text': 'old status', 'pause': 0.0}])
        late = self.app.ui[0]
        self.fixture.flush()
        self.assertEqual(len(releases), 1)
        self.assertFalse(self.app._speech_active)
        self.app._speak_and_continue([{'text': 'B', 'pause': 0.0}], 'neutral')
        newer = self.app._speech_operation_token
        late()
        self.assertEqual(len(releases), 1)
        self.assertIs(self.app._speech_operation_token, newer)
        self.fixture.flush()
        self.assertEqual(self.audio, [['B']])

    def test_process_monitor_start_failure_reports_terminal_failure_without_query(self):
        self.app._continuation = None
        self.app._state = self.app.STATE_THINKING
        self.app._ai.monitor_process = lambda *_: self.fail('Real process check entered')
        with self.assertRaisesRegex(RuntimeError, 'synthetic start failure'):
            handlers.handle_monitor_process(self.app, {'process_name': 'fake'}, DispatchCtx('A', 'neutral', [], False))
        self.fixture.flush()
        self.assertTrue(any("couldn't start" in text for text in self.app.notices))
        self.assertEqual(self.app._state, self.app.STATE_IDLE)
        self.threads[-1].deliver()
        self.assertEqual(self.app._ai.calls, [])
        self.assert_next_request()

    def test_late_process_progress_cannot_overwrite_start_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'synthetic start failure'):
            handlers.handle_monitor_process(self.app, {'process_name': 'fake'}, DispatchCtx('A', 'neutral', [], False))
        progress = self.app.ui.pop(0)
        self.fixture.flush()
        notices = self.app.notices[:]
        progress()
        self.assertEqual(self.app.notices, notices)

    def test_late_deep_ocr_progress_cannot_overwrite_start_failure(self):
        self.app._screen = SimpleNamespace(capture_deep_text=lambda **_: self.fail('Native OCR entered'))
        ctx = DispatchCtx('A', 'neutral', [{'text': 'Analyzing A'}], False, 'user')
        handlers.handle_analyze_screen_deep(self.app, {}, ctx)
        progress = self.app.ui.pop(0)
        self.app._run_deferred_ai_tick_callbacks()
        self.fixture.flush()
        notices = self.app.notices[:]
        self.assertTrue(any("couldn't start" in text for text in notices))
        progress()
        self.assertEqual(self.app.notices, notices)

    def test_failed_legacy_start_cannot_invalidate_newer_user_lifetime(self):
        current = []
        def new_input():
            self.app._invalidate_request_context()
            current.append(self.app._capture_context_validity())
        self.at_start = new_input
        self.legacy()
        self.assertTrue(current[0]())
        self.fixture.flush()
        self.assertEqual(self.app.notices, [])

    def test_legacy_failure_after_context_acquisition_cannot_invalidate_newer_speech(self):
        def acquire():
            self.app._invalidate_request_context()
            self.app._speak_and_continue([{'text': 'B', 'pause': 0.0}], 'neutral')
            return 'A notes'
        with patch('agetha.ui.dashboard.read_notepad_text', side_effect=acquire):
            self.legacy()
        self.fixture.flush()
        self.assertEqual(self.audio, [['B']], 'A handoff adopted and invalidated B lifetime')
        self.assertFalse(any('follow-up' in text for text in self.app.notices))

    def test_legacy_failure_notice_cannot_adopt_new_user_during_validity_capture(self):
        original = self.app._capture_context_validity
        def newer():
            self.app._invalidate_request_context()
            return original()
        with patch.object(self.app, '_capture_context_validity', side_effect=newer):
            self.legacy()
        self.fixture.flush()
        self.assertEqual(self.app.notices, [], 'Failure notice adopted B lifetime')

    def test_queued_bounded_failure_notice_expires_when_new_user_is_admitted(self):
        self.handoff(self.admit())
        self.app._invalidate_request_context()
        self.app._invalidate_continuation_ui_delivery()
        self.app._continuation.start('B', authority_origin='user')
        self.fixture.flush()
        self.assertEqual(self.app.notices, [])
        self.assertEqual(self.audio, [])

    def exclusive(self, callback):
        self.app._defer_exclusive_ai_operation(callback)
        self.app._run_deferred_ai_tick_callbacks()

    def test_exclusive_refusal_releases_once_and_late_callback_cannot_reclaim(self):
        released, effects = [], []
        original = self.app._release_ai_operation
        def release(token, **kw):
            result = original(token, **kw)
            if result: released.append(token)
            return result
        self.app._release_ai_operation = release
        self.at_construct = lambda: setattr(self.app, '_closing', True)
        self.exclusive(lambda: effects.append('A'))
        self.assertEqual(len(released), 1)
        self.assertFalse(self.app._ai_busy)
        self.assertFalse(self.app._deferred_ai_callbacks_inflight)
        self.app._closing = False
        for worker in self.threads: worker.deliver()
        self.fixture.flush()
        self.assertEqual(effects, [])
        self.assertEqual(len(released), 1)
        self.assert_next_request()

    def test_exclusive_start_exception_releases_once_and_next_request_proceeds(self):
        self.exclusive(lambda: self.fail('Failed worker ran'))
        self.assertFalse(self.app._ai_busy)
        self.assertFalse(self.app._ai_busy_noninterruptible)
        self.assertIsNone(self.app._ai_operation_token)
        self.assertFalse(self.app._deferred_ai_callbacks_inflight)
        self.assertEqual(self.app._workers, set())
        for worker in self.threads: worker.deliver()
        self.assert_next_request()

    def test_exclusive_construction_exception_releases_and_next_request_proceeds(self):
        self.mode = 'construct_raise'
        self.test_exclusive_start_exception_releases_once_and_next_request_proceeds()

    def test_exclusive_success_owns_reservation_until_callback_finishes(self):
        self.mode = 'normal'
        seen = []
        self.exclusive(lambda: seen.append((self.app._ai_busy, self.app._ai_busy_noninterruptible)))
        self.assertTrue(self.app._ai_busy)
        self.threads[-1].deliver()
        self.fixture.flush()
        self.assertEqual(seen, [(True, True)])
        self.assertIsNone(self.app._ai_operation_token)
        self.assertFalse(self.app._ai_busy)

    def test_exclusive_failed_start_leaves_thinking_with_truthful_failure_notice(self):
        self.app._state = self.app.STATE_THINKING
        self.exclusive(lambda: self.fail('Failed start entered'))
        self.fixture.flush()
        self.assertEqual(self.app._state, self.app.STATE_IDLE)
        self.assertTrue(any("couldn't start" in text for text in self.app.notices))

    def test_exclusive_failed_construction_leaves_thinking_with_failure_notice(self):
        self.mode = 'construct_raise'
        self.test_exclusive_failed_start_leaves_thinking_with_truthful_failure_notice()

    def test_exclusive_start_failure_releases_reservation_once(self):
        releases = []
        original = self.app._release_ai_operation
        def release(token, **kw):
            result = original(token, **kw)
            if result: releases.append(token)
            return result
        self.app._release_ai_operation = release
        self.exclusive(lambda: self.fail('Failed start entered'))
        self.assertEqual(len(releases), 1)
        self.threads[-1].deliver()
        self.fixture.flush()
        self.assertEqual(len(releases), 1)

    def test_failed_exclusive_starter_replay_cannot_acquire_again(self):
        self.app._defer_exclusive_ai_operation(lambda: self.fail('Failed starter entered'))
        late = self.app._post_ai_tick_callbacks[0]
        self.app._run_deferred_ai_tick_callbacks()
        self.assertFalse(self.app._ai_busy)
        self.assertEqual(len(self.threads), 1)
        late()
        self.assertEqual(len(self.threads), 1)
        self.assertIsNone(self.app._ai_operation_token)
        self.fixture.flush()

    def test_exclusive_cancel_before_deferred_start_prevents_acquiring_slot(self):
        effects = []
        self.mode = 'normal'
        self.app._defer_exclusive_ai_operation(lambda: effects.append('A'))
        self.app._on_cancel_ai()
        self.app._run_deferred_ai_tick_callbacks()
        for worker in self.threads: worker.deliver()
        self.assertEqual(effects, [])
        self.assertFalse(self.app._ai_busy)

    def test_exclusive_cancel_during_handoff_prevents_callback_and_releases(self):
        effects = []
        self.mode = 'normal'
        self.at_start = self.app._on_cancel_ai
        self.exclusive(lambda: effects.append('A'))
        for worker in self.threads: worker.deliver()
        self.assertEqual(effects, [])
        self.assertFalse(self.app._ai_busy)

    def test_exclusive_invalidation_between_validity_and_reservation_cannot_claim_slot(self):
        original = self.app._reserve_ai_operation
        def invalidate(**kw):
            self.app._on_cancel_ai()
            return original(**kw)
        self.app._reserve_ai_operation = invalidate
        self.mode = 'normal'
        self.exclusive(lambda: self.fail('Expired callback entered'))
        self.assertEqual(self.threads, [], 'Expired A acquired a slot and started a worker')
        self.assertTrue(self.app._cancel_event.is_set(), 'Expired reservation cleared cancellation')
        self.assertFalse(self.app._ai_busy)
        self.assertIsNone(self.app._ai_operation_token)

    def test_shutdown_between_validity_and_reservation_keeps_cancellation_set(self):
        original = self.app._reserve_ai_operation
        def shutdown(**kw):
            self.app._graceful_shutdown()
            return original(**kw)
        self.app._reserve_ai_operation = shutdown
        self.exclusive(lambda: self.fail('Closed callback entered'))
        self.assertTrue(self.app._cancel_event.is_set())
        self.assertFalse(self.app._ai_busy)
        self.assertEqual(self.threads, [])

    def test_shutdown_during_reservation_clear_cannot_lose_cancellation(self):
        event = self.app._cancel_event
        def clear():
            self.app._graceful_shutdown()
            event.clear()
        self.app._cancel_event = SimpleNamespace(is_set=event.is_set, set=event.set, clear=clear)
        self.exclusive(lambda: self.fail('Closed callback entered'))
        self.assertTrue(event.is_set(), 'Reservation erased shutdown cancellation')
        self.assertFalse(self.app._ai_busy)
        self.assertEqual(self.threads, [])

    def test_shutdown_before_exclusive_deferred_start_drops_callback(self):
        effects = []
        self.app._defer_exclusive_ai_operation(lambda: effects.append('A'))
        self.app._graceful_shutdown()
        self.app._run_deferred_ai_tick_callbacks()
        self.assertEqual(self.threads, [])
        self.assertEqual(effects, [])
        self.assertFalse(self.app._ai_busy)

    def test_native_start_failure_notifies_once_before_late_entry(self):
        observed, failures = [], []
        kw = {}
        if 'on_start_failure' in inspect.signature(self.app._start_worker).parameters:
            kw['on_start_failure'] = lambda: failures.append('failed')
        with self.assertRaisesRegex(RuntimeError, 'synthetic start failure'):
            self.app._start_worker(lambda: observed.append('A'), name='fake', **kw)
        self.assertEqual(failures, ['failed'])
        self.threads[-1].deliver()
        self.assertEqual(observed, [])
        self.assertEqual(failures, ['failed'])
        self.assertEqual(self.app._workers, set())


if __name__ == '__main__':
    unittest.main()
