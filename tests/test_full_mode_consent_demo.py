from __future__ import annotations

import inspect
import unittest
from dataclasses import replace

from agetha.platform.full_mode_consent import (
    CONSENT_DEMO_MESSAGE,
    NOTEPAD_COMMAND,
    ConsentDemoProcess,
    ConsentDemoStatus,
    ConsentDemoTarget,
    FixedConsentTyper,
    FullModeConsentDemo,
)


def process(**changes: object) -> ConsentDemoProcess:
    values: dict[str, object] = {
        "pid": 314,
        "process_name": "notepad.exe",
        "created_at": 1234.5,
    }
    values.update(changes)
    return ConsentDemoProcess(**values)


def target(**changes: object) -> ConsentDemoTarget:
    values: dict[str, object] = {
        "pid": 314,
        "process_name": "notepad.exe",
        "created_at": 1234.5,
        "hwnd": 2718,
        "bounds": (20, 30, 640, 480),
        "foreground_hwnd": 2718,
        "process_alive": True,
        "window_valid": True,
    }
    values.update(changes)
    return ConsentDemoTarget(**values)


class FakeDemoEnvironment:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []
        self.launched: ConsentDemoProcess | None = process()
        self.waited: ConsentDemoTarget | None = target()
        self.current: ConsentDemoTarget | None = target()
        self.cancelled = False
        self.stopping = False
        self.now = 100.0
        self.type_result = True

    def launch(self, command: tuple[str, ...]) -> ConsentDemoProcess | None:
        self.events.append(("launch", command))
        return self.launched

    def wait_for_target(
        self,
        launched: ConsentDemoProcess,
        timeout_seconds: float,
        should_abort,
    ) -> ConsentDemoTarget | None:
        self.events.append(
            ("wait", launched.pid, timeout_seconds, bool(should_abort()))
        )
        return self.waited

    def validate(
        self,
        expected: ConsentDemoTarget,
    ) -> ConsentDemoTarget | None:
        self.events.append(("validate", expected.hwnd))
        return self.current

    def type_static(self, live: ConsentDemoTarget, text: str) -> bool:
        self.events.append(("type", live.hwnd, text))
        return self.type_result

    def clock(self) -> float:
        return self.now

    def demo(self, *, timeout_seconds: float = 4.0) -> FullModeConsentDemo:
        return FullModeConsentDemo(
            launcher=self.launch,
            target_wait=self.wait_for_target,
            validator=self.validate,
            type_static=FixedConsentTyper(
                send_static=self.type_static,
                authorized=lambda _target: True,
            ),
            cancel_requested=lambda: self.cancelled,
            shutdown_requested=lambda: self.stopping,
            clock=self.clock,
            timeout_seconds=timeout_seconds,
        )


class FullModeConsentDemoTests(unittest.TestCase):
    def test_success_uses_only_fixed_notepad_command_and_static_warning(self) -> None:
        fake = FakeDemoEnvironment()

        result = fake.demo().run_full_mode_consent_demo()

        self.assertEqual(result.status, ConsentDemoStatus.TYPED)
        self.assertTrue(result.typed)
        self.assertEqual(fake.events[0], ("launch", ("notepad.exe",)))
        type_events = [event for event in fake.events if event[0] == "type"]
        self.assertEqual(
            type_events,
            [("type", 2718, CONSENT_DEMO_MESSAGE)],
        )
        self.assertEqual(NOTEPAD_COMMAND, ("notepad.exe",))
        self.assertTrue(
            CONSENT_DEMO_MESSAGE.startswith(
                "ARE YOU REALLY SURE YOU WANT TO CONTINUE THIS?\n\n"
            )
        )

    def test_public_run_api_accepts_no_app_or_text_payload(self) -> None:
        fake = FakeDemoEnvironment()
        demo = fake.demo()

        with self.assertRaises(TypeError):
            demo.run_full_mode_consent_demo("user supplied text")  # type: ignore[call-arg]
        with self.assertRaisesRegex(TypeError, "unexpected keyword argument 'executable'"):
            FullModeConsentDemo(  # type: ignore[call-arg]
                launcher=fake.launch,
                target_wait=fake.wait_for_target,
                validator=fake.validate,
                type_static=fake.type_static,
                cancel_requested=lambda: False,
                shutdown_requested=lambda: False,
                executable="other.exe",
            )

    def test_fixed_typer_public_call_accepts_no_text_parameter(self) -> None:
        sent: list[str] = []
        fixed = FixedConsentTyper(
            send_static=lambda locked, text: sent.append(text) or True,
            authorized=lambda locked: locked.hwnd == 2718,
        )

        self.assertEqual(tuple(inspect.signature(fixed.__call__).parameters), ("target",))
        self.assertTrue(fixed(target()))
        self.assertEqual(sent, [CONSENT_DEMO_MESSAGE])

    def test_launch_failure_returns_fallback_without_typing(self) -> None:
        fake = FakeDemoEnvironment()
        fake.launched = None

        result = fake.demo().run_full_mode_consent_demo()

        self.assertEqual(result.status, ConsentDemoStatus.LAUNCH_FAILED)
        self.assertFalse(result.typed)
        self.assertFalse(any(event[0] == "type" for event in fake.events))

    def test_wrong_launched_process_is_rejected_before_target_wait(self) -> None:
        fake = FakeDemoEnvironment()
        fake.launched = process(process_name="other.exe")

        result = fake.demo().run_full_mode_consent_demo()

        self.assertEqual(result.status, ConsentDemoStatus.TARGET_CHANGED)
        self.assertEqual([event[0] for event in fake.events], ["launch"])

    def test_initial_target_must_match_pid_name_creation_hwnd_bounds_and_foreground(self) -> None:
        mutations = {
            "pid": {"pid": 999},
            "name": {"process_name": "wordpad.exe"},
            "creation": {"created_at": 1234.6},
            "hwnd": {"hwnd": 0},
            "bounds": {"bounds": (20, 30, 0, 480)},
            "foreground": {"foreground_hwnd": 8},
            "process-exit": {"process_alive": False},
            "invalid-window": {"window_valid": False},
        }
        for label, change in mutations.items():
            with self.subTest(label=label):
                fake = FakeDemoEnvironment()
                fake.waited = target(**change)

                result = fake.demo().run_full_mode_consent_demo()

                expected = (
                    ConsentDemoStatus.PROCESS_EXITED
                    if label == "process-exit"
                    else ConsentDemoStatus.TARGET_CHANGED
                )
                self.assertEqual(result.status, expected)
                self.assertFalse(any(event[0] == "type" for event in fake.events))

    def test_boolean_pid_cannot_alias_numeric_notepad_pid(self) -> None:
        fake = FakeDemoEnvironment()
        fake.launched = process(pid=1)
        fake.waited = target(pid=True)
        fake.current = target(pid=True)

        result = fake.demo().run_full_mode_consent_demo()

        self.assertEqual(result.status, ConsentDemoStatus.TARGET_CHANGED)
        self.assertFalse(any(event[0] == "type" for event in fake.events))

    def test_fresh_validation_must_preserve_every_locked_target_field(self) -> None:
        mutations = {
            "pid": {"pid": 999},
            "name": {"process_name": "other.exe"},
            "creation": {"created_at": 1234.6},
            "hwnd": {"hwnd": 99},
            "bounds": {"bounds": (21, 30, 640, 480)},
            "foreground": {"foreground_hwnd": 99},
            "process-exit": {"process_alive": False},
            "invalid-window": {"window_valid": False},
        }
        for label, change in mutations.items():
            with self.subTest(label=label):
                fake = FakeDemoEnvironment()
                fake.current = replace(target(), **change)

                result = fake.demo().run_full_mode_consent_demo()

                expected = (
                    ConsentDemoStatus.PROCESS_EXITED
                    if label == "process-exit"
                    else ConsentDemoStatus.TARGET_CHANGED
                )
                self.assertEqual(result.status, expected)
                self.assertFalse(any(event[0] == "type" for event in fake.events))

    def test_cancel_after_validation_is_checked_immediately_before_typing(self) -> None:
        fake = FakeDemoEnvironment()

        def validate_then_cancel(
            expected: ConsentDemoTarget,
        ) -> ConsentDemoTarget:
            fake.events.append(("validate", expected.hwnd))
            fake.cancelled = True
            return target()

        fake.validate = validate_then_cancel  # type: ignore[method-assign]

        result = fake.demo().run_full_mode_consent_demo()

        self.assertEqual(result.status, ConsentDemoStatus.CANCELLED)
        self.assertFalse(any(event[0] == "type" for event in fake.events))

    def test_shutdown_before_launch_prevents_every_external_effect(self) -> None:
        fake = FakeDemoEnvironment()
        fake.stopping = True

        result = fake.demo().run_full_mode_consent_demo()

        self.assertEqual(result.status, ConsentDemoStatus.SHUTDOWN)
        self.assertEqual(fake.events, [])

    def test_wait_receives_a_bounded_timeout_and_late_result_is_not_typed(self) -> None:
        fake = FakeDemoEnvironment()

        def wait_past_deadline(launched, timeout_seconds, should_abort):
            fake.events.append(("wait", launched.pid, timeout_seconds, False))
            fake.now += timeout_seconds + 0.01
            return target()

        fake.wait_for_target = wait_past_deadline  # type: ignore[method-assign]

        result = fake.demo(timeout_seconds=2.5).run_full_mode_consent_demo()

        self.assertEqual(result.status, ConsentDemoStatus.TIMED_OUT)
        wait_event = next(event for event in fake.events if event[0] == "wait")
        self.assertGreater(wait_event[2], 0.0)
        self.assertLessEqual(wait_event[2], 2.5)
        self.assertFalse(any(event[0] == "type" for event in fake.events))

    def test_validator_exception_fails_closed_without_typing(self) -> None:
        fake = FakeDemoEnvironment()

        def broken_validator(_expected):
            raise RuntimeError("validation unavailable")

        fake.validate = broken_validator  # type: ignore[method-assign]

        result = fake.demo().run_full_mode_consent_demo()

        self.assertEqual(result.status, ConsentDemoStatus.TARGET_CHANGED)
        self.assertFalse(any(event[0] == "type" for event in fake.events))

    def test_broken_clock_returns_a_controlled_fallback_without_launching(self) -> None:
        fake = FakeDemoEnvironment()

        def broken_clock() -> float:
            raise OSError("monotonic clock unavailable")

        fake.clock = broken_clock  # type: ignore[method-assign]

        result = fake.demo().run_full_mode_consent_demo()

        self.assertEqual(result.status, ConsentDemoStatus.TIMED_OUT)
        self.assertEqual(fake.events, [])

    def test_type_failure_never_reports_the_demo_as_completed(self) -> None:
        fake = FakeDemoEnvironment()
        fake.type_result = False

        result = fake.demo().run_full_mode_consent_demo()

        self.assertEqual(result.status, ConsentDemoStatus.TYPE_FAILED)
        self.assertFalse(result.typed)

    def test_non_boolean_typer_result_never_reports_success(self) -> None:
        fake = FakeDemoEnvironment()
        fake.type_result = "yes"  # type: ignore[assignment]

        result = fake.demo().run_full_mode_consent_demo()

        self.assertEqual(result.status, ConsentDemoStatus.TYPE_FAILED)
        self.assertFalse(result.typed)


if __name__ == "__main__":
    unittest.main()
