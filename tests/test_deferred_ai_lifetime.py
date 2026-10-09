"""Deferred AI handoff contracts using fake UI and provider-free work."""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from main import CompanionApp


class TestDeferredAiLifetime(unittest.TestCase):
    def setUp(self):
        self.app = CompanionApp.__new__(CompanionApp)
        app = self.app
        app._closing = False
        app._ai_tick_lock = threading.Lock()
        app._ai_busy = app._ai_busy_noninterruptible = app._speech_active = False
        app._ai_operation_token = None
        app._cancel_event = threading.Event()
        app._pending_user_message = None
        app._post_ai_tick_callbacks = []
        app._deferred_ai_callbacks_inflight = False
        app._worker_lock = threading.Lock()
        app._workers = set()
        self.input_state = {"state": "normal"}
        app._input_box = SimpleNamespace(config=lambda **v: self.input_state.update(v))
        app._re_enable_input = lambda: self.input_state.update(state="normal")
        self.notices = []
        app._subtitle = SimpleNamespace(show_message=lambda text, *_: self.notices.append(text))
        app._set_state = lambda state: setattr(app, "_state", state)
        app._reschedule_screen_poll = lambda: None
        self.ui, self.work = [], []
        app._schedule_ui = lambda callback: self.ui.append(callback) or callback

        def hold_worker(target, **_kwargs):
            self.work.append(target)
            return object()

        app._start_worker = hold_worker

    def _start(self, callback=lambda: None):
        self.app._defer_exclusive_ai_operation(callback)
        self.app._run_deferred_ai_tick_callbacks()

    def _deliver_ui(self):
        while self.ui:
            self.ui.pop(0)()

    def _assert_released(self):
        self.assertFalse(self.app._ai_busy)
        self.assertFalse(self.app._ai_busy_noninterruptible)
        self.assertIsNone(self.app._ai_operation_token)

    def test_worker_start_exception_releases_reservation(self):
        self.app._start_worker = MagicMock(side_effect=RuntimeError("synthetic start failure"))
        self._start()
        self._assert_released()
        self._deliver_ui()
        self.assertEqual(self.input_state["state"], "normal")

    def test_refused_worker_does_not_leave_late_disable_callback(self):
        self.app._start_worker = lambda *_a, **_k: None
        self._start()
        self._assert_released()
        self._deliver_ui()
        self.assertEqual(self.input_state["state"], "normal")

    def test_close_during_startup_releases_without_ui_work(self):
        effects = []
        self.app._input_box.config = lambda **_v: effects.append("input")
        self.app._re_enable_input = lambda: effects.append("restore")

        def close(*_args, **_kwargs):
            self.app._closing = True
            return None

        self.app._start_worker = close
        self._start(lambda: effects.append("work"))
        self._assert_released()
        self._deliver_ui()
        self.assertEqual(effects, [])

    def test_ui_schedule_exception_releases_reserved_slot(self):
        calls = []

        def fail_once(callback):
            calls.append(callback)
            if len(calls) == 1:
                raise RuntimeError("synthetic scheduler failure")
            self.ui.append(callback)
            return callback

        self.app._schedule_ui = fail_once
        self._start()
        self._assert_released()
        self.assertEqual(self.work, [])

    def test_failed_start_cannot_release_or_disable_replacement(self):
        successor = []

        def replace(*_args, **_kwargs):
            self.app._release_ai_operation(self.app._ai_operation_token)
            successor.append(self.app._reserve_ai_operation(
                direct=False, user_message=None, origin="tool_result", noninterruptible=True,
            ))
            self.assertIsNotNone(successor[0])
            raise RuntimeError("old worker failed to start")

        self.app._start_worker = replace
        self._start()
        self._deliver_ui()
        self.assertIs(self.app._ai_operation_token, successor[0])
        self.assertTrue(self.app._ai_busy)
        self.assertEqual(self.input_state["state"], "normal")

    def test_successful_work_owns_slot_until_completion(self):
        observed = []
        self._start(lambda: observed.append((self.app._ai_busy, self.app._ai_busy_noninterruptible)))
        self.assertTrue(self.app._ai_busy)
        self._deliver_ui()
        self.assertEqual(self.input_state["state"], "disabled")
        self.work.pop()()
        self._assert_released()
        self._deliver_ui()
        self.assertEqual(observed, [(True, True)])
        self.assertEqual(self.input_state["state"], "normal")

    def test_callback_exception_releases_and_restores_input(self):
        def fail():
            raise RuntimeError("synthetic callback failure")

        self._start(fail)
        self._deliver_ui()
        self.work.pop()()
        self._assert_released()
        self._deliver_ui()
        self.assertEqual(self.input_state["state"], "normal")

    def test_acknowledged_worker_cannot_begin_callback_after_close(self):
        effects = []
        self._start(lambda: effects.append("work"))
        self.app._closing = True
        self.work.pop()()
        self._deliver_ui()
        self.assertEqual(effects, [])
        self._assert_released()

    def test_deferred_work_runs_after_failed_handoff_release(self):
        observed = []

        def refuse(*_args, **_kwargs):
            self.app._defer_after_ai_tick(lambda: observed.append(self.app._ai_busy))
            return None

        self.app._start_worker = refuse
        self._start()
        self.assertEqual(observed, [False])

    def test_delayed_completion_ui_cannot_enable_successor_input(self):
        self._start()
        self.work.pop()()
        successor = self.app._reserve_ai_operation(direct=True, user_message="next", origin="user")
        self.input_state["state"] = "disabled"
        self._deliver_ui()
        self.assertEqual(self.input_state["state"], "disabled")
        self.assertIs(self.app._ai_operation_token, successor)

    def test_failed_thread_start_removes_worker_bookkeeping(self):
        worker = MagicMock()
        worker.ident = None
        worker.start.side_effect = RuntimeError("synthetic thread start failure")
        with patch("main.threading.Thread", return_value=worker):
            with self.assertRaisesRegex(RuntimeError, "synthetic thread start failure"):
                CompanionApp._start_worker(self.app, lambda: None, name="fake")
        self.assertEqual(self.app._workers, set())

    def test_refused_handoff_cannot_run_held_work_later(self):
        effects, held = [], []

        def refuse(target, **_kwargs):
            held.append(target)
            return None

        self.app._start_worker = refuse
        self._start(lambda: effects.append("work"))
        self._assert_released()
        held.pop()()
        self.assertEqual(effects, [])

    def test_started_work_keeps_slot_when_handoff_raises(self):
        entered, finish = threading.Event(), threading.Event()
        workers, observed = [], []

        def callback():
            entered.set()
            finish.wait(2)
            observed.append(self.app._ai_busy)

        def start_then_fail(target, **_kwargs):
            worker = threading.Thread(target=target, daemon=True)
            workers.append(worker)
            worker.start()
            self.assertTrue(entered.wait(1))
            raise RuntimeError("synthetic error after worker started")

        self.app._start_worker = start_then_fail
        try:
            self._start(callback)
            self.assertTrue(self.app._ai_busy, "started callback lost its slot")
        finally:
            finish.set()
            for worker in workers:
                worker.join(2)
                self.assertFalse(worker.is_alive())
        self._assert_released()
        self.assertEqual(observed, [True])

    def test_thread_that_started_before_error_remains_tracked_until_done(self):
        finish = threading.Event()
        workers = []
        thread_type = threading.Thread

        class StartedThenFailed(thread_type):
            def start(self):
                workers.append(self)
                super().start()
                raise RuntimeError("synthetic error after thread start")

        try:
            with patch("main.threading.Thread", StartedThenFailed):
                with self.assertRaises(RuntimeError):
                    CompanionApp._start_worker(self.app, lambda: finish.wait(2), name="fake")
            self.assertEqual(self.app._workers, set(workers))
        finally:
            finish.set()
            for worker in workers:
                worker.join(2)
                self.assertFalse(worker.is_alive())
        self.assertEqual(self.app._workers, set())

    def test_late_thread_entry_after_start_error_cannot_run_withdrawn_work(self):
        workers, observed = [], []

        class LateThread:
            ident = None

            def __init__(self, target, **_kwargs):
                self.target = target
                workers.append(self)

            def start(self):
                raise RuntimeError("synthetic error before thread entry")

        with patch("main.threading.Thread", LateThread):
            with self.assertRaises(RuntimeError):
                CompanionApp._start_worker(
                    self.app, lambda: observed.append(workers[0] in self.app._workers), name="fake",
                )
        self.assertEqual(self.app._workers, set())
        workers[0].target()
        self.assertEqual(observed, [])
        self.assertEqual(self.app._workers, set())

    def test_normal_worker_completion_removes_bookkeeping(self):
        observed = []
        worker = CompanionApp._start_worker(self.app, lambda: observed.append("work"), name="fake")
        self.assertIsNotNone(worker)
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(observed, ["work"])
        self.assertEqual(self.app._workers, set())


if __name__ == "__main__":
    unittest.main()
