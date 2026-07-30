from __future__ import annotations

import importlib
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agetha.platform.linux_session import detect_linux_desktop
from agetha.platform import screen_reader
from agetha.ui import w95_window


class _FakeWindow:
    def __init__(self) -> None:
        self.current_state = "normal"
        self.override_calls: list[bool] = []
        self.iconify_calls = 0
        self.deiconify_calls = 0
        self.lift_calls = 0

    def state(self):
        return self.current_state

    def overrideredirect(self, value):
        self.override_calls.append(bool(value))

    def iconify(self):
        self.iconify_calls += 1
        self.current_state = "iconic"

    def deiconify(self):
        self.deiconify_calls += 1
        self.current_state = "normal"

    def withdraw(self):
        self.current_state = "withdrawn"

    def transient(self, _parent):
        pass

    def attributes(self, *_args):
        pass

    def update_idletasks(self):
        pass

    def lift(self):
        self.lift_calls += 1


class TestOptionalGraphicalImport(unittest.TestCase):
    def _assert_failure(self, exc: Exception) -> None:
        secret = "/private/runtime/display-authority-secret"
        with patch.object(screen_reader.logger, "warning") as warning:
            module, available = screen_reader._load_optional_pyautogui(
                lambda _name: (_ for _ in ()).throw(exc),
            )
        self.assertIsNone(module)
        self.assertFalse(available)
        rendered = " ".join(str(value) for call in warning.call_args_list for value in call.args)
        self.assertIn(type(exc).__name__, rendered)
        self.assertNotIn(secret, rendered)

    def test_importerror_is_optional(self):
        self._assert_failure(ImportError("missing"))

    def test_display_connection_error_is_optional(self):
        DisplayConnectionError = type("DisplayConnectionError", (RuntimeError,), {})
        self._assert_failure(DisplayConnectionError("secret authority data"))

    def test_mouseinfo_display_keyerror_is_optional(self):
        self._assert_failure(KeyError("DISPLAY"))

    def test_xauth_error_is_optional(self):
        XauthError = type("XauthError", (RuntimeError,), {})
        self._assert_failure(XauthError("invalid authority"))

    def test_success_returns_module(self):
        expected = object()
        module, available = screen_reader._load_optional_pyautogui(
            lambda _name: expected,
        )
        self.assertIs(module, expected)
        self.assertTrue(available)

    def test_application_modules_remain_importable(self):
        for name in (
            "agetha.platform.screen_reader",
            "agetha.platform.screen_monitoring",
            "agetha.ui.w95_window",
            "agetha.ui.dashboard",
        ):
            with self.subTest(name=name):
                self.assertIsNotNone(importlib.import_module(name))


class TestLinuxCapabilities(unittest.TestCase):
    @staticmethod
    def _which(available: set[str]):
        return lambda command: f"/usr/bin/{command}" if command in available else None

    def test_no_display_is_unavailable(self):
        caps = detect_linux_desktop(
            env={}, platform_name="linux", which=self._which(set()), mss_ok=True,
        )
        self.assertEqual(caps.session_type, "unknown")
        self.assertFalse(caps.automatic_ocr_supported)
        self.assertFalse(caps.explicit_capture_supported)

    def test_wayland_with_xwayland_does_not_enable_automatic_capture(self):
        caps = detect_linux_desktop(
            env={
                "XDG_SESSION_TYPE": "wayland",
                "DISPLAY": ":0",
                "WAYLAND_DISPLAY": "wayland-0",
                "XDG_CURRENT_DESKTOP": "GNOME",
            },
            platform_name="linux",
            which=self._which({"gnome-screenshot"}),
            pyautogui_ok=True,
            mss_ok=True,
        )
        self.assertTrue(caps.x11_bridge)
        self.assertEqual(caps.selected_screenshot_backend, "unavailable")
        self.assertFalse(caps.automatic_ocr_supported)
        self.assertFalse(caps.explicit_capture_supported)

    def test_wayland_explicit_grim_never_enables_automatic_ocr(self):
        caps = detect_linux_desktop(
            env={"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"},
            platform_name="linux", which=self._which({"grim"}), mss_ok=False,
        )
        self.assertEqual(caps.selected_screenshot_backend, "wayland-grim")
        self.assertFalse(caps.automatic_ocr_supported)
        self.assertTrue(caps.explicit_capture_supported)

    def test_x11_uses_healthy_mss(self):
        caps = detect_linux_desktop(
            env={"XDG_SESSION_TYPE": "x11", "DISPLAY": ":99"},
            platform_name="linux", which=self._which(set()), mss_ok=True,
        )
        self.assertEqual(caps.selected_screenshot_backend, "x11-mss")
        self.assertTrue(caps.automatic_ocr_supported)
        self.assertTrue(caps.explicit_capture_supported)

    def test_non_linux_does_not_probe_desktop_capture(self):
        caps = detect_linux_desktop(
            env={"DISPLAY": ":0"}, platform_name="win32",
            which=self._which({"scrot"}), mss_ok=True,
        )
        self.assertEqual(caps.session_type, "unknown")
        self.assertFalse(caps.automatic_ocr_supported)


class TestManagedWindowPolicy(unittest.TestCase):
    def test_linux_never_enables_override_redirect(self):
        win = _FakeWindow()
        with patch.object(w95_window, "IS_WINDOWS", False):
            w95_window.apply_borderless_win95(win, object(), topmost=True)
            w95_window.refresh_borderless(win)
        self.assertEqual(win.override_calls, [])

    def test_windows_keeps_borderless_behavior(self):
        win = _FakeWindow()
        with patch.object(w95_window, "IS_WINDOWS", True), patch.object(
            w95_window, "strip_native_caption",
        ):
            w95_window.apply_borderless_win95(win, object(), topmost=False)
            w95_window.refresh_borderless(win)
        self.assertEqual(win.override_calls, [True, True])

    def test_managed_minimize_restore_is_idempotent(self):
        win = _FakeWindow()
        self.assertTrue(w95_window.minimize_managed(win))
        self.assertTrue(w95_window.minimize_managed(win))
        self.assertEqual(win.iconify_calls, 1)
        self.assertTrue(w95_window.restore_managed(win))
        self.assertTrue(w95_window.restore_managed(win))
        self.assertEqual(win.deiconify_calls, 1)
        self.assertEqual(win.override_calls, [])


class TestCaptureValidation(unittest.TestCase):
    DISPLAY = {"left": 0, "top": 0, "width": 1366, "height": 768}

    def test_rejects_zero_and_negative_dimensions(self):
        for width, height in ((0, 10), (10, 0), (-1, 10), (10, -1)):
            with self.subTest(width=width, height=height):
                rect, status = screen_reader._clip_capture_rect(
                    {"left": 0, "top": 0, "width": width, "height": height},
                    self.DISPLAY,
                )
                self.assertIsNone(rect)
                self.assertEqual(status, "skipped_invalid_geometry")

    def test_clips_partially_offscreen_target(self):
        rect, status = screen_reader._clip_capture_rect(
            {"left": -20, "top": 700, "width": 100, "height": 100},
            self.DISPLAY,
        )
        self.assertEqual(status, "")
        self.assertEqual(rect, {"left": 0, "top": 700, "width": 80, "height": 68})

    def test_rejects_fully_offscreen_target(self):
        rect, status = screen_reader._clip_capture_rect(
            {"left": 2000, "top": 0, "width": 100, "height": 100},
            self.DISPLAY,
        )
        self.assertIsNone(rect)
        self.assertEqual(status, "skipped_fully_offscreen")

    def test_skips_minimized_and_unmapped_targets(self):
        base = {"left": 0, "top": 0, "width": 100, "height": 100}
        for flag, status in (
            ({"minimized": True}, "skipped_minimized"),
            ({"mapped": False}, "skipped_unmapped"),
        ):
            with self.subTest(status=status):
                target, actual = screen_reader._validate_capture_target({**base, **flag})
                self.assertIsNone(target)
                self.assertEqual(actual, status)

    def test_failed_backend_is_not_retried_continuously(self):
        reader = screen_reader.ScreenReader.__new__(screen_reader.ScreenReader)
        backend = MagicMock(return_value=None)
        reader._backend_fn = None
        reader._backend_name = "lazy"
        reader._backend_candidates = [("broken", backend)]
        reader._disabled_backends = set()
        reader._capture_warning_emitted = False
        reader.last_monitor_status = "initializing"
        self.assertIsNone(reader._capture_with_backend())
        self.assertIsNone(reader._capture_with_backend())
        backend.assert_called_once()

    def test_skipped_capture_clears_stale_ocr_state(self):
        reader = screen_reader.ScreenReader.__new__(screen_reader.ScreenReader)
        reader._state_lock = __import__("threading").RLock()
        reader.last_active_window_title = "Old window"
        reader.last_capture_metadata = object()
        reader.last_word_positions = [{"text": "old"}]
        reader.last_angry_keywords = ["old"]
        reader.last_pattern_matches = [object()]
        reader.last_new_pattern_events = [object()]
        reader._clear_stale_capture_state("skipped_invalid_geometry")
        self.assertEqual(reader.last_active_window_title, "")
        self.assertIsNone(reader.last_capture_metadata)
        self.assertEqual(reader.last_word_positions, [])
        self.assertEqual(reader.last_monitor_status, "skipped_invalid_geometry")

    def test_wayland_automatic_backend_list_is_empty(self):
        reader = screen_reader.ScreenReader.__new__(screen_reader.ScreenReader)
        reader._system = "Linux"
        with patch.dict(
            os.environ,
            {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"},
            clear=True,
        ):
            self.assertEqual(reader._ordered_backends(automatic=True), [])
            names = [name for name, _fn in reader._ordered_backends(automatic=False)]
        self.assertNotIn("gnome-screenshot", names)

    def test_external_command_timeout_is_controlled(self):
        result = screen_reader._grab_temp_png(
            lambda _path: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(["capture"], 1),
            ),
        )
        self.assertIsNone(result)

    def test_external_command_nonzero_exit_is_controlled(self):
        failed = subprocess.CompletedProcess(["scrot"], 1, b"", b"failure")
        with patch.object(screen_reader, "_cmd_exists", return_value=True), patch.object(
            screen_reader.subprocess, "run", return_value=failed,
        ):
            self.assertIsNone(screen_reader._grab_scrot())

    def test_external_command_invalid_image_is_controlled(self):
        def _write_invalid(path: str) -> bool:
            Path(path).write_bytes(b"not a png")
            return True

        self.assertIsNone(screen_reader._grab_temp_png(_write_invalid))

    def test_invalid_bbox_never_reaches_imagegrab(self):
        with patch.object(screen_reader, "IMAGEGRAB_OK", True), patch.object(
            screen_reader.ImageGrab, "grab",
        ) as grab:
            self.assertIsNone(screen_reader._grab_imagegrab((10, 10, 10, 20)))
        grab.assert_not_called()


@unittest.skipUnless(
    sys.platform.startswith("linux") and bool(os.environ.get("DISPLAY")),
    "Linux Tk lifecycle smoke requires DISPLAY",
)
class TestLinuxTkLifecycle(unittest.TestCase):
    def test_managed_windows_remain_interactive_after_restore(self):
        import tkinter as tk
        from agetha.app_config import get_settings
        from agetha.ui.dashboard import open_dashboard

        root = tk.Tk()
        root.title("Agetha Linux smoke")
        entry = tk.Entry(root)
        entry.pack()
        enabled = tk.BooleanVar(value=False)
        toggle = tk.Checkbutton(root, variable=enabled)
        toggle.pack()
        existing = set(root.winfo_children())
        open_dashboard(root, get_settings())
        dashboards = [
            child for child in root.winfo_children()
            if child not in existing and isinstance(child, tk.Toplevel)
        ]
        self.assertEqual(len(dashboards), 1)
        dashboard = dashboards[0]
        entry.bind("<Button-1>", lambda _event: entry.focus_set(), add="+")
        root.update()
        entry.event_generate("<Button-1>")
        toggle.invoke()
        root.update()
        self.assertEqual(entry.focus_get(), entry)
        self.assertTrue(enabled.get())
        self.assertFalse(bool(root.overrideredirect()))
        self.assertFalse(bool(dashboard.overrideredirect()))
        self.assertTrue(w95_window.minimize_managed(root))
        root.update()
        self.assertEqual(root.state(), "iconic")
        self.assertTrue(w95_window.restore_managed(root))
        root.update()
        self.assertEqual(root.state(), "normal")
        self.assertTrue(root.winfo_ismapped())
        dashboard.destroy()
        root.destroy()


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux X11 screenshot smoke")
class TestLinuxX11Screenshot(unittest.TestCase):
    def test_mss_returns_positive_virtual_x11_frame(self):
        if not os.environ.get("DISPLAY"):
            self.skipTest("DISPLAY is unavailable")
        try:
            import mss  # noqa: F401
        except ImportError:
            self.skipTest("optional mss package is unavailable")
        frame = screen_reader._grab_mss_frame(scope="virtual_desktop")
        self.assertIsNotNone(frame)
        self.assertGreater(frame.image.width, 0)
        self.assertGreater(frame.image.height, 0)
