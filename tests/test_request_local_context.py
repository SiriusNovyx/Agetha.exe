"""Request-local web/Notepad contracts. Run only in an isolated public-source copy.

The real app reservation, routing and prompt boundaries use controlled workers,
fake retrieval/provider/speech and synthetic markers. No native effects or data.
"""
from __future__ import annotations

import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main
from agetha.commands.handlers import memory_presentation, web_context
from agetha.commands.handlers.support import DispatchCtx
from agetha.core.ai_engine import AIEngine
from agetha.core.continuation import ContinuationEngine
from agetha.core.read_only_tools import ReadOnlyToolExecutor
from tests import test_followup_characterization as fixtures
from tests.test_followup_characterization import FakeAI, FakeApp, answer


class RequestLocalContext(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FollowupCharacterization(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app = self.fixture.app
        self.settings = self.fixture.settings

    def enqueue(self, kind, owner, *, message=None):
        self.app._speech_active = False
        ctx = DispatchCtx(owner if message is None else message, 'neutral', [], False, 'user')
        marker = f'REQUEST_{owner}_CONTEXT'
        if kind == 'notepad':
            with patch('agetha.ui.dashboard.read_notepad_text', return_value=marker):
                memory_presentation.handle_read_notepad(self.app, {}, ctx)
        else:
            with patch('agetha.features.web_rag.format_search_results_for_prompt', return_value=marker):
                web_context.handle_search_web(self.app, {'query': owner}, ctx)

    def deliver(self, index=0):
        self.app.jobs.pop(index)[1]()

    def reset_scenario(self):
        self.assertFalse(self.app._ai_busy)
        self.app.jobs.clear()
        self.app.ui.clear()
        self.app._input_box.config(state='normal')
        self.app._cancel_event.clear()
        self.app._speech_active = False

    def bounded_provider(self, goal):
        self.fixture.to_provider(goal=goal)
        name, callback = self.app.jobs.pop()
        self.assertEqual(name, 'continuation-provider')
        return callback

    def assert_owner(self, call, owner, forbidden=('A', 'B', 'C')):
        self.assertIn(f'REQUEST_{owner}_CONTEXT', call['turn'])
        for other in forbidden:
            if other != owner:
                self.assertNotIn(f'REQUEST_{other}_CONTEXT', call['turn'])

    def direct(self, message='DIRECT'):
        self.app._speech_active = False
        self.app._ai.responses.append({'command': 'idle'})
        self.app._ai_tick(message, origin='user')
        call = self.app._ai.calls[-1]
        for owner in ('A', 'B', 'C'):
            self.assertNotIn(f'REQUEST_{owner}_CONTEXT', call['turn'])
        self.assertNotIn('Do not call read_notepad again', call['system'])
        self.assertNotIn('Do not call search_web or fetch_webpage again', call['system'])
        return call

    def test_queued_requests_keep_context_and_expire_superseded_notepad(self):
        for kind in ('web', 'notepad'):
            with self.subTest(kind=kind):
                self.app._ai.calls.clear()
                for owner in ('A', 'B', 'C'):
                    self.enqueue(kind, owner)
                for owner in ('A', 'B', 'C'):
                    self.app._speech_active = False
                    call_count = len(self.app._ai.calls)
                    self.deliver()
                    if kind == 'notepad' and owner != 'C':
                        self.assertEqual(len(self.app._ai.calls), call_count)
                        continue
                    self.assert_owner(self.app._ai.calls[-1], owner)

    def test_reverse_completion_keeps_current_request_context(self):
        for kind in ('web', 'notepad'):
            with self.subTest(kind=kind):
                self.enqueue(kind, 'A')
                self.enqueue(kind, 'B')
                self.deliver(1)
                self.assert_owner(self.app._ai.calls[-1], 'B')
                self.app._speech_active = False
                call_count = len(self.app._ai.calls)
                self.deliver()
                if kind == 'notepad':
                    self.assertEqual(len(self.app._ai.calls), call_count)
                    self.assert_owner(self.app._ai.calls[-1], 'B')
                else:
                    self.assert_owner(self.app._ai.calls[-1], 'A')

    def test_older_completion_cannot_clear_newer_queued_context(self):
        for kind in ('web', 'notepad'):
            with self.subTest(kind=kind):
                self.enqueue(kind, 'A')
                def create_b():
                    self.app._ai.hook = lambda: None
                    self.enqueue(kind, 'B')
                self.app._ai.hook = create_b
                self.deliver()
                self.assert_owner(self.app._ai.calls[-1], 'A')
                self.app._speech_active = False
                self.deliver()
                self.assert_owner(self.app._ai.calls[-1], 'B')

    def test_older_cleanup_cannot_clear_newer_context_at_consumer_boundary(self):
        for kind in ('web', 'notepad'):
            with self.subTest(kind=kind):
                entered, resume = threading.Event(), threading.Event()
                errors, workers = [], []
                first = True
                def dispatch(*args, **kwargs):
                    nonlocal first
                    if not first:
                        return
                    first = False
                    self.enqueue(kind, 'B')
                    def pause_b():
                        self.app._ai.hook = lambda: None
                        entered.set()
                        if not resume.wait(3):
                            raise AssertionError('B was not released')
                    self.app._ai.hook = pause_b
                    def run_b():
                        try:
                            self.deliver()
                        except BaseException as exc:
                            errors.append(exc)
                    worker = threading.Thread(target=run_b)
                    workers.append(worker)
                    worker.start()
                    if not entered.wait(3):
                        raise AssertionError('B did not reach its consumer')
                with patch.object(self.app, '_dispatch_response', dispatch):
                    self.enqueue(kind, 'A')
                    try:
                        self.deliver()  # A returns/cleans while B is paused before prompt construction.
                    finally:
                        resume.set()
                        for worker in workers:
                            worker.join(3)
                    self.assertFalse(any(w.is_alive() for w in workers))
                    self.assertEqual(errors, [])
                    self.assertTrue(entered.is_set())
                    self.assert_owner(self.app._ai.calls[-1], 'B')

    def test_direct_user_does_not_inherit_queued_notes_with_engine_on_or_off(self):
        for enabled in (False, True):
            with self.subTest(engine=enabled):
                self.app._continuation = ContinuationEngine() if enabled else None
                self.enqueue('notepad', 'A')
                self.direct('B')
                self.deliver()

    def test_queued_direct_user_does_not_inherit_web_before_worker_returns(self):
        original_start = FakeApp._start_worker
        def immediately_deliver_direct(app, target, *, name, args=(), kwargs=None):
            if name == 'queued-ai':
                target(*args, **(kwargs or {}))
                return object()
            return original_start(app, target, name=name, args=args, kwargs=kwargs)
        def queue_b():
            self.app._ai.hook = lambda: None
            self.app._reserve_ai_operation(direct=True, user_message='B', origin='user')
        self.app._ai.hook = queue_b
        self.app._ai.responses.extend([answer(), {'command': 'idle'}])
        with patch.object(FakeApp, '_start_worker', immediately_deliver_direct):
            web_context._requery_with_web_context(self.app, DispatchCtx('A', 'neutral', [], False), 'REQUEST_A_CONTEXT')
        self.assert_owner(self.app._ai.calls[0], 'A')
        self.assertNotIn('REQUEST_A_CONTEXT', self.app._ai.calls[1]['turn'])
        self.assertNotIn('Do not call search_web', self.app._ai.calls[1]['system'])

    def test_bounded_request_does_not_inherit_legacy_context(self):
        for kind in ('web', 'notepad'):
            with self.subTest(kind=kind):
                self.enqueue(kind, 'A')
                self.app._continuation_tools = ReadOnlyToolExecutor(
                    settings=self.settings, functions={'read_notepad': lambda: 'REQUEST_B_CONTEXT'},
                )
                self.bounded_provider('B')()
                self.assert_owner(self.app._ai.calls[-1], 'B')
                self.app._speech_active = False
                self.deliver()
                self.assert_owner(self.app._ai.calls[-1], 'A')

    def test_canceled_legacy_work_cannot_populate_next_direct_request(self):
        for kind in ('web', 'notepad'):
            with self.subTest(kind=kind):
                self.enqueue(kind, 'A')
                self.app._on_cancel_ai()
                self.deliver()
                self.direct('B')

    def test_stale_legacy_callback_cannot_populate_newer_context(self):
        for kind in ('web', 'notepad'):
            with self.subTest(kind=kind):
                self.reset_scenario()
                self.enqueue(kind, 'A')
                self.app._on_cancel_ai()
                self.app._continuation.start('B', authority_origin='user')
                self.direct('B')
                self.enqueue(kind, 'B')
                call_count = len(self.app._ai.calls)
                self.deliver()
                self.assertEqual(len(self.app._ai.calls), call_count)
                self.app._speech_active = False
                self.deliver()
                self.assert_owner(self.app._ai.calls[-1], 'B')

    def test_canceled_bounded_tool_result_cannot_populate_legacy_request(self):
        def retrieve_a():
            self.app._on_cancel_ai()
            self.direct('DIRECT')
            self.enqueue('notepad', 'B')
            return 'REQUEST_A_CONTEXT'
        self.app._continuation_tools = ReadOnlyToolExecutor(settings=self.settings, functions={'read_notepad': retrieve_a})
        self.fixture.bounded()
        self.fixture.job('continuation-tool')
        self.assertFalse(any(name == 'continuation-provider' for name, _ in self.app.jobs))
        self.deliver()
        self.assert_owner(self.app._ai.calls[-1], 'B')

    def test_stale_bounded_provider_context_cannot_be_consumed_by_newer_request(self):
        self.app._continuation_tools = ReadOnlyToolExecutor(settings=self.settings, functions={'read_notepad': lambda: 'REQUEST_A_CONTEXT'})
        old = self.bounded_provider('A')
        self.app._continuation.start('B', authority_origin='user')
        self.enqueue('web', 'B')
        old()
        self.assertEqual(self.app._ai.calls, [])
        self.deliver()
        self.assert_owner(self.app._ai.calls[-1], 'B')

    def test_context_not_reused_after_success(self):
        for kind in ('web', 'notepad'):
            with self.subTest(kind=kind):
                self.enqueue(kind, 'A')
                self.deliver()
                self.assert_owner(self.app._ai.calls[-1], 'A')
                self.direct('A repeated without retrieval')

    def test_context_not_reused_after_provider_exception_or_timeout(self):
        for kind in ('web', 'notepad'):
            for failure in (RuntimeError('fake provider error'), TimeoutError('fake timeout')):
                with self.subTest(kind=kind, failure=type(failure).__name__):
                    self.enqueue(kind, 'A')
                    self.app._ai.responses.append(failure)
                    self.deliver()
                    self.assertIsNone(self.app._ai_operation_token)
                    self.direct('B')

    def test_worker_start_failure_leaves_no_context_for_another_request(self):
        for kind in ('web', 'notepad'):
            with self.subTest(kind=kind):
                self.app.start_failure = True
                with self.assertRaisesRegex(RuntimeError, 'startup'):
                    self.enqueue(kind, 'A')
                self.app.start_failure = False
                self.direct('B')

    def test_worker_refusal_leaves_no_context_for_another_request(self):
        for kind in ('web', 'notepad'):
            with self.subTest(kind=kind):
                self.app.refuse_worker = True
                self.enqueue(kind, 'A')
                self.app.refuse_worker = False
                self.direct('B')

    def test_pending_context_cannot_leak_into_new_direct_request(self):
        for kind in ('web', 'notepad'):
            with self.subTest(kind=kind):
                self.enqueue(kind, 'A')
                self.direct('unrelated request')
                call_count = len(self.app._ai.calls)
                self.deliver()
                if kind == 'notepad':
                    self.assertEqual(len(self.app._ai.calls), call_count)
                    self.assertNotIn('REQUEST_A_CONTEXT', self.app._ai.calls[-1]['turn'])
                else:
                    self.assert_owner(self.app._ai.calls[-1], 'A')

    def test_busy_provider_keeps_token_and_context_does_not_escape(self):
        for kind in ('web', 'notepad'):
            with self.subTest(kind=kind):
                self.enqueue(kind, 'A')
                token = self.app._reserve_ai_operation(direct=True, user_message='BUSY', origin='user')
                self.deliver()
                self.assertIs(self.app._ai_operation_token, token)
                self.app._release_ai_operation(token)
                self.direct('B')

    def test_shutdown_skips_query_without_publishing_context(self):
        self.enqueue('notepad', 'A')
        self.app._graceful_shutdown()
        self.deliver()
        self.assertEqual(self.app._ai.calls, [])
        self.assertEqual(self.app.spoken, [])

    def test_retained_web_and_notes_bound_and_label_untrusted_context(self):
        for kind in ('web', 'notepad'):
            with self.subTest(kind=kind):
                payload = 'REQUEST_A_CONTEXT ' + 'x'*20000 + ' OUTSIDE_LIMIT'
                if kind == 'web':
                    with patch('agetha.features.web_rag.format_search_results_for_prompt', return_value=payload):
                        web_context.handle_search_web(self.app, {'query': 'A'}, DispatchCtx('A','neutral',[],False))
                else:
                    with patch('agetha.ui.dashboard.read_notepad_text', return_value=payload):
                        memory_presentation.handle_read_notepad(self.app, {}, DispatchCtx('A','neutral',[],False))
                self.app._speech_active = False
                self.deliver()
                call = self.app._ai.calls[-1]
                self.assertIn('REQUEST_A_CONTEXT', call['turn'])
                self.assertNotIn('OUTSIDE_LIMIT', call['turn'])
                self.assertIn('UNTRUSTED', call['turn'])
                self.assertIn('Do not call '+('search_web' if kind == 'web' else 'read_notepad'), call['system'])

    def test_real_query_apis_ignore_ambient_context_with_ownerless_fields(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                ai, payloads = self.real_ai()
                ai._pending_web_rag_context = 'REQUEST_A_CONTEXT'
                ai._pending_suppress_web_rag = True
                ai._pending_notepad_context = 'REQUEST_A_CONTEXT'
                ai._pending_suppress_read_notepad = True
                method = AIEngine.query_streaming if streaming else AIEngine.query
                method(ai, user_message='B', request_origin='user', request_profile='fast_user')
                self.assertNotIn('REQUEST_A_CONTEXT', str(payloads))
                self.assertNotIn('Do not call read_notepad again', str(payloads))
                self.assertNotIn('Do not call search_web', str(payloads))

    def test_real_query_apis_consume_only_explicit_web_and_notes(self):
        for streaming in (False, True):
            for kind in ('web', 'notepad'):
                with self.subTest(streaming=streaming, kind=kind):
                    ai, payloads = self.real_ai()
                    ai._pending_web_rag_context = ai._pending_notepad_context = 'REQUEST_A_CONTEXT'
                    ai._pending_suppress_web_rag = ai._pending_suppress_read_notepad = True
                    kwargs = ({'web_rag_context':'REQUEST_B_CONTEXT', 'suppress_web_rag':True}
                              if kind == 'web' else {'notepad_context':'REQUEST_B_CONTEXT', 'suppress_read_notepad':True})
                    method = AIEngine.query_streaming if streaming else AIEngine.query
                    result = method(ai, user_message='B', request_origin='tool_result', request_profile='fast_tool_result', **kwargs)
                    self.assertEqual(result['segments'][0]['text'], 'answer')
                    self.assertIn('REQUEST_B_CONTEXT', str(payloads))
                    self.assertNotIn('REQUEST_A_CONTEXT', str(payloads))
                    self.assertIn('UNTRUSTED', str(payloads))
                    self.assertIn('Do not call '+('search_web' if kind == 'web' else 'read_notepad'), str(payloads))
                    unrelated = []
                    ai._provider_create = lambda **kw: (unrelated.append(kw['messages']) or
                        (iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=json.dumps(answer())))])])
                         if streaming else SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer())))])))
                    method(ai, user_message='C', request_origin='user', request_profile='fast_user')
                    self.assertNotIn('REQUEST_B_CONTEXT', str(unrelated))
                    self.assertNotIn('Do not call search_web', str(unrelated))
                    self.assertNotIn('Do not call read_notepad', str(unrelated))

    def test_identical_messages_still_capture_distinct_request_context(self):
        for kind in ('web', 'notepad'):
            with self.subTest(kind=kind):
                self.enqueue(kind, 'A', message='same text')
                self.enqueue(kind, 'B', message='same text')
                self.deliver(1)
                self.assert_owner(self.app._ai.calls[-1], 'B')
                self.app._speech_active = False
                call_count = len(self.app._ai.calls)
                self.deliver()
                if kind == 'notepad':
                    self.assertEqual(len(self.app._ai.calls), call_count)
                    self.assert_owner(self.app._ai.calls[-1], 'B')
                else:
                    self.assert_owner(self.app._ai.calls[-1], 'A')

    def test_late_invalidated_retrieval_cannot_replace_newer_context(self):
        for canceled in (False, True):
            with self.subTest(canceled=canceled):
                self.reset_scenario()
                self.app._continuation.start('A', authority_origin='user')
                def finish_old_lookup():
                    if canceled:
                        self.app._on_cancel_ai()
                    else:
                        self.app._invalidate_request_context()
                        self.app._continuation.start('B', authority_origin='user')
                    self.direct('B')
                    self.enqueue('notepad', 'B')
                    return 'REQUEST_A_CONTEXT'
                with patch('agetha.ui.dashboard.read_notepad_text', side_effect=finish_old_lookup):
                    memory_presentation.handle_read_notepad(self.app, {}, DispatchCtx('A','neutral',[],False))
                self.deliver()  # B was created before A's lookup finally returned.
                self.assert_owner(self.app._ai.calls[-1], 'B')
                self.assertEqual(self.app.jobs, [])  # A cannot hand off after losing ownership.

    def test_new_direct_input_expires_queued_context_even_with_engine_off(self):
        for kind in ('web', 'notepad'):
            with self.subTest(kind=kind):
                self.reset_scenario()
                self.app._continuation = None
                self.enqueue(kind, 'A')
                self.app._on_user_input()
                self.fixture.job('user-ai')
                call_count = len(self.app._ai.calls)
                self.deliver()
                self.assertEqual(len(self.app._ai.calls), call_count)
                self.assertNotIn('REQUEST_A_CONTEXT', self.app._ai.calls[-1]['turn'])

    def test_delayed_web_lookup_after_cancellation_cannot_submit_expired_context(self):
        def finish_old_lookup(results):
            self.app._on_cancel_ai()
            self.enqueue('web', 'B')
            return 'REQUEST_A_CONTEXT'
        with patch('agetha.features.web_rag.format_search_results_for_prompt', side_effect=finish_old_lookup):
            web_context.handle_search_web(self.app, {'query':'A'}, DispatchCtx('A','neutral',[],False))
        self.direct('B')
        self.deliver()
        self.assert_owner(self.app._ai.calls[-1], 'B')
        self.app._speech_active = False
        call_count = len(self.app._ai.calls)
        self.deliver()
        self.assertEqual(len(self.app._ai.calls), call_count)

    def test_new_input_with_engine_off_preserves_unrelated_queued_ui_sync(self):
        self.app._continuation = None
        scheduled = threading.Event()
        effects, values, errors = [], [], []
        original = self.app._schedule_ui
        def schedule(callback, delay_ms=0):
            result = original(callback, delay_ms)
            scheduled.set()
            return result
        def run():
            try:
                values.append(self.app._call_ui_sync(lambda: effects.append('UI') or 'UI'))
            except BaseException as exc:
                errors.append(exc)
        with patch.object(self.app, '_schedule_ui', schedule):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(scheduled.wait(3))
                self.app._on_user_input()
                self.fixture.flush()
            finally:
                worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(effects, ['UI'])
            self.assertEqual(values, ['UI'])

    def test_web_fetch_success_disabled_missing_url_and_error_are_request_local(self):
        cases = (
            ({'url':'https://example.invalid'}, self.settings, None, 'REQUEST_A_CONTEXT'),
            ({'url':''}, self.settings, None, 'no url provided'),
            ({'url':'https://example.invalid'}, self.settings, ValueError('synthetic fetch failure'), 'synthetic fetch failure'),
            ({'url':'https://example.invalid'}, type(self.settings)({**self.settings.raw,'ENABLE_WEB_RAG':'no'}), None, 'web fetch is disabled'),
        )
        for response, settings, error, expected in cases:
            with self.subTest(expected=expected), patch.object(web_context,'get_settings',return_value=settings), \
                    patch('agetha.features.web_rag.fetch_webpage',side_effect=error,return_value={}), \
                    patch('agetha.features.web_rag.format_fetched_page_for_prompt',return_value='REQUEST_A_CONTEXT'):
                self.app._speech_active = False
                web_context.handle_fetch_webpage(self.app, response, DispatchCtx('A','neutral',[],False))
                self.deliver()
                self.assertIn(expected,self.app._ai.calls[-1]['turn'])
                self.assertIn('Do not call search_web',self.app._ai.calls[-1]['system'])
                self.direct('B')

    def test_streaming_host_forwards_only_own_context(self):
        for kind in ('web','notepad'):
            with self.subTest(kind=kind), patch.object(main,'_SETTINGS',SimpleNamespace(enable_streaming=True)):
                self.enqueue(kind,'A')
                self.enqueue(kind,'B')
                self.deliver(1)
                self.assert_owner(self.app._ai.calls[-1],'B')
                self.app._speech_active = False
                call_count = len(self.app._ai.calls)
                self.deliver()
                if kind == 'notepad':
                    self.assertEqual(len(self.app._ai.calls), call_count)
                    self.assert_owner(self.app._ai.calls[-1], 'B')
                else:
                    self.assert_owner(self.app._ai.calls[-1],'A')

    def test_bounded_web_followup_isolated_from_legacy_web_and_notes(self):
        for kind in ('web','notepad'):
            with self.subTest(legacy=kind):
                self.reset_scenario()
                self.enqueue(kind,'A')
                self.app._continuation_tools = ReadOnlyToolExecutor(settings=self.settings, functions={
                    'search_web':lambda query,limit:[{'title':'REQUEST_B_CONTEXT','snippet':'synthetic','url':''}],
                })
                self.fixture.bounded(command='search_web',goal='B',query='B')
                self.fixture.job('continuation-tool')
                self.fixture.job('continuation-provider')
                self.assert_owner(self.app._ai.calls[-1],'B')
                self.app._speech_active = False
                self.deliver()
                self.assert_owner(self.app._ai.calls[-1],'A')

    def test_late_bounded_web_result_cannot_populate_newer_legacy_context(self):
        def old_web(query,limit):
            self.app._on_cancel_ai()
            self.direct('B')
            self.enqueue('notepad','B')
            return [{'title':'REQUEST_A_CONTEXT','snippet':'synthetic','url':''}]
        self.app._continuation_tools = ReadOnlyToolExecutor(settings=self.settings,functions={'search_web':old_web})
        self.fixture.bounded(command='search_web',goal='A',query='A')
        self.fixture.job('continuation-tool')
        self.assertFalse(any(n=='continuation-provider' for n,_ in self.app.jobs))
        self.deliver()
        self.assert_owner(self.app._ai.calls[-1],'B')

    def real_ai(self):
        ai = FakeAI(self.settings)
        ai._client = object()
        ai._ensure_provider_initialized = lambda authorization=None: True
        ai._update_user_activity = lambda _: None
        ai._track_tokens = lambda _: None
        ai._use_local_ai, ai._use_openrouter, ai._enable_groq = True, False, False
        ai._config = {'LOCAL_AI_MODEL':'synthetic'}
        payloads = []
        def provider(**kwargs):
            payloads.append(kwargs['messages'])
            raw = json.dumps(answer())
            if kwargs['stream']:
                return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=raw))])])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw))])
        ai._provider_create = provider
        return ai, payloads


if __name__ == '__main__':
    unittest.main()
