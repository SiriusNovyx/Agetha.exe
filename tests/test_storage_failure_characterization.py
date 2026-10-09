"""Observed F08/F12 edge contracts, using synthetic storage only.

These assertions characterize current loss/failure behavior. They do not endorse
it or define a replacement persistence policy.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_memory_retention_characterization as fixtures
from agetha.core import ai_engine as ai, memory_search as search, memory_system as memory


class StorageFailureCharacterization(unittest.TestCase):
    engine = fixtures.MemoryRetentionCharacterization.engine

    def setUp(self):
        fixtures.MemoryRetentionCharacterization.setUp(self)
        self.enterContext(patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True))
        self.enterContext(patch.object(memory, 'EPISODIC_HARD_CAP', 50))
        self.enterContext(patch.object(memory, 'EPISODIC_ENTRY_MAX_CHARS', 300))

    def persist(self, engine, summary='NEW_SYNTHETIC_FACT'):
        return engine._persist_profile_memory(
            ai.REQUEST_PROFILES['normal'], 'remember a synthetic fact',
            json.dumps({'summary_memory': summary}), {'command': 'speak'})

    def episodic_summaries(self):
        return [row['summary'] for row in json.loads(self.episodic.read_text(encoding='utf-8'))]

    def archive_summaries(self):
        return [json.loads(line)['summary'] for line in self.longterm.read_text(encoding='utf-8').splitlines()]

    def deny_open(self, target, denied_mode):
        original = Path.open
        def denied(path, mode='r', *args, **kwargs):
            if path == target and mode == denied_mode:
                raise PermissionError('synthetic storage denial')
            return original(path, mode, *args, **kwargs)
        return patch.object(Path, 'open', denied)

    def test_episodic_read_denial_preserves_bytes_but_next_append_can_replace_them(self):
        self.episodic.write_text('[{"summary":"PRIOR_SYNTHETIC_FACT"}]', encoding='utf-8')
        before = self.episodic.read_bytes()
        with self.deny_open(self.episodic, 'r'):
            self.assertEqual(memory.get_recent_memories(), [])
            self.assertEqual(self.episodic.read_bytes(), before)
            memory.log_memory('NEW_SYNTHETIC_FACT')
        self.assertEqual(self.episodic_summaries(), ['NEW_SYNTHETIC_FACT'])

    def test_episodic_rows_have_no_semantic_validation_on_read_or_append(self):
        rows = [None, {'summary': 42}, {'summary': 'PRIOR_SYNTHETIC_FACT'}]
        self.episodic.write_text(json.dumps(rows), encoding='utf-8')
        before = self.episodic.read_bytes()
        self.assertEqual(memory.get_recent_memories(0), rows)
        self.assertEqual(self.episodic.read_bytes(), before)
        memory.log_memory('NEW_SYNTHETIC_FACT')
        after = json.loads(self.episodic.read_text(encoding='utf-8'))
        self.assertEqual(after[:-1], rows)
        self.assertEqual(after[-1]['summary'], 'NEW_SYNTHETIC_FACT')

    def test_flat_append_failure_does_not_stop_episodic_and_archive_writes(self):
        self.flat.write_text('PRIOR_SYNTHETIC_FACT\n', encoding='utf-8')
        before = self.flat.read_bytes()
        engine = self.engine()
        with self.deny_open(self.flat, 'a'), self.assertLogs('Agetha', 'WARNING'):
            self.assertIsNone(self.persist(engine))
        self.assertEqual(self.flat.read_bytes(), before)
        self.assertEqual(self.episodic_summaries(), ['NEW_SYNTHETIC_FACT'])
        self.assertEqual(self.archive_summaries(), ['NEW_SYNTHETIC_FACT'])

    def test_failed_episodic_replace_preserves_it_but_other_mirrors_commit(self):
        self.episodic.write_text('[{"summary":"PRIOR_SYNTHETIC_FACT"}]', encoding='utf-8')
        before = self.episodic.read_bytes()
        engine = self.engine()
        original = memory.write_atomic
        def denied(target, content):
            if target == self.episodic:
                raise OSError('synthetic replace failure')
            return original(target, content)
        with patch.object(memory, 'write_atomic', denied):
            self.assertIsNone(self.persist(engine))
        self.assertEqual(self.episodic.read_bytes(), before)
        self.assertEqual(self.flat.read_text(encoding='utf-8').splitlines(), ['NEW_SYNTHETIC_FACT'])
        self.assertEqual(self.archive_summaries(), ['NEW_SYNTHETIC_FACT'])

    def test_archive_append_failure_does_not_roll_back_flat_or_episodic(self):
        search.log_longterm_memory('PRIOR_SYNTHETIC_FACT')
        before = self.longterm.read_bytes()
        engine = self.engine()
        with self.deny_open(self.longterm, 'a'), self.assertLogs('Agetha', 'WARNING'):
            self.assertIsNone(self.persist(engine))
        self.assertEqual(self.longterm.read_bytes(), before)
        self.assertEqual(self.flat.read_text(encoding='utf-8').splitlines(), ['NEW_SYNTHETIC_FACT'])
        self.assertEqual(self.episodic_summaries(), ['NEW_SYNTHETIC_FACT'])

    def test_failed_condensation_writes_do_not_prevent_live_history_eviction(self):
        engine = self.engine()
        engine.HISTORY_LIMIT = 1
        engine._record('User: "OLD_SYNTHETIC_TURN"', 'old answer')
        with self.deny_open(self.flat, 'a'), patch.object(memory, 'write_atomic', side_effect=OSError('synthetic disk full')):
            engine._record('User: "NEW_SYNTHETIC_TURN"', 'new answer')
        self.assertEqual([row['assistant'] for row in engine._history], ['new answer'])
        self.assertFalse(self.flat.exists())
        self.assertFalse(self.episodic.exists())
        self.assertEqual(self.conversation.read_text(encoding='utf-8').count('AI_RAW:'), 2)
        restarted = self.engine()
        self.assertEqual(restarted._history, [])
        self.assertEqual(self.conversation.read_bytes(), b'')

    def test_unterminated_archive_record_and_new_append_become_one_unparseable_line(self):
        prior = json.dumps({'summary': 'PRIOR_SYNTHETIC_FACT'})
        self.longterm.write_text(prior, encoding='utf-8')
        self.assertEqual(search.get_longterm_entry_count(), 1)
        search.log_longterm_memory('NEW_SYNTHETIC_FACT')
        after = self.longterm.read_text(encoding='utf-8')
        self.assertTrue(after.startswith(prior + '{'))
        self.assertEqual(len(after.splitlines()), 1)
        self.assertEqual(search.get_longterm_entry_count(), 0)

    def test_archive_reads_skip_damaged_lines_without_rewriting_original(self):
        good = json.dumps({'summary': 'PRIOR_SYNTHETIC_FACT'})
        self.longterm.write_text(good + '\n{synthetic damaged tail}\n', encoding='utf-8')
        before = (self.longterm.read_bytes(), self.longterm.stat().st_mtime_ns)
        self.assertEqual(search.get_longterm_entry_count(), 1)
        self.assertEqual((self.longterm.read_bytes(), self.longterm.stat().st_mtime_ns), before)
        search.log_longterm_memory('NEW_SYNTHETIC_FACT')
        self.assertTrue(self.longterm.read_bytes().startswith(before[0]))
        self.assertEqual(search.get_longterm_entry_count(), 2)

    def test_conversation_append_failure_keeps_live_turn_but_restart_loses_it(self):
        engine = self.engine()
        with self.deny_open(self.conversation, 'a'), self.assertLogs('Agetha', 'WARNING'):
            engine._record('User: "UNSAVED_SYNTHETIC_TURN"', 'synthetic answer')
        self.assertEqual(len(engine._history), 1)
        self.assertEqual(self.conversation.read_bytes(), b'')
        restarted = self.engine()
        self.assertEqual(restarted._history, [])
        self.assertFalse(self.flat.exists())
        self.assertFalse(self.episodic.exists())
