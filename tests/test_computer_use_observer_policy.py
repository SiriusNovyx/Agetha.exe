"""Atomic observer and deterministic policy tests."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agetha.computer_use.models import (  # noqa: E402
    ComputerAction,
    ComputerActionKind,
    ComputerObservation,
    ControlSource,
    ObservedControl,
    PolicyCode,
    PolicyDisposition,
    ProcessIdentity,
    Rect,
    WindowIdentity,
)
from agetha.computer_use.observer import (  # noqa: E402
    AtomicScreenSnapshot,
    ComputerObserver,
    RawControl,
    UnavailableAccessibilityProvider,
)
from agetha.computer_use.policy import ComputerUsePolicy, PolicyContext  # noqa: E402


def locked_target(
    *,
    pid: int = 200,
    created_at: float | None = 20.0,
    title: str = "Notes",
    process: str = "notepad.exe",
) -> WindowIdentity:
    return WindowIdentity(
        hwnd=99,
        process=ProcessIdentity(pid, process, created_at),
        bounds=Rect(100, 100, 500, 400),
        title=title,
    )


class FakeSource:
    def __init__(self, snapshot: AtomicScreenSnapshot) -> None:
        self.snapshot = snapshot
        self.expected: list[WindowIdentity | None] = []

    def capture(self, expected_target: WindowIdentity | None) -> AtomicScreenSnapshot:
        self.expected.append(expected_target)
        return self.snapshot


class FakeAccessibility:
    available = True

    def __init__(self, controls: tuple[RawControl, ...]) -> None:
        self._controls = controls

    def controls(self, snapshot: AtomicScreenSnapshot) -> tuple[RawControl, ...]:
        self.last_snapshot = snapshot
        return self._controls


def snapshot(*, target: WindowIdentity | None = None) -> AtomicScreenSnapshot:
    return AtomicScreenSnapshot(
        target=target or locked_target(),
        foreground=True,
        screen_bounds=Rect(0, 0, 1920, 1080),
        cursor=(1, 2),
        ocr_controls=(
            RawControl("Editor password=secret", Rect(120, 130, 200, 80), 0.91),
            RawControl("outside", Rect(900, 900, 20, 20), 1.0),
        ),
        process_alive=True,
        captured_at=100.0,
    )


def observation(*, target: WindowIdentity | None = None, foreground: bool = True) -> ComputerObservation:
    current = target or locked_target()
    return ComputerObservation(
        observation_id="obs:1",
        target=current,
        foreground=foreground,
        screen_bounds=Rect(0, 0, 1920, 1080),
        cursor=(0, 0),
        controls=(
            ObservedControl(
                "ocr:1",
                ControlSource.OCR,
                "Editor",
                Rect(120, 130, 200, 80),
                0.9,
            ),
        ),
        process_alive=True,
        captured_at=100.0,
    )


def action(kind: ComputerActionKind, **kwargs: object) -> ComputerAction:
    confidence = kwargs.pop("confidence", 0.9)
    return ComputerAction(
        action=kind,
        observation_id="obs:1",
        confidence=confidence,
        **kwargs,
    )


def context(**overrides: object) -> PolicyContext:
    values: dict[str, object] = {
        "enabled": True,
        "explicit_user_activation": True,
        "request_origin": "user",
        "session_id": "session:1",
        "expected_session_id": "session:1",
        "generation": 1,
        "expected_generation": 1,
        "now": 100.0,
        "deadline": 200.0,
        "step": 0,
        "max_steps": 30,
        "cancelled": False,
        "shutdown": False,
        "expected_target": locked_target(),
        "allowed_processes": frozenset({"NOTEPAD.EXE"}),
        "payload_refs": frozenset({"user_text_1"}),
        "typing_authorized": True,
    }
    values.update(overrides)
    return PolicyContext(**values)  # type: ignore[arg-type]


class TestComputerObserver(unittest.TestCase):
    def test_noop_accessibility_is_honest_and_ocr_ids_are_observation_scoped(self) -> None:
        source = FakeSource(snapshot())
        observer = ComputerObserver(
            source,
            accessibility=UnavailableAccessibilityProvider(),
            observation_id_factory=lambda number: f"obs:{number}",
        )

        first = observer.observe(locked_target())
        second = observer.observe(locked_target())

        self.assertFalse(first.accessibility_available)
        self.assertEqual(first.controls[0].control_id, "ocr:1")
        self.assertEqual(second.controls[0].control_id, "ocr:1")
        self.assertNotEqual(first.observation_id, second.observation_id)
        self.assertEqual(source.expected, [locked_target(), locked_target()])
        self.assertNotIn("secret", first.controls[0].label)
        self.assertIn("[REDACTED]", first.controls[0].label)
        self.assertEqual(len(first.controls), 1)  # out-of-target OCR was dropped

    def test_accessible_controls_precede_and_deduplicate_ocr(self) -> None:
        native = RawControl(
            "Editor password=secret",
            Rect(120, 130, 200, 80),
            1.0,
            source=ControlSource.ACCESSIBILITY,
            role="textbox",
        )
        provider = FakeAccessibility((native,))
        observer = ComputerObserver(
            FakeSource(snapshot()),
            accessibility=provider,
            observation_id_factory=lambda number: f"obs:{number}",
        )

        result = observer.observe(locked_target())

        self.assertTrue(result.accessibility_available)
        self.assertEqual(result.controls[0].control_id, "acc:1")
        self.assertEqual(result.controls[0].source, ControlSource.ACCESSIBILITY)
        # The OCR duplicate differs in role, so it remains an explicitly lower-priority item.
        self.assertEqual(result.controls[1].control_id, "ocr:1")


class TestComputerUsePolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = ComputerUsePolicy()

    def test_session_gates_fail_closed(self) -> None:
        cases = (
            (context(enabled=False), PolicyCode.FEATURE_DISABLED),
            (context(request_origin="ambient"), PolicyCode.USER_AUTHORITY_REQUIRED),
            (context(generation=2), PolicyCode.SESSION_MISMATCH),
            (context(cancelled=True), PolicyCode.CANCELLED),
            (context(shutdown=True), PolicyCode.SHUTDOWN),
            (context(now=200.0), PolicyCode.EXPIRED),
            (context(step=30), PolicyCode.STEP_LIMIT),
        )
        finish = action(ComputerActionKind.FINISH)
        for current, expected in cases:
            with self.subTest(expected=expected):
                result = self.policy.evaluate(finish, observation(), current)
                self.assertFalse(result.allowed)
                self.assertEqual(result.code, expected)

    def test_pid_reuse_bounds_and_focus_changes_stop_effects(self) -> None:
        click = action(ComputerActionKind.CLICK_CONTROL, target_id="ocr:1")
        reused = observation(target=locked_target(created_at=21.0))
        moved = observation(target=replace(locked_target(), bounds=Rect(110, 100, 500, 400)))
        background = observation(foreground=False)

        for current in (reused, moved):
            result = self.policy.evaluate(click, current, context())
            self.assertEqual(result.disposition, PolicyDisposition.REOBSERVE)
            self.assertEqual(result.code, PolicyCode.TARGET_CHANGED)
        result = self.policy.evaluate(click, background, context())
        self.assertEqual(result.code, PolicyCode.TARGET_NOT_FOREGROUND)

    def test_controls_and_raw_points_require_bounds_and_confidence(self) -> None:
        low = action(ComputerActionKind.CLICK_CONTROL, target_id="ocr:1", confidence=0.2)
        outside = action(ComputerActionKind.CLICK_POINT, x=10, y=10)
        inside = action(ComputerActionKind.CLICK_POINT, x=150, y=150)

        self.assertEqual(
            self.policy.evaluate(low, observation(), context()).code,
            PolicyCode.LOW_CONFIDENCE,
        )
        self.assertEqual(
            self.policy.evaluate(outside, observation(), context()).code,
            PolicyCode.OUT_OF_BOUNDS,
        )
        self.assertTrue(self.policy.evaluate(inside, observation(), context()).allowed)

    def test_typing_keypress_hotkey_and_enter_rules(self) -> None:
        type_ok = action(ComputerActionKind.TYPE_PAYLOAD, payload_ref="user_text_1")
        type_bad = action(ComputerActionKind.TYPE_PAYLOAD, payload_ref="user_text_2")
        enter = action(ComputerActionKind.KEYPRESS, key="enter")
        safe_key = action(ComputerActionKind.KEYPRESS, key="tab")
        bad_hotkey = action(ComputerActionKind.HOTKEY, keys=("alt", "f4"))

        self.assertTrue(self.policy.evaluate(type_ok, observation(), context()).allowed)
        self.assertEqual(
            self.policy.evaluate(type_bad, observation(), context()).code,
            PolicyCode.PAYLOAD_UNAUTHORIZED,
        )
        self.assertEqual(
            self.policy.evaluate(enter, observation(), context()).code,
            PolicyCode.SUBMIT_NOT_AUTHORIZED,
        )
        self.assertTrue(
            self.policy.evaluate(enter, observation(), context(submit_authorized=True)).allowed
        )
        self.assertTrue(self.policy.evaluate(safe_key, observation(), context()).allowed)
        self.assertEqual(
            self.policy.evaluate(bad_hotkey, observation(), context()).code,
            PolicyCode.HOTKEY_NOT_ALLOWED,
        )
        low_type = replace(type_ok, confidence=0.1)
        self.assertEqual(
            self.policy.evaluate(low_type, observation(), context()).code,
            PolicyCode.LOW_CONFIDENCE,
        )

    def test_sensitive_target_hands_off_even_if_planner_claims_finish(self) -> None:
        secure = observation(
            target=locked_target(
                title="Enter password",
                process="password-manager.exe",
            )
        )
        secure_context = context(
            expected_target=secure.target,
            allowed_processes=frozenset({"password-manager.exe"}),
        )

        result = self.policy.evaluate(action(ComputerActionKind.FINISH), secure, secure_context)

        self.assertEqual(result.disposition, PolicyDisposition.HANDOFF)
        self.assertEqual(result.code, PolicyCode.SENSITIVE_HANDOFF)

    def test_named_password_managers_always_require_handoff(self) -> None:
        for process_name in ("Bitwarden.exe", "KeePass.exe", "1Password.exe"):
            with self.subTest(process_name=process_name):
                secure = observation(target=locked_target(process=process_name))
                result = self.policy.evaluate(
                    action(ComputerActionKind.FINISH),
                    secure,
                    context(
                        expected_target=secure.target,
                        allowed_processes=frozenset({process_name}),
                    ),
                )
                self.assertEqual(result.disposition, PolicyDisposition.HANDOFF)
                self.assertEqual(result.code, PolicyCode.SENSITIVE_HANDOFF)

    def test_high_impact_goal_or_selected_control_requires_handoff(self) -> None:
        click = action(ComputerActionKind.CLICK_CONTROL, target_id="ocr:1")
        point = action(ComputerActionKind.CLICK_POINT, x=150, y=150)
        destructive_control = replace(
            observation(),
            controls=(replace(observation().controls[0], label="Delete account"),),
        )

        from_goal = self.policy.evaluate(
            click,
            observation(),
            context(goal_summary="Install software as administrator"),
        )
        from_control = self.policy.evaluate(click, destructive_control, context())
        from_point = self.policy.evaluate(point, destructive_control, context())

        self.assertEqual(from_goal.disposition, PolicyDisposition.HANDOFF)
        self.assertEqual(from_control.disposition, PolicyDisposition.HANDOFF)
        self.assertEqual(from_point.disposition, PolicyDisposition.HANDOFF)

    def test_install_reset_account_and_payment_phrases_require_handoff(self) -> None:
        phrases = (
            "Install Chrome",
            "Installer setup",
            "Remove this app",
            "Reset this PC",
            "Wipe drive",
            "Transfer funds",
            "Buy now",
            "Subscribe",
            "Change account settings",
        )
        point = action(ComputerActionKind.CLICK_POINT, x=150, y=150)
        for phrase in phrases:
            with self.subTest(phrase=phrase, source="goal"):
                result = self.policy.preflight_observation(
                    observation(),
                    context(goal_summary=phrase),
                )
                self.assertEqual(result.disposition, PolicyDisposition.HANDOFF)
            with self.subTest(phrase=phrase, source="visible_control"):
                high_impact = replace(
                    observation(),
                    controls=(replace(observation().controls[0], label=phrase),),
                )
                result = self.policy.evaluate(point, high_impact, context())
                self.assertEqual(result.disposition, PolicyDisposition.HANDOFF)

    def test_administrator_or_elevated_window_title_requires_handoff(self) -> None:
        for title in (
            "Administrator: Windows PowerShell",
            "Elevated Command Prompt",
        ):
            with self.subTest(title=title):
                secure = observation(target=locked_target(title=title))
                result = self.policy.evaluate(
                    action(ComputerActionKind.FINISH),
                    secure,
                    context(expected_target=secure.target),
                )
                self.assertEqual(result.disposition, PolicyDisposition.HANDOFF)

    def test_focus_requires_explicit_authority_and_respects_presence_restriction(self) -> None:
        focus = action(ComputerActionKind.FOCUS_WINDOW)
        self.assertEqual(
            self.policy.evaluate(focus, observation(foreground=False), context()).code,
            PolicyCode.FOCUS_NOT_AUTHORIZED,
        )
        self.assertTrue(
            self.policy.evaluate(
                focus,
                observation(foreground=False),
                context(focus_authorized=True),
            ).allowed
        )
        self.assertEqual(
            self.policy.evaluate(
                focus,
                observation(foreground=False),
                context(focus_authorized=True, presentation_restricted=True),
            ).code,
            PolicyCode.FOCUS_RESTRICTED,
        )


if __name__ == "__main__":
    unittest.main()
