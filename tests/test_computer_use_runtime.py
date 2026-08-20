"""Fake-only tests for the Computer Use runtime adapters."""

from __future__ import annotations

import sys
import threading
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agetha.computer_use.models import (  # noqa: E402
    LiveTargetState,
    ProcessIdentity as ComputerProcessIdentity,
    Rect,
    WindowIdentity,
)
from agetha.computer_use.runtime import (  # noqa: E402
    ComputerUseRuntimeUnavailable,
    ExactWindowFocus,
    LazyPyAutoGUIInput,
    LockedTargetScreenSource,
    LockedTargetValidator,
    build_executor_dependencies,
    ocr_word_to_raw_control,
    process_identity_to_computer_use,
    running_application_to_window_identity,
    runtime_platform_status,
)
from agetha.platform.process_awareness import (  # noqa: E402
    ProcessContextMode,
    ProcessIdentity,
    RunningApplication,
    identities_match,
)


def awareness_identity(*, created_at: float | None = 10.0) -> ProcessIdentity:
    return ProcessIdentity(321, r"C:\Tools\editor.exe", created_at)


def application(**overrides: object) -> RunningApplication:
    values: dict[str, object] = {
        "identity": awareness_identity(),
        "window_handle": 55,
        "window_title": "Notes",
        "visible": True,
        "foreground": True,
        "bounds": (100, 100, 400, 300),
    }
    values.update(overrides)
    return RunningApplication(**values)  # type: ignore[arg-type]


def target(**overrides: object) -> WindowIdentity:
    values: dict[str, object] = {
        "hwnd": 55,
        "process": ComputerProcessIdentity(321, "editor.exe", 10.0),
        "bounds": Rect(100, 100, 400, 300),
        "title": "Notes",
    }
    values.update(overrides)
    return WindowIdentity(**values)  # type: ignore[arg-type]


def window_info(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "left": 100,
        "top": 100,
        "width": 400,
        "height": 300,
        "title": "Notes",
        "hwnd": 55,
        "process_name": r"C:\Tools\editor.exe",
        "process_id": 321,
        "mapped": True,
        "minimized": False,
    }
    values.update(overrides)
    return values


class TrackingRLock:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.depth = 0

    def __enter__(self):
        self._lock.acquire()
        self.depth += 1
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.depth -= 1
        self._lock.release()


class FakeAwareness:
    def __init__(self) -> None:
        self.mode = ProcessContextMode.VISIBLE_APPS
        self.current = awareness_identity()
        self.calls: list[tuple[ProcessIdentity, bool]] = []

    def validate_identity(self, expected: ProcessIdentity, *, strict: bool = True) -> bool:
        self.calls.append((expected, strict))
        return identities_match(expected, self.current, strict=strict)


@dataclass
class FakeImage:
    size: tuple[int, int]


@dataclass
class FakeFrame:
    image: FakeImage
    left: int
    top: int
    title: str
    hwnd: int
    scope: str
    process_name: str
    process_id: int


class FakeScreenReader:
    automatic_capture_supported = True

    def __init__(self) -> None:
        self._capture_lock = TrackingRLock()
        self.info = window_info()
        self.resolved_info: dict[str, object] | None = self.info
        self.foreground_info: dict[str, object] | None = self.info
        self.last_capture_metadata: object | None = object()
        self.last_word_positions: list[dict] = [{"text": "stale"}]
        self.calls: list[object] = []
        self.refresh = True
        self.after_capture = lambda: None
        self.frame_overrides: dict[str, object] = {}

    def _assert_locked(self) -> None:
        if self._capture_lock.depth <= 0:
            raise AssertionError("screen state read outside capture lock")

    def preserve_external_target(self) -> dict[str, object] | None:
        self._assert_locked()
        self.calls.append("preserve")
        return None if self.foreground_info is None else dict(self.foreground_info)

    def _resolve_capture_target(self, supplied: dict) -> dict[str, object] | None:
        self._assert_locked()
        self.calls.append(("resolve", supplied["hwnd"]))
        return None if self.resolved_info is None else dict(self.resolved_info)

    def _foreground_info(self) -> dict[str, object] | None:
        self._assert_locked()
        self.calls.append("foreground")
        return None if self.foreground_info is None else dict(self.foreground_info)

    def capture_text(
        self,
        max_chars: int = 3000,
        focused_only: bool = True,
        *,
        force_refresh: bool = False,
    ) -> str:
        self._assert_locked()
        self.calls.append(("capture", max_chars, focused_only, force_refresh))
        if self.refresh:
            values: dict[str, object] = {
                "image": FakeImage((400, 300)),
                "left": 100,
                "top": 100,
                "title": "password=secret",
                "hwnd": 55,
                "scope": "focused_window",
                "process_name": "editor.exe",
                "process_id": 321,
            }
            values.update(self.frame_overrides)
            self.last_capture_metadata = FakeFrame(**values)  # type: ignore[arg-type]
            self.last_word_positions = [
                {
                    "text": "Save password=secret",
                    "screen_x": 120,
                    "screen_y": 130,
                    "w": 80,
                    "h": 20,
                    "conf": 92,
                },
                {
                    "text": "outside",
                    "screen_x": 700,
                    "screen_y": 700,
                    "w": 10,
                    "h": 10,
                    "conf": 100,
                },
            ]
        self.after_capture()
        return "raw OCR must not be retained"


class FakePyAutoGUI:
    def __init__(self, *, failsafe: bool = True) -> None:
        self.FAILSAFE = failsafe
        self.events: list[tuple[object, ...]] = []

    def moveTo(self, *args, **kwargs) -> None:
        self.events.append(("moveTo", args, kwargs))

    def click(self, *args, **kwargs) -> None:
        self.events.append(("click", args, kwargs))

    def doubleClick(self, *args, **kwargs) -> None:
        self.events.append(("doubleClick", args, kwargs))

    def scroll(self, *args, **kwargs) -> None:
        self.events.append(("scroll", args, kwargs))

    def press(self, *args, **kwargs) -> None:
        self.events.append(("press", args, kwargs))

    def hotkey(self, *args, **kwargs) -> None:
        self.events.append(("hotkey", args, kwargs))

    def size(self):
        return (1920, 1080)

    def position(self):
        return (5, 6)


class TestConversionsAndOCR(unittest.TestCase):
    def test_process_and_application_conversion_are_minimal(self) -> None:
        converted = process_identity_to_computer_use(awareness_identity())
        self.assertEqual(converted, ComputerProcessIdentity(321, "editor.exe", 10.0))

        window = running_application_to_window_identity(application())
        self.assertEqual(window, target())
        self.assertIsNone(
            running_application_to_window_identity(application(window_handle=None))
        )
        self.assertIsNone(running_application_to_window_identity(application(bounds=None)))

    def test_ocr_words_are_redacted_scaled_and_bounded(self) -> None:
        inside = ocr_word_to_raw_control(
            {
                "text": "password=secret",
                "screen_x": 110,
                "screen_y": 120,
                "w": 20,
                "h": 10,
                "conf": 75,
            },
            target_bounds=Rect(100, 100, 400, 300),
            screen_bounds=Rect(0, 0, 1920, 1080),
        )
        self.assertIsNotNone(inside)
        assert inside is not None
        self.assertEqual(inside.confidence, 0.75)
        self.assertNotIn("secret", inside.label)
        self.assertIsNone(
            ocr_word_to_raw_control(
                {
                    "text": "outside",
                    "screen_x": 10,
                    "screen_y": 10,
                    "w": 20,
                    "h": 10,
                    "conf": 1,
                },
                target_bounds=Rect(100, 100, 400, 300),
            )
        )
        self.assertIsNone(
            ocr_word_to_raw_control(
                {"text": "bad", "screen_x": True, "screen_y": 1, "w": 2, "h": 2}
            )
        )


class TestLockedTargetValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.awareness = FakeAwareness()
        self.screen = FakeScreenReader()
        self.validator = LockedTargetValidator(
            self.awareness,
            self.screen,
            platform_name="win32",
            environment={},
        )

    def test_strict_process_hwnd_focus_and_bounds_all_match(self) -> None:
        result = self.validator(target(), True)
        self.assertEqual(result, LiveTargetState(target(), True, True, True))
        self.assertGreaterEqual(len(self.awareness.calls), 2)
        self.assertTrue(all(strict for _identity, strict in self.awareness.calls))

    def test_pid_reuse_bounds_and_foreground_changes_fail_closed(self) -> None:
        self.awareness.current = awareness_identity(created_at=11.0)
        self.assertFalse(self.validator(target(), True).authorized)

        self.awareness.current = awareness_identity()
        self.screen.resolved_info = window_info(left=101)
        moved = self.validator(target(), True)
        self.assertTrue(moved.is_window)
        self.assertFalse(moved.authorized)

        self.screen.resolved_info = window_info()
        self.screen.foreground_info = window_info(hwnd=77)
        background = self.validator(target(), True)
        self.assertFalse(background.foreground)
        self.assertFalse(background.authorized)

    def test_off_and_wayland_are_unavailable_without_process_calls(self) -> None:
        self.awareness.mode = ProcessContextMode.OFF
        self.assertFalse(self.validator(target(), True).authorized)
        self.assertEqual(self.awareness.calls, [])

        wayland = LockedTargetValidator(
            FakeAwareness(),
            FakeScreenReader(),
            platform_name="linux",
            environment={"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
        )
        self.assertEqual(wayland.status, "unavailable_wayland")


class TestLockedCapture(unittest.TestCase):
    def setUp(self) -> None:
        self.awareness = FakeAwareness()
        self.screen = FakeScreenReader()
        self.source = LockedTargetScreenSource(
            self.screen,
            self.awareness,
            screen_bounds=lambda: Rect(0, 0, 1920, 1080),
            cursor_position=lambda: (9, 10),
            monotonic=lambda: 123.0,
            platform_name="win32",
            environment={},
        )

    def test_capture_is_fresh_locked_exact_and_bounded(self) -> None:
        result = self.source.capture(target())

        self.assertEqual(result.target, replace(target(), title="password= [REDACTED]"))
        self.assertTrue(result.foreground)
        self.assertTrue(result.process_alive)
        self.assertEqual(result.cursor, (9, 10))
        self.assertEqual(result.captured_at, 123.0)
        self.assertEqual(len(result.ocr_controls), 1)
        self.assertNotIn("secret", result.ocr_controls[0].label)
        self.assertEqual(self.screen._capture_lock.depth, 0)
        self.assertIn(("capture", 3000, True, True), self.screen.calls)
        self.assertGreaterEqual(self.screen.calls.count("preserve"), 2)

    def test_pre_capture_identity_mismatch_never_captures(self) -> None:
        self.screen.foreground_info = window_info(hwnd=99)
        result = self.source.capture(target())
        self.assertIsNone(result.target)
        self.assertFalse(result.process_alive)
        self.assertFalse(any(call[0] == "capture" for call in self.screen.calls if isinstance(call, tuple)))

    def test_post_capture_process_or_window_change_discards_words(self) -> None:
        def reuse_pid() -> None:
            self.awareness.current = awareness_identity(created_at=11.0)

        self.screen.after_capture = reuse_pid
        self.assertIsNone(self.source.capture(target()).target)

        self.awareness.current = awareness_identity()
        self.screen.after_capture = lambda: setattr(
            self.screen,
            "foreground_info",
            window_info(left=150),
        )
        self.assertIsNone(self.source.capture(target()).target)

    def test_frame_pid_name_hwnd_bounds_and_scope_mismatches_are_rejected(self) -> None:
        cases = (
            {"process_id": 999},
            {"process_name": "other.exe"},
            {"hwnd": 99},
            {"left": 101},
            {"image": FakeImage((399, 300))},
            {"scope": "active_monitor"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.screen.frame_overrides = changes
                self.assertIsNone(self.source.capture(target()).target)

    def test_stale_screen_reader_publication_is_rejected(self) -> None:
        self.screen.refresh = False
        result = self.source.capture(target())
        self.assertIsNone(result.target)
        self.assertEqual(result.ocr_controls, ())

    def test_wayland_and_unsupported_platforms_do_not_capture(self) -> None:
        for platform_name, environment, status in (
            (
                "linux",
                {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"},
                "unavailable_wayland",
            ),
            ("darwin", {}, "unavailable_platform"),
        ):
            with self.subTest(platform_name=platform_name):
                screen = FakeScreenReader()
                source = LockedTargetScreenSource(
                    screen,
                    FakeAwareness(),
                    screen_bounds=lambda: Rect(0, 0, 1920, 1080),
                    cursor_position=lambda: (0, 0),
                    platform_name=platform_name,
                    environment=environment,
                )
                self.assertEqual(source.status, status)
                with self.assertRaises(ComputerUseRuntimeUnavailable):
                    source.capture(target())
                self.assertEqual(screen.calls, [])


class TestInputAndFocus(unittest.TestCase):
    def test_pyautogui_is_lazy_preserves_failsafe_and_has_no_typing_path(self) -> None:
        module = FakePyAutoGUI()
        imports: list[str] = []

        def importer(name: str) -> object:
            imports.append(name)
            return module

        adapter = LazyPyAutoGUIInput(
            importer=importer,
            platform_name="win32",
            environment={},
        )
        self.assertEqual(imports, [])
        self.assertFalse(adapter.loaded)
        self.assertFalse(hasattr(adapter, "write"))
        self.assertTrue(adapter.move_pointer(1, 2))
        self.assertTrue(adapter.click(3, 4))
        self.assertTrue(adapter.double_click(5, 6))
        self.assertTrue(adapter.scroll(-2, None, None))
        self.assertTrue(adapter.keypress("tab"))
        self.assertTrue(adapter.hotkey(("ctrl", "a")))
        self.assertEqual(imports, ["pyautogui"])
        self.assertTrue(module.FAILSAFE)
        self.assertEqual([item[0] for item in module.events], [
            "moveTo", "click", "doubleClick", "scroll", "press", "hotkey"
        ])

        module.FAILSAFE = False
        self.assertFalse(adapter.click(1, 1))
        self.assertFalse(module.FAILSAFE)
        self.assertEqual(len(module.events), 6)

    def test_disabled_failsafe_and_wayland_never_invoke_input(self) -> None:
        unsafe = FakePyAutoGUI(failsafe=False)
        adapter = LazyPyAutoGUIInput(
            importer=lambda _name: unsafe,
            platform_name="win32",
            environment={},
        )
        self.assertFalse(adapter.click(1, 2))
        self.assertEqual(unsafe.events, [])

        imports: list[str] = []
        wayland = LazyPyAutoGUIInput(
            importer=lambda name: imports.append(name),
            platform_name="linux",
            environment={"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"},
        )
        self.assertFalse(wayland.click(1, 2))
        self.assertEqual(imports, [])

    def test_focus_uses_only_exact_hwnd_and_revalidates_afterward(self) -> None:
        expected = target()
        states = [
            LiveTargetState(expected, True, False, True),
            LiveTargetState(expected, True, True, True),
        ]
        validation: list[tuple[int, bool]] = []

        class Validator:
            def validate(self, value: WindowIdentity, require_foreground: bool = True):
                validation.append((value.hwnd, require_foreground))
                return states.pop(0)

        focused: list[int] = []
        focus = ExactWindowFocus(
            Validator(),  # type: ignore[arg-type]
            native_focus=lambda hwnd: focused.append(hwnd) is None,
            platform_name="win32",
            environment={},
        )
        self.assertTrue(focus(expected))
        self.assertEqual(focused, [55])
        self.assertEqual(validation, [(55, False), (55, True)])

    def test_executor_dependencies_keep_guarded_unicode_typing_injected(self) -> None:
        guarded_calls: list[str] = []

        def guarded(text: str, _target: WindowIdentity, _event: threading.Event) -> bool:
            guarded_calls.append(text)
            return True

        module = FakePyAutoGUI()
        inputs = LazyPyAutoGUIInput(
            importer=lambda _name: module,
            platform_name="win32",
            environment={},
        )
        deps = build_executor_dependencies(
            FakeAwareness(),
            FakeScreenReader(),
            guarded_type=guarded,
            input_adapter=inputs,
            native_focus=lambda _hwnd: True,
            platform_name="win32",
            environment={},
        )
        self.assertIs(deps.guarded_type, guarded)
        self.assertTrue(deps.guarded_type("สวัสดี", target(), threading.Event()))
        self.assertEqual(guarded_calls, ["สวัสดี"])
        self.assertEqual(module.events, [])

    def test_platform_status_is_honest(self) -> None:
        self.assertEqual(runtime_platform_status(platform_name="win32", environment={}), "available_windows")
        self.assertEqual(
            runtime_platform_status(platform_name="linux", environment={"DISPLAY": ":0"}),
            "available_xorg",
        )
        self.assertEqual(
            runtime_platform_status(platform_name="linux", environment={}),
            "unavailable_x11_display",
        )


if __name__ == "__main__":
    unittest.main()
