"""Headless capability collection and refresh-cancellation tests."""

from __future__ import annotations

import threading
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from agetha.app_config import AppSettings
from agetha.ui.senses_panel import (
    CapabilityStatus,
    SensesRefreshController,
    SensesRuntime,
    collect_senses_state,
    sanitize_status_detail,
)


def _modules(*available: str):
    allowed = set(available)
    return lambda name: name in allowed


def _settings(**values: str) -> AppSettings:
    defaults = {
        "ENABLE_SCREEN_READER": "yes",
        "OCR_FOCUSED_WINDOW_ONLY": "yes",
        "DEEP_OCR_BACKEND": "none",
        "ENABLE_VOICE": "no",
        "USE_LOCAL_STT": "no",
        "VOICE_OUTPUT_MODE": "bleeps_only",
        "ENABLE_LONGTERM_MEMORY": "yes",
        "ENABLE_DREAMS": "yes",
        "ENABLE_TASKS": "yes",
        "EPISODIC_PROMPT_LIMIT": "10",
        "ENABLE_COMMAND_EXECUTION": "yes",
        "ENABLE_COMMAND_CONFIRMATIONS": "yes",
        "PROTECTED_PROCESSES": "explorer.exe,svchost.exe",
        "ENABLE_CIRCADIAN_RHYTHM": "yes",
        "ENABLE_PRESENCE_ETIQUETTE": "yes",
        "ENABLE_TERMINAL_SENTINEL": "no",
        "ENABLE_WEB_RAG": "no",
        "FASTER_MODE": "no",
        "DRY_RUN_MODE": "no",
    }
    defaults.update(values)
    return AppSettings(defaults)


class TestSensesCollector(unittest.TestCase):
    def test_windows_runtime_capability_report(self) -> None:
        reader = SimpleNamespace(
            _automatic_capture_available=True,
            _explicit_capture_available=True,
            _available=True,
            _backend_name="mss",
            last_monitor_status="ocr_complete",
        )
        runtime = SensesRuntime(
            screen_reader=reader,
            companion_state="idle",
            last_safe_scan_time=datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc),
        )
        report = collect_senses_state(
            _settings(), runtime=runtime, platform_name="win32",
            module_available=_modules("mss", "pytesseract", "PIL"),
            memory_accessible=True,
        )
        self.assertEqual(report.get("desktop_gui").status, CapabilityStatus.AVAILABLE)
        self.assertEqual(report.get("focused_window_ocr").status, CapabilityStatus.AVAILABLE)
        self.assertEqual(report.get("automatic_capture").status, CapabilityStatus.AVAILABLE)
        self.assertEqual(report.get("capture_mode").detail, "mss")
        self.assertEqual(report.get("last_safe_scan").status, CapabilityStatus.AVAILABLE)

    def test_linux_xorg_and_wayland_are_distinguished(self) -> None:
        xorg = SimpleNamespace(
            session_type="x11",
            automatic_ocr_supported=True,
            explicit_capture_supported=True,
            selected_screenshot_backend="x11-mss",
        )
        x_report = collect_senses_state(
            _settings(), platform_name="linux", linux_capabilities=xorg,
            module_available=_modules("mss", "pytesseract", "PIL"),
            memory_accessible=True,
        )
        self.assertEqual(x_report.get("automatic_capture").status, CapabilityStatus.AVAILABLE)
        self.assertIn("Xorg", x_report.get("automatic_capture").detail)

        wayland = SimpleNamespace(
            session_type="wayland",
            automatic_ocr_supported=False,
            explicit_capture_supported=True,
            selected_screenshot_backend="wayland-grim",
        )
        w_report = collect_senses_state(
            _settings(), platform_name="linux", linux_capabilities=wayland,
            module_available=_modules("pytesseract", "PIL"),
            memory_accessible=True,
        )
        self.assertEqual(w_report.get("desktop_gui").status, CapabilityStatus.AVAILABLE)
        self.assertEqual(w_report.get("automatic_capture").status, CapabilityStatus.DEGRADED)
        self.assertIn("Wayland", w_report.get("automatic_capture").detail)
        self.assertEqual(w_report.get("capture_mode").status, CapabilityStatus.DEGRADED)

    def test_disabled_voice_and_missing_microphone_dependency(self) -> None:
        disabled = collect_senses_state(
            _settings(ENABLE_VOICE="no"), platform_name="win32",
            module_available=_modules("mss"), memory_accessible=True,
        )
        self.assertEqual(disabled.get("voice_input").status, CapabilityStatus.DISABLED)
        self.assertEqual(disabled.get("microphone").status, CapabilityStatus.DISABLED)

        missing = collect_senses_state(
            _settings(ENABLE_VOICE="yes"), platform_name="win32",
            module_available=_modules("mss"), memory_accessible=True,
            microphone_name=None,
        )
        self.assertEqual(missing.get("voice_input").status, CapabilityStatus.UNAVAILABLE)

    def test_local_ollama_and_remote_provider_reports_do_not_probe(self) -> None:
        local = collect_senses_state(
            _settings(USE_LOCAL_AI="yes"), platform_name="win32",
            memory_accessible=True,
        )
        self.assertEqual(local.get("provider").detail, "Local Ollama")
        self.assertEqual(local.get("provider_location").status, CapabilityStatus.AVAILABLE)
        self.assertEqual(local.get("provider_availability").status, CapabilityStatus.UNKNOWN)
        self.assertIn("Not probed", local.get("provider_availability").detail)

        remote = collect_senses_state(
            _settings(USE_LOCAL_AI="no", ENABLE_GROQ="yes", ENABLE_OPENROUTER="no"),
            platform_name="win32", memory_accessible=True,
        )
        self.assertEqual(remote.get("provider").detail, "Groq")
        self.assertEqual(remote.get("provider_location").status, CapabilityStatus.DEGRADED)

    def test_action_warnings_fast_mode_and_sentinel_state(self) -> None:
        report = collect_senses_state(
            _settings(
                ENABLE_COMMAND_EXECUTION="no",
                ENABLE_COMMAND_CONFIRMATIONS="no",
                FASTER_MODE="yes",
                ENABLE_TERMINAL_SENTINEL="yes",
            ),
            runtime=SensesRuntime(companion_state="talking"),
            platform_name="win32", memory_accessible=True,
        )
        self.assertEqual(report.get("command_execution").status, CapabilityStatus.DISABLED)
        self.assertEqual(report.get("confirmations").status, CapabilityStatus.DISABLED)
        self.assertEqual(report.get("fast_mode").status, CapabilityStatus.AVAILABLE)
        self.assertIn("safety", report.get("fast_mode").detail)
        self.assertEqual(report.get("terminal_sentinel").status, CapabilityStatus.AVAILABLE)
        self.assertEqual(report.get("companion_state").status, CapabilityStatus.AVAILABLE)

        warning = collect_senses_state(
            _settings(ENABLE_COMMAND_EXECUTION="yes", ENABLE_COMMAND_CONFIRMATIONS="no"),
            platform_name="win32", memory_accessible=True,
        )
        self.assertEqual(warning.get("confirmations").status, CapabilityStatus.DEGRADED)

    def test_deep_ocr_reachability_is_never_implicitly_checked(self) -> None:
        configured = collect_senses_state(
            _settings(
                DEEP_OCR_BACKEND="unlimited_ocr",
                UNLIMITED_OCR_SERVER_URL="http://127.0.0.1:8000",
            ),
            runtime=SensesRuntime(deep_ocr_checked=False),
            platform_name="win32", memory_accessible=True,
        )
        self.assertEqual(configured.get("deep_ocr_configured").status, CapabilityStatus.AVAILABLE)
        self.assertEqual(configured.get("deep_ocr_reachable").status, CapabilityStatus.UNKNOWN)
        self.assertIn("explicit", configured.get("deep_ocr_reachable").detail.casefold())

        known = collect_senses_state(
            _settings(DEEP_OCR_BACKEND="unlimited_ocr"),
            runtime=SensesRuntime(deep_ocr_checked=True, deep_ocr_reachable=False),
            platform_name="win32", memory_accessible=True,
        )
        self.assertEqual(known.get("deep_ocr_reachable").status, CapabilityStatus.UNAVAILABLE)

    def test_report_never_exposes_keys_raw_paths_or_private_content(self) -> None:
        secret = "sk-THIS_IS_A_SECRET_123456789"
        report = collect_senses_state(
            _settings(
                UNLIMITED_OCR_API_KEY=secret,
                TESSERACT_PATH=r"C:\\Users\\private-person\\Tesseract\\tesseract.exe",
            ),
            runtime=SensesRuntime(
                selected_microphone=r"C:\\Users\\private-person\\private-mic",
                last_safe_scan_time=r"C:\\Users\\private-person\\scan.txt",
            ),
            platform_name="win32", memory_accessible=True,
        )
        rendered = str(report.as_dict())
        self.assertNotIn(secret, rendered)
        self.assertNotIn("private-person", rendered)
        self.assertNotIn("tesseract.exe", rendered)
        self.assertNotIn("raw OCR", rendered)
        self.assertEqual(
            sanitize_status_detail("api_key=secret-value C:\\Users\\me\\x"),
            "api_key= [REDACTED] [local path]",
        )

    def test_unknown_state_is_reported_honestly(self) -> None:
        report = collect_senses_state(
            _settings(), runtime=SensesRuntime(), platform_name="mystery-os",
            module_available=_modules(), memory_accessible=None,
        )
        self.assertEqual(report.get("capture_mode").status, CapabilityStatus.UNKNOWN)
        self.assertEqual(report.get("companion_state").status, CapabilityStatus.UNKNOWN)
        self.assertEqual(report.get("provider_availability").status, CapabilityStatus.UNKNOWN)


class TestSensesRefreshController(unittest.TestCase):
    def test_refresh_result_is_discarded_after_shutdown(self) -> None:
        started = threading.Event()
        release = threading.Event()
        published: list[object] = []
        workers: list[threading.Thread] = []

        def collector():
            started.set()
            release.wait(timeout=2)
            return object()

        def start_worker(callback):
            worker = threading.Thread(target=callback)
            workers.append(worker)
            worker.start()
            return worker

        controller = SensesRefreshController(
            collector, lambda generation, snapshot: published.append((generation, snapshot)),
            start_worker=start_worker,
        )
        self.assertIsNotNone(controller.refresh())
        self.assertTrue(started.wait(timeout=1))
        controller.close()
        controller.close()
        release.set()
        workers[0].join(timeout=1)
        self.assertFalse(workers[0].is_alive())
        self.assertEqual(published, [])
        self.assertIsNone(controller.refresh())


if __name__ == "__main__":
    unittest.main()
