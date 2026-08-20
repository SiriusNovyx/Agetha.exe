from __future__ import annotations

import unittest
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import main
from agetha.core.capabilities import (
    CapabilityController,
    CapabilityPolicy,
    CapabilityProfile,
)
from agetha.core.context_dependencies import ContextKind, ContextRequest


class TestMainOwnedScreenContextProvider(unittest.TestCase):
    @staticmethod
    def app(screen) -> main.CompanionApp:
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._screen = screen
        app._closing = False
        app._capabilities = CapabilityController(
            CapabilityPolicy(
                CapabilityProfile.FULL,
                {"ENABLE_COMMAND_EXECUTION": True},
            ),
        )
        return app

    def test_one_targeted_local_capture_returns_labeled_untrusted_context(self) -> None:
        target = {
            "left": 10,
            "top": 20,
            "width": 600,
            "height": 400,
            "title": "Editor",
            "hwnd": 77,
            "process_name": "editor.exe",
            "process_id": 321,
        }
        screen = SimpleNamespace(
            preserve_external_target=MagicMock(return_value=target),
            capture_text=MagicMock(return_value="Build failed: missing symbol"),
            redact_for_external_context=lambda value: value,
            last_monitor_status="ocr_complete",
        )
        app = self.app(screen)

        outcome = app._acquire_read_only_context(
            ContextRequest(ContextKind.SCREEN),
            cancel_check=lambda: False,
        )

        self.assertTrue(outcome.success)
        self.assertEqual(outcome.kind, ContextKind.SCREEN)
        self.assertEqual(outcome.status, "ocr_complete")
        self.assertIn("UNTRUSTED", outcome.provider_context)
        self.assertIn("Build failed: missing symbol", outcome.provider_context)
        screen.capture_text.assert_called_once_with(
            max_chars=3000,
            focused_only=True,
            force_refresh=True,
            capture_target=target,
        )

    def test_missing_target_attempts_once_and_returns_useful_safe_status(self) -> None:
        screen = SimpleNamespace(
            preserve_external_target=MagicMock(return_value=None),
            capture_text=MagicMock(return_value=""),
            redact_for_external_context=lambda value: value,
            last_monitor_status="skipped_own_window",
        )
        app = self.app(screen)

        outcome = app._acquire_read_only_context(
            ContextRequest(ContextKind.SCREEN),
            cancel_check=lambda: False,
        )

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.status, "target_unavailable")
        self.assertIn("current screen context is unavailable", outcome.provider_context)
        self.assertNotIn("screen reader", outcome.provider_context.casefold())
        screen.capture_text.assert_called_once_with(
            max_chars=3000,
            focused_only=True,
            force_refresh=True,
            capture_target=None,
        )

    def test_cancel_before_capture_performs_no_observation(self) -> None:
        screen = SimpleNamespace(
            preserve_external_target=MagicMock(),
            capture_text=MagicMock(),
            redact_for_external_context=lambda value: value,
            last_monitor_status="",
        )
        app = self.app(screen)

        outcome = app._acquire_read_only_context(
            ContextRequest(ContextKind.SCREEN),
            cancel_check=lambda: True,
        )

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.status, "cancelled")
        screen.preserve_external_target.assert_not_called()
        screen.capture_text.assert_not_called()

    def test_capture_is_ordered_before_compact_transition(self) -> None:
        capture_started = threading.Event()
        release_capture = threading.Event()
        transition_started = threading.Event()
        transition_finished = threading.Event()
        outcomes = []

        def capture_text(**_kwargs) -> str:
            capture_started.set()
            self.assertTrue(release_capture.wait(2.0))
            return "Build failed: missing symbol"

        screen = SimpleNamespace(
            preserve_external_target=lambda: None,
            capture_text=capture_text,
            redact_for_external_context=lambda value: value,
            last_monitor_status="ocr_complete",
        )
        app = self.app(screen)

        capture_worker = threading.Thread(
            target=lambda: outcomes.append(
                app._acquire_read_only_context(
                    ContextRequest(ContextKind.SCREEN),
                    cancel_check=lambda: False,
                ),
            ),
        )
        capture_worker.start()
        self.assertTrue(capture_started.wait(2.0))

        def downgrade() -> None:
            transition_started.set()
            app._capabilities.begin_compact_transition()
            transition_finished.set()

        transition_worker = threading.Thread(target=downgrade)
        transition_worker.start()
        self.assertTrue(transition_started.wait(2.0))
        try:
            self.assertFalse(transition_finished.wait(0.05))
        finally:
            release_capture.set()
            capture_worker.join(2.0)
            transition_worker.join(2.0)

        self.assertFalse(capture_worker.is_alive())
        self.assertFalse(transition_worker.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].success)
        self.assertTrue(transition_finished.is_set())

    def test_unsupported_kind_fails_without_touching_screen(self) -> None:
        screen = SimpleNamespace(
            preserve_external_target=MagicMock(),
            capture_text=MagicMock(),
        )
        app = self.app(screen)
        malformed = object.__new__(ContextRequest)
        object.__setattr__(malformed, "kind", "unknown")

        outcome = app._acquire_read_only_context(
            malformed,
            cancel_check=lambda: False,
        )

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.status, "unsupported_context")
        screen.capture_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
