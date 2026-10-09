"""Legacy memory final-delivery regressions; disposable/fake state only.

Keep provider admission, parsing, dispatch and app speech ownership real.
Only external workers/UI/audio/readers are fake. No sleeps or real user data.
"""
from __future__ import annotations

import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main
from agetha.app_config import AppSettings
from agetha.commands.handlers import memory_presentation
from agetha.commands.handlers.support import DispatchCtx
from tests import test_continuation_comparison as comparison


A='LEGACY_REQUEST_A'
B='DIRECT_REQUEST_B'


def response(text=A):
    return dict(command='speak',mood='neutral',shutdown=False,
        segments=[dict(text=text,pause=0.0)])


class LegacyMemoryDelivery(unittest.TestCase):
    def setUp(self):
        self.harness=comparison.ContinuationComparison(methodName='runTest')
        self.harness.case('legacy')
        self.addCleanup(self.harness.doCleanups)
        self.app=self.harness.app
        self.app._persistent_mood=None
        self.app._input_var=SimpleNamespace(get=lambda:B,set=lambda _:None)
        self.provider_started=threading.Event()
        self.provider_returned=threading.Event()
        self.final_attempted=[]
        self.trace=[]
        self.dispatch=self.app._dispatch_response
        def attempt(*args,**kwargs):
            self.final_attempted.append(args[0])
            self.trace.append('final_delivery_attempt')
            return self.dispatch(*args,**kwargs)
        self.app._dispatch_response=attempt

    def memory(self, *, enabled=True, text=A):
        if not enabled:
            settings=AppSettings({**self.harness.fixture.settings.raw,'ENABLE_LONGTERM_MEMORY':'no'})
            self.enterContext(patch.object(memory_presentation,'get_settings',return_value=settings))
        memory_presentation.handle_search_memory(self.app,dict(command='search_memory',query=text),
            DispatchCtx(text,'neutral',[],False,'user'))
        self.trace.append('memory_admitted')
        self.assertEqual(self.app.jobs[-1][0],'memory-requery')

    def run_provider(self, result=None, during=None):
        self.app._ai.responses.append(response() if result is None else result)
        def started():
            self.provider_started.set()
            self.trace.append('provider_started')
            self.assertTrue(self.app._ai_busy)
            if during:
                during()
            self.provider_returned.set()
            self.trace.append('provider_result_ready')
        self.app._ai.hook=started
        self.harness.job('memory-requery')
        self.assertTrue(self.provider_started.wait(0))
        self.assertTrue(self.provider_returned.wait(0))
        self.trace.append('worker_returned')

    def newer_input(self):
        self.app._input_box.config(state='normal')
        self.app._on_user_input()
        self.trace.append('direct_B_admitted')
        self.assertEqual(self.app.jobs[-1][0],'user-ai')

    def play_b(self):
        self.app._ai.hook=lambda:None
        self.app._ai.responses.append(response(B))
        self.harness.job('user-ai')
        if any(n=='queued-ai' for n,_ in self.app.jobs):
            self.harness.job('queued-ai')
        self.harness.ui()

    def assert_no_a(self):
        self.assertFalse(any(A in str(event) for event in self.harness.events),
            f'Stale {A} escaped into user-visible events: {self.harness.events}; trace={self.trace}')
        self.assertFalse(any(A in text for batch in self.harness.audio for text in batch))
        self.assertFalse(any(A in text for batch in self.harness.subtitles for text in batch))

    def test_primary_provider_returns_after_B_admission_cannot_deliver_A(self):
        self.memory()
        self.run_provider(during=self.newer_input)
        b_epoch=self.app._context_request_epoch
        self.harness.ui()
        self.assert_no_a()
        self.assertEqual(self.app._context_request_epoch,b_epoch)
        self.assertFalse(self.app._ai_busy)
        self.play_b()
        self.assertEqual(self.harness.audio,[[B]])
        self.assertEqual(self.harness.subtitles,[[B]])
        self.assertNotIn(A,self.app._ai.calls[-1]['turn'])
        self.assertEqual(self.app._ai.calls[-1].get('memory_search_context',''),'')

    def test_current_memory_result_delivers_exactly_once(self):
        for enabled in (True,False):
            with self.subTest(enabled=enabled):
                self.memory(enabled=enabled)
                self.run_provider()
                self.harness.finish()
        self.assertEqual(self.harness.audio,[[A],[A]])
        self.assertEqual(self.harness.subtitles,[[A],[A]])
        self.assertFalse(self.app._speech_active)
        self.assertFalse(self.app._ai_busy)

    def test_disabled_memory_followup_cannot_deliver_stale_result(self):
        self.memory(enabled=False)
        self.run_provider(during=self.newer_input)
        self.harness.ui()
        self.assert_no_a()

    def test_generation_change_without_cancel_flag_suppresses_result(self):
        self.memory()
        self.run_provider(during=self.app._invalidate_request_context)
        self.assertFalse(self.app._cancel_event.is_set())
        self.harness.ui()
        self.assert_no_a()

    def test_cancel_then_new_request_clears_flag_without_reviving_A(self):
        self.memory()
        def invalidate():
            self.app._on_cancel_ai()
            self.newer_input()
            self.app._cancel_event.clear()
        self.run_provider(during=invalidate)
        self.harness.ui()
        self.assert_no_a()
        self.play_b()
        self.assertEqual(self.harness.audio,[[B]])

    def test_cancelled_provider_result_has_no_final_delivery(self):
        self.memory()
        self.run_provider(during=self.app._on_cancel_ai)
        self.harness.ui()
        self.assert_no_a()
        self.assertFalse(self.app._ai_busy)

    def test_shutdown_before_provider_result_suppresses_delivery(self):
        self.memory()
        self.run_provider(during=self.app._graceful_shutdown)
        self.harness.ui()
        self.assert_no_a()
        self.assertFalse(self.app._ai_busy)

    def test_compact_transition_before_provider_result_suppresses_old_delivery(self):
        self.memory()
        self.run_provider(during=self.harness.fixture.transition)
        self.harness.ui()
        self.assert_no_a()
        self.assertFalse(self.app._ai_busy)

    def test_profile_UI_generation_change_suppresses_result(self):
        self.memory()
        self.run_provider(during=self.app._invalidate_continuation_ui_delivery)
        self.harness.ui()
        self.assert_no_a()

    def test_queued_speech_invalidated_before_UI_callback_does_not_deliver(self):
        self.memory()
        self.run_provider()
        self.assertEqual(len(self.final_attempted),1)
        self.assertEqual(self.harness.audio,[])
        self.app._invalidate_request_context()
        self.harness.ui()
        self.assert_no_a()
        self.assertFalse(self.app._speech_active)

    def test_late_speech_cleanup_cannot_clear_newer_speech_owner(self):
        self.memory()
        self.run_provider()
        held=list(self.app.ui)
        self.app.ui.clear()
        self.newer_input()
        # Input cancellation/stop can detach the old queued speech before B starts.
        self.app._speech_active=False
        self.app._speech_operation_token=None
        self.play_b()
        b_token=self.app._speech_operation_token
        b_state=self.app._state
        for callback in held:
            callback()
        self.harness.ui()
        self.assert_no_a()
        self.assertIs(self.app._speech_operation_token,b_token)
        self.assertTrue(self.app._speech_active)
        self.assertEqual(self.app._state,b_state)
        self.assertEqual(self.harness.audio,[[B]])

    def test_queued_idle_cleanup_cannot_clear_B_subtitle_or_state(self):
        self.memory()
        self.run_provider(result=dict(command='idle',mood='sad',segments=[]))
        held=list(self.app.ui)
        self.app.ui.clear()
        self.newer_input()
        self.play_b()
        b_state=self.app._state
        b_token=self.app._speech_operation_token
        visible_before=list(self.harness.events)
        for callback in held:
            callback()
        self.harness.ui()
        self.assertEqual(self.harness.events,visible_before,'Old idle UI changed B')
        self.assertEqual(self.app._state,b_state)
        self.assertIs(self.app._speech_operation_token,b_token)

    def test_queued_provider_limit_notice_cannot_appear_for_B(self):
        self.memory()
        self.run_provider(result=dict(command='idle',mood='neutral',segments=[],groq_exhausted=True))
        held=list(self.app.ui)
        self.app.ui.clear()
        self.newer_input()
        self.play_b()
        visible_before=list(self.harness.events)
        for callback in held:
            callback()
        self.harness.ui()
        self.assertEqual(self.app.notices,[])
        self.assertEqual(self.harness.events,visible_before)

    def test_queued_response_motion_cannot_outlive_memory_request(self):
        motion=[]
        self.app._motion=SimpleNamespace(play_mood=motion.append)
        self.app._motion_request_job=None
        self.app._presence_decision=lambda:SimpleNamespace(allow_voice=True,allow_window_motion=True)
        self.memory()
        result=response()
        result['mood']='sad'
        self.run_provider(result=result)
        self.newer_input()
        self.harness.ui()
        self.assertEqual(motion,[],'Stale memory response moved the UI')
        self.assert_no_a()

    def test_current_memory_response_motion_and_text_deliver_once(self):
        motion=[]
        self.app._motion=SimpleNamespace(play_mood=motion.append)
        self.app._motion_request_job=None
        self.app._presence_decision=lambda:SimpleNamespace(allow_voice=True,allow_window_motion=True)
        self.memory()
        result=response()
        result['mood']='sad'
        self.run_provider(result=result)
        self.harness.finish()
        self.assertEqual(motion,['sad'])
        self.assertEqual(self.harness.audio,[[A]])
        self.assertEqual(self.harness.subtitles,[[A]])

    def test_UI_claim_during_enqueue_cannot_strand_current_motion(self):
        attempted=threading.Event()
        motion=[]
        errors=[]
        ui_threads=[]
        class ClaimLock:
            def __init__(self):
                self.lock=threading.Lock()
            def __enter__(self):
                if threading.current_thread().name=='synthetic-memory-ui':
                    attempted.set()
                self.lock.acquire()
            def __exit__(self,*args):
                self.lock.release()
        self.app._ai_tick_lock=ClaimLock()
        self.app._motion=SimpleNamespace(play_mood=motion.append)
        self.app._motion_request_job=None
        self.app._presence_decision=lambda:SimpleNamespace(allow_window_motion=True)
        def enqueue(callback):
            def deliver():
                try:
                    callback()
                except BaseException as exc:
                    errors.append(exc)
            ui=threading.Thread(target=deliver,name='synthetic-memory-ui')
            ui_threads.append(ui)
            self.addCleanup(lambda:ui.join(timeout=2))
            ui.start()
            self.assertTrue(attempted.wait(2),'Fake UI never attempted its claim')
            return callback
        self.app._schedule_ui=enqueue
        self.app._play_response_motion('sad',result_is_current=lambda:True)
        ui_threads[0].join(timeout=2)
        self.assertFalse(ui_threads[0].is_alive(),'Motion handoff deadlocked')
        self.assertEqual(errors,[])
        self.assertEqual(motion,['sad'])
        self.assertIsNone(self.app._motion_request_job)

    def test_old_motion_cleanup_does_not_clear_successor_job(self):
        self.app._motion=SimpleNamespace(play_mood=lambda _:None)
        self.app._motion_request_job=None
        self.app._presence_decision=lambda:SimpleNamespace(allow_voice=True,allow_window_motion=True)
        self.memory()
        self.run_provider()
        held=list(self.app.ui)
        self.app.ui.clear()
        self.newer_input()
        # A newer UI owner has replaced the old job before its retained callback.
        newer_job=object()
        self.app._motion_request_job=newer_job
        for callback in held:
            callback()
        self.assertIs(self.app._motion_request_job,newer_job)

    def test_successful_Full_commit_without_invalidation_keeps_current_result(self):
        compact=AppSettings({**self.harness.fixture.settings.raw,'COMPACT_MODE':'yes'})
        full=AppSettings({**self.harness.fixture.settings.raw,'COMPACT_MODE':'no'})
        self.app._capabilities=comparison.CapabilityController(comparison.CapabilityPolicy.from_settings(compact))
        self.app._capability_consent=comparison.CapabilityConsentFlow()
        first=self.app._capability_consent.begin_enable()
        self.app._capability_consent.confirm_first(first.generation)
        final=self.app._capability_consent.finish_demo(first.generation)
        self.app._capability_transition_generation=self.app._capabilities.begin_full_transition()
        self.memory()
        def commit():
            with patch('agetha.app_config.patch_config_key',return_value=True), \
                 patch('agetha.app_config.clear_compact_mode_fail_closed',return_value=True), \
                 patch.object(main,'get_settings',return_value=full), \
                 patch.object(self.app,'_start_full_mode_services'), \
                 patch.object(self.app,'_refresh_dashboard_after_profile_commit'):
                self.app._on_final_full_mode_decision(final.generation,True)
        self.run_provider(during=commit)
        self.harness.finish()
        self.assertEqual(self.app._capabilities.snapshot().profile.value,'full')
        self.assertEqual(self.harness.audio,[[A]])

    def test_multiple_memory_requests_do_not_cross_deliver(self):
        self.memory()
        self.app._invalidate_request_context()
        self.memory(text=B)
        self.harness.job('memory-requery')
        self.assertEqual(self.app._ai.calls,[],'Expired worker started another provider cycle')
        self.harness.ui()
        self.assert_no_a()
        self.app._ai.responses.append(response(B))
        self.app._ai.hook=lambda:None
        self.harness.job('memory-requery')
        self.harness.finish()
        self.assertEqual(self.harness.audio,[[B]])
        self.assertEqual(self.harness.subtitles,[[B]])

    def test_current_direct_response_remains_unchanged(self):
        self.app._ai.responses.append(response(B))
        self.app._ai_tick(B,origin='user')
        self.harness.finish()
        self.assertEqual(self.harness.audio,[[B]])
        self.assertEqual(self.harness.subtitles,[[B]])

    def test_provider_error_releases_owner_without_retry_or_delivery(self):
        self.memory()
        self.run_provider(result=RuntimeError('synthetic provider error'),during=self.newer_input)
        self.harness.ui()
        self.assert_no_a()
        self.assertEqual(len(self.app._ai.calls),1)
        self.assertEqual(self.final_attempted,[])
        self.assertFalse(self.app._ai_busy)
        self.assertEqual([n for n,_ in self.app.jobs],['user-ai'])

    def test_empty_memory_results_preserve_prompt_and_valid_delivery(self):
        with patch('agetha.core.memory_search.format_search_results_for_prompt',return_value='[no synthetic matches]'):
            self.memory()
        self.run_provider()
        self.harness.finish()
        self.assertEqual(self.app._ai.calls[-1]['memory_search_context'],'[no synthetic matches]')
        self.assertEqual(self.harness.audio,[[A]])

    def test_memory_reader_failure_still_uses_current_result_lifetime(self):
        with patch('agetha.core.memory_search.search_memories',side_effect=ValueError('synthetic reader failure')):
            self.memory()
        self.run_provider(during=self.newer_input)
        self.harness.ui()
        self.assert_no_a()
        self.assertIn('synthetic reader failure',self.app._ai.calls[-1]['memory_search_context'])

    def test_real_parser_malformed_result_cannot_reset_successor(self):
        self.enterContext(patch('agetha.core.ai_engine._MEMORY_SYSTEM_AVAILABLE',False))
        self.memory()
        engine=comparison.RealQueryFakeAI(self.harness.fixture.settings,['{bad'])
        self.app._ai=engine
        create=engine._client.create
        def result(**kwargs):
            self.newer_input()
            return create(**kwargs)
        engine._client.chat.completions.create=result
        with patch('agetha.core.ai_engine._MEMORY_SYSTEM_AVAILABLE',False):
            self.harness.job('memory-requery')
        held=list(self.app.ui)
        self.app.ui.clear()
        engine._client.chat.completions.create=create
        engine._client.responses.append(json.dumps(response(B)))
        self.harness.job('user-ai')
        self.harness.ui()
        b_state=self.app._state
        visible_before=list(self.harness.events)
        for callback in held:
            callback()
        self.harness.ui()
        self.assertEqual(self.harness.events,visible_before)
        self.assertEqual(self.app._state,b_state)
        self.assertEqual(self.harness.audio,[[B]])
        self.assertEqual(len(engine._client.calls),2)

    def test_final_delivery_exception_does_not_clear_successor(self):
        self.memory()
        self.run_provider()
        def broken_subtitle(*args,**kwargs):
            self.newer_input()
            self.app._speech_active=False
            self.app._speech_operation_token=None
            self.app._subtitle.speak=self.harness.subtitle
            self.play_b()
            raise RuntimeError('synthetic final delivery failure')
        self.app._subtitle.speak=broken_subtitle
        with self.assertRaisesRegex(RuntimeError,'synthetic final delivery failure'):
            self.harness.ui()
        b_token=self.app._speech_operation_token
        self.harness.ui()
        self.assertIs(self.app._speech_operation_token,b_token)
        self.assertTrue(self.app._speech_active)
        # Audio A entered while current before the subtitle raised. It is irreversible.
        self.assertEqual(self.harness.audio,[[A],[B]])
        self.assertEqual(self.harness.subtitles,[[B]])


if __name__=='__main__':
    unittest.main()
