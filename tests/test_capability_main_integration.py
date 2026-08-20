"""Headless app-lifecycle integration tests for Compact/Full profiles."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from agetha.app_config import AppSettings  # noqa: E402
from agetha.core.capabilities import (  # noqa: E402
    Capability,
    CapabilityController,
    CapabilityPolicy,
    CapabilityProfile,
)
from agetha.core.capability_consent import CapabilityConsentFlow  # noqa: E402


def _settings(*, compact: bool, **overrides: str) -> AppSettings:
    raw = {
        "COMPACT_MODE": "yes" if compact else "no",
        "ENABLE_COMMAND_EXECUTION": "yes",
        "ENABLE_UNICODE_TYPING": "yes",
        "ENABLE_COMPUTER_USE": "yes",
        "ENABLE_PROCESS_AWARENESS": "yes",
        "ENABLE_TERMINAL_SENTINEL": "yes",
        "ENABLE_AMBIENT_POLLS": "yes",
        "ENABLE_WEB_RAG": "yes",
        "ENABLE_AGENT_CONTINUATION": "yes",
    }
    raw.update(overrides)
    return AppSettings(raw)


class TestMainCapabilityLifecycle(unittest.TestCase):
    @staticmethod
    def _app(settings: AppSettings):
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._capabilities = CapabilityController(CapabilityPolicy.from_settings(settings))
        app._capability_consent = CapabilityConsentFlow(initial_full=not settings.compact_mode)
        app._computer_use = None
        app._computer_use_start_cancel = None
        app._typing_cancel_event = None
        app._terminal_sentinel = None
        app._process_awareness = None
        app._screen = None
        app._continuation_tools = None
        app._computer_use_status_window = None
        app._sentinel_popups = set()
        app._poll_job = None
        app._dashboard = None
        app._schedule_ui = lambda callback: callback()
        app._close_computer_use_status = MagicMock()
        app._stop_computer_use = MagicMock()
        app._stop_computer_use_escape_hotkey = MagicMock()
        app._invalidate_continuation_ui_delivery = MagicMock()
        app._invalidate_computer_use_ui_delivery = MagicMock()
        app._sync_screen_window_state = MagicMock()
        app._schedule_screen_poll = MagicMock()
        app.root = SimpleNamespace(after_cancel=MagicMock())
        return app

    def test_dashboard_is_single_instance_and_existing_window_is_presented(self) -> None:
        app = self._app(_settings(compact=True))
        handles = []

        class FakeDashboard:
            def __init__(self, on_close) -> None:
                self.is_open = True
                self.present_count = 0
                self._on_close = on_close

            def present(self) -> bool:
                self.present_count += 1
                return self.is_open

            def close(self) -> None:
                if not self.is_open:
                    return
                self.is_open = False
                self._on_close(self)

        def fake_open_dashboard(_root, _settings, **kwargs):
            handle = FakeDashboard(kwargs["on_close"])
            handles.append(handle)
            return handle

        with patch.object(main, "get_settings", return_value=_settings(compact=True)), patch(
            "agetha.ui.dashboard.open_dashboard",
            side_effect=fake_open_dashboard,
        ) as opener:
            app._open_dashboard()
            app._open_dashboard()

        self.assertEqual(opener.call_count, 1)
        self.assertIs(app._dashboard, handles[0])
        self.assertEqual(handles[0].present_count, 1)

    def test_compact_commit_rebuilds_open_dashboard_with_current_profile(self) -> None:
        app = self._app(_settings(compact=False))
        profiles: list[bool] = []
        handles = []

        class FakeDashboard:
            def __init__(self, on_close) -> None:
                self.is_open = True
                self._on_close = on_close

            def present(self) -> bool:
                return self.is_open

            def close(self) -> None:
                if not self.is_open:
                    return
                self.is_open = False
                self._on_close(self)

        def fake_open_dashboard(_root, settings, **kwargs):
            profiles.append(settings.compact_mode)
            handle = FakeDashboard(kwargs["on_close"])
            handles.append(handle)
            return handle

        with patch("agetha.ui.dashboard.open_dashboard", side_effect=fake_open_dashboard):
            with patch.object(main, "get_settings", return_value=_settings(compact=False)):
                app._open_dashboard()
            with patch.object(main, "get_settings", return_value=_settings(compact=True)), patch(
                "agetha.app_config.patch_config_key", return_value=True,
            ):
                self.assertTrue(app._activate_compact_mode())

        self.assertEqual(profiles, [False, True])
        self.assertFalse(handles[0].is_open)
        self.assertIs(app._dashboard, handles[1])

    def test_profile_commit_does_not_open_a_closed_dashboard(self) -> None:
        app = self._app(_settings(compact=False))
        app._open_dashboard = MagicMock()

        with patch.object(main, "get_settings", return_value=_settings(compact=True)), patch(
            "agetha.app_config.patch_config_key", return_value=True,
        ):
            self.assertTrue(app._activate_compact_mode())

        app._open_dashboard.assert_not_called()

    def test_failed_compact_persistence_rebuilds_open_dashboard_fail_closed(self) -> None:
        app = self._app(_settings(compact=False))
        shown_profiles: list[bool] = []

        class FakeDashboard:
            def __init__(self, on_close) -> None:
                self.is_open = True
                self._on_close = on_close

            def present(self) -> bool:
                return self.is_open

            def close(self) -> None:
                if self.is_open:
                    self.is_open = False
                    self._on_close(self)

        def fake_open_dashboard(_root, settings, **kwargs):
            shown_profiles.append(settings.compact_mode)
            return FakeDashboard(kwargs["on_close"])

        full = _settings(compact=False)
        with patch("agetha.ui.dashboard.open_dashboard", side_effect=fake_open_dashboard), patch.object(
            main, "get_settings", return_value=full,
        ):
            app._open_dashboard()
            with patch("agetha.app_config.patch_config_key", return_value=False):
                self.assertFalse(app._activate_compact_mode())

        self.assertEqual(shown_profiles, [False, True])
        self.assertEqual(app._capabilities.snapshot().profile, CapabilityProfile.COMPACT)

    def test_compact_start_does_not_construct_advanced_services(self) -> None:
        app = self._app(_settings(compact=True))
        with patch.object(main, "get_settings", return_value=_settings(compact=True)), patch(
            "agetha.platform.process_awareness.ProcessAwareness",
        ) as process, patch(
            "agetha.features.terminal_sentinel.TerminalSentinel.from_settings",
        ) as sentinel, patch.object(app, "_initialize_computer_use_runtime") as computer:
            app._start_full_mode_services()
        process.assert_not_called()
        sentinel.assert_not_called()
        computer.assert_not_called()

    def test_full_starts_only_individually_enabled_services(self) -> None:
        settings = _settings(compact=False, ENABLE_TERMINAL_SENTINEL="no")
        app = self._app(settings)
        screen = MagicMock()
        with patch.object(main, "get_settings", return_value=settings), patch.object(
            main, "ScreenReader", return_value=screen,
        ), patch(
            "agetha.platform.process_awareness.ProcessAwareness",
            return_value=MagicMock(),
        ) as process, patch(
            "agetha.features.terminal_sentinel.TerminalSentinel.from_settings",
        ) as sentinel, patch.object(app, "_initialize_computer_use_runtime") as computer:
            app._start_full_mode_services()
        self.assertIs(app._screen, screen)
        app._sync_screen_window_state.assert_called_once()
        screen.cache_own_window_handle.assert_called_once()
        app._schedule_screen_poll.assert_called_once()
        process.assert_called_once()
        sentinel.assert_not_called()
        computer.assert_called_once()

    def test_screen_reader_finishing_after_downgrade_is_stopped_not_attached(self) -> None:
        settings = _settings(compact=False)
        app = self._app(settings)
        candidate = MagicMock()

        def construct_reader(**_kwargs):
            app._capabilities.begin_compact_transition()
            return candidate

        with patch.object(main, "get_settings", return_value=settings), patch.object(
            main, "ScreenReader", side_effect=construct_reader,
        ):
            app._start_full_mode_services()

        self.assertIsNone(app._screen)
        candidate.stop.assert_called_once_with()
        app._sync_screen_window_state.assert_not_called()
        app._schedule_screen_poll.assert_not_called()

    def test_downgrade_flips_gate_before_cancelling_and_stops_owners(self) -> None:
        settings = _settings(compact=False)
        compact = _settings(compact=True)
        app = self._app(settings)
        order: list[str] = []
        process = MagicMock()
        sentinel = MagicMock()
        computer = MagicMock()
        screen = MagicMock()
        observations = MagicMock()
        continuation_tools = MagicMock()
        senses = MagicMock()
        app._process_awareness = process
        app._terminal_sentinel = sentinel
        app._computer_use = computer
        app._screen = screen
        app._observation_bus = observations
        app._continuation_tools = continuation_tools
        app._senses_panel = senses
        app._typing_cancel_event = __import__("threading").Event()
        app._poll_job = "screen-poll"
        app._stop_computer_use.side_effect = lambda *_: order.append(
            app._capabilities.snapshot().profile.value
        )
        with patch.object(main, "get_settings", return_value=compact), patch(
            "agetha.app_config.patch_config_key", return_value=True,
        ):
            self.assertTrue(app._activate_compact_mode())
        self.assertEqual(order, ["compact"])
        computer.shutdown.assert_called_once()
        process.shutdown.assert_called_once()
        sentinel.stop.assert_called_once()
        screen.stop.assert_called_once()
        continuation_tools.set_process_awareness.assert_called_once_with(None)
        observations.clear.assert_called_once_with()
        senses.close.assert_called_once_with()
        self.assertTrue(app._typing_cancel_event.is_set())
        app.root.after_cancel.assert_called_once_with("screen-poll")
        self.assertEqual(app._capabilities.snapshot().profile, CapabilityProfile.COMPACT)
        self.assertFalse(app._capabilities.is_allowed(Capability.COMPUTER_USE))

    def test_persistence_failure_stays_fail_closed_compact(self) -> None:
        app = self._app(_settings(compact=False))
        with patch("agetha.app_config.patch_config_key", return_value=False):
            self.assertFalse(app._activate_compact_mode())
        self.assertEqual(app._capabilities.snapshot().profile, CapabilityProfile.COMPACT)


if __name__ == "__main__":
    unittest.main()
