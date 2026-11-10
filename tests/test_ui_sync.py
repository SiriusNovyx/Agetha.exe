"""Synchronous UI delivery contracts, using only fake UI and in-memory state."""

from __future__ import annotations

import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main


class ControlledCompletion:
    """Hold the timed wait at its deadline while the fake UI starts work."""

    def __init__(self):
        self.done = threading.Event()
        self.waiting = threading.Event()
        self.expire = threading.Event()
        self.waiting_for_started_work = threading.Event()

    def set(self):
        self.done.set()

    def is_set(self):
        return self.done.is_set()

    def wait(self, timeout=None):
        if timeout is not None:
            self.waiting.set()
            if not self.expire.wait(2):
                raise AssertionError("test did not release the timed wait")
            return self.done.is_set()
        self.waiting_for_started_work.set()
        return self.done.wait(2)


class TestSynchronousUiDelivery(unittest.TestCase):
    def setUp(self) -> None:
        self.app = main.CompanionApp.__new__(main.CompanionApp)
        self.app._closing = False
        self.app._shutdown_complete = False
        self.app._cancel_event = threading.Event()
        self.app._continuation_ui_epoch = 0
        self.app._ai_tick_lock = threading.Lock()
        self.app._ui_owner_thread_id = threading.get_ident()
        self.app._ui_callback_queue = queue.SimpleQueue()
        self.app.root = SimpleNamespace(after=lambda *_args: "fake-job")
        self.effects: list[str] = []

    def _start_call(self, callback, timeout=0.0):
        results, errors = [], []

        def call():
            try:
                results.append(self.app._call_ui_sync(callback, timeout))
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=call, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 2)
        return worker, results, errors

    def _join(self, call):
        worker, results, errors = call
        worker.join(2)
        self.assertFalse(worker.is_alive(), "synchronous UI caller did not finish")
        self.assertEqual(errors, [])
        return results

    def test_timeout_prevents_operation_when_callback_is_delivered_later(self):
        call = self._start_call(lambda: self.effects.append("effect"))
        queued = self.app._ui_callback_queue.get(timeout=2)
        self.assertEqual(self._join(call), [None])
        self.assertEqual(self.effects, [])
        queued()
        self.assertEqual(self.effects, [], "timed-out UI work executed after failure")

    def test_queued_success_returns_result_and_performs_one_effect(self):
        def operation():
            self.effects.append("effect")
            return "saved"

        call = self._start_call(operation, timeout=2)
        self.app._ui_callback_queue.get(timeout=2)()
        self.assertEqual(self._join(call), ["saved"])
        self.assertEqual(self.effects, ["effect"])

    def test_owner_thread_success_remains_synchronous(self):
        def operation():
            self.effects.append("effect")
            return 42

        self.assertEqual(self.app._call_ui_sync(operation), 42)
        self.assertEqual(self.effects, ["effect"])
        self.assertTrue(self.app._ui_callback_queue.empty())

    def test_close_before_delivery_prevents_ui_work(self):
        for close_method in ("_disable_input_for_close", "_graceful_shutdown"):
            with self.subTest(close_method=close_method):
                self.setUp()
                self.app._stop_computer_use_escape_hotkey = lambda: None
                self.app._clear_context_capture_targets = lambda: None
                self.app._graceful_shutdown_continue = lambda: None
                self.app._input_box = SimpleNamespace(config=lambda **_kwargs: None)
                call = self._start_call(lambda: self.effects.append("effect"), timeout=2)
                queued = self.app._ui_callback_queue.get(timeout=2)
                getattr(self.app, close_method)()
                queued()
                self.assertEqual(self._join(call), [None])
                self.assertEqual(self.effects, [])

    def test_cancellation_before_delivery_prevents_ui_work(self):
        call = self._start_call(lambda: self.effects.append("effect"), timeout=2)
        queued = self.app._ui_callback_queue.get(timeout=2)
        self.app._cancel_event.set()
        queued()
        self.assertEqual(self._join(call), [None])
        self.assertEqual(self.effects, [])

    def test_escape_then_cancel_reset_does_not_revive_old_work(self):
        self.app._clear_context_capture_targets = lambda: None
        self.app._stop_computer_use = lambda _reason: None
        self.app._re_enable_input = lambda: None
        call = self._start_call(lambda: self.effects.append("effect"), timeout=2)
        queued = self.app._ui_callback_queue.get(timeout=2)
        self.app._on_cancel_ai()
        self.app._cancel_event.clear()  # A following request resets this shared signal.
        queued()
        self.assertEqual(self._join(call), [None])
        self.assertEqual(self.effects, [])

    def test_escape_during_preflight_cannot_adopt_a_new_request_epoch(self):
        checked, resume = threading.Event(), threading.Event()
        cancel_event = self.app._cancel_event

        def paused_check():
            cancelled = cancel_event.is_set()
            if threading.current_thread() is not threading.main_thread():
                checked.set()
                if not resume.wait(2):
                    raise AssertionError("test did not release cancellation preflight")
            return cancelled

        self.app._cancel_event = SimpleNamespace(
            is_set=paused_check, set=cancel_event.set, clear=cancel_event.clear,
        )
        self.app._clear_context_capture_targets = lambda: None
        self.app._stop_computer_use = lambda _reason: None
        self.app._re_enable_input = lambda: None
        self.addCleanup(resume.set)
        call = self._start_call(lambda: self.effects.append("effect"), timeout=2)
        try:
            self.assertTrue(checked.wait(2))
            self.app._on_cancel_ai()
            self.app._cancel_event.clear()
        finally:
            resume.set()
        self.app._ui_callback_queue.get(timeout=2)()
        self.assertEqual(self._join(call), [None])
        self.assertEqual(self.effects, [])

    def test_closed_or_cancelled_owner_thread_call_has_no_effect(self):
        for state in ("closed", "cancelled"):
            with self.subTest(state=state):
                self.setUp()
                if state == "closed":
                    self.app._closing = True
                else:
                    self.app._cancel_event.set()
                self.assertIsNone(self.app._call_ui_sync(lambda: self.effects.append("effect")))
                self.assertEqual(self.effects, [])

    def test_started_callback_outlives_deadline_and_returns_actual_result(self):
        completion = ControlledCompletion()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.addCleanup(completion.expire.set)

        def operation():
            started.set()
            if not release.wait(2):
                raise AssertionError("test did not release the started operation")
            self.effects.append("effect")
            return "completed"

        # Only the completion clock is replaced; real threads and the queue remain.
        clock = SimpleNamespace(**vars(threading))
        clock.Event = lambda: completion
        with patch.object(main, "threading", clock):
            call = self._start_call(operation, timeout=0)
            queued = self.app._ui_callback_queue.get(timeout=2)
            self.assertTrue(completion.waiting.wait(2))

            def reach_deadline():
                if started.wait(2):
                    self.app._cancel_event.set()
                    self.app._closing = True
                    completion.expire.set()
                    try:
                        self.assertTrue(completion.waiting_for_started_work.wait(2))
                        self.assertEqual(call[1], [], "started work was reported as timed out")
                    finally:
                        release.set()

            deadline_errors = []

            def observe_deadline():
                try:
                    reach_deadline()
                except BaseException as exc:
                    deadline_errors.append(exc)

            observer = threading.Thread(target=observe_deadline, daemon=True)
            observer.start()
            try:
                queued()  # Delivery remains on the UI owner thread.
            finally:
                release.set()
                completion.expire.set()
                observer.join(2)
            self.assertFalse(observer.is_alive())
            self.assertEqual(deadline_errors, [])
            self.assertEqual(self._join(call), ["completed"])
        self.assertEqual(self.effects, ["effect"])

    def test_callback_exception_releases_worker_and_still_reaches_ui_handler(self):
        def operation():
            raise ValueError("synthetic UI failure")

        call = self._start_call(operation, timeout=2)
        queued = self.app._ui_callback_queue.get(timeout=2)
        with self.assertRaisesRegex(ValueError, "synthetic UI failure"):
            queued()
        self.assertEqual(self._join(call), [None])
        self.assertEqual(self.effects, [])

    def test_owner_thread_callback_exception_still_propagates(self):
        def operation():
            raise ValueError("synthetic UI failure")

        with self.assertRaisesRegex(ValueError, "synthetic UI failure"):
            self.app._call_ui_sync(operation)

    def test_expired_call_does_not_invalidate_a_following_success(self):
        first = self._start_call(lambda: self.effects.append("expired"))
        old_callback = self.app._ui_callback_queue.get(timeout=2)
        self.assertEqual(self._join(first), [None])
        second = self._start_call(lambda: self.effects.append("current") or "ok", timeout=2)
        current_callback = self.app._ui_callback_queue.get(timeout=2)
        old_callback()
        current_callback()
        self.assertEqual(self._join(second), ["ok"])
        self.assertEqual(self.effects, ["current"])

    def test_schedule_rejection_returns_none_without_effect(self):
        self.app._schedule_ui = lambda _callback: None
        self.assertEqual(self._join(self._start_call(lambda: self.effects.append("effect"))), [None])
        self.assertEqual(self.effects, [])


if __name__ == "__main__":
    unittest.main()
