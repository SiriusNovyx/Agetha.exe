from __future__ import annotations

import inspect
import os
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw

from agetha.app_config import AppSettings
from agetha.platform.ocr_backends.base import OCRLine, OCRResult, OCRWord
from agetha.platform.ocr_backends.tesseract_backend import TesseractOCRBackend
from agetha.platform.screen_monitoring import (
    CapturedFrame,
    PatternEventTracker,
    ScreenChangeDetector,
    compile_title_exclusions,
    preprocess_ocr_image,
    redact_sensitive_text,
)
from agetha.platform import screen_reader as screen_reader_module
from agetha.platform.screen_reader import (
    PatternMatch,
    ScreenReader,
    _get_foreground_window_info_linux,
    _linux_process_name_from_window,
    _scan_patterns,
)


def _image(size=(120, 80), color="white"):
    return Image.new("RGB", size, color)


def _frame(
    image=None, *, left=0, top=0, title="Editor", hwnd=10,
    scope="focused_window", process_name="editor.exe", process_id=None,
):
    return CapturedFrame(
        image or _image(), left, top, title, hwnd, scope, process_name, process_id,
    )


def _tesseract_data(words):
    data = {
        key: [] for key in (
            "text", "conf", "left", "top", "width", "height",
            "page_num", "block_num", "par_num", "line_num", "word_num",
        )
    }
    for index, item in enumerate(words, 1):
        text, conf, left, top, width, height, line_num = item
        values = {
            "text": text, "conf": str(conf), "left": left, "top": top,
            "width": width, "height": height, "page_num": 1,
            "block_num": 1, "par_num": 1, "line_num": line_num,
            "word_num": index,
        }
        for key, value in values.items():
            data[key].append(value)
    return data


def _fake_tesseract(words):
    fake = MagicMock()
    fake.Output.DICT = object()
    fake.image_to_data.return_value = _tesseract_data(words)
    return fake


def _capture_reader() -> ScreenReader:
    reader = ScreenReader.__new__(ScreenReader)
    reader._system = "Windows"
    reader._capture_lock = threading.RLock()
    reader._state_lock = threading.RLock()
    reader._stopped = False
    reader._capture_available = True
    reader._backend_name = "lazy"
    reader._backend_fn = None
    reader._backend_candidates = []
    reader._pending_backend_frame = None
    reader._excluded_apps = ()
    reader._title_exclusions = ((), ())
    reader._get_own_hwnd = lambda: 999
    reader.last_monitor_status = ""
    reader.last_active_window_title = ""
    reader.last_capture_metadata = None
    return reader


def _ocr_reader(result: OCRResult, frame=None) -> ScreenReader:
    reader = _capture_reader()
    reader._available = True
    reader._standard_scan_lock = threading.Lock()
    reader._clock = time.monotonic
    reader._capture_frame = MagicMock(return_value=frame or _frame())
    reader._focused_target_is_current = lambda _frame: True
    reader._selected_psm = lambda _frame: 6
    reader._ocr_max_dimension = 2560
    reader._ocr_preprocessing = "basic"
    reader._effective_ocr_languages = "eng"
    reader._ocr_min_word_confidence = 30.0
    reader._ocr_min_pattern_confidence = 45.0
    reader._change_detector = None
    reader._event_tracker = PatternEventTracker()
    reader._pattern_cooldown_seconds = 60.0
    reader._pattern_confirm_scans = 1
    reader._low_confidence_confirm_scans = 2
    reader._pattern_clear_scans = 2
    reader._standard_ocr_backend = MagicMock()
    reader._standard_ocr_backend.analyze.return_value = result
    reader.last_angry_keywords = []
    reader.last_pattern_matches = []
    reader.last_new_pattern_events = []
    reader.last_word_positions = []
    reader._capture_left = 0
    reader._capture_top = 0
    return reader


class TestCoordinateTransformation(unittest.TestCase):
    def test_01_no_resize_two_times_upscale(self):
        processed = preprocess_ocr_image(_image((100, 50)), max_dimension=200)
        fake = _fake_tesseract([("Error", 90, 40, 20, 30, 10, 1)])
        result = TesseractOCRBackend(fake).analyze(
            processed.image,
            processing_scale_x=processed.scale_x,
            processing_scale_y=processed.scale_y,
        )
        self.assertEqual((processed.scale_x, processed.scale_y), (2.0, 2.0))
        self.assertEqual((result.words[0].x, result.words[0].y), (20, 10))

    def test_02_downscale_then_upscale(self):
        processed = preprocess_ocr_image(_image((4000, 2000)), max_dimension=1000)
        fake = _fake_tesseract([("Error", 90, 500, 250, 100, 50, 1)])
        result = TesseractOCRBackend(fake).analyze(
            processed.image,
            processing_scale_x=processed.scale_x,
            processing_scale_y=processed.scale_y,
        )
        self.assertEqual((processed.scale_x, processed.scale_y), (0.5, 0.5))
        self.assertEqual((result.words[0].x, result.words[0].y), (1000, 500))

    def test_03_nonzero_window_origin(self):
        fake = _fake_tesseract([("Error", 90, 40, 20, 20, 10, 1)])
        word = TesseractOCRBackend(fake).analyze(
            object(), capture_left=700, capture_top=300, scale=2,
        ).words[0]
        self.assertEqual((word.x, word.y), (720, 310))

    def test_04_negative_desktop_origin(self):
        fake = _fake_tesseract([("Error", 90, 40, 20, 20, 10, 1)])
        word = TesseractOCRBackend(fake).analyze(
            object(), capture_left=-1920, capture_top=-200, scale=2,
        ).words[0]
        self.assertEqual((word.x, word.y), (-1900, -190))

    def test_05_bounding_box_sizes_remain_correct(self):
        fake = _fake_tesseract([("Error", 90, 20, 10, 80, 24, 1)])
        word = TesseractOCRBackend(fake).analyze(object(), scale=2).words[0]
        self.assertEqual((word.width, word.height), (40, 12))


class TestCaptureBehavior(unittest.TestCase):
    def test_05a_linux_xdotool_metadata_includes_process_name(self):
        def fake_run(command, **_kwargs):
            lookup = {
                ("xdotool", "getactivewindow"): "42\n",
                ("xdotool", "getwindowname", "42"): "Database.kdbx\n",
                ("xdotool", "getwindowgeometry", "42"): (
                    "Position: 10,20 (screen: 0)\nGeometry: 800x600\n"
                ),
            }
            return MagicMock(returncode=0, stdout=lookup[tuple(command)])

        with (
            patch.object(screen_reader_module, "IS_LINUX", True),
            patch.object(screen_reader_module.subprocess, "run", side_effect=fake_run),
            patch.object(
                screen_reader_module,
                "_linux_window_process",
                return_value=("keepassxc", 321),
            ),
        ):
            info = _get_foreground_window_info_linux()
        self.assertEqual(info["process_name"], "keepassxc")
        self.assertEqual(info["hwnd"], 42)

    def test_05b_linux_wmctrl_metadata_includes_process_name(self):
        def fake_run(command, **_kwargs):
            if command[:2] == ["xdotool", "getactivewindow"]:
                raise FileNotFoundError
            if command[:3] == ["xprop", "-root", "_NET_ACTIVE_WINDOW"]:
                return MagicMock(
                    returncode=0,
                    stdout="_NET_ACTIVE_WINDOW(WINDOW): window id # 0x2a\n",
                )
            return MagicMock(
                returncode=0,
                stdout="0x0000002a 0 10 20 800 600 host Database.kdbx\n",
            )

        with (
            patch.object(screen_reader_module, "IS_LINUX", True),
            patch.object(screen_reader_module.subprocess, "run", side_effect=fake_run),
            patch.object(
                screen_reader_module,
                "_linux_window_process",
                return_value=("keepassxc", 321),
            ),
        ):
            info = _get_foreground_window_info_linux()
        self.assertEqual(info["process_name"], "keepassxc")
        self.assertEqual(info["hwnd"], 42)

    def test_05c_linux_process_resolver_falls_back_to_xprop_pid(self):
        missing = MagicMock(returncode=1, stdout="")
        xprop = MagicMock(returncode=0, stdout="_NET_WM_PID(CARDINAL) = 321\n")
        with (
            patch.object(screen_reader_module, "IS_LINUX", True),
            patch.object(
                screen_reader_module.subprocess,
                "run",
                side_effect=[missing, xprop],
            ),
            patch.object(screen_reader_module.os, "readlink", return_value="/usr/bin/keepassxc"),
        ):
            name = _linux_process_name_from_window(42)
        self.assertEqual(name, "keepassxc")

    @unittest.skipUnless(os.name == "nt", "Windows metadata path")
    def test_05d_windows_metadata_includes_process_id(self):
        user32 = MagicMock()
        kernel32 = MagicMock()
        native = MagicMock(user32=user32, kernel32=kernel32)
        user32.GetWindowTextLengthW.return_value = 0

        def set_rect(_hwnd, rect_pointer):
            rect_pointer._obj.left = 10
            rect_pointer._obj.top = 20
            rect_pointer._obj.right = 810
            rect_pointer._obj.bottom = 620
            return True

        def set_pid(_hwnd, pid_pointer):
            pid_pointer._obj.value = 321
            return 1

        user32.GetWindowRect.side_effect = set_rect
        user32.GetWindowThreadProcessId.side_effect = set_pid
        kernel32.OpenProcess.return_value = 0
        with (
            patch.object(screen_reader_module, "IS_WINDOWS", True),
            patch.object(screen_reader_module.ctypes, "windll", native, create=True),
        ):
            info = screen_reader_module._get_window_info(77)
        self.assertEqual(info["process_id"], 321)
        self.assertEqual(info["hwnd"], 77)

    def test_06_focused_capture_success(self):
        reader = _capture_reader()
        reader._foreground_info = lambda: {
            "left": 11, "top": 22, "width": 100, "height": 60,
            "title": "Code", "hwnd": 7, "process_name": "code.exe",
        }
        with patch.object(screen_reader_module, "_grab_mss_frame", return_value=_frame(left=11, top=22, hwnd=7)):
            captured = reader._capture_frame()
        self.assertEqual((captured.left, captured.top, captured.hwnd), (11, 22, 7))

    def test_07_focused_mss_failure_followed_by_fallback(self):
        reader = _capture_reader()
        reader._foreground_info = lambda: {
            "left": 80, "top": 90, "width": 100, "height": 60,
            "title": "Code", "hwnd": 7,
        }
        fallback = _frame(left=-500, top=0, hwnd=None, scope="virtual_desktop")
        reader._backend_candidates = [("fake", lambda: fallback)]
        with patch.object(screen_reader_module, "_grab_mss_frame", return_value=None), patch.object(screen_reader_module, "_find_monitor_for_window", return_value=None):
            captured = reader._capture_frame()
        self.assertIs(captured, fallback)

    def test_08_fallback_origin_is_not_stale(self):
        reader = _capture_reader()
        reader._foreground_info = lambda: {
            "left": 800, "top": 900, "width": 100, "height": 60,
            "title": "Code", "hwnd": 7,
        }
        reader._backend_candidates = [("fake", lambda: _frame(left=0, top=0, hwnd=None, scope="primary_monitor"))]
        with patch.object(screen_reader_module, "_grab_mss_frame", return_value=None), patch.object(screen_reader_module, "_find_monitor_for_window", return_value=None):
            captured = reader._capture_frame()
        self.assertEqual((captured.left, captured.top), (0, 0))

    def test_09_own_window_focus_skips_cycle(self):
        reader = _capture_reader()
        reader._foreground_info = lambda: {
            "left": 0, "top": 0, "width": 100, "height": 60,
            "title": "Agetha", "hwnd": 999,
        }
        with patch.object(screen_reader_module, "_grab_mss_frame") as grab:
            self.assertIsNone(reader._capture_frame())
        grab.assert_not_called()
        self.assertEqual(reader.last_monitor_status, "skipped_own_window")

    def test_10_own_window_does_not_capture_desktop(self):
        reader = _capture_reader()
        reader._foreground_info = lambda: {
            "left": 0, "top": 0, "width": 100, "height": 60,
            "title": "Agetha", "hwnd": 999,
        }
        backend = MagicMock()
        reader._backend_candidates = [("fake", backend)]
        reader._capture_frame()
        backend.assert_not_called()

    def test_11_virtual_desktop_origin_is_retained(self):
        reader = _capture_reader()
        expected = _frame(left=-1440, top=-120, hwnd=None, scope="virtual_desktop")
        reader._backend_candidates = [("fake", lambda: expected)]
        captured = reader._capture_frame(focused_only=False)
        self.assertEqual((captured.left, captured.top), (-1440, -120))

    def test_12_failed_capture_preserves_previous_state(self):
        reader = _ocr_reader(OCRResult("new", [], "tesseract"))
        reader.last_active_window_title = "Previous"
        reader.last_pattern_matches = ["previous"]
        reader._capture_frame.return_value = None
        self.assertEqual(reader.capture_text(), "")
        self.assertEqual(reader.last_active_window_title, "Previous")
        self.assertEqual(reader.last_pattern_matches, ["previous"])

    def test_12a_full_desktop_poll_skips_excluded_foreground(self):
        reader = _capture_reader()
        reader._excluded_apps = ("passwordmanager.exe",)
        reader._foreground_info = lambda: {
            "left": 0, "top": 0, "width": 100, "height": 60,
            "title": "Vault", "hwnd": 7, "process_name": "passwordmanager.exe",
        }
        backend = MagicMock(return_value=_frame(scope="virtual_desktop"))
        reader._backend_candidates = [("fake", backend)]
        self.assertIsNone(reader._capture_frame(focused_only=False))
        backend.assert_not_called()
        self.assertEqual(reader.last_monitor_status, "skipped_excluded_window")

    def test_12b_full_desktop_poll_skips_own_window(self):
        reader = _capture_reader()
        reader._foreground_info = lambda: {
            "left": 0, "top": 0, "width": 100, "height": 60,
            "title": "Agetha", "hwnd": 999, "process_name": "python.exe",
        }
        backend = MagicMock(return_value=_frame(scope="virtual_desktop"))
        reader._backend_candidates = [("fake", backend)]
        self.assertIsNone(reader._capture_frame(focused_only=False))
        backend.assert_not_called()
        self.assertEqual(reader.last_monitor_status, "skipped_own_window")

    def test_12c_main_thread_handle_cache_is_wired_after_background_init(self):
        source = inspect.getsource(__import__("main").CompanionApp._init_background)
        self.assertIn("self._screen.cache_own_window_handle()", source)

    def test_12d_manual_focused_capture_still_skips_own_window(self):
        reader = _capture_reader()
        reader._foreground_info = lambda: {
            "left": 0, "top": 0, "width": 100, "height": 60,
            "title": "Agetha", "hwnd": 999, "process_name": "python.exe",
        }
        with patch.object(screen_reader_module, "_grab_mss_frame") as grab:
            self.assertIsNone(reader._capture_frame(automatic=False))
        grab.assert_not_called()
        self.assertEqual(reader.last_monitor_status, "skipped_own_window")

    def test_12e_preserved_target_survives_confirmation_focus_change(self):
        reader = _capture_reader()
        reader._foreground_info = lambda: {
            "left": 0, "top": 0, "width": 100, "height": 60,
            "title": "Agetha", "hwnd": 999, "process_name": "python.exe",
        }
        reader.last_capture_metadata = _frame(
            title="Document", hwnd=77, process_name="reader.exe", process_id=321,
        )
        external = {
            "left": 25, "top": 35, "width": 640, "height": 480,
            "title": "Document", "hwnd": 77, "process_name": "reader.exe",
            "process_id": 321,
        }
        captured_frame = _frame(
            left=25, top=35, title="Document", hwnd=77,
            process_name="reader.exe", process_id=321,
        )
        with (
            patch.object(screen_reader_module, "_get_window_info", return_value=external),
            patch.object(screen_reader_module, "_grab_mss_frame", return_value=captured_frame),
        ):
            target = reader.preserve_external_target()
            captured = reader._capture_frame(
                focused_only=True,
                automatic=False,
                capture_target=target,
            )
        self.assertEqual(target["hwnd"], 77)
        self.assertEqual(captured.hwnd, 77)

    def test_12f_preserved_target_failure_does_not_capture_monitor(self):
        reader = _capture_reader()
        target = {
            "left": 25, "top": 35, "width": 640, "height": 480,
            "title": "Document", "hwnd": 77, "process_name": "reader.exe",
            "process_id": 321,
        }
        with (
            patch.object(screen_reader_module, "_get_window_info", return_value=target),
            patch.object(screen_reader_module, "_grab_mss_frame", return_value=None),
        ):
            reader._capture_with_backend = MagicMock()
            captured = reader._capture_frame(
                focused_only=True,
                automatic=False,
                capture_target=target,
            )
        self.assertIsNone(captured)
        reader._capture_with_backend.assert_not_called()
        self.assertEqual(reader.last_monitor_status, "capture_target_failed")

    def test_12g_windows_preserved_target_rejects_reused_hwnd(self):
        reader = _capture_reader()
        target = {
            "left": 25, "top": 35, "width": 640, "height": 480,
            "title": "Document", "hwnd": 77, "process_name": "reader.exe",
            "process_id": 321,
        }
        reused = dict(target, process_name="other.exe", process_id=654)
        with (
            patch.object(
                screen_reader_module,
                "_get_window_info",
                return_value=reused,
            ),
            patch.object(screen_reader_module, "_grab_mss_frame") as grab,
        ):
            captured = reader._capture_frame(
                focused_only=True,
                automatic=False,
                capture_target=target,
            )
        self.assertIsNone(captured)
        grab.assert_not_called()
        self.assertEqual(reader.last_monitor_status, "capture_target_unavailable")

    def test_12h_linux_preserved_target_refreshes_geometry(self):
        reader = _capture_reader()
        reader._system = "Linux"
        target = {
            "left": 25, "top": 35, "width": 640, "height": 480,
            "title": "Document", "hwnd": 77, "process_name": "reader",
            "process_id": 321,
        }
        refreshed = dict(target, left=125, top=135, width=800, height=600)
        captured_frame = _frame(
            left=125, top=135, title="Document", hwnd=77,
            process_name="reader", process_id=321,
        )
        with (
            patch.object(
                screen_reader_module,
                "_get_linux_window_info",
                return_value=refreshed,
            ),
            patch.object(
                screen_reader_module,
                "_grab_mss_frame",
                return_value=captured_frame,
            ) as grab,
        ):
            captured = reader._capture_frame(
                focused_only=True,
                automatic=False,
                capture_target=target,
            )
        self.assertIs(captured, captured_frame)
        self.assertEqual(
            grab.call_args.args[0],
            {"left": 125, "top": 135, "width": 800, "height": 600},
        )

    def test_12i_linux_preserved_target_rejects_reused_xid(self):
        reader = _capture_reader()
        reader._system = "Linux"
        target = {
            "left": 25, "top": 35, "width": 640, "height": 480,
            "title": "Document", "hwnd": 77, "process_name": "reader",
            "process_id": 321,
        }
        reused = dict(target, title="Different window")
        with (
            patch.object(
                screen_reader_module,
                "_get_linux_window_info",
                return_value=reused,
            ),
            patch.object(screen_reader_module, "_grab_mss_frame") as grab,
        ):
            captured = reader._capture_frame(
                focused_only=True,
                automatic=False,
                capture_target=target,
            )
        self.assertIsNone(captured)
        grab.assert_not_called()
        self.assertEqual(reader.last_monitor_status, "capture_target_unavailable")

    def test_12j_linux_preserved_target_rejects_closed_window(self):
        reader = _capture_reader()
        reader._system = "Linux"
        target = {
            "left": 25, "top": 35, "width": 640, "height": 480,
            "title": "Document", "hwnd": 77, "process_name": "reader",
            "process_id": 321,
        }
        with (
            patch.object(
                screen_reader_module,
                "_get_linux_window_info",
                return_value=None,
            ),
            patch.object(screen_reader_module, "_grab_mss_frame") as grab,
        ):
            captured = reader._capture_frame(
                focused_only=True,
                automatic=False,
                capture_target=target,
            )
        self.assertIsNone(captured)
        grab.assert_not_called()


class TestConcurrency(unittest.TestCase):
    def test_13_deep_ocr_cannot_restore_stale_standard_state(self):
        reader = _ocr_reader(OCRResult("", [], "tesseract"))
        reader._deep_backend_name = "unlimited_ocr"
        backend = MagicMock()
        backend.configuration_error.return_value = None
        def analyze(_image, **_kwargs):
            reader.last_active_window_title = "New Standard State"
            return OCRResult("deep", [], "unlimited_ocr")
        backend.analyze.side_effect = analyze
        reader._deep_ocr_backend = backend
        reader.last_active_window_title = "Old"
        reader.capture_deep_text()
        self.assertEqual(reader.last_active_window_title, "New Standard State")

    def test_14_concurrent_standard_scans_are_serialized(self):
        reader = _ocr_reader(OCRResult("ok", [], "tesseract"))
        active = 0
        peak = 0
        guard = threading.Lock()
        def analyze(*_args, **_kwargs):
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with guard:
                active -= 1
            return OCRResult("ok", [], "tesseract")
        reader._standard_ocr_backend.analyze.side_effect = analyze
        threads = [threading.Thread(target=reader.capture_text) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(peak, 1)

    def test_15_shutdown_remains_safe(self):
        reader = ScreenReader.__new__(ScreenReader)
        reader._stopped = False
        reader._deep_ocr_backend = MagicMock()
        reader.stop()
        reader.stop()
        reader._deep_ocr_backend.close.assert_called_once()


class TestChangeDetection(unittest.TestCase):
    def setUp(self):
        self.detector = ScreenChangeDetector(
            threshold=0.025, force_refresh_seconds=20, state_expiry_seconds=300,
        )

    def _record(self, frame, now=0):
        scan, _reason, thumb, _cached = self.detector.should_scan(frame, now)
        self.assertTrue(scan)
        self.detector.record(frame, thumbnail=thumb, text="cached", text_hash="x", now=now)

    def test_16_first_frame_runs_ocr(self):
        self.assertTrue(self.detector.should_scan(_frame(), 0)[0])

    def test_17_identical_second_frame_skips_ocr(self):
        frame = _frame()
        self._record(frame)
        self.assertEqual(self.detector.should_scan(frame, 1)[:2], (False, "unchanged"))

    def test_18_cursor_like_change_below_threshold(self):
        first = _frame(_image((640, 360)))
        self._record(first)
        second_image = _image((640, 360))
        ImageDraw.Draw(second_image).rectangle((5, 5, 7, 20), fill="black")
        self.assertFalse(self.detector.should_scan(_frame(second_image), 1)[0])

    def test_19_significant_region_change_triggers(self):
        first = _frame(_image((640, 360)))
        self._record(first)
        second_image = _image((640, 360))
        ImageDraw.Draw(second_image).rectangle((0, 0, 400, 300), fill="black")
        self.assertTrue(self.detector.should_scan(_frame(second_image), 1)[0])

    def test_20_forced_refresh_after_interval(self):
        frame = _frame()
        self._record(frame)
        self.assertEqual(self.detector.should_scan(frame, 21)[1], "forced_refresh")

    def test_21_switching_hwnd_triggers(self):
        self._record(_frame(hwnd=1))
        self.assertEqual(self.detector.should_scan(_frame(hwnd=2), 1)[1], "new_target")

    def test_22_expired_state_is_cleaned(self):
        self._record(_frame(hwnd=1))
        self.detector.should_scan(_frame(hwnd=2), 301)
        self.assertNotIn(("hwnd", 1, "focused_window"), self.detector.states)


class TestEventDeduplication(unittest.TestCase):
    def setUp(self):
        self.tracker = PatternEventTracker()
        self.key = ("hwnd", 10)

    def update(self, matches, now):
        return self.tracker.update(
            matches, window_key=self.key, now=now, cooldown_seconds=60,
            confirm_scans=1, low_confidence_confirm_scans=2, clear_scans=2,
            minimum_confidence=65,
        )

    def test_23_new_event_triggers_once(self):
        match = PatternMatch("py", "angry", "Error", "ValueError", confidence=90)
        self.assertEqual(self.update([match], 0), [match])

    def test_24_same_event_during_cooldown_does_not_retrigger(self):
        match = PatternMatch("py", "angry", "Error", "ValueError", confidence=90)
        self.update([match], 0)
        self.assertEqual(self.update([match], 10), [])

    def test_25_changed_snippet_is_new_event(self):
        first = PatternMatch("py", "angry", "Error", "ValueError: one", confidence=90)
        second = PatternMatch("py", "angry", "Error", "ValueError: two", confidence=90)
        self.update([first], 0)
        self.assertEqual(self.update([second], 1), [second])

    def test_26_cleared_event_can_trigger_after_reappearing(self):
        match = PatternMatch("py", "angry", "Error", "ValueError", confidence=90)
        self.update([match], 0)
        self.update([], 1)
        self.update([], 2)
        self.assertEqual(self.update([match], 61), [match])

    def test_27_low_confidence_requires_confirmation(self):
        match = PatternMatch("py", "angry", "Error", "ValueError", confidence=50)
        self.assertEqual(self.update([match], 0), [])
        self.assertEqual(self.update([match], 1), [match])


class TestStructuredOCR(unittest.TestCase):
    def setUp(self):
        self.fake = _fake_tesseract([
            ("ValueError:", 90, 10, 10, 60, 12, 1),
            ("bad", 80, 75, 10, 20, 12, 1),
            ("Next", 70, 10, 30, 30, 12, 2),
            ("line", 60, 45, 30, 25, 12, 2),
        ])
        self.result = TesseractOCRBackend(self.fake).analyze(object())

    def test_28_words_grouped_into_lines(self):
        self.assertEqual([line.text for line in self.result.lines], ["ValueError: bad", "Next line"])

    def test_29_newlines_preserved(self):
        self.assertEqual(self.result.text, "ValueError: bad\nNext line")

    def test_30_pattern_receives_line_coordinates(self):
        match = _scan_patterns(self.result.text, self.result.lines)[0]
        self.assertEqual((match.screen_x, match.screen_y), (52, 16))

    def test_31_snippet_contains_actual_regex_match(self):
        match = _scan_patterns(self.result.text, self.result.lines)[0]
        self.assertIn("ValueError", match.snippet)

    def test_32_confidence_propagates(self):
        match = _scan_patterns(self.result.text, self.result.lines)[0]
        self.assertEqual(match.confidence, 85.0)


class TestPatterns(unittest.TestCase):
    def test_33_existing_python_patterns(self):
        self.assertTrue(_scan_patterns("Traceback (most recent call last)"))

    def test_34_existing_shell_and_build_patterns(self):
        categories = {
            match.category for match in _scan_patterns(
                "command not found\nBuild FAILED"
            )
        }
        self.assertTrue({"cmd_not_found", "build_error"}.issubset(categories))

    def test_35_roblox_nil_index_matches(self):
        matches = _scan_patterns(
            "attempt to index nil with 'Name'", window_title="Roblox Studio",
        )
        self.assertTrue(any(match.category == "luau_runtime" for match in matches))

    def test_36_roblox_infinite_yield_matches(self):
        matches = _scan_patterns(
            "Infinite yield possible on Workspace", window_title="Roblox Studio",
        )
        self.assertTrue(any(match.category == "luau_infinite_yield" for match in matches))

    def test_37_roblox_pattern_respects_app_context(self):
        matches = _scan_patterns(
            "attempt to index nil with 'Name'", window_title="News - Browser",
        )
        self.assertFalse(any(match.category == "luau_runtime" for match in matches))

    def test_38_broad_prose_does_not_false_positive(self):
        self.assertEqual(_scan_patterns("The build of this story is exciting and calm."), [])

    def test_38a_scoped_pattern_is_skipped_without_app_context(self):
        matches = _scan_patterns("is not a valid member of")
        self.assertFalse(any(match.category == "luau_runtime" for match in matches))

    def test_38b_global_confidence_is_a_floor_for_every_pattern(self):
        line = OCRLine("FATAL ERROR", 0, 0, 100, 20, 35.0)
        self.assertFalse(_scan_patterns(
            line.text,
            [line],
            minimum_confidence=90.0,
        ))
        self.assertTrue(_scan_patterns(
            line.text,
            [line],
            minimum_confidence=20.0,
        ))

    def test_39_custom_patterns_still_load(self):
        original_loaded = screen_reader_module._custom_patterns_loaded
        original_length = len(screen_reader_module.PATTERN_REGISTRY)
        settings = MagicMock()
        settings.ocr_custom_patterns.return_value = [("Deploy", "happy", "deploy complete")]
        try:
            screen_reader_module._custom_patterns_loaded = False
            with patch("agetha.app_config.get_settings", return_value=settings):
                screen_reader_module._ensure_custom_patterns()
            self.assertTrue(_scan_patterns("deploy complete"))
        finally:
            del screen_reader_module.PATTERN_REGISTRY[original_length:]
            screen_reader_module._custom_patterns_loaded = original_loaded

    def test_40_invalid_custom_regex_is_ignored(self):
        original_loaded = screen_reader_module._custom_patterns_loaded
        original_length = len(screen_reader_module.PATTERN_REGISTRY)
        settings = MagicMock()
        settings.ocr_custom_patterns.return_value = [("Bad", "thinking", "[")]
        try:
            screen_reader_module._custom_patterns_loaded = False
            with patch("agetha.app_config.get_settings", return_value=settings):
                screen_reader_module._ensure_custom_patterns()
            self.assertEqual(len(screen_reader_module.PATTERN_REGISTRY), original_length)
        finally:
            del screen_reader_module.PATTERN_REGISTRY[original_length:]
            screen_reader_module._custom_patterns_loaded = original_loaded


class TestConfigurationPrivacy(unittest.TestCase):
    def test_41_invalid_threshold_falls_back(self):
        self.assertEqual(AppSettings({"OCR_CHANGE_THRESHOLD": "bad"}).ocr_change_threshold, 0.025)

    def test_42_numeric_settings_are_clamped(self):
        settings = AppSettings({
            "OCR_CHANGE_THRESHOLD": "9", "OCR_PATTERN_CONFIRM_SCANS": "999",
            "OCR_MIN_WORD_CONFIDENCE": "-2",
        })
        self.assertEqual(settings.ocr_change_threshold, 1.0)
        self.assertEqual(settings.ocr_pattern_confirm_scans, 20)
        self.assertEqual(settings.ocr_min_word_confidence, 0.0)

    def test_43_invalid_psm_falls_back_auto(self):
        self.assertEqual(AppSettings({"OCR_PSM": "99"}).ocr_psm, "auto")

    def test_44_missing_language_data_falls_back_safely(self):
        reader = ScreenReader.__new__(ScreenReader)
        reader._tesseract_checked = False
        reader._tesseract_ready = False
        reader._ocr_languages = "deu+eng"
        reader._effective_ocr_languages = "deu+eng"
        reader.last_monitor_status = ""
        fake = MagicMock()
        fake.get_languages.return_value = ["eng"]
        with patch.object(screen_reader_module, "TESSERACT_OK", True), patch.object(screen_reader_module, "pytesseract", fake):
            self.assertTrue(reader._ensure_tesseract())
        self.assertEqual(reader._effective_ocr_languages, "eng")

    def test_45_excluded_app_skips_ocr(self):
        reader = _capture_reader()
        reader._excluded_apps = ("passwordmanager.exe",)
        reader._foreground_info = lambda: {
            "left": 0, "top": 0, "width": 100, "height": 60,
            "title": "Vault", "hwnd": 1, "process_name": "passwordmanager.exe",
        }
        self.assertIsNone(reader._capture_frame())
        self.assertEqual(reader.last_monitor_status, "skipped_excluded_window")

    def test_45a_extensionless_exclusion_matches_executable_name(self):
        reader = _capture_reader()
        reader._excluded_apps = ("passwordmanager",)
        reader._foreground_info = lambda: {
            "left": 0, "top": 0, "width": 100, "height": 60,
            "title": "Vault", "hwnd": 1, "process_name": "passwordmanager.exe",
        }
        self.assertIsNone(reader._capture_frame())
        self.assertEqual(reader.last_monitor_status, "skipped_excluded_window")

    def test_46_excluded_title_skips_ocr(self):
        reader = _capture_reader()
        reader._title_exclusions = compile_title_exclusions("Private, re:^Secret")
        reader._foreground_info = lambda: {
            "left": 0, "top": 0, "width": 100, "height": 60,
            "title": "Secret document", "hwnd": 1,
        }
        self.assertIsNone(reader._capture_frame())

    def test_47_redaction_only_changes_external_context(self):
        raw = "ValueError: password=hunter2 Bearer abcdefghijklmnop"
        reader = ScreenReader.__new__(ScreenReader)
        reader._redact_sensitive_context = True
        external = reader.redact_for_external_context(raw)
        self.assertIn("ValueError", raw)
        self.assertNotIn("hunter2", external)
        self.assertNotIn("abcdefghijklmnop", external)

    def test_48_local_patterns_see_original_text(self):
        raw = "ValueError: password=hunter2"
        self.assertTrue(_scan_patterns(raw))
        self.assertNotIn("hunter2", redact_sensitive_text(raw))


class TestStaleResults(unittest.TestCase):
    def test_49_changed_foreground_marks_result_stale(self):
        line = OCRLine("ValueError: bad", 1, 2, 30, 10, 90)
        reader = _ocr_reader(OCRResult("ValueError: bad", [], "tesseract", lines=[line]))
        reader._focused_target_is_current = lambda _frame: False
        self.assertEqual(reader.capture_text(), "")
        self.assertEqual(reader.last_monitor_status, "discarded_stale_window")

    def test_50_stale_result_produces_no_event(self):
        line = OCRLine("ValueError: bad", 1, 2, 30, 10, 90)
        reader = _ocr_reader(OCRResult("ValueError: bad", [], "tesseract", lines=[line]))
        reader.last_new_pattern_events = []
        reader._focused_target_is_current = lambda _frame: False
        reader.capture_text()
        self.assertEqual(reader.last_new_pattern_events, [])

    def test_50a_skipped_capture_clears_only_transient_events(self):
        reader = _ocr_reader(OCRResult("", [], "tesseract"))
        previous = PatternMatch(
            "python_error", "angry", "Python error", "old", confidence=90.0,
        )
        reader.last_new_pattern_events = [previous]
        reader.last_pattern_matches = [previous]
        reader.last_word_positions = [{"text": "old"}]
        reader._capture_frame.return_value = None
        self.assertEqual(reader.capture_text(), "")
        self.assertEqual(reader.last_new_pattern_events, [])
        self.assertEqual(reader.last_pattern_matches, [previous])
        self.assertEqual(reader.last_word_positions, [{"text": "old"}])


class TestBackwardCompatibility(unittest.TestCase):
    def setUp(self):
        word = OCRWord("Error", 10, 20, 30, 12, 90)
        line = OCRLine("ValueError: bad", 10, 20, 80, 12, 90, [word])
        self.reader = _ocr_reader(
            OCRResult("ValueError: bad", [word], "tesseract", lines=[line]),
        )

    def test_51_capture_text_returns_string(self):
        self.assertIsInstance(self.reader.capture_text(), str)

    def test_52_word_position_dictionary_keys(self):
        self.reader.capture_text()
        self.assertEqual(
            set(self.reader.last_word_positions[0]),
            {"text", "screen_x", "screen_y", "w", "h", "conf"},
        )

    def test_53_last_pattern_matches_available(self):
        self.reader.capture_text()
        self.assertTrue(self.reader.last_pattern_matches)

    def test_54_has_angry_trigger_still_works(self):
        self.reader.capture_text()
        self.assertTrue(self.reader.has_angry_trigger)

    def test_55_dominant_mood_still_works(self):
        self.reader.capture_text()
        self.assertEqual(self.reader.dominant_mood(), "angry")

    def test_56_deep_ocr_remains_optional(self):
        reader = ScreenReader.__new__(ScreenReader)
        reader._deep_backend_name = "none"
        result = reader.capture_deep_text()
        self.assertEqual(result.metadata["error"], "disabled")

    def test_57_automatic_polling_never_calls_unlimited_ocr(self):
        source = inspect.getsource(__import__("main").CompanionApp._ai_tick)
        self.assertIn("capture_text", source)
        self.assertNotIn("capture_deep_text", source)


if __name__ == "__main__":
    unittest.main()
