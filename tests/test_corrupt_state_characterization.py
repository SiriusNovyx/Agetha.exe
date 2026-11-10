"""F08 preservation regressions; all bytes and settings are synthetic."""
from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agetha import app_config
from agetha.commands.handlers import memory_presentation
from agetha.commands.handlers.support import DispatchCtx
from agetha.core import companion_stats as stats, read_only_tools
from agetha.features import tasks


class CorruptStateCharacterization(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.task_file = self.root / 'tasks.json'
        self.stats_file = self.root / 'companion_stats.json'
        for module, attr, target in ((tasks, 'TASKS_FILE', self.task_file), (stats, 'STATS_FILE', self.stats_file)):
            self.enterContext(patch.object(module, 'MEMORY_DIR', self.root))
            self.enterContext(patch.object(module, attr, target))
        self.settings = app_config.AppSettings({})
        self.enterContext(patch.object(app_config, 'get_settings', return_value=self.settings))
        self.enterContext(patch.object(stats, '_read_cpu_percent', return_value=0.0))

    def state(self, module):
        with module._lock:
            return module._load_unlocked() if module is tasks else module._load_stats_unlocked()

    def test_missing_files_return_absent_without_creating_files(self):
        self.assertEqual(tasks.get_tasks(), [])
        self.assertEqual(stats.load_stats(), stats._DEFAULTS)
        self.assertEqual(self.state(tasks), ('ABSENT', []))
        self.assertEqual(self.state(stats), ('ABSENT', stats._DEFAULTS))
        self.assertFalse(self.task_file.exists())
        self.assertFalse(self.stats_file.exists())

    def test_valid_files_load_without_rewriting_bytes(self):
        texts = ('[{"id":3,"text":"synthetic task","done":false}]', '{"affection":74,"bytes_devoured":20}')
        for target, text in zip((self.task_file, self.stats_file), texts):
            target.write_text(text, encoding='utf-8')
        self.assertEqual(tasks.get_tasks()[0]['id'], 3)
        self.assertEqual(stats.load_stats()['affection'], 74)
        for module, target, text in zip((tasks, stats), (self.task_file, self.stats_file), texts):
            self.assertEqual(self.state(module)[0], 'VALID')
            self.assertEqual(target.read_text(), text)

    def test_empty_malformed_truncated_and_wrong_root_preserve_bytes_on_read(self):
        for module, target, loader, texts in (
            (tasks, self.task_file, tasks.get_tasks, ('', 'not-json', '[{"id":1,"text":"salvage"},', '{}')),
            (stats, self.stats_file, stats.load_stats, ('', '{broken', '{"affection":75,', '[]')),
        ):
            for text in texts:
                with self.subTest(file=target.name, input=text):
                    target.write_text(text, encoding='utf-8')
                    before = (target.read_bytes(), target.stat().st_mtime_ns)
                    result = loader()
                    self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)
                    self.assertEqual(result, [] if module is tasks else stats._DEFAULTS)
                    self.assertEqual(self.state(module)[0], 'CORRUPT')

    def test_mixed_task_rows_remain_readable_but_cannot_be_rewritten(self):
        original = '[{"id":1,"text":"keep"},null,{"id":2,"text":""}]'
        self.task_file.write_text(original, encoding='utf-8')
        self.assertEqual([t['text'] for t in tasks.get_tasks()], ['keep'])
        self.assertIsNone(tasks.add_task('new'))
        self.assertIsNone(tasks.complete_task(1))
        self.assertEqual(self.state(tasks)[0], 'RECOVERABLE')
        self.assertEqual(self.task_file.read_text(), original)

    def test_invalid_task_id_recovers_other_rows_without_discarding_original(self):
        original = '[{"id":"bad","text":"bad id"},{"id":2,"text":"good"}]'
        self.task_file.write_text(original, encoding='utf-8')
        self.assertEqual([t['text'] for t in tasks.get_tasks()], ['good'])
        self.assertEqual(self.state(tasks)[0], 'RECOVERABLE')
        self.assertIsNone(tasks.complete_task(2))
        self.assertEqual(self.task_file.read_text(), original)

    def test_all_invalid_task_rows_are_corrupt_and_unwritable(self):
        for text in ('[null]', '[{"text":42}]', '[{"id":1,"text":"ok","done":"false"}]'):
            with self.subTest(input=text):
                self.task_file.write_text(text, encoding='utf-8')
                self.assertEqual(tasks.get_tasks(), [])
                self.assertEqual(self.state(tasks)[0], 'CORRUPT')
                self.assertIsNone(tasks.add_task('new'))
                self.assertEqual(self.task_file.read_text(), text)

    def test_invalid_stats_fields_keep_good_values_without_allowing_save(self):
        original = '{"affection":"bad","entropy":"71","max_infection_reached":"false","extra":"keep"}'
        self.stats_file.write_text(original, encoding='utf-8')
        loaded = stats.load_stats()
        self.assertEqual(loaded['affection'], 50)
        self.assertEqual(loaded['entropy'], 71)
        self.assertIs(loaded['max_infection_reached'], False)
        self.assertEqual(self.state(stats)[0], 'RECOVERABLE')
        self.assertFalse(stats.save_stats(loaded))
        stats.update_stats('user_polite')
        self.assertEqual(self.stats_file.read_text(), original)

    def test_overflow_and_nonfinite_stats_keep_original_and_usable_fields(self):
        for text in ('{"affection":79,"bytes_devoured":1e999}', '{"affection":79,"entropy":NaN}',
                     '{"affection":79,"core_heat":Infinity}'):
            with self.subTest(input=text):
                self.stats_file.write_text(text, encoding='utf-8')
                self.assertEqual(stats.load_stats()['affection'], 79)
                self.assertEqual(self.state(stats)[0], 'RECOVERABLE')
                stats.update_stats('command')
                self.assertEqual(self.stats_file.read_text(), text)

    def test_read_permission_errors_do_not_attempt_replacement(self):
        original_read = Path.read_text
        for module, target, loader in ((tasks, self.task_file, tasks.get_tasks), (stats, self.stats_file, stats.load_stats)):
            with self.subTest(file=target.name):
                target.write_text('unreadable synthetic evidence', encoding='utf-8')
                before = target.read_bytes()
                def denied(path, *args, **kwargs):
                    if path == target:
                        raise PermissionError('synthetic read denial')
                    return original_read(path, *args, **kwargs)
                with patch.object(Path, 'read_text', denied), patch.object(module, 'write_atomic') as writer:
                    self.assertEqual(loader(), [] if module is tasks else stats._DEFAULTS)
                    self.assertEqual(self.state(module)[0], 'UNREADABLE')
                    if module is tasks:
                        self.assertIsNone(tasks.add_task('new'))
                    else:
                        self.assertFalse(stats.save_stats(stats._DEFAULTS))
                        stats.update_stats('tick')
                    writer.assert_not_called()
                self.assertEqual(target.read_bytes(), before)

    def test_invalid_utf8_is_corrupt_and_preserved_byte_for_byte(self):
        for module, target in ((tasks, self.task_file), (stats, self.stats_file)):
            with self.subTest(file=target.name):
                target.write_bytes(b'\xffsynthetic invalid utf8')
                self.assertEqual(tasks.get_tasks() if module is tasks else stats.load_stats(),
                                 [] if module is tasks else stats._DEFAULTS)
                self.assertEqual(self.state(module)[0], 'CORRUPT')
                self.assertEqual(target.read_bytes(), b'\xffsynthetic invalid utf8')

    def test_excessive_json_nesting_is_corrupt_and_preserved(self):
        original = b'[' * 5000 + b'0' + b']' * 5000
        for module, target, loader in ((tasks, self.task_file, tasks.get_tasks),
                                       (stats, self.stats_file, stats.load_stats)):
            with self.subTest(file=target.name):
                target.write_bytes(original)
                self.assertEqual(loader(), [] if module is tasks else stats._DEFAULTS)
                self.assertEqual(self.state(module)[0], 'CORRUPT')
                if module is tasks:
                    self.assertIsNone(tasks.add_task('new'))
                else:
                    self.assertFalse(stats.save_stats({'affection': 80}))
                    stats.update_stats('tick')
                self.assertEqual(target.read_bytes(), original)

    def test_corruption_reads_never_invoke_repair_even_if_writer_would_fail(self):
        for module, target, loader in ((tasks, self.task_file, tasks.get_tasks), (stats, self.stats_file, stats.load_stats)):
            with self.subTest(file=target.name):
                target.write_text('damaged', encoding='utf-8')
                with patch.object(module, 'write_atomic', side_effect=OSError('disk full')) as writer:
                    for _ in range(3):
                        self.assertEqual(loader(), [] if module is tasks else stats._DEFAULTS)
                    writer.assert_not_called()
                self.assertEqual(target.read_text(), 'damaged')

    def test_failed_replace_of_valid_state_preserves_original(self):
        self.task_file.write_text('[{"id":1,"text":"keep"}]', encoding='utf-8')
        self.stats_file.write_text('{"affection":79}', encoding='utf-8')
        before = {p: p.read_bytes() for p in (self.task_file, self.stats_file)}
        with patch('agetha.utils.os.replace', side_effect=OSError('synthetic replace failure')):
            self.assertIsNone(tasks.add_task('new'))
            self.assertFalse(stats.save_stats({'affection': 80}))
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(list(self.root.glob('.*.tmp')), [])

    def test_failed_write_of_valid_state_preserves_original(self):
        self.task_file.write_text('[{"id":1,"text":"keep"}]', encoding='utf-8')
        self.stats_file.write_text('{"affection":79}', encoding='utf-8')
        before = {p: p.read_bytes() for p in (self.task_file, self.stats_file)}
        with patch.object(tasks, 'write_atomic', side_effect=OSError('disk full')):
            self.assertIsNone(tasks.complete_task(1))
        with patch.object(stats, 'write_atomic', side_effect=OSError('disk full')):
            self.assertFalse(stats.save_stats({'affection': 80}))
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_concurrent_corrupt_loads_and_mutations_preserve_original(self):
        for module, target, loader in ((tasks, self.task_file, tasks.get_tasks), (stats, self.stats_file, stats.load_stats)):
            with self.subTest(file=target.name):
                target.write_text('damaged', encoding='utf-8')
                barrier = threading.Barrier(6)
                def load(index):
                    barrier.wait(timeout=5)
                    if index % 2:
                        if module is tasks:
                            tasks.add_task('new')
                        else:
                            stats.update_stats('tick')
                    return loader()
                with patch.object(module, 'write_atomic', wraps=module.write_atomic) as writer:
                    with ThreadPoolExecutor(max_workers=6) as pool:
                        results = list(pool.map(load, range(6)))
                self.assertTrue(all(r == results[0] for r in results))
                self.assertEqual(results[0], [] if module is tasks else stats._DEFAULTS)
                writer.assert_not_called()
                self.assertEqual(target.read_text(), 'damaged')

    def test_later_disk_corruption_cannot_be_hidden_by_stats_cache(self):
        self.stats_file.write_text('{"affection":79}', encoding='utf-8')
        self.assertEqual(stats.load_stats()['affection'], 79)
        self.stats_file.write_text('damaged after valid read', encoding='utf-8')
        self.assertEqual(stats.load_stats(), stats._DEFAULTS)
        stats.update_stats('tick')
        self.assertFalse(stats.save_stats({'affection': 80}))
        self.assertEqual(self.stats_file.read_text(), 'damaged after valid read')

    def test_continuation_task_reader_preserves_corrupt_bytes_and_reports_unreadable(self):
        self.task_file.write_text('[{"text":"salvage"},', encoding='utf-8')
        original = self.task_file.read_bytes()
        self.assertEqual(read_only_tools._default_list_tasks(), ['[task list is unreadable]'])
        self.assertEqual(self.task_file.read_bytes(), original)

    def test_strict_task_read_reports_unavailable_instead_of_empty(self):
        self.task_file.write_text('damaged', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'CORRUPT'):
            tasks.get_tasks(require_available=True)
        self.assertEqual(self.task_file.read_text(), 'damaged')

    def test_missing_file_mutations_create_valid_state(self):
        self.assertIsNotNone(tasks.add_task('first synthetic task'))
        self.assertTrue(stats.save_stats({'affection': 80}))
        self.assertEqual(tasks.get_tasks()[0]['text'], 'first synthetic task')
        self.assertEqual(stats.load_stats()['affection'], 80)

    def test_valid_concurrent_mutations_retain_all_task_and_stats_updates(self):
        self.task_file.write_text('[]', encoding='utf-8')
        self.stats_file.write_text('{"affection":50}', encoding='utf-8')
        barrier = threading.Barrier(6)
        def mutate(index):
            barrier.wait(timeout=5)
            record = tasks.add_task(f'synthetic task {index}')
            stats.update_stats('user_polite')
            return record
        with ThreadPoolExecutor(max_workers=6) as pool:
            records = list(pool.map(mutate, range(6)))
        self.assertTrue(all(record is not None for record in records))
        self.assertEqual({record['id'] for record in records}, set(range(1, 7)))
        self.assertEqual(len(tasks.get_tasks()), 6)
        self.assertEqual(stats.load_stats()['affection'], 68)

    def test_external_valid_replacement_is_seen_without_sticky_unavailability(self):
        self.task_file.write_text('damaged tasks', encoding='utf-8')
        self.stats_file.write_text('damaged stats', encoding='utf-8')
        self.assertIsNone(tasks.add_task('blocked'))
        self.assertFalse(stats.save_stats({'affection': 80}))
        self.assertEqual(self.task_file.read_text(), 'damaged tasks')
        self.assertEqual(self.stats_file.read_text(), 'damaged stats')
        # A fixture provides valid bytes; production performs no repair.
        self.task_file.write_text('[]', encoding='utf-8')
        self.stats_file.write_text('{"affection":79}', encoding='utf-8')
        self.assertIsNotNone(tasks.add_task('allowed'))
        stats.update_stats('user_polite')
        self.assertEqual(tasks.get_tasks()[0]['text'], 'allowed')
        self.assertEqual(stats.load_stats()['affection'], 82)

    def test_invalid_save_input_does_not_replace_valid_stats(self):
        self.stats_file.write_text('{"affection":79}', encoding='utf-8')
        before = self.stats_file.read_bytes()
        self.assertFalse(stats.save_stats({'affection': 'invalid'}))
        self.assertEqual(stats.load_stats()['affection'], 79)
        self.assertEqual(self.stats_file.read_bytes(), before)

    def test_task_prompt_degrades_without_writing_damaged_file(self):
        self.task_file.write_text('damaged', encoding='utf-8')
        self.assertEqual(tasks.format_tasks_for_prompt(), '')
        self.assertEqual(tasks.get_pending_count(), 0)
        self.assertEqual(self.task_file.read_text(), 'damaged')

    def test_stats_prompt_and_perk_degrade_without_writing_damaged_file(self):
        self.stats_file.write_text('damaged', encoding='utf-8')
        self.assertIn('infection: 0%', stats.format_stats_for_prompt())
        self.assertFalse(stats.infection_perk_active())
        self.assertEqual(self.stats_file.read_text(), 'damaged')

    def test_list_task_handler_reports_persistence_unavailable(self):
        self.task_file.write_text('damaged', encoding='utf-8')
        callbacks, shown = [], []
        app = SimpleNamespace(root=object(), _speak_and_continue=lambda *args: None)
        ctx = DispatchCtx(None, 'neutral', [], False)
        with (
            patch.object(memory_presentation, 'get_settings', return_value=self.settings),
            patch.object(memory_presentation, '_schedule_app_ui', side_effect=lambda app, cb: callbacks.append(cb)),
            patch('main.AgethaPopup', side_effect=lambda root, lines, mood: shown.extend(lines)),
        ):
            memory_presentation.handle_list_tasks(app, {}, ctx)
            for callback in callbacks:
                callback()
        self.assertTrue(any('CORRUPT' in line for line in shown), shown)
        self.assertFalse(any('no tasks' in line for line in shown))
        self.assertEqual(self.task_file.read_text(), 'damaged')

    def test_task_mutation_handlers_report_failure_and_preserve_corruption(self):
        for handler, response in ((memory_presentation.handle_add_task, {'text': 'new'}),
                                  (memory_presentation.handle_complete_task, {'task_id': 1})):
            with self.subTest(handler=handler.__name__):
                self.task_file.write_text('damaged', encoding='utf-8')
                callbacks, errors, successes = [], [], []
                app = SimpleNamespace(_speak_and_continue=lambda *args: None,
                                      _show_op_error=errors.append, _show_op_success=successes.append)
                ctx = DispatchCtx(None, 'neutral', [], False)
                with (
                    patch.object(memory_presentation, 'get_settings', return_value=self.settings),
                    patch.object(memory_presentation, '_schedule_app_ui',
                                 side_effect=lambda app, cb: callbacks.append(cb)),
                ):
                    self.assertTrue(handler(app, response, ctx))
                    for callback in callbacks:
                        callback()
                self.assertEqual(len(errors), 1)
                self.assertEqual(successes, [])
                self.assertEqual(self.task_file.read_text(), 'damaged')
