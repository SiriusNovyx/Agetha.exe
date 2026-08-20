"""Headless integration tests for Compact/Full consent orchestration.

The real consent state machine and capability controller are exercised here.
Only the unavailable boundaries (Tk presentation, Notepad, config persistence,
providers, and advanced-service construction) are replaced with inert fakes.
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from agetha.app_config import AppSettings  # noqa: E402
from agetha.core.capabilities import (  # noqa: E402
    Capability,
    CapabilityController,
    CapabilityPolicy,
    CapabilityProfile,
)
from agetha.core.capability_consent import (  # noqa: E402
    CapabilityConsentFlow,
    ConsentState,
)
from agetha.platform.full_mode_consent import (  # noqa: E402
    ConsentDemoResult,
    ConsentDemoStatus,
)


def _settings(*, compact: bool) -> AppSettings:
    return AppSettings(
        {
            "COMPACT_MODE": "yes" if compact else "no",
            "ENABLE_COMMAND_EXECUTION": "yes",
            "ENABLE_UNICODE_TYPING": "yes",
            "ENABLE_COMPUTER_USE": "yes",
            "ENABLE_PROCESS_AWARENESS": "yes",
            "ENABLE_TERMINAL_SENTINEL": "yes",
            "ENABLE_AMBIENT_POLLS": "yes",
        }
    )


class _FakeRoot:
    def __init__(self) -> None:
        self.cancelled: list[object] = []

    def after_cancel(self, job_id: object) -> None:
        self.cancelled.append(job_id)


class _FakeConsentUI:
    def __init__(self) -> None:
        self.first_decision = None
        self.fallback_decision = None
        self.final_decision = None
        self.final_decisions = []
        self.fallback_reason = ""
        self.cancel_count = 0

    def show_first_confirmation(self, callback) -> bool:
        self.first_decision = callback
        return True

    def show_demo_fallback(self, reason: object, callback) -> bool:
        self.fallback_reason = str(reason)
        self.fallback_decision = callback
        return True

    def show_final_confirmation(self, callback) -> bool:
        self.final_decision = callback
        self.final_decisions.append(callback)
        return True

    def cancel_all(self) -> None:
        self.cancel_count += 1


class _FakeConsentDemo:
    def __init__(self, result: ConsentDemoResult) -> None:
        self._result = result
        self.run_count = 0

    def run_full_mode_consent_demo(self) -> ConsentDemoResult:
        self.run_count += 1
        return self._result


class _ForbiddenBoundary:
    """Record any attempted provider/Computer Use API access."""

    def __init__(self) -> None:
        self.accesses: list[str] = []

    def __getattr__(self, name: str):
        self.accesses.append(name)
        raise AssertionError(f"consent unexpectedly accessed {name}")


class TestFullModeMainIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.compact_settings = _settings(compact=True)
        self.full_settings = _settings(compact=False)

    def _app(self, *, run_workers: bool = True):
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._shutdown_complete = False
        app._capabilities = CapabilityController(
            CapabilityPolicy.from_settings(self.compact_settings)
        )
        app._capability_consent = CapabilityConsentFlow(initial_full=False)
        app._full_mode_consent_ui = _FakeConsentUI()
        app.root = _FakeRoot()

        app._schedule_ui = lambda callback: callback()
        app._pending_workers = []

        def start_worker(target, *, name: str, args: tuple = (), kwargs=None):
            invocation = lambda: target(*args, **(kwargs or {}))
            app._pending_workers.append((name, invocation))
            if run_workers:
                invocation()
            return SimpleNamespace(name=name)

        app._start_worker = start_worker
        app._start_full_mode_services_count = 0
        app._start_full_mode_services = lambda: setattr(
            app,
            "_start_full_mode_services_count",
            app._start_full_mode_services_count + 1,
        )
        app._errors = []
        app._show_op_error = app._errors.append

        # These boundaries must remain untouched throughout consent.  The
        # Notepad presentation is represented by _FakeConsentDemo instead.
        app._ai = _ForbiddenBoundary()
        app._computer_use = _ForbiddenBoundary()
        app._initialize_computer_use_runtime = lambda: app._computer_use.accesses.append(
            "initialize"
        )
        return app

    @staticmethod
    def _runtime_settings(compact: AppSettings, full: AppSettings):
        return patch.object(
            main,
            "get_settings",
            side_effect=lambda reload=False: full if reload else compact,
        )

    def _begin_and_accept_first(self, app) -> int:
        app._begin_full_mode_consent()
        generation = app._capability_consent.snapshot.generation
        self.assertIsNotNone(app._full_mode_consent_ui.first_decision)
        app._full_mode_consent_ui.first_decision(True)
        return generation

    def _reach_final_confirmation(
        self,
        app,
        *,
        status: ConsentDemoStatus = ConsentDemoStatus.TYPED,
        reason: str = "typed",
    ) -> int:
        demo = _FakeConsentDemo(ConsentDemoResult(status, reason))
        app._build_full_mode_consent_demo = lambda _generation: demo
        generation = self._begin_and_accept_first(app)
        self.assertEqual(demo.run_count, 1)
        if status is not ConsentDemoStatus.TYPED:
            self.assertIsNotNone(app._full_mode_consent_ui.fallback_decision)
            app._full_mode_consent_ui.fallback_decision(True)
        self.assertIsNotNone(app._full_mode_consent_ui.final_decision)
        return generation

    def test_first_no_returns_to_compact_without_starting_demo(self) -> None:
        app = self._app(run_workers=False)

        with self._runtime_settings(self.compact_settings, self.full_settings):
            app._begin_full_mode_consent()
            self.assertEqual(
                app._capability_consent.snapshot.state,
                ConsentState.FIRST_CONFIRMATION,
            )
            app._full_mode_consent_ui.first_decision(False)

        self.assertEqual(app._capability_consent.snapshot.state, ConsentState.COMPACT)
        self.assertEqual(app._capabilities.snapshot().profile, CapabilityProfile.COMPACT)
        self.assertEqual(app._pending_workers, [])
        self.assertEqual(app._start_full_mode_services_count, 0)

    def test_first_yes_enters_demo_while_full_capabilities_remain_denied(self) -> None:
        app = self._app(run_workers=False)

        generation = self._begin_and_accept_first(app)

        self.assertTrue(
            app._capability_consent.is_current(generation, ConsentState.CONSENT_DEMO)
        )
        self.assertEqual(app._capabilities.snapshot().profile, CapabilityProfile.COMPACT)
        self.assertFalse(app._capabilities.is_allowed(Capability.COMPUTER_USE))
        self.assertEqual([name for name, _run in app._pending_workers], [
            "full-mode-consent-demo"
        ])
        self.assertEqual(app._start_full_mode_services_count, 0)

    def test_demo_fallback_then_final_no_stays_compact_with_zero_provider_or_cu_use(
        self,
    ) -> None:
        app = self._app()
        demo = _FakeConsentDemo(
            ConsentDemoResult(
                ConsentDemoStatus.LAUNCH_FAILED,
                "Notepad could not be launched.",
            )
        )
        app._build_full_mode_consent_demo = lambda _generation: demo

        with self._runtime_settings(self.compact_settings, self.full_settings):
            self._begin_and_accept_first(app)
            self.assertEqual(
                app._capability_consent.snapshot.state,
                ConsentState.CONSENT_DEMO,
            )
            self.assertEqual(
                app._full_mode_consent_ui.fallback_reason,
                "Notepad could not be launched.",
            )
            app._full_mode_consent_ui.fallback_decision(True)
            self.assertEqual(
                app._capability_consent.snapshot.state,
                ConsentState.FINAL_CONFIRMATION,
            )
            app._full_mode_consent_ui.final_decision(False)

        self.assertEqual(app._capability_consent.snapshot.state, ConsentState.COMPACT)
        self.assertEqual(app._capabilities.snapshot().profile, CapabilityProfile.COMPACT)
        self.assertEqual(app._ai.accesses, [])
        self.assertEqual(app._computer_use.accesses, [])
        self.assertEqual(app._start_full_mode_services_count, 0)

    def test_final_yes_persists_before_committing_full_and_starting_services(self) -> None:
        app = self._app()
        events: list[object] = []
        app._start_full_mode_services = lambda: events.append("services")
        app._refresh_dashboard_after_profile_commit = lambda *_args: events.append("dashboard")

        with self._runtime_settings(self.compact_settings, self.full_settings), patch(
            "agetha.app_config.patch_config_key",
            side_effect=lambda key, value: events.append((key, value)) or True,
        ):
            self._reach_final_confirmation(app)
            app._full_mode_consent_ui.final_decision(True)

        self.assertEqual(
            events,
            [("COMPACT_MODE", "no"), "services", "dashboard"],
        )
        self.assertEqual(app._capability_consent.snapshot.state, ConsentState.FULL)
        self.assertEqual(app._capabilities.snapshot().profile, CapabilityProfile.FULL)
        self.assertTrue(app._capabilities.is_allowed(Capability.COMPUTER_USE))
        self.assertEqual(app._ai.accesses, [])
        self.assertEqual(app._computer_use.accesses, [])

    def test_final_persistence_failure_remains_compact_and_starts_no_services(
        self,
    ) -> None:
        app = self._app()
        writes: list[tuple[str, str]] = []

        with self._runtime_settings(self.compact_settings, self.full_settings), patch(
            "agetha.app_config.patch_config_key",
            side_effect=lambda key, value: writes.append((key, value)) or False,
        ):
            self._reach_final_confirmation(app)
            app._full_mode_consent_ui.final_decision(True)

        self.assertEqual(writes, [("COMPACT_MODE", "no")])
        self.assertEqual(app._capability_consent.snapshot.state, ConsentState.COMPACT)
        self.assertEqual(app._capabilities.snapshot().profile, CapabilityProfile.COMPACT)
        self.assertEqual(app._start_full_mode_services_count, 0)
        self.assertEqual(
            app._errors,
            ["Full Mode could not be saved; Compact Mode remains active."],
        )

    def test_escape_during_demo_cancels_consent_and_invalidates_pending_worker(
        self,
    ) -> None:
        app = self._app(run_workers=False)
        app._cancel_event = threading.Event()
        app._continuation = None
        app._typing_cancel_event = threading.Event()
        app._invalidate_continuation_ui_delivery = lambda: None
        app._stop_computer_use = lambda _reason: None
        app._re_enable_input = lambda: None
        app._set_state = lambda _state: None
        app._subtitle = SimpleNamespace(show_message=lambda *_args: None)

        with self._runtime_settings(self.compact_settings, self.full_settings):
            old_generation = self._begin_and_accept_first(app)
            app._on_cancel_ai()

        self.assertEqual(app._capability_consent.snapshot.state, ConsentState.COMPACT)
        self.assertGreater(app._capability_consent.snapshot.generation, old_generation)
        self.assertEqual(app._capabilities.snapshot().profile, CapabilityProfile.COMPACT)
        self.assertTrue(app._cancel_event.is_set())
        self.assertTrue(app._typing_cancel_event.is_set())
        self.assertGreaterEqual(app._full_mode_consent_ui.cancel_count, 1)

    def test_cancelled_demo_worker_cannot_open_a_late_final_confirmation(self) -> None:
        app = self._app(run_workers=False)
        demo = _FakeConsentDemo(
            ConsentDemoResult(ConsentDemoStatus.TYPED, "typed")
        )
        app._build_full_mode_consent_demo = lambda _generation: demo

        with self._runtime_settings(self.compact_settings, self.full_settings):
            generation = self._begin_and_accept_first(app)
            self.assertEqual(len(app._pending_workers), 1)
            app._cancel_full_mode_consent(generation)
            app._pending_workers[0][1]()

        self.assertEqual(demo.run_count, 1)
        self.assertIsNone(app._full_mode_consent_ui.final_decision)
        self.assertIsNone(app._full_mode_consent_ui.fallback_decision)
        self.assertEqual(app._capability_consent.snapshot.state, ConsentState.COMPACT)
        self.assertEqual(app._capabilities.snapshot().profile, CapabilityProfile.COMPACT)
        self.assertEqual(app._start_full_mode_services_count, 0)

    def test_stale_final_yes_cannot_write_config_or_enable_full(self) -> None:
        app = self._app()
        writes: list[tuple[str, str]] = []

        with self._runtime_settings(self.compact_settings, self.full_settings), patch(
            "agetha.app_config.patch_config_key",
            side_effect=lambda key, value: writes.append((key, value)) or True,
        ):
            generation = self._reach_final_confirmation(app)
            stale_final_callback = app._full_mode_consent_ui.final_decision
            app._cancel_full_mode_consent(generation)
            stale_final_callback(True)

        self.assertEqual(writes, [])
        self.assertEqual(app._capability_consent.snapshot.state, ConsentState.COMPACT)
        self.assertEqual(app._capabilities.snapshot().profile, CapabilityProfile.COMPACT)
        self.assertEqual(app._start_full_mode_services_count, 0)

    def test_stale_no_from_old_attempt_cannot_cancel_new_consent_generation(self) -> None:
        app = self._app(run_workers=False)

        with self._runtime_settings(self.compact_settings, self.full_settings):
            app._begin_full_mode_consent()
            old_generation = app._capability_consent.snapshot.generation
            stale_no = app._full_mode_consent_ui.first_decision
            app._cancel_full_mode_consent(old_generation)

            app._begin_full_mode_consent()
            current = app._capability_consent.snapshot
            transition = app._capability_transition_generation
            cancel_count = app._full_mode_consent_ui.cancel_count
            stale_no(False)

        self.assertGreater(current.generation, old_generation)
        self.assertEqual(app._capability_consent.snapshot, current)
        self.assertTrue(app._capabilities.snapshot().transitioning)
        self.assertEqual(app._capability_transition_generation, transition)
        self.assertEqual(app._full_mode_consent_ui.cancel_count, cancel_count)

    def test_stale_first_yes_cannot_start_a_worker_for_newer_demo(self) -> None:
        app = self._app(run_workers=False)

        with self._runtime_settings(self.compact_settings, self.full_settings):
            app._begin_full_mode_consent()
            old_generation = app._capability_consent.snapshot.generation
            stale_yes = app._full_mode_consent_ui.first_decision
            app._cancel_full_mode_consent(old_generation)

            app._begin_full_mode_consent()
            current_yes = app._full_mode_consent_ui.first_decision
            current_yes(True)
            current = app._capability_consent.snapshot
            transition = app._capability_transition_generation
            workers = tuple(app._pending_workers)

            stale_yes(True)

        self.assertEqual(current.state, ConsentState.CONSENT_DEMO)
        self.assertEqual(app._capability_consent.snapshot, current)
        self.assertEqual(app._capability_transition_generation, transition)
        self.assertEqual(tuple(app._pending_workers), workers)
        self.assertEqual(len(workers), 1)

    def test_stale_demo_completion_cannot_replace_newer_final_confirmation(
        self,
    ) -> None:
        app = self._app(run_workers=False)

        with self._runtime_settings(self.compact_settings, self.full_settings):
            app._begin_full_mode_consent()
            old_generation = app._capability_consent.snapshot.generation
            app._full_mode_consent_ui.first_decision(True)
            app._cancel_full_mode_consent(old_generation)

            app._begin_full_mode_consent()
            new_generation = app._capability_consent.snapshot.generation
            app._full_mode_consent_ui.first_decision(True)
            app._show_final_full_mode_confirmation(new_generation)
            current = app._capability_consent.snapshot
            current_final = app._full_mode_consent_ui.final_decision
            shown = tuple(app._full_mode_consent_ui.final_decisions)

            app._show_final_full_mode_confirmation(old_generation)

        self.assertEqual(current.state, ConsentState.FINAL_CONFIRMATION)
        self.assertEqual(app._capability_consent.snapshot, current)
        self.assertIs(app._full_mode_consent_ui.final_decision, current_final)
        self.assertEqual(tuple(app._full_mode_consent_ui.final_decisions), shown)
        self.assertEqual(len(shown), 1)

    def test_final_yes_with_stale_transition_token_cannot_persist_or_mutate_state(
        self,
    ) -> None:
        app = self._app()
        events: list[object] = []

        with self._runtime_settings(self.compact_settings, self.full_settings), patch(
            "agetha.app_config.patch_config_key",
            side_effect=lambda key, value: events.append((key, value)) or True,
        ):
            self._reach_final_confirmation(app)
            app._capabilities.begin_compact_transition()
            newer_capabilities = app._capabilities.snapshot()
            current_consent = app._capability_consent.snapshot
            app._activate_compact_mode = lambda: events.append("compact")

            app._full_mode_consent_ui.final_decision(True)

        self.assertEqual(events, [])
        self.assertEqual(app._capability_consent.snapshot, current_consent)
        self.assertEqual(app._capabilities.snapshot(), newer_capabilities)
        self.assertEqual(app._start_full_mode_services_count, 0)


if __name__ == "__main__":
    unittest.main()
