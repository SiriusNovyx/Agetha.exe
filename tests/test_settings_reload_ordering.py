"""Reload ordering against synthetic files. Run only in a disposable source copy.

utils initializes settings during import, before test fixtures can isolate paths.
"""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from agetha import app_config as config
from agetha import utils
from agetha.core import fast_mode_profile as fast


class SettingsReloadOrdering(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / 'config.txt'
        self.environment = self.root / 'empty-overrides.txt'
        self.environment.write_text('', encoding='utf-8')
        self.releases = []
        self.threads = []
        self.results = {}
        self.errors = {}
        for module, name, value in (
            (config, 'BASE_DIR', self.root), (config, 'CONFIG_PATH', self.path),
            (config, 'ENV_PATH', self.environment), (config, '_settings', None),
            (config, '_last_load', None), (fast, 'CONFIG_PATH', self.path),
            (fast, '_SNAPSHOT_CACHE', {}), (fast, '_ACTIVE_CACHE', set()),
        ):
            self.enterContext(patch.object(module, name, value))
        for name in ('_settings_reload_generation', '_settings_published_generation'):
            if hasattr(config, name):
                self.enterContext(patch.object(config, name, 0))
        for name in ('TOUCH_COOLDOWN_SEC', 'WAKE_DELAY_MS', 'LOAF_TIMER_MS', 'SCREEN_POLL_INTERVAL_MS'):
            self.enterContext(patch.object(utils, name, getattr(utils, name)))

    def tearDown(self):
        # Release/join while every path/cache patch is still installed.
        for event in self.releases:
            event.set()
        for worker in self.threads:
            worker.join(5)
            self.assertFalse(worker.is_alive(), 'test worker did not stop')

    def write(self, **values):
        self.path.write_text(config.render_config_document(config.DEFAULT_CONFIG, values), encoding='utf-8')

    def start(self, name, operation=None):
        def run():
            try:
                self.results[name] = (operation or (lambda: config.get_settings(reload=True)))()
            except Exception as error:
                self.errors[name] = error
        worker = threading.Thread(target=run, name=name)
        self.threads.append(worker)
        worker.start()
        return worker

    def finish(self, worker):
        worker.join(5)
        self.assertFalse(worker.is_alive(), 'operation blocked')
        self.assertNotIn(worker.name, self.errors)
        return self.results[worker.name]

    def held_loads(self, *names):
        reader = config.parse_config_file
        gates = {name: (threading.Event(), threading.Event()) for name in names}
        self.releases.extend(release for _, release in gates.values())
        def load(*args, **kwargs):
            values = reader(*args, **kwargs)
            gate = gates.get(threading.current_thread().name)
            if gate:
                entered, release = gate
                entered.set()
                if not release.wait(5):
                    raise AssertionError('held loader was not released')
            return values
        self.enterContext(patch.object(config, 'parse_config_file', side_effect=load))
        return gates

    def test_single_reload_updates_lookup_and_retains_old_consumer_snapshot(self):
        self.write(AI_MAX_TOKENS='300')
        before = config.get_settings()
        self.write(AI_MAX_TOKENS='700')
        after = config.get_settings(reload=True)
        self.assertEqual(after.ai_max_tokens, 700)
        self.assertIs(config.get_settings(), after)
        self.assertEqual(before.ai_max_tokens, 300)

    def test_older_reload_cannot_overwrite_newer_publication(self):
        self.write(AI_MAX_TOKENS='200')
        config.get_settings()
        self.write(AI_MAX_TOKENS='300')
        entered, release = self.held_loads('older')['older']
        older = self.start('older')
        self.assertTrue(entered.wait(5))
        self.write(AI_MAX_TOKENS='700')
        newer = config.get_settings(reload=True)
        self.assertEqual(newer.ai_max_tokens, 700)
        release.set()
        stale_caller_result = self.finish(older)
        self.assertEqual(config.get_settings().ai_max_tokens, 700)
        self.assertIs(config.get_settings(), newer)
        self.assertIs(stale_caller_result, newer)

    def test_older_reload_can_publish_first_while_newer_is_loading(self):
        self.write(AI_MAX_TOKENS='300')
        gates = self.held_loads('older', 'newer')
        older = self.start('older')
        self.assertTrue(gates['older'][0].wait(5))
        self.write(AI_MAX_TOKENS='700')
        newer = self.start('newer')
        self.assertTrue(gates['newer'][0].wait(5))
        gates['older'][1].set()
        first = self.finish(older)
        self.assertEqual(first.ai_max_tokens, 300)
        self.assertIs(config.get_settings(), first)
        gates['newer'][1].set()
        second = self.finish(newer)
        self.assertEqual(second.ai_max_tokens, 700)
        self.assertIs(config.get_settings(), second)

    def test_three_overlapping_reloads_keep_latest_successful_publication(self):
        self.write(AI_MAX_TOKENS='300')
        gates = self.held_loads('first', 'second')
        first = self.start('first')
        self.assertTrue(gates['first'][0].wait(5))
        self.write(AI_MAX_TOKENS='700')
        second = self.start('second')
        self.assertTrue(gates['second'][0].wait(5))
        self.write(AI_MAX_TOKENS='900')
        newest = config.get_settings(reload=True)
        for worker in (second, first):
            gates[worker.name][1].set()
            self.assertIs(self.finish(worker), newest)
        self.assertEqual(config.get_settings().ai_max_tokens, 900)

    def test_repeated_sequential_reloads_preserve_final_order(self):
        retained = []
        for value in ('300', '400', '700'):
            self.write(AI_MAX_TOKENS=value)
            retained.append(config.get_settings(reload=True))
        self.assertEqual([s.ai_max_tokens for s in retained], [300, 400, 700])
        self.assertIs(config.get_settings(), retained[-1])

    def test_loader_exception_preserves_publication_and_allows_next_reload(self):
        self.write(AI_MAX_TOKENS='300')
        before = config.get_settings()
        with patch.object(config, 'parse_config_file', side_effect=OSError('synthetic read failure')):
            with self.assertRaises(OSError):
                config.get_settings(reload=True)
        self.assertIs(config.get_settings(), before)
        self.write(AI_MAX_TOKENS='700')
        self.assertEqual(self.finish(self.start('after-error')).ai_max_tokens, 700)

    def test_constructor_exception_preserves_publication_and_allows_next_reload(self):
        self.write(AI_MAX_TOKENS='300')
        before = config.get_settings()
        with patch.object(config, 'AppSettings', side_effect=ValueError('synthetic construction failure')):
            with self.assertRaises(ValueError):
                config.get_settings(reload=True)
        self.assertIs(config.get_settings(), before)
        self.write(AI_MAX_TOKENS='700')
        self.assertEqual(self.finish(self.start('after-construction-error')).ai_max_tokens, 700)

    def test_invalid_document_publishes_complete_validated_fallback(self):
        self.write(AI_MAX_TOKENS='300', ENABLE_WEB_RAG='yes')
        before = config.get_settings()
        self.path.write_text('AI_MAX_TOKENS=invalid\nENABLE_WEB_RAG=invalid\nWAKE_DELAY_SEC=4\n', encoding='utf-8')
        after = config.get_settings(reload=True)
        self.assertEqual(after.ai_max_tokens, 400)
        self.assertFalse(after.enable_web_rag)
        self.assertEqual(after.wake_delay_ms, 4000)
        self.assertEqual(before.ai_max_tokens, 300)
        self.assertIn('invalid', self.path.read_text(encoding='utf-8'))

    def test_cached_reads_do_not_wait_for_inflight_reload(self):
        self.write(AI_MAX_TOKENS='200')
        before = config.get_settings()
        self.write(AI_MAX_TOKENS='700')
        entered, release = self.held_loads('reload')['reload']
        worker = self.start('reload')
        self.assertTrue(entered.wait(5))
        cached_reader = self.start('cached-reader', config.get_settings)
        self.assertIs(self.finish(cached_reader), before)
        release.set()
        self.assertEqual(self.finish(worker).ai_max_tokens, 700)

    def test_concurrent_first_load_does_not_restore_stale_initial_settings(self):
        self.write(AI_MAX_TOKENS='300')
        entered, release = self.held_loads('first')['first']
        first = self.start('first', config.get_settings)
        self.assertTrue(entered.wait(5))
        self.write(AI_MAX_TOKENS='700')
        winner = config.get_settings()
        self.assertEqual(winner.ai_max_tokens, 700)
        release.set()
        self.assertIs(self.finish(first), winner)
        self.assertEqual(config.get_settings().ai_max_tokens, 700)

    def test_newer_failed_reload_does_not_discard_older_success(self):
        self.write(AI_MAX_TOKENS='200')
        config.get_settings()
        self.write(AI_MAX_TOKENS='300')
        entered, release = self.held_loads('older')['older']
        older = self.start('older')
        self.assertTrue(entered.wait(5))
        with patch.object(config, 'parse_config_file', side_effect=OSError('newer failed')):
            with self.assertRaises(OSError):
                config.get_settings(reload=True)
        release.set()
        self.assertEqual(self.finish(older).ai_max_tokens, 300)
        self.assertEqual(config.get_settings().ai_max_tokens, 300)
        self.write(AI_MAX_TOKENS='700')
        self.assertEqual(config.get_settings(reload=True).ai_max_tokens, 700)

    def test_fast_transition_can_publish_while_older_reload_is_held(self):
        self.write(FASTER_MODE='no', AI_MAX_TOKENS='600')
        before = config.get_settings()
        entered, release = self.held_loads('before-fast')['before-fast']
        worker = self.start('before-fast')
        self.assertTrue(entered.wait(5))
        snapshot = self.root / 'memory' / fast.FAST_MODE_SNAPSHOT_NAME
        self.assertTrue(fast.activate_fast_mode(self.path, snapshot).ok)
        winner = config.get_settings(reload=True)
        self.assertTrue(winner.faster_mode)
        self.assertEqual(winner.ai_max_tokens, 220)
        release.set()
        self.assertIs(self.finish(worker), winner)
        self.assertEqual(before.ai_max_tokens, 600)
        self.assertEqual(fast.get_fast_mode_original_value('AI_MAX_TOKENS', self.path, snapshot), '600')
        self.assertTrue(fast.deactivate_fast_mode(self.path, snapshot).ok)
        restored = config.get_settings(reload=True)
        self.assertFalse(restored.faster_mode)
        self.assertEqual(restored.ai_max_tokens, 600)

    def test_compact_recovery_reload_cannot_be_undone_by_older_full_read(self):
        self.write(COMPACT_MODE='no')
        before = config.get_settings()
        entered, release = self.held_loads('old-full')['old-full']
        worker = self.start('old-full')
        self.assertTrue(entered.wait(5))
        self.assertTrue(config.arm_compact_mode_fail_closed())
        winner = config.get_settings(reload=True)
        self.assertTrue(winner.compact_mode)
        release.set()
        self.assertIs(self.finish(worker), winner)
        self.assertTrue(config.get_settings().compact_mode)
        self.assertFalse(before.compact_mode)

    def timings(self):
        return (utils.TOUCH_COOLDOWN_SEC, utils.WAKE_DELAY_MS,
                utils.LOAF_TIMER_MS, utils.SCREEN_POLL_INTERVAL_MS)

    def test_competing_compatibility_refreshes_use_winning_reload(self):
        self.write(TOUCH_COOLDOWN_SEC='2', WAKE_DELAY_SEC='1', LOAF_TIMER_MIN='2', SCREEN_POLL_INTERVAL_SEC='30')
        entered, release = self.held_loads('old-refresh')['old-refresh']
        worker = self.start('old-refresh', utils.refresh_config_constants)
        self.assertTrue(entered.wait(5))
        self.write(TOUCH_COOLDOWN_SEC='6', WAKE_DELAY_SEC='4', LOAF_TIMER_MIN='3', SCREEN_POLL_INTERVAL_SEC='90')
        utils.refresh_config_constants()
        self.assertEqual(self.timings(), (6.0, 4000, 180000, 90000))
        release.set()
        self.finish(worker)
        self.assertEqual(self.timings(), (6.0, 4000, 180000, 90000))
        self.assertEqual(config.get_settings().wake_delay_ms, 4000)

    def test_delayed_compatibility_refresh_cannot_install_returned_old_snapshot(self):
        self.write(TOUCH_COOLDOWN_SEC='2', WAKE_DELAY_SEC='1', LOAF_TIMER_MIN='2', SCREEN_POLL_INTERVAL_SEC='30')
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        getter = config.get_settings
        def held_return(reload=False):
            settings = getter(reload=reload)
            if reload and threading.current_thread().name == 'old-refresh':
                entered.set()
                if not release.wait(5):
                    raise AssertionError('refresh not released')
            return settings
        self.enterContext(patch.object(utils, 'get_settings', side_effect=held_return))
        worker = self.start('old-refresh', utils.refresh_config_constants)
        self.assertTrue(entered.wait(5))
        self.write(TOUCH_COOLDOWN_SEC='6', WAKE_DELAY_SEC='4', LOAF_TIMER_MIN='3', SCREEN_POLL_INTERVAL_SEC='90')
        utils.refresh_config_constants()
        release.set()
        self.finish(worker)
        self.assertEqual(config.get_settings().wake_delay_ms, 4000)
        self.assertEqual(self.timings(), (6.0, 4000, 180000, 90000))

    def test_plain_reload_keeps_existing_explicit_compatibility_refresh_contract(self):
        self.write(WAKE_DELAY_SEC='1')
        utils.refresh_config_constants()
        self.write(WAKE_DELAY_SEC='4')
        self.assertEqual(config.get_settings(reload=True).wake_delay_ms, 4000)
        self.assertEqual(utils.WAKE_DELAY_MS, 1000)
        utils.refresh_config_constants()
        self.assertEqual(utils.WAKE_DELAY_MS, 4000)

    def test_profile_cache_reader_does_not_block_configuration_writer(self):
        self.write(FASTER_MODE='yes', AI_MAX_TOKENS='300')
        cache_held, reader_entered = threading.Event(), threading.Event()
        writer = None
        original_overlay = fast.get_fast_mode_runtime_overrides
        def overlay(**kwargs):
            reader_entered.set()
            return original_overlay(**kwargs)
        def write_while_cache_held():
            with fast._CACHE_LOCK:
                cache_held.set()
                if not reader_entered.wait(5):
                    raise AssertionError('reader did not reach profile cache')
                text = config.render_config_document(config.DEFAULT_CONFIG, {'AI_MAX_TOKENS': '700'})
                # Bound the fixture even if a future loader holds this lock
                # while waiting for the cache. RLock lets the real writer enter.
                if not config._CONFIG_WRITE_LOCK.acquire(timeout=3):
                    raise AssertionError('configuration writer blocked by cache reader')
                try:
                    config.write_config_document(self.path, text)
                finally:
                    config._CONFIG_WRITE_LOCK.release()
        self.enterContext(patch.object(fast, 'get_fast_mode_runtime_overrides', side_effect=overlay))
        writer = self.start('profile-writer', write_while_cache_held)
        self.assertTrue(cache_held.wait(5))
        reader = self.start('profile-reader')
        self.finish(writer)
        self.finish(reader)
        self.assertEqual(config.get_settings(reload=True).ai_max_tokens, 700)


if __name__ == '__main__':
    unittest.main()
