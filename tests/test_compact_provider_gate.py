"""Provider-lazy Compact startup and ambient generation-bound integration tests."""

from __future__ import annotations

import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import main
from agetha.app_config import AppSettings
from agetha.commands import command_handlers
from agetha.commands.command_handlers import DispatchCtx
from agetha.core import ai_engine as ai_module
from agetha.core.ai_engine import AIEngine
from agetha.core.capabilities import Capability, CapabilityController, CapabilityPolicy


def _settings(*, compact: bool) -> AppSettings:
    return AppSettings({
        "COMPACT_MODE": "yes" if compact else "no",
        "ENABLE_AMBIENT_POLLS": "yes",
        "ENABLE_PROCESS_AWARENESS": "yes",
        "ENABLE_SCREEN_READER": "yes",
        "ENABLE_TERMINAL_SENTINEL": "yes",
        "ENABLE_COMMAND_EXECUTION": "yes",
    })


class TestCompactProviderInitialization(unittest.TestCase):
    @staticmethod
    def _local_config() -> dict[str, str]:
        return {
            "USE_LOCAL_AI": "yes",
            "LOCAL_AI_MODEL": "llama3",
            "ENABLE_GROQ": "no",
            "ENABLE_OPENROUTER": "no",
            "LOCAL_AI_TIMEOUT": "30",
        }

    def test_local_provider_is_deferred_until_first_direct_query(self) -> None:
        config = self._local_config()
        settings = _settings(compact=True)
        provider_json = (
            '{"command":"speak","mood":"neutral",'
            '"segments":[{"text":"hello","pause":0.0}]}'
        )

        with patch.object(AIEngine, "_resolve_config_path", return_value=Path("config.txt")), patch.object(
            AIEngine, "_resolve_system_path", return_value="C:/Users/user",
        ), patch.object(AIEngine, "_load_config", return_value=dict(config)), patch.object(
            AIEngine, "_load_compact_characters", return_value="",
        ), patch.object(Path, "write_text", return_value=0), patch.object(
            ai_module, "get_settings", return_value=settings,
        ), patch.object(
            ai_module._LocalOllamaClient,
            "_generate",
            side_effect=["pong", provider_json],
        ) as generate, patch.object(
            ai_module._LocalOllamaClient,
            "list_models",
            return_value={"llama3"},
        ) as list_models:
            engine = AIEngine(defer_provider_init=True)
            generate.assert_not_called()
            list_models.assert_not_called()

            engine._build_prompt = MagicMock(return_value=("system", "user", []))
            engine._record_profile_response = MagicMock()
            result = engine.query(user_message="hello")

        self.assertEqual(result["command"], "speak")
        self.assertEqual(generate.call_count, 2)
        list_models.assert_called_once_with()

    def test_default_full_initialization_remains_eager(self) -> None:
        settings = _settings(compact=False)
        with patch.object(AIEngine, "_resolve_config_path", return_value=Path("config.txt")), patch.object(
            AIEngine, "_resolve_system_path", return_value="C:/Users/user",
        ), patch.object(
            AIEngine, "_load_config", return_value=self._local_config(),
        ), patch.object(AIEngine, "_load_compact_characters", return_value=""), patch.object(
            Path, "write_text", return_value=0,
        ), patch.object(ai_module, "get_settings", return_value=settings), patch.object(
            ai_module._LocalOllamaClient, "_generate", return_value="pong",
        ) as generate, patch.object(
            ai_module._LocalOllamaClient, "list_models", return_value={"llama3"},
        ) as list_models:
            engine = AIEngine()

        self.assertIsNotNone(engine._client)
        generate.assert_called_once()
        list_models.assert_called_once_with()

    def test_compact_construction_does_not_construct_remote_provider_clients(self) -> None:
        config = {
            "USE_LOCAL_AI": "no",
            "ENABLE_GROQ": "yes",
            "GROQ_API_KEY": "test-groq-key",
            "ENABLE_OPENROUTER": "yes",
        }
        settings = AppSettings({
            "COMPACT_MODE": "yes",
            "ENABLE_OPENROUTER": "yes",
            "OPENROUTER_API_KEY": "test-openrouter-key",
        })
        with patch.object(AIEngine, "_resolve_config_path", return_value=Path("config.txt")), patch.object(
            AIEngine, "_resolve_system_path", return_value="C:/Users/user",
        ), patch.object(AIEngine, "_load_config", return_value=config), patch.object(
            AIEngine, "_load_compact_characters", return_value="",
        ), patch.object(Path, "write_text", return_value=0), patch.object(
            ai_module, "get_settings", return_value=settings,
        ), patch.object(ai_module, "GROQ_OK", True), patch.object(
            ai_module, "Groq", create=True,
        ) as groq_client, patch.object(
            ai_module, "_OpenRouterClient",
        ) as openrouter_client, patch.object(
            AIEngine, "_ask_provider_choice",
        ) as provider_choice:
            engine = AIEngine(defer_provider_init=True)

        self.assertIsNone(engine._client)
        groq_client.assert_not_called()
        openrouter_client.assert_not_called()
        provider_choice.assert_not_called()

    def test_generation_expiry_after_local_validation_blocks_chat_provider_call(self) -> None:
        settings = _settings(compact=False)
        allowed = [True]

        def finish_validation() -> set[str]:
            allowed[0] = False
            return {"llama3"}

        with patch.object(AIEngine, "_resolve_config_path", return_value=Path("config.txt")), patch.object(
            AIEngine, "_resolve_system_path", return_value="C:/Users/user",
        ), patch.object(
            AIEngine, "_load_config", return_value=self._local_config(),
        ), patch.object(AIEngine, "_load_compact_characters", return_value=""), patch.object(
            Path, "write_text", return_value=0,
        ), patch.object(ai_module, "get_settings", return_value=settings), patch.object(
            ai_module._LocalOllamaClient, "_generate", return_value="pong",
        ) as generate, patch.object(
            ai_module._LocalOllamaClient,
            "list_models",
            side_effect=finish_validation,
        ):
            engine = AIEngine(defer_provider_init=True)
            engine._build_prompt = MagicMock(return_value=("system", "user", []))
            result = engine.query(
                user_message="ambient prompt",
                provider_authorization=lambda: allowed[0],
            )

        self.assertEqual(result["command"], "idle")
        generate.assert_called_once()  # Initialization ping only; no chat request.

    def test_compact_background_init_requests_provider_deferred_ai(self) -> None:
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._capabilities = CapabilityController(
            CapabilityPolicy.from_settings(_settings(compact=True)),
        )
        app._schedule_ui = lambda callback: callback()
        app._advance_progress = MagicMock()
        app._continuation = None
        app._continuation_tools = None
        app._process_awareness = None
        app._screen = None
        app._initialize_computer_use_runtime = MagicMock()
        app._sync_screen_window_state = MagicMock()
        app._subtitle = MagicMock()
        app._load_gifs_simple = MagicMock()
        app._start_placeholder_refresh = MagicMock()

        with patch.object(main, "BleepPlayer", return_value=MagicMock()), patch.object(
            main, "AIEngine", return_value=MagicMock(),
        ) as engine_type, patch.object(
            main, "VoiceOutputCoordinator", return_value=MagicMock(),
        ):
            app._init_background()

        self.assertTrue(engine_type.call_args.kwargs["defer_provider_init"])


class TestAmbientCapabilityGeneration(unittest.TestCase):
    @staticmethod
    def _app():
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._state = app.STATE_IDLE
        app._capabilities = CapabilityController(
            CapabilityPolicy.from_settings(_settings(compact=False)),
        )
        app._ai_tick_lock = threading.Lock()
        app._ai_busy = False
        app._ai_busy_noninterruptible = False
        app._ai_operation_token = None
        app._speech_active = False
        app._pending_user_message = None
        app._pending_user_origin = "user"
        app._pending_screen_context = None
        app._post_ai_tick_callbacks = []
        app._deferred_ai_callbacks_inflight = False
        app._cancel_event = threading.Event()
        app._last_screen_text = ""
        app._last_direct_interaction_time = 0.0
        app._process_awareness = None
        app._screen = None
        app._ai = MagicMock()
        app._ai.query.return_value = {
            "command": "idle", "mood": "neutral", "segments": [],
        }
        app.root = MagicMock()
        app.root.after.side_effect = lambda _delay, callback: callback()
        app._input_box = MagicMock()
        app._subtitle = MagicMock()
        app._set_state = MagicMock()
        app._re_enable_input = MagicMock()
        app._update_token_status = MagicMock()
        app._dispatch_response = MagicMock()
        app._reschedule_screen_poll = MagicMock()
        app._wake_from_presence_rest = MagicMock()
        app._fast_mode_runtime_active = lambda: False
        app._run_deferred_ai_tick_callbacks = MagicMock()
        app._drain_pending_user_message = MagicMock()
        app._presence_decision = lambda: SimpleNamespace(
            allow_popup=True,
            queue_nonurgent=False,
            reason="allowed",
        )
        app._observe_capture_target = MagicMock()
        app._evaluate_terminal_sentinel_events = MagicMock(return_value=False)
        app._speak_and_continue = MagicMock()
        return app

    @staticmethod
    def _runtime_settings(**overrides):
        values = {
            "enable_streaming": False,
            "enable_computer_use": False,
            "computer_use_allowed_apps": (),
            "include_window_title_in_context": False,
            "ocr_pause_while_typing_sec": 0,
            "ocr_focused_window_only": True,
            "enable_screen_reader": True,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_downgrade_during_process_probe_blocks_screen_and_provider(self) -> None:
        app = self._app()
        process = MagicMock()
        process.last_snapshot = SimpleNamespace(foreground=None)
        process.poll.side_effect = app._capabilities.begin_compact_transition
        app._process_awareness = process
        screen = MagicMock()
        screen.automatic_capture_supported = True
        app._screen = screen

        with patch.object(main, "_SETTINGS", self._runtime_settings()), patch.object(
            main, "get_settings", return_value=self._runtime_settings(),
        ), patch.object(main, "native_error_popup"):
            app._ai_tick(origin="ambient")

        process.poll.assert_called_once_with()
        screen.capture_text.assert_not_called()
        app._ai.query.assert_not_called()
        app._dispatch_response.assert_not_called()
        self.assertFalse(app._ai_busy)

    def test_downgrade_during_screen_probe_discards_capture_before_provider(self) -> None:
        app = self._app()
        screen = MagicMock()
        screen._get_own_hwnd.return_value = 100
        screen.automatic_capture_supported = True
        screen.capture_text.side_effect = lambda **_kwargs: (
            app._capabilities.begin_compact_transition() and "private OCR"
        )
        screen.last_monitor_status = "ocr_complete"
        screen.last_pattern_matches = []
        screen.last_new_pattern_events = []
        screen.last_word_positions = []
        app._screen = screen

        with patch.object(main, "_SETTINGS", self._runtime_settings()), patch.object(
            main, "get_settings", return_value=self._runtime_settings(),
        ), patch.object(main, "native_error_popup"):
            app._ai_tick(origin="ambient")

        screen.capture_text.assert_called_once_with(focused_only=True)
        app._ai.query.assert_not_called()
        app._dispatch_response.assert_not_called()
        self.assertNotIn("private OCR", app._last_screen_text)

    def test_ambient_provider_receives_generation_check_and_stale_result_is_dropped(self) -> None:
        app = self._app()

        class GuardedAI:
            def __init__(self) -> None:
                self.invoked = False
                self.provider_attempted = False

            def query(self, *, provider_authorization, **_kwargs):
                self.invoked = True
                app._capabilities.begin_compact_transition()
                self.provider_attempted = bool(provider_authorization())
                return {"command": "idle", "mood": "neutral", "segments": []}

        guarded = GuardedAI()
        app._ai = guarded
        with patch.object(main, "_SETTINGS", self._runtime_settings()), patch.object(
            main, "get_settings", return_value=self._runtime_settings(),
        ), patch.object(main, "native_error_popup"):
            app._ai_tick(origin="ambient")

        self.assertTrue(guarded.invoked)
        self.assertFalse(guarded.provider_attempted)
        app._dispatch_response.assert_not_called()
        self.assertFalse(app._ai_busy)

    def test_direct_chat_remains_available_in_compact_without_ambient_authorization(self) -> None:
        app = self._app()
        app._capabilities.begin_compact_transition()
        compact = CapabilityPolicy.from_settings(_settings(compact=True))
        generation = app._capabilities.snapshot().generation
        app._capabilities.commit_compact(compact, generation)

        class DirectAI:
            def __init__(self) -> None:
                self.kwargs = None

            def query(self, **kwargs):
                self.kwargs = kwargs
                return {"command": "idle", "mood": "neutral", "segments": []}

        direct = DirectAI()
        app._ai = direct
        with patch.object(main, "_SETTINGS", self._runtime_settings()), patch.object(
            main, "get_settings", return_value=self._runtime_settings(),
        ):
            app._ai_tick("hello", origin="user")

        self.assertIsNotNone(direct.kwargs)
        self.assertNotIn("provider_authorization", direct.kwargs)
        app._dispatch_response.assert_called_once()

    def test_reminder_followup_carries_its_capability_generation_into_provider_ui(self) -> None:
        app = self._app()
        callbacks = []
        app._ai = object()
        app._ai_query = MagicMock(return_value=None)
        authorization = app._capabilities.authorize(
            command_handlers.capability_for_command("set_reminder"),
        )
        response = {
            "seconds": 1,
            "reminder_text": "tea",
            command_handlers._CAPABILITY_AUTHORIZATION: authorization,
        }
        ctx = DispatchCtx(None, "neutral", [], False)
        settings = self._runtime_settings(enable_streaming=True)
        with patch.object(main, "_SETTINGS", settings), patch.object(
            main, "get_settings", return_value=settings,
        ), patch.object(
            command_handlers,
            "set_reminder",
            side_effect=lambda _seconds, _text, callback, **_kwargs: callbacks.append(callback),
        ):
            command_handlers.handle_set_reminder(app, response, ctx)
            callbacks[0]("tea")

        current = app._ai_query.call_args.kwargs["result_is_current"]
        self.assertTrue(current())
        app._capabilities.begin_compact_transition()
        self.assertFalse(current())

    def test_terminal_explain_carries_sentinel_generation_into_provider(self) -> None:
        app = self._app()
        authorization = app._capabilities.authorize(
            Capability.TERMINAL_SENTINEL,
        )
        self.assertIsNotNone(authorization)

        settings = self._runtime_settings()
        with patch.object(main, "_SETTINGS", settings), patch.object(
            main, "get_settings", return_value=settings,
        ):
            app._ai_tick(
                "Explain this confirmed terminal error",
                origin="terminal_sentinel",
                capability_authorization=authorization,
            )
            provider_authorization = app._ai.query.call_args.kwargs[
                "provider_authorization"
            ]
            self.assertTrue(provider_authorization())
            app._ai.query.reset_mock()
            app._dispatch_response.reset_mock()
            app._capabilities.begin_compact_transition()
            app._ai_tick(
                "Explain this confirmed terminal error",
                origin="terminal_sentinel",
                capability_authorization=authorization,
            )

        app._ai.query.assert_not_called()
        app._dispatch_response.assert_not_called()


if __name__ == "__main__":
    unittest.main()
