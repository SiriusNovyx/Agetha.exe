"""Prompt flexibility with fake engine state and no saved persona access."""

import json
import re
import unittest
from unittest.mock import patch

from agetha.core import ai_engine, memory_system
from tests import test_fast_mode_runtime as fast
from tests import test_language_policy as language


class RoleplayPromptFlexibility(unittest.TestCase):
    def prompt(self, profile):
        engine = fast._engine()
        if profile == 'normal':
            engine._faster_mode = False
            engine._fast_profile_active = False
        with patch.object(ai_engine, '_MEMORY_SYSTEM_AVAILABLE', False):
            system, _turn, _messages = engine._build_prompt(
                '', 'Stay in character and explain the scene.', '', request_profile=profile)
        return system

    def test_normal_conversation_has_no_three_segment_quota(self):
        prompt = self.prompt('normal')
        self.assertIsNone(re.search(r'1[–-]3 (?:spoken )?segments|segments: 1[–-]3', prompt))
        self.assertIn('last pause', prompt)

    def test_faster_conversation_is_not_limited_to_eight_word_fragments(self):
        prompt = self.prompt('fast_user')
        self.assertFalse('each 1-8 words' in prompt, 'Faster prompt still imposes an eight-word fragment limit')
        self.assertFalse('1-3 max' in prompt, 'Faster prompt still imposes a three-segment maximum')
        self.assertIn('last pause', prompt)

    def test_analysis_still_uses_complete_detail_guidance(self):
        quick = self.prompt('fast_user')
        for profile in ('fast_tool_result', 'deep_analysis'):
            with self.subTest(profile=profile):
                prompt = self.prompt(profile)
                self.assertIn('Preserve essential analysis details', prompt)
                self.assertFalse('each 1-8 words' in prompt)
                self.assertNotEqual(prompt, quick)

    def test_character_voice_has_no_blanket_gender_or_register_ban(self):
        for prompt in (ai_engine.SYSTEM_PROMPT, ai_engine.SYSTEM_PROMPT_FASTER,
                       memory_system.DEFAULT_SOUL_MD):
            with self.subTest(prompt=prompt[:30]):
                self.assertFalse('Do not unnecessarily add gendered speech' in prompt)
                self.assertFalse('Do not add unrequested gendered speech' in prompt)
                self.assertIn('conversational register', prompt)

    def test_new_persona_template_has_no_explanation_or_emotion_quota(self):
        template = memory_system.DEFAULT_SOUL_MD
        self.assertFalse('Never lecture or explain' in template)
        self.assertFalse('Use at most once per session' in template)
        self.assertFalse('Do not narrate stage directions' in template)
        self.assertIn('Agetha', template)

    def test_parser_retains_longer_roleplay_and_quoted_payloads(self):
        segments = [{'text': f'Scene {index}: This reply has more than eight words and keeps its requested detail.',
                     'pause': 0.0} for index in range(7)]
        parsed = language._engine()._parse(json.dumps({
            'command': 'speak', 'mood': 'neutral', 'segments': segments, 'shutdown': False}))
        self.assertEqual(parsed['segments'], segments)
