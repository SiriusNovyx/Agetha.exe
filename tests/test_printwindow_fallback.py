from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from PIL import Image

from agetha.platform.screen_monitoring import CapturedFrame
from agetha.platform.screen_reader import ScreenReader


def _target(**overrides):
    value = {
        "left": 20,
        "top": 30,
        "width": 320,
        "height": 180,
        "title": "Diagnostic Notepad",
        "hwnd": 123,
        "process_name": "notepad.exe",
        "process_id": 456,
        "minimized": False,
        "mapped": True,
    }
    value.update(overrides)
    return value


def _frame(color: str, *, scope: str = "focused_window") -> CapturedFrame:
    return CapturedFrame(
        image=Image.new("RGB", (320, 180), color),
        left=20,
        top=30,
        title="Diagnostic Notepad",
        hwnd=123,
        scope=scope,
        process_name="notepad.exe",
        process_id=456,
    )


class PrintWindowFallbackTests(unittest.TestCase):
    def test_uniform_frame_detection_does_not_treat_visible_detail_as_blank(self) -> None:
        from agetha.platform.screen_reader import _image_looks_uniform

        blank = Image.new("RGB", (320, 180), "black")
        detailed = blank.copy()
        for x in range(40, 280):
            for y in range(80, 100):
                detailed.putpixel((x, y), (255, 255, 255))

        self.assertTrue(_image_looks_uniform(blank))
        self.assertFalse(_image_looks_uniform(detailed))

    def test_printwindow_is_used_only_for_uniform_mss_focused_frame(self) -> None:
        from agetha.platform.screen_reader import _grab_focused_windows_frame

        fallback = _frame("white")
        with patch(
            "agetha.platform.screen_reader._grab_mss_frame",
            return_value=_frame("black"),
        ), patch(
            "agetha.platform.screen_reader._grab_printwindow_frame",
            return_value=fallback,
        ) as printwindow:
            result = _grab_focused_windows_frame(
                _target(), allow_printwindow=True,
            )

        self.assertIs(result, fallback)
        printwindow.assert_called_once()

    def test_nonuniform_mss_frame_never_invokes_printwindow(self) -> None:
        from agetha.platform.screen_reader import _grab_focused_windows_frame

        visible = _frame("black")
        visible.image.paste("white", (30, 30, 100, 60))
        with patch(
            "agetha.platform.screen_reader._grab_mss_frame",
            return_value=visible,
        ), patch(
            "agetha.platform.screen_reader._grab_printwindow_frame",
        ) as printwindow:
            result = _grab_focused_windows_frame(
                _target(), allow_printwindow=True,
            )

        self.assertIs(result, visible)
        printwindow.assert_not_called()

    def test_disabled_fallback_returns_original_uniform_mss_frame(self) -> None:
        from agetha.platform.screen_reader import _grab_focused_windows_frame

        blank = _frame("black")
        with patch(
            "agetha.platform.screen_reader._grab_mss_frame", return_value=blank,
        ), patch(
            "agetha.platform.screen_reader._grab_printwindow_frame",
        ) as printwindow:
            result = _grab_focused_windows_frame(
                _target(), allow_printwindow=False,
            )

        self.assertIs(result, blank)
        printwindow.assert_not_called()

    def test_minimized_and_excluded_targets_are_rejected_before_fallback(self) -> None:
        for info, excluded_apps, expected in (
            (_target(minimized=True), frozenset(), "skipped_minimized"),
            (_target(), frozenset({"notepad.exe"}), "skipped_excluded_window"),
        ):
            with self.subTest(expected=expected):
                reader = ScreenReader.__new__(ScreenReader)
                reader._system = "Windows"
                reader._capture_lock = threading.Lock()
                reader._state_lock = None
                reader._stopped = False
                reader._app_closing = False
                reader._app_minimized = False
                reader._app_mapped = True
                reader._automatic_capture_available = True
                reader._explicit_capture_available = True
                reader._capture_available = True
                reader._foreground_info = lambda: info
                reader._get_own_hwnd = lambda: None
                reader._excluded_apps = excluded_apps
                reader._title_exclusions = ()
                reader._enable_printwindow_fallback = True

                with patch(
                    "agetha.platform.screen_reader._grab_focused_windows_frame",
                ) as capture:
                    result = reader._capture_frame(
                        focused_only=True, automatic=True,
                    )

                self.assertIsNone(result)
                self.assertEqual(reader.last_monitor_status, expected)
                capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
