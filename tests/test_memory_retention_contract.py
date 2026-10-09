"""F12 contracts backed by existing consumers; storage and providers are isolated.

These tests preserve the documented session-log reset and compatibility mirrors.
They do not establish a new archive/migration policy.
"""
from __future__ import annotations

import builtins
import json
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_memory_retention_characterization as characterization
from agetha import app_config
from agetha.core import ai_engine as ai, memory_search as search, memory_system as memory


class MemoryRetentionContract(unittest.TestCase):
    # Reuse the inspected, isolated fixture without inheriting its test methods.
    engine = characterization.MemoryRetentionCharacterization.engine
    prompt = characterization.MemoryRetentionCharacterization.prompt
    seed_episodic = characterization.MemoryRetentionCharacterization.seed_episodic

    def setUp(self):
        characterization.MemoryRetentionCharacterization.setUp(self)
        for name, value in (('EPISODIC_HARD_CAP', 50), ('EPISODIC_PROMPT_LIMIT', 10),
                            ('EPISODIC_ENTRY_MAX_CHARS', 300)):
            self.enterContext(patch.object(memory, name, value))
        self.config_before = self.config.read_bytes()
        self.env_before = (self.root / '.env').read_bytes()

    def assert_owned_state(self, engine, *, history, log_turns, flat, episodic, archive):
        self.assertEqual(len(engine._history), history)
        self.assertEqual(self.conversation.read_text().count('AI_RAW:'), log_turns)
        for path, expected, reader in (
            (self.flat, flat, lambda p: p.read_text().splitlines()),
            (self.episodic, episodic, lambda p: [e['summary'] for e in json.loads(p.read_text())]),
            (self.longterm, archive, lambda p: [json.loads(line)['summary'] for line in p.read_text().splitlines()]),
        ):
            if expected is None:
                self.assertFalse(path.exists(), str(path))
            else:
                self.assertEqual(reader(path), expected, str(path))
        self.assertEqual(self.config.read_bytes(), self.config_before)
        self.assertEqual((self.root / '.env').read_bytes(), self.env_before)

    def snapshot(self):
        return {p: (p.read_bytes(), p.stat().st_mtime_ns) if p.exists() else None
                for p in (self.flat, self.episodic, self.longterm)}

    def assert_preserved(self, before):
        self.assertEqual(self.snapshot(), before)

    def seed_soul(self):
        (self.store / 'soul.md').write_text('Synthetic identity for retention tests.', encoding='utf-8')

    def persist_summary(self, engine, summary):
        engine._persist_profile_memory(ai.REQUEST_PROFILES['normal'], 'remember this synthetic fact',
                                       json.dumps({'summary_memory': summary}), {'command': 'speak'})

    def test_fresh_start_has_empty_session_context_and_no_memory_writes(self):
        engine = self.engine()
        self.assertEqual(self.conversation.read_bytes(), b'')
        self.assertEqual(list(self.store.iterdir()), [])
        self.assert_owned_state(engine, history=0, log_turns=0, flat=None, episodic=None, archive=None)

    def test_existing_session_log_resets_without_reconstructing_prompt_history(self):
        self.conversation.write_text('OLD_SESSION_ONLY_MARKER', encoding='utf-8')
        self.flat.write_text('legacy durable fact\n', encoding='utf-8')
        self.seed_episodic('episodic durable fact')
        search.log_longterm_memory('archive durable fact')
        before = self.snapshot()
        engine = self.engine()
        self.assertEqual(engine._build_history(), [])
        self.assertEqual(self.conversation.read_bytes(), b'')
        self.assert_preserved(before)
        self.assert_owned_state(engine, history=0, log_turns=0, flat=['legacy durable fact'],
                                episodic=['episodic durable fact'], archive=['archive durable fact'])

    def test_restart_discards_live_turns_and_session_log_without_new_memory(self):
        first = self.engine()
        first._record('User: "uncondensed synthetic turn"', 'synthetic answer')
        self.assert_owned_state(first, history=1, log_turns=1, flat=None, episodic=None, archive=None)
        second = self.engine()
        self.assertEqual(second._build_history(), [])
        self.assert_owned_state(second, history=0, log_turns=0, flat=None, episodic=None, archive=None)

    def test_multiple_prior_turns_condense_once_per_evicted_turn_to_compatibility_and_episodic(self):
        engine = self.engine()
        engine.HISTORY_LIMIT = 2
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True):
            for index in range(4):
                engine._record(f'User: "synthetic prior turn {index}"', f'answer {index}')
        self.assertEqual([e['assistant'] for e in engine._history], ['answer 2', 'answer 3'])
        self.assert_owned_state(engine, history=2, log_turns=4,
                                flat=['synthetic prior turn 0.', 'synthetic prior turn 1.'],
                                episodic=['[condensed history] synthetic prior turn 0.',
                                          '[condensed history] synthetic prior turn 1.'], archive=None)

    def test_large_history_bounds_live_and_episodic_context_but_keeps_compatibility_summaries(self):
        engine = self.engine()
        engine.HISTORY_LIMIT = 3
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True), patch.object(memory, 'EPISODIC_HARD_CAP', 5):
            for index in range(60):
                engine._record(f'User: "synthetic large turn {index}"', f'answer {index}')
        flat = [f'synthetic large turn {index}.' for index in range(57)]
        episodic = [f'[condensed history] synthetic large turn {index}.' for index in range(52, 57)]
        self.assertEqual([e['assistant'] for e in engine._history], ['answer 57', 'answer 58', 'answer 59'])
        self.assert_owned_state(engine, history=3, log_turns=60, flat=flat, episodic=episodic, archive=None)
        before = self.snapshot()
        restarted = self.engine()
        self.assert_preserved(before)
        self.assert_owned_state(restarted, history=0, log_turns=0, flat=flat, episodic=episodic, archive=None)

    def test_missing_conversation_file_is_created_without_importing_durable_memory(self):
        self.flat.write_text('old installation fact\n', encoding='utf-8')
        self.assertFalse(self.conversation.exists())
        before = self.snapshot()
        engine = self.engine()
        self.assertEqual(self.conversation.read_bytes(), b'')
        self.assert_preserved(before)
        self.assert_owned_state(engine, history=0, log_turns=0, flat=['old installation fact'],
                                episodic=None, archive=None)

    def test_existing_legacy_memory_stays_intact_without_implicit_conversion(self):
        self.seed_soul()
        self.flat.write_text('LEGACY_ONLY_CANONICAL_FALLBACK\n', encoding='utf-8')
        before = self.snapshot()
        engine = self.engine()
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True):
            system, user, messages = self.prompt(engine)
        self.assertNotIn('LEGACY_ONLY_CANONICAL_FALLBACK', system + user + str(messages))
        self.assertEqual(engine._load_memories(), 'LEGACY_ONLY_CANONICAL_FALLBACK')
        self.assert_preserved(before)
        self.assert_owned_state(engine, history=0, log_turns=0,
                                flat=['LEGACY_ONLY_CANONICAL_FALLBACK'], episodic=None, archive=None)

    def test_existing_episodic_is_the_available_module_prompt_owner_after_restart(self):
        self.seed_soul()
        self.seed_episodic('EPISODIC_CANONICAL_RECENT')
        before = self.snapshot()
        engine = self.engine()
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True):
            system, user, messages = self.prompt(engine)
        self.assertIn('EPISODIC_CANONICAL_RECENT', system)
        self.assertIn('EPISODIC_CANONICAL_RECENT', user)
        self.assertEqual(engine._build_history(), [])
        self.assertEqual(messages[-1], {'role': 'user', 'content': user})
        self.assert_preserved(before)
        self.assert_owned_state(engine, history=0, log_turns=0, flat=None,
                                episodic=['EPISODIC_CANONICAL_RECENT'], archive=None)

    def test_related_stores_keep_separate_recent_archive_and_fallback_consumers(self):
        self.seed_soul()
        self.flat.write_text('LEGACY_UNIQUE\nRELATED_FACT\n', encoding='utf-8')
        self.seed_episodic('RELATED_FACT')
        search.log_longterm_memory('RELATED_FACT')
        before = self.snapshot()
        engine = self.engine()
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True):
            system, user, _ = self.prompt(engine)
        self.assertNotIn('LEGACY_UNIQUE', system + user)
        self.assertEqual(system.count('RELATED_FACT'), 1)
        self.assertEqual(user.count('RELATED_FACT'), 2)  # recent recap + archive recap
        self.assertEqual(search.search_memories('RELATED_FACT')[0]['summary'], 'RELATED_FACT')
        self.assert_preserved(before)
        self.assert_owned_state(engine, history=0, log_turns=0, flat=['LEGACY_UNIQUE', 'RELATED_FACT'],
                                episodic=['RELATED_FACT'], archive=['RELATED_FACT'])

    def test_available_memory_module_writes_one_record_per_documented_store(self):
        engine = self.engine()
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True):
            self.persist_summary(engine, 'one synthetic provider summary')
        self.assert_owned_state(engine, history=0, log_turns=0, flat=['one synthetic provider summary'],
                                episodic=['one synthetic provider summary'], archive=['one synthetic provider summary'])

    def test_related_summary_writes_have_different_limits_so_flat_is_not_a_lossless_archive_duplicate(self):
        engine = self.engine()
        summary = ('synthetic extended memory ' * 50).strip()
        candidate = summary[:1000].strip()
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True), patch.object(memory, 'EPISODIC_ENTRY_MAX_CHARS', 300):
            self.persist_summary(engine, summary)
        self.assert_owned_state(engine, history=0, log_turns=0, flat=[candidate],
                                episodic=[candidate[:300]], archive=[candidate[:500]])
        self.assertGreater(len(candidate), len(candidate[:500]))

    def test_disabled_archive_keeps_documented_recent_and_compatibility_writes(self):
        engine = self.engine()
        engine._app_settings = app_config.AppSettings({'ENABLE_LONGTERM_MEMORY': 'no'})
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True):
            self.persist_summary(engine, 'synthetic recent only fact')
        self.assert_owned_state(engine, history=0, log_turns=0, flat=['synthetic recent only fact'],
                                episodic=['synthetic recent only fact'], archive=None)

    def test_missing_module_uses_flat_canonical_fallback_and_preserves_episodic(self):
        self.flat.write_text('LEGACY_CANONICAL_FALLBACK\n', encoding='utf-8')
        self.seed_episodic('UNAVAILABLE_EPISODIC_MARKER')
        episodic_before = self.episodic.read_bytes()
        original_import = builtins.__import__
        def unavailable(name, *args, **kwargs):
            if name == 'agetha.core.memory_system':
                raise ImportError('synthetic missing memory module')
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', unavailable):
            namespace = runpy.run_path(str(Path(ai.__file__)))
            self.assertFalse(namespace['_MEMORY_SYSTEM_AVAILABLE'])
            engine_class = namespace['AIEngine']
            with (
                patch.object(engine_class, '_resolve_config_path', return_value=self.config),
                patch.object(engine_class, '_ensure_provider_initialized',
                             side_effect=AssertionError('unexpected provider initialization')),
                patch.dict(engine_class.__init__.__globals__, {'native_error_popup': ai.native_error_popup}),
            ):
                engine = engine_class(defer_provider_init=True)
                self.persist_summary(engine, 'new fallback summary')
                system, user, _ = self.prompt(engine)
        self.assertIn('LEGACY_CANONICAL_FALLBACK', system)
        self.assertIn('new fallback summary', system)
        self.assertNotIn('UNAVAILABLE_EPISODIC_MARKER', system + user)
        self.assertEqual(self.episodic.read_bytes(), episodic_before)
        self.assert_owned_state(engine, history=0, log_turns=0,
                                flat=['LEGACY_CANONICAL_FALLBACK', 'new fallback summary'],
                                episodic=['UNAVAILABLE_EPISODIC_MARKER'], archive=['new fallback summary'])

    def test_new_summary_survives_restart_in_memory_stores_while_live_history_expires(self):
        self.seed_soul()
        first = self.engine()
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True):
            first._record('User: "remember a synthetic fact"', 'synthetic answer')
            self.persist_summary(first, 'durable synthetic fact')
        self.assert_owned_state(first, history=1, log_turns=1, flat=['durable synthetic fact'],
                                episodic=['durable synthetic fact'], archive=['durable synthetic fact'])
        before = self.snapshot()
        second = self.engine()
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True):
            system, user, messages = self.prompt(second)
        self.assertIn('durable synthetic fact', system + user)
        self.assertEqual(second._build_history(), [])
        self.assertNotIn('remember a synthetic fact', str(messages))
        self.assert_preserved(before)
        self.assert_owned_state(second, history=0, log_turns=0, flat=['durable synthetic fact'],
                                episodic=['durable synthetic fact'], archive=['durable synthetic fact'])

    def test_search_and_one_shot_recap_reconstruct_from_disk_without_rewriting(self):
        self.seed_soul()
        self.seed_episodic('recent durable synthetic fact')
        search.log_longterm_memory('searchable durable synthetic fact')
        search._cache_entries, search._cache_mtime, search._cache_size = [], None, 0
        before = self.snapshot()
        engine = self.engine()
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True):
            _, first, _ = self.prompt(engine)
            _, second, _ = self.prompt(engine)
        self.assertIn('SESSION RECAP', first)
        self.assertIn('searchable durable synthetic fact', first)
        self.assertNotIn('SESSION RECAP', second)
        self.assertEqual(search.search_memories('searchable')[0]['summary'], 'searchable durable synthetic fact')
        self.assert_preserved(before)
        self.assert_owned_state(engine, history=0, log_turns=0, flat=None,
                                episodic=['recent durable synthetic fact'], archive=['searchable durable synthetic fact'])

    def test_append_to_existing_archive_keeps_prior_records_searchable_before_and_after_restart(self):
        prior = {'ts': '2026-01-01T00:00:00+00:00', 'summary': 'PRIOR_ARCHIVE_FACT', 'source': 'ai'}
        self.longterm.write_text(json.dumps(prior) + '\n', encoding='utf-8')
        prior_bytes = self.longterm.read_bytes()
        engine = self.engine()
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True):
            self.persist_summary(engine, 'NEW_ARCHIVE_FACT')
        self.assertTrue(self.longterm.read_bytes().startswith(prior_bytes))
        self.assert_owned_state(engine, history=0, log_turns=0, flat=['NEW_ARCHIVE_FACT'],
                                episodic=['NEW_ARCHIVE_FACT'], archive=['PRIOR_ARCHIVE_FACT', 'NEW_ARCHIVE_FACT'])
        self.assertEqual([e['summary'] for e in search.search_memories('PRIOR')], ['PRIOR_ARCHIVE_FACT'])
        self.assertEqual(search.get_longterm_entry_count(), 2)
        before = self.snapshot()
        search._cache_entries, search._cache_mtime, search._cache_size = [], None, 0
        restarted = self.engine()
        self.assertEqual([e['summary'] for e in search.search_memories('PRIOR')], ['PRIOR_ARCHIVE_FACT'])
        self.assertEqual([e['summary'] for e in search.search_memories('NEW')], ['NEW_ARCHIVE_FACT'])
        self.assert_preserved(before)
        self.assert_owned_state(restarted, history=0, log_turns=0, flat=['NEW_ARCHIVE_FACT'],
                                episodic=['NEW_ARCHIVE_FACT'], archive=['PRIOR_ARCHIVE_FACT', 'NEW_ARCHIVE_FACT'])

    def test_compatibility_read_bounds_prompt_tail_without_truncating_legacy_file(self):
        content = 'synthetic old legacy summary\n' * 10000 + 'LEGACY_TAIL_MARKER'
        self.flat.write_text(content, encoding='utf-8')
        before = self.snapshot()
        engine = self.engine()
        engine._memory_chars_limit = 32
        engine._session_recap_pending = False
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', False):
            system, _, _ = self.prompt(engine)
        self.assertEqual(engine._load_memories(32), content[-32:])
        self.assertIn(content[-32:], system)
        self.assert_preserved(before)
        self.assert_owned_state(engine, history=0, log_turns=0, flat=content.splitlines(), episodic=None, archive=None)

    def test_append_during_archive_read_denial_reloads_prior_records_when_reads_recover(self):
        prior = {'ts': '2026-01-01T00:00:00+00:00', 'summary': 'PRIOR_ARCHIVE_FACT', 'source': 'ai'}
        self.longterm.write_text(json.dumps(prior) + '\n', encoding='utf-8')
        prior_bytes = self.longterm.read_bytes()
        engine = self.engine()
        original_open = Path.open
        def denied_read(path, mode='r', *args, **kwargs):
            if path == self.longterm and mode == 'r':
                raise PermissionError('synthetic temporary archive read denial')
            return original_open(path, mode, *args, **kwargs)
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True), patch.object(Path, 'open', denied_read):
            self.persist_summary(engine, 'NEW_ARCHIVE_FACT')
        self.assertTrue(self.longterm.read_bytes().startswith(prior_bytes))
        self.assertEqual([e['summary'] for e in search.search_memories('PRIOR')], ['PRIOR_ARCHIVE_FACT'])
        self.assertEqual(search.get_longterm_entry_count(), 2)
        self.assert_owned_state(engine, history=0, log_turns=0, flat=['NEW_ARCHIVE_FACT'],
                                episodic=['NEW_ARCHIVE_FACT'], archive=['PRIOR_ARCHIVE_FACT', 'NEW_ARCHIVE_FACT'])
