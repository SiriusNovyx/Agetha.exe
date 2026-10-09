"""F11 behavior evidence, including remaining unsafe delivery/startup behavior.

Run only in a disposable checkout with synthetic config/environment/memory
installed BEFORE imports (main/utils load settings at import). No real Tk,
speech, memory, web, providers, or OS effects. These tests do not bless defects
as a future contract; convergence must replace the documented unsafe outcomes.
"""
from __future__ import annotations

import json
import queue
import threading
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import patch

import main
from agetha.app_config import AppSettings
from agetha.commands import command_handlers as handlers
from agetha.commands.handlers import memory_presentation, web_context
from agetha.commands.handlers.support import DispatchCtx
from agetha.core.ai_engine import AIEngine
from agetha.core.capabilities import CapabilityController, CapabilityPolicy
from agetha.core.context_dependencies import ContextKind, ContextOutcome, ContextRequest, UnresolvedContextObjectiveStore
from agetha.core.continuation import ContinuationEngine, ContinuationState, DecisionKind
from agetha.core.read_only_tools import ReadOnlyToolExecutor


def answer(text="answer"):
    return {"command": "speak", "mood": "neutral", "segments": [{"text": text}], "shutdown": False}


class FakeAI(AIEngine):
    """Real prompt/resolver boundary with an in-memory provider collaborator."""
    def __init__(self, settings):
        self._app_settings = settings
        self._faster_mode = True
        self._fast_profile_active = True
        self._fast_mode_original_values = {"AI_MAX_TOKENS": "600", "HISTORY_LIMIT": "7"}
        self.HISTORY_LIMIT = 3
        self._history = []
        self._system_path = "synthetic"
        self._compact_chars = ""
        self._session_recap_pending = False
        self._datetime_provider = None
        self._get_inactivity_seconds = lambda: 0
        self._load_memories = lambda: ""
        self.responses = deque()
        self.calls = []
        self.hook = lambda: None
        self.recorded = []
        self._record = lambda user, raw: self.recorded.append((user, raw))

    def query(self, **kwargs):
        self.hook()
        web, suppress_web = kwargs.get("web_rag_context", ""), kwargs.get("suppress_web_rag", False)
        notes, suppress_notes = kwargs.get("notepad_context", ""), kwargs.get("suppress_read_notepad", False)
        system, turn, _ = self._build_prompt(
            kwargs.get("screen_context", ""), kwargs.get("user_message", ""), kwargs.get("doc_content", ""),
            memory_search_context=kwargs.get("memory_search_context", ""),
            suppress_search_memory=kwargs.get("suppress_search_memory", False),
            web_rag_context=web, suppress_web_rag=suppress_web,
            notepad_context=notes, suppress_read_notepad=suppress_notes,
            request_profile=kwargs.get("request_profile"),
        )
        self.calls.append(dict(kwargs, system=system, turn=turn))
        value = self.responses.popleft() if self.responses else answer()
        if isinstance(value, Exception):
            raise value
        return value

    def query_streaming(self, on_token=None, **kwargs):
        value = self.query(**kwargs)
        if on_token:
            on_token("synthetic chunk")
        return value


class FakeApp(main.CompanionApp):
    """Queue delivery is controlled by the test; orchestration stays real."""
    def _start_worker(self, target, *, name, args=(), kwargs=None, on_start_failure=None):
        if self.start_failure:
            if on_start_failure is not None:
                on_start_failure()
            raise RuntimeError("synthetic worker startup failure")
        if self._closing or self.refuse_worker:
            if on_start_failure is not None:
                on_start_failure()
            return None
        self.jobs.append((name, lambda: target(*args, **(kwargs or {}))))
        return object()

    def _schedule_ui(self, callback, delay_ms=0):
        if self._closing:
            return None
        self.ui.append(callback)
        return callback


class FakeInput(dict):
    def config(self, **values):
        self.update(values)


class FollowupCharacterization(unittest.TestCase):
    def setUp(self):
        self.settings = AppSettings({
            "COMPACT_MODE": "no", "ENABLE_STREAMING": "no", "ENABLE_COMPUTER_USE": "no",
            "ENABLE_WEB_RAG": "yes", "ENABLE_LONGTERM_MEMORY": "yes", "ENABLE_DATETIME_CONTEXT": "no",
            "ENABLE_PROCESS_AWARENESS": "no", "ENABLE_EMOTION_ENGINE": "no", "ENABLE_DREAMS": "no",
            "ENABLE_TASKS": "no", "ENABLE_CIRCADIAN_RHYTHM": "no", "ENABLE_STATUS_PROVIDERS": "no",
        })
        for module in (main, handlers, web_context, memory_presentation):
            self.enterContext(patch.object(module, "get_settings", return_value=self.settings))
        self.enterContext(patch.object(main, "_SETTINGS", self.settings))
        self.enterContext(patch("agetha.core.companion_stats.update_stats"))
        self.enterContext(patch("agetha.core.emotion_engine.note"))
        self.enterContext(patch("agetha.ui.glitch_overlay.maybe_mood_glitch"))
        self.enterContext(patch("agetha.ui.dashboard.read_notepad_text", return_value="A notes"))
        self.enterContext(patch("agetha.core.memory_search.search_memories", return_value=[]))
        self.enterContext(patch("agetha.features.web_rag.search_web", return_value=[]))
        self.app = FakeApp.__new__(FakeApp)
        app = self.app
        app._closing = app._shutdown_complete = False
        app._state = app.STATE_IDLE
        app._ai_tick_lock = threading.Lock()
        app._ai_busy = app._ai_busy_noninterruptible = app._speech_active = False
        app._ai_operation_token = app._pending_user_message = None
        app._pending_user_origin = "user"
        app._pending_screen_context = app._pending_capability_authorization = None
        app._post_ai_tick_callbacks = []
        app._deferred_ai_callbacks_inflight = False
        app._cancel_event = threading.Event()
        app._last_screen_text = ""
        app._screen = app._process_awareness = None
        app._capabilities = CapabilityController(CapabilityPolicy.from_settings(self.settings))
        self.now = 0.0
        app._continuation = ContinuationEngine(clock=lambda: self.now)
        app._continuation_ui_epoch = 0
        app._context_capture_target_lock = threading.Lock()
        app._context_capture_targets = {}
        app._unresolved_context_objectives = UnresolvedContextObjectiveStore(clock=lambda: self.now)
        app._completed_context_history_sessions = set()
        app._ai = FakeAI(self.settings)
        app._continuation_tools = ReadOnlyToolExecutor(settings=self.settings, functions={"read_notepad": lambda: "A notes"})
        app.jobs, app.ui, app.spoken, app.notices = [], [], [], []
        app.start_failure = app.refuse_worker = False
        app.root = SimpleNamespace()
        app._input_box = FakeInput(state="normal")
        app._input_var = SimpleNamespace(get=lambda: "B", set=lambda _: None)
        app._poll_job = None
        app._wake_from_presence_rest = lambda: None
        app._stop_computer_use = lambda _: None
        app._stop_computer_use_escape_hotkey = lambda: None
        app._graceful_shutdown_continue = lambda: None
        app._re_enable_input = lambda **_: None
        app._set_state = lambda state, *args: setattr(app, "_state", state)
        app._update_token_status = app._reschedule_screen_poll = lambda: None
        app._fast_mode_runtime_active = lambda: False
        app._try_short_mood_speak = lambda *_, **__: False
        app._presence_decision = lambda: SimpleNamespace(allow_voice=False)
        app._guard = SimpleNamespace(check=lambda *_: True)
        app._recent_unresolved_context_for_prompt = lambda _: ""
        app._clear_unresolved_context_if_topic_changed = lambda _: None
        app._subtitle = SimpleNamespace(
            speak=lambda segments, **kw: app.spoken.append((segments, kw)),
            show_thinking=lambda raw: app.notices.append(raw),
            show_message=lambda text, *_: app.notices.append(text),
        )
        app._voice_out = app._bleep = None

    def flush(self):
        for _ in range(50):
            if not self.app.ui:
                return
            self.app.ui.pop(0)()
        self.fail("unbounded fake UI queue")

    def job(self, name):
        i = next(i for i, (n, _) in enumerate(self.app.jobs) if n == name)
        self.app.jobs.pop(i)[1]()

    def ctx(self, segments=None):
        return DispatchCtx("A", "neutral", segments or [], False, "user")

    def bounded(self, command="read_notepad", *, goal="A", **fields):
        engine = self.app._continuation
        started = engine.start(goal, authority_origin="user")
        decision = engine.accept_initial_model_response(started.session_id, started.generation, {"command": command, **fields})
        self.app._handle_continuation_decision(decision)
        return decision

    def to_provider(self, goal="A"):
        self.bounded(goal=goal)
        self.job("continuation-tool")
        return self.app.jobs[-1][1]

    def legacy(self):
        memory_presentation.handle_read_notepad(self.app, {"command": "read_notepad"}, self.ctx())

    def transition(self):
        compact = AppSettings({**self.settings.raw, "COMPACT_MODE": "yes"})
        self.app._capability_consent = SimpleNamespace(downgrade_to_compact=lambda: None)
        self.app._refresh_dashboard_after_profile_commit = lambda _: None
        with patch("agetha.app_config.arm_compact_mode_fail_closed", return_value=True), patch("agetha.app_config.clear_compact_mode_fail_closed"), patch("agetha.app_config.patch_config_key", return_value=True), patch.object(main, "get_settings", return_value=compact):
            self.assertTrue(self.app._activate_compact_mode())

    def test_direct_user_routes_readonly_to_engine_when_enabled(self):
        self.app._ai.responses.append({"command": "read_notepad"})
        self.app._ai_tick("A", origin="user")
        self.assertEqual([n for n, _ in self.app.jobs], ["continuation-tool"])

    def test_direct_user_routes_readonly_to_legacy_when_disabled(self):
        self.app._continuation = None
        self.app._ai.responses.append({"command": "read_notepad"})
        self.app._ai_tick("A", origin="user")
        self.assertEqual([n for n, _ in self.app.jobs], ["notepad-requery"])

    def test_bounded_normal_followup_preserves_goal_and_delivers_once(self):
        self.to_provider()()
        self.flush()
        self.assertEqual(self.app._ai.calls[0]["user_message"], "A")
        self.assertEqual(self.app._ai.calls[0]["request_profile"], "tool_continuation")
        self.assertIn("A notes", self.app._ai.calls[0]["turn"])
        self.assertEqual(len(self.app.spoken), 1)
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertFalse(self.app._ai_busy)

    def test_legacy_normal_followup_delivers_passively_and_clears_notes(self):
        self.legacy()
        self.job("notepad-requery")
        self.flush()
        self.assertEqual(len(self.app.spoken), 1)
        self.assertIn("[internal event: tool_result]", self.app._ai.calls[0]["user_message"])

    def test_bounded_tool_failure_continues_with_bounded_error_data(self):
        self.app._continuation_tools = ReadOnlyToolExecutor(settings=self.settings, functions={"read_notepad": lambda: (_ for _ in ()).throw(ValueError("synthetic private error"))})
        self.to_provider()()
        self.flush()
        self.assertEqual(len(self.app.spoken), 1)
        self.assertIn("ValueError", self.app._ai.calls[0]["turn"])
        self.assertNotIn("synthetic private error", self.app._ai.calls[0]["turn"])

    def test_legacy_tool_failure_requeries_with_error_message(self):
        with patch("agetha.ui.dashboard.read_notepad_text", side_effect=ValueError("synthetic private error")):
            self.legacy()
        self.job("notepad-requery")
        self.assertIn("synthetic private error", self.app._ai.calls[0]["turn"])

    def test_bounded_provider_exception_releases_slot_and_stops(self):
        run = self.to_provider()
        self.app._ai.responses.append(RuntimeError("synthetic provider failure"))
        run()
        self.assertIsNone(self.app._ai_operation_token)
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertEqual(self.app._continuation.last_snapshot().state, ContinuationState.STOPPED)

    def test_legacy_provider_exception_releases_slot_and_clears_context(self):
        self.legacy()
        self.app._ai.responses.append(RuntimeError("synthetic provider failure"))
        self.job("notepad-requery")
        self.assertIsNone(self.app._ai_operation_token)
        self.assertEqual(self.app.spoken, [])

    def test_bounded_provider_timeout_exception_releases_slot(self):
        run = self.to_provider()
        self.app._ai.responses.append(TimeoutError("synthetic timeout"))
        run()
        self.assertFalse(self.app._ai_busy)
        self.assertIsNone(self.app._continuation.active_snapshot())

    def test_legacy_provider_timeout_exception_clears_context(self):
        self.legacy()
        self.app._ai.responses.append(TimeoutError("synthetic timeout"))
        self.job("notepad-requery")
        self.assertFalse(self.app._ai_busy)

    def test_new_direct_input_during_bounded_provider_drops_old_answer_and_queues_user(self):
        run = self.to_provider()
        def new_input():
            self.app._on_user_input()
            self.job("user-ai")
        self.app._ai.hook = new_input
        run()
        self.flush()
        self.assertEqual(self.app.spoken, [])
        self.assertEqual([n for n, _ in self.app.jobs][-1], "queued-ai")
        self.assertFalse(self.app._ai_busy)

    def test_legacy_provider_cannot_speak_after_new_direct_input(self):
        self.legacy()
        def new_input():
            self.app._on_user_input()
            self.job("user-ai")
        self.app._ai.hook = new_input
        self.job("notepad-requery")
        self.flush()
        self.assertEqual(self.app.spoken, [], "New input invalidates the producing request before speech")
        self.assertEqual([n for n, _ in self.app.jobs], ["queued-ai"])

    def test_escape_before_bounded_worker_delivery_prevents_retrieval(self):
        self.bounded()
        self.app._on_cancel_ai()
        self.job("continuation-tool")
        self.assertEqual(self.app._ai.calls, [])
        self.assertFalse(any(n == "continuation-provider" for n, _ in self.app.jobs))

    def test_escape_before_legacy_delivery_skips_query(self):
        self.legacy()
        self.app._on_cancel_ai()
        self.job("notepad-requery")
        self.assertEqual(self.app._ai.calls, [])

    def test_generation_preemption_rejects_bounded_provider_work(self):
        run = self.to_provider()
        self.app._continuation.start("B", authority_origin="user")
        run()
        self.assertEqual(self.app._ai.calls, [])

    def test_legacy_queued_requery_has_no_generation_boundary(self):
        self.legacy()
        self.app._continuation.start("B", authority_origin="user")
        self.job("notepad-requery")
        self.flush()
        self.assertEqual(len(self.app.spoken), 1)
        self.assertEqual(self.app._continuation.active_snapshot().original_user_message, "B")

    def test_bounded_status_speech_finishes_before_tool_worker_starts(self):
        self.bounded(segments=[{"text": "checking"}])
        self.assertEqual(self.app.jobs, [])
        self.flush()
        self.app.spoken[0][1]["on_done"]()
        self.flush()
        self.assertEqual([n for n, _ in self.app.jobs], ["continuation-tool"])

    def test_bounded_provider_with_pending_speech_stops_instead_of_waiting(self):
        run = self.to_provider()
        self.app._speech_active = True
        run()
        self.assertEqual(self.app._ai.calls, [])
        self.assertIsNone(self.app._continuation.active_snapshot())

    def test_legacy_status_speech_can_make_followup_drop_without_retry(self):
        web_context.handle_search_web(self.app, {"query": "A"}, self.ctx([{"text": "checking"}]))
        self.job("web-search-requery")
        self.assertEqual(self.app._ai.calls, [])
        self.flush()
        self.app.spoken[0][1]["on_done"]()
        self.flush()
        self.assertEqual(self.app.jobs, [])

    def test_shutdown_before_bounded_delivery_rejects_followup(self):
        run = self.to_provider()
        self.app._graceful_shutdown()
        run()
        self.assertEqual(self.app._ai.calls, [])
        self.assertEqual(self.app.spoken, [])

    def test_shutdown_before_legacy_delivery_skips_query_and_cleans_delivered_worker(self):
        self.legacy()
        self.app._graceful_shutdown()
        self.job("notepad-requery")
        self.assertEqual(self.app._ai.calls, [])

    def test_profile_transition_does_not_cancel_basic_bounded_notes(self):
        run = self.to_provider()
        self.transition()
        run()
        self.flush()
        self.assertEqual(len(self.app.spoken), 1)

    def test_profile_transition_expires_owned_legacy_notes(self):
        self.legacy()
        self.transition()
        self.job("notepad-requery")
        self.flush()
        self.assertEqual(self.app.spoken, [])

    def test_multiple_bounded_tools_are_session_owned_and_repeats_stop(self):
        self.app._ai.responses.append({"command": "read_notepad"})
        self.to_provider()()
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertEqual(self.app._continuation.last_snapshot().step, 1)
        self.assertEqual(len(self.app._ai.calls), 1)

    def test_legacy_followup_cannot_start_another_tool_from_model_response(self):
        self.legacy()
        self.app._ai.responses.append({"command": "search_memory", "query": "B", "segments": [{"text": "data"}]})
        self.job("notepad-requery")
        self.flush()
        self.assertEqual(self.app.jobs, [])
        self.assertEqual(len(self.app.spoken), 1)

    def test_late_bounded_tool_result_cannot_enter_new_request(self):
        def late_notes():
            self.app._continuation.start("B", authority_origin="user")
            return "A late context"
        self.app._continuation_tools = ReadOnlyToolExecutor(settings=self.settings, functions={"read_notepad": late_notes})
        self.bounded()
        self.job("continuation-tool")
        self.assertEqual(self.app.jobs, [])
        self.assertEqual(self.app._continuation.active_snapshot().history, ())

    def test_notepad_context_of_A_is_not_consumed_by_B_before_A_worker_delivery(self):
        self.legacy()
        self.app._ai.responses.append({"command": "idle"})
        self.app._ai_tick("B", origin="user")
        self.assertNotIn("A notes", self.app._ai.calls[0]["turn"])
        self.assertNotIn("Do not call read_notepad", self.app._ai.calls[0]["system"])

    def test_old_legacy_completion_preserves_newer_notepad_context(self):
        self.legacy()
        def newer_notes():
            self.app._ai.hook = lambda: None
            with patch("agetha.ui.dashboard.read_notepad_text", return_value="B newer notes"):
                memory_presentation.handle_read_notepad(self.app, {}, DispatchCtx("B", "neutral", [], False, "user"))
        self.app._ai.hook = newer_notes
        self.job("notepad-requery")
        self.assertIn("A notes", self.app._ai.calls[0]["turn"])
        self.assertNotIn("B newer notes", self.app._ai.calls[0]["turn"])
        self.app._speech_active = False
        self.job("notepad-requery")
        self.assertIn("B newer notes", self.app._ai.calls[1]["turn"])
        self.assertNotIn("A notes", self.app._ai.calls[1]["turn"])

    def test_web_context_cannot_enter_next_direct_prompt_before_worker_returns(self):
        original_start = FakeApp._start_worker
        def deliver_queued_user_immediately(app, target, *, name, args=(), kwargs=None):
            if name == "queued-ai":
                target(*args, **(kwargs or {}))
                return object()
            return original_start(app, target, name=name, args=args, kwargs=kwargs)
        def enqueue_user():
            self.app._ai.hook = lambda: None
            self.app._reserve_ai_operation(direct=True, user_message="B", origin="user")
        self.app._ai.hook = enqueue_user
        self.app._ai.responses.extend([answer(), {"command": "idle"}])
        with patch.object(FakeApp, "_start_worker", deliver_queued_user_immediately):
            web_context._requery_with_web_context(self.app, self.ctx(), "A web context")
        self.assertIn("A web context", self.app._ai.calls[0]["turn"])
        self.assertNotIn("A web context", self.app._ai.calls[1]["turn"])
        self.assertNotIn("Do not call search_web", self.app._ai.calls[1]["system"])

    def test_legacy_notes_cannot_contaminate_bounded_provider_prompt(self):
        self.legacy()
        self.app._continuation_tools = ReadOnlyToolExecutor(settings=self.settings, functions={"read_notepad": lambda: "B current notes"})
        self.to_provider(goal="B")()
        self.assertNotIn("A notes", self.app._ai.calls[0]["turn"])
        self.assertNotIn("Do not call read_notepad", self.app._ai.calls[0]["system"])
        self.assertIn("B current notes", self.app._ai.calls[0]["doc_content"])

    def test_enabled_continuation_still_reaches_clipboard_legacy_handler(self):
        self.app._ai.responses.append({"command": "get_clipboard"})
        with patch.object(handlers, "get_clipboard", return_value="synthetic clipboard"):
            self.app._ai_tick("A", origin="user")
        self.assertEqual([n for n, _ in self.app.jobs], ["clipboard-requery"])
        self.app._ai.responses.append({"command": "read_notepad"})
        self.app._ai_tick("B", origin="user")
        self.assertEqual([n for n, _ in self.app.jobs], ["clipboard-requery", "continuation-tool"])

    def test_followup_provider_worker_start_failure_stops_session(self):
        self.bounded()
        self.app.start_failure = True
        with self.assertRaisesRegex(RuntimeError, "startup"):
            self.job("continuation-tool")
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertEqual(self.app._continuation.last_snapshot().state, ContinuationState.STOPPED)
        self.assertEqual(self.app._ai.calls, [])
        self.assertFalse(self.app._ai_busy)

    def test_cancelled_bounded_owner_retains_slot_until_provider_returns(self):
        run = self.to_provider()
        observed = []
        def cancel_inflight():
            self.app._continuation.cancel_active("escape")
            observed.append((self.app._ai_busy, self.app._ai_operation_token is not None))
            self.assertIsNone(self.app._reserve_ai_operation(direct=True, user_message="B", origin="user"))
        self.app._ai.hook = cancel_inflight
        run()
        self.assertEqual(observed, [(True, True)])
        self.assertFalse(self.app._ai_busy)
        self.assertEqual(self.app.spoken, [])

    def test_bounded_untrusted_text_cannot_authorize_mutation(self):
        self.app._ai.responses.append({"command": "write_file", "path": "synthetic", "segments": []})
        self.to_provider()()
        self.assertIn("untrusted", self.app._ai.calls[0]["turn"].lower())
        self.assertEqual(self.app._continuation.last_snapshot().state, ContinuationState.STOPPED)
        self.assertEqual(len(self.app._ai.calls), 1)

    def test_legacy_untrusted_text_cannot_dispatch_mutation(self):
        self.legacy()
        self.app._ai.responses.append({"command": "write_file", "path": "synthetic", "segments": []})
        with patch.dict(handlers.HANDLERS, {"write_file": lambda *_: self.fail("mutation handler reached") } ):
            self.job("notepad-requery")
        self.assertIn("untrusted", self.app._ai.calls[0]["turn"].lower())

    def test_bounded_occupied_provider_stops_without_releasing_other_owner(self):
        run = self.to_provider()
        token = self.app._reserve_ai_operation(direct=True, user_message="other", origin="user")
        run()
        self.assertIs(self.app._ai_operation_token, token)
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertEqual(self.app._ai.calls, [])

    def test_legacy_occupied_provider_drops_followup_and_keeps_other_owner(self):
        self.legacy()
        token = self.app._reserve_ai_operation(direct=True, user_message="other", origin="user")
        self.job("notepad-requery")
        self.assertIs(self.app._ai_operation_token, token)

    def test_bounded_worker_start_exception_stops_session(self):
        self.app.start_failure = True
        with self.assertRaisesRegex(RuntimeError, "startup"):
            self.bounded()
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertEqual(self.app._continuation.last_snapshot().state, ContinuationState.STOPPED)
        self.assertFalse(self.app._ai_busy)
        self.now = 121
        self.assertFalse(self.app._continuation.is_current(self.app._continuation.last_snapshot().session_id, 1))

    def test_bounded_worker_refusal_stops_session(self):
        self.app.refuse_worker = True
        self.bounded()
        self.assertIsNone(self.app._continuation.active_snapshot())
        self.assertEqual(self.app._continuation.last_snapshot().state, ContinuationState.STOPPED)

    def test_legacy_worker_failure_leaves_no_notepad_context_for_later_request(self):
        self.app.start_failure = True
        with self.assertRaisesRegex(RuntimeError, "startup"):
            self.legacy()
        self.app.start_failure = False
        self.app._ai.responses.append({"command": "idle"})
        self.app._ai_tick("B", origin="user")
        self.assertNotIn("A notes", self.app._ai.calls[0]["turn"])

    def test_legacy_web_worker_failure_sets_no_pending_context(self):
        self.app.start_failure = True
        with self.assertRaisesRegex(RuntimeError, "startup"):
            web_context.handle_search_web(self.app, {"query": "A"}, self.ctx())

    def test_bounded_deadline_crossed_during_provider_drops_late_answer(self):
        run = self.to_provider()
        self.app._ai.hook = lambda: setattr(self, "now", 121)
        run()
        self.flush()
        self.assertEqual(self.app.spoken, [])
        self.assertFalse(self.app._ai_busy)
        self.assertIsNotNone(self.app._continuation.active_snapshot(), "Current deadline check is lazy; no stop transition here")

    def test_bounded_final_speech_already_queued_expires_with_generation(self):
        self.to_provider()()
        self.assertEqual(self.app.spoken, [])
        self.app._invalidate_continuation_ui_delivery()
        self.app._continuation.start("B", authority_origin="user")
        self.flush()
        self.assertEqual(self.app.spoken, [], "Final speech retains the continuation owner through delivery")

    def test_shutdown_rejects_pending_speech_delivery(self):
        self.app._schedule_ui = main.CompanionApp._schedule_ui.__get__(self.app)
        self.app._ui_owner_thread_id = threading.get_ident()
        self.app._ui_callback_queue = queue.SimpleQueue()
        worker = threading.Thread(target=lambda: self.app._speak_and_continue([{"text": "A"}], "neutral"))
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(self.app._ui_callback_queue.empty())
        self.app._graceful_shutdown()
        self.app._drain_ui_queue()
        self.assertTrue(self.app._ui_callback_queue.empty())
        self.assertEqual(self.app.spoken, [])

    def test_distinct_bounded_tools_continue_twice_without_legacy_fields(self):
        self.app._continuation_tools = ReadOnlyToolExecutor(settings=self.settings, functions={"search_memory": lambda *_: "first", "read_notepad": lambda: "second"})
        self.bounded("search_memory", query="A")
        self.job("continuation-tool")
        self.app._ai.responses.append({"command": "read_notepad"})
        self.job("continuation-provider")
        self.job("continuation-tool")
        self.job("continuation-provider")
        self.flush()
        self.assertEqual(len(self.app._ai.calls), 2)
        self.assertEqual(len(self.app._continuation.last_snapshot().history), 2)
        self.assertNotIn("first", self.app._ai.calls[1]["doc_content"])
        self.assertEqual(len(self.app.spoken), 1)

    def test_screen_context_failure_is_ephemeral_and_answer_records_once(self):
        started = self.app._continuation.start("A", authority_origin="user")
        decision = self.app._continuation.accept_context_request(started.session_id, started.generation, ContextRequest(ContextKind.SCREEN), request_origin="user")
        self.app._acquire_read_only_context = lambda *_args, **_kw: ContextOutcome(ContextKind.SCREEN, False, "target_unavailable", "synthetic unavailable")
        self.app._handle_continuation_decision(decision)
        self.job("continuation-context")
        self.assertEqual(self.app._unresolved_context_objectives.current().message, "A")
        self.job("continuation-provider")
        self.flush()
        self.assertEqual(len(self.app._ai.recorded), 1)
        self.assertNotIn("synthetic unavailable", str(self.app._ai.recorded))

    def test_profile_transition_denies_screen_acquisition_without_canceling_goal(self):
        started = self.app._continuation.start("A", authority_origin="user")
        decision = self.app._continuation.accept_context_request(started.session_id, started.generation, ContextRequest(ContextKind.SCREEN), request_origin="user")
        self.app._handle_continuation_decision(decision)
        self.transition()
        self.job("continuation-context")
        self.job("continuation-provider")
        self.flush()
        self.assertIn("unavailable", self.app._ai.calls[0]["turn"])
        self.assertEqual(len(self.app.spoken), 1)

    def test_real_ai_streaming_and_nonstreaming_ignore_ownerless_pending_context(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                ai = FakeAI(self.settings)
                ai._client = object()
                ai._ensure_provider_initialized = lambda authorization=None: True
                ai._update_user_activity = lambda _: None
                ai._track_tokens = lambda _: None
                ai._use_local_ai, ai._use_openrouter, ai._enable_groq = True, False, False
                ai._config = {"LOCAL_AI_MODEL": "synthetic"}
                ai._pending_web_rag_context = "A web text"
                ai._pending_suppress_web_rag = True
                payloads = []
                def provider(**kwargs):
                    payloads.append(kwargs)
                    raw = json.dumps(answer())
                    if kwargs["stream"]:
                        return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=raw))])])
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw))])
                ai._provider_create = provider
                method = AIEngine.query_streaming if streaming else AIEngine.query
                result = method(ai, user_message="B", request_origin="tool_result", request_profile="fast_tool_result")
                self.assertEqual(result["segments"][0]["text"], "answer")
                self.assertNotIn("A web text", str(payloads[0]["messages"]))
                self.assertNotIn("Do not call search_web", payloads[0]["messages"][0]["content"])

    def test_memory_legacy_context_is_argument_local_and_suppression_explicit(self):
        memory_presentation.handle_search_memory(self.app, {"query": "A"}, self.ctx())
        self.job("memory-requery")
        call = self.app._ai.calls[0]
        self.assertTrue(call["suppress_search_memory"])
        self.assertTrue(call["memory_search_context"])

    def test_streaming_and_nonstreaming_host_paths_deliver_equivalent_bounded_answer(self):
        for enabled in (False, True):
            with self.subTest(streaming=enabled), patch.object(main, "_SETTINGS", SimpleNamespace(enable_streaming=enabled)):
                self.app._speech_active = False
                self.app.spoken.clear()
                self.to_provider()()
                self.flush()
                self.assertEqual(self.app.spoken[0][0][0]["text"], "answer")
                self.assertFalse(self.app._ai_busy)


if __name__ == "__main__":
    unittest.main()
