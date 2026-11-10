"""Real speech coordination and subtitle execution with in-memory audio/UI."""
import unittest
from unittest.mock import patch

from tests import test_continuation_comparison as comparison


class AudioEnqueueOwnership(unittest.TestCase):
    def setUp(self):
        self.fixture = comparison.SubtitleBoundaryComparison(methodName='runTest')

    def run_speech(self, mode, texts=('A', 'B'), validity=lambda: True):
        renderer, voice = self.fixture.renderer(mode)
        segments = [{'text': text, 'pause': 0.0} for text in texts]
        voice.start_speech(segments, 'neutral', result_is_current=validity)
        with patch('main.time.sleep', return_value=None):
            renderer._run(segments, lambda: None, result_is_current=validity)
        return renderer, voice

    def test_tts_only_queues_each_segment_once(self):
        self.run_speech('tts_only')
        self.assertEqual(self.fixture.tts, ['A', 'B'])

    def test_tts_only_keeps_batch_admission_before_subtitle_execution(self):
        renderer, voice = self.fixture.renderer('tts_only')
        segments = [{'text': 'A', 'pause': 0.0}]
        voice.start_speech(segments, 'neutral')
        self.assertEqual(self.fixture.tts, ['A'])
        with patch('main.time.sleep', return_value=None):
            renderer._run(segments, lambda: None)
        self.assertEqual(self.fixture.tts, ['A'])

    def test_both_keeps_subtitle_segment_enqueue(self):
        renderer, voice = self.fixture.renderer('both')
        segments = [{'text': 'A', 'pause': 0.0}, {'text': 'B', 'pause': 0.0}]
        voice.start_speech(segments, 'neutral')
        self.assertEqual(self.fixture.tts, [])
        with patch('main.time.sleep', return_value=None):
            renderer._run(segments, lambda: None)
        self.assertEqual(self.fixture.tts, ['A', 'B'])

    def test_bleeps_only_does_not_queue_tts(self):
        self.run_speech('bleeps_only')
        self.assertEqual(self.fixture.tts, [])
        self.assertEqual(self.fixture.bleeps, ['start'])

    def test_repeated_user_segments_are_not_deduplicated(self):
        self.run_speech('tts_only', ('A', 'A'))
        self.assertEqual(self.fixture.tts, ['A', 'A'])

    def test_empty_segments_queue_no_text(self):
        self.run_speech('tts_only', ())
        self.assertEqual(self.fixture.tts, [])

    def test_expired_speech_does_not_enqueue(self):
        self.run_speech('tts_only', validity=lambda: False)
        self.assertEqual(self.fixture.tts, [])

    def test_missing_tts_keeps_bleep_fallback_without_late_tts_enqueue(self):
        renderer, voice = self.fixture.renderer('tts_only')
        voice._tts.engine_name = 'synthetic'
        voice._tts.package_ok = False
        segments = [{'text': 'A', 'pause': 0.0}]
        voice.start_speech(segments, 'neutral')
        with patch('main.time.sleep', return_value=None):
            renderer._run(segments, lambda: None)
        self.assertEqual(self.fixture.bleeps, ['start'])
        self.assertEqual(self.fixture.tts, [])

    def test_failed_batch_keeps_bleep_fallback_without_retrying_segments(self):
        renderer, voice = self.fixture.renderer('tts_only')
        def fail(*args, **kwargs):
            raise RuntimeError('synthetic audio queue failure')
        voice._tts.speak_segments = fail
        segments = [{'text': 'A', 'pause': 0.0}]
        voice.start_speech(segments, 'neutral')
        with patch('main.time.sleep', return_value=None):
            renderer._run(segments, lambda: None)
        self.assertEqual(self.fixture.bleeps, ['start'])
        self.assertEqual(self.fixture.tts, [])
