from __future__ import annotations

import threading
import unittest
import queue
from unittest.mock import MagicMock

import main
from agetha.core.context_dependencies import (
    ContextKind,
    ContextOutcome,
    ContextRequest,
    UnresolvedContextObjectiveStore,
)
from agetha.core.continuation import ContinuationEngine, DecisionKind


class ContinuationMainIntegrationTests(unittest.TestCase):
    def test_legacy_screen_signal_translates_to_typed_dependency(self) -> None:
        request = main.CompanionApp._context_request_from_model_response(
            {"command": "request_screen_read"},
        )

        self.assertEqual(request, ContextRequest(ContextKind.SCREEN))
        self.assertIsNone(
            main.CompanionApp._context_request_from_model_response(
                {"command": "speak", "segments": [{"text": "hi"}]},
            ),
        )

    def test_context_signal_routing_preserves_goal_and_exhausts_repeat(self) -> None:
        engine = ContinuationEngine(id_factory=lambda: "session:screen")
        started = engine.start("Describe what I am looking at", authority_origin="user")

        run_context = main.CompanionApp._accept_continuation_response(
            engine,
            (started.session_id, started.generation),
            {"command": "request_screen_read"},
            request_origin="user",
        )
        continued = engine.accept_context_outcome(
            started.session_id,
            started.generation,
            ContextOutcome(
                ContextKind.SCREEN,
                False,
                "target_unavailable",
                "[The current screen context is unavailable.]",
            ),
        )
        repeated = main.CompanionApp._accept_continuation_response(
            engine,
            (started.session_id, started.generation),
            {"command": "request_screen_read"},
            request_origin="tool_result",
        )

        self.assertIs(run_context.kind, DecisionKind.RUN_CONTEXT)
        self.assertEqual(
            continued.snapshot.original_user_message,
            "Describe what I am looking at",
        )
        self.assertIs(repeated.kind, DecisionKind.STOPPED)
        self.assertEqual(repeated.reason, "repeated_context_dependency")

    def test_context_worker_continues_same_original_goal_after_one_acquisition(self) -> None:
        engine = ContinuationEngine(id_factory=lambda: "session:screen")
        started = engine.start("What am I looking at?", authority_origin="user")
        run_context = engine.accept_context_request(
            started.session_id,
            started.generation,
            ContextRequest(ContextKind.SCREEN),
            request_origin="user",
        )
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._continuation = engine
        app._acquire_read_only_context = MagicMock(
            return_value=ContextOutcome(
                ContextKind.SCREEN,
                True,
                "ocr_complete",
                "[UNTRUSTED SCREEN OCR]\nCompiler error",
            ),
        )
        app._handle_continuation_decision = MagicMock()
        app._unresolved_context_objectives = UnresolvedContextObjectiveStore()

        app._run_continuation_context(run_context)

        app._acquire_read_only_context.assert_called_once()
        continued = app._handle_continuation_decision.call_args.args[0]
        self.assertIs(continued.kind, DecisionKind.CALL_PROVIDER)
        self.assertEqual(
            continued.snapshot.original_user_message,
            "What am I looking at?",
        )
        self.assertEqual(len(continued.snapshot.context_history), 1)

    def test_failed_context_preserves_short_lived_direct_user_objective(self) -> None:
        clock = MagicMock(return_value=10.0)
        store = UnresolvedContextObjectiveStore(clock=clock, ttl_seconds=30)
        engine = ContinuationEngine(id_factory=lambda: "session:screen")
        started = engine.start("What am I looking at?", authority_origin="user")
        run_context = engine.accept_context_request(
            started.session_id,
            started.generation,
            ContextRequest(ContextKind.SCREEN),
            request_origin="user",
        )
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._continuation = engine
        app._acquire_read_only_context = MagicMock(
            return_value=ContextOutcome(
                ContextKind.SCREEN,
                False,
                "target_unavailable",
                "[The current screen context is unavailable.]",
            ),
        )
        app._handle_continuation_decision = MagicMock()
        app._unresolved_context_objectives = store

        app._run_continuation_context(run_context)

        pending = store.current()
        self.assertIsNotNone(pending)
        self.assertEqual(pending.message, "What am I looking at?")
        self.assertEqual(pending.kind, ContextKind.SCREEN)

    def test_cancelled_context_worker_clears_unresolved_objective(self) -> None:
        store = UnresolvedContextObjectiveStore()
        store.remember("What am I looking at?", ContextKind.SCREEN, origin="user")
        engine = ContinuationEngine(id_factory=lambda: "session:screen")
        started = engine.start("What now?", authority_origin="user")
        run_context = engine.accept_context_request(
            started.session_id,
            started.generation,
            ContextRequest(ContextKind.SCREEN),
            request_origin="user",
        )
        engine.cancel_active("escape")
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._continuation = engine
        app._acquire_read_only_context = MagicMock()
        app._handle_continuation_decision = MagicMock()
        app._unresolved_context_objectives = store

        app._run_continuation_context(run_context)

        self.assertIsNone(store.current())
        app._acquire_read_only_context.assert_not_called()

    def test_successful_context_final_records_and_clears_exactly_once(self) -> None:
        store = UnresolvedContextObjectiveStore()
        store.remember("What am I looking at?", ContextKind.SCREEN, origin="user")
        engine = ContinuationEngine(id_factory=lambda: "session:screen")
        started = engine.start("What am I looking at?", authority_origin="user")
        run_context = engine.accept_context_request(
            started.session_id,
            started.generation,
            ContextRequest(ContextKind.SCREEN),
            request_origin="user",
        )
        engine.accept_context_outcome(
            started.session_id,
            started.generation,
            ContextOutcome(
                ContextKind.SCREEN,
                True,
                "ocr_complete",
                "[UNTRUSTED SCREEN OCR]\nCompiler error raw OCR",
            ),
        )
        final = engine.accept_continuation_model_response(
            started.session_id,
            started.generation,
            {
                "command": "speak",
                "mood": "neutral",
                "segments": [{"text": "That is a compiler error.", "pause": 0.0}],
            },
        )
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._continuation = engine
        app._unresolved_context_objectives = store
        app._completed_context_history_sessions = set()
        app._ai = MagicMock()
        app._speak_and_continue = MagicMock()

        app._handle_continuation_decision(final)
        app._handle_continuation_decision(final)

        app._ai.record_context_continuation_turn.assert_called_once()
        recorded = app._ai.record_context_continuation_turn.call_args.args
        self.assertEqual(recorded[0], "What am I looking at?")
        self.assertNotIn("Compiler error raw OCR", str(recorded[1]))
        self.assertIsNone(store.current())

    def test_unresolved_objective_is_direct_user_only_and_topic_change_clears(self) -> None:
        store = UnresolvedContextObjectiveStore()
        store.remember("Describe my screen", ContextKind.SCREEN, origin="user")
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._unresolved_context_objectives = store

        direct_context = app._recent_unresolved_context_for_prompt("user")
        ambient_context = app._recent_unresolved_context_for_prompt("ambient")
        app._clear_unresolved_context_if_topic_changed(
            {"command": "speak", "segments": [{"text": "Hello"}]},
        )

        self.assertIn("Describe my screen", direct_context)
        self.assertEqual(ambient_context, "")
        self.assertIsNone(store.current())

    def test_what_now_screen_dependency_keeps_recent_objective_until_completion(self) -> None:
        store = UnresolvedContextObjectiveStore()
        store.remember("Describe my screen", ContextKind.SCREEN, origin="user")
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._unresolved_context_objectives = store

        app._clear_unresolved_context_if_topic_changed(
            {"command": "request_screen_read"},
        )

        self.assertIsNotNone(store.current())

    def test_repeated_context_exhaustion_has_useful_deterministic_message(self) -> None:
        engine = ContinuationEngine(id_factory=lambda: "session:screen")
        started = engine.start("What is on my screen?", authority_origin="user")
        engine.accept_context_request(
            started.session_id,
            started.generation,
            ContextRequest(ContextKind.SCREEN),
            request_origin="user",
        )
        engine.accept_context_outcome(
            started.session_id,
            started.generation,
            ContextOutcome(
                ContextKind.SCREEN,
                False,
                "target_unavailable",
                "[The current screen context is unavailable.]",
            ),
        )
        stopped = engine.accept_context_request(
            started.session_id,
            started.generation,
            ContextRequest(ContextKind.SCREEN),
            request_origin="tool_result",
        )
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._continuation = engine
        app._subtitle = MagicMock()
        app._set_state = MagicMock()
        app._reschedule_screen_poll = MagicMock()
        app._schedule_ui = lambda callback: callback()

        app._handle_continuation_decision(stopped)

        text = app._subtitle.show_message.call_args.args[0]
        self.assertIn("bring the relevant window forward", text)
        self.assertNotIn("screen reader", text.casefold())
        self.assertNotIn("authorized", text.casefold())

    def test_repeated_context_fallback_records_one_final_turn(self) -> None:
        engine = ContinuationEngine(id_factory=lambda: "session:screen")
        started = engine.start("What is on my screen?", authority_origin="user")
        engine.accept_context_request(
            started.session_id,
            started.generation,
            ContextRequest(ContextKind.SCREEN),
            request_origin="user",
        )
        engine.accept_context_outcome(
            started.session_id,
            started.generation,
            ContextOutcome(
                ContextKind.SCREEN,
                False,
                "target_unavailable",
                "[The current screen context is unavailable.]",
            ),
        )
        stopped = engine.accept_context_request(
            started.session_id,
            started.generation,
            ContextRequest(ContextKind.SCREEN),
            request_origin="tool_result",
        )
        store = UnresolvedContextObjectiveStore()
        store.remember(
            "What is on my screen?",
            ContextKind.SCREEN,
            origin="user",
        )
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._continuation = engine
        app._completed_context_history_sessions = set()
        app._unresolved_context_objectives = store
        app._ai = MagicMock()
        app._subtitle = MagicMock()
        app._set_state = MagicMock()
        app._reschedule_screen_poll = MagicMock()
        app._schedule_ui = lambda callback: callback()

        app._handle_continuation_decision(stopped)
        app._handle_continuation_decision(stopped)

        app._ai.record_context_continuation_turn.assert_called_once()
        recorded = app._ai.record_context_continuation_turn.call_args.args[1]
        self.assertIn("bring the relevant window forward", recorded["segments"][0]["text"])
        self.assertIsNotNone(store.current())

    def test_provider_failure_after_context_records_useful_final_turn(self) -> None:
        engine = ContinuationEngine(id_factory=lambda: "session:screen")
        started = engine.start("Explain this error", authority_origin="user")
        engine.accept_context_request(
            started.session_id,
            started.generation,
            ContextRequest(ContextKind.SCREEN),
            request_origin="user",
        )
        engine.accept_context_outcome(
            started.session_id,
            started.generation,
            ContextOutcome(
                ContextKind.SCREEN,
                False,
                "target_unavailable",
                "[The current screen context is unavailable.]",
            ),
        )
        stopped = engine.provider_failed(
            started.session_id,
            started.generation,
            "provider_error",
        )
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._continuation = engine
        app._completed_context_history_sessions = set()
        app._unresolved_context_objectives = UnresolvedContextObjectiveStore()
        app._ai = MagicMock()
        app._subtitle = MagicMock()
        app._set_state = MagicMock()
        app._reschedule_screen_poll = MagicMock()
        app._schedule_ui = lambda callback: callback()

        app._handle_continuation_decision(stopped)

        app._ai.record_context_continuation_turn.assert_called_once()
        recorded = app._ai.record_context_continuation_turn.call_args.args[1]
        self.assertIn("current screen context", recorded["segments"][0]["text"])
        self.assertNotIn("provider", recorded["segments"][0]["text"].casefold())

    def test_provider_failure_after_successful_context_is_not_reported_as_denial(self) -> None:
        engine = ContinuationEngine(id_factory=lambda: "session:screen")
        started = engine.start("Explain this error", authority_origin="user")
        engine.accept_context_request(
            started.session_id,
            started.generation,
            ContextRequest(ContextKind.SCREEN),
            request_origin="user",
        )
        engine.accept_context_outcome(
            started.session_id,
            started.generation,
            ContextOutcome(
                ContextKind.SCREEN,
                True,
                "ocr_complete",
                "[UNTRUSTED SCREEN OCR]\nBuild failed",
            ),
        )
        stopped = engine.provider_failed(
            started.session_id,
            started.generation,
            "provider_error",
        )
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._continuation = engine
        app._completed_context_history_sessions = set()
        app._unresolved_context_objectives = UnresolvedContextObjectiveStore()
        app._ai = MagicMock()
        app._subtitle = MagicMock()
        app._set_state = MagicMock()
        app._reschedule_screen_poll = MagicMock()
        app._schedule_ui = lambda callback: callback()

        app._handle_continuation_decision(stopped)

        recorded = app._ai.record_context_continuation_turn.call_args.args[1]
        text = recorded["segments"][0]["text"].casefold()
        self.assertIn("couldn't finish", text)
        self.assertNotIn("authorized", text)
        self.assertNotIn("provider", text)

    def test_screen_question_acquires_and_answers_in_same_logical_turn(self) -> None:
        engine = ContinuationEngine(id_factory=lambda: "session:screen")
        started = engine.start("What am I looking at?", authority_origin="user")
        first = main.CompanionApp._accept_continuation_response(
            engine,
            (started.session_id, started.generation),
            {"command": "request_screen_read"},
            request_origin="user",
        )
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._continuation = engine
        app._continuation_ui_epoch = 0
        app._unresolved_context_objectives = UnresolvedContextObjectiveStore()
        app._completed_context_history_sessions = set()
        app._acquire_read_only_context = MagicMock(
            return_value=ContextOutcome(
                ContextKind.SCREEN,
                True,
                "ocr_complete",
                "[UNTRUSTED SCREEN OCR]\nBuild failed: missing symbol",
            ),
        )
        app._ai_query = MagicMock(return_value={
            "command": "speak",
            "segments": [{"text": "The build is missing a symbol.", "pause": 0.0}],
        })
        app._ai = MagicMock()
        app._speak_and_continue = MagicMock()
        app._start_worker = lambda target, *, name, args: target(*args)

        app._handle_continuation_decision(first)

        app._acquire_read_only_context.assert_called_once()
        app._ai_query.assert_called_once()
        self.assertEqual(app._ai_query.call_args.args[0], "What am I looking at?")
        self.assertIn("missing symbol", app._ai_query.call_args.kwargs["doc_content"])
        app._speak_and_continue.assert_called_once_with(
            [{"text": "The build is missing a symbol.", "pause": 0.0}],
            "neutral",
            False,
        )
        app._ai.record_context_continuation_turn.assert_called_once()

    def test_unquoted_authorized_path_stops_before_followup_instruction(self) -> None:
        cases = (
            (r"Read C:\temp\report.txt and summarize it", "c:/temp/report.txt"),
            ("Read /tmp/report.txt and summarize it", "/tmp/report.txt"),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                resources = main.CompanionApp._continuation_resources_from_user(message)
                paths = {
                    resource.value
                    for resource in resources
                    if resource.kind == "path"
                }
                self.assertEqual(paths, {expected})

    def test_quoted_authorized_path_keeps_instruction_words_in_filename(self) -> None:
        resources = main.CompanionApp._continuation_resources_from_user(
            'Read "C:\\Research and Review\\report.txt"',
        )

        paths = {resource.value for resource in resources if resource.kind == "path"}
        self.assertEqual(paths, {"c:/research and review/report.txt"})

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

    def test_cancelled_continuation_cannot_transmit_private_provider_context(self) -> None:
        current = {"value": True}
        transmitted: list[str] = []

        class AuthorizationAwareAI:
            def query(self, **kwargs):
                current["value"] = False
                authorization = kwargs.get("provider_authorization")
                if authorization is None or authorization():
                    transmitted.append(kwargs["doc_content"])
                return {"command": "idle"}

        app = main.CompanionApp.__new__(main.CompanionApp)
        app._cancel_event = threading.Event()
        app._ai = AuthorizationAwareAI()
        app._screen = None
        app._last_screen_text = ""
        app._reserve_ai_operation = MagicMock(return_value=object())
        app._release_ai_operation = MagicMock()
        app._drain_pending_user_message = MagicMock()
        app._schedule_owned_ai_ui = MagicMock()

        runtime_settings = type("Settings", (), {"enable_streaming": False})()
        with unittest.mock.patch.object(main, "_SETTINGS", runtime_settings):
            result = app._ai_query(
                "continue",
                screen_context="",
                doc_content="private local notes",
                result_is_current=lambda: current["value"],
            )

        self.assertIsNone(result)
        self.assertEqual(transmitted, [])


if __name__ == "__main__":
    unittest.main()
