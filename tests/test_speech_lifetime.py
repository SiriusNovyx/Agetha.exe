"""F11 speech ownership regressions. Run only in a disposable, guarded copy.

Real CompanionApp/continuation/handler flows; fake providers, UI, and audio.
No real configuration, user data, native TTS, or microphone access.
"""
from __future__ import annotations

from types import SimpleNamespace
import inspect
import queue
import threading
import unittest
from unittest.mock import patch

from tests import test_followup_characterization as fixtures
from agetha.commands.handlers import web_context
from agetha.commands.handlers.support import DispatchCtx
from agetha.features import tts_player


def response(text):
    return {'command': 'speak', 'mood': 'neutral', 'segments': [{'text': text, 'pause': 0.0}], 'shutdown': False}


def guarded_call(method, *args, result_is_current, **kwargs):
    """Exercise original APIs for RED; new optional ownership args for GREEN."""
    if 'result_is_current' in inspect.signature(method).parameters:
        kwargs['result_is_current'] = result_is_current
    return method(*args, **kwargs)


class HeldThread:
    def __init__(self, target, args=(), **kwargs):
        self.callback = lambda: target(*args)
    def start(self):
        pass
    def is_alive(self):
        return False


class SpeechLifetime(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FollowupCharacterization(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app = self.fixture.app
        self.audio = []
        self.stops = []
        self.app._voice_out = SimpleNamespace(
            start_speech=lambda segments, mood, **kw: self.audio.append([s['text'] for s in segments]),
            stop=lambda: self.stops.append('stop'),
        )

    def flush(self):
        self.fixture.flush()

    def legacy(self, kind='notepad', text='A'):
        if kind == 'notepad':
            self.fixture.legacy()
        else:
            with patch('agetha.features.web_rag.format_search_results_for_prompt', return_value='synthetic A web'):
                web_context.handle_search_web(self.app, {'query': 'A'}, DispatchCtx('A', 'neutral', [], False))
        self.app._ai.responses.append(response(text))
        self.fixture.job('notepad-requery' if kind == 'notepad' else 'web-search-requery')

    def bounded(self, text='A'):
        self.fixture.to_provider()
        self.app._ai.responses.append(response(text))
        self.fixture.job('continuation-provider')

    def newer_input(self):
        self.app._input_box.config(state='normal')
        self.app._on_user_input()

    def play_current_b(self):
        self.app._ai.hook = lambda: None
        self.app._ai.responses.append(response('B'))
        self.fixture.job('user-ai')
        if any(name == 'queued-ai' for name, _ in self.app.jobs):
            self.fixture.job('queued-ai')
        self.flush()

    def test_current_legacy_final_plays_exactly_once(self):
        self.legacy()
        self.flush()
        self.assertEqual(self.audio, [['A']])
        self.assertEqual(len(self.app.spoken), 1)

    def test_current_bounded_final_plays_exactly_once(self):
        self.bounded()
        self.flush()
        self.assertEqual(self.audio, [['A']])
        self.assertEqual(len(self.app.spoken), 1)

    def test_legacy_provider_result_after_new_user_cannot_speak(self):
        self.fixture.legacy()
        self.app._ai.responses.append(response('A'))
        def admit_b():
            self.newer_input()
            self.fixture.job('user-ai')
        self.app._ai.hook = admit_b
        self.fixture.job('notepad-requery')
        self.flush()
        self.assertEqual(self.audio, [], 'A was invalidated during provider work')
        self.app._ai.hook = lambda: None
        self.app._ai.responses.append(response('B'))
        self.fixture.job('queued-ai')
        self.flush()
        self.assertEqual(self.audio, [['B']])

    def test_bounded_provider_result_after_new_user_cannot_speak(self):
        self.fixture.to_provider()
        self.app._ai.responses.append(response('A'))
        def admit_b():
            self.newer_input()
            self.fixture.job('user-ai')
        self.app._ai.hook = admit_b
        self.fixture.job('continuation-provider')
        self.flush()
        self.assertEqual(self.audio, [])
        self.app._ai.hook = lambda: None
        self.app._ai.responses.append(response('B'))
        self.fixture.job('queued-ai')
        self.flush()
        self.assertEqual(self.audio, [['B']])

    def test_queued_bounded_final_rechecks_session_at_speech_delivery(self):
        self.bounded()
        self.app._continuation.start('B', authority_origin='user')
        self.flush()
        self.assertEqual(self.audio, [])
        self.assertEqual(self.app.spoken, [])

    def test_queued_legacy_web_final_expires_before_ui_delivery(self):
        self.legacy('web')
        self.newer_input()
        self.flush()
        self.assertEqual(self.audio, [])
        self.play_current_b()
        self.assertEqual(self.audio, [['B']])

    def test_queued_legacy_final_expires_with_engine_disabled(self):
        self.app._continuation = None
        self.legacy()
        self.newer_input()
        self.flush()
        self.assertEqual(self.audio, [])
        self.play_current_b()
        self.assertEqual(self.audio, [['B']])

    def test_escape_prevents_pending_final_and_reset_does_not_revive_it(self):
        self.bounded()
        self.app._on_cancel_ai()
        self.app._cancel_event.clear()
        self.flush()
        self.assertEqual(self.audio, [])

    def test_multiple_current_callbacks_keep_existing_order(self):
        self.app._speak_and_continue(response('ONE')['segments'], 'neutral')
        self.app._speak_and_continue(response('TWO')['segments'], 'neutral')
        self.flush()
        self.assertEqual(self.audio, [['ONE'], ['TWO']])

    def test_started_speech_is_not_undone_but_next_old_callback_is_suppressed(self):
        self.app._speak_and_continue(response('A1')['segments'], 'neutral')
        self.app._speak_and_continue(response('A2')['segments'], 'neutral')
        self.app.ui.pop(0)()
        self.assertEqual(self.audio, [['A1']])
        self.newer_input()
        self.flush()
        self.assertEqual(self.audio, [['A1']])
        self.assertEqual(self.stops, [], 'No retroactive stop of speech that already began')

    def test_shutdown_blocks_even_a_callback_retained_outside_ui_queue(self):
        self.bounded()
        callbacks = self.app.ui[:]
        self.app.ui.clear()
        self.app._graceful_shutdown()
        for callback in callbacks:
            callback()
        self.assertEqual(self.audio, [])
        self.assertEqual(self.app.spoken, [])

    def test_profile_ui_invalidation_suppresses_already_queued_final(self):
        self.bounded()
        self.fixture.transition()
        self.flush()
        self.assertEqual(self.audio, [])

    def test_failed_callback_cannot_be_redelivered_after_invalidation(self):
        self.app._speak_and_continue(response('A')['segments'], 'neutral')
        callback = self.app.ui.pop()
        with patch.object(self.app, '_set_state', side_effect=RuntimeError('synthetic UI boundary')):
            try:
                callback()
            except RuntimeError:
                pass
        self.newer_input()
        callback()
        self.flush()
        self.assertEqual(self.audio, [])

    def test_invalid_old_callback_does_not_suppress_new_current_speech(self):
        self.app._speak_and_continue(response('A')['segments'], 'neutral')
        old = self.app.ui.pop()
        self.newer_input()
        self.app._speak_and_continue(response('B')['segments'], 'neutral')
        self.flush()
        old()
        self.assertEqual(self.audio, [['B']])

    def test_repeated_lifecycles_do_not_reuse_old_speech_validity(self):
        callbacks = []
        for text in ('A', 'B'):
            self.app._speak_and_continue(response(text)['segments'], 'neutral')
            callbacks.append(self.app.ui.pop())
            self.app._on_cancel_ai()
            self.app._cancel_event.clear()
        self.app._speak_and_continue(response('C')['segments'], 'neutral')
        self.flush()
        for callback in callbacks:
            callback()
        self.assertEqual(self.audio, [['C']])

    def test_provider_barrier_holds_a_until_b_invalidates_then_releases(self):
        entered, resume = threading.Event(), threading.Event()
        errors = []
        self.fixture.legacy()
        self.app._ai.responses.append(response('A'))
        def provider_barrier():
            entered.set()
            if not resume.wait(3):
                raise AssertionError('provider barrier not released')
        self.app._ai.hook = provider_barrier
        def deliver_a():
            try:
                self.fixture.job('notepad-requery')
            except BaseException as exc:
                errors.append(exc)
        worker = threading.Thread(target=deliver_a)
        worker.start()
        try:
            self.assertTrue(entered.wait(3))
            self.newer_input()
            self.fixture.job('user-ai')
        finally:
            resume.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.flush()
        self.assertEqual(self.audio, [])
        self.app._ai.hook = lambda: None
        self.app._ai.responses.append(response('B'))
        self.fixture.job('queued-ai')
        self.flush()
        self.assertEqual(self.audio, [['B']])

    def test_old_completion_cannot_release_current_b_speech_activity(self):
        self.app._speak_and_continue(response('A')['segments'], 'neutral')
        self.flush()
        old_done = self.app.spoken[-1][1]['on_done']
        self.newer_input()
        self.app._cancel_event.clear()
        self.app._speak_and_continue(response('B')['segments'], 'neutral')
        self.flush()
        call_count = len(self.app._ai.calls)
        old_done()
        self.app._ai_tick('C', origin='user')
        self.assertEqual(len(self.app._ai.calls), call_count, 'B still owns the current speech slot')

    def test_stale_admission_cannot_replace_current_b_speech_activity(self):
        self.app._speak_and_continue(response('B')['segments'], 'neutral')
        self.flush()
        calls = len(self.app._ai.calls)
        guarded_call(self.app._speak_and_continue, response('A')['segments'], 'neutral',
                     result_is_current=lambda: False)
        self.flush()
        self.app._ai_tick('C', origin='user')
        self.assertEqual(len(self.app._ai.calls), calls, 'Invalid A cannot claim or release B activity')
        self.assertEqual(self.audio, [['B']])

    def test_invalidated_reservation_cannot_overwrite_pending_b(self):
        token = self.app._reserve_ai_operation(direct=False, user_message=None, origin='ambient')
        old_epoch = int(getattr(self.app, '_context_request_epoch', 0))
        self.newer_input()
        self.fixture.job('user-ai')  # B is now pending behind the existing provider.
        self.app._reserve_ai_operation(direct=True, user_message='A', origin='user',
                                       request_context_epoch=old_epoch)
        self.app._release_ai_operation(token)
        self.app._drain_pending_user_message()
        self.app._ai.responses.append(response('B'))
        self.fixture.job('queued-ai')
        self.flush()
        self.assertEqual(self.audio, [['B']])

    def short_speech(self):
        self.app._try_short_mood_speak = fixtures.main.CompanionApp._try_short_mood_speak.__get__(self.app)
        static = self.app.EXTRA_STATIC_GIFS['happy']
        self.app._gif_cache = {static: object()}
        self.app._play_gif = lambda name: None
        self.app.root.after = lambda delay, callback: self.app.ui.append(callback)
        value = response('SHORT A')
        value['mood'] = 'happy'
        self.app._dispatch_response(value, 'A', origin='tool_result')

    def test_current_short_final_still_speaks(self):
        self.short_speech()
        self.flush()
        self.assertEqual(self.audio, [['SHORT A']])

    def test_short_final_cannot_bypass_pending_speech_invalidation(self):
        self.short_speech()
        self.newer_input()
        self.flush()
        self.assertEqual(self.audio, [])

    def test_accepted_touch_invalidates_queued_older_final(self):
        self.app._speak_and_continue(response('A')['segments'], 'neutral')
        self.app._last_touch_time = 0.0
        with patch.object(fixtures.main.time, 'time', return_value=100.0):
            self.app._on_gif_click()
        self.flush()
        self.assertEqual(self.audio, [])
        self.assertEqual([name for name, _ in self.app.jobs], ['touch-ai'])

    def test_completed_bounded_result_cannot_adopt_new_input_lifetime(self):
        self.fixture.to_provider()
        self.app._ai.responses.append(response('A'))
        accept = self.app._accept_continuation_response
        def after_completion(*args, **kwargs):
            result = accept(*args, **kwargs)
            self.newer_input()  # A is already terminal; B's provider is held.
            return result
        with patch.object(self.app, '_accept_continuation_response', side_effect=after_completion):
            self.fixture.job('continuation-provider')
        self.flush()
        self.assertEqual(self.audio, [])
        self.play_current_b()
        self.assertEqual(self.audio, [['B']])

    def test_initial_bounded_final_cannot_adopt_new_input_lifetime(self):
        self.app._ai.responses.append(response('A'))
        accept = self.app._accept_continuation_response
        def after_completion(*args, **kwargs):
            result = accept(*args, **kwargs)
            self.newer_input()
            return result
        with patch.object(self.app, '_accept_continuation_response', side_effect=after_completion):
            self.app._ai_tick('A', origin='user')
        self.flush()
        self.assertEqual(self.audio, [])

    def test_accepted_file_drop_invalidates_queued_older_final(self):
        self.app._speak_and_continue(response('A')['segments'], 'neutral')
        prepared = SimpleNamespace(accepted=True, filename='synthetic.txt', reason='',
                                   local_path=None, provider_context=SimpleNamespace(allowed=True, text='B'))
        with patch.object(fixtures.main, 'prepare_file_drop', return_value=prepared):
            self.app._on_file_drop(SimpleNamespace(data='synthetic'))
        self.flush()
        self.assertEqual(self.audio, [])
        self.assertEqual([name for name, _ in self.app.jobs], ['file-drop-ai'])

    def test_delayed_bounded_provider_cannot_adopt_touch_or_drop_lifetime(self):
        for kind in ('touch', 'file_drop'):
            with self.subTest(kind=kind):
                self.fixture.to_provider()
                self.app._last_touch_time = 0.0
                self.app._input_box.config(state='normal')
                if kind == 'touch':
                    with patch.object(fixtures.main.time, 'time', return_value=100.0):
                        self.app._on_gif_click()
                else:
                    prepared = SimpleNamespace(accepted=True, filename='synthetic.txt', reason='',
                                               local_path=None, provider_context=SimpleNamespace(allowed=True, text='B'))
                    with patch.object(fixtures.main, 'prepare_file_drop', return_value=prepared):
                        self.app._on_file_drop(SimpleNamespace(data='synthetic'))
                self.app._ai.responses.append(response('A'))
                self.fixture.job('continuation-provider')
                self.flush()
                self.assertEqual(self.audio, [])
                self.app.jobs.clear()
                self.app._ai.responses.clear()

    def test_delayed_accepted_user_worker_cannot_speak_after_newer_user(self):
        self.app._input_var.get = lambda: 'A'
        self.app._on_user_input()
        old_worker = self.app.jobs.pop()[1]
        self.app._input_var.get = lambda: 'B'
        self.newer_input()
        self.app._ai.responses.append(response('B'))
        self.fixture.job('user-ai')
        self.flush()
        old_worker()
        self.app.spoken[-1][1]['on_done']()
        if any(name == 'queued-ai' for name, _ in self.app.jobs):
            self.app._ai.responses.append(response('A'))
            self.fixture.job('queued-ai')
        self.flush()
        self.assertEqual(self.audio, [['B']])

    def test_old_delayed_completion_does_not_idle_or_shutdown_new_speech(self):
        shutdowns = []
        self.app._shutdown = lambda: shutdowns.append('shutdown')
        self.app.root.after = lambda delay, callback: self.app.ui.append(callback)
        self.app._speak_and_continue(response('A')['segments'], 'neutral', True)
        self.flush()
        self.app.spoken[-1][1]['on_done']()
        retained = self.app.ui[:]
        self.app.ui.clear()
        self.newer_input()
        self.app._cancel_event.clear()
        self.app._speak_and_continue(response('B')['segments'], 'neutral')
        self.flush()
        for callback in retained:
            callback()
        self.assertEqual(self.app._state, self.app.STATE_TALKING)
        self.assertEqual(shutdowns, [])

    def test_short_delayed_gif_cue_expires_after_speech_entry(self):
        self.short_speech()
        cues = []
        self.app._play_gif = cues.append
        self.app.ui.pop(0)()  # queues the original 12 ms cue
        self.app.ui.pop(0)()  # valid audio enters before invalidation
        self.newer_input()
        self.flush()
        self.assertEqual(self.audio, [['SHORT A']])
        self.assertEqual(cues, [])


class QueuedPlaybackLifetime(unittest.TestCase):
    def setUp(self):
        self.valid = True
        self.audio = []
        self.player = tts_player.TTSPlayer.__new__(tts_player.TTSPlayer)
        p = self.player
        p._package_ok = p._engine_ready = True
        p._engine_name = 'pyttsx3'
        p._volume = 0.5
        p._queue = queue.Queue()
        p._shutdown, p._paused = threading.Event(), threading.Event()
        p._init_engine = lambda: None
        self.started_callback = lambda **kw: None
        self.disconnected = []
        def connect(topic, callback):
            self.started_callback = callback
            return object()
        self.connect = connect
        p._engine = SimpleNamespace(
            say=lambda text: setattr(self, 'pending_text', text),
            runAndWait=lambda: self.audio.append(self.pending_text),
            connect=self.connect,
            disconnect=lambda token: self.disconnected.append(token),
        )
        self.pygame = SimpleNamespace(mixer=SimpleNamespace(
            get_init=lambda: True,
            Sound=lambda path: SimpleNamespace(play=self.play_sound),
        ))
        self.enterContext(patch.dict('sys.modules', pygame=self.pygame))

    def current(self):
        return self.valid

    def play_sound(self):
        self.audio.append('audio-file')
        return SimpleNamespace(get_busy=lambda: False, stop=lambda: None)

    def drain(self):
        self.player._queue.put(None)
        self.player._worker_loop()

    def test_tts_queue_checks_ownership_when_worker_consumes_item(self):
        guarded_call(self.player.speak_text, 'A', result_is_current=self.current)
        self.valid = False
        guarded_call(self.player.speak_text, 'B', result_is_current=lambda: True)
        self.drain()
        self.assertEqual(self.audio, ['B'])
        self.assertTrue(self.player._queue.empty())

    def test_current_tts_queue_preserves_text_order(self):
        for text in ('ONE', 'TWO'):
            guarded_call(self.player.speak_text, text, result_is_current=self.current)
        self.drain()
        self.assertEqual(self.audio, ['ONE', 'TWO'])

    def test_started_tts_finishes_but_next_old_item_is_suppressed(self):
        def play_first():
            self.audio.append(self.pending_text)
            self.valid = False
        self.player._engine.runAndWait = play_first
        for text in ('A1', 'A2'):
            guarded_call(self.player.speak_text, text, result_is_current=self.current)
        self.drain()
        self.assertEqual(self.audio, ['A1'])

    def test_audio_file_checks_after_decoder_before_sound_play(self):
        def decode(path):
            self.valid = False
            return SimpleNamespace(play=self.play_sound)
        self.pygame.mixer.Sound = decode
        guarded_call(tts_player._play_audio_file, 'synthetic-unused', self.player._shutdown, result_is_current=self.current)
        self.assertEqual(self.audio, [])

    def test_edge_generation_can_expire_before_playback(self):
        self.player._engine_name = 'edge_tts'
        self.player._engine = {'voice': 'synthetic', 'rate': '+0%', 'volume': '+0%'}
        def finish_generation(path):
            self.valid = False
        edge = SimpleNamespace(Communicate=lambda *a, **k: SimpleNamespace(save_sync=finish_generation))
        with patch.object(tts_player, 'edge_tts', edge):
            guarded_call(self.player._speak_edge_tts, 'A', result_is_current=self.current)
        self.assertEqual(self.audio, [])

    def test_final_validity_exception_drops_item_without_poisoning_b(self):
        def failed_boundary():
            raise RuntimeError('synthetic validity failure')
        guarded_call(self.player.speak_text, 'A', result_is_current=failed_boundary)
        guarded_call(self.player.speak_text, 'B', result_is_current=lambda: True)
        self.drain()
        self.assertEqual(self.audio, ['B'])

    def test_held_subtitle_worker_cannot_queue_audio_after_invalidation(self):
        subtitle = fixtures.main.SubtitleRenderer.__new__(fixtures.main.SubtitleRenderer)
        subtitle._thread = None
        subtitle._stop_event = threading.Event()
        subtitle._canvas = SimpleNamespace(after=lambda delay, callback: None)
        subtitle.clear = subtitle._schedule_draw = lambda *a: None
        subtitle._bleep = None
        subtitle._voice_out = SimpleNamespace(speak_segment=lambda text, **kw: self.audio.append(text), stop_bleeps=lambda: None)
        with patch.object(fixtures.main.threading, 'Thread', HeldThread), patch.object(fixtures.main.time, 'sleep'):
            guarded_call(subtitle.speak, response('A')['segments'], result_is_current=self.current)
            self.valid = False
            subtitle._thread.callback()
        self.assertEqual(self.audio, [])

    def test_held_bleep_worker_cannot_play_after_invalidation(self):
        bleep = fixtures.main.BleepPlayer.__new__(fixtures.main.BleepPlayer)
        bleep._mixer_ready = True
        bleep._thread = None
        bleep._stop_event = threading.Event()
        bleep._paused = False
        def play():
            self.audio.append('bleep')
            bleep._stop_event.set()
        bleep._make_bleep = lambda *a: SimpleNamespace(play=play)
        with patch.object(fixtures.main.threading, 'Thread', HeldThread), patch.object(fixtures.main.time, 'sleep'):
            guarded_call(bleep.start_talking, result_is_current=self.current)
            self.valid = False
            bleep._thread.callback()
        self.assertEqual(self.audio, [])

    def test_pyttsx3_invalidation_during_say_cannot_leave_a_for_b(self):
        pending = []
        def say(text):
            pending.append(text)
            if text == 'A':
                self.valid = False
        def play_pending():
            self.audio.extend(pending)
            pending.clear()
        self.player._engine = SimpleNamespace(say=say, runAndWait=play_pending, stop=pending.clear,
                                             connect=self.connect, disconnect=lambda token: self.disconnected.append(token))
        guarded_call(self.player.speak_text, 'A', result_is_current=self.current)
        guarded_call(self.player.speak_text, 'B', result_is_current=lambda: True)
        self.drain()
        self.assertEqual(self.audio, ['B'])
        self.assertEqual(pending, [])

    def test_kokoro_generation_can_expire_before_playback(self):
        self.player._engine_name = 'kokoro'
        def generated(*args, **kwargs):
            self.valid = False
            yield ('synthetic', 'synthetic', [0.0, 0.25])
        self.player._engine = {'pipeline': generated, 'voice': 'synthetic', 'speed': 1.0}
        guarded_call(self.player._speak_kokoro, 'A', result_is_current=self.current)
        self.assertEqual(self.audio, [])

    def test_old_subtitle_completion_cannot_stop_newer_audio(self):
        subtitle = fixtures.main.SubtitleRenderer.__new__(fixtures.main.SubtitleRenderer)
        subtitle._stop_event = threading.Event()
        subtitle._canvas = SimpleNamespace(after=lambda delay, callback: None)
        subtitle.clear = subtitle._schedule_draw = lambda *a: None
        subtitle._bleep = None
        def queue_audio(text, **kwargs):
            self.audio.append(text)
            self.valid = False  # B owns audio by the time A reaches cleanup.
        stops = []
        subtitle._voice_out = SimpleNamespace(speak_segment=queue_audio, stop_bleeps=lambda: stops.append('stop B'))
        with patch.object(fixtures.main.time, 'sleep'):
            guarded_call(subtitle._run, response('A')['segments'], None, result_is_current=self.current)
        self.assertEqual(stops, [])
        self.assertEqual(self.audio, ['A'])

    def test_subtitle_wait_cannot_start_expired_worker(self):
        subtitle = fixtures.main.SubtitleRenderer.__new__(fixtures.main.SubtitleRenderer)
        subtitle._stop_event = threading.Event()
        subtitle.stop = lambda: setattr(self, 'valid', False)
        starts = []
        class RecordingThread(HeldThread):
            def start(self):
                starts.append('thread started')
        with patch.object(fixtures.main.threading, 'Thread', RecordingThread):
            guarded_call(subtitle.speak, response('A')['segments'], result_is_current=self.current)
        self.assertEqual(starts, [])

    def test_bleep_wait_cannot_start_expired_worker(self):
        bleep = fixtures.main.BleepPlayer.__new__(fixtures.main.BleepPlayer)
        bleep._mixer_ready = True
        bleep._stop_event = threading.Event()
        bleep.stop = lambda: setattr(self, 'valid', False)
        starts = []
        class RecordingThread(HeldThread):
            def start(self):
                starts.append('thread started')
        with patch.object(fixtures.main.threading, 'Thread', RecordingThread):
            guarded_call(bleep.start_talking, result_is_current=self.current)
        self.assertEqual(starts, [])

    def test_bleep_sound_preparation_can_expire_before_play(self):
        bleep = fixtures.main.BleepPlayer.__new__(fixtures.main.BleepPlayer)
        bleep._stop_event = threading.Event()
        bleep._paused = False
        def prepare(*args):
            self.valid = False
            return SimpleNamespace(play=lambda: self.audio.append('expired bleep'))
        bleep._make_bleep = prepare
        with patch.object(fixtures.main.time, 'sleep'):
            guarded_call(bleep._loop, 'neutral', result_is_current=self.current)
        self.assertEqual(self.audio, [])

    def test_pyttsx3_native_entry_during_say_is_not_retroactively_stopped(self):
        stops = []
        def say(text):
            self.started_callback(name='synthetic')
            self.audio.append(text)  # Fake driver enters physical playback here.
            self.valid = False
        self.player._engine = SimpleNamespace(
            say=say, runAndWait=lambda: None, stop=lambda: stops.append('stop started A'),
            connect=self.connect, disconnect=lambda token: self.disconnected.append(token),
        )
        for text in ('A1', 'A2'):
            guarded_call(self.player.speak_text, text, result_is_current=self.current)
        self.drain()
        self.assertEqual(self.audio, ['A1'])
        self.assertEqual(stops, [])

    def test_failed_pyttsx3_staging_cannot_resurrect_a_during_b(self):
        pending = []
        def say(text):
            pending.append(text)
            if text == 'A':
                self.valid = False
                raise RuntimeError('synthetic staging failure')
        def play_pending():
            self.audio.extend(pending)
            pending.clear()
        self.player._engine = SimpleNamespace(say=say, runAndWait=play_pending, stop=pending.clear,
                                             connect=self.connect, disconnect=lambda token: None)
        guarded_call(self.player.speak_text, 'A', result_is_current=self.current)
        guarded_call(self.player.speak_text, 'B', result_is_current=lambda: True)
        self.drain()
        self.assertEqual(self.audio, ['B'])

    def test_new_speech_resumes_after_invalidated_subtitle_pause(self):
        voice = tts_player.VoiceOutputCoordinator.__new__(tts_player.VoiceOutputCoordinator)
        voice._pause_lock = threading.RLock()
        voice._pause_is_current = None
        voice._mode, voice._tts, voice._bleep = 'tts_only', self.player, None
        subtitle = fixtures.main.SubtitleRenderer.__new__(fixtures.main.SubtitleRenderer)
        subtitle._stop_event = threading.Event()
        subtitle._canvas = SimpleNamespace(after=lambda delay, callback: None)
        subtitle.clear = subtitle._schedule_draw = lambda *a: None
        subtitle._bleep, subtitle._voice_out = None, voice
        def invalidate_at_pause(delay):
            if delay == 0.25:
                self.valid = False
        with patch.object(fixtures.main.time, 'sleep', side_effect=invalidate_at_pause):
            guarded_call(subtitle._run, [{'text': 'A', 'pause': 0.25}], None, result_is_current=self.current)
        guarded_call(voice.start_speech, response('B')['segments'], 'neutral', result_is_current=lambda: True)
        # Prevent a faulty paused worker from hanging the test; failures remain observable audio.
        with patch.object(tts_player.time, 'sleep', side_effect=RuntimeError('still paused')):
            self.drain()
        self.assertEqual(self.audio, ['B'])

    def test_current_and_unowned_pause_survive_new_speech_admission(self):
        for predicate in (None, lambda: True):
            with self.subTest(owned=predicate is not None):
                voice = tts_player.VoiceOutputCoordinator.__new__(tts_player.VoiceOutputCoordinator)
                voice._pause_lock = threading.RLock()
                voice._pause_is_current = None
                voice._mode, voice._tts, voice._bleep = 'tts_only', self.player, None
                guarded_call(voice.pause, result_is_current=predicate)
                guarded_call(voice.start_speech, response('B')['segments'], 'neutral', result_is_current=lambda: True)
                self.assertTrue(self.player._paused.is_set(), 'Current/explicit pause remains in effect')

    def test_pause_handoff_cannot_leave_b_paused_after_a_expires(self):
        voice = tts_player.VoiceOutputCoordinator.__new__(tts_player.VoiceOutputCoordinator)
        voice._pause_lock = threading.RLock()
        voice._pause_is_current = None
        voice._mode, voice._tts, voice._bleep = 'tts_only', self.player, None
        entered, release, b_entered = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def held_pause():
            entered.set()
            if not release.wait(3):
                raise AssertionError('pause barrier not released')
            self.player._paused.set()
        def pause_a():
            try:
                guarded_call(voice.pause, result_is_current=self.current)
            except BaseException as exc:
                errors.append(exc)
        def start_b():
            b_entered.set()
            try:
                guarded_call(voice.start_speech, response('B')['segments'], 'neutral', result_is_current=lambda: True)
            except BaseException as exc:
                errors.append(exc)
        with patch.object(self.player, 'pause', side_effect=held_pause):
            a = threading.Thread(target=pause_a)
            b = threading.Thread(target=start_b)
            a.start()
            try:
                self.assertTrue(entered.wait(3))
                self.valid = False
                b.start()
                self.assertTrue(b_entered.wait(3))
            finally:
                release.set()
                a.join(3)
                if b.ident is not None:
                    b.join(3)
        self.assertFalse(a.is_alive() or b.is_alive())
        self.assertEqual(errors, [])
        with patch.object(tts_player.time, 'sleep', side_effect=RuntimeError('still paused')):
            self.drain()
        self.assertEqual(self.audio, ['B'])


if __name__ == '__main__':
    unittest.main()
