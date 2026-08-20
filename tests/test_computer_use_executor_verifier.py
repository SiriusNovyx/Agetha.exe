"""Injected effect boundary and local verifier tests."""

from __future__ import annotations

import sys
import threading
import unittest
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agetha.computer_use.executor import (  # noqa: E402
    ComputerExecutor,
    ExecutorDependencies,
)
from agetha.computer_use.models import (  # noqa: E402
    ComputerAction,
    ComputerActionKind,
    ComputerObservation,
    ControlSource,
    ExecutionResult,
    ExecutionStatus,
    LiveTargetState,
    ObservedControl,
    PolicyCode,
    PolicyDecision,
    PolicyDisposition,
    ProcessIdentity,
    Rect,
    VerificationStatus,
    WindowIdentity,
)
from agetha.computer_use.verifier import ComputerVerifier  # noqa: E402


def target(*, created_at: float = 5.0) -> WindowIdentity:
    return WindowIdentity(
        55,
        ProcessIdentity(321, "notepad.exe", created_at),
        Rect(100, 100, 400, 300),
        "Notes",
    )


def observation(*, observation_id: str = "obs:1", state: str = "") -> ComputerObservation:
    return ComputerObservation(
        observation_id=observation_id,
        target=target(),
        foreground=True,
        screen_bounds=Rect(0, 0, 1920, 1080),
        cursor=(0, 0),
        controls=(
            ObservedControl(
                "ocr:1",
                ControlSource.OCR,
                "Editor",
                Rect(120, 130, 100, 40),
                1.0,
                state=state,
            ),
        ),
        process_alive=True,
        captured_at=1.0,
    )


ALLOW = PolicyDecision(PolicyDisposition.ALLOW, PolicyCode.ALLOWED, "allowed")


class FakeEffects:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.now = 10.0
        self.live = LiveTargetState(target(), True, True, True)
        self.shutdown = False
        self.invalidate_during_type = False

    def validate(self, expected: WindowIdentity, foreground: bool) -> LiveTargetState:
        self.events.append(("validate", expected.hwnd, foreground))
        return self.live

    def move(self, x: int, y: int) -> bool:
        self.events.append(("move", x, y))
        return True

    def click(self, x: int, y: int) -> bool:
        self.events.append(("click", x, y))
        return True

    def double(self, x: int, y: int) -> bool:
        self.events.append(("double", x, y))
        return True

    def scroll(self, amount: int, x: int | None, y: int | None) -> bool:
        self.events.append(("scroll", amount, x, y))
        return True

    def keypress(self, key: str) -> bool:
        self.events.append(("key", key))
        return True

    def hotkey(self, keys: tuple[str, ...]) -> bool:
        self.events.append(("hotkey", keys))
        return True

    def focus(self, expected: WindowIdentity) -> bool:
        self.events.append(("focus", expected.hwnd))
        return True

    def guarded_type(self, text: str, expected: WindowIdentity, event: threading.Event) -> bool:
        self.events.append(("guarded_type", text, expected.hwnd, event.is_set()))
        if self.invalidate_during_type:
            self.live = LiveTargetState(target(created_at=9.0), True, True, False)
            return False
        return True

    def wait(self, seconds: float, event: threading.Event) -> bool:
        self.events.append(("wait", seconds, event.is_set()))
        self.now += seconds
        return True

    def executor(self) -> ComputerExecutor:
        return ComputerExecutor(
            ExecutorDependencies(
                validate_target=self.validate,
                move_pointer=self.move,
                click=self.click,
                double_click=self.double,
                scroll=self.scroll,
                keypress=self.keypress,
                hotkey=self.hotkey,
                focus_window=self.focus,
                guarded_type=self.guarded_type,
                wait=self.wait,
                is_shutdown=lambda: self.shutdown,
                monotonic=lambda: self.now,
            )
        )


class TestComputerExecutor(unittest.TestCase):
    def setUp(self) -> None:
        self.effects = FakeEffects()
        self.executor = self.effects.executor()
        self.cancel = threading.Event()

    def execute(self, action: ComputerAction, *, payloads: dict[str, str] | None = None) -> ExecutionResult:
        return self.executor.execute(
            action,
            observation(),
            ALLOW,
            payloads=payloads or {},
            cancel_event=self.cancel,
            deadline=100.0,
        )

    def test_click_control_revalidates_target_immediately_before_effect(self) -> None:
        result = self.execute(
            ComputerAction(
                ComputerActionKind.CLICK_CONTROL,
                "obs:1",
                target_id="ocr:1",
                confidence=1.0,
            )
        )

        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(
            self.effects.events,
            [("validate", 55, True), ("click", 170, 150)],
        )

    def test_every_effect_kind_uses_the_single_injected_boundary(self) -> None:
        cases = (
            ComputerAction(ComputerActionKind.MOVE_POINTER, "obs:1", x=150, y=150, confidence=1),
            ComputerAction(
                ComputerActionKind.DOUBLE_CLICK_CONTROL,
                "obs:1",
                target_id="ocr:1",
                confidence=1,
            ),
            ComputerAction(
                ComputerActionKind.SCROLL,
                "obs:1",
                amount=-3,
                x=150,
                y=150,
                confidence=1,
            ),
            ComputerAction(ComputerActionKind.KEYPRESS, "obs:1", key="tab", confidence=1),
            ComputerAction(
                ComputerActionKind.HOTKEY,
                "obs:1",
                keys=("ctrl", "a"),
                confidence=1,
            ),
        )
        for current in cases:
            with self.subTest(action=current.action):
                self.effects.events.clear()
                result = self.execute(current)
                self.assertEqual(result.status, ExecutionStatus.SUCCESS)
                self.assertEqual(self.effects.events[0], ("validate", 55, True))
                self.assertEqual(len(self.effects.events), 2)

    def test_scroll_without_coordinates_uses_locked_target_center(self) -> None:
        result = self.execute(
            ComputerAction(
                ComputerActionKind.SCROLL,
                "obs:1",
                amount=-3,
                confidence=1,
            )
        )

        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(
            self.effects.events,
            [("validate", 55, True), ("scroll", -3, 300, 250)],
        )

    def test_stop_arriving_during_target_validation_blocks_the_effect(self) -> None:
        original_validate = self.effects.validate

        def validate_then_cancel(expected: WindowIdentity, foreground: bool) -> LiveTargetState:
            result = original_validate(expected, foreground)
            self.cancel.set()
            return result

        self.executor = ComputerExecutor(
            replace(self.effects.executor()._deps, validate_target=validate_then_cancel)  # type: ignore[attr-defined]
        )
        click = ComputerAction(
            ComputerActionKind.CLICK_POINT,
            "obs:1",
            x=150,
            y=150,
            confidence=1,
        )

        result = self.execute(click)

        self.assertEqual(result.status, ExecutionStatus.CANCELLED)
        self.assertEqual(self.effects.events, [("validate", 55, True)])

    def test_pid_reuse_or_focus_change_prevents_all_input(self) -> None:
        actions = (
            ComputerAction(ComputerActionKind.CLICK_POINT, "obs:1", x=150, y=150, confidence=1),
            ComputerAction(ComputerActionKind.KEYPRESS, "obs:1", key="tab", confidence=1),
        )
        invalid_states = (
            LiveTargetState(target(created_at=6.0), True, True, True),
            LiveTargetState(target(), True, False, True),
        )
        for current_action, state in zip(actions, invalid_states):
            with self.subTest(action=current_action.action):
                self.effects.events.clear()
                self.effects.live = state
                result = self.execute(current_action)
                self.assertEqual(result.status, ExecutionStatus.TARGET_CHANGED)
                self.assertEqual(self.effects.events[0][0], "validate")  # type: ignore[index]
                self.assertEqual(len(self.effects.events), 1)

    def test_cancellation_shutdown_and_policy_denial_have_no_effect(self) -> None:
        click = ComputerAction(
            ComputerActionKind.CLICK_POINT,
            "obs:1",
            x=150,
            y=150,
            confidence=1,
        )
        self.cancel.set()
        self.assertEqual(self.execute(click).status, ExecutionStatus.CANCELLED)
        self.assertEqual(self.effects.events, [])

        self.cancel.clear()
        denied = PolicyDecision(PolicyDisposition.DENY, PolicyCode.OUT_OF_BOUNDS, "outside")
        result = self.executor.execute(
            click,
            observation(),
            denied,
            payloads={},
            cancel_event=self.cancel,
            deadline=100,
        )
        self.assertEqual(result.status, ExecutionStatus.POLICY_DENIED)
        self.assertEqual(self.effects.events, [])

        self.effects.shutdown = True
        self.assertEqual(self.execute(click).status, ExecutionStatus.SHUTDOWN)
        self.assertEqual(self.effects.events, [])

    def test_stale_observation_id_is_rejected_even_with_allow_decision(self) -> None:
        stale = ComputerAction(
            ComputerActionKind.CLICK_POINT,
            "obs:stale",
            x=150,
            y=150,
            confidence=1,
        )
        result = self.execute(stale)
        self.assertEqual(result.status, ExecutionStatus.TARGET_CHANGED)
        self.assertEqual(self.effects.events, [])

    def test_exact_unicode_payload_is_resolved_only_at_guarded_typing_callback(self) -> None:
        private = "สวัสดี 世界"
        action = ComputerAction(
            ComputerActionKind.TYPE_PAYLOAD,
            "obs:1",
            payload_ref="user_text_1",
            confidence=1,
        )

        result = self.execute(action, payloads={"user_text_1": private})

        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(
            self.effects.events,
            [
                ("validate", 55, True),
                ("guarded_type", private, 55, False),
            ],
        )
        self.assertNotIn(private, repr(result))

    def test_target_change_during_guarded_typing_is_reported_structurally(self) -> None:
        self.effects.invalidate_during_type = True
        action = ComputerAction(
            ComputerActionKind.TYPE_PAYLOAD,
            "obs:1",
            payload_ref="user_text_1",
            confidence=1,
        )

        result = self.execute(action, payloads={"user_text_1": "private"})

        self.assertEqual(result.status, ExecutionStatus.TARGET_CHANGED)
        self.assertEqual(
            self.effects.events,
            [
                ("validate", 55, True),
                ("guarded_type", "private", 55, False),
                ("validate", 55, True),
            ],
        )
        self.assertNotIn("private", repr(result))

    def test_focus_validation_does_not_require_foreground_but_other_actions_do(self) -> None:
        focus = ComputerAction(ComputerActionKind.FOCUS_WINDOW, "obs:1", confidence=1)
        result = self.execute(focus)

        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(self.effects.events[0], ("validate", 55, False))
        self.assertEqual(self.effects.events[1], ("focus", 55))

    def test_wait_uses_injected_clock_and_waiter_without_input_validation(self) -> None:
        wait = ComputerAction(ComputerActionKind.WAIT, "obs:1", amount=250, confidence=1)
        result = self.execute(wait)

        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(self.effects.events, [("wait", 0.25, False)])


class TestComputerVerifier(unittest.TestCase):
    def setUp(self) -> None:
        self.verifier = ComputerVerifier()

    def test_control_state_change_is_verified_locally(self) -> None:
        action = ComputerAction(
            ComputerActionKind.CLICK_CONTROL,
            "obs:1",
            target_id="ocr:1",
            confidence=1,
        )
        execution = ExecutionResult(ExecutionStatus.SUCCESS, action.action, "effect completed")

        result = self.verifier.verify(
            observation(state="off"),
            observation(observation_id="obs:2", state="on"),
            action,
            execution,
        )

        self.assertEqual(result.status, VerificationStatus.VERIFIED)

    def test_unprovable_typing_is_left_unverified_without_an_ai_call(self) -> None:
        action = ComputerAction(
            ComputerActionKind.TYPE_PAYLOAD,
            "obs:1",
            payload_ref="user_text_1",
            confidence=1,
        )
        result = self.verifier.verify(
            observation(),
            observation(observation_id="obs:2"),
            action,
            ExecutionResult(ExecutionStatus.SUCCESS, action.action),
        )
        self.assertEqual(result.status, VerificationStatus.UNVERIFIED)

    def test_target_change_and_executor_failure_fail_closed(self) -> None:
        action = ComputerAction(ComputerActionKind.KEYPRESS, "obs:1", key="tab", confidence=1)
        failed = self.verifier.verify(
            observation(),
            observation(observation_id="obs:2"),
            action,
            ExecutionResult(ExecutionStatus.FAILED, action.action, "failed"),
        )
        self.assertEqual(failed.status, VerificationStatus.FAILED)

        changed_observation = replace(
            observation(observation_id="obs:2"),
            target=target(created_at=6.0),
        )
        changed = self.verifier.verify(
            observation(),
            changed_observation,
            action,
            ExecutionResult(ExecutionStatus.SUCCESS, action.action),
        )
        self.assertEqual(changed.status, VerificationStatus.TARGET_CHANGED)


if __name__ == "__main__":
    unittest.main()
