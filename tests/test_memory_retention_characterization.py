"""F12 retention characterization using synthetic storage and deferred providers.

Constructor tests use the real initialization path with injected config/storage.
No providers, native UI, real conversation file or root memory are used.
"""
from __future__ import annotations

import builtins
import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agetha import app_config
from agetha.core import ai_engine as ai
from agetha.core import memory_search as search
from agetha.core import memory_system as memory


class MemoryRetentionCharacterization(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = self.root / 'config.txt'
        self.config.write_text(app_config.DEFAULT_CONFIG, encoding='utf-8')
        self.store = self.root / 'memory'
        self.store.mkdir()
        self.conversation = self.root / 'conversation.txt'
        self.flat = self.store / 'memory.txt'
        self.episodic = self.store / 'episodic_memory.json'
        self.longterm = self.store / 'longterm_memory.jsonl'
        self.settings = app_config.AppSettings({
            'ENABLE_COMPANION_STATS_CONTEXT': 'no', 'ENABLE_EMOTION_ENGINE': 'no',
            'ENABLE_CIRCADIAN_RHYTHM': 'no', 'ENABLE_DREAMS': 'no',
            'ENABLE_TASKS': 'no', 'ENABLE_STATUS_PROVIDERS': 'no',
            'ENABLE_LONGTERM_MEMORY': 'yes', 'FASTER_MODE': 'no',
        })
        env = self.root / '.env'
        env.write_text('', encoding='utf-8')
        self.enterContext(patch.object(app_config, 'ENV_PATH', env))
        self.enterContext(patch.object(app_config, 'CONFIG_PATH', self.config))
        self.enterContext(patch.object(app_config, '_last_load', None))
        self.enterContext(patch.object(app_config, 'get_settings', return_value=self.settings))
        self.enterContext(patch.object(ai.AIEngine, '_resolve_config_path', return_value=self.config))
        self.enterContext(patch.object(ai, 'get_settings', return_value=self.settings))
        self.enterContext(patch.object(ai, 'native_error_popup', side_effect=AssertionError('unexpected native UI')))
        self.enterContext(patch.object(ai.AIEngine, '_ensure_provider_initialized', side_effect=AssertionError('unexpected provider init')))
        for name, value in (('MEMORY_DIR', self.store), ('EPISODIC_FILE', self.episodic),
                            ('SOUL_FILE', self.store / 'soul.md'), ('_soul_cache', None)):
            self.enterContext(patch.object(memory, name, value))
        for name, value in (('MEMORY_DIR', self.store), ('LONGTERM_FILE', self.longterm),
                            ('_cache_entries', []), ('_cache_mtime', None), ('_cache_size', 0)):
            self.enterContext(patch.object(search, name, value))

    def engine(self):
        return ai.AIEngine(defer_provider_init=True)

    def prompt(self, engine):
        return engine._build_prompt('', 'synthetic next turn', '')

    def seed_episodic(self, summary='EPISODIC_ONLY_MARKER'):
        self.episodic.write_text(json.dumps([
            {'ts': '2026-01-01T00:00:00+00:00', 'source': 'ai', 'summary': summary}
        ]), encoding='utf-8')

    def test_fresh_start_creates_empty_conversation_without_other_store_writes(self):
        engine = self.engine()
        self.assertEqual(self.conversation.read_text(), '')
        self.assertEqual(engine._history, [])
        self.assertEqual(list(self.store.iterdir()), [])

    def test_existing_conversation_is_truncated_and_not_loaded_into_history(self):
        self.conversation.write_text('old synthetic conversation', encoding='utf-8')
        engine = self.engine()
        self.assertEqual(self.conversation.read_text(), '')
        self.assertEqual(engine._build_history(), [])

    def test_missing_conversation_is_recreated_on_startup(self):
        self.assertFalse(self.conversation.exists())
        self.engine()
        self.assertTrue(self.conversation.exists())
        self.assertEqual(self.conversation.stat().st_size, 0)

    def test_large_conversation_is_truncated_without_reading_it(self):
        self.conversation.write_text('synthetic old turn\n' * 100_000, encoding='utf-8')
        original = Path.read_text
        def no_conversation_read(path, *args, **kwargs):
            if path == self.conversation:
                raise AssertionError('constructor must not read existing conversation')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', no_conversation_read):
            engine = self.engine()
        self.assertEqual(self.conversation.stat().st_size, 0)
        self.assertEqual(engine._history, [])

    def test_multiple_turns_append_log_but_keep_only_bounded_live_history(self):
        engine = self.engine()
        engine.HISTORY_LIMIT = 2
        for i in range(4):
            engine._record(f'User: "synthetic message {i}"', f'assistant {i}')
        self.assertEqual(len(engine._history), 2)
        self.assertEqual(engine._history[0]['assistant'], 'assistant 2')
        self.assertEqual(self.conversation.read_text().count('AI_RAW:'), 4)
        self.assertEqual(self.flat.read_text().splitlines(),
                         ['synthetic message 0.', 'synthetic message 1.'])
        entries = memory.get_recent_memories(0)
        self.assertEqual([e['summary'] for e in entries],
                         ['[condensed history] synthetic message 0.',
                          '[condensed history] synthetic message 1.'])
        self.assertFalse(self.longterm.exists())

    def test_restart_discards_uncondensed_turns_but_keeps_persistent_summaries(self):
        first = self.engine()
        first.HISTORY_LIMIT = 1
        first._record('User: "older synthetic turn"', 'old answer')
        first._record('User: "newest uncondensed turn"', 'new answer')
        before = {p: p.read_bytes() for p in (self.flat, self.episodic)}
        second = self.engine()
        self.assertEqual(second._history, [])
        self.assertEqual(self.conversation.read_text(), '')
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        system, user, messages = self.prompt(second)
        self.assertIn('older synthetic turn', system + user)
        self.assertNotIn('newest uncondensed turn', system + user + str(messages))

    def test_cold_cache_restart_reloads_episodic_and_longterm_from_disk(self):
        self.seed_episodic('durable episodic synthetic fact')
        search.log_longterm_memory('durable archive synthetic fact')
        memory.get_recent_memories()
        search.search_memories('archive')
        memory._soul_cache = None
        search._cache_entries = []
        search._cache_mtime = None
        search._cache_size = 0
        engine = self.engine()
        system, user, _ = self.prompt(engine)
        self.assertIn('durable episodic synthetic fact', system)
        self.assertIn('durable archive synthetic fact', user)
        self.assertEqual(search.search_memories('archive')[0]['summary'], 'durable archive synthetic fact')

    def test_existing_flat_memory_survives_startup_without_conversion(self):
        self.flat.write_text('LEGACY_ONLY_MARKER\n', encoding='utf-8')
        engine = self.engine()
        self.assertEqual(self.flat.read_text(), 'LEGACY_ONLY_MARKER\n')
        self.assertFalse(self.episodic.exists())
        self.assertEqual(engine._load_memories(), 'LEGACY_ONLY_MARKER')

    def test_existing_episodic_survives_startup_and_populates_prompt(self):
        self.seed_episodic()
        before = self.episodic.read_bytes()
        engine = self.engine()
        self.assertEqual(self.episodic.read_bytes(), before)
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True):
            system, user, _ = self.prompt(engine)
        self.assertIn('EPISODIC_ONLY_MARKER', system)
        self.assertIn('EPISODIC_ONLY_MARKER', user)  # one-shot session recap too
        self.assertFalse(self.flat.exists())

    def test_first_prompt_generates_default_soul_when_missing_or_empty(self):
        soul = self.store / 'soul.md'
        for empty in (False, True):
            with self.subTest(empty_file=empty):
                if empty:
                    soul.write_text('', encoding='utf-8')
                memory._soul_cache = None
                engine = self.engine()
                with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True):
                    system, _, _ = self.prompt(engine)
                self.assertEqual(soul.read_text(), memory.DEFAULT_SOUL_MD)
                self.assertIn(memory.DEFAULT_SOUL_MD.strip(), system)
                self.assertFalse(self.episodic.exists())
                self.assertFalse(self.flat.exists())

    def test_both_stores_survive_but_available_module_ignores_flat_prompt_path(self):
        self.flat.write_text('LEGACY_ONLY_MARKER\nshared synthetic fact\n', encoding='utf-8')
        self.seed_episodic('shared synthetic fact EPISODIC_ONLY_MARKER')
        before = {p: p.read_bytes() for p in (self.flat, self.episodic)}
        engine = self.engine()
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True):
            system, user, _ = self.prompt(engine)
        self.assertNotIn('LEGACY_ONLY_MARKER', system + user)
        self.assertIn('EPISODIC_ONLY_MARKER', system + user)
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_unavailable_module_uses_flat_prompt_and_leaves_episodic_intact(self):
        self.flat.write_text('LEGACY_ONLY_MARKER\n', encoding='utf-8')
        self.seed_episodic()
        before = self.episodic.read_bytes()
        engine = self.engine()
        engine._session_recap_pending = False
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', False):
            system, _, _ = self.prompt(engine)
        self.assertIn('LEGACY_ONLY_MARKER', system)
        self.assertNotIn('EPISODIC_ONLY_MARKER', system)
        self.assertEqual(self.episodic.read_bytes(), before)

    def test_import_failure_selects_legacy_fallback(self):
        original_import = builtins.__import__
        def without_memory(name, *args, **kwargs):
            if name == 'agetha.core.memory_system':
                raise ImportError('synthetic unavailable memory module')
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', without_memory):
            namespace = runpy.run_path(str(Path(ai.__file__)))
        self.assertFalse(namespace['_MEMORY_SYSTEM_AVAILABLE'])
        engine_class = namespace['AIEngine']
        self.flat.write_text('FALLBACK_IMPORT_MARKER\n', encoding='utf-8')
        with (
            patch.object(engine_class, '_resolve_config_path', return_value=self.config),
            patch.object(engine_class, '_ensure_provider_initialized', side_effect=AssertionError('unexpected provider init')),
            patch.dict(engine_class.__init__.__globals__, {'native_error_popup': ai.native_error_popup}),
        ):
            engine = engine_class(defer_provider_init=True)
        self.assertEqual(engine._load_memories(), 'FALLBACK_IMPORT_MARKER')
        engine._app_settings = self.settings
        with patch('builtins.__import__', without_memory):
            system, _, _ = self.prompt(engine)
        self.assertIn('FALLBACK_IMPORT_MARKER', system)

    def test_failed_startup_truncation_keeps_old_log_but_does_not_restore_history(self):
        self.conversation.write_text('synthetic old log\n', encoding='utf-8')
        original = Path.write_text
        def denied(path, *args, **kwargs):
            if path == self.conversation:
                raise PermissionError('synthetic startup write denial')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'write_text', denied), self.assertLogs('Agetha', 'WARNING'):
            engine = self.engine()
        self.assertEqual(self.conversation.read_text(), 'synthetic old log\n')
        self.assertEqual(engine._history, [])
        engine._record('User: "synthetic new turn"', 'new answer')
        self.assertTrue(self.conversation.read_text().startswith('synthetic old log\n'))
        self.assertIn('synthetic new turn', self.conversation.read_text())

    def test_provider_summary_writes_related_records_to_three_stores(self):
        engine = self.engine()
        raw = json.dumps({'command': 'speak', 'summary_memory': 'shared synthetic fact'})
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True):
            engine._persist_profile_memory(ai.REQUEST_PROFILES['normal'], 'remember this', raw,
                                           {'command': 'speak', 'mood': 'neutral'})
        self.assertEqual(self.flat.read_text().strip(), 'shared synthetic fact')
        self.assertEqual(memory.get_recent_memories()[0]['summary'], 'shared synthetic fact')
        self.assertEqual(json.loads(self.longterm.read_text())['summary'], 'shared synthetic fact')
        second = self.engine()
        self.assertEqual(second._history, [])
        self.assertEqual(search.search_memories('shared synthetic')[0]['summary'], 'shared synthetic fact')

    def test_repeated_summaries_append_duplicates_without_deduplication(self):
        engine = self.engine()
        raw = json.dumps({'summary_memory': 'repeated synthetic fact'})
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', True):
            for _ in range(2):
                engine._persist_profile_memory(ai.REQUEST_PROFILES['normal'], 'remember this', raw,
                                               {'command': 'speak'})
        self.assertEqual(self.flat.read_text().splitlines(), ['repeated synthetic fact'] * 2)
        self.assertEqual(len(memory.get_recent_memories(0)), 2)
        self.assertEqual(len(self.longterm.read_text().splitlines()), 2)

    def test_module_unavailable_condensation_writes_flat_only(self):
        engine = self.engine()
        engine.HISTORY_LIMIT = 1
        with patch.object(ai, '_MEMORY_SYSTEM_AVAILABLE', False):
            engine._record('User: "synthetic older message"', 'old')
            engine._record('User: "synthetic newer message"', 'new')
        self.assertEqual(self.flat.read_text().strip(), 'synthetic older message.')
        self.assertFalse(self.episodic.exists())
        self.assertFalse(self.longterm.exists())

    def test_large_flat_file_is_fully_read_before_bounded_tail(self):
        content = 'old synthetic summary\n' * 100_000 + 'TAIL_MARKER'
        self.flat.write_text(content, encoding='utf-8')
        engine = self.engine()
        original = Path.read_text
        sizes = []
        def measure(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == self.flat:
                sizes.append(len(result))
            return result
        with patch.object(Path, 'read_text', measure):
            loaded = engine._load_memories(32)
        self.assertEqual(loaded, content[-32:])
        self.assertEqual(sizes, [len(content)])
        self.assertEqual(self.flat.read_text(), content)

    def test_episodic_caps_prune_oldest_but_flat_and_longterm_keep_entries(self):
        engine = self.engine()
        with patch.object(memory, 'EPISODIC_HARD_CAP', 2), patch.object(memory, 'EPISODIC_ENTRY_MAX_CHARS', 12):
            for i in range(3):
                text = f'synthetic-{i}-long-summary'
                engine._save_memory(text)
                memory.log_memory(text)
                search.log_longterm_memory(text)
        self.assertEqual([e['summary'] for e in memory.get_recent_memories(0)],
                         ['synthetic-1-', 'synthetic-2-'])
        self.assertEqual(len(self.flat.read_text().splitlines()), 3)
        self.assertEqual(len(self.longterm.read_text().splitlines()), 3)

    def test_clear_episodic_leaves_legacy_and_longterm_available_on_restart(self):
        self.flat.write_text('same synthetic fact\n', encoding='utf-8')
        self.seed_episodic('same synthetic fact')
        search.log_longterm_memory('same synthetic fact')
        before = {p: p.read_bytes() for p in (self.flat, self.longterm)}
        memory.clear_episodic()  # only patched synthetic path
        self.engine()
        self.assertEqual(memory.get_recent_memories(), [])
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertIn('same synthetic fact', search.format_session_recap_for_prompt())

    def test_corrupt_episodic_read_keeps_bytes_but_next_append_replaces_them(self):
        self.episodic.write_text('[{"summary":"salvage"},', encoding='utf-8')
        original = self.episodic.read_bytes()
        self.assertEqual(memory.get_recent_memories(), [])
        self.assertEqual(self.episodic.read_bytes(), original)
        memory.log_memory('new synthetic summary')
        self.assertEqual([e['summary'] for e in memory.get_recent_memories()], ['new synthetic summary'])
