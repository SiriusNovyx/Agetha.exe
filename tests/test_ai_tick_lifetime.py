"""AI reservation failures with fake UI/providers and isolated effects."""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import main
from agetha.app_config import AppSettings
from agetha.core.capabilities import CapabilityController, CapabilityPolicy
from agetha.core.continuation import DecisionKind


class TestAiTickReservationLifetime(unittest.TestCase):
    def setUp(self):
        self.settings = AppSettings({
            "COMPACT_MODE": "no", "ENABLE_PROCESS_AWARENESS": "no",
            "ENABLE_STREAMING": "no", "ENABLE_COMPUTER_USE": "no",
        })
        self.enterContext(patch.object(main, "_SETTINGS", self.settings))
        self.enterContext(patch("agetha.core.companion_stats.update_stats"))
        self.enterContext(patch("agetha.core.emotion_engine.note"))
        self.app = main.CompanionApp.__new__(main.CompanionApp)
        app = self.app
        app._closing = False
        app._state = app.STATE_IDLE
        app._ai_tick_lock = threading.Lock()
        app._ai_busy = app._ai_busy_noninterruptible = app._speech_active = False
        app._ai_operation_token = None
        app._pending_user_message = None
        app._pending_user_origin = "user"
        app._pending_screen_context = None
        app._post_ai_tick_callbacks = []
        app._deferred_ai_callbacks_inflight = False
        app._cancel_event = threading.Event()
        app._last_screen_text = ""
        app._screen = app._process_awareness = None
        app._capabilities = CapabilityController(CapabilityPolicy.from_settings(self.settings))
        app._fast_mode_runtime_active = lambda: False
        app._ai = MagicMock()
        app._ai.query.return_value = {"command": "idle", "segments": []}
        app._continuation = MagicMock()
        app._continuation.start.return_value = SimpleNamespace(
            kind=DecisionKind.STARTED, session_id="fake-session", generation=1,
        )
        self.input_state = {"state": "normal"}
        app._input_box = SimpleNamespace(config=lambda **values: self.input_state.update(values))
        app._re_enable_input = lambda: self.input_state.update(state="normal")
        app._schedule_ui = lambda callback: (callback(), "fake-job")[1]
        app._wake_from_presence_rest = lambda: None
        app._set_state = lambda state: setattr(app, "_state", state)
        app._update_token_status = lambda: None
        app._reschedule_screen_poll = lambda: None
        app._continuation_resources_from_user = MagicMock(return_value=())
        app._allows_sensitive_outbound_continuation = lambda _message: False
        app._preserve_context_capture_target = MagicMock()
        app._recent_unresolved_context_for_prompt = MagicMock(return_value="")
        app._clear_unresolved_context_if_topic_changed = MagicMock()
        app._context_request_from_model_response = MagicMock(return_value=None)
        app._accept_continuation_response = MagicMock(return_value=None)
        app._dispatch_response = MagicMock()
        app._handle_continuation_decision = MagicMock()
        app._start_worker = MagicMock()  # A queued next turn must never launch a provider.

    def _assert_slot_available(self):
        self.assertFalse(self.app._ai_busy)
        self.assertIsNone(self.app._ai_operation_token)
        token = self.app._reserve_ai_operation(direct=True, user_message="next", origin="user")
        self.assertIsNotNone(token, "failure left the next turn blocked")
        self.app._release_ai_operation(token)

    def test_continuation_start_failure_releases_reservation(self):
        self.app._continuation.start.side_effect = RuntimeError("synthetic setup failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic setup failure"):
            self.app._ai_tick("hello", origin="user")
        self._assert_slot_available()

    def test_failures_across_reserved_setup_and_response_release_slot(self):
        symbols = (
            "_continuation_resources_from_user", "_preserve_context_capture_target",
            "_recent_unresolved_context_for_prompt", "_clear_unresolved_context_if_topic_changed",
            "_accept_continuation_response", "_dispatch_response",
        )
        for symbol in symbols:
            with self.subTest(symbol=symbol):
                method = getattr(self.app, symbol)
                method.side_effect = RuntimeError("synthetic stage failure")
                try:
                    with self.assertRaisesRegex(RuntimeError, "synthetic stage failure"):
                        self.app._ai_tick("hello", origin="user")
                    self._assert_slot_available()
                    self.assertEqual(self.input_state["state"], "normal")
                finally:
                    method.side_effect = None
                    # Isolate subcases even against the old leaking implementation.
                    token = self.app._ai_operation_token
                    if token is not None:
                        self.app._release_ai_operation(token)

    def test_setup_failure_runs_deferred_work_after_release(self):
        observed = []
        self.app._defer_after_ai_tick(lambda: observed.append(self.app._ai_busy))
        self.app._continuation.start.side_effect = RuntimeError("synthetic setup failure")
        with self.assertRaises(RuntimeError):
            self.app._ai_tick("hello", origin="user")
        self.assertEqual(observed, [False])
        self._assert_slot_available()

    def test_setup_failure_does_not_release_a_replacement_owner(self):
        replacement = []

        def replace_owner(*_args, **_kwargs):
            self.app._release_ai_operation(self.app._ai_operation_token)
            replacement.append(self.app._reserve_ai_operation(
                direct=True, user_message="newer", origin="user",
            ))
            raise RuntimeError("old setup failed")

        self.app._continuation.start.side_effect = replace_owner
        with self.assertRaises(RuntimeError):
            self.app._ai_tick("hello", origin="user")
        self.assertTrue(self.app._ai_busy)
        self.assertIs(self.app._ai_operation_token, replacement[0])

    def test_recovery_ui_cannot_reset_successor_after_release(self):
        original_release = self.app._release_ai_operation
        successor = []
        queued = []
        self.app._schedule_ui = lambda callback: queued.append(callback) or "fake-job"

        def release_then_replace(token, **kwargs):
            released = original_release(token, **kwargs)
            if released:
                successor.append(self.app._reserve_ai_operation(
                    direct=True, user_message="successor", origin="user",
                ))
                self.input_state["state"] = "disabled"
                self.app._state = self.app.STATE_THINKING
            return released

        self.app._release_ai_operation = release_then_replace
        self.app._continuation.start.side_effect = RuntimeError("synthetic setup failure")
        with self.assertRaises(RuntimeError):
            self.app._ai_tick("hello", origin="user")
        for callback in queued:
            callback()
        self.assertEqual(self.input_state["state"], "disabled")
        self.assertEqual(self.app._state, self.app.STATE_THINKING)
        self.assertIs(self.app._ai_operation_token, successor[0])

    def test_normal_dispatch_releases_before_deferred_work(self):
        observed = []
        self.app._defer_after_ai_tick(lambda: observed.append(self.app._ai_busy))
        self.app._ai_tick("hello", origin="user")
        self.assertEqual(observed, [False])
        self._assert_slot_available()

    def test_provider_failure_still_returns_with_free_slot(self):
        self.app._ai.query.side_effect = RuntimeError("quota")
        self.app._ai_tick("hello", origin="user")
        self._assert_slot_available()
        self.assertEqual(self.input_state["state"], "normal")


if __name__ == "__main__":
    unittest.main()
