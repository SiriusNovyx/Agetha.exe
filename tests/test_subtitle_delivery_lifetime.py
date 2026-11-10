"""Held owner queues and fake Canvas state for renderer lifetime contracts."""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main
from agetha.features.tts_player import VoiceOutputCoordinator


class Canvas:
    def __init__(self, owner):
        self.owner = owner
        self.timers, self.effects, self.foreign_calls = [], [], []

    def config(self, **_kwargs): pass
    def bind(self, *_args, **_kwargs): pass

    def after(self, delay, callback):
        if threading.get_ident() != self.owner:
            self.foreign_calls.append('after')
        self.timers.append((delay, callback))
        return str(len(self.timers))

    def delete(self, _tag):
        if threading.get_ident() != self.owner:
            self.foreign_calls.append('delete')
        self.effects.append('CLEAR')


class SubtitleDeliveryLifetime(unittest.TestCase):
    def setUp(self):
        self.canvas = Canvas(threading.get_ident())
        self.posts, self.audio, self.done = [], [], []
        self.closing = False
        with patch.object(main.SubtitleRenderer, '_load_font', return_value=object()):
            self.renderer = main.SubtitleRenderer(self.canvas, schedule_ui=self.post)
        self.renderer._draw = lambda text, color='#ffffff': self.canvas.effects.append(text)
        self.renderer._voice_out = SimpleNamespace(
            speak_segment=lambda text, **_kwargs: self.audio.append(text),
            stop_bleeps=lambda: None, pause=lambda **_kwargs: None, resume=lambda: None)

    def post(self, callback):
        if self.closing:
            return None
        self.posts.append(callback)
        return callback

    def ui(self):
        while True:
            while self.posts:
                self.posts.pop(0)()
            ready = [item for item in self.canvas.timers if item[0] == 0]
            self.canvas.timers[:] = [item for item in self.canvas.timers if item[0] != 0]
            if not ready:
                return
            for _delay, callback in ready:
                callback()

    def run_speech(self, texts=('A', 'B'), predicate=None):
        with patch.object(main.time, 'sleep', return_value=None):
            self.renderer._run([{'text': text, 'pause': 0} for text in texts],
                               lambda: self.done.append('done'), result_is_current=predicate)

    def test_held_success_coalesces_text_and_completes_once(self):
        self.run_speech()
        self.assertEqual(self.canvas.effects, [])
        self.ui()
        self.assertEqual(self.canvas.effects, ['CLEAR', 'A B'])
        self.assertEqual(self.audio, ['A', 'B'])
        self.assertEqual(self.done, ['done'])

    def test_expired_result_cannot_draw_or_clear_on_delivery(self):
        valid = [True]
        self.run_speech(('OLD',), lambda: valid[0])
        valid[0] = False
        self.ui()
        self.assertEqual(self.canvas.effects, [])
        self.assertEqual(self.done, ['done'])  # Host completion retains cleanup authority.

    def test_stop_expires_held_draw_and_clear(self):
        self.run_speech(('OLD',))
        self.renderer.stop()
        self.ui()
        self.assertEqual(self.canvas.effects, [])
        self.assertEqual(self.done, ['done'])

    def test_new_static_message_rejects_old_held_speech_draw(self):
        self.run_speech(('OLD',))
        self.renderer.show_message('NEW', duration=0)
        self.ui()
        self.assertEqual(self.canvas.effects, ['NEW'])
        self.assertEqual(self.done, ['done'])

    def test_old_static_timer_cannot_clear_new_static_message(self):
        self.renderer.show_message('OLD', duration=6)
        self.ui()
        old_timers = list(self.canvas.timers)
        self.canvas.timers.clear()
        self.renderer.show_message('NEW', duration=0)
        self.ui()
        for _delay, callback in old_timers:
            callback()
        self.assertEqual(self.canvas.effects, ['OLD', 'NEW'])

    def test_current_static_timer_clears_current_message(self):
        self.renderer.show_message('CURRENT', duration=6)
        self.ui()
        self.assertEqual([delay for delay, _callback in self.canvas.timers], [6000])
        self.canvas.timers.pop()[1]()
        self.assertEqual(self.canvas.effects, ['CURRENT', 'CLEAR'])

    def test_old_static_timer_cannot_clear_thinking_preview(self):
        self.renderer.show_message('OLD', duration=6)
        self.ui()
        old_timers = list(self.canvas.timers)
        self.canvas.timers.clear()
        self.renderer.show_thinking('{"text":"preview"')
        self.ui()
        for _delay, callback in old_timers:
            callback()
        self.assertEqual(self.canvas.effects, ['OLD', 'preview'])

    def test_held_worker_cannot_enter_successor_presentation(self):
        workers = []
        class HeldThread:
            def __init__(self, target, args, **_kwargs):
                self.callback = lambda: target(*args)
                workers.append(self)
            def start(self): pass
            def is_alive(self): return False
        with patch.object(main.threading, 'Thread', HeldThread), \
             patch.object(main.time, 'sleep', return_value=None):
            self.renderer.speak([{'text': 'OLD', 'pause': 0}],
                                on_done=lambda: self.done.append('old done'))
            self.renderer.speak([{'text': 'NEW', 'pause': 0}],
                                on_done=lambda: self.done.append('new done'))
            workers[0].callback()
            workers[1].callback()
        self.ui()
        self.assertEqual(self.canvas.effects, ['CLEAR', 'NEW'])
        self.assertEqual(self.audio, ['NEW'])
        self.assertEqual(sorted(self.done), ['new done', 'old done'])

    def test_close_rejects_held_and_future_presentations(self):
        self.renderer.show_message('OLD', duration=6)
        self.renderer.close()
        self.renderer.show_message('NEW', duration=0)
        self.renderer.show_thinking('{"text":"preview"')
        self.renderer.speak([{'text': 'SPEECH', 'pause': 0}])
        self.ui()
        for _delay, callback in self.canvas.timers:
            callback()
        self.assertEqual(self.canvas.effects, [])
        self.assertEqual(self.audio, [])

    def test_static_message_supersedes_old_held_static_draw(self):
        self.renderer.show_message('OLD', duration=0)
        self.renderer.show_message('NEW', duration=0)
        self.ui()
        self.assertEqual(self.canvas.effects, ['NEW'])

    def test_worker_uses_host_queue_without_canvas_scheduling(self):
        errors = []
        def work():
            try:
                self.run_speech(('WORKER',))
            except BaseException as exc:
                errors.append(exc)
        worker = threading.Thread(target=work)
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.canvas.foreign_calls, [])
        self.assertEqual(self.canvas.effects, [])
        self.ui()
        self.assertEqual(self.canvas.effects, ['CLEAR', 'WORKER'])
        self.assertEqual(self.done, ['done'])

    def test_shutdown_before_delivery_rejects_held_effects(self):
        self.run_speech(('OLD',))
        self.closing = True
        self.renderer.stop()
        self.ui()
        self.assertEqual(self.canvas.effects, [])

    def test_new_speech_after_stop_does_not_revalidate_old_delivery(self):
        self.run_speech(('OLD',))
        self.renderer.stop()
        with patch.object(main.time, 'sleep', return_value=None):
            self.renderer.speak([{'text': 'NEW', 'pause': 0}],
                                on_done=lambda: self.done.append('new done'))
            self.renderer._thread.join(2)
        self.assertFalse(self.renderer._thread.is_alive())
        self.ui()
        self.assertEqual(self.canvas.effects, ['CLEAR', 'NEW'])
        self.assertEqual(sorted(self.done), ['done', 'new done'])

    def test_thinking_preview_preserves_text_and_color(self):
        observed = []
        self.renderer._draw = lambda text, color: observed.append((text, color))
        self.renderer.show_thinking('{"text":"partial"')
        self.ui()
        self.assertEqual(observed, [('partial', '#888899')])

    def test_stop_then_new_thinking_preview_is_eligible(self):
        self.renderer.show_thinking('{"text":"OLD"')
        self.renderer.stop()
        self.renderer.show_thinking('{"text":"NEW"')
        self.ui()
        self.assertEqual(self.canvas.effects, ['NEW'])

    def test_thinking_preview_does_not_wait_for_prior_speech_worker(self):
        joins = []
        self.renderer._thread = SimpleNamespace(is_alive=lambda: True,
            join=lambda **kwargs: joins.append(kwargs))
        self.renderer.show_thinking('{"text":"NEW"')
        self.ui()
        self.assertEqual(joins, [])
        self.assertEqual(self.canvas.effects, ['NEW'])

    def test_local_stop_releases_audio_pause_before_later_speech(self):
        paused = threading.Event()
        voice = VoiceOutputCoordinator.__new__(VoiceOutputCoordinator)
        voice._pause_lock = threading.RLock()
        voice._pause_is_current = None
        voice._mode, voice._bleep = 'tts_only', None
        voice._tts = SimpleNamespace(package_ok=True, pause=paused.set, resume=paused.clear,
            speak_segments=lambda segments, **kw: self.audio.extend(
                s['text'] for s in segments if not paused.is_set()))
        self.renderer._voice_out = voice
        def sleep(delay):
            if delay == 0.7:
                self.assertTrue(paused.is_set())
                self.renderer.show_message('NEW', duration=0)
        with patch.object(main.time, 'sleep', side_effect=sleep):
            self.renderer._run([{'text': 'OLD', 'pause': 0.7}], None)
        voice.start_speech([{'text': 'LATER', 'pause': 0}], 'neutral')
        self.ui()
        self.assertFalse(paused.is_set())
        self.assertEqual(self.audio, ['LATER'])
        self.assertEqual(self.canvas.effects, ['NEW'])

    def test_rejected_queue_does_not_run_valid_completion_on_worker(self):
        self.closing = True
        worker = threading.Thread(target=lambda: self.run_speech(('A',), lambda: True))
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.done, [])
        self.assertEqual(self.canvas.foreign_calls, [])

    def test_silent_render_does_not_enqueue_audio(self):
        with patch.object(main.time, 'sleep', return_value=None):
            self.renderer._run([{'text': 'SILENT', 'pause': 0}],
                               lambda: self.done.append('done'), allow_audio=False)
        self.ui()
        self.assertEqual(self.audio, [])
        self.assertEqual(self.canvas.effects, ['CLEAR', 'SILENT'])
        self.assertEqual(self.done, ['done'])
