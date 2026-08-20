"""Focused integration regressions for Agetha Polyglot Presence."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agetha.app_config import AppSettings, validate_config_value  # noqa: E402
from agetha.commands.command_guard import CommandGuard  # noqa: E402
from agetha.commands.command_handlers import (  # noqa: E402
    DispatchCtx,
    HANDLERS,
    dispatch,
)
from agetha.core.ai_engine import (  # noqa: E402
    AIEngine,
    FEW_SHOTS,
    FEW_SHOTS_FASTER,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_FASTER,
)
from agetha.core.observation_bus import ObservationBus  # noqa: E402
from agetha.core.capabilities import CapabilityController, CapabilityPolicy  # noqa: E402
from agetha.core.request_context import (  # noqa: E402
    REQUEST_ORIGINS,
    render_request_message,
)
from agetha.features.terminal_sentinel import (  # noqa: E402
    TerminalSentinel,
    TerminalSentinelConfig,
)
from agetha.platform.screen_monitoring import CapturedFrame  # noqa: E402
from agetha.platform.unicode_typing import TypingPreview, UnicodeTypeResult  # noqa: E402


def _engine(settings: AppSettings | None = None) -> AIEngine:
    engine = AIEngine.__new__(AIEngine)
    engine._command_execution_enabled = True
    engine._app_settings = settings or AppSettings({
        "ENABLE_UNICODE_TYPING": "yes",
        "ENABLE_WINDOW_CONTROL": "yes",
        "ENABLE_WEB_RAG": "yes",
        "ENABLE_GLITCH_EFFECTS": "yes",
    })
    return engine


class TestNaturalMultilingualContract(unittest.TestCase):
    def test_both_prompts_contain_language_and_exact_data_boundary(self) -> None:
        for prompt in (SYSTEM_PROMPT, SYSTEM_PROMPT_FASTER):
            with self.subTest(prompt=prompt[:20]):
                self.assertIn("Mirror the user's current language", prompt)
                self.assertIn("conversational register", prompt)
                self.assertIn("exact", prompt.casefold())
        self.assertIn("Do not translate or transliterate", SYSTEM_PROMPT)
        self.assertIn("personality guidance, not an output filter", SYSTEM_PROMPT)

    def test_examples_are_multilingual_vectors_not_a_declared_preference(self) -> None:
        examples = json.dumps(FEW_SHOTS + FEW_SHOTS_FASTER, ensure_ascii=False)
        self.assertIn("hello", examples.casefold())
        self.assertIn("สวัสดี", examples)
        self.assertNotIn("preferred language", examples.casefold())

    def test_type_text_and_unrelated_dialogue_keep_exact_formal_thai(self) -> None:
        exact = "  ขอบคุณครับ\u0301  "
        parsed = _engine()._parse(json.dumps({
            "command": "type_text",
            "text": exact,
            "mode": "auto",
            "speed": "normal",
            "restore_clipboard": True,
            "segments": [],
        }, ensure_ascii=False))
        self.assertEqual(parsed["text"], exact)

        spoken = _engine()._parse(json.dumps({
            "command": "speak",
            "segments": [{"text": "เอกสารเขียนว่า ขอบคุณครับ", "pause": 0}],
        }, ensure_ascii=False))
        self.assertEqual(spoken["segments"][0]["text"], "เอกสารเขียนว่า ขอบคุณครับ")

    def test_unknown_typing_options_use_documented_safe_defaults(self) -> None:
        parsed = _engine()._parse(json.dumps({
            "command": "type_text",
            "text": "สวัสดี",
            "mode": "mystery",
            "speed": "warp",
            "segments": [],
        }, ensure_ascii=False))
        self.assertEqual(parsed["mode"], "auto")
        self.assertEqual(parsed["speed"], "normal")

    def test_type_text_payload_is_omitted_from_history_and_conversation_log(self) -> None:
        secret = "AKIAABCDEFGHIJKLMNOP"
        raw = json.dumps({
            "command": "type_text",
            "text": secret,
            "mode": "auto",
            "speed": "normal",
            "restore_clipboard": True,
            "segments": [{"text": "พร้อมพิมพ์แล้ว", "pause": 0.0}],
        }, ensure_ascii=False)
        engine = _engine()
        result = engine._parse(raw)
        engine._history = []
        engine.HISTORY_LIMIT = 6
        engine._fast_runtime_enabled = lambda: False
        with tempfile.TemporaryDirectory() as folder:
            engine._conversation_path = Path(folder) / "conversation.txt"
            engine._record_profile_response(
                SimpleNamespace(record_history=True, history_stub=""),
                f'User: "type {secret}"',
                raw,
                result,
            )
            persisted = engine._conversation_path.read_text(encoding="utf-8")
        retained = json.dumps(engine._history, ensure_ascii=False)
        self.assertNotIn(secret, retained)
        self.assertNotIn(secret, persisted)
        self.assertIn("exact payload omitted", retained)


class TestTypedSettingsAndOrigins(unittest.TestCase):
    def test_new_settings_are_typed_bounded_and_safely_defaulted(self) -> None:
        defaults = AppSettings({})
        self.assertTrue(defaults.enable_unicode_typing)
        self.assertEqual(defaults.unicode_typing_mode, "auto")
        self.assertTrue(defaults.enable_presence_etiquette)
        self.assertFalse(defaults.enable_terminal_sentinel)
        self.assertTrue(defaults.enable_senses_panel)

        bounded = AppSettings({
            "UNICODE_TYPING_DELAY_MS": "9999",
            "UNICODE_TYPING_PREVIEW_THRESHOLD": "1",
            "PRESENCE_DISMISS_COOLDOWN_SEC": "999999",
            "PRESENCE_RAPID_TYPING_COOLDOWN_SEC": "0",
            "TERMINAL_SENTINEL_COOLDOWN_SEC": "1",
            "UNICODE_TYPING_MODE": "invalid",
        })
        self.assertEqual(bounded.unicode_typing_delay_ms, 500)
        self.assertEqual(bounded.unicode_typing_preview_threshold, 40)
        self.assertEqual(bounded.presence_dismiss_cooldown_sec, 86_400)
        self.assertEqual(bounded.presence_rapid_typing_cooldown_sec, 1)
        self.assertEqual(bounded.terminal_sentinel_cooldown_sec, 10)
        self.assertEqual(bounded.unicode_typing_mode, "auto")
        self.assertTrue(validate_config_value("UNICODE_TYPING_MODE", "paced"))
        self.assertFalse(validate_config_value("UNICODE_TYPING_MODE", "unknown"))

    def test_terminal_sentinel_origin_is_typed_and_labelled(self) -> None:
        self.assertIn("terminal_sentinel", REQUEST_ORIGINS)
        rendered = render_request_message("terminal_sentinel", "Explain this")
        self.assertIn("[internal event: terminal_sentinel]", rendered)


class TestUnicodeCommandIntegration(unittest.TestCase):
    @staticmethod
    def _app() -> MagicMock:
        app = MagicMock()
        app.root.after.side_effect = lambda _delay, callback: callback()
        app._ATTENTION_MOODS = set()
        app._try_short_mood_speak.return_value = False
        app._closing = False
        app._typing_cancel_event = None
        app._typing_operation_lock = threading.Lock()
        return app

    def test_command_guard_description_never_echoes_typed_secret(self) -> None:
        guard = CommandGuard.__new__(CommandGuard)
        secret = "password=hunter2"
        detail = guard.describe("type_text", {"text": secret, "mode": "paste"})
        self.assertNotIn(secret, detail)
        self.assertNotIn("hunter2", detail)
        self.assertIn(str(len(secret)), detail)

    def test_dispatch_still_invokes_guard_for_type_text(self) -> None:
        app = self._app()
        app._guard.check.return_value = True
        preview = TypingPreview(
            target_application="notepad.exe",
            target_window_title="Untitled - Notepad",
            character_count=7,
            line_count=1,
            method="windows-sendinput-unicode",
            clipboard_fallback_may_be_used=True,
            reversible=False,
            reasons=(),
        )

        def prepare(_app, response, _settings):
            response["_typing_preview"] = preview
            return True

        handler = MagicMock(return_value=True)
        settings = AppSettings({
            "COMPACT_MODE": "no",
            "ENABLE_COMMAND_EXECUTION": "yes",
            "ENABLE_UNICODE_TYPING": "yes",
        })
        with patch("agetha.commands.command_handlers.get_settings", return_value=settings), patch(
            "agetha.commands.command_handlers._prepare_unicode_typing", side_effect=prepare,
        ), patch.dict(HANDLERS, {"type_text": handler}):
            dispatch(app, {"command": "type_text", "text": "สวัสดี", "segments": []}, "type it")
        app._guard.check.assert_called_once()
        handler.assert_called_once()

    def test_direct_handler_cannot_bypass_dispatch_policy(self) -> None:
        app = self._app()
        ctx = DispatchCtx("type it", "neutral", [], False, "user")
        settings = AppSettings({
            "COMPACT_MODE": "no",
            "ENABLE_COMMAND_EXECUTION": "yes",
            "ENABLE_UNICODE_TYPING": "yes",
        })
        with patch("agetha.commands.command_handlers.get_settings", return_value=settings), patch(
            "agetha.commands.command_handlers.type_unicode_text",
        ) as os_type:
            self.assertTrue(HANDLERS["type_text"](app, {"text": "secret"}, ctx))
        os_type.assert_not_called()

    def test_legacy_direct_system_helper_is_fail_closed(self) -> None:
        from agetha.commands.system_commands import type_text as legacy_type_text

        self.assertIn("refused", legacy_type_text("must not be entered"))

    def test_explicit_preview_success_is_not_reported_as_an_error(self) -> None:
        app = self._app()
        app._guard.check.return_value = True
        preview = TypingPreview(
            target_application="notepad.exe",
            target_window_title="Untitled - Notepad",
            character_count=7,
            line_count=1,
            method="preview",
            clipboard_fallback_may_be_used=False,
            reversible=False,
            reasons=(),
        )

        def prepare(_app, response, _settings):
            response["_typing_preview"] = preview
            response["_typing_dependencies"] = SimpleNamespace()
            response["_typing_target"] = None
            return True

        outcome = UnicodeTypeResult(
            success=True,
            method="preview",
            characters_requested=7,
            characters_sent=0,
            target_identity="notepad.exe",
            clipboard_restored=None,
            message="Typing preview is ready; no text was entered.",
        )
        settings = AppSettings({
            "COMPACT_MODE": "no",
            "ENABLE_COMMAND_EXECUTION": "yes",
            "ENABLE_UNICODE_TYPING": "yes",
        })
        with patch("agetha.commands.command_handlers.get_settings", return_value=settings), patch(
            "agetha.commands.command_handlers._prepare_unicode_typing", side_effect=prepare,
        ), patch(
            "agetha.commands.command_handlers.type_unicode_text", return_value=outcome,
        ), patch(
            "agetha.commands.command_handlers._start_app_worker",
            side_effect=lambda _app, target, name: target(),
        ):
            dispatch(
                app,
                {"command": "type_text", "text": "preview", "mode": "preview", "segments": []},
                "show preview",
            )
        app._show_op_error.assert_not_called()
        app._show_op_success.assert_called_once()

    def test_disabled_gate_blocks_before_target_capture_or_guard(self) -> None:
        app = self._app()
        settings = AppSettings({
            "ENABLE_COMMAND_EXECUTION": "no",
            "ENABLE_UNICODE_TYPING": "yes",
        })
        with patch("agetha.commands.command_handlers.get_settings", return_value=settings), patch(
            "agetha.commands.command_handlers.default_dependencies",
        ) as dependencies:
            dispatch(app, {"command": "type_text", "text": "x", "segments": []}, "type it")
        dependencies.assert_not_called()
        app._guard.check.assert_not_called()

    def test_sentinel_origin_cannot_execute_model_suggested_command(self) -> None:
        app = self._app()
        handler = MagicMock(return_value=True)
        with patch.dict(HANDLERS, {"run_command": handler}):
            dispatch(
                app,
                {
                    "command": "run_command",
                    "cmd": "echo unsafe",
                    "shutdown": True,
                    "segments": [{"text": "Try this command manually.", "pause": 0.0}],
                },
                None,
                origin="terminal_sentinel",
            )
        handler.assert_not_called()
        app._guard.check.assert_not_called()
        app._speak_and_continue.assert_called_once()
        self.assertFalse(app._speak_and_continue.call_args.args[2])
        self.assertFalse(app._speak_and_continue.call_args.kwargs["allow_audio"])
        app._play_response_motion.assert_not_called()

    def test_sentinel_origin_cannot_open_provider_selected_popup(self) -> None:
        app = self._app()
        dispatch(
            app,
            {
                "command": "popup",
                "popup": ["untrusted focus request"],
                "segments": [{"text": "Passive explanation", "pause": 0.0}],
            },
            None,
            origin="terminal_sentinel",
        )
        app._guard.check.assert_not_called()
        app._speak_and_continue.assert_called_once()
        self.assertFalse(app._speak_and_continue.call_args.kwargs["allow_audio"])


class TestSentinelMainIntegration(unittest.TestCase):
    @staticmethod
    def _full_capabilities() -> CapabilityController:
        return CapabilityController(CapabilityPolicy.from_settings(AppSettings({
            "COMPACT_MODE": "no",
            "ENABLE_AMBIENT_POLLS": "yes",
            "ENABLE_TERMINAL_SENTINEL": "yes",
            "ENABLE_PROCESS_AWARENESS": "no",
        })))

    def test_sentinel_rejection_still_consumes_event_before_ambient_provider(self) -> None:
        import main

        app = main.CompanionApp.__new__(main.CompanionApp)
        app._capabilities = self._full_capabilities()
        app._closing = False
        app._state = app.STATE_IDLE
        app._is_minimized = False
        app._display_width = 1920
        app._display_height = 1080
        app._recent_key_times = []
        app._last_direct_interaction_time = 0.0
        app._presence = None
        app._observation_bus = ObservationBus(max_size=8)
        app._terminal_sentinel = TerminalSentinel(
            TerminalSentinelConfig(
                enabled=True,
                allowed_apps=("Code",),
                minimum_confidence=0,
            ),
            ignore_store_path=None,
        )
        frame = CapturedFrame(
            image=Image.new("RGB", (1200, 800)),
            left=50,
            top=50,
            title="Terminal",
            hwnd=22,
            scope="focused_window",
            process_name="WindowsTerminal.exe",
            process_id=42,
        )
        app._screen = SimpleNamespace(last_capture_metadata=frame, _own_hwnd=11)
        event = SimpleNamespace(
            category="py_runtime",
            label="Python runtime error",
            snippet="Traceback (most recent call last): TypeError: bad value",
            severity="error",
            confidence=99,
            cooldown_seconds=30,
        )
        self.assertTrue(app._evaluate_terminal_sentinel_events([event], event.snippet))

    def test_validated_sentinel_event_ends_ambient_turn_before_provider(self) -> None:
        import main

        app = main.CompanionApp.__new__(main.CompanionApp)
        app._capabilities = self._full_capabilities()
        app._process_awareness = None
        app._closing = False
        app._state = app.STATE_IDLE
        app._is_minimized = False
        app._display_width = 1920
        app._display_height = 1080
        app._recent_key_times = []
        app._last_direct_interaction_time = 0.0
        app._last_observed_app_key = None
        app._last_safe_scan_time = None
        app._last_screen_text = ""
        app._presence = None
        app._observation_bus = ObservationBus(max_size=16)
        app._terminal_sentinel = TerminalSentinel(
            TerminalSentinelConfig(
                enabled=True,
                allowed_apps=("WindowsTerminal",),
                cooldown_seconds=120,
                minimum_confidence=0,
            ),
            ignore_store_path=None,
        )
        event = SimpleNamespace(
            category="py_runtime",
            label="Python runtime error",
            snippet="Traceback (most recent call last): TypeError: bad value",
            severity="error",
            confidence=99,
            cooldown_seconds=30,
        )
        frame = CapturedFrame(
            image=Image.new("RGB", (1200, 800)),
            left=50,
            top=50,
            title="Terminal",
            hwnd=22,
            scope="focused_window",
            process_name="WindowsTerminal.exe",
            process_id=42,
        )
        screen = MagicMock()
        screen.automatic_capture_supported = True
        screen.capture_text.return_value = event.snippet
        screen.last_monitor_status = "ocr_complete"
        screen.last_new_pattern_events = [event]
        screen.last_pattern_matches = [event]
        screen.last_word_positions = []
        screen.last_capture_metadata = frame
        screen._own_hwnd = 11
        screen._get_own_hwnd.return_value = 11
        screen.get_active_window_title.return_value = "Terminal"
        screen.redact_for_external_context.side_effect = lambda text: text
        app._screen = screen
        app._ai = MagicMock()
        app._ai_tick_lock = threading.Lock()
        app._ai_busy = False
        app._ai_busy_noninterruptible = False
        app._ai_operation_token = None
        app._speech_active = False
        app._pending_user_message = None
        app._pending_user_origin = "user"
        app._post_ai_tick_callbacks = []
        app._deferred_ai_callbacks_inflight = False
        app._cancel_event = threading.Event()
        app.root = MagicMock()
        app.root.after.side_effect = lambda _delay, callback: callback()
        app._reschedule_screen_poll = MagicMock()
        app._drain_pending_user_message = MagicMock()
        app._show_terminal_sentinel_notification = MagicMock()

        app._ai_tick(origin="ambient")

        app._ai.query.assert_not_called()
        app._ai.query_streaming.assert_not_called()
        app._show_terminal_sentinel_notification.assert_called_once()
        self.assertEqual(app._observation_bus.peek()[0].kind.value, "app_focused")


if __name__ == "__main__":
    unittest.main(verbosity=2)
