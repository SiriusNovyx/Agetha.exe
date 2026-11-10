"""Episodic mutation contracts using only temporary synthetic storage."""
import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agetha.core import memory_system as memory


class EpisodicPreservation(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.target = self.directory / 'episodic_memory.json'
        for name, value in (
            ('MEMORY_DIR', self.directory), ('EPISODIC_FILE', self.target),
            ('SOUL_FILE', self.directory / 'soul.md'), ('_soul_cache', None),
            ('EPISODIC_HARD_CAP', 50),
        ):
            self.enterContext(patch.object(memory, name, value))

    def seed(self, data):
        self.target.write_text(json.dumps(data), encoding='utf-8')

    def assert_append_preserves(self, raw):
        self.target.write_bytes(raw)
        before = self.target.stat()
        memory.log_memory('new synthetic fact')
        after = self.target.stat()
        self.assertEqual(self.target.read_bytes(), raw)
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(after.st_size, before.st_size)

    def test_missing_file_accepts_first_entry(self):
        memory.log_memory('first fact')
        self.assertEqual([e['summary'] for e in memory.get_recent_memories()], ['first fact'])

    def test_valid_empty_array_accepts_entry(self):
        self.seed([])
        memory.log_memory('first fact')
        self.assertEqual(len(memory.get_recent_memories()), 1)

    def test_healthy_append_preserves_existing_metadata(self):
        original = {'summary': 'old fact', 'custom': {'version': 1}, 'ts': 'legacy timestamp'}
        self.seed([original])
        memory.log_memory('new fact', source='user', mood='happy')
        result = memory.get_recent_memories()
        self.assertEqual(result[0], original)
        self.assertEqual((result[1]['summary'], result[1]['source'], result[1]['mood']),
                         ('new fact', 'user', 'happy'))

    def test_healthy_append_retains_existing_cap(self):
        self.seed([{'summary': 'one'}, {'summary': 'two'}])
        with patch.object(memory, 'EPISODIC_HARD_CAP', 2):
            memory.log_memory('three')
        self.assertEqual([e['summary'] for e in memory.get_recent_memories()], ['two', 'three'])

    def test_truncated_json_is_not_replaced(self):
        self.assert_append_preserves(b'[{"summary":"salvage"},')

    def test_blank_file_is_not_treated_as_valid_empty_array(self):
        for raw in (b'', b' \n\t'):
            with self.subTest(raw=raw):
                self.assert_append_preserves(raw)

    def test_wrong_json_root_is_not_replaced(self):
        for value in ({'summary': 'salvage'}, None, 2, 'legacy text'):
            with self.subTest(value=value):
                self.assert_append_preserves(json.dumps(value).encode())

    def test_invalid_utf8_is_preserved(self):
        self.assert_append_preserves(b'[{"summary":"salvage\xff"}]')

    def test_nonrecord_rows_are_not_rewritten(self):
        for value in ([None], ['legacy text'], [2], [{'summary': 2}], [{'unknown': 'field'}]):
            with self.subTest(value=value):
                self.assert_append_preserves(json.dumps(value).encode())

    def test_partial_rows_can_be_read_but_cannot_be_rewritten(self):
        original = [{'summary': 'usable fact'}, None]
        self.assert_append_preserves(json.dumps(original).encode())
        self.assertEqual(memory.get_recent_memories(), [{'summary': 'usable fact'}])

    def test_read_denial_refuses_append_without_replacement(self):
        self.seed([{'summary': 'old fact'}])
        before = self.target.read_bytes()
        read = Path.read_text
        def deny(path, *args, **kwargs):
            if path == self.target:
                raise PermissionError('synthetic read denial')
            return read(path, *args, **kwargs)
        with patch.object(Path, 'read_text', deny):
            memory.log_memory('new fact')
        self.assertEqual(self.target.read_bytes(), before)

    def test_recovered_reads_keep_old_entries_on_next_append(self):
        self.seed([{'summary': 'old fact'}])
        with patch.object(Path, 'read_text', side_effect=OSError('synthetic denial')):
            memory.log_memory('refused fact')
        memory.log_memory('accepted fact')
        self.assertEqual([e['summary'] for e in memory.get_recent_memories()], ['old fact', 'accepted fact'])

    def test_deep_json_failure_is_preserved(self):
        self.assert_append_preserves(('[' * 2000 + '0' + ']' * 2000).encode())

    def test_replace_failure_preserves_valid_original(self):
        self.seed([{'summary': 'old fact'}])
        original = self.target.read_bytes()
        with patch('agetha.utils.os.replace', side_effect=OSError('synthetic replace failure')):
            memory.log_memory('new fact')
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(list(self.directory.iterdir()), [self.target])

    def test_readers_do_not_repair_corrupt_data(self):
        self.target.write_bytes(b'{broken')
        with patch.object(memory, '_write_atomic', side_effect=AssertionError('unexpected repair')):
            self.assertEqual(memory.get_recent_memories(), [])
            self.assertEqual(memory.get_memory_stats()['episodic']['count'], 0)
        self.assertEqual(self.target.read_bytes(), b'{broken')

    def test_selective_clear_refuses_partially_valid_data(self):
        self.seed([{'summary': 'usable'}, None])
        original = self.target.read_bytes()
        self.assertEqual(memory.clear_episodic_selective(keep_last=1), 0)
        self.assertEqual(self.target.read_bytes(), original)

    def test_explicit_clear_all_retains_its_authorized_behavior(self):
        self.target.write_bytes(b'{broken')
        memory.clear_episodic()
        self.assertEqual(json.loads(self.target.read_text()), [])

    def test_concurrent_failed_mutations_preserve_original(self):
        self.target.write_bytes(b'{broken')
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(memory.log_memory, ['synthetic fact'] * 8))
        self.assertEqual(self.target.read_bytes(), b'{broken')

    def test_concurrent_healthy_appends_keep_each_entry(self):
        self.seed([])
        values = [f'synthetic fact {i}' for i in range(8)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(memory.log_memory, values))
        self.assertCountEqual([e['summary'] for e in memory.get_recent_memories(limit=0)], values)

    def test_rejected_append_releases_lock_for_repaired_file(self):
        self.target.write_bytes(b'{broken')
        memory.log_memory('refused fact')
        self.seed([])  # Fixture repairs its own synthetic file.
        memory.log_memory('accepted fact')
        self.assertEqual([e['summary'] for e in memory.get_recent_memories()], ['accepted fact'])
