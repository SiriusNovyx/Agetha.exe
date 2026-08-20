from __future__ import annotations

import threading
import unittest
import queue
from unittest.mock import MagicMock

import main
from agetha.core.continuation import ContinuationEngine


class ContinuationMainIntegrationTests(unittest.TestCase):
    def test_sensitive_outbound_authority_requires_explicit_transfer_to_web(self) -> None:
        allows = main.CompanionApp._allows_sensitive_outbound_continuation

        self.assertFalse(allows("search my memory for project alpha"))
        self.assertFalse(allows("search the web for private equity news"))
        self.assertFalse(allows("search my notes and also search the web"))
        self.assertFalse(allows("never send my local notes online"))
        self.assertFalse(allows("don't share my private data on the internet"))
        self.assertFalse(allows("Do not use my local documents to search the web"))
        self.assertFalse(allows("avoid uploading my files to the internet"))
        self.assertFalse(allows("search online without using my local notes"))
        self.assertTrue(allows("search the web using my notes"))
        self.assertTrue(allows("upload my local document to the web"))

    def test_worker_continuation_decision_only_enqueues_ui_work(self) -> None:
        engine = ContinuationEngine(id_factory=lambda: "session:1")
        started = engine.start("hello", authority_origin="user")
        final = engine.accept_initial_model_response(
            started.session_id,
            started.generation,
            {
                "command": "speak",
                "segments": [{"text": "done", "pause": 0.0}],
            },
        )
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._continuation = engine
        queued: list[object] = []
        app._schedule_ui = lambda callback: queued.append(callback) or object()
        app._speak_and_continue = MagicMock()

        worker = threading.Thread(
            target=app._handle_continuation_decision,
            args=(final,),
        )
        worker.start()
        worker.join(2.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(queued), 1)
        app._speak_and_continue.assert_not_called()

        queued.pop()()
        app._speak_and_continue.assert_called_once()

    def test_worker_ui_schedule_uses_queue_without_touching_tk(self) -> None:
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app.root = MagicMock()
        app._ui_owner_thread_id = threading.get_ident()
        app._ui_callback_queue = queue.SimpleQueue()
        app._ui_queue_poll_job = None
        callback = MagicMock()
        result = []

        worker = threading.Thread(
            target=lambda: result.append(app._schedule_ui(callback)),
        )
        worker.start()
        worker.join(2.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [callback])
        app.root.after.assert_not_called()
        callback.assert_not_called()

        app._drain_ui_queue()
        callback.assert_called_once()
        app.root.after.assert_called_once()

    def test_preempted_final_is_discarded_before_queued_ui_runs(self) -> None:
        ids = iter(("session:1", "session:2"))
        engine = ContinuationEngine(id_factory=lambda: next(ids))
        started = engine.start("first", authority_origin="user")
        final = engine.accept_initial_model_response(
            started.session_id,
            started.generation,
            {
                "command": "speak",
                "segments": [{"text": "old result", "pause": 0.0}],
            },
        )
        engine.start("second", authority_origin="user")
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._continuation = engine
        app._speak_and_continue = MagicMock()

        app._handle_continuation_decision(final)

        app._speak_and_continue.assert_not_called()

    def test_escape_invalidates_final_queued_after_provider_completion(self) -> None:
        engine = ContinuationEngine(id_factory=lambda: "session:1")
        started = engine.start("hello", authority_origin="user")
        final = engine.accept_initial_model_response(
            started.session_id,
            started.generation,
            {
                "command": "speak",
                "segments": [{"text": "too late", "pause": 0.0}],
            },
        )
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._continuation = engine
        app._continuation_ui_epoch = 0
        app._speak_and_continue = MagicMock()
        queued = []
        app._schedule_ui = lambda callback: queued.append(callback) or callback

        worker = threading.Thread(
            target=app._handle_continuation_decision,
            args=(final,),
        )
        worker.start()
        worker.join(2.0)
        self.assertEqual(len(queued), 1)

        app._invalidate_continuation_ui_delivery()
        engine.cancel_active("escape")
        queued.pop()()

        app._speak_and_continue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
