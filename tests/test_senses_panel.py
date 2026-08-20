"""Headless capability collection and refresh-cancellation tests."""

from __future__ import annotations

import threading
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from agetha.app_config import AppSettings
from agetha.core.capabilities import CapabilityProfile
from agetha.ui.senses_panel import (
    CapabilityStatus,
    ComputerUsePanelSnapshot,
    ContinuationPanelSnapshot,
    ProcessPanelSnapshot,
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
        "COMPACT_MODE": "no",
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
        "ENABLE_AGENT_CONTINUATION": "yes",
        "AGENT_MAX_STEPS": "6",
        "ENABLE_PROCESS_AWARENESS": "yes",
        "PROCESS_CONTEXT_MODE": "visible_apps",
        "ENABLE_COMPUTER_USE": "no",
        "COMPUTER_USE_MAX_STEPS": "30",
        "COMPUTER_USE_PLANNER_PROVIDER": "inherit",
        "COMPUTER_USE_PLANNER_MODEL": "",
        "FASTER_MODE": "no",
        "DRY_RUN_MODE": "no",
    }
    defaults.update(values)
    return AppSettings(defaults)


class TestSensesCollector(unittest.TestCase):
    def test_compact_profile_reports_effective_advanced_capabilities_disabled(self) -> None:
        report = collect_senses_state(
            _settings(
                COMPACT_MODE="yes",
                ENABLE_AMBIENT_POLLS="yes",
                ENABLE_COMMAND_EXECUTION="yes",
                ENABLE_PROCESS_AWARENESS="yes",
                ENABLE_TERMINAL_SENTINEL="yes",
                ENABLE_COMPUTER_USE="yes",
            ),
            runtime=SensesRuntime(
                process_snapshot=ProcessPanelSnapshot(
                    state="available", foreground_app="notepad.exe",
                ),
                computer_use_snapshot=ComputerUsePanelSnapshot(
                    active=True, state="running", target_app="notepad.exe",
                ),
            ),
            platform_name="win32",
            module_available=_modules("mss", "pytesseract", "PIL"),
            memory_accessible=True,
        )

        self.assertEqual(report.profile, CapabilityProfile.COMPACT)
        self.assertEqual(report.get("capability_profile").detail, "COMPACT")
        for key in (
            "automatic_capture",
            "process_awareness",
            "terminal_sentinel",
            "computer_use",
            "computer_use_active",
            "computer_planner_provider",
        ):
            item = report.get(key)
            self.assertEqual(item.status, CapabilityStatus.DISABLED, key)
            self.assertEqual(item.detail, "Disabled — Compact Mode", key)

    def test_compact_collection_does_not_read_advanced_runtime_services(self) -> None:
        calls = {
            "process_snapshot": 0,
            "computer_snapshot": 0,
            "provider_status": 0,
            "sentinel_state": 0,
        }

        class ProcessAwareness:
            @property
            def last_snapshot(self):
                calls["process_snapshot"] += 1
                return SimpleNamespace(status="available")

        class ComputerUse:
            def snapshot(self):
                calls["computer_snapshot"] += 1
                return SimpleNamespace(state="running")

        class AIEngine:
            def get_token_status(self):
                calls["provider_status"] += 1
                raise AssertionError("Compact Senses must not query a provider object")

        class TerminalSentinel:
            @property
            def enabled(self):
                calls["sentinel_state"] += 1
                raise AssertionError("Compact Senses must not inspect disabled services")

        screen = SimpleNamespace(
            capture=lambda: (_ for _ in ()).throw(
                AssertionError("Senses must not capture the screen")
            ),
        )
        report = collect_senses_state(
            _settings(
                COMPACT_MODE="yes",
                ENABLE_PROCESS_AWARENESS="yes",
                ENABLE_COMPUTER_USE="yes",
                ENABLE_TERMINAL_SENTINEL="yes",
            ),
            runtime=SimpleNamespace(
                _screen=screen,
                _process_awareness=ProcessAwareness(),
                _computer_use=ComputerUse(),
                _ai=AIEngine(),
                _terminal_sentinel=TerminalSentinel(),
            ),
            platform_name="win32",
            module_available=_modules(),
            memory_accessible=True,
        )

        self.assertEqual(report.profile, CapabilityProfile.COMPACT)
        self.assertEqual(
            calls,
            {
                "process_snapshot": 0,
                "computer_snapshot": 0,
                "provider_status": 0,
                "sentinel_state": 0,
            },
        )

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

    def test_agent_process_and_computer_use_rows_use_only_minimized_snapshots(self) -> None:
        runtime = SensesRuntime(
            continuation_snapshot=ContinuationPanelSnapshot(
                active=True, state="awaiting_tool", step=2, max_steps=6,
            ),
            process_snapshot=ProcessPanelSnapshot(
                state="available",
                foreground_app=r"C:\Users\private-person\Apps\notepad.exe",
                visible_app_count=3,
            ),
            computer_use_snapshot=ComputerUsePanelSnapshot(
                active=True,
                state="running",
                target_app=r"C:\Users\private-person\Apps\notepad.exe",
                step=4,
                max_steps=30,
                recovery_calls=1,
                last_result=(
                    "password=super-secret raw OCR: Bank balance; "
                    r"payload=สวัสดี C:\Users\private-person\private.txt"
                ),
                accessibility_available=False,
                ocr_available=True,
            ),
        )
        report = collect_senses_state(
            _settings(
                ENABLE_COMPUTER_USE="yes",
                COMPUTER_USE_PLANNER_PROVIDER="groq",
                COMPUTER_USE_PLANNER_MODEL="planner-small",
            ),
            runtime=runtime,
            platform_name="win32",
            memory_accessible=True,
        )

        self.assertEqual(report.get("continuation_engine").status, CapabilityStatus.AVAILABLE)
        self.assertIn("step 2 / 6", report.get("continuation_engine").detail)
        self.assertEqual(report.get("process_awareness").status, CapabilityStatus.AVAILABLE)
        self.assertIn("3 visible", report.get("process_awareness").detail)
        self.assertEqual(report.get("process_foreground").detail, "notepad.exe")
        self.assertEqual(report.get("computer_use_active").status, CapabilityStatus.AVAILABLE)
        self.assertEqual(report.get("computer_use_target").detail, "notepad.exe")
        self.assertEqual(report.get("computer_use_step").detail, "4 / 30")
        self.assertEqual(report.get("computer_use_recovery_calls").detail, "1")
        self.assertEqual(report.get("computer_planner_provider").detail, "Groq")
        self.assertEqual(report.get("computer_planner_model").detail, "planner-small")
        self.assertEqual(report.get("computer_recovery_model").detail, "Primary provider and model")
        self.assertEqual(
            report.get("computer_use_accessibility").status,
            CapabilityStatus.UNAVAILABLE,
        )
        self.assertEqual(report.get("computer_use_ocr").status, CapabilityStatus.AVAILABLE)

        rendered = str(report.as_dict())
        for private_value in (
            "private-person", "super-secret", "Bank balance", "สวัสดี", "private.txt",
        ):
            self.assertNotIn(private_value, rendered)

    def test_disabled_new_features_report_no_active_target_or_provider(self) -> None:
        report = collect_senses_state(
            _settings(
                ENABLE_AGENT_CONTINUATION="no",
                ENABLE_PROCESS_AWARENESS="no",
                ENABLE_COMPUTER_USE="no",
            ),
            runtime=SensesRuntime(),
            platform_name="win32",
            memory_accessible=True,
        )
        self.assertEqual(report.get("continuation_engine").status, CapabilityStatus.DISABLED)
        self.assertEqual(report.get("process_awareness").status, CapabilityStatus.DISABLED)
        self.assertEqual(report.get("process_context_mode").status, CapabilityStatus.DISABLED)
        self.assertEqual(report.get("computer_use").status, CapabilityStatus.DISABLED)
        self.assertEqual(report.get("computer_use_active").status, CapabilityStatus.DISABLED)
        self.assertEqual(report.get("computer_use_target").status, CapabilityStatus.DISABLED)
        self.assertEqual(report.get("computer_planner_provider").status, CapabilityStatus.DISABLED)

    def test_from_app_reads_snapshots_without_capture_scan_or_planning(self) -> None:
        calls = {"continuation": 0, "computer": 0}

        class Continuation:
            def active_snapshot(self):
                calls["continuation"] += 1
                return SimpleNamespace(
                    state="awaiting_model",
                    step=1,
                    max_steps=6,
                    original_user_message="do not expose this goal",
                )

            def last_snapshot(self):
                raise AssertionError("active state should be sufficient")

        class ProcessAwareness:
            last_status = "available"
            last_snapshot = SimpleNamespace(
                status="available",
                foreground=SimpleNamespace(
                    identity=SimpleNamespace(
                        name=r"C:\Users\private-person\Apps\code.exe",
                    ),
                    window_title="Private project — token=secret-value",
                    sensitive=False,
                ),
                visible_apps=(object(), object()),
            )

            def snapshot(self):
                raise AssertionError("Senses must not trigger a process scan")

        class ComputerUse:
            _observer = SimpleNamespace(accessibility_available=False)

            def snapshot(self):
                calls["computer"] += 1
                return SimpleNamespace(
                    state="running",
                    target_process=r"C:\Users\private-person\Apps\notepad.exe",
                    step=3,
                    max_steps=30,
                    recovery_calls=0,
                    last_result="starting",
                    goal="do not expose this goal",
                    payload="do not expose this payload",
                )

            def run(self):
                raise AssertionError("Senses must not run Computer Use")

        screen = SimpleNamespace(
            _available=True,
            capture=lambda: (_ for _ in ()).throw(
                AssertionError("Senses must not capture the screen")
            ),
        )
        view = SensesRuntime.from_app(SimpleNamespace(
            _screen=screen,
            _continuation=Continuation(),
            _process_awareness=ProcessAwareness(),
            _computer_use=ComputerUse(),
        ))

        self.assertEqual(calls, {"continuation": 1, "computer": 1})
        self.assertEqual(view.process_snapshot.foreground_app, "code.exe")
        self.assertEqual(view.computer_use_snapshot.target_app, "notepad.exe")
        rendered = repr(view)
        self.assertNotIn("private-person", rendered)
        self.assertNotIn("do not expose", rendered)
        self.assertNotIn("secret-value", rendered)


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
