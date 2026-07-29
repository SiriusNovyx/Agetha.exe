from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from agetha import app_config
from agetha.core import fast_mode_profile as fast


class FastModeSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.config = self.root / "config.txt"
        self.snapshot = self.root / "memory" / fast.FAST_MODE_SNAPSHOT_NAME
        self.config.write_text(
            "FASTER_MODE = no\n"
            "AI_MAX_TOKENS = 400\n"
            "HISTORY_LIMIT = 6\n"
            "UNKNOWN_PLUGIN_KEY = keep-me\n",
            encoding="utf-8",
        )
        fast.invalidate_fast_mode_profile_cache()

    def tearDown(self) -> None:
        fast.invalidate_fast_mode_profile_cache()
        self._temp.cleanup()

    def _symlink_or_skip(
        self, target: Path, link: Path, *, target_is_directory: bool = False,
    ) -> None:
        try:
            os.symlink(target, link, target_is_directory=target_is_directory)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")

    def test_fast_mode_overrides_never_include_secrets_or_safety_settings(self) -> None:
        safe, unsafe = fast.validate_fast_mode_override_allowlist()
        self.assertTrue(safe, unsafe)
        self.assertEqual(unsafe, ())
        self.assertTrue(set(fast.FAST_MODE_OVERRIDES).isdisjoint(
            fast.FAST_MODE_FORBIDDEN_KEYS,
        ))
        self.assertTrue(all(
            not app_config._is_secret_key(key) for key in fast.FAST_MODE_OVERRIDES
        ))

    def test_every_override_is_supported_typed_in_range_and_single_line(self) -> None:
        supported = app_config.default_config_dict()
        for key, value in fast.FAST_MODE_OVERRIDES.items():
            with self.subTest(key=key):
                self.assertIn(key, supported)
                self.assertNotIn("\r", value)
                self.assertNotIn("\n", value)
                self.assertTrue(app_config.validate_config_value(
                    key, value, enforce_range=True,
                ))

    def test_unsafe_future_profile_fails_closed_without_writes(self) -> None:
        unsafe = {**fast.FAST_MODE_OVERRIDES, "ENABLE_COMMAND_EXECUTION": "no"}
        before = self.config.read_bytes()
        with patch.object(fast, "FAST_MODE_OVERRIDES", unsafe):
            result = fast.activate_fast_mode(self.config, self.snapshot)
        self.assertEqual(result.status, "unsafe_profile_definition")
        self.assertIn("ENABLE_COMMAND_EXECUTION", result.warnings[0])
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.snapshot.exists())

    def test_secret_future_profile_is_rejected_without_printing_value(self) -> None:
        unsafe = {**fast.FAST_MODE_OVERRIDES, "OPENROUTER_API_KEY": "secret-value"}
        safe, names = fast.validate_fast_mode_override_allowlist(unsafe)
        self.assertFalse(safe)
        self.assertEqual(names, ("OPENROUTER_API_KEY",))
        self.assertNotIn("secret-value", str(names))

    def test_managed_values_are_not_loaded_from_env(self) -> None:
        env = self.root / ".env"
        env.write_text("AI_MAX_TOKENS=999\nHISTORY_LIMIT=19\n", encoding="utf-8")
        with patch.object(app_config, "ENV_PATH", env):
            parsed = app_config.parse_config_file(self.config)
        self.assertEqual(parsed["AI_MAX_TOKENS"], "400")
        self.assertEqual(parsed["HISTORY_LIMIT"], "6")

    def test_snapshot_schema_accepts_exactly_the_canonical_key_set(self) -> None:
        raw = app_config.parse_config_document(self.config.read_text(encoding="utf-8"))
        snapshot = fast._new_snapshot(raw)
        self.assertIsNotNone(fast._validate_snapshot(snapshot)[0])
        snapshot["managed_keys"].pop("AI_MAX_TOKENS")
        valid, error = fast._validate_snapshot(snapshot)
        self.assertIsNone(valid)
        self.assertIn("allowlist mismatch", str(error))

    def test_snapshot_rejects_out_of_range_original_and_forced_values(self) -> None:
        raw = app_config.parse_config_document(self.config.read_text(encoding="utf-8"))
        snapshot = fast._new_snapshot(raw)
        snapshot["managed_keys"]["AI_MAX_TOKENS"]["original_value"] = "999999"
        self.assertIsNone(fast._validate_snapshot(snapshot)[0])
        snapshot = fast._new_snapshot(raw)
        snapshot["managed_keys"]["AI_MAX_TOKENS"]["forced_value"] = "999999"
        self.assertIsNone(fast._validate_snapshot(snapshot)[0])

    def test_normal_preexisting_unlocked_lock_file_succeeds(self) -> None:
        self.snapshot.parent.mkdir()
        lock = self.snapshot.parent / ".fast_mode.lock"
        lock.write_bytes(b"\0")
        result = fast.activate_fast_mode(self.config, self.snapshot)
        self.assertEqual(result.status, "activated")
        self.assertTrue(lock.exists())

    def test_mocked_reparse_lock_path_is_rejected(self) -> None:
        self.snapshot.parent.mkdir()
        lock = self.snapshot.parent / ".fast_mode.lock"
        lock.write_bytes(b"\0")
        original = fast._is_reparse_or_symlink

        def _mocked(path: Path) -> bool:
            return Path(path).name == ".fast_mode.lock" or original(path)

        before = self.config.read_bytes()
        with patch.object(fast, "_is_reparse_or_symlink", side_effect=_mocked):
            result = fast.activate_fast_mode(self.config, self.snapshot)
        self.assertEqual(result.status, "unsafe_path_state")
        self.assertEqual(self.config.read_bytes(), before)

    def test_descriptor_path_identity_mismatch_is_rejected(self) -> None:
        first = self.root / "first.lock"
        second = self.root / "second.lock"
        first.write_bytes(b"\0")
        second.write_bytes(b"\0")
        with first.open("r+b") as handle, patch.object(
            fast, "_stat_identity", side_effect=[(1, 1), (1, 2)],
        ):
            with self.assertRaises(fast.UnsafeFastModePathError):
                fast._validate_open_file_identity(handle, second, "lock path")

    def test_config_symlink_is_refused(self) -> None:
        target = self.root / "real-config.txt"
        target.write_text(self.config.read_text(encoding="utf-8"), encoding="utf-8")
        self.config.unlink()
        self._symlink_or_skip(target, self.config)
        result = fast.activate_fast_mode(self.config, self.snapshot)
        self.assertEqual(result.status, "unsafe_path_state")

    def test_snapshot_symlink_is_refused(self) -> None:
        self.snapshot.parent.mkdir()
        target = self.root / "outside.json"
        target.write_text("{}", encoding="utf-8")
        self._symlink_or_skip(target, self.snapshot)
        result = fast.activate_fast_mode(self.config, self.snapshot)
        self.assertEqual(result.status, "unsafe_path_state")

    def test_lock_symlink_is_refused(self) -> None:
        self.snapshot.parent.mkdir()
        target = self.root / "outside.lock"
        target.write_bytes(b"\0")
        lock = self.snapshot.parent / ".fast_mode.lock"
        self._symlink_or_skip(target, lock)
        result = fast.activate_fast_mode(self.config, self.snapshot)
        self.assertEqual(result.status, "unsafe_path_state")

    def test_post_lock_config_revalidation_failure_changes_no_state(self) -> None:
        self._assert_revalidation_failure_is_safe("unsafe Fast Mode config path")

    def test_post_lock_memory_revalidation_failure_changes_no_state(self) -> None:
        self._assert_revalidation_failure_is_safe("unsafe Fast Mode memory directory")

    def test_post_lock_snapshot_revalidation_failure_changes_no_state(self) -> None:
        self._assert_revalidation_failure_is_safe("unsafe Fast Mode snapshot path")

    def _assert_revalidation_failure_is_safe(self, message: str) -> None:
        before = self.config.read_bytes()
        with patch.object(
            fast,
            "_revalidate_transaction_paths",
            side_effect=fast.UnsafeFastModePathError(message),
        ):
            result = fast.activate_fast_mode(self.config, self.snapshot)
        self.assertEqual(result.status, "unsafe_path_state")
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.snapshot.exists())

    def test_arbitrary_and_relative_snapshot_paths_are_refused(self) -> None:
        outside = self.root / "outside.json"
        absolute = fast.activate_fast_mode(self.config, outside)
        relative = fast.activate_fast_mode(self.config, Path("..") / "escape.json")
        self.assertEqual(absolute.status, "unsafe_path_state")
        self.assertEqual(relative.status, "unsafe_path_state")
        self.assertFalse(outside.exists())

    def test_case_insensitive_path_key_comparison_is_supported(self) -> None:
        with patch.object(fast.os.path, "normcase", side_effect=lambda value: value.lower()):
            self.assertEqual(
                fast._path_key(Path("C:/Temp/Memory/File.json")),
                fast._path_key(Path("c:/temp/memory/file.json")),
            )

    def test_lock_timeout_returns_profile_busy_without_changes(self) -> None:
        self.snapshot.parent.mkdir()
        lock_path = self.snapshot.parent / ".fast_mode.lock"
        handle = fast._open_verified_lock_file(lock_path)
        before = self.config.read_bytes()
        try:
            if os.name == "nt":
                target = "msvcrt.locking"
            else:
                target = "fcntl.flock"
            with (
                patch(target, side_effect=OSError("busy")),
                patch.object(fast.time, "monotonic", side_effect=[0.0, 1.0]),
            ):
                with self.assertRaises(fast.FastModeProfileBusyError):
                    fast._acquire_file_lock(handle, timeout_seconds=0.01)
        finally:
            handle.close()
        self.assertEqual(self.config.read_bytes(), before)

    def test_lock_released_during_retry_is_acquired(self) -> None:
        self.snapshot.parent.mkdir()
        handle = fast._open_verified_lock_file(self.snapshot.parent / ".fast_mode.lock")
        try:
            target = "msvcrt.locking" if os.name == "nt" else "fcntl.flock"
            with (
                patch(target, side_effect=[OSError("busy"), None]),
                patch.object(fast.time, "monotonic", side_effect=[0.0, 0.0]),
                patch.object(fast.time, "sleep"),
            ):
                fast._acquire_file_lock(handle, timeout_seconds=1.0)
        finally:
            handle.close()

    def test_public_transaction_maps_lock_timeout_to_profile_busy(self) -> None:
        before = self.config.read_bytes()
        with patch.object(
            fast, "_acquire_file_lock",
            side_effect=fast.FastModeProfileBusyError("busy"),
        ):
            result = fast.apply_config_updates_with_fast_mode(
                {"ENABLE_TASKS": "no"}, self.config,
            )
        self.assertEqual(result.status, "profile_busy")
        self.assertFalse(result.ok)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse(self.snapshot.exists())

    def test_atomic_temp_creation_failure_is_not_applied(self) -> None:
        before = self.config.read_bytes()
        with patch.object(app_config.tempfile, "mkstemp", side_effect=OSError("full")):
            with self.assertRaises(app_config.AtomicWriteError) as raised:
                app_config._write_atomic_config(self.config, "changed\n")
        self.assertEqual(raised.exception.state, "write_not_applied")
        self.assertEqual(self.config.read_bytes(), before)

    def test_atomic_file_fsync_failure_is_not_applied(self) -> None:
        before = self.config.read_bytes()
        with patch.object(app_config.os, "fsync", side_effect=OSError("fsync")):
            with self.assertRaises(app_config.AtomicWriteError) as raised:
                app_config._write_atomic_config(self.config, "changed\n")
        self.assertFalse(raised.exception.write_applied)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".config.txt.*.tmp")), [])

    def test_atomic_directory_fsync_failure_reports_applied_state(self) -> None:
        with patch.object(
            app_config, "_fsync_parent_directory", side_effect=OSError("dir fsync"),
        ):
            with self.assertRaises(app_config.AtomicWriteError) as raised:
                app_config._write_atomic_config(self.config, "CHANGED = yes\n")
        self.assertTrue(raised.exception.write_applied)
        self.assertEqual(self.config.read_text(encoding="utf-8"), "CHANGED = yes\n")

    def test_atomic_temp_names_are_unpredictable_and_exclusive(self) -> None:
        real_mkstemp = tempfile.mkstemp
        names: list[str] = []

        def _recording_mkstemp(*args, **kwargs):
            fd, name = real_mkstemp(*args, **kwargs)
            names.append(name)
            return fd, name

        with patch.object(app_config.tempfile, "mkstemp", side_effect=_recording_mkstemp):
            app_config._write_atomic_config(self.config, "ONE = 1\n")
            app_config._write_atomic_config(self.config, "TWO = 2\n")
        self.assertEqual(len(set(names)), 2)
        self.assertEqual(list(self.root.glob(".config.txt.*.tmp")), [])

    def test_post_write_validation_failure_enters_recoverable_state(self) -> None:
        self.assertTrue(fast.activate_fast_mode(self.config, self.snapshot).ok)
        with patch.object(fast, "_config_matches", return_value=False):
            result = fast.deactivate_fast_mode(self.config, self.snapshot)
        self.assertEqual(result.status, "verification_pending")
        self.assertTrue(self.snapshot.exists())
        self.assertEqual(
            app_config.read_config_document(self.config)[1]["FASTER_MODE"], "no",
        )
        recovered = fast.reconcile_fast_mode_profile(self.config, self.snapshot)
        self.assertIn(recovered.status, {"restored", "cleanup_completed"})
        self.assertFalse(self.snapshot.exists())

    def test_cache_update_failure_is_recovered_from_disk_truth(self) -> None:
        with patch.object(fast, "_cache_snapshot", side_effect=RuntimeError("cache")):
            result = fast.activate_fast_mode(self.config, self.snapshot)
        self.assertEqual(result.status, "config_write_failed")
        fast.invalidate_fast_mode_profile_cache(self.config)
        recovered = fast.reconcile_fast_mode_profile(self.config, self.snapshot)
        self.assertEqual(recovered.status, "active_valid")

    def test_structured_audit_contains_names_not_values_or_paths(self) -> None:
        with patch("agetha.core.audit_log.log_audit", return_value=True) as audit:
            result = fast.activate_fast_mode(self.config, self.snapshot)
        self.assertTrue(result.ok)
        actions = [call.args[0] for call in audit.call_args_list]
        self.assertIn("fast_mode_activation_started", actions)
        self.assertIn("fast_mode_activated", actions)
        details = audit.call_args_list[-1].args[1]
        serialized = json.dumps(details)
        self.assertIn("AI_MAX_TOKENS", serialized)
        self.assertNotIn("keep-me", serialized)
        self.assertNotIn(str(self.root), serialized)

    def test_audit_failure_never_blocks_activation(self) -> None:
        with patch("agetha.core.audit_log.log_audit", side_effect=OSError("audit")):
            result = fast.activate_fast_mode(self.config, self.snapshot)
        self.assertEqual(result.status, "activated")


class FastModeCliTests(unittest.TestCase):
    def test_exit_code_contract(self) -> None:
        self.assertEqual(fast.fast_mode_cli_exit_code("active_valid"), 0)
        self.assertEqual(fast.fast_mode_cli_exit_code("restore_required"), 1)
        self.assertEqual(fast.fast_mode_cli_exit_code("unsafe_path_state"), 2)
        self.assertEqual(fast.fast_mode_cli_exit_code("profile_busy"), 3)

    def test_status_is_read_only_and_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "config.txt"
            config.write_text("FASTER_MODE = no\n", encoding="utf-8")
            before = config.read_bytes()
            output = StringIO()
            with patch.object(fast, "CONFIG_PATH", config), redirect_stdout(output):
                code = fast.main(["status"])
            payload = json.loads(output.getvalue().strip().splitlines()[-1])
            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "inactive_clean")
            self.assertEqual(config.read_bytes(), before)
            self.assertFalse((Path(td) / "memory").exists())

    def test_reconcile_and_restore_commands_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "config.txt"
            config.write_text(
                "FASTER_MODE = yes\nAI_MAX_TOKENS = 400\nHISTORY_LIMIT = 6\n",
                encoding="utf-8",
            )
            with patch.object(fast, "CONFIG_PATH", config), redirect_stdout(StringIO()):
                self.assertEqual(fast.main(["reconcile"]), 0)
                self.assertEqual(fast.main(["restore"]), 0)
            raw = app_config.read_config_document(config)[1]
            self.assertEqual(raw["FASTER_MODE"], "no")
            self.assertEqual(raw["AI_MAX_TOKENS"], "400")


if __name__ == "__main__":
    unittest.main()
