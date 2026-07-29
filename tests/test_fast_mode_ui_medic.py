from __future__ import annotations

import json
import tempfile
import tkinter as tk
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import agetha.core
import medic_helper
from agetha.ui import dashboard


ROOT = Path(__file__).resolve().parent.parent


class _FakeFastModeAPI:
    def __init__(
        self,
        *,
        active: bool = True,
        status: str = "active_valid",
        apply_status: str = "active_valid",
        apply_ok: bool = True,
    ) -> None:
        self.active = active
        self.status = status
        self.apply_status = apply_status
        self.apply_ok = apply_ok
        self.applied: list[dict[str, str]] = []
        self.inspect_calls = 0
        self.reconcile_calls = 0
        self.restore_calls = 0

    def managed_fast_mode_keys(self) -> tuple[str, ...]:
        return ("AI_MAX_TOKENS", "HISTORY_LIMIT")

    def is_fast_mode_profile_active(self) -> bool:
        return self.active

    def inspect_fast_mode_profile(self) -> SimpleNamespace:
        self.inspect_calls += 1
        return SimpleNamespace(
            status=self.status,
            active=self.active,
            valid=True,
            managed_count=2,
            original_values={},
            forced_values={},
            warnings=(),
        )

    def get_fast_mode_original_value(self, key: str) -> str | None:
        return {"AI_MAX_TOKENS": "400", "HISTORY_LIMIT": None}[key]

    def get_fast_mode_forced_value(self, key: str) -> str:
        return {"AI_MAX_TOKENS": "220", "HISTORY_LIMIT": "3"}[key]

    def apply_config_updates_with_fast_mode(self, updates: dict[str, str]) -> SimpleNamespace:
        self.applied.append(updates)
        return SimpleNamespace(
            status=self.apply_status,
            changed_keys=tuple(updates),
            warnings=(),
            error=None if self.apply_ok else "controlled failure",
            ok=self.apply_ok,
        )

    def reconcile_fast_mode_profile(self) -> SimpleNamespace:
        self.reconcile_calls += 1
        return SimpleNamespace(
            status="active_repaired", changed_keys=("AI_MAX_TOKENS",), warnings=(), error=None, ok=True,
        )

    def restore_fast_mode_profile(self) -> SimpleNamespace:
        self.restore_calls += 1
        self.active = False
        return SimpleNamespace(
            status="restored", changed_keys=("AI_MAX_TOKENS",), warnings=(), error=None, ok=True,
        )


class DashboardFastModeTests(unittest.TestCase):
    def test_dashboard_state_uses_profile_inspection_and_canonical_keys(self) -> None:
        api = _FakeFastModeAPI()
        state = dashboard.get_fast_mode_dashboard_state(api)

        self.assertEqual(state.status, "active_valid")
        self.assertTrue(state.active)
        self.assertEqual(state.managed_keys, ("AI_MAX_TOKENS", "HISTORY_LIMIT"))
        self.assertEqual(
            dashboard.format_fast_mode_summary(state),
            "Fast Mode: active — 2 settings temporarily managed",
        )

    def test_managed_field_status_shows_forced_and_restored_values(self) -> None:
        api = _FakeFastModeAPI()
        state = dashboard.get_fast_mode_dashboard_state(api)

        self.assertEqual(
            dashboard.format_fast_mode_managed_status(
                "AI_MAX_TOKENS", state=state, api=api,
            ),
            "Managed by Fast Mode — forced: 220; restored later: 400",
        )
        self.assertIn(
            "default (setting was absent)",
            dashboard.format_fast_mode_managed_status(
                "HISTORY_LIMIT", state=state, api=api,
            ),
        )
        self.assertEqual(
            dashboard.format_fast_mode_managed_status("UNMANAGED", state=state, api=api),
            "",
        )

    def test_inactive_profile_does_not_mark_managed_fields_read_only(self) -> None:
        api = _FakeFastModeAPI(active=False, status="inactive_clean")
        state = dashboard.get_fast_mode_dashboard_state(api)

        self.assertFalse(state.active)
        self.assertEqual(
            dashboard.format_fast_mode_managed_status(
                "AI_MAX_TOKENS", state=state, api=api,
            ),
            "",
        )

    def test_dashboard_apply_uses_exactly_one_coordinated_transaction(self) -> None:
        api = _FakeFastModeAPI()
        updates = {"FASTER_MODE": "yes", "ENABLE_TASKS": "no"}

        result = dashboard.apply_dashboard_config_updates(updates, api)

        self.assertTrue(result.ok)
        self.assertEqual(api.applied, [updates])
        self.assertIsNot(api.applied[0], updates)

    def test_enabling_and_disabling_use_the_coordinated_transaction(self) -> None:
        enabling = _FakeFastModeAPI(active=False, status="inactive_clean")
        disabling = _FakeFastModeAPI(active=True, status="active_valid", apply_status="restored")

        enabled = dashboard.apply_dashboard_config_updates({"FASTER_MODE": "yes"}, enabling)
        disabled = dashboard.apply_dashboard_config_updates({"FASTER_MODE": "no"}, disabling)

        self.assertTrue(enabled.ok)
        self.assertTrue(disabled.ok)
        self.assertEqual(enabling.applied, [{"FASTER_MODE": "yes"}])
        self.assertEqual(disabling.applied, [{"FASTER_MODE": "no"}])

    def test_validation_and_lock_failures_are_not_reported_as_saved(self) -> None:
        invalid = _FakeFastModeAPI(apply_status="invalid_updates", apply_ok=False)
        busy = _FakeFastModeAPI(apply_status="profile_busy", apply_ok=False)

        invalid_result = dashboard.apply_dashboard_config_updates(
            {"FASTER_MODE": "maybe"}, invalid,
        )
        busy_result = dashboard.apply_dashboard_config_updates(
            {"ENABLE_TASKS": "no"}, busy,
        )

        self.assertFalse(dashboard._fast_mode_result_ok(invalid_result))
        self.assertFalse(dashboard._fast_mode_result_ok(busy_result))
        self.assertEqual(invalid.applied, [{"FASTER_MODE": "maybe"}])
        self.assertEqual(busy.applied, [{"ENABLE_TASKS": "no"}])

    def test_snapshot_quarantine_and_unrelated_active_save_stay_coordinated(self) -> None:
        quarantined = _FakeFastModeAPI(
            active=False,
            status="snapshot_invalid",
            apply_status="snapshot_quarantined",
        )
        active = _FakeFastModeAPI(active=True, status="active_valid")

        quarantine_result = dashboard.apply_dashboard_config_updates(
            {"ENABLE_TASKS": "yes"}, quarantined,
        )
        unrelated_result = dashboard.apply_dashboard_config_updates(
            {"ENABLE_TASKS": "no"}, active,
        )

        self.assertTrue(dashboard._fast_mode_result_ok(quarantine_result))
        self.assertTrue(dashboard._fast_mode_result_ok(unrelated_result))
        self.assertEqual(quarantined.applied, [{"ENABLE_TASKS": "yes"}])
        self.assertEqual(active.applied, [{"ENABLE_TASKS": "no"}])

    def test_snapshot_write_failure_is_not_reported_as_success(self) -> None:
        result = SimpleNamespace(status="snapshot_write_failed", error="locked")
        self.assertFalse(dashboard._fast_mode_result_ok(result))

    def test_busy_and_verification_failures_have_actionable_messages(self) -> None:
        busy = dashboard.format_fast_mode_failure("profile_busy")
        pending = dashboard.format_fast_mode_failure("verification_pending")
        self.assertIn("another process", busy)
        self.assertIn("No settings were changed", busy)
        self.assertIn("may have completed", pending)
        self.assertIn("reconciliation", pending)

    def test_pending_activation_requires_confirmation_without_checkbox_change(self) -> None:
        api = _FakeFastModeAPI(active=False, status="snapshot_missing")
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.txt"
            config.write_text("FASTER_MODE = yes\n", encoding="utf-8")
            action, state = dashboard.get_fast_mode_apply_confirmation(
                {"ENABLE_TASKS": "no"}, api, config,
            )

        self.assertEqual(action, "activate")
        self.assertEqual(state.status, "snapshot_missing")

    def test_pending_restoration_uses_current_disk_state_and_requires_confirmation(self) -> None:
        api = _FakeFastModeAPI(active=False, status="restore_required")
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.txt"
            # The dashboard may have opened while this was still `yes`; the
            # pre-apply decision must use the current file value instead.
            config.write_text("FASTER_MODE = no\n", encoding="utf-8")
            action, state = dashboard.get_fast_mode_apply_confirmation(
                {"ENABLE_TASKS": "yes"}, api, config,
            )

        self.assertEqual(action, "restore")
        self.assertEqual(state.status, "restore_required")

    def test_cleanup_pending_is_not_misreported_as_restoration(self) -> None:
        api = _FakeFastModeAPI(active=False, status="cleanup_pending")
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.txt"
            config.write_text("FASTER_MODE = no\n", encoding="utf-8")
            action, state = dashboard.get_fast_mode_apply_confirmation(
                {"ENABLE_TASKS": "yes"}, api, config,
            )

        self.assertIsNone(action)
        self.assertIn("cleanup pending", dashboard.format_fast_mode_summary(state))

    def test_active_profile_disables_only_managed_editor(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        root.withdraw()
        api = _FakeFastModeAPI()
        try:
            with patch.object(dashboard, "_load_fast_mode_api", return_value=api):
                dashboard.open_dashboard(root, SimpleNamespace(raw={}))
                root.update_idletasks()
                top = next(
                    widget for widget in root.winfo_children()
                    if isinstance(widget, tk.Toplevel)
                )

                labels: dict[str, tk.Label] = {}
                close_buttons: list[tk.Button] = []

                def collect(widget: tk.Misc) -> None:
                    for child in widget.winfo_children():
                        if isinstance(child, tk.Label):
                            labels[str(child.cget("text"))] = child
                        if isinstance(child, tk.Button) and str(child.cget("text")) == "✕":
                            close_buttons.append(child)
                        collect(child)

                collect(top)
                managed_label = labels["AI_MAX_TOKENS *"]
                unmanaged_label = labels["FILE_READ_CHARS *"]
                managed_entry = next(
                    child for child in managed_label.master.winfo_children()
                    if isinstance(child, tk.Entry)
                )
                unmanaged_entry = next(
                    child for child in unmanaged_label.master.winfo_children()
                    if isinstance(child, tk.Entry)
                )
                self.assertEqual(str(managed_entry.cget("state")), "disabled")
                self.assertEqual(str(unmanaged_entry.cget("state")), "normal")
                self.assertEqual(len(close_buttons), 1)
                close_buttons[0].invoke()
        finally:
            for widget in root.winfo_children():
                try:
                    widget.destroy()
                except tk.TclError:
                    pass
            root.destroy()

    def test_closing_dashboard_after_edit_does_not_apply_settings(self) -> None:
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        root.withdraw()
        api = _FakeFastModeAPI(active=False, status="inactive_clean")
        try:
            with patch.object(dashboard, "_load_fast_mode_api", return_value=api):
                dashboard.open_dashboard(root, SimpleNamespace(raw={}))
                root.update_idletasks()
                top = next(
                    widget for widget in root.winfo_children()
                    if isinstance(widget, tk.Toplevel)
                )

                descendants: list[tk.Widget] = []

                def collect(widget: tk.Misc) -> None:
                    for child in widget.winfo_children():
                        descendants.append(child)
                        collect(child)

                collect(top)
                entry = next(widget for widget in descendants if isinstance(widget, tk.Entry))
                entry.delete(0, "end")
                entry.insert(0, "unsaved-change")
                close = next(
                    widget for widget in descendants
                    if isinstance(widget, tk.Button) and str(widget.cget("text")) == "✕"
                )
                close.invoke()
                root.update_idletasks()

                self.assertEqual(api.applied, [])
                self.assertFalse(top.winfo_exists())
        finally:
            for widget in root.winfo_children():
                try:
                    widget.destroy()
                except tk.TclError:
                    pass
            root.destroy()


class MedicFastModeTests(unittest.TestCase):
    def _run(self, command) -> dict[str, object]:
        output = StringIO()
        with redirect_stdout(output):
            command()
        return json.loads(output.getvalue().strip().splitlines()[-1])

    def test_status_command_is_machine_readable_and_non_mutating(self) -> None:
        api = _FakeFastModeAPI()
        with patch.object(agetha.core, "fast_mode_profile", api, create=True):
            payload = self._run(medic_helper.cmd_fast_mode_status)

        self.assertEqual(payload["status"], "active_valid")
        self.assertTrue(payload["active"])
        self.assertEqual(payload["managed_count"], 2)
        self.assertEqual(api.inspect_calls, 1)
        self.assertEqual(api.reconcile_calls, 0)
        self.assertEqual(api.restore_calls, 0)

    def test_reconcile_and_restore_are_separate_explicit_commands(self) -> None:
        api = _FakeFastModeAPI()
        with patch.object(agetha.core, "fast_mode_profile", api, create=True):
            repaired = self._run(medic_helper.cmd_fast_mode_reconcile)
            restored = self._run(medic_helper.cmd_fast_mode_restore)

        self.assertEqual(repaired["status"], "active_repaired")
        self.assertEqual(restored["status"], "restored")
        self.assertEqual(api.reconcile_calls, 1)
        self.assertEqual(api.restore_calls, 1)

    def test_machine_output_does_not_emit_warning_or_error_text(self) -> None:
        api = _FakeFastModeAPI()
        result = SimpleNamespace(
            status="config_write_failed",
            changed_keys=(),
            warnings=("private warning C:/Users/name",),
            error="private error C:/Users/name",
            ok=False,
        )
        output = StringIO()
        with redirect_stdout(output):
            medic_helper._print_fast_mode_result(result, api)
        raw = output.getvalue()

        self.assertNotIn("private warning", raw)
        self.assertNotIn("private error", raw)
        self.assertEqual(json.loads(raw)["warning_count"], 1)

    def test_conflict_count_comes_from_conflict_warnings_not_changed_keys(self) -> None:
        api = _FakeFastModeAPI(active=False)
        result = SimpleNamespace(
            status="restore_conflict_preserved",
            changed_keys=("AI_MAX_TOKENS", "HISTORY_LIMIT", "ENABLE_TASKS"),
            warnings=(
                "Preserved a user-edited restore value for AI_MAX_TOKENS",
                "Preserved a user-edited restore value for HISTORY_LIMIT",
            ),
            error=None,
            ok=True,
        )
        output = StringIO()
        with redirect_stdout(output):
            medic_helper._print_fast_mode_result(result, api)
        payload = json.loads(output.getvalue())

        self.assertEqual(len(payload["changed_keys"]), 3)
        self.assertEqual(payload["conflict_count"], 2)

    def test_restored_snapshot_retained_is_success_with_retry_warning(self) -> None:
        result = SimpleNamespace(
            status="restored_snapshot_retained",
            changed_keys=(),
            warnings=("snapshot cleanup should be retried",),
            error="cleanup failed",
            ok=False,
        )
        self.assertTrue(medic_helper._fast_mode_result_ok(result))

    def test_commands_are_registered_and_powershell_requires_confirmation(self) -> None:
        self.assertIs(medic_helper._COMMANDS["fast_mode_status"], medic_helper.cmd_fast_mode_status)
        self.assertIs(medic_helper._COMMANDS["fast_mode_reconcile"], medic_helper.cmd_fast_mode_reconcile)
        self.assertIs(medic_helper._COMMANDS["fast_mode_restore"], medic_helper.cmd_fast_mode_restore)

        script = (ROOT / "Medic_Checker.ps1").read_text(encoding="utf-8")
        self.assertIn("Restore pre-Fast-Mode settings now? [Y/N]", script)
        self.assertIn("fast_mode_restore", script)
        self.assertLess(
            script.index("Restore pre-Fast-Mode settings now? [Y/N]"),
            script.index("Get-FastModeHealth -Command 'fast_mode_restore'"),
        )
        self.assertIn('$conflicts = [int]$Health.conflict_count', script)
        self.assertIn('preserved $conflicts intentional manual edit(s)', script)
        self.assertIn("'^restored_snapshot_retained$'", script)

    def test_declined_action_skips_reconcile_for_one_launch_without_blocking(self) -> None:
        script = (ROOT / "Medic_Checker.ps1").read_text(encoding="utf-8")

        self.assertIn(
            "Skip-FastModeReconcileForLaunch -Reason 'Fast Mode activation or repair was declined; no profile changes were made by Medic.'",
            script,
        )
        self.assertIn(
            "Skip-FastModeReconcileForLaunch -Reason 'Pre-Fast-Mode restoration was declined; no recovery changes were made by Medic.'",
            script,
        )
        signal = script.index("$env:AGETHA_SKIP_FAST_MODE_RECONCILE = '1'")
        launch = script.index("& $script:VenvPython (Join-Path $Script:Root 'main.py')")
        self.assertLess(signal, launch)
        self.assertNotIn("FastModeLaunchAllowed", script)
        self.assertNotIn("Block-FastModeLaunch", script)

    def test_main_consumes_skip_signal_once(self) -> None:
        from main import _consume_fast_mode_reconcile_skip

        environment = {"AGETHA_SKIP_FAST_MODE_RECONCILE": "1"}
        self.assertTrue(_consume_fast_mode_reconcile_skip(environment))
        self.assertNotIn("AGETHA_SKIP_FAST_MODE_RECONCILE", environment)
        self.assertFalse(_consume_fast_mode_reconcile_skip(environment))

    def test_fast_mode_profile_is_in_required_core_file_allowlist(self) -> None:
        script = (ROOT / "Medic_Checker.ps1").read_text(encoding="utf-8")
        core_files = script[
            script.index("$coreFiles = @("):script.index("$missingCore =")
        ]
        self.assertIn("'agetha\\core\\fast_mode_profile.py'", core_files)


if __name__ == "__main__":
    unittest.main()
