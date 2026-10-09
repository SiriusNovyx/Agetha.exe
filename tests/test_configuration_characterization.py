"""F10 configuration contracts against disposable config and inert services.

These tests record current behavior, including disagreements. They do not
approve those disagreements as the desired future configuration model.
Collect and run this module only in a disposable source copy: imported startup
consumers initialize settings before per-test fixtures begin.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agetha import app_config as config
from agetha import utils
from agetha.commands.command_guard import CommandGuard
from agetha.core import ai_engine as ai
from agetha.core import fast_mode_profile as fast
from agetha.core.capabilities import Capability, CapabilityController, CapabilityPolicy
from agetha.features import tts_player
from agetha.ui import dashboard
from tests import test_capability_main_integration as lifecycle

main = lifecycle.main


class ConfigurationCharacterization(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.root / 'config.txt'
        self.snapshot = self.root / 'memory' / fast.FAST_MODE_SNAPSHOT_NAME
        self.environment = self.root / 'empty-overrides.txt'
        self.environment.write_text('', encoding='utf-8')
        for module, name, value in (
            (config, 'BASE_DIR', self.root), (config, 'CONFIG_PATH', self.path),
            (config, 'ENV_PATH', self.environment), (config, '_settings', None),
            (config, '_last_load', None), (fast, 'CONFIG_PATH', self.path),
            (fast, '_SNAPSHOT_CACHE', {}), (fast, '_ACTIVE_CACHE', set()),
            (utils, 'CONFIG_PATH', self.path), (utils, 'ENV_PATH', self.environment),
        ):
            self.enterContext(patch.object(module, name, value))
        for name in ('TOUCH_COOLDOWN_SEC', 'WAKE_DELAY_MS', 'LOAF_TIMER_MS', 'SCREEN_POLL_INTERVAL_MS'):
            self.enterContext(patch.object(utils, name, getattr(utils, name)))
        self.enterContext(patch.object(ai.AIEngine, '_resolve_config_path', return_value=self.path))
        self.enterContext(patch.object(ai, 'native_error_popup', side_effect=AssertionError('native UI forbidden')))
        self.enterContext(patch.object(ai.AIEngine, '_ensure_provider_initialized', side_effect=AssertionError('provider forbidden')))

    def write(self, **updates):
        # Source defaults, not the user's config. Providers remain deferred.
        text = config.render_config_document(config.DEFAULT_CONFIG, updates)
        self.path.write_text(text, encoding='utf-8')

    def engine(self):
        return ai.AIEngine(defer_provider_init=True)

    def app(self, settings):
        return lifecycle.TestMainCapabilityLifecycle._app(settings)

    def ready_full_consent(self, app):
        app._capability_transition_generation = app._capabilities.begin_full_transition()
        flow = app._capability_consent
        generation = flow.begin_enable().generation
        flow.confirm_first(generation)
        flow.finish_demo(generation)
        app._start_full_mode_services = Mock()
        app._show_op_error = Mock()
        app._refresh_dashboard_after_profile_commit = Mock()
        return generation

    def test_parse_missing_config_uses_defaults_without_creating_storage(self):
        values = config.parse_config_file()
        self.assertEqual(values['AI_MAX_TOKENS'], '400')
        self.assertEqual(values['COMPACT_MODE'], 'yes')
        self.assertTrue(config.get_last_config_load().file_missing)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.snapshot.exists())

    def test_get_settings_before_application_initialization_creates_default_config(self):
        settings = config.get_settings()
        self.assertTrue(self.path.is_file())
        self.assertEqual(self.path.read_text(encoding='utf-8'), config.DEFAULT_CONFIG)
        self.assertEqual(settings.ai_max_tokens, 400)
        self.assertTrue(settings.compact_mode)
        self.assertFalse((self.root / 'memory').exists())

    def test_existing_document_is_preserved_with_last_duplicate_and_unknown_values(self):
        original = '# synthetic\nAI_MAX_TOKENS = 300\nai_max_tokens=700\nPLUGIN_FLAG=x\n'
        self.path.write_text(original, encoding='utf-8')
        settings = config.get_settings()
        self.assertEqual(settings.ai_max_tokens, 700)
        self.assertEqual(settings.get('PLUGIN_FLAG'), 'x')
        self.assertTrue(settings.compact_mode)
        self.assertEqual(self.path.read_text(encoding='utf-8'), original)

    def test_reload_replaces_cached_view_without_updating_retained_snapshot(self):
        self.write(AI_MAX_TOKENS='300')
        before = config.get_settings()
        self.write(AI_MAX_TOKENS='700')
        self.assertEqual(config.get_settings().ai_max_tokens, 300)
        after = config.get_settings(reload=True)
        self.assertEqual(after.ai_max_tokens, 700)
        self.assertEqual(before.ai_max_tokens, 300)
        self.assertEqual(config.get_settings().ai_max_tokens, 700)

    def test_early_bootstrap_adds_stable_defaults_without_rebinding_main_snapshot(self):
        self.path.write_text('AI_MAX_TOKENS=300\n', encoding='utf-8')
        old = config.get_settings()
        self.enterContext(patch.object(main, '_SETTINGS', old))
        self.path.write_text('AI_MAX_TOKENS=700\n', encoding='utf-8')
        with patch.object(main, '_warn_if_no_api_key'):
            main._early_config_check()
        self.assertEqual(config.get_settings().ai_max_tokens, 700)
        self.assertEqual(main._SETTINGS.ai_max_tokens, 300)
        raw = config.read_config_document(self.path)[1]
        self.assertTrue(set(config.SETTING_SPECS).issubset(raw))

    def test_missing_values_use_defaults_without_inserting_them_on_regular_load(self):
        self.path.write_text('PLUGIN_FLAG=keep\n', encoding='utf-8')
        old = self.path.read_bytes()
        settings = config.get_settings()
        self.assertEqual(settings.ai_max_tokens, 400)
        self.assertEqual(settings.screen_poll_interval_ms, 120000)
        self.assertEqual(self.path.read_bytes(), old)

    def test_invalid_typed_values_fall_back_and_numeric_ranges_clamp_without_disk_repair(self):
        self.path.write_text('AI_MAX_TOKENS=bad\nENABLE_VOICE=perhaps\nOCR_MAX_DIMENSION=1\n', encoding='utf-8')
        original = self.path.read_bytes()
        settings = config.get_settings()
        self.assertEqual(settings.ai_max_tokens, 400)
        self.assertEqual(settings.enable_voice, config.AppSettings({}).enable_voice)
        self.assertEqual(settings.ocr_max_dimension, 640)
        self.assertEqual(set(config.get_last_config_load().invalid_keys), {'AI_MAX_TOKENS', 'ENABLE_VOICE'})
        self.assertEqual(self.path.read_bytes(), original)

    def test_generic_patch_accepts_structurally_valid_invalid_value_and_loader_falls_back(self):
        self.write(AI_MAX_TOKENS='300')
        self.assertTrue(config.patch_config_key('AI_MAX_TOKENS', 'bad'))
        self.assertEqual(config.read_config_document(self.path)[1]['AI_MAX_TOKENS'], 'bad')
        self.assertEqual(config.get_settings().ai_max_tokens, 400)

    def test_raw_mapping_is_mutable_but_an_existing_capability_policy_stays_derived(self):
        self.write(COMPACT_MODE='no', ENABLE_WEB_RAG='no')
        settings = config.get_settings()
        policy = CapabilityPolicy.from_settings(settings)
        original = self.path.read_bytes()
        settings.raw['ENABLE_WEB_RAG'] = 'yes'
        self.assertTrue(config.get_settings().enable_web_rag)
        self.assertFalse(policy.is_allowed(Capability.WEB_RAG))
        self.assertTrue(CapabilityPolicy.from_settings(settings).is_allowed(Capability.WEB_RAG))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(config.get_settings(reload=True).enable_web_rag)

    def test_nonsecret_environment_override_has_separate_precedence_from_profile_switches(self):
        self.write(AI_TEMPERATURE='0.7', FASTER_MODE='no', COMPACT_MODE='yes')
        self.environment.write_text('AI_TEMPERATURE=1.2\nANIMATION_SPEED=2\nFASTER_MODE=yes\nCOMPACT_MODE=no\nAI_MAX_TOKENS=800\n', encoding='utf-8')
        settings = config.get_settings()
        self.assertEqual(settings.ai_temperature, 0.7)
        self.assertEqual(settings.animation_speed, 2.0)
        self.assertFalse(settings.faster_mode)
        self.assertTrue(settings.compact_mode)
        self.assertEqual(settings.ai_max_tokens, 400)  # Managed Fast key ignores environment even when inactive.
        self.assertEqual(config.read_config_document(self.path)[1]['AI_TEMPERATURE'], '0.7')

    def test_failed_patch_before_replace_preserves_file_and_cached_settings(self):
        self.write(AI_MAX_TOKENS='300')
        config.get_settings()
        original = self.path.read_bytes()
        with patch.object(config, '_write_atomic_config', side_effect=config.AtomicWriteError('write_not_applied', 'synthetic denial')):
            self.assertEqual(config.patch_config_keys({'AI_MAX_TOKENS':'700'}), (False, ['AI_MAX_TOKENS']))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(config.get_settings().ai_max_tokens, 300)

    def test_failure_after_replace_reports_failure_while_disk_changes_and_cache_stays_old(self):
        self.write(AI_MAX_TOKENS='300')
        config.get_settings()
        writer = config._write_atomic_config
        def applied_then_failed(path, text):
            writer(path, text)
            raise config.AtomicWriteError('write_applied_verification_failed', 'synthetic verification interruption')
        with patch.object(config, '_write_atomic_config', side_effect=applied_then_failed):
            self.assertFalse(config.patch_config_key('AI_MAX_TOKENS', '700'))
        self.assertEqual(config.read_config_document(self.path)[1]['AI_MAX_TOKENS'], '700')
        self.assertEqual(config.get_settings().ai_max_tokens, 300)
        self.assertEqual(config.get_settings(reload=True).ai_max_tokens, 700)

    def test_deferred_ai_consumers_keep_generation_limits_until_reconstructed(self):
        self.write(AI_MAX_TOKENS='300', HISTORY_LIMIT='3')
        first = self.engine()
        self.assertTrue(config.patch_config_key('AI_MAX_TOKENS', '700'))
        second = self.engine()
        profile = first._resolve_request_profile(user_message='synthetic request')
        self.assertEqual(first._output_limit_for_profile(profile), 300)
        self.assertEqual(second._output_limit_for_profile(profile), 700)

    def test_new_ai_can_combine_fresh_file_history_limit_with_cached_generation_limit(self):
        self.write(AI_MAX_TOKENS='300', HISTORY_LIMIT='3')
        config.get_settings()
        self.write(AI_MAX_TOKENS='700', HISTORY_LIMIT='8')
        engine = self.engine()
        profile = engine._resolve_request_profile(user_message='synthetic request')
        self.assertEqual(engine.HISTORY_LIMIT, 8)
        self.assertEqual(engine._output_limit_for_profile(profile), 300)
        config.get_settings(reload=True)
        self.assertEqual(self.engine()._output_limit_for_profile(profile), 700)

    def test_guard_created_before_reload_keeps_confirmation_contract(self):
        self.write(ENABLE_COMMAND_CONFIRMATIONS='no')
        before = CommandGuard()
        self.assertTrue(config.patch_config_key('ENABLE_COMMAND_CONFIRMATIONS', 'yes'))
        after = CommandGuard()
        with patch.object(before, '_confirm_on_ui', return_value=False) as old_dialog, patch.object(after, '_confirm_on_ui', return_value=False) as new_dialog:
            self.assertTrue(before.check('open_file', {}))
            self.assertFalse(after.check('open_file', {}))
        old_dialog.assert_not_called()
        new_dialog.assert_called_once()

    def test_capability_reload_requires_explicit_policy_commit(self):
        self.write(COMPACT_MODE='no', ENABLE_WEB_RAG='no')
        controller = CapabilityController(CapabilityPolicy.from_settings(config.get_settings()))
        self.assertTrue(config.patch_config_key('ENABLE_WEB_RAG', 'yes'))
        self.assertTrue(config.get_settings().enable_web_rag)
        self.assertFalse(controller.is_allowed(Capability.WEB_RAG))
        generation = controller.begin_full_transition()
        self.assertTrue(controller.commit_full(CapabilityPolicy.from_settings(config.get_settings()), generation))
        self.assertTrue(controller.is_allowed(Capability.WEB_RAG))

    def test_profile_transition_denies_full_effects_and_invalidates_prior_tokens(self):
        self.write(COMPACT_MODE='no', ENABLE_COMMAND_EXECUTION='yes')
        controller = CapabilityController(CapabilityPolicy.from_settings(config.get_settings()))
        token = controller.authorize(Capability.APP_CONTROL)
        generation = controller.begin_compact_transition()
        self.assertFalse(controller.is_authorized(token))
        self.assertFalse(controller.is_allowed(Capability.APP_CONTROL))
        self.assertTrue(controller.is_allowed(Capability.CHAT))
        self.assertTrue(config.patch_config_key('COMPACT_MODE', 'yes'))
        self.assertTrue(controller.commit_compact(CapabilityPolicy.from_settings(config.get_settings()), generation))
        self.assertFalse(controller.is_authorized(token))
        self.assertFalse(controller.is_allowed(Capability.APP_CONTROL))

    def test_full_commit_persists_then_installs_policy_and_requests_service_rebuild(self):
        self.write(COMPACT_MODE='yes', ENABLE_COMMAND_EXECUTION='yes')
        app = self.app(config.get_settings())
        generation = self.ready_full_consent(app)
        app._on_final_full_mode_decision(generation, True)
        self.assertEqual(config.read_config_document(self.path)[1]['COMPACT_MODE'], 'no')
        self.assertTrue(app._capabilities.is_allowed(Capability.APP_CONTROL))
        app._start_full_mode_services.assert_called_once()
        app._refresh_dashboard_after_profile_commit.assert_called_once()
        app._show_op_error.assert_not_called()

    def test_compact_failed_save_keeps_runtime_compact_and_restart_marker_authoritative(self):
        self.write(COMPACT_MODE='no', ENABLE_COMMAND_EXECUTION='yes')
        app = self.app(config.get_settings())
        writer = config._write_atomic_config
        def deny_config_only(path, text):
            if path == self.path:
                raise config.AtomicWriteError('write_not_applied', 'synthetic denial')
            return writer(path, text)
        with patch.object(config, '_write_atomic_config', side_effect=deny_config_only):
            self.assertFalse(app._activate_compact_mode())
        self.assertFalse(app._capabilities.is_allowed(Capability.APP_CONTROL))
        self.assertEqual(config.read_config_document(self.path)[1]['COMPACT_MODE'], 'no')
        self.assertTrue(config.compact_mode_fail_closed_required())
        self.assertTrue(config.get_settings(reload=True).compact_mode)

    def test_fast_activation_changes_disk_but_existing_settings_and_engine_need_reload_reconstruction(self):
        self.write(FASTER_MODE='no', AI_MAX_TOKENS='600', ENABLE_COMMAND_CONFIRMATIONS='yes')
        old = config.get_settings()
        engine = self.engine()
        self.assertTrue(fast.activate_fast_mode(self.path, self.snapshot).ok)
        self.assertEqual(config.read_config_document(self.path)[1]['AI_MAX_TOKENS'], '220')
        self.assertFalse(config.get_settings().faster_mode)
        new = config.get_settings(reload=True)
        self.assertTrue(new.faster_mode)
        self.assertEqual(new.ai_max_tokens, 220)
        self.assertEqual(old.ai_max_tokens, 600)
        self.assertEqual(engine._resolve_request_profile(user_message='hello').name, 'normal')
        self.assertEqual(self.engine()._resolve_request_profile(user_message='hello').name, 'fast_user')
        self.assertTrue(new.enable_command_confirmations)

    def test_fast_managed_edits_save_restore_preference_while_original_and_forced_values_remain(self):
        self.write(FASTER_MODE='no', AI_MAX_TOKENS='600')
        self.assertTrue(fast.activate_fast_mode(self.path, self.snapshot).ok)
        self.assertTrue(fast.apply_config_updates_with_fast_mode({'AI_MAX_TOKENS':'700'}, self.path, self.snapshot).ok)
        self.assertEqual(config.get_settings(reload=True).ai_max_tokens, 220)
        self.assertEqual(fast.get_fast_mode_original_value('AI_MAX_TOKENS'), '600')
        self.assertEqual(config.read_config_document(self.path)[1]['AI_MAX_TOKENS'], '220')
        self.assertTrue(fast.deactivate_fast_mode(self.path, self.snapshot).ok)
        self.assertEqual(config.get_settings(reload=True).ai_max_tokens, 700)
        self.assertFalse(self.snapshot.exists())

    def test_fast_profile_restart_reconciliation_preserves_original_and_forced_values(self):
        self.write(FASTER_MODE='no', AI_MAX_TOKENS='600')
        self.assertTrue(fast.activate_fast_mode(self.path, self.snapshot).ok)
        original = self.snapshot.read_bytes()
        fast.invalidate_fast_mode_profile_cache()
        result = fast.reconcile_fast_mode_profile(self.path, self.snapshot)
        self.assertTrue(result.ok)
        self.assertTrue(fast.is_fast_mode_profile_active())
        self.assertEqual(fast.get_fast_mode_original_value('AI_MAX_TOKENS'), '600')
        self.assertEqual(config.get_settings(reload=True).ai_max_tokens, 220)
        self.assertEqual(self.snapshot.read_bytes(), original)

    def test_fast_failed_config_write_keeps_recovery_originals_without_claiming_active(self):
        self.write(FASTER_MODE='no', AI_MAX_TOKENS='600')
        original = self.path.read_bytes()
        with patch.object(fast, '_write_and_verify_config', side_effect=config.AtomicWriteError('write_not_applied', 'synthetic denial')):
            result = fast.activate_fast_mode(self.path, self.snapshot)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, 'config_write_failed')
        self.assertEqual(self.path.read_bytes(), original)
        payload = json.loads(self.snapshot.read_text(encoding='utf-8'))
        self.assertEqual(payload['managed_keys']['AI_MAX_TOKENS']['original_value'], '600')
        self.assertFalse(fast.is_fast_mode_profile_active())
        self.assertFalse(config.get_settings(reload=True).faster_mode)

    def test_dashboard_transaction_requires_explicit_reload_and_separate_profile_request(self):
        self.write(COMPACT_MODE='yes', ENABLE_WEB_RAG='no')
        old = dashboard.build_dashboard_presentation(config.get_settings())
        result = dashboard.apply_dashboard_config_updates({'ENABLE_WEB_RAG':'yes'}, fast)
        self.assertTrue(result.ok)
        self.assertFalse(config.get_settings().enable_web_rag)
        config.get_settings(reload=True)
        self.assertTrue(config.get_settings().enable_web_rag)
        # A profile request uses its dedicated consent path, not the generic transaction.
        split = dashboard.split_dashboard_profile_update({'COMPACT_MODE':'no'}, current_compact_mode=True)
        self.assertEqual(split.generic_updates, {})
        self.assertIs(split.requested_compact_mode, False)
        self.assertTrue(old.compact_mode_on)
        self.assertNotIn('System Monitor', old.tabs)

    def test_refresh_constants_does_not_update_wake_delay_imported_by_main(self):
        self.write(WAKE_DELAY_SEC='1')
        old = config.get_settings().wake_delay_ms
        self.enterContext(patch.object(main, 'WAKE_DELAY_MS', old))
        self.write(WAKE_DELAY_SEC='4')
        utils.refresh_config_constants()
        app = main.CompanionApp.__new__(main.CompanionApp)
        app.root = SimpleNamespace(after=Mock(return_value='job'))
        app._set_state = Mock()
        app._wake_job = None
        app._finish_wake = Mock()
        app._start_wake_sequence()
        self.assertEqual(utils.WAKE_DELAY_MS, 4000)
        app.root.after.assert_called_once_with(1000, app._finish_wake)

    def test_animation_helper_reads_current_snapshot_while_audio_coordinator_keeps_mode(self):
        self.write(ANIMATION_SPEED='1', VOICE_OUTPUT_MODE='bleeps_only')
        first = tts_player.VoiceOutputCoordinator(Mock(), config.get_settings())
        self.assertTrue(config.patch_config_keys({'ANIMATION_SPEED':'2','VOICE_OUTPUT_MODE':'tts_only'})[0])
        with patch.object(tts_player, 'TTSPlayer'):
            second = tts_player.VoiceOutputCoordinator(Mock(), config.get_settings())
        self.assertEqual(main._read_animation_speed(), 2.0)
        self.assertEqual(first.mode, 'bleeps_only')
        self.assertEqual(second.mode, 'tts_only')

    def test_sensing_construction_keeps_capture_gate_until_service_recreation(self):
        from agetha.platform import screen_reader
        self.write(ENABLE_SCREEN_READER='no')
        with patch.object(screen_reader, 'TESSERACT_OK', False), patch.object(screen_reader, '_has_display', return_value=True), patch.object(screen_reader.platform, 'system', return_value='Windows'), patch.object(screen_reader.ScreenReader, '_ordered_backends', return_value=[]), patch.object(screen_reader, '_ensure_custom_patterns'):
            first = screen_reader.ScreenReader()
            self.assertTrue(config.patch_config_key('ENABLE_SCREEN_READER', 'yes'))
            second = screen_reader.ScreenReader()
        self.assertFalse(first.automatic_capture_supported)
        self.assertTrue(second.automatic_capture_supported)

    def test_multiple_reads_during_fast_activation_keep_old_snapshot_until_explicit_reload(self):
        self.write(FASTER_MODE='no', AI_MAX_TOKENS='600')
        old = config.get_settings()
        writer = fast._write_and_verify_config
        observed = []
        def observe_transaction(path, text, updates):
            observed.append((config.get_settings().ai_max_tokens, config.read_config_document(path)[1]['AI_MAX_TOKENS']))
            writer(path, text, updates)
            observed.append((config.get_settings().ai_max_tokens, config.read_config_document(path)[1]['AI_MAX_TOKENS']))
        with patch.object(fast, '_write_and_verify_config', side_effect=observe_transaction):
            self.assertTrue(fast.activate_fast_mode(self.path, self.snapshot).ok)
        self.assertEqual(observed, [(600,'600'), (600,'220')])
        self.assertEqual(old.ai_max_tokens, 600)
        self.assertEqual(config.get_settings(reload=True).ai_max_tokens, 220)

    def test_overlapping_reloads_keep_newer_publication_after_old_read_finishes(self):
        self.write(AI_MAX_TOKENS='300')
        read_complete = threading.Event()
        release = threading.Event()
        reader = config.parse_config_file
        errors = []
        def controlled_reader(*args, **kwargs):
            values = reader(*args, **kwargs)
            if threading.current_thread().name == 'f10-old-read':
                read_complete.set()
                if not release.wait(3):
                    raise AssertionError('test did not release old read')
            return values
        def reload_old():
            try:
                config.get_settings(reload=True)
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=reload_old, name='f10-old-read')
        with patch.object(config, 'parse_config_file', side_effect=controlled_reader):
            worker.start()
            try:
                self.assertTrue(read_complete.wait(3))
                self.write(AI_MAX_TOKENS='700')
                self.assertEqual(config.get_settings(reload=True).ai_max_tokens, 700)
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(config.read_config_document(self.path)[1]['AI_MAX_TOKENS'], '700')
        self.assertEqual(config.get_settings().ai_max_tokens, 700)
        self.assertEqual(config.get_settings(reload=True).ai_max_tokens, 700)

    def test_medic_first_duplicate_can_disagree_with_python_last_duplicate(self):
        import medic_helper
        self.path.write_text('VOICE_OUTPUT_MODE=bleeps_only\nVOICE_OUTPUT_MODE=tts_only\n', encoding='utf-8')
        with patch.object(medic_helper, '_MEDIC_DIR', self.root):
            self.assertEqual(medic_helper._config_flag('VOICE_OUTPUT_MODE'), 'bleeps_only')
        self.assertEqual(config.get_settings().voice_output_mode, 'tts_only')

    def test_full_failed_persistence_keeps_compact_and_does_not_start_services(self):
        self.write(COMPACT_MODE='yes', ENABLE_COMMAND_EXECUTION='yes')
        app = self.app(config.get_settings())
        generation = self.ready_full_consent(app)
        original = self.path.read_bytes()
        with patch.object(config, '_write_atomic_config', side_effect=config.AtomicWriteError('write_not_applied', 'synthetic denial')):
            app._on_final_full_mode_decision(generation, True)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(app._capabilities.is_allowed(Capability.APP_CONTROL))
        app._start_full_mode_services.assert_not_called()
        app._show_op_error.assert_called_once()

    def test_out_of_range_budget_clamps_in_settings_but_engine_raw_validation_uses_default(self):
        self.write(AI_MAX_TOKENS='9000')
        settings = config.get_settings()
        engine = self.engine()
        self.assertEqual(settings.ai_max_tokens, 8192)
        self.assertEqual(engine._config['AI_MAX_TOKENS'], '400')
        profile = engine._resolve_request_profile(user_message='synthetic request')
        self.assertEqual(engine._output_limit_for_profile(profile), 8192)
        self.assertEqual(config.read_config_document(self.path)[1]['AI_MAX_TOKENS'], '9000')


if __name__ == '__main__':
    unittest.main()
