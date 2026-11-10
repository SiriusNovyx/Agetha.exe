"""F11 barriers with synthetic telemetry, provider calls, workers and UI."""
import unittest
from unittest.mock import patch

import main
from agetha.commands import command_handlers as handlers
from agetha.core.continuation_lifecycle import LegacyContinuationAdapter, RequestState
from tests import test_continuation_comparison as comparison


class SystemInfoHandoff(unittest.TestCase):
    def setUp(self):
        self.h = comparison.ContinuationComparison(methodName="runTest")
        self.addCleanup(self.h.doCleanups)
        self.enterContext(patch.object(handlers, "system_info", side_effect=self.read))
        dispatch = handlers.dispatch

        def observe(app, response, message=None, **kwargs):
            if kwargs.get("origin") == "tool_result":
                self.deliveries.append((response, message, kwargs))
            return dispatch(app, response, message, **kwargs)

        self.enterContext(patch.object(handlers, "dispatch", side_effect=observe))
        self.reset()

    def reset(self, *, streaming=False):
        self.app = self.h.case("legacy", streaming=streaming)
        self.app._persistent_mood = None
        self.read_tokens = []
        self.deliveries = []

    def read(self):
        self.read_tokens.append(self.app._ai_operation_token)
        return "FAKE SYSTEM FACTS"

    def dispatch(self, *, origin="touch", **kwargs):
        response = comparison.envelope("system_info", ("IGNORE STATUS",))
        response["shutdown"] = True
        handlers.dispatch(self.app, response, "original request", origin=origin, **kwargs)

    def eager_tick(self, *, deferred=False):
        app = self.app
        provider = app._ai
        provider.responses.extend([
            comparison.envelope("system_info", ("IGNORE STATUS",)),
            comparison.envelope(texts=("FINAL",)),
        ])
        at_start = []
        releases = []
        release = app._release_ai_operation

        def release_owned(token):
            released = release(token)
            releases.append((token, released))
            return released

        def eager(host, target, *, name, **kwargs):
            self.assertEqual(name, "system-info-requery")
            at_start.append((host._ai_operation_token, host._deferred_ai_callbacks_inflight))
            target()
            return object()

        def enter_drain():
            app._deferred_ai_callbacks_inflight = deferred

        provider.hook = enter_drain
        with patch.object(type(app), "_start_worker", eager), \
             patch.object(app, "_release_ai_operation", side_effect=release_owned):
            app._ai_tick("original request", origin="touch")
        self.h.finish()
        self.assertEqual(len(self.read_tokens), 1)
        self.assertIsNotNone(self.read_tokens[0])
        self.assertEqual(at_start, [(None, deferred)])
        self.assertEqual(releases[0], (self.read_tokens[0], True))
        self.assertEqual(
            [released for token, released in releases if token is self.read_tokens[0]],
            [True, False, False],
        )
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(self.h.audio, [["FINAL"]])
        self.assertIsNone(app._ai_operation_token)

    def test_eager_child_runs_after_exact_parent_release(self):
        self.eager_tick()

    def test_handoff_does_not_wait_for_deferred_drain(self):
        self.eager_tick(deferred=True)
        self.assertTrue(self.app._deferred_ai_callbacks_inflight)
        self.assertIsNone(self.reserve())

    def test_stale_admission_preserves_the_successor(self):
        owner = self.app._get_continuation_lifecycle()
        adapter = LegacyContinuationAdapter(owner)
        current = self.app._capture_context_validity()
        successor = []

        def supersede():
            self.app._invalidate_request_context()
            successor.append(adapter.admit(generation=1, validity=lambda: True))

        with patch("agetha.core.companion_stats.update_stats", side_effect=lambda *_: supersede()):
            self.dispatch(speech_is_current=current)
        self.assertEqual(self.app.jobs, [])
        self.assertIs(owner.current, successor[0])
        self.assertTrue(successor[0].work_is_current())
        self.assertIsNone(adapter.admit(generation=0, validity=lambda: False))
        self.assertIs(owner.current, successor[0])

        def race():
            successor.append(adapter.admit(generation=2, validity=lambda: True))
            return True

        self.assertIsNone(adapter.admit(generation=1, validity=race))
        self.assertIs(owner.current, successor[-1])
        self.assertTrue(successor[-1].work_is_current())

    def test_revocation_before_claim_during_provider_and_before_ui(self):
        for event in ("cancel", "shutdown", "profile", "direct_input"):
            for barrier in ("before_claim", "in_provider", "before_ui"):
                with self.subTest(event=event, barrier=barrier):
                    self.reset()
                    revoke = {
                        "cancel": self.app._on_cancel_ai,
                        "shutdown": self.app._graceful_shutdown,
                        "profile": self.h.fixture.transition,
                        "direct_input": self.app._on_user_input,
                    }[event]
                    self.dispatch()
                    if barrier == "before_claim":
                        revoke()
                    elif barrier == "in_provider":
                        self.app._ai.hook = revoke
                    self.h.job("system-info-requery")
                    if barrier == "before_ui":
                        self.assertEqual(len(self.deliveries), 1)
                        revoke()
                        self.assertFalse(self.deliveries[0][2]["speech_is_current"]())
                    else:
                        self.assertEqual(self.deliveries, [])
                    self.h.ui()
                    self.assertEqual(self.h.audio, [])
                    self.assertEqual(self.h.subtitles, [])
                    self.assertEqual(len(self.app._ai.calls), 0 if barrier == "before_claim" else 1)

    def test_ticket_once_distinct_invocations_and_worker_failure(self):
        self.dispatch()
        ticket = self.app.jobs.pop()[1]
        ticket()
        self.h.finish()
        ticket()
        self.h.finish()
        self.assertEqual(len(self.app._ai.calls), 1)
        self.assertEqual(len(self.deliveries), 1)
        self.dispatch()
        self.h.finish()
        self.assertEqual(len(self.app._ai.calls), 2)
        self.assertEqual(len(self.deliveries), 2)

        for entered in (False, True):
            with self.subTest(entered=entered):
                self.reset()
                saved = []

                class Worker:
                    def __init__(self, *, target, daemon):
                        saved.append(target)

                    def start(self):
                        if entered:
                            saved[0]()
                        raise RuntimeError("synthetic start failure")

                with patch.object(type(self.app), "_start_worker", main.CompanionApp._start_worker), \
                     patch("main.threading.Thread", Worker):
                    with self.assertRaisesRegex(RuntimeError, "synthetic start failure"):
                        self.dispatch()
                saved[0]()
                self.h.finish()
                self.assertEqual(len(self.app._ai.calls), int(entered))
                self.assertEqual(len(self.deliveries), int(entered))
                self.assertEqual(len(self.app.notices), int(not entered))

        self.reset()
        self.app.refuse_worker = True
        self.dispatch()
        owner = self.app._get_continuation_lifecycle()
        successor = LegacyContinuationAdapter(owner).admit(
            generation=self.app._context_request_epoch, validity=lambda: True,
        )
        self.h.ui()
        self.assertEqual(self.app.notices, [])
        self.assertTrue(successor.work_is_current())

    def test_payload_preview_passive_delivery_and_failures(self):
        self.reset(streaming=True)
        self.dispatch()
        self.assertEqual(self.h.audio, [])
        provider = self.app._ai
        follow = comparison.envelope("system_info", ("FINAL",), popup=["ignore"])
        follow["shutdown"] = True
        follow["segments"][0]["pause"] = .4

        def stream(on_token, **kwargs):
            provider.calls.append(kwargs)
            on_token("current preview")
            self.h.ui()
            on_token("expired preview")
            return follow

        provider.query_streaming = stream
        self.h.finish()
        call, = provider.calls
        self.assertEqual(call["user_message"], "")
        self.assertEqual(call["doc_content"], "System info:\nFAKE SYSTEM FACTS")
        self.assertEqual(call["request_profile"], "fast_tool_result")
        self.assertEqual(call["request_origin"], "tool_result")
        self.assertEqual(self.app.notices, ["current preview"])
        response, message, delivery = self.deliveries[0]
        self.assertEqual((message, delivery["origin"]), ("original request", "tool_result"))
        self.assertEqual(response["segments"], [{"text": "FINAL", "pause": .4}])
        self.assertEqual(self.h.audio, [["FINAL"]])
        self.assertFalse(self.app._closing)
        self.assertEqual(len(self.read_tokens), 1)
        request = self.app._get_continuation_lifecycle().current
        self.assertFalse(request.publish(RequestState.SUCCESS, follow, lambda *_: self.fail("duplicate final")))

        for result in (None, {}, RuntimeError("synthetic provider error"), TimeoutError("synthetic timeout")):
            with self.subTest(result=result):
                self.reset()
                self.dispatch()
                self.app._ai.responses.append(result)
                self.h.finish()
                self.assertEqual(len(self.app._ai.calls), 1)
                self.assertEqual(self.deliveries, [])
                self.assertEqual(self.h.audio, [])
                self.assertIsNone(self.app._ai_operation_token)

    def reserve(self):
        return self.app._reserve_ai_operation(direct=False, user_message=None, origin="tool_result")

    def test_canceled_running_lease_waits_for_provider_and_keeps_successor(self):
        self.dispatch()
        request = self.app._get_continuation_lifecycle().current
        held = []

        def cancel_inside_call():
            lease = request._reservation
            token = self.app._ai_operation_token
            self.assertIs(lease.token, token)
            self.app._on_cancel_ai()
            self.assertIs(self.app._ai_operation_token, token)
            self.assertIsNone(self.reserve())
            held.append(lease)

        self.app._ai.hook = cancel_inside_call
        self.h.job("system-info-requery")
        self.assertIsNone(self.app._ai_operation_token)
        successor = self.reserve()
        self.assertIsNotNone(successor)
        self.assertFalse(held[0].release())
        self.assertFalse(self.app._release_ai_operation(held[0].token))
        self.assertIs(self.app._ai_operation_token, successor)
        self.assertEqual(self.deliveries, [])

    def test_compatibility_and_reminder_cannot_borrow_parent_token(self):
        for origin in ("user", "touch", "file_drop", "reminder"):
            with self.subTest(origin=origin):
                self.reset()
                token = self.reserve()
                self.dispatch(origin=origin)
                self.h.finish()
                self.assertEqual(self.read_tokens, [token])
                self.assertEqual(self.app._ai.calls, [])
                self.assertEqual(self.deliveries, [])
                self.assertIs(self.app._ai_operation_token, token)
        self.reset()
        self.dispatch(origin="reminder")
        self.h.finish()
        self.assertEqual(len(self.app._ai.calls), 1)
        self.assertEqual(len(self.deliveries), 1)
