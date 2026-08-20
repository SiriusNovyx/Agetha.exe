"""Deterministic tests for the standalone continuation state machine."""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from agetha.core.continuation import (
    AUTOMATIC_READ_ONLY_COMMANDS,
    AuthorizedResource,
    ContinuationEngine,
    ContinuationState,
    DecisionKind,
    ToolOutcome,
)
from agetha.core.context_dependencies import (
    ContextKind,
    ContextOutcome,
    ContextRequest,
)


class FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def outcome(
    tool: str,
    context: str = "result",
    *,
    sensitivity: str = "public",
    continuation_allowed: bool = True,
    discovered_resources: tuple[AuthorizedResource, ...] = (),
) -> ToolOutcome:
    return ToolOutcome(
        tool=tool,
        success=True,
        summary=f"{tool} completed",
        provider_context=context,
        sensitivity=sensitivity,
        continuation_allowed=continuation_allowed,
        discovered_resources=discovered_resources,
    )


class ContinuationTestCase(unittest.TestCase):
    def engine(self, clock: FakeClock | None = None, **kwargs) -> ContinuationEngine:
        sequence = iter(f"session-{i}" for i in range(1, 100))
        return ContinuationEngine(
            clock=clock or FakeClock(),
            id_factory=lambda: next(sequence),
            **kwargs,
        )

    @staticmethod
    def ids(decision) -> tuple[str, int]:
        return decision.session_id, decision.generation


class TestAuthorityAndOwnership(ContinuationTestCase):
    def test_only_direct_user_authority_can_start(self) -> None:
        engine = self.engine()
        for origin in (
            "ambient", "tool_result", "terminal_sentinel", "reminder", "touch", "file_drop",
        ):
            with self.subTest(origin=origin):
                denied = engine.start("do something", authority_origin=origin)
                self.assertEqual(denied.kind, DecisionKind.BLOCKED)
                self.assertEqual(denied.reason, "direct_user_authority_required")
                self.assertIsNone(engine.active_snapshot())

        started = engine.start("research this", authority_origin="user")
        self.assertEqual(started.kind, DecisionKind.STARTED)
        self.assertEqual(started.snapshot.authority_origin, "user")

    def test_bare_tool_result_cannot_start_or_borrow_authority(self) -> None:
        engine = self.engine()
        ignored = engine.accept_continuation_model_response(
            "missing", 1, {"command": "system_info"},
        )
        self.assertEqual(ignored.kind, DecisionKind.IGNORED)

        started = engine.start("inspect system", authority_origin="user")
        sid, generation = self.ids(started)
        wrong_origin = engine.accept_continuation_model_response(
            sid, generation, {"command": "system_info"},
        )
        self.assertEqual(wrong_origin.kind, DecisionKind.IGNORED)
        self.assertEqual(engine.active_snapshot().state, ContinuationState.AWAITING_MODEL)

        accepted = engine.accept_initial_model_response(
            sid, generation, {"command": "system_info"},
        )
        self.assertEqual(accepted.kind, DecisionKind.RUN_TOOL)

    def test_new_user_preempts_and_late_callbacks_are_ignored(self) -> None:
        engine = self.engine()
        first = engine.start("first", authority_origin="user")
        old_id, old_generation = self.ids(first)
        status = engine.accept_initial_model_response(
            old_id,
            old_generation,
            {
                "command": "system_info",
                "segments": [{"text": "Checking.", "pause": 0.1}],
            },
        )
        self.assertEqual(status.kind, DecisionKind.STATUS)

        second = engine.start("second", authority_origin="user")
        self.assertGreater(second.generation, old_generation)
        self.assertTrue(engine.cancel_requested(old_id, old_generation))
        stale_status = engine.status_finished(old_id, old_generation)
        stale_outcome = engine.accept_tool_outcome(
            old_id, old_generation, outcome("system_info"),
        )
        self.assertEqual(stale_status.kind, DecisionKind.IGNORED)
        self.assertEqual(stale_outcome.kind, DecisionKind.IGNORED)
        self.assertEqual(engine.active_snapshot().session_id, second.session_id)

    def test_snapshots_outcomes_and_decisions_are_immutable(self) -> None:
        engine = self.engine()
        started = engine.start("goal", authority_origin="user")
        with self.assertRaises(FrozenInstanceError):
            started.snapshot.step = 99
        item = outcome("system_info")
        with self.assertRaises(FrozenInstanceError):
            item.summary = "changed"
        with self.assertRaises(FrozenInstanceError):
            started.reason = "changed"


class TestBoundsAndCancellation(ContinuationTestCase):
    def test_max_step_limit_stops_before_another_tool(self) -> None:
        engine = self.engine(max_steps=1)
        started = engine.start("inspect", authority_origin="user")
        sid, generation = self.ids(started)
        first = engine.accept_initial_model_response(
            sid, generation, {"command": "system_info"},
        )
        self.assertEqual(first.kind, DecisionKind.RUN_TOOL)
        continuation = engine.accept_tool_outcome(
            sid, generation, outcome("system_info"),
        )
        self.assertEqual(continuation.kind, DecisionKind.CALL_PROVIDER)
        stopped = engine.accept_continuation_model_response(
            sid, generation, {"command": "recycle_bin_status"},
        )
        self.assertEqual(stopped.kind, DecisionKind.STOPPED)
        self.assertEqual(stopped.reason, "max_steps_reached")
        self.assertIsNone(engine.active_snapshot())

    def test_deadline_uses_injected_clock_without_sleep(self) -> None:
        clock = FakeClock()
        engine = self.engine(clock, max_duration_sec=5)
        started = engine.start("goal", authority_origin="user")
        sid, generation = self.ids(started)
        clock.advance(5)
        expired = engine.accept_initial_model_response(
            sid, generation, {"command": "system_info"},
        )
        self.assertEqual(expired.kind, DecisionKind.STOPPED)
        self.assertEqual(expired.reason, "deadline_exceeded")
        self.assertTrue(engine.cancel_requested(sid, generation))

    def test_cancel_and_shutdown_invalidate_callbacks(self) -> None:
        engine = self.engine()
        started = engine.start("goal", authority_origin="user")
        sid, generation = self.ids(started)
        cancelled = engine.cancel_active("escape")
        self.assertEqual(cancelled.kind, DecisionKind.STOPPED)
        self.assertEqual(cancelled.snapshot.state, ContinuationState.CANCELLED)
        self.assertEqual(
            engine.accept_initial_model_response(
                sid, generation, {"command": "system_info"},
            ).kind,
            DecisionKind.IGNORED,
        )

        active = engine.start("new goal", authority_origin="user")
        stopped = engine.shutdown()
        self.assertEqual(stopped.kind, DecisionKind.STOPPED)
        self.assertEqual(stopped.reason, "shutdown")
        self.assertTrue(engine.cancel_requested(active.session_id, active.generation))
        self.assertEqual(engine.shutdown().reason, "already_shutdown")
        self.assertEqual(
            engine.start("later", authority_origin="user").kind,
            DecisionKind.BLOCKED,
        )

    def test_result_truncation_and_history_are_bounded(self) -> None:
        engine = self.engine(max_tool_result_chars=8, max_history=2, max_steps=4)
        started = engine.start("inspect", authority_origin="user")
        sid, generation = self.ids(started)
        commands = ("system_info", "recycle_bin_status", "view_memory")
        for index, command in enumerate(commands):
            response = {"command": command}
            decision = (
                engine.accept_initial_model_response(sid, generation, response)
                if index == 0
                else engine.accept_continuation_model_response(sid, generation, response)
            )
            self.assertEqual(decision.kind, DecisionKind.RUN_TOOL)
            continued = engine.accept_tool_outcome(
                sid,
                generation,
                outcome(command, context=f"payload-{index}-is-too-long"),
            )
            self.assertEqual(continued.kind, DecisionKind.CALL_PROVIDER)
            self.assertLessEqual(len(continued.provider_context), 8)
            self.assertTrue(continued.provider_context.endswith("…"))

        snapshot = engine.active_snapshot()
        self.assertEqual(len(snapshot.history), 2)
        self.assertEqual(
            tuple(item.tool for item in snapshot.history),
            ("recycle_bin_status", "view_memory"),
        )


class TestPolicy(ContinuationTestCase):
    def test_state_changing_or_unknown_response_is_blocked(self) -> None:
        engine = self.engine()
        started = engine.start("inspect", authority_origin="user")
        blocked = engine.accept_initial_model_response(
            started.session_id,
            started.generation,
            {"command": "delete_file", "path": "C:/keep.txt"},
        )
        self.assertEqual(blocked.kind, DecisionKind.BLOCKED)
        self.assertEqual(blocked.reason, "state_changing_or_unknown_command")
        self.assertIsNone(engine.active_snapshot())
        self.assertNotIn("delete_file", AUTOMATIC_READ_ONLY_COMMANDS)

    def test_exact_command_argument_cycle_is_blocked(self) -> None:
        engine = self.engine()
        started = engine.start("check memory", authority_origin="user")
        sid, generation = self.ids(started)
        request = {"command": "search_memory", "query": "cat", "limit": 5}
        first = engine.accept_initial_model_response(sid, generation, request)
        self.assertEqual(first.kind, DecisionKind.RUN_TOOL)
        engine.accept_tool_outcome(sid, generation, outcome("search_memory"))
        repeated = engine.accept_continuation_model_response(sid, generation, request)
        self.assertEqual(repeated.kind, DecisionKind.BLOCKED)
        self.assertEqual(repeated.reason, "repeated_tool_cycle")

    def test_resource_tools_require_exact_session_capability(self) -> None:
        url = AuthorizedResource("url", "https://example.com/docs")
        denied_engine = self.engine()
        denied = denied_engine.start("read page", authority_origin="user")
        blocked = denied_engine.accept_initial_model_response(
            denied.session_id,
            denied.generation,
            {"command": "fetch_webpage", "url": "https://example.com/docs"},
        )
        self.assertEqual(blocked.kind, DecisionKind.BLOCKED)
        self.assertEqual(blocked.reason, "resource_not_authorized:url")

        engine = self.engine()
        started = engine.start(
            "read page",
            authority_origin="user",
            authorized_resources=(url,),
        )
        allowed = engine.accept_initial_model_response(
            started.session_id,
            started.generation,
            {"command": "fetch_webpage", "url": "https://EXAMPLE.com/docs#section"},
        )
        self.assertEqual(allowed.kind, DecisionKind.RUN_TOOL)

    def test_sensitive_local_context_cannot_flow_to_web_without_user_grant(self) -> None:
        engine = self.engine()
        started = engine.start("search my memory", authority_origin="user")
        sid, generation = self.ids(started)
        engine.accept_initial_model_response(
            sid, generation, {"command": "search_memory", "query": "account"},
        )
        engine.accept_tool_outcome(
            sid,
            generation,
            outcome("search_memory", "private result", sensitivity="private"),
        )
        blocked = engine.accept_continuation_model_response(
            sid, generation, {"command": "search_web", "query": "follow up"},
        )
        self.assertEqual(blocked.kind, DecisionKind.BLOCKED)
        self.assertEqual(blocked.reason, "sensitive_context_cannot_cross_to_web")

        granted = self.engine()
        started = granted.start(
            "search my memory then the web",
            authority_origin="user",
            allow_sensitive_outbound=True,
        )
        sid, generation = self.ids(started)
        granted.accept_initial_model_response(
            sid, generation, {"command": "search_memory", "query": "account"},
        )
        granted.accept_tool_outcome(
            sid,
            generation,
            outcome("search_memory", "private result", sensitivity="private"),
        )
        allowed = granted.accept_continuation_model_response(
            sid, generation, {"command": "search_web", "query": "follow up"},
        )
        self.assertEqual(allowed.kind, DecisionKind.RUN_TOOL)

    def test_secret_or_disallowed_tool_outcome_stops(self) -> None:
        for sensitivity, continuation_allowed, expected in (
            ("secret", True, "tool_outcome_too_sensitive"),
            ("public", False, "tool_outcome_disallows_continuation"),
        ):
            with self.subTest(sensitivity=sensitivity, allowed=continuation_allowed):
                engine = self.engine()
                started = engine.start("inspect", authority_origin="user")
                engine.accept_initial_model_response(
                    started.session_id,
                    started.generation,
                    {"command": "system_info"},
                )
                stopped = engine.accept_tool_outcome(
                    started.session_id,
                    started.generation,
                    outcome(
                        "system_info",
                        sensitivity=sensitivity,
                        continuation_allowed=continuation_allowed,
                    ),
                )
                self.assertEqual(stopped.reason, expected)
                self.assertIsNone(engine.active_snapshot())


class TestWorkflow(ContinuationTestCase):
    def test_search_fetch_statuses_then_final_without_recursion(self) -> None:
        engine = self.engine(max_steps=4)
        started = engine.start("What changed in Python?", authority_origin="user")
        sid, generation = self.ids(started)

        search_status = engine.accept_initial_model_response(
            sid,
            generation,
            {
                "command": "search_web",
                "query": "Python changes",
                "segments": [{"text": "Let me search.", "pause": 0.0}],
            },
        )
        self.assertEqual(search_status.kind, DecisionKind.STATUS)
        self.assertEqual(search_status.final_message, "Let me search.")
        run_search = engine.status_finished(sid, generation)
        self.assertEqual(run_search.kind, DecisionKind.RUN_TOOL)
        self.assertEqual(run_search.tool_request.command, "search_web")

        page_url = AuthorizedResource("url", "https://docs.python.org/3/whatsnew/")
        after_search = engine.accept_tool_outcome(
            sid,
            generation,
            outcome(
                "search_web",
                "search hits",
                discovered_resources=(page_url,),
            ),
        )
        self.assertEqual(after_search.kind, DecisionKind.CALL_PROVIDER)
        self.assertEqual(after_search.snapshot.state, ContinuationState.AWAITING_MODEL)

        fetch_status = engine.accept_continuation_model_response(
            sid,
            generation,
            {
                "command": "fetch_webpage",
                "url": "https://docs.python.org/3/whatsnew/",
                "segments": [{"text": "Found the docs. Reading them.", "pause": 0.0}],
            },
        )
        self.assertEqual(fetch_status.kind, DecisionKind.STATUS)
        run_fetch = engine.status_finished(sid, generation)
        self.assertEqual(run_fetch.kind, DecisionKind.RUN_TOOL)
        self.assertEqual(run_fetch.tool_request.command, "fetch_webpage")

        after_fetch = engine.accept_tool_outcome(
            sid,
            generation,
            outcome("fetch_webpage", "Python added several features."),
        )
        self.assertEqual(after_fetch.kind, DecisionKind.CALL_PROVIDER)
        final = engine.accept_continuation_model_response(
            sid,
            generation,
            {
                "command": "speak",
                "segments": [{"text": "The main changes are ...", "pause": 0.0}],
            },
        )
        self.assertEqual(final.kind, DecisionKind.FINAL)
        self.assertEqual(final.final_message, "The main changes are ...")
        self.assertEqual(final.snapshot.step, 2)
        self.assertEqual(final.snapshot.state, ContinuationState.COMPLETED)
        self.assertIsNone(engine.active_snapshot())


class TestContextDependencies(ContinuationTestCase):
    def test_context_request_is_typed_and_preserves_original_goal(self) -> None:
        engine = self.engine()
        started = engine.start(
            "Describe what I am looking at",
            authority_origin="user",
        )
        sid, generation = self.ids(started)

        decision = engine.accept_context_request(
            sid,
            generation,
            ContextRequest(ContextKind.SCREEN),
            request_origin="user",
        )

        self.assertEqual(decision.kind, DecisionKind.RUN_CONTEXT)
        self.assertEqual(
            decision.snapshot.original_user_message,
            "Describe what I am looking at",
        )
        self.assertEqual(decision.context_request.kind, ContextKind.SCREEN)
        self.assertEqual(decision.snapshot.step, 1)

    def test_failed_context_outcome_calls_provider_with_safe_status(self) -> None:
        engine = self.engine()
        started = engine.start("What is on screen?", authority_origin="user")
        sid, generation = self.ids(started)
        request = ContextRequest(ContextKind.SCREEN)
        engine.accept_context_request(
            sid, generation, request, request_origin="user",
        )

        follow = engine.accept_context_outcome(
            sid,
            generation,
            ContextOutcome(
                ContextKind.SCREEN,
                False,
                "target_unavailable",
                "[Screen context unavailable.]",
            ),
        )

        self.assertEqual(follow.kind, DecisionKind.CALL_PROVIDER)
        self.assertEqual(follow.outcome.status, "target_unavailable")
        self.assertEqual(len(follow.snapshot.context_history), 1)
        self.assertIn("Screen context unavailable", follow.provider_context)

    def test_repeated_context_dependency_stops_without_second_run(self) -> None:
        engine = self.engine()
        started = engine.start(
            "Describe the current screen",
            authority_origin="user",
        )
        sid, generation = self.ids(started)
        request = ContextRequest(ContextKind.SCREEN)
        first = engine.accept_context_request(
            sid, generation, request, request_origin="user",
        )
        self.assertEqual(first.kind, DecisionKind.RUN_CONTEXT)
        follow = engine.accept_context_outcome(
            sid,
            generation,
            ContextOutcome(
                ContextKind.SCREEN,
                False,
                "target_unavailable",
                "[Screen context unavailable.]",
            ),
        )
        self.assertEqual(follow.kind, DecisionKind.CALL_PROVIDER)

        repeated = engine.accept_context_request(
            sid,
            generation,
            request,
            request_origin="tool_result",
        )

        self.assertEqual(repeated.kind, DecisionKind.STOPPED)
        self.assertEqual(repeated.reason, "repeated_context_dependency")
        self.assertIsNone(engine.active_snapshot())

    def test_wrong_origin_cannot_start_or_borrow_context(self) -> None:
        engine = self.engine()
        started = engine.start("Describe it", authority_origin="user")
        sid, generation = self.ids(started)

        denied = engine.accept_context_request(
            sid,
            generation,
            ContextRequest(ContextKind.SCREEN),
            request_origin="ambient",
        )

        self.assertEqual(denied.kind, DecisionKind.IGNORED)
        self.assertEqual(denied.reason, "unexpected_model_response_origin")
        self.assertEqual(engine.active_snapshot().step, 0)

    def test_context_outcome_must_match_pending_kind(self) -> None:
        engine = self.engine()
        started = engine.start("Describe it", authority_origin="user")
        sid, generation = self.ids(started)
        engine.accept_context_request(
            sid,
            generation,
            ContextRequest(ContextKind.SCREEN),
            request_origin="user",
        )

        malformed = object.__new__(ContextOutcome)
        object.__setattr__(malformed, "kind", "unknown")
        object.__setattr__(malformed, "success", False)
        object.__setattr__(malformed, "status", "bad")
        object.__setattr__(malformed, "provider_context", "")
        object.__setattr__(malformed, "sensitivity", "private")
        stopped = engine.accept_context_outcome(sid, generation, malformed)

        self.assertEqual(stopped.kind, DecisionKind.BLOCKED)
        self.assertEqual(stopped.reason, "context_outcome_mismatch")


if __name__ == "__main__":
    unittest.main(verbosity=2)
