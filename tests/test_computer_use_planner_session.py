"""Planner isolation, recovery, lifecycle, and immediate STOP tests."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Callable, Mapping


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agetha.computer_use.executor import ComputerExecutor, ExecutorDependencies  # noqa: E402
from agetha.computer_use.models import (  # noqa: E402
    ComputerActionKind,
    ControlSource,
    LiveTargetState,
    ProcessIdentity,
    Rect,
    SessionState,
    WindowIdentity,
)
from agetha.computer_use.observer import (  # noqa: E402
    AtomicScreenSnapshot,
    ComputerObserver,
    RawControl,
)
from agetha.computer_use.planner import (  # noqa: E402
    ComputerPlanner,
    PLANNER_SYSTEM_PROMPT,
    PlannerProtocolError,
)
from agetha.computer_use.policy import ComputerUsePolicy  # noqa: E402
from agetha.computer_use.session import (  # noqa: E402
    ComputerUseManager,
    ComputerUseSessionSpec,
)
from agetha.computer_use.verifier import ComputerVerifier  # noqa: E402


class FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value
        self.lock = threading.Lock()

    def monotonic(self) -> float:
        with self.lock:
            return self.value

    def advance(self, seconds: float) -> None:
        with self.lock:
            self.value += seconds


def target() -> WindowIdentity:
    return WindowIdentity(
        700,
        ProcessIdentity(800, "notepad.exe", 50.0),
        Rect(100, 100, 600, 500),
        "Notes",
    )


class SnapshotSource:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.calls = 0
        self.current_target = target()
        self.foreground = True
        self.control_label = "Editor"

    def capture(self, expected_target: WindowIdentity | None) -> AtomicScreenSnapshot:
        self.calls += 1
        return AtomicScreenSnapshot(
            target=self.current_target,
            foreground=self.foreground,
            screen_bounds=Rect(0, 0, 1920, 1080),
            cursor=(10, 10),
            ocr_controls=(
                RawControl(
                    self.control_label,
                    Rect(150, 160, 300, 200),
                    0.98,
                    source=ControlSource.OCR,
                ),
            ),
            process_alive=True,
            captured_at=self.clock.monotonic(),
        )


ResponseFactory = Callable[[Mapping[str, object]], Mapping[str, object]]


def response(action: str, *, confidence: float = 1.0, **fields: object) -> ResponseFactory:
    def build(payload: Mapping[str, object]) -> Mapping[str, object]:
        return {
            "action": action,
            "observation_id": payload["observation_id"],
            "confidence": confidence,
            **fields,
        }

    return build


class ScriptedClient:
    def __init__(self, responses: list[ResponseFactory]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []
        self.system_prompts: list[str] = []

    def request(
        self,
        system_prompt: str,
        payload: Mapping[str, object],
        cancel_event: threading.Event,
    ) -> Mapping[str, object]:
        del cancel_event
        self.system_prompts.append(system_prompt)
        # Round-trip captures the actual JSON-safe provider boundary.
        self.calls.append(json.loads(json.dumps(payload, ensure_ascii=False)))
        if not self.responses:
            raise AssertionError("unexpected planner call")
        return self.responses.pop(0)(payload)


class BlockingClient:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def request(
        self,
        system_prompt: str,
        payload: Mapping[str, object],
        cancel_event: threading.Event,
    ) -> Mapping[str, object]:
        del system_prompt, cancel_event
        self.calls += 1
        self.started.set()
        if not self.release.wait(3.0):
            raise AssertionError("test did not release planner")
        return {
            "action": "click_point",
            "observation_id": payload["observation_id"],
            "x": 200,
            "y": 200,
            "confidence": 1.0,
        }


class Harness:
    def __init__(
        self,
        cheap: ScriptedClient | BlockingClient,
        *,
        recovery: ScriptedClient | None = None,
    ) -> None:
        self.clock = FakeClock()
        self.source = SnapshotSource(self.clock)
        self.observer = ComputerObserver(
            self.source,
            monotonic=self.clock.monotonic,
            observation_id_factory=lambda number: f"obs:{number}",
        )
        self.effects: list[object] = []
        self.statuses = []
        self.planner = ComputerPlanner(
            cheap,
            recovery_client=recovery,
            request_id_factory=lambda number: f"request:{number}",
        )
        dependencies = ExecutorDependencies(
            validate_target=self.validate,
            move_pointer=lambda x, y: self.record(("move", x, y)),
            click=lambda x, y: self.record(("click", x, y)),
            double_click=lambda x, y: self.record(("double", x, y)),
            scroll=lambda amount, x, y: self.record(("scroll", amount, x, y)),
            keypress=lambda key: self.record(("key", key)),
            hotkey=lambda keys: self.record(("hotkey", keys)),
            focus_window=lambda window: self.record(("focus", window.hwnd)),
            guarded_type=self.guarded_type,
            wait=self.wait,
            monotonic=self.clock.monotonic,
        )
        self.manager = ComputerUseManager(
            observer=self.observer,
            planner=self.planner,
            policy=ComputerUsePolicy(),
            executor=ComputerExecutor(dependencies),
            verifier=ComputerVerifier(),
            monotonic=self.clock.monotonic,
            status_sink=self.statuses.append,
            session_id_factory=lambda generation: f"session:{generation}",
        )

    def validate(self, expected: WindowIdentity, foreground: bool) -> LiveTargetState:
        self.effects.append(("validate", foreground))
        return LiveTargetState(self.source.current_target, True, self.source.foreground, True)

    def record(self, value: object) -> bool:
        self.effects.append(value)
        return True

    def guarded_type(
        self,
        text: str,
        expected: WindowIdentity,
        cancel_event: threading.Event,
    ) -> bool:
        self.effects.append(("guarded_type", text, expected.hwnd, cancel_event.is_set()))
        return True

    def wait(self, seconds: float, cancel_event: threading.Event) -> bool:
        if cancel_event.is_set():
            return False
        self.clock.advance(seconds)
        return True

    def spec(self, **overrides: object) -> ComputerUseSessionSpec:
        values: dict[str, object] = {
            "goal": "Use Notepad",
            "initial_target": target(),
            "enabled": True,
            "explicit_user_activation": True,
            "request_origin": "user",
            "max_steps": 10,
            "timeout_seconds": 120,
            "recovery_after_failures": 2,
            "max_recovery_calls": 2,
        }
        values.update(overrides)
        return ComputerUseSessionSpec(**values)  # type: ignore[arg-type]


class TestComputerPlanner(unittest.TestCase):
    def test_prompt_is_specialized_and_stale_observation_is_rejected(self) -> None:
        client = ScriptedClient(
            [
                lambda payload: {
                    "action": "finish",
                    "observation_id": "obs:stale",
                    "confidence": 1.0,
                }
            ]
        )
        harness = Harness(client)
        captured = harness.observer.observe(target())

        with self.assertRaises(PlannerProtocolError):
            harness.planner.plan(
                session_id="session:1",
                generation=1,
                step=0,
                goal="finish",
                observation=captured,
                payload_refs=(),
                cancel_event=threading.Event(),
                is_current=lambda session, generation: True,
            )

        self.assertEqual(client.system_prompts, [PLANNER_SYSTEM_PROMPT])
        prompt = client.system_prompts[0].casefold()
        self.assertNotIn("soul.md", prompt)
        self.assertNotIn("relationship", prompt)
        self.assertNotIn("dream", prompt)


class TestComputerUseSession(unittest.TestCase):
    def test_disabled_or_non_user_origin_never_observes_or_calls_provider(self) -> None:
        client = ScriptedClient([])
        harness = Harness(client)

        disabled = harness.manager.run(harness.spec(enabled=False))
        ambient = harness.manager.run(harness.spec(request_origin="ambient"))
        sentinel = harness.manager.run(harness.spec(request_origin="terminal_sentinel"))

        self.assertEqual(disabled.state, SessionState.BLOCKED)
        self.assertEqual(ambient.state, SessionState.BLOCKED)
        self.assertEqual(sentinel.state, SessionState.BLOCKED)
        self.assertEqual(harness.source.calls, 0)
        self.assertEqual(client.calls, [])
        self.assertEqual(harness.effects, [])

    def test_pre_cancelled_bootstrap_event_never_reaches_observer_or_planner(self) -> None:
        client = ScriptedClient([])
        harness = Harness(client)
        cancelled = threading.Event()
        cancelled.set()

        outcome = harness.manager.run(harness.spec(), cancel_event=cancelled)

        self.assertEqual(outcome.state, SessionState.CANCELLED)
        self.assertEqual(harness.source.calls, 0)
        self.assertEqual(client.calls, [])
        self.assertEqual(harness.effects, [])

    def test_sensitive_transition_is_blocked_before_next_planner_call(self) -> None:
        harness: Harness

        def navigate_to_sensitive(payload: Mapping[str, object]) -> Mapping[str, object]:
            harness.source.current_target = replace(
                target(),
                title="Bitwarden password vault",
            )
            return response("wait", amount=1)(payload)

        client = ScriptedClient([navigate_to_sensitive])
        harness = Harness(client)

        outcome = harness.manager.run(harness.spec())

        self.assertEqual(outcome.state, SessionState.BLOCKED)
        self.assertEqual(len(client.calls), 1)
        self.assertIn("sensitive", outcome.safe_reason)

    def test_exact_unicode_payload_never_crosses_planner_boundary(self) -> None:
        private = "สวัสดี 世界"
        client = ScriptedClient(
            [
                response(
                    "type_payload",
                    payload_ref="user_text_1",
                    expected_result="text entered",
                ),
                response("finish"),
            ]
        )
        harness = Harness(client)
        spec = harness.spec(
            goal=f"Type {private} in Notepad",
            payloads={"payload:user_text_1": private},
            typing_authorized=True,
        )

        outcome = harness.manager.run(spec)

        self.assertEqual(outcome.state, SessionState.COMPLETED)
        self.assertIn(("guarded_type", private, 700, False), harness.effects)
        provider_text = json.dumps(client.calls, ensure_ascii=False)
        self.assertNotIn(private, provider_text)
        self.assertIn("payload:user_text_1", provider_text)
        self.assertNotIn(private, repr(spec))
        self.assertNotIn(private, repr(harness.manager.snapshot()))
        self.assertNotIn(private, repr(outcome))

    def test_post_type_ocr_echo_is_replaced_before_every_planner_call(self) -> None:
        private = "สวัสดี 世界"
        harness: Harness

        def type_then_echo(payload: Mapping[str, object]) -> Mapping[str, object]:
            harness.source.control_label = private
            return response(
                "type_payload",
                payload_ref="user_text_1",
                expected_result="text entered",
            )(payload)

        client = ScriptedClient([type_then_echo, response("finish")])
        harness = Harness(client)
        outcome = harness.manager.run(harness.spec(
            goal=f"Type {private} in Notepad",
            payloads={"payload:user_text_1": private},
            typing_authorized=True,
        ))

        provider_text = json.dumps(client.calls, ensure_ascii=False)
        self.assertEqual(outcome.state, SessionState.COMPLETED)
        self.assertEqual(len(client.calls), 2)
        self.assertNotIn(private, provider_text)
        self.assertIn("payload:user_text_1", provider_text)

    def test_low_confidence_reobserves_then_uses_recovery_only(self) -> None:
        cheap = ScriptedClient(
            [
                response("click_point", confidence=0.2, x=200, y=200),
                response("click_point", confidence=0.3, x=200, y=200),
            ]
        )
        recovery = ScriptedClient([response("finish")])
        harness = Harness(cheap, recovery=recovery)

        outcome = harness.manager.run(harness.spec())

        self.assertEqual(outcome.state, SessionState.COMPLETED)
        self.assertEqual(len(cheap.calls), 2)
        self.assertEqual(len(recovery.calls), 1)
        self.assertEqual(outcome.recovery_calls, 1)
        self.assertEqual(harness.effects, [])
        self.assertFalse(cheap.calls[0]["recovery"])
        self.assertTrue(recovery.calls[0]["recovery"])

    def test_blocked_cheap_planner_gets_one_bounded_primary_recovery(self) -> None:
        cheap = ScriptedClient([response("blocked", reason="ambiguous")])
        recovery = ScriptedClient([response("blocked", reason="still ambiguous")])
        harness = Harness(cheap, recovery=recovery)

        outcome = harness.manager.run(harness.spec(max_recovery_calls=1))

        self.assertEqual(outcome.state, SessionState.BLOCKED)
        self.assertEqual(len(cheap.calls), 1)
        self.assertEqual(len(recovery.calls), 1)
        self.assertEqual(outcome.recovery_calls, 1)

    def test_step_limit_is_deterministic(self) -> None:
        client = ScriptedClient([response("observe_again") for _ in range(3)])
        harness = Harness(client)

        outcome = harness.manager.run(harness.spec(max_steps=3))

        self.assertEqual(outcome.state, SessionState.BLOCKED)
        self.assertEqual(outcome.steps, 3)
        self.assertEqual(len(client.calls), 3)
        self.assertIn("step limit", outcome.safe_reason)

    def test_fake_clock_expiry_blocks_action_before_any_effect(self) -> None:
        harness: Harness

        def advance_then_click(payload: Mapping[str, object]) -> Mapping[str, object]:
            harness.clock.advance(5.0)
            return response("click_point", x=200, y=200)(payload)

        client = ScriptedClient([advance_then_click])
        harness = Harness(client)

        outcome = harness.manager.run(harness.spec(timeout_seconds=1.0))

        self.assertEqual(outcome.state, SessionState.BLOCKED)
        self.assertEqual(harness.effects, [])
        self.assertIn("expired", outcome.safe_reason)

    def test_stop_invalidates_generation_while_provider_is_pending(self) -> None:
        client = BlockingClient()
        harness = Harness(client)
        outcomes = []

        worker = threading.Thread(
            target=lambda: outcomes.append(harness.manager.run(harness.spec())),
            daemon=True,
        )
        worker.start()
        self.assertTrue(client.started.wait(2.0))

        before = harness.manager.snapshot().generation
        self.assertTrue(harness.manager.cancel_active("STOP"))
        immediate = harness.manager.snapshot()
        self.assertEqual(immediate.state, SessionState.CANCELLED)
        self.assertGreater(immediate.generation, before)
        self.assertEqual(harness.effects, [])

        client.release.set()
        worker.join(3.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcomes[0].state, SessionState.CANCELLED)
        self.assertEqual(harness.effects, [])

    def test_shutdown_is_idempotent_and_discards_pending_result(self) -> None:
        client = BlockingClient()
        harness = Harness(client)
        outcomes = []
        worker = threading.Thread(
            target=lambda: outcomes.append(harness.manager.run(harness.spec())),
            daemon=True,
        )
        worker.start()
        self.assertTrue(client.started.wait(2.0))

        harness.manager.shutdown()
        harness.manager.shutdown()
        self.assertEqual(harness.manager.snapshot().state, SessionState.SHUTDOWN)
        client.release.set()
        worker.join(3.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(outcomes[0].state, SessionState.SHUTDOWN)
        self.assertEqual(harness.effects, [])


if __name__ == "__main__":
    unittest.main()
