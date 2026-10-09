"""F11 observed contracts, including known gaps. No convergence or fixes.

Run only with isolated storage and fake providers/workers/UI/audio. The real
admission, reservation, continuation, prompt and speech lifetime code runs.
Assertions describe current behavior; unsafe observations are not approval.
"""
from __future__ import annotations

import json
import threading
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main
from agetha.app_config import AppSettings
from agetha.commands import command_handlers
from agetha.commands.handlers import memory_presentation, web_context
from agetha.commands.handlers.support import DispatchCtx
from agetha.core.continuation import ContinuationEngine, ContinuationState, DecisionKind
from agetha.core.ai_engine import AIEngine
from agetha.core.capabilities import CapabilityController, CapabilityPolicy
from agetha.core.capability_consent import CapabilityConsentFlow
from agetha.core.read_only_tools import ReadOnlyToolExecutor
from agetha.features.tts_player import VoiceOutputCoordinator
from tests import test_followup_characterization as fixtures


def envelope(command='speak', texts=('FINAL A',), **fields):
    return dict(command=command, mood='happy', shutdown=False,
        segments=[dict(text=t, pause=0.0) for t in texts], **fields)


class ContinuationComparison(unittest.TestCase):
    observations = {}

    def case(self, path, streaming=False):
        if hasattr(self, 'fixture'):
            self.fixture.doCleanups()
        self.fixture = fixtures.FollowupCharacterization(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app = self.fixture.app
        self.path = path
        if path == 'legacy':
            self.app._continuation = None
        self.app._context_request_epoch = 0
        self.app._speech_operation_token = None
        self.events, self.completions, self.audio, self.subtitles = [], [], [], []
        self.app._presence_decision = lambda: SimpleNamespace(allow_voice=True)
        self.app._voice_out = SimpleNamespace(start_speech=self.voice, stop=lambda: None)
        self.app._subtitle.speak = self.subtitle
        self.app._subtitle.clear = lambda:self.events.append(('subtitle_clear',))
        self.app._set_state = lambda state, *_: self.events.append(('state', state)) or setattr(self.app, '_state', state)
        self.app._continuation_tools = ReadOnlyToolExecutor(settings=self.fixture.settings,
            functions={'read_notepad': lambda: 'ALPHA notes',
                'search_web': lambda *_: [{'title': 'ALPHA web', 'snippet': 'synthetic public fact'}],
                'search_memory': lambda *_: 'ALPHA memory'},
            resolver=lambda *_: self.fail('Unexpected DNS'), clock=lambda: self.fixture.now)
        self.fixture.enterContext(patch('agetha.ui.dashboard.read_notepad_text', return_value='ALPHA notes'))
        self.fixture.enterContext(patch('agetha.features.web_rag.format_search_results_for_prompt', return_value='ALPHA web'))
        self.fixture.enterContext(patch('agetha.core.memory_search.format_search_results_for_prompt', return_value='ALPHA memory'))
        if streaming:
            settings=AppSettings({**self.fixture.settings.raw,'ENABLE_STREAMING':'yes'})
            self.fixture.enterContext(patch.object(main,'_SETTINGS',settings))
        self.app._ai.hook = lambda: self.events.append(('provider', self.fixture.now))
        return self.app

    def voice(self, segments, mood, **kw):
        text=[s['text'] for s in segments]
        self.audio.append(text)
        self.events.append(('audio_enqueue', text, mood))

    def subtitle(self, segments, **kw):
        text=[s['text'] for s in segments]
        self.subtitles.append(text)
        self.completions.append(kw['on_done'])
        self.events.append(('subtitle', text))

    def ui(self):
        for _ in range(100):
            if not self.app.ui:
                return
            self.app.ui.pop(0)()
        self.fail('Unbounded UI work')

    def job(self, name=None):
        i=next((i for i,(n,_) in enumerate(self.app.jobs) if name is None or n==name), None)
        self.assertIsNotNone(i, (name, [n for n,_ in self.app.jobs]))
        n,callback=self.app.jobs.pop(i)
        self.events.append(('worker', n))
        with patch('main.threading.current_thread', return_value=SimpleNamespace(name='synthetic-worker')):
            callback()

    def complete(self):
        self.assertTrue(self.completions)
        self.completions.pop(0)()
        self.ui()

    def finish(self):
        for _ in range(30):
            self.ui()
            if self.app.jobs:
                self.job()
            elif self.completions:
                self.complete()
            else:
                return
        self.fail('Unbounded synthetic continuation')

    def start(self, feature='read_notepad', texts=()):
        fields={'query':'synthetic query'} if feature in ('search_web','search_memory') else {}
        response=envelope(feature, texts, **fields)
        if self.path=='bounded':
            engine=self.app._continuation
            d=engine.start('A',authority_origin='user')
            d=engine.accept_initial_model_response(d.session_id,d.generation,response)
            self.app._handle_continuation_decision(d)
        else:
            handlers={'read_notepad':memory_presentation.handle_read_notepad,
                'search_web':web_context.handle_search_web,'search_memory':memory_presentation.handle_search_memory}
            handlers[feature](self.app,response,DispatchCtx('A','happy',response['segments'],False,'user'))
        self.ui()

    def provider_ready(self, feature='read_notepad'):
        self.start(feature)
        if self.path=='bounded':
            self.job('continuation-tool')
            self.ui()

    def record(self, label=''):
        engine=self.app._continuation
        active=engine.active_snapshot() if engine else None
        last=engine.last_snapshot() if engine else None
        calls=[]
        for call in self.app._ai.calls:
            calls.append({k:call.get(k) for k in ('user_message','request_profile','request_origin',
                'screen_context','doc_content','web_rag_context','notepad_context','memory_search_context',
                'suppress_web_rag','suppress_read_notepad','suppress_search_memory','turn')})
        self.observations[self._testMethodName+'/'+self.path+label]=dict(
            requests=calls,events=self.events,audio=self.audio,subtitles=self.subtitles,
            notices=self.app.notices,active=active.state.value if active else None,
            last=last.state.value if last else None,state=self.app._state,
            busy=self.app._ai_busy,speech_active=self.app._speech_active,
            queued_workers=[n for n,_ in self.app.jobs],clock=self.fixture.now)

    @classmethod
    def tearDownClass(cls):
        # Explicit storage isolation: this evidence file is beneath copied tests.
        root=Path(__file__).resolve().parents[1]
        if not (root/'isolated-temp').is_dir():
            return
        (root/'isolated-temp'/'continuation-observations.json').write_text(
            json.dumps(cls.observations,indent=2,default=str),encoding='utf-8')

    def normal(self, feature, marker):
        for path in ('legacy','bounded'):
            with self.subTest(path=path):
                self.case(path)
                self.app._last_screen_text='CACHED screen'
                self.app._ai.responses.extend([envelope(feature,(),query='synthetic query'),envelope()])
                self.app._ai_tick('A',origin='user')
                self.finish()
                self.assertEqual(self.audio,[['FINAL A']])
                self.assertEqual(self.subtitles,[['FINAL A']])
                self.assertEqual(len(self.app._ai.calls),2)
                follow=self.app._ai.calls[-1]
                self.assertIn(marker,follow['turn'])
                self.assertEqual(follow['request_profile'],'tool_continuation' if path=='bounded' else 'fast_tool_result')
                self.assertEqual(follow['screen_context'],'' if path=='bounded' else 'CACHED screen')
                self.assertFalse(self.app._ai_busy)
                self.assertFalse(self.app._speech_active)
                self.record()

    def test_normal_web_direct_message_and_followup(self):
        self.normal('search_web','ALPHA web')

    def test_normal_notepad_direct_message_and_followup(self):
        self.normal('read_notepad','ALPHA notes')

    def test_normal_memory_direct_message_and_followup(self):
        self.normal('search_memory','ALPHA memory')

    def test_bounded_multiple_sequential_tools_use_latest_context_only(self):
        self.case('bounded')
        self.provider_ready()
        self.app._ai.responses.extend([envelope('search_memory',(),query='second'),envelope()])
        self.finish()
        self.assertEqual(len(self.app._ai.calls),2)
        self.assertIn('ALPHA notes',self.app._ai.calls[0]['doc_content'])
        self.assertNotIn('ALPHA notes',self.app._ai.calls[1]['doc_content'])
        self.assertIn('ALPHA memory',self.app._ai.calls[1]['doc_content'])
        self.assertEqual(len(self.app._continuation.last_snapshot().history),2)
        self.assertEqual(self.audio,[['FINAL A']])
        self.record()

    def test_legacy_followup_tool_request_is_passive_and_does_not_chain(self):
        self.case('legacy')
        self.provider_ready()
        self.app._ai.responses.append(envelope('search_memory',('PASSIVE',),query='second'))
        self.finish()
        self.assertEqual(len(self.app._ai.calls),1)
        self.assertEqual(self.audio,[['PASSIVE']])
        self.assertEqual(self.app.jobs,[])
        self.record()

    def test_two_sequential_direct_requests_have_separate_context(self):
        for path in ('legacy','bounded'):
            self.case(path)
            self.app._ai.responses.extend([envelope('read_notepad',()),envelope(texts=('A',))])
            self.app._ai_tick('A',origin='user')
            self.finish()
            self.app._ai.responses.extend([envelope('search_web',(),query='second'),envelope(texts=('B',))])
            self.app._ai_tick('B',origin='user')
            self.finish()
            self.assertEqual(self.audio,[['A'],['B']])
            self.assertNotIn('ALPHA notes',self.app._ai.calls[-1]['turn'])
            self.record()

    def test_streaming_and_nonstreaming_followup_have_one_final(self):
        for path in ('legacy','bounded'):
            for streaming in (False,True):
                self.case(path,streaming)
                self.provider_ready()
                self.app._ai.responses.append(envelope(texts=('A','B')))
                self.finish()
                self.assertEqual(self.audio,[['A','B']])
                self.assertEqual(len(self.app._ai.calls),1)
                self.record('/stream='+str(streaming))

    def test_nonempty_status_waits_bounded_but_legacy_early_requery_is_lost(self):
        for path in ('legacy','bounded'):
            self.case(path)
            self.start(texts=('STATUS',))
            self.assertTrue(self.app._speech_active)
            self.assertEqual(self.audio,[['STATUS']])
            if path=='legacy':
                self.assertEqual(len(self.app.jobs),1)
                self.job()
                self.assertEqual(self.app._ai.calls,[])
                self.complete()
                self.assertEqual(self.app.jobs,[])
            else:
                self.assertEqual(self.app.jobs,[])
                self.complete()
                self.assertEqual(self.app.jobs[0][0],'continuation-tool')
                self.finish()
                self.assertEqual(len(self.app._ai.calls),1)
                self.assertEqual(len(self.audio),2)
            self.record()

    def test_repeated_and_many_segments_preserve_distinct_current_limits(self):
        for path in ('legacy','bounded'):
            self.case(path)
            self.provider_ready()
            self.app._ai.responses.append(envelope(texts=tuple(['A','B','A']+[str(i) for i in range(14)])))
            self.finish()
            self.assertEqual(len(self.audio[0]),16 if path=='bounded' else 17)
            self.assertEqual(self.audio[0][:3],['A','B','A'])
            self.record()

    def test_idle_with_segments_is_spoken_only_by_bounded(self):
        for path in ('legacy','bounded'):
            self.case(path)
            self.provider_ready()
            self.app._ai.responses.append(envelope('idle',('IDLE TEXT',)))
            self.finish()
            self.assertEqual(self.audio,[['IDLE TEXT']] if path=='bounded' else [])
            self.record()

    def test_provider_exception_and_timeout_release_both_but_only_bounded_has_terminal_session(self):
        for path in ('legacy','bounded'):
            for error in (RuntimeError('synthetic failure'),TimeoutError('synthetic timeout')):
                self.case(path)
                self.provider_ready()
                self.app._ai.responses.append(error)
                self.finish()
                self.assertFalse(self.app._ai_busy)
                self.assertIsNone(self.app._ai_operation_token)
                self.assertEqual(self.audio,[])
                if path=='bounded':
                    self.assertIsNone(self.app._continuation.active_snapshot())
                    self.assertEqual(self.app._continuation.last_snapshot().state,ContinuationState.STOPPED)
                self.record('/'+type(error).__name__)

    def test_context_failure_requeries_with_different_error_privacy(self):
        for path in ('legacy','bounded'):
            self.case(path)
            def fail():
                raise ValueError('SYNTHETIC private marker')
            self.app._continuation_tools=ReadOnlyToolExecutor(settings=self.fixture.settings,functions={'read_notepad':fail})
            with patch('agetha.ui.dashboard.read_notepad_text',side_effect=fail):
                self.provider_ready()
            self.finish()
            self.assertEqual(len(self.app._ai.calls),1)
            self.assertEqual('SYNTHETIC private marker' in self.app._ai.calls[0]['turn'],path=='legacy')
            self.record()

    def test_worker_refusal_has_no_stranded_owner_or_late_work(self):
        for path in ('legacy','bounded'):
            self.case(path)
            self.app.refuse_worker=True
            self.start()
            self.finish()
            self.assertEqual(self.app.jobs,[])
            self.assertEqual(self.app._ai.calls,[])
            self.assertFalse(self.app._ai_busy)
            if path=='bounded':
                self.assertIsNone(self.app._continuation.active_snapshot())
            self.record()

    def test_cancel_before_provider_drops_both_repaired_paths(self):
        for path in ('legacy','bounded'):
            self.case(path)
            self.provider_ready()
            self.app._on_cancel_ai()
            self.finish()
            self.assertEqual(self.app._ai.calls,[])
            self.assertEqual(self.audio,[])
            self.assertFalse(self.app._ai_busy)
            self.record()

    def test_cancel_during_provider_drops_both_repaired_paths(self):
        for path in ('legacy','bounded'):
            self.case(path)
            self.provider_ready()
            self.app._ai.hook=self.app._on_cancel_ai
            self.finish()
            self.assertEqual(self.audio,[])
            self.assertFalse(self.app._ai_busy)
            self.record()

    def test_newer_direct_input_during_provider_prevents_old_speech(self):
        for path in ('legacy','bounded'):
            self.case(path)
            self.provider_ready()
            def newer():
                self.app._ai.hook=lambda:None
                self.app._on_user_input()
            self.app._ai.hook=newer
            self.app._ai.responses.extend([envelope(texts=('OLD A',)),envelope(texts=('NEW B',))])
            self.finish()
            self.assertEqual(self.audio,[['NEW B']])
            self.record()

    def test_queued_final_speech_and_ui_expire_on_new_generation(self):
        for path in ('legacy','bounded'):
            self.case(path)
            self.provider_ready()
            self.app._ai.responses.append(envelope())
            self.job()
            self.app._invalidate_request_context()
            self.app._invalidate_continuation_ui_delivery()
            if self.app._continuation:
                self.app._continuation.start('B',authority_origin='user')
            self.ui()
            self.assertEqual(self.audio,[])
            self.assertEqual(self.subtitles,[])
            self.record()

    def test_shutdown_during_provider_suppresses_final_and_releases(self):
        for path in ('legacy','bounded'):
            self.case(path)
            self.provider_ready()
            self.app._ai.hook=self.app._graceful_shutdown
            self.finish()
            self.assertEqual(self.audio,[])
            self.assertFalse(self.app._ai_busy)
            self.record()

    def test_compact_during_provider_expires_both_Notepad_deliveries(self):
        for path in ('legacy','bounded'):
            self.case(path)
            self.provider_ready()
            self.app._ai.hook=self.fixture.transition
            self.app._ai.responses.append(envelope())
            self.finish()
            self.assertEqual(self.audio,[])
            if path=='bounded':
                self.assertEqual(self.app._continuation.active_snapshot().state,ContinuationState.AWAITING_MODEL)
            self.record()

    def test_compact_after_final_queued_drops_both(self):
        for path in ('legacy','bounded'):
            self.case(path)
            self.provider_ready()
            self.job()
            # Bounded decision first reaches Tk and queues speech; legacy already queued it.
            if path=='bounded':
                while self.app.ui and not self.app._speech_active:
                    self.app.ui.pop(0)()
            self.fixture.transition()
            self.ui()
            self.assertEqual(self.audio,[])
            self.record()

    def test_compact_invalidated_status_can_leave_bounded_awaiting_status(self):
        self.case('bounded')
        self.start(texts=('STATUS',))
        self.fixture.transition()
        self.complete()
        self.assertEqual(self.app.jobs,[])
        self.assertEqual(self.app._continuation.active_snapshot().state,ContinuationState.AWAITING_STATUS)
        self.record()

    def test_memory_legacy_late_result_cannot_adopt_successor_epoch_after_cancel_clears(self):
        for path in ('legacy','bounded'):
            self.case(path)
            self.provider_ready('search_memory')
            self.app._ai.responses.append(envelope())
            def new_generation():
                self.app._invalidate_request_context()
                self.app._cancel_event.clear()  # After B admission; no provider overlap introduced.
                if self.app._continuation:
                    self.app._continuation.start('B',authority_origin='user')
            self.app._ai.hook=new_generation
            self.finish()
            self.assertEqual(self.audio,[])
            self.record()

    def test_deadline_begins_at_admission_and_counts_initial_provider(self):
        self.case('bounded')
        self.fixture.now=10
        self.app._ai.hook=lambda:setattr(self.fixture,'now',30)
        self.app._ai.responses.append(envelope('read_notepad',()))
        self.app._ai_tick('A',origin='user')
        snap=self.app._continuation.active_snapshot()
        self.assertEqual((snap.started_at_monotonic,snap.deadline_monotonic),(10,130))
        self.assertEqual(snap.state,ContinuationState.AWAITING_TOOL)
        self.record()

    def test_exact_deadline_validation_stops_but_is_current_alone_is_lazy(self):
        for at in (119.999,120.0,120.001):
            self.case('bounded')
            engine=self.app._continuation
            start=engine.start('A',authority_origin='user')
            self.fixture.now=at
            self.assertEqual(engine.is_current(start.session_id,start.generation),at<120)
            self.assertIsNotNone(engine.active_snapshot())
            d=engine.accept_initial_model_response(start.session_id,start.generation,envelope())
            self.assertEqual(d.kind,DecisionKind.FINAL if at<120 else DecisionKind.STOPPED)
            self.record('/at='+str(at))

    def test_status_and_tool_wait_do_not_extend_deadline(self):
        self.case('bounded')
        self.start(texts=('STATUS',))
        self.fixture.now=119
        self.complete()
        self.assertEqual(self.app._continuation.active_snapshot().deadline_monotonic,120)
        self.fixture.now=120
        self.job()
        self.ui()
        self.assertEqual(self.app._ai.calls,[])
        self.assertEqual(self.app._continuation.active_snapshot().state,ContinuationState.AWAITING_TOOL)
        self.record()

    def test_status_completion_at_exact_deadline_terminates(self):
        self.case('bounded')
        self.start(texts=('STATUS',))
        self.fixture.now=120
        self.complete()
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertEqual(self.app._continuation.last_snapshot().state,ContinuationState.STOPPED)
        self.record()

    def test_late_provider_deadline_result_drops_bounded_but_legacy_has_no_goal_deadline(self):
        for path in ('legacy','bounded'):
            self.case(path)
            self.provider_ready()
            self.app._ai.hook=lambda:setattr(self.fixture,'now',120)
            self.app._ai.responses.append(envelope())
            self.finish()
            self.assertEqual(self.audio,[['FINAL A']] if path=='legacy' else [])
            self.assertFalse(self.app._ai_busy)
            if path=='bounded':
                self.assertEqual(self.app._continuation.active_snapshot().state,ContinuationState.AWAITING_MODEL)
            self.record()

    def test_cancel_retains_recorded_absolute_deadline(self):
        self.case('bounded')
        self.start()
        self.fixture.now=20
        self.app._on_cancel_ai()
        snap=self.app._continuation.last_snapshot()
        self.assertEqual(snap.deadline_monotonic,120)
        self.assertEqual(snap.state,ContinuationState.CANCELLED)
        self.record()

    def test_successful_full_commit_during_provider_keeps_basic_final_in_both(self):
        for path in ('legacy','bounded'):
            self.case(path)
            compact=AppSettings({**self.fixture.settings.raw,'COMPACT_MODE':'yes'})
            full=AppSettings({**self.fixture.settings.raw,'COMPACT_MODE':'no'})
            self.app._capabilities=CapabilityController(CapabilityPolicy.from_settings(compact))
            self.app._capability_consent=CapabilityConsentFlow()
            first=self.app._capability_consent.begin_enable()
            self.app._capability_consent.confirm_first(first.generation)
            final=self.app._capability_consent.finish_demo(first.generation)
            self.app._capability_transition_generation=self.app._capabilities.begin_full_transition()
            before=self.app._continuation_ui_epoch
            self.provider_ready()
            def commit():
                with patch('agetha.app_config.patch_config_key',return_value=True), \
                     patch('agetha.app_config.clear_compact_mode_fail_closed',return_value=True), \
                     patch.object(main,'get_settings',return_value=full), \
                     patch.object(self.app,'_start_full_mode_services'), \
                     patch.object(self.app,'_refresh_dashboard_after_profile_commit'):
                    self.app._on_final_full_mode_decision(final.generation,True)
            self.app._ai.hook=commit
            self.app._ai.responses.append(envelope())
            self.finish()
            self.assertEqual(self.app._capabilities.snapshot().profile.value,'full')
            self.assertEqual(self.app._continuation_ui_epoch,before)
            self.assertEqual(self.audio,[['FINAL A']])
            self.record()

    def test_true_disabled_setting_retains_web_notes_memory_legacy_fallback(self):
        for feature in ('search_web','read_notepad','search_memory'):
            self.case('legacy')
            disabled=AppSettings({**self.fixture.settings.raw,'ENABLE_AGENT_CONTINUATION':'no'})
            for module in (main,web_context,memory_presentation,command_handlers):
                self.fixture.enterContext(patch.object(module,'get_settings',return_value=disabled))
            self.fixture.enterContext(patch.object(main,'_SETTINGS',disabled))
            self.app._ai._app_settings=disabled
            self.app._capabilities=CapabilityController(CapabilityPolicy.from_settings(disabled))
            self.app._ai.responses.extend([envelope(feature,(),query='synthetic'),envelope()])
            self.app._ai_tick('A',origin='user')
            self.finish()
            self.assertEqual(len(self.app._ai.calls),2)
            self.assertEqual(self.audio,[['FINAL A']])
            self.record('/disabled/'+feature)

    def test_real_malformed_followup_is_idle_and_bounded_completes(self):
        for path in ('legacy','bounded'):
            self.case(path)
            self.provider_ready()
            engine=RealQueryFakeAI(self.fixture.settings,['{bad'])
            self.app._ai=engine
            with patch('agetha.core.ai_engine._MEMORY_SYSTEM_AVAILABLE',False):
                self.finish()
            self.assertEqual(len(engine._client.calls),1)
            self.assertEqual(self.audio,[])
            self.assertFalse(self.app._ai_busy)
            if path=='bounded':
                self.assertEqual(self.app._continuation.last_snapshot().state,ContinuationState.COMPLETED)
            self.record('/malformed')

    def test_final_accepted_before_deadline_can_deliver_after_deadline(self):
        self.case('bounded')
        self.provider_ready()
        self.fixture.now=119
        self.job()
        self.assertEqual(self.app._continuation.last_snapshot().state,ContinuationState.COMPLETED)
        self.fixture.now=121
        self.finish()
        self.assertEqual(self.audio,[['answer']])
        self.record()

    def test_status_voice_policy_and_final_mood_differ(self):
        for path in ('legacy','bounded'):
            self.case(path)
            self.app._presence_decision=lambda:SimpleNamespace(allow_voice=False)
            self.start(texts=('STATUS',))
            self.assertEqual(self.subtitles,[['STATUS']])
            self.assertEqual(self.audio,[['STATUS']] if path=='legacy' else [])
            self.complete()
            self.app._ai.responses.append(envelope())
            self.finish()
            self.assertEqual(self.audio[-1],['FINAL A'])
            self.assertEqual([e for e in self.events if e[0]=='audio_enqueue'][-1][2],
                'happy' if path=='legacy' else 'neutral')
            self.record()

    def test_stream_preview_needs_live_provider_reservation(self):
        for path in ('legacy','bounded'):
            self.case(path,streaming=True)
            self.provider_ready()
            query=self.app._ai.query
            def stream(on_token=None,**kwargs):
                on_token('LIVE PREFIX')
                self.ui()
                result=query(**kwargs)
                on_token('HELD PREFIX')
                return result
            self.app._ai.query_streaming=stream
            self.finish()
            self.assertIn('LIVE PREFIX',self.app.notices)
            self.assertNotIn('HELD PREFIX',self.app.notices)
            self.assertEqual(self.audio,[['answer']])
            self.record()

    def test_empty_speech_helper_retained_completion_has_no_producer_guard(self):
        self.case('legacy')
        valid=[True]
        done=[]
        self.app._speak_and_continue([], 'neutral', on_done=lambda:done.append('done'),
            result_is_current=lambda:valid[0])
        valid[0]=False
        self.ui()
        self.assertEqual(done,['done'])
        self.record()


class SubtitleBoundaryComparison(unittest.TestCase):
    def renderer(self, mode='both'):
        self.jobs,self.draws,self.tts,self.bleeps=[],[],[],[]
        bleep=SimpleNamespace(start_talking=lambda **kw:self.bleeps.append('start'),
            stop=lambda:None,pause=lambda:None,resume=lambda:None)
        voice=VoiceOutputCoordinator.__new__(VoiceOutputCoordinator)
        voice._pause_lock=threading.RLock()
        voice._pause_is_current=None
        voice._mode=mode
        voice._settings=SimpleNamespace(voice_tts_engine='synthetic')
        voice._bleep=bleep
        voice._tts=SimpleNamespace(package_ok=True,
            speak_segments=lambda segs,**kw:self.tts.extend(s['text'] for s in segs),
            speak_text=lambda text,**kw:self.tts.append(text),pause=lambda:None,resume=lambda:None)
        renderer=main.SubtitleRenderer.__new__(main.SubtitleRenderer)
        renderer._stop_event=threading.Event()
        renderer._thread=None
        renderer._voice_out=voice
        renderer._bleep=bleep
        renderer._canvas=SimpleNamespace(after=lambda delay,cb:self.jobs.append((delay,cb)))
        renderer._draw_pending=False
        renderer._pending_text=''
        renderer._pending_color=''
        renderer._draw=lambda text,color:self.draws.append(text)
        renderer.clear=lambda:self.draws.append('CLEAR')
        return renderer,voice

    def test_current_tts_only_batch_plus_segments_duplicates_enqueue_in_both_paths(self):
        segments=[{'text':'A','pause':0},{'text':'B','pause':0}]
        for mode,expected in [('bleeps_only',[]),('both',['A','B']),('tts_only',['A','B','A','B'])]:
            with self.subTest(mode=mode):
                renderer,voice=self.renderer(mode)
                voice.start_speech(segments,'happy',result_is_current=lambda:True)
                with patch('main.time.sleep',return_value=None):
                    renderer._run(segments,lambda:None,result_is_current=lambda:True)
                self.assertEqual(self.tts,expected)

    def test_retained_draw_has_no_request_guard_although_initial_clear_does(self):
        renderer,_=self.renderer()
        valid=[True]
        done=[]
        with patch('main.time.sleep',return_value=None):
            renderer._run([{'text':'OLD','pause':0}],lambda:done.append('done'),result_is_current=lambda:valid[0])
        valid[0]=False
        for _,callback in self.jobs:
            callback()
        self.assertEqual(self.draws,['OLD'])
        self.assertEqual(done,['done'])  # App wrapper separately guards user-facing completion.

    def test_subtitle_worker_schedules_tk_directly_without_app_queue(self):
        renderer,_=self.renderer()
        observed=[]
        renderer._canvas.after=lambda delay,cb:observed.append(threading.current_thread())
        worker=object()
        with patch('main.threading.current_thread',return_value=worker),patch('main.time.sleep',return_value=None):
            renderer._run([{'text':'A','pause':0}],lambda:None,result_is_current=lambda:True)
        self.assertEqual(observed,[worker,worker,worker])

    def test_old_static_message_clear_can_clear_newer_subtitle(self):
        renderer,_=self.renderer()
        renderer.show_message('OLD')
        old=list(self.jobs)
        renderer.show_message('NEW')
        self.jobs[2][1]()
        old[1][1]()
        self.assertEqual(self.draws,['NEW','CLEAR'])

    def test_held_draw_coalesces_cumulative_segments(self):
        renderer,_=self.renderer()
        with patch('main.time.sleep',return_value=None):
            renderer._run([{'text':'A','pause':0},{'text':'B','pause':0}],lambda:None,
                result_is_current=lambda:True)
        for _,callback in self.jobs:
            callback()
        self.assertEqual(self.draws,['CLEAR','A B'])
        self.assertEqual(self.tts,['A','B'])

    def test_cancel_during_pause_prevents_resume_and_next_segment(self):
        renderer,voice=self.renderer()
        valid=[True]
        resumed=[]
        voice.resume=lambda:resumed.append('resume')
        def advance(delay):
            if delay==0.7:
                valid[0]=False
        with patch('main.time.sleep',side_effect=advance):
            renderer._run([{'text':'A','pause':0.7},{'text':'B','pause':0}],lambda:None,
                result_is_current=lambda:valid[0])
        self.assertEqual(self.tts,['A'])
        self.assertEqual(resumed,[])


class SequenceClient:
    def __init__(self, responses):
        self.responses=deque(responses)
        self.calls=[]
        self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        value=self.responses.popleft()
        if kwargs.get('stream'):
            chunks=value if isinstance(value,list) else [value]
            return iter(SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=chunk))],usage=None)
                for chunk in chunks)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=value))],usage=None)


class RealQueryFakeAI(fixtures.FakeAI):
    query=AIEngine.query
    query_streaming=AIEngine.query_streaming

    def __init__(self, settings, responses):
        super().__init__(settings)
        self._client=SequenceClient(responses)
        self._show_error_gif=False
        self._use_local_ai=self._use_openrouter=self._use_gemini=False
        self._enable_groq=True
        self._groq_model='openai/gpt-oss-120b'
        self._command_execution_enabled=True
        self._update_user_activity=self._track_tokens=lambda _:None
        self._save_memory=lambda *_a,**_kw: (_ for _ in ()).throw(AssertionError('Unexpected memory write'))
        self._history=[{'user':'PRIOR USER','assistant':'PRIOR ANSWER'}]


class ProviderRequestComparison(unittest.TestCase):
    observations={}

    def engine(self, responses):
        settings=AppSettings({'AI_MAX_TOKENS':'600','HISTORY_LIMIT':'3','ENABLE_COMMAND_EXECUTION':'yes',
            'ENABLE_DATETIME_CONTEXT':'no','ENABLE_LONGTERM_MEMORY':'no',
            'ENABLE_EMOTION_ENGINE':'no','ENABLE_DREAMS':'no','ENABLE_TASKS':'no',
            'ENABLE_CIRCADIAN_RHYTHM':'no','ENABLE_STATUS_PROVIDERS':'no',
            'ENABLE_COMPANION_STATS_CONTEXT':'no','ENABLE_SESSION_RECAP':'no'})
        self.enterContext(patch('agetha.core.ai_engine._MEMORY_SYSTEM_AVAILABLE',False))
        return RealQueryFakeAI(settings,responses)

    def request(self, engine, profile, *, stream=False, origin='tool_result', **kwargs):
        args=dict(user_message='A',screen_context='SCREEN',doc_content='ALPHA',
            request_profile=profile,request_origin=origin)
        args.update(kwargs)
        return engine.query_streaming(**args) if stream else engine.query(**args)

    def test_actual_provider_messages_history_schema_and_budget_differ(self):
        for profile in ('fast_tool_result','tool_continuation'):
            engine=self.engine([json.dumps(envelope())])
            result=self.request(engine,profile)
            call=engine._client.calls[0]
            joined=json.dumps(call['messages'])
            self.assertEqual('PRIOR USER' in joined,profile=='fast_tool_result')
            self.assertIn('ALPHA',joined)
            self.assertEqual(call['max_tokens'],600 if profile=='fast_tool_result' else 480)
            self.assertEqual(call['timeout'],30)
            self.assertEqual(call['response_format'],{'type':'json_object'})
            self.assertEqual(call['reasoning_effort'],'medium')
            self.assertEqual(result['command'],'speak')
            self.assertEqual(engine.recorded,[])
            self.observations[profile]=call

    def test_streaming_followups_emit_raw_prefixes_direct_only_emits_validated_whole(self):
        raw=json.dumps(envelope(texts=('A','B')))
        chunks=[raw[:25],raw[25:50],raw[50:]]
        for profile,origin in [('fast_tool_result','tool_result'),('tool_continuation','tool_result'),('normal','user')]:
            engine=self.engine([chunks])
            tokens=[]
            result=self.request(engine,profile,stream=True,origin=origin,on_token=tokens.append,
                doc_content='' if origin=='user' else 'ALPHA')
            self.assertEqual(result['segments'][0]['text'],'A')
            self.assertEqual(tokens,[raw] if origin=='user' else [chunks[0],''.join(chunks[:2]),raw])
            self.assertEqual(len(engine._client.calls),1)
            self.observations[profile+'/stream']=dict(call=engine._client.calls[0],tokens=tokens)

    def test_malformed_followups_have_no_format_retry_but_direct_user_has_one(self):
        for profile in ('fast_tool_result','tool_continuation'):
            engine=self.engine(['{bad'])
            result=self.request(engine,profile)
            self.assertEqual(len(engine._client.calls),1)
            self.assertEqual(result['command'],'idle')
        engine=self.engine(['{bad',json.dumps(envelope())])
        result=self.request(engine,'normal',origin='user',doc_content='')
        self.assertEqual(result['command'],'speak')
        self.assertEqual(len(engine._client.calls),2)
        self.assertEqual(len(engine.recorded),1)

    def test_parser_requires_one_envelope_and_preserves_17_segments_before_engine_limit(self):
        for profile in ('fast_tool_result','tool_continuation'):
            engine=self.engine([json.dumps(envelope(texts=tuple(str(i) for i in range(17))))])
            result=self.request(engine,profile)
            self.assertEqual(len(result['segments']),17)
            continuation=ContinuationEngine(clock=lambda:0)
            start=continuation.start('A',authority_origin='user')
            decision=continuation.accept_initial_model_response(start.session_id,start.generation,result)
            self.assertEqual(len(decision.messages),16)
            bad=self.engine([json.dumps([envelope(),envelope()])])
            result=self.request(bad,profile)
            self.assertEqual(result['command'],'idle')
            self.assertEqual(result['segments'],[])

    @classmethod
    def tearDownClass(cls):
        root=Path(__file__).resolve().parents[1]
        if not (root/'isolated-temp').is_dir():
            return
        (root/'isolated-temp'/'provider-comparison.json').write_text(
            json.dumps(cls.observations,indent=2,default=str),encoding='utf-8')


if __name__=='__main__':
    unittest.main()
