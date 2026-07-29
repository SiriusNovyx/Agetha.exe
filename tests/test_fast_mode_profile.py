from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agetha import app_config
from agetha.core import fast_mode_profile as fast


class FastModeProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.config = self.root / "config.txt"
        self.snapshot = self.root / "memory" / fast.FAST_MODE_SNAPSHOT_NAME
        fast.invalidate_fast_mode_profile_cache()

    def tearDown(self) -> None:
        fast.invalidate_fast_mode_profile_cache()
        self._temp.cleanup()

    def _write_config(self, text: str | None = None) -> None:
        self.config.write_text(
            text if text is not None else (
                "# custom heading\n"
                "FASTER_MODE = no\n"
                "AI_MAX_TOKENS = 400\n"
                "HISTORY_LIMIT = 6\n"
                "UNKNOWN_PLUGIN_KEY = keep-me\n"
            ),
            encoding="utf-8",
        )

    def _activate(self) -> fast.FastModeReconcileResult:
        return fast.activate_fast_mode(self.config, self.snapshot)

    def test_profile_reexports_the_single_app_config_mapping(self) -> None:
        self.assertIs(fast.FAST_MODE_OVERRIDES, app_config.FAST_MODE_OVERRIDES)

    def test_activation_snapshots_raw_values_and_forces_profile(self) -> None:
        self._write_config()
        result = self._activate()
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "activated")
        payload = json.loads(self.snapshot.read_text(encoding="utf-8"))
        max_tokens = payload["managed_keys"]["AI_MAX_TOKENS"]
        self.assertTrue(max_tokens["was_present"])
        self.assertEqual(max_tokens["original_value"], "400")
        self.assertFalse(payload["managed_keys"]["MEMORY_CHARS"]["was_present"])
        self.assertIsNone(payload["managed_keys"]["MEMORY_CHARS"]["original_value"])
        text, raw = app_config.read_config_document(self.config)
        self.assertIn("# custom heading", text)
        self.assertEqual(raw["UNKNOWN_PLUGIN_KEY"], "keep-me")
        self.assertEqual(raw["FASTER_MODE"], "yes")
        for key, value in fast.FAST_MODE_OVERRIDES.items():
            self.assertEqual(raw[key], value)

    def test_structural_renderer_updates_all_duplicates_and_removes_all(self) -> None:
        original = (
            "# keep\r\n"
            "AI_MAX_TOKENS = 400\r\n"
            "UNKNOWN = x\r\n"
            "ai_max_tokens=600\r\n"
            "MEMORY_CHARS = 600\r\n"
            "MEMORY_CHARS = 900\r\n"
        )
        rendered = app_config.render_config_document(
            original,
            {"AI_MAX_TOKENS": "220"},
            {"MEMORY_CHARS"},
        )
        self.assertEqual(rendered.count("220"), 2)
        self.assertNotIn("MEMORY_CHARS", rendered)
        self.assertIn("# keep\r\n", rendered)
        self.assertIn("UNKNOWN = x\r\n", rendered)

    def test_activation_preserves_comments_order_and_unknown_keys(self) -> None:
        before = "# one\nUNKNOWN = yes\n\n# two\nFASTER_MODE = no\n"
        self._write_config(before)
        self.assertTrue(self._activate().ok)
        after = self.config.read_text(encoding="utf-8")
        self.assertLess(after.index("# one"), after.index("UNKNOWN = yes"))
        self.assertLess(after.index("UNKNOWN = yes"), after.index("# two"))
        self.assertIn("\n\n# two", after)

    def test_dashboard_activation_captures_prospective_managed_edit(self) -> None:
        self._write_config()
        result = fast.apply_config_updates_with_fast_mode(
            {"AI_MAX_TOKENS": "600", "FASTER_MODE": "yes"},
            self.config,
            self.snapshot,
        )
        self.assertEqual(result.status, "activated")
        payload = json.loads(self.snapshot.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["managed_keys"]["AI_MAX_TOKENS"]["original_value"], "600",
        )
        self.assertEqual(
            app_config.read_config_document(self.config)[1]["AI_MAX_TOKENS"], "220",
        )

    def test_activation_rejects_invalid_original_managed_value(self) -> None:
        self._write_config(
            "FASTER_MODE = no\n"
            "AI_MAX_TOKENS = not-a-number\n"
            "UNKNOWN_PLUGIN_KEY = keep-me\n"
        )
        before = self.config.read_bytes()

        result = self._activate()

        self.assertEqual(result.status, "invalid_updates")
        self.assertIn("AI_MAX_TOKENS", result.warnings[0])
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.snapshot.exists())

    def test_dashboard_activation_rejects_invalid_prospective_original(self) -> None:
        self._write_config()
        before = self.config.read_bytes()

        result = fast.apply_config_updates_with_fast_mode(
            {"AI_MAX_TOKENS": "invalid", "FASTER_MODE": "yes"},
            self.config,
            self.snapshot,
        )

        self.assertEqual(result.status, "invalid_updates")
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.snapshot.exists())

    def test_failed_snapshot_write_leaves_config_unchanged(self) -> None:
        self._write_config()
        before = self.config.read_bytes()
        with patch.object(fast, "_write_snapshot", side_effect=OSError("denied")):
            result = self._activate()
        self.assertEqual(result.status, "snapshot_write_failed")
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.snapshot.exists())

    def test_failed_activation_config_write_keeps_recoverable_snapshot(self) -> None:
        self._write_config()
        before = self.config.read_bytes()
        with patch.object(fast, "write_config_document", side_effect=OSError("locked")):
            result = self._activate()
        self.assertEqual(result.status, "config_write_failed")
        self.assertEqual(self.config.read_bytes(), before)
        self.assertTrue(self.snapshot.exists())

    def test_restart_is_idempotent_and_does_not_replace_original(self) -> None:
        self._write_config()
        self.assertTrue(self._activate().ok)
        first = json.loads(self.snapshot.read_text(encoding="utf-8"))
        one = fast.reconcile_fast_mode_profile(self.config, self.snapshot)
        two = fast.reconcile_fast_mode_profile(self.config, self.snapshot)
        second = json.loads(self.snapshot.read_text(encoding="utf-8"))
        self.assertEqual(one.status, "active_valid")
        self.assertEqual(two.status, "active_valid")
        self.assertEqual(
            first["managed_keys"]["AI_MAX_TOKENS"]["original_value"],
            second["managed_keys"]["AI_MAX_TOKENS"]["original_value"],
        )

    def test_restart_repairs_drift_and_preserves_it_for_restoration(self) -> None:
        self._write_config()
        self.assertTrue(self._activate().ok)
        text = self.config.read_text(encoding="utf-8").replace(
            "AI_MAX_TOKENS = 220", "AI_MAX_TOKENS = 600",
        )
        self.config.write_text(text, encoding="utf-8")
        repaired = fast.reconcile_fast_mode_profile(self.config, self.snapshot)
        self.assertEqual(repaired.status, "active_repaired")
        self.assertEqual(
            app_config.read_config_document(self.config)[1]["AI_MAX_TOKENS"], "220",
        )
        restored = fast.apply_config_updates_with_fast_mode(
            {"FASTER_MODE": "no"}, self.config, self.snapshot,
        )
        self.assertEqual(restored.status, "restore_conflict_preserved")
        self.assertEqual(
            app_config.read_config_document(self.config)[1]["AI_MAX_TOKENS"], "600",
        )

    def test_edit_back_to_original_clears_stale_restore_override(self) -> None:
        self._write_config()
        self.assertTrue(self._activate().ok)
        text = self.config.read_text(encoding="utf-8").replace(
            "AI_MAX_TOKENS = 220", "AI_MAX_TOKENS = 600",
        )
        self.config.write_text(text, encoding="utf-8")
        self.assertEqual(
            fast.reconcile_fast_mode_profile(self.config, self.snapshot).status,
            "active_repaired",
        )

        text = self.config.read_text(encoding="utf-8").replace(
            "AI_MAX_TOKENS = 220", "AI_MAX_TOKENS = 400",
        )
        self.config.write_text(text, encoding="utf-8")
        repaired = fast.reconcile_fast_mode_profile(self.config, self.snapshot)

        self.assertEqual(repaired.status, "active_repaired")
        snapshot = json.loads(self.snapshot.read_text(encoding="utf-8"))
        self.assertNotIn(
            "restore_override", snapshot["managed_keys"]["AI_MAX_TOKENS"],
        )
        restored = fast.deactivate_fast_mode(self.config, self.snapshot)
        self.assertEqual(restored.status, "restored")
        self.assertEqual(
            app_config.read_config_document(self.config)[1]["AI_MAX_TOKENS"], "400",
        )

    def test_unmanaged_edit_survives_reconcile_and_restore(self) -> None:
        self._write_config()
        self.assertTrue(self._activate().ok)
        text = app_config.render_config_document(
            self.config.read_text(encoding="utf-8"),
            {"UNKNOWN_PLUGIN_KEY": "changed"},
        )
        self.config.write_text(text, encoding="utf-8")
        self.assertEqual(
            fast.reconcile_fast_mode_profile(self.config, self.snapshot).status,
            "active_valid",
        )
        self.assertTrue(fast.deactivate_fast_mode(self.config, self.snapshot).ok)
        self.assertEqual(
            app_config.read_config_document(self.config)[1]["UNKNOWN_PLUGIN_KEY"],
            "changed",
        )

    def test_restoration_removes_keys_that_were_originally_missing(self) -> None:
        self._write_config()
        self.assertTrue(self._activate().ok)
        restored = fast.deactivate_fast_mode(self.config, self.snapshot)
        self.assertEqual(restored.status, "restored")
        raw = app_config.read_config_document(self.config)[1]
        self.assertEqual(raw["AI_MAX_TOKENS"], "400")
        self.assertEqual(raw["HISTORY_LIMIT"], "6")
        self.assertNotIn("MEMORY_CHARS", raw)
        self.assertEqual(raw["FASTER_MODE"], "no")
        self.assertFalse(self.snapshot.exists())

    def test_failed_restoration_retains_snapshot_and_forced_config(self) -> None:
        self._write_config()
        self.assertTrue(self._activate().ok)
        before = self.config.read_bytes()
        with patch.object(fast, "write_config_document", side_effect=OSError("locked")):
            result = fast.deactivate_fast_mode(self.config, self.snapshot)
        self.assertEqual(result.status, "config_write_failed")
        self.assertEqual(self.config.read_bytes(), before)
        self.assertTrue(self.snapshot.exists())

    def test_post_replace_verification_failure_does_not_cache_active(self) -> None:
        self._write_config()
        self.assertTrue(self._activate().ok)

        def _write_then_fail(
            config: Path,
            rendered: str,
            _expected: object,
            _absent: object = None,
        ) -> None:
            app_config.write_config_document(config, rendered)
            raise OSError("verification read failed")

        with patch.object(fast, "_write_and_verify_config", side_effect=_write_then_fail):
            result = fast.deactivate_fast_mode(self.config, self.snapshot)

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "config_write_failed")
        self.assertEqual(
            app_config.read_config_document(self.config)[1]["FASTER_MODE"], "no",
        )
        self.assertTrue(self.snapshot.exists())
        self.assertFalse(fast.is_fast_mode_profile_active(self.config, self.snapshot))

    def test_deactivation_ignores_forced_dashboard_field_echoes(self) -> None:
        self._write_config()
        self.assertTrue(self._activate().ok)
        submitted = {**fast.FAST_MODE_OVERRIDES, "FASTER_MODE": "no"}
        result = fast.apply_config_updates_with_fast_mode(
            submitted, self.config, self.snapshot,
        )
        self.assertEqual(result.status, "restored")
        raw = app_config.read_config_document(self.config)[1]
        self.assertEqual(raw["AI_MAX_TOKENS"], "400")
        self.assertEqual(raw["HISTORY_LIMIT"], "6")

    def test_invalid_inactive_snapshot_is_quarantined_without_config_change(self) -> None:
        self._write_config()
        self.snapshot.parent.mkdir(parents=True)
        self.snapshot.write_text("{broken", encoding="utf-8")
        before = self.config.read_bytes()
        result = fast.reconcile_fast_mode_profile(self.config, self.snapshot)
        self.assertEqual(result.status, "snapshot_quarantined")
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.snapshot.exists())
        self.assertEqual(len(list(self.snapshot.parent.glob("fast_mode_snapshot.invalid-*.json"))), 1)

    def test_invalid_active_snapshot_fails_closed(self) -> None:
        self._write_config("FASTER_MODE = yes\nAI_MAX_TOKENS = 777\n")
        self.snapshot.parent.mkdir(parents=True)
        self.snapshot.write_text("{broken", encoding="utf-8")
        before = self.config.read_bytes()
        result = fast.reconcile_fast_mode_profile(self.config, self.snapshot)
        self.assertEqual(result.status, "snapshot_invalid")
        self.assertFalse(result.ok)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertTrue(self.snapshot.exists())

    def test_snapshot_rejects_unapproved_or_secret_keys(self) -> None:
        forbidden = {
            "ENABLE_COMMAND_CONFIRMATIONS", "ENABLE_COMMAND_EXECUTION",
            "ENABLE_WINDOW_CONTROL", "PROTECTED_PROCESSES",
            "OCR_REDACT_SENSITIVE_TEXT", "OCR_EXCLUDED_APPS",
            "OCR_EXCLUDED_TITLE_PATTERNS", "UNLIMITED_OCR_ALLOW_REMOTE",
            "DEEP_OCR_BACKEND", "USE_LOCAL_AI", "ENABLE_GROQ",
            "ENABLE_OPENROUTER", "OPENROUTER_MODEL", "GROQ_MODEL",
            "LOCAL_AI_MODEL", "ENABLE_LONGTERM_MEMORY",
        }
        self.assertTrue(forbidden.isdisjoint(fast.FAST_MODE_OVERRIDES))
        self._write_config()
        self.assertTrue(self._activate().ok)
        payload = json.loads(self.snapshot.read_text(encoding="utf-8"))
        payload["managed_keys"]["OPENROUTER_API_KEY"] = {
            "was_present": True,
            "original_value": "secret",
            "forced_value": "secret",
        }
        self.snapshot.write_text(json.dumps(payload), encoding="utf-8")
        fast.invalidate_fast_mode_profile_cache(self.config)
        inspection = fast.inspect_fast_mode_profile(self.config, self.snapshot)
        self.assertFalse(inspection.valid)
        self.assertEqual(inspection.status, "snapshot_invalid")

    def test_snapshot_path_cannot_escape_the_owned_memory_location(self) -> None:
        self._write_config()
        before = self.config.read_bytes()
        outside = self.root / "outside-snapshot.json"

        result = fast.activate_fast_mode(self.config, outside)

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "unsafe_path_state")
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(outside.exists())

    def test_runtime_overlay_is_path_scoped_and_wins_over_env(self) -> None:
        self._write_config()
        other = self.root / "other" / "config.txt"
        other.parent.mkdir()
        other.write_text("FASTER_MODE = yes\nAI_MAX_TOKENS = 777\n", encoding="utf-8")
        env_file = self.root / ".env"
        env_file.write_text("FASTER_MODE=no\nAI_MAX_TOKENS=999\n", encoding="utf-8")
        self.assertTrue(self._activate().ok)
        with patch.object(app_config, "ENV_PATH", env_file):
            active = app_config.parse_config_file(self.config)
            unrelated = app_config.parse_config_file(other)
        self.assertEqual(active["FASTER_MODE"], "yes")
        self.assertEqual(active["AI_MAX_TOKENS"], "220")
        self.assertEqual(unrelated["FASTER_MODE"], "yes")
        self.assertEqual(unrelated["AI_MAX_TOKENS"], "777")

    def test_disk_disabled_fast_mode_wins_over_env_enable(self) -> None:
        self._write_config("FASTER_MODE = no\nAI_MAX_TOKENS = 400\n")
        env_file = self.root / ".env"
        env_file.write_text("FASTER_MODE=yes\n", encoding="utf-8")

        with patch.object(app_config, "ENV_PATH", env_file):
            parsed = app_config.parse_config_file(self.config)

        self.assertEqual(parsed["FASTER_MODE"], "no")
        self.assertEqual(parsed["AI_MAX_TOKENS"], "400")

    def test_restore_snapshot_unlink_failure_is_successful_and_retryable(self) -> None:
        self._write_config()
        self.assertTrue(self._activate().ok)

        with patch.object(Path, "unlink", side_effect=OSError("file is locked")):
            result = fast.deactivate_fast_mode(self.config, self.snapshot)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "restored_snapshot_retained")
        self.assertIn("cleanup remains pending", " ".join(result.warnings))
        self.assertTrue(self.snapshot.exists())
        self.assertFalse(json.loads(self.snapshot.read_text(encoding="utf-8"))["active"])
        inspection = fast.inspect_fast_mode_profile(self.config, self.snapshot)
        self.assertEqual(inspection.status, "cleanup_pending")
        self.assertFalse(inspection.active)
        self.assertFalse(fast.is_fast_mode_profile_active(self.config, self.snapshot))
        self.assertEqual(
            fast.get_fast_mode_runtime_overrides(
                config_path=self.config,
                snapshot_path=self.snapshot,
                config_enabled=True,
            ),
            {},
        )
        restored = app_config.read_config_document(self.config)[1]
        self.assertEqual(restored["FASTER_MODE"], "no")
        self.assertEqual(restored["AI_MAX_TOKENS"], "400")

        edited = app_config.render_config_document(
            self.config.read_text(encoding="utf-8"),
            {"AI_MAX_TOKENS": "650"},
        )
        self.config.write_text(edited, encoding="utf-8")
        with patch.object(Path, "unlink", side_effect=OSError("still locked")):
            pending = fast.reconcile_fast_mode_profile(self.config, self.snapshot)

        self.assertTrue(pending.ok)
        self.assertEqual(pending.status, "cleanup_pending")
        self.assertEqual(
            app_config.read_config_document(self.config)[1]["AI_MAX_TOKENS"], "650",
        )

        retry = fast.reconcile_fast_mode_profile(self.config, self.snapshot)

        self.assertTrue(retry.ok)
        self.assertEqual(retry.status, "cleanup_completed")
        self.assertFalse(self.snapshot.exists())
        self.assertEqual(
            app_config.read_config_document(self.config)[1]["AI_MAX_TOKENS"], "650",
        )

    def test_reenable_with_cleanup_snapshot_creates_a_fresh_snapshot(self) -> None:
        self._write_config()
        self.assertTrue(self._activate().ok)
        with patch.object(Path, "unlink", side_effect=OSError("file is locked")):
            restored = fast.deactivate_fast_mode(self.config, self.snapshot)
        self.assertEqual(restored.status, "restored_snapshot_retained")

        edited = app_config.render_config_document(
            self.config.read_text(encoding="utf-8"),
            {"FASTER_MODE": "yes", "AI_MAX_TOKENS": "500"},
        )
        self.config.write_text(edited, encoding="utf-8")
        with patch.object(Path, "unlink", side_effect=OSError("still locked")):
            blocked = fast.reconcile_fast_mode_profile(self.config, self.snapshot)

        self.assertFalse(blocked.ok)
        self.assertEqual(blocked.status, "snapshot_cleanup_failed")
        self.assertFalse(json.loads(self.snapshot.read_text(encoding="utf-8"))["active"])
        self.assertEqual(
            app_config.read_config_document(self.config)[1]["AI_MAX_TOKENS"], "500",
        )
        activated = fast.reconcile_fast_mode_profile(self.config, self.snapshot)

        self.assertTrue(activated.ok)
        self.assertEqual(activated.status, "activated")
        snapshot = json.loads(self.snapshot.read_text(encoding="utf-8"))
        self.assertTrue(snapshot["active"])
        self.assertEqual(
            snapshot["managed_keys"]["AI_MAX_TOKENS"]["original_value"], "500",
        )
        self.assertEqual(
            app_config.read_config_document(self.config)[1]["AI_MAX_TOKENS"], "220",
        )
        self.assertEqual(
            fast.deactivate_fast_mode(self.config, self.snapshot).status, "restored",
        )
        self.assertEqual(
            app_config.read_config_document(self.config)[1]["AI_MAX_TOKENS"], "500",
        )

    def test_original_and_forced_helpers_use_validated_cached_state(self) -> None:
        self._write_config()
        self.assertTrue(self._activate().ok)
        self.assertEqual(
            fast.get_fast_mode_original_value(
                "ai_max_tokens", self.config, self.snapshot,
            ),
            "400",
        )
        self.assertEqual(fast.get_fast_mode_forced_value("ai_max_tokens"), "220")
        self.assertTrue(fast.is_fast_mode_profile_active(self.config, self.snapshot))

    def test_active_helper_does_not_reread_validated_cached_snapshot(self) -> None:
        self._write_config()
        self.assertTrue(self._activate().ok)
        with patch.object(fast, "_load_snapshot", side_effect=AssertionError("disk read")):
            self.assertTrue(fast.is_fast_mode_profile_active(self.config, self.snapshot))
            self.assertEqual(
                fast.get_fast_mode_original_value(
                    "AI_MAX_TOKENS", self.config, self.snapshot,
                ),
                "400",
            )

    def test_apply_inactive_updates_uses_structural_atomic_path(self) -> None:
        self._write_config()
        result = fast.apply_config_updates_with_fast_mode(
            {"UNKNOWN_PLUGIN_KEY": "new"}, self.config, self.snapshot,
        )
        self.assertEqual(result.status, "config_updated")
        self.assertEqual(
            app_config.read_config_document(self.config)[1]["UNKNOWN_PLUGIN_KEY"],
            "new",
        )

    def test_inactive_clean_reconcile_is_read_only_and_needs_no_lock(self) -> None:
        self._write_config()
        before = self.config.read_bytes()
        with patch.object(
            fast, "_transaction",
            side_effect=AssertionError("inactive clean path must not lock"),
        ):
            result = fast.reconcile_fast_mode_profile(self.config, self.snapshot)

        self.assertEqual(result.status, "inactive_clean")
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.snapshot.parent.exists())

    def test_apply_rejects_secret_and_multiline_updates(self) -> None:
        self._write_config()
        before = self.config.read_bytes()
        secret = fast.apply_config_updates_with_fast_mode(
            {"OPENROUTER_API_KEY": "secret"}, self.config, self.snapshot,
        )
        multiline = fast.apply_config_updates_with_fast_mode(
            {"UNKNOWN": "yes\nFASTER_MODE=yes"}, self.config, self.snapshot,
        )
        self.assertEqual(secret.status, "invalid_updates")
        self.assertEqual(multiline.status, "invalid_updates")
        self.assertEqual(self.config.read_bytes(), before)

    def test_invalid_typed_restore_value_keeps_snapshot(self) -> None:
        self._write_config()
        self.assertTrue(self._activate().ok)
        text = self.config.read_text(encoding="utf-8").replace(
            "AI_MAX_TOKENS = 220", "AI_MAX_TOKENS = not-a-number",
        )
        self.config.write_text(text, encoding="utf-8")
        result = fast.deactivate_fast_mode(self.config, self.snapshot)
        self.assertEqual(result.status, "config_write_failed")
        self.assertTrue(self.snapshot.exists())
        self.assertEqual(
            app_config.read_config_document(self.config)[1]["AI_MAX_TOKENS"],
            "not-a-number",
        )

    def test_profile_migration_preserves_original_values(self) -> None:
        self._write_config()
        self.assertTrue(self._activate().ok)
        before = json.loads(self.snapshot.read_text(encoding="utf-8"))
        with patch.object(fast, "FAST_MODE_PROFILE_VERSION", 2):
            result = fast.reconcile_fast_mode_profile(self.config, self.snapshot)
            migrated = json.loads(self.snapshot.read_text(encoding="utf-8"))
        self.assertEqual(result.status, "active_repaired")
        self.assertEqual(migrated["profile_version"], 2)
        self.assertEqual(
            migrated["managed_keys"]["AI_MAX_TOKENS"]["original_value"],
            before["managed_keys"]["AI_MAX_TOKENS"]["original_value"],
        )

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not Windows ACLs")
    def test_snapshot_permissions_are_user_only_on_posix(self) -> None:
        self._write_config()
        self.assertTrue(self._activate().ok)
        mode = stat.S_IMODE(self.snapshot.stat().st_mode)
        self.assertEqual(mode, 0o600)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_snapshot_symlink_is_never_followed(self) -> None:
        self._write_config("FASTER_MODE = yes\nAI_MAX_TOKENS = 777\n")
        outside = self.root / "outside.json"
        outside.write_text("do-not-touch", encoding="utf-8")
        self.snapshot.parent.mkdir(parents=True)
        try:
            os.symlink(outside, self.snapshot)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable")
        result = fast.reconcile_fast_mode_profile(self.config, self.snapshot)
        self.assertFalse(result.ok)
        self.assertEqual(outside.read_text(encoding="utf-8"), "do-not-touch")


if __name__ == "__main__":
    unittest.main()
