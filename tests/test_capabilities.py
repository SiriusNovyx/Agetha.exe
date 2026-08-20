"""Deterministic Compact/Full capability-policy tests."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agetha.app_config import (  # noqa: E402
    AppSettings,
    FAST_MODE_OVERRIDES,
    default_config_dict,
    render_config_document,
    validate_config_value,
)
from agetha.core.fast_mode_profile import FAST_MODE_FORBIDDEN_KEYS  # noqa: E402
from agetha.core.capabilities import (  # noqa: E402
    Capability,
    CapabilityController,
    CapabilityPolicy,
    CapabilityProfile,
    DecisionReason,
    capability_for_command,
)


ADVANCED_GATES = {
    Capability.TERMINAL_SENTINEL: "ENABLE_TERMINAL_SENTINEL",
    Capability.PROCESS_AWARENESS: "ENABLE_PROCESS_AWARENESS",
    Capability.COMPUTER_USE: "ENABLE_COMPUTER_USE",
    Capability.COMPUTER_PLANNER: "ENABLE_COMPUTER_USE",
    Capability.RECOVERY_PLANNER: "ENABLE_COMPUTER_USE",
    Capability.OS_TYPING: "ENABLE_UNICODE_TYPING",
    Capability.APP_CONTROL: "ENABLE_COMMAND_EXECUTION",
    Capability.BACKGROUND_SENSING: "ENABLE_AMBIENT_POLLS",
    Capability.ADVANCED_OS_INTEGRATION: "ENABLE_COMMAND_EXECUTION",
}


class TestCapabilityProfileConfiguration(unittest.TestCase):
    def test_missing_and_yes_are_compact_while_no_is_full(self) -> None:
        self.assertTrue(AppSettings({}).compact_mode)
        self.assertEqual(CapabilityPolicy.from_settings(AppSettings({})).profile,
                         CapabilityProfile.COMPACT)
        self.assertEqual(CapabilityPolicy.from_settings(
            AppSettings({"COMPACT_MODE": "yes"})).profile, CapabilityProfile.COMPACT)
        self.assertEqual(CapabilityPolicy.from_settings(
            AppSettings({"COMPACT_MODE": "no"})).profile, CapabilityProfile.FULL)

    def test_fast_mode_cannot_override_compact_mode(self) -> None:
        self.assertNotIn("COMPACT_MODE", FAST_MODE_OVERRIDES)
        self.assertIn("COMPACT_MODE", FAST_MODE_FORBIDDEN_KEYS)

    def test_environment_cannot_bypass_disk_backed_consent(self) -> None:
        from agetha import app_config

        config = {"COMPACT_MODE": "yes"}
        with patch.object(app_config, "ENV_PATH") as env_path:
            env_path.exists.return_value = True
            env_path.read_text.return_value = "COMPACT_MODE=no\n"
            app_config._load_env_overrides(config)
        self.assertEqual(config["COMPACT_MODE"], "yes")

    def test_compact_mode_is_typed_default_and_comment_preserving(self) -> None:
        self.assertEqual(default_config_dict()["COMPACT_MODE"], "yes")
        self.assertTrue(validate_config_value("COMPACT_MODE", "no"))
        self.assertFalse(validate_config_value("COMPACT_MODE", "perhaps"))
        original = "# keep me\nUNKNOWN_PLUGIN_FLAG = custom\nCOMPACT_MODE = yes\n"
        rendered = render_config_document(original, {"COMPACT_MODE": "no"})
        self.assertIn("# keep me", rendered)
        self.assertIn("UNKNOWN_PLUGIN_FLAG = custom", rendered)
        self.assertIn("COMPACT_MODE = no", rendered)


class TestCapabilityMatrix(unittest.TestCase):
    def test_core_capabilities_are_allowed_in_both_profiles(self) -> None:
        for compact in ("yes", "no"):
            policy = CapabilityPolicy.from_settings(AppSettings({
                "COMPACT_MODE": compact,
                "ENABLE_WEB_RAG": "yes",
                "ENABLE_AGENT_CONTINUATION": "yes",
            }))
            for capability in (
                Capability.CHAT,
                Capability.MEMORY,
                Capability.EMOTION_PERSONALITY,
                Capability.WEB_RAG,
                Capability.READ_ONLY_CONTINUATION,
            ):
                with self.subTest(compact=compact, capability=capability):
                    self.assertTrue(policy.decision(capability).allowed)

    def test_compact_overrides_every_advanced_individual_gate(self) -> None:
        raw = {"COMPACT_MODE": "yes"}
        raw.update({key: "yes" for key in ADVANCED_GATES.values()})
        policy = CapabilityPolicy.from_settings(AppSettings(raw))
        for capability in ADVANCED_GATES:
            with self.subTest(capability=capability):
                decision = policy.decision(capability)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, DecisionReason.COMPACT_MODE)
        self.assertEqual(
            policy.decision(Capability.ADVANCED_UI).reason,
            DecisionReason.COMPACT_MODE,
        )

    def test_full_still_respects_each_individual_gate(self) -> None:
        for capability, key in ADVANCED_GATES.items():
            with self.subTest(capability=capability, configured=True):
                enabled = CapabilityPolicy.from_settings(AppSettings({
                    "COMPACT_MODE": "no", key: "yes",
                })).decision(capability)
                self.assertTrue(enabled.allowed)
                self.assertEqual(enabled.reason, DecisionReason.ALLOWED)
            with self.subTest(capability=capability, configured=False):
                disabled = CapabilityPolicy.from_settings(AppSettings({
                    "COMPACT_MODE": "no", key: "no",
                })).decision(capability)
                self.assertFalse(disabled.allowed)
                self.assertEqual(disabled.reason, DecisionReason.FEATURE_DISABLED)

    def test_web_and_continuation_still_respect_their_feature_gates(self) -> None:
        policy = CapabilityPolicy.from_settings(AppSettings({
            "COMPACT_MODE": "yes",
            "ENABLE_WEB_RAG": "no",
            "ENABLE_AGENT_CONTINUATION": "no",
        }))
        self.assertEqual(policy.decision(Capability.WEB_RAG).reason,
                         DecisionReason.FEATURE_DISABLED)
        self.assertEqual(policy.decision(Capability.READ_ONLY_CONTINUATION).reason,
                         DecisionReason.FEATURE_DISABLED)


class TestCapabilityController(unittest.TestCase):
    @staticmethod
    def _policy(compact: bool) -> CapabilityPolicy:
        return CapabilityPolicy.from_settings(AppSettings({
            "COMPACT_MODE": "yes" if compact else "no",
            "ENABLE_COMMAND_EXECUTION": "yes",
            "ENABLE_UNICODE_TYPING": "yes",
            "ENABLE_COMPUTER_USE": "yes",
            "ENABLE_PROCESS_AWARENESS": "yes",
        }))

    def test_full_entry_remains_compact_until_committed(self) -> None:
        controller = CapabilityController(self._policy(True))
        generation = controller.begin_full_transition()
        self.assertTrue(controller.snapshot().transitioning)
        self.assertFalse(controller.is_allowed(Capability.OS_TYPING))
        controller.commit_full(self._policy(False), generation)
        self.assertFalse(controller.snapshot().transitioning)
        self.assertTrue(controller.is_allowed(Capability.OS_TYPING))

    def test_compact_transition_invalidates_old_effect_token_immediately(self) -> None:
        controller = CapabilityController(self._policy(False))
        token = controller.authorize(Capability.OS_TYPING)
        self.assertIsNotNone(token)
        generation = controller.begin_compact_transition()
        self.assertFalse(controller.is_authorized(token))
        self.assertFalse(controller.is_allowed(Capability.OS_TYPING))
        controller.commit_compact(self._policy(True), generation)
        self.assertFalse(controller.is_authorized(token))

    def test_stale_transition_callbacks_cannot_commit(self) -> None:
        controller = CapabilityController(self._policy(True))
        first = controller.begin_full_transition()
        second = controller.cancel_transition()
        self.assertGreater(second, first)
        self.assertFalse(controller.commit_full(self._policy(False), first))
        self.assertEqual(controller.snapshot().profile, CapabilityProfile.COMPACT)

    def test_cancelled_full_transition_restores_configured_compact_core_gates(self) -> None:
        settings = AppSettings({
            "COMPACT_MODE": "yes",
            "ENABLE_WEB_RAG": "yes",
            "ENABLE_AGENT_CONTINUATION": "yes",
        })
        policy = CapabilityPolicy.from_settings(settings)
        controller = CapabilityController(policy)
        controller.begin_full_transition()
        controller.cancel_transition(policy)
        self.assertTrue(controller.is_allowed(Capability.WEB_RAG))
        self.assertTrue(controller.is_allowed(Capability.READ_ONLY_CONTINUATION))

    def test_perform_authorized_rejects_invalid_and_stale_tokens_without_effect(self) -> None:
        controller = CapabilityController(self._policy(False))
        token = controller.authorize(Capability.APP_CONTROL)
        self.assertIsNotNone(token)
        generation = controller.begin_compact_transition()
        controller.commit_compact(self._policy(True), generation)
        effects: list[str] = []

        self.assertEqual(
            controller.perform_authorized(token, lambda: effects.append("launched")),
            (False, None),
        )
        self.assertEqual(
            controller.perform_authorized(object(), lambda: effects.append("launched")),
            (False, None),
        )
        self.assertEqual(effects, [])

    def test_perform_authorized_serializes_effect_before_transition_start(self) -> None:
        controller = CapabilityController(self._policy(False))
        token = controller.authorize(Capability.APP_CONTROL)
        self.assertIsNotNone(token)
        effect_started = threading.Event()
        release_effect = threading.Event()
        transition_started = threading.Event()
        transition_finished = threading.Event()
        result: list[tuple[bool, str | None]] = []

        def effect() -> str:
            effect_started.set()
            self.assertTrue(release_effect.wait(2.0))
            return "launched"

        effect_thread = threading.Thread(
            target=lambda: result.append(controller.perform_authorized(token, effect)),
        )
        effect_thread.start()
        self.assertTrue(effect_started.wait(2.0))

        def downgrade() -> None:
            transition_started.set()
            controller.begin_compact_transition()
            transition_finished.set()

        transition_thread = threading.Thread(target=downgrade)
        transition_thread.start()
        self.assertTrue(transition_started.wait(2.0))
        self.assertFalse(transition_finished.wait(0.05))
        release_effect.set()
        effect_thread.join(2.0)
        transition_thread.join(2.0)

        self.assertFalse(effect_thread.is_alive())
        self.assertFalse(transition_thread.is_alive())
        self.assertEqual(result, [(True, "launched")])
        self.assertTrue(transition_finished.is_set())
        self.assertFalse(controller.is_authorized(token))


class TestCommandCapabilityClassification(unittest.TestCase):
    def test_core_and_read_only_commands_are_not_escalated(self) -> None:
        expected = {
            "speak": Capability.CHAT,
            "idle": Capability.CHAT,
            "search_memory": Capability.MEMORY,
            "search_web": Capability.WEB_RAG,
            "fetch_webpage": Capability.WEB_RAG,
            "read_document": Capability.READ_ONLY_CONTINUATION,
        }
        for command, capability in expected.items():
            with self.subTest(command=command):
                self.assertEqual(capability_for_command(command), capability)

    def test_dispatch_policy_blocks_compact_effect_before_guard_or_preflight(self) -> None:
        from agetha.commands import command_handlers

        app = SimpleNamespace(
            _guard=SimpleNamespace(check=lambda *_a, **_k: self.fail("guard called")),
            _show_op_error=lambda *_a, **_k: None,
            _speak_and_continue=lambda *_a, **_k: None,
            _reschedule_screen_poll=lambda: None,
            _set_state=lambda *_a, **_k: None,
            _ATTENTION_MOODS=set(),
            _try_short_mood_speak=lambda *_a, **_k: False,
            _capabilities=CapabilityController(CapabilityPolicy.from_settings(
                AppSettings({
                    "COMPACT_MODE": "yes",
                    "ENABLE_COMMAND_EXECUTION": "yes",
                    "ENABLE_UNICODE_TYPING": "yes",
                })
            )),
        )
        with patch.object(command_handlers, "get_settings", return_value=AppSettings({
            "COMPACT_MODE": "yes",
            "ENABLE_COMMAND_EXECUTION": "yes",
            "ENABLE_UNICODE_TYPING": "yes",
        })), patch.object(command_handlers, "_prepare_unicode_typing") as preflight:
            command_handlers.dispatch(
                app,
                {"command": "type_text", "text": "must not type", "segments": []},
                "type this",
            )
        preflight.assert_not_called()

    def test_dispatch_allows_compact_web_and_memory_commands(self) -> None:
        from agetha.commands import command_handlers

        settings = AppSettings({
            "COMPACT_MODE": "yes",
            "ENABLE_WEB_RAG": "yes",
            "ENABLE_AGENT_CONTINUATION": "yes",
            "ENABLE_LONGTERM_MEMORY": "yes",
        })
        app = SimpleNamespace(
            _guard=SimpleNamespace(check=lambda *_a, **_k: True),
            _show_op_error=lambda *_a, **_k: self.fail("capability denied"),
            _speak_and_continue=lambda *_a, **_k: None,
            _reschedule_screen_poll=lambda: None,
            _set_state=lambda *_a, **_k: None,
            _ATTENTION_MOODS=set(),
            _try_short_mood_speak=lambda *_a, **_k: False,
            _capabilities=CapabilityController(CapabilityPolicy.from_settings(settings)),
        )
        for command in ("search_web", "search_memory", "read_document"):
            handler = MagicMock(return_value=True)
            with self.subTest(command=command), patch.object(
                command_handlers, "get_settings", return_value=settings,
            ), patch.dict(command_handlers.HANDLERS, {command: handler}):
                command_handlers.dispatch(
                    app, {"command": command, "segments": []}, "look it up",
                )
                handler.assert_called_once()

    def test_effect_authorization_is_invalid_after_downgrade(self) -> None:
        full = AppSettings({
            "COMPACT_MODE": "no",
            "ENABLE_COMMAND_EXECUTION": "yes",
            "ENABLE_UNICODE_TYPING": "yes",
        })
        compact = AppSettings({
            "COMPACT_MODE": "yes",
            "ENABLE_COMMAND_EXECUTION": "yes",
            "ENABLE_UNICODE_TYPING": "yes",
        })
        controller = CapabilityController(CapabilityPolicy.from_settings(full))
        token = controller.authorize(Capability.OS_TYPING)
        self.assertIsNotNone(token)
        dependencies = SimpleNamespace(effect_authorized=lambda: controller.is_authorized(token))
        self.assertTrue(dependencies.effect_authorized())
        generation = controller.begin_compact_transition()
        controller.commit_compact(CapabilityPolicy.from_settings(compact), generation)
        self.assertFalse(dependencies.effect_authorized())

    def test_old_effect_stays_invalid_after_compact_then_full_again(self) -> None:
        def policy(compact: bool) -> CapabilityPolicy:
            return CapabilityPolicy.from_settings(AppSettings({
                "COMPACT_MODE": "yes" if compact else "no",
                "ENABLE_COMMAND_EXECUTION": "yes",
            }))

        controller = CapabilityController(policy(False))
        stale = controller.authorize(Capability.APP_CONTROL)
        self.assertIsNotNone(stale)
        compact_generation = controller.begin_compact_transition()
        controller.commit_compact(policy(True), compact_generation)
        full_generation = controller.begin_full_transition()
        controller.commit_full(policy(False), full_generation)
        self.assertTrue(controller.is_allowed(Capability.APP_CONTROL))
        self.assertFalse(controller.is_authorized(stale))

    def test_guarded_launch_holds_authorization_through_launch_primitive(self) -> None:
        from agetha.commands import command_handlers

        full = AppSettings({
            "COMPACT_MODE": "no",
            "ENABLE_COMMAND_EXECUTION": "yes",
        })
        controller = CapabilityController(CapabilityPolicy.from_settings(full))
        transition_started = threading.Event()
        transition_finished = threading.Event()
        transition_was_blocked: list[bool] = []
        transition_thread: list[threading.Thread] = []

        def launch(_argv: tuple[str, ...]) -> None:
            def downgrade() -> None:
                transition_started.set()
                controller.begin_compact_transition()
                transition_finished.set()

            worker = threading.Thread(target=downgrade)
            transition_thread.append(worker)
            worker.start()
            self.assertTrue(transition_started.wait(2.0))
            transition_was_blocked.append(not transition_finished.wait(0.05))

        app = SimpleNamespace(_capabilities=controller)
        with patch.object(command_handlers, "get_settings", return_value=full):
            launched = command_handlers.guarded_launch_application(
                app,
                ("notepad.exe",),
                guard_approved=True,
                launcher=launch,
            )
        transition_thread[0].join(2.0)

        self.assertTrue(launched)
        self.assertEqual(transition_was_blocked, [True])
        self.assertFalse(transition_thread[0].is_alive())
        self.assertTrue(transition_finished.is_set())

    def test_normal_unicode_send_holds_authorization_through_input_primitive(self) -> None:
        from agetha.commands import command_handlers
        from agetha.platform.unicode_typing import (
            NativeSendResult,
            TypingPreview,
            TypingTarget,
            UnicodeTypingDependencies,
            utf16_code_units,
        )

        full = AppSettings({
            "COMPACT_MODE": "no",
            "ENABLE_COMMAND_EXECUTION": "yes",
            "ENABLE_UNICODE_TYPING": "yes",
            "UNICODE_TYPING_MODE": "unicode",
        })
        controller = CapabilityController(CapabilityPolicy.from_settings(full))
        target = TypingTarget(
            stable_id="win:200:42",
            title="Untitled - Notepad",
            process_name="notepad.exe",
            window_handle=200,
        )
        transition_started = threading.Event()
        transition_finished = threading.Event()
        transition_was_blocked: list[bool] = []
        transition_thread: list[threading.Thread] = []

        def send_native(text: str) -> NativeSendResult:
            def downgrade() -> None:
                transition_started.set()
                controller.begin_compact_transition()
                transition_finished.set()

            worker = threading.Thread(target=downgrade)
            transition_thread.append(worker)
            worker.start()
            self.assertTrue(transition_started.wait(2.0))
            transition_was_blocked.append(not transition_finished.wait(0.05))
            return NativeSendResult(True, len(text), len(utf16_code_units(text)))

        dependencies = UnicodeTypingDependencies(
            platform_name="windows",
            session_type="desktop",
            get_focused_target=lambda: target,
            send_native_unicode=send_native,
        )
        preview = TypingPreview(
            target_application="notepad.exe",
            target_window_title="Untitled - Notepad",
            character_count=5,
            line_count=1,
            method="windows-sendinput-unicode",
            clipboard_fallback_may_be_used=False,
            reversible=False,
            reasons=(),
        )

        def prepare(_app, response, _settings) -> bool:
            response["_typing_dependencies"] = dependencies
            response["_typing_target"] = target
            response["_typing_preview"] = preview
            return True

        app = MagicMock()
        app._capabilities = controller
        app._guard.check.return_value = True
        app._ATTENTION_MOODS = set()
        app._try_short_mood_speak.return_value = False
        app._closing = False
        app._typing_cancel_event = None
        app._typing_operation_lock = threading.Lock()
        app._cancel_event = threading.Event()
        app.root.after.side_effect = lambda _delay, callback: callback()
        with patch.object(command_handlers, "get_settings", return_value=full), patch.object(
            command_handlers, "_prepare_unicode_typing", side_effect=prepare,
        ), patch.object(
            command_handlers,
            "_start_app_worker",
            side_effect=lambda _app, worker, name: worker(),
        ):
            command_handlers.dispatch(
                app,
                {
                    "command": "type_text",
                    "text": "hello",
                    "mode": "unicode",
                    "segments": [],
                },
                "type it",
            )
        transition_thread[0].join(2.0)

        self.assertEqual(transition_was_blocked, [True])
        self.assertFalse(transition_thread[0].is_alive())
        self.assertTrue(transition_finished.is_set())

    def test_downgrade_during_guard_blocks_handler_effect(self) -> None:
        from agetha.commands import command_handlers

        full = AppSettings({
            "COMPACT_MODE": "no",
            "ENABLE_COMMAND_EXECUTION": "yes",
        })
        compact = AppSettings({
            "COMPACT_MODE": "yes",
            "ENABLE_COMMAND_EXECUTION": "yes",
        })
        controller = CapabilityController(CapabilityPolicy.from_settings(full))
        handler = MagicMock(return_value=True)

        def guard_then_downgrade(*_args, **_kwargs):
            generation = controller.begin_compact_transition()
            controller.commit_compact(CapabilityPolicy.from_settings(compact), generation)
            return True

        app = SimpleNamespace(
            _guard=SimpleNamespace(check=guard_then_downgrade),
            _show_op_error=lambda *_a, **_k: None,
            _speak_and_continue=lambda *_a, **_k: None,
            _reschedule_screen_poll=lambda: None,
            _set_state=lambda *_a, **_k: None,
            _ATTENTION_MOODS=set(),
            _try_short_mood_speak=lambda *_a, **_k: False,
            _capabilities=controller,
        )
        with patch.object(command_handlers, "get_settings", return_value=full), patch.dict(
            command_handlers.HANDLERS, {"open_app": handler},
        ):
            command_handlers.dispatch(
                app, {"command": "open_app", "app": "notepad.exe", "segments": []},
                "open it",
            )
        handler.assert_not_called()


class TestDelayedCapabilityOwnership(unittest.TestCase):
    @staticmethod
    def _full_settings() -> AppSettings:
        return AppSettings({
            "COMPACT_MODE": "no",
            "ENABLE_COMMAND_EXECUTION": "yes",
            "ENABLE_WINDOW_CONTROL": "yes",
        })

    @staticmethod
    def _app(settings: AppSettings):
        controller = CapabilityController(CapabilityPolicy.from_settings(settings))
        notifications: list[str] = []
        provider_calls: list[str] = []
        root = SimpleNamespace(
            winfo_id=lambda: 101,
            after=lambda _delay, callback: callback(),
        )
        app = SimpleNamespace(
            root=root,
            _guard=SimpleNamespace(check=lambda *_a, **_k: True),
            _show_op_error=lambda *_a, **_k: None,
            _show_op_success=lambda *_a, **_k: None,
            _speak_and_continue=lambda *_a, **_k: None,
            _reschedule_screen_poll=lambda: None,
            _set_state=lambda *_a, **_k: None,
            _ATTENTION_MOODS=set(),
            _try_short_mood_speak=lambda *_a, **_k: False,
            _capabilities=controller,
            _subtitle=SimpleNamespace(
                show_message=lambda message, _color: notifications.append(message),
            ),
            _ai=object(),
            _ai_query=lambda message, **_kwargs: provider_calls.append(message),
            pick_window_sync=lambda matches: matches[0][0] if matches else None,
        )
        return app, controller, notifications, provider_calls

    def test_delayed_app_control_workers_reject_stale_dispatch_generation(self) -> None:
        from agetha.commands import command_handlers

        cases = (
            ("target_window_close", {"target_app": "Notepad"}),
            ("force_close", {"app": "notepad.exe"}),
            ("target_window_move", {"target_app": "Notepad", "x": 10, "y": 20}),
            (
                "target_window_resize",
                {"target_app": "Notepad", "x": 10, "y": 20,
                 "width": 640, "height": 480},
            ),
        )
        settings = self._full_settings()
        for command, arguments in cases:
            with self.subTest(command=command):
                app, controller, _notifications, _provider_calls = self._app(settings)
                workers: list[object] = []
                effects: list[str] = []

                def operate(*_args, **_kwargs):
                    runner = _kwargs.get("effect_runner")
                    if callable(runner):
                        performed, result = runner(
                            lambda: effects.append(command) or True,
                        )
                        return bool(performed and result), "operation completed"
                    effects.append(command)
                    return True, "operation completed"

                response = {"command": command, "segments": [], **arguments}
                with patch.object(
                    command_handlers, "get_settings", return_value=settings,
                ), patch.object(
                    command_handlers,
                    "_start_app_worker",
                    side_effect=lambda _app, worker, _name: workers.append(worker),
                ), patch.object(
                    command_handlers,
                    "resolve_target_hwnd",
                    return_value=(202, "Untitled - Notepad"),
                ), patch.object(
                    command_handlers, "close_window", side_effect=operate,
                ), patch.object(
                    command_handlers, "kill_process_by_hwnd", side_effect=operate,
                ), patch.object(
                    command_handlers, "move_window", side_effect=operate,
                ), patch.object(
                    command_handlers, "resize_window", side_effect=operate,
                ), patch.object(
                    command_handlers,
                    "kill_process_by_name",
                    side_effect=lambda *_args: effects.append(command) or (True, "killed"),
                ), patch.object(
                    command_handlers, "IS_WINDOWS", True,
                ), patch.object(
                    command_handlers, "IS_LINUX", False,
                ):
                    command_handlers.dispatch(app, response, "do it")
                    self.assertEqual(len(workers), 1)
                    controller.begin_compact_transition()
                    workers[0]()
                    self.assertEqual(effects, [])

    def test_target_worker_holds_authorization_through_os_effect(self) -> None:
        from agetha.commands import command_handlers

        settings = self._full_settings()
        app, controller, _notifications, _provider_calls = self._app(settings)
        workers: list[object] = []
        transition_started = threading.Event()
        transition_finished = threading.Event()
        transition_was_blocked: list[bool] = []
        transition_threads: list[threading.Thread] = []

        def operate() -> bool:
            def downgrade() -> None:
                transition_started.set()
                controller.begin_compact_transition()
                transition_finished.set()

            worker = threading.Thread(target=downgrade)
            transition_threads.append(worker)
            worker.start()
            self.assertTrue(transition_started.wait(2.0))
            transition_was_blocked.append(not transition_finished.wait(0.05))
            return True

        def move(_hwnd, _x, _y, *, effect_runner=None):
            self.assertTrue(callable(effect_runner))
            performed, moved = effect_runner(operate)
            return bool(performed and moved), "operation completed"

        with patch.object(
            command_handlers, "get_settings", return_value=settings,
        ), patch.object(
            command_handlers,
            "_start_app_worker",
            side_effect=lambda _app, worker, _name: workers.append(worker),
        ), patch.object(
            command_handlers,
            "resolve_target_hwnd",
            return_value=(202, "Untitled - Notepad"),
        ), patch.object(
            command_handlers, "move_window", side_effect=move,
        ), patch.object(
            command_handlers, "IS_WINDOWS", True,
        ), patch.object(
            command_handlers, "IS_LINUX", False,
        ):
            command_handlers.dispatch(
                app,
                {
                    "command": "target_window_move",
                    "target_app": "Notepad",
                    "x": 10,
                    "y": 20,
                    "segments": [],
                },
                "move it",
            )
            self.assertEqual(len(workers), 1)
            workers[0]()

        transition_threads[0].join(2.0)
        self.assertEqual(transition_was_blocked, [True])
        self.assertFalse(transition_threads[0].is_alive())
        self.assertTrue(transition_finished.is_set())

    def test_target_selection_does_not_hold_transition_lock_and_stale_effect_is_denied(self) -> None:
        from agetha.commands import command_handlers

        settings = self._full_settings()
        app, controller, _notifications, _provider_calls = self._app(settings)
        workers: list[object] = []
        selected: list[str] = []
        closed: list[int] = []

        def resolve(*_args, **_kwargs):
            selected.append("Notepad")
            controller.begin_compact_transition()
            return 202, "Untitled - Notepad"

        with patch.object(
            command_handlers, "get_settings", return_value=settings,
        ), patch.object(
            command_handlers,
            "_start_app_worker",
            side_effect=lambda _app, worker, _name: workers.append(worker),
        ), patch.object(
            command_handlers, "resolve_target_hwnd", side_effect=resolve,
            create=True,
        ), patch.object(
            command_handlers,
            "close_window",
            side_effect=lambda hwnd: closed.append(hwnd) or (True, "close sent"),
        ), patch.object(
            command_handlers, "IS_WINDOWS", True,
        ), patch.object(
            command_handlers, "IS_LINUX", False,
        ):
            command_handlers.dispatch(
                app,
                {
                    "command": "target_window_close",
                    "target_app": "Notepad",
                    "segments": [],
                },
                "close it",
            )
            workers[0]()

        self.assertEqual(selected, ["Notepad"])
        self.assertEqual(closed, [])

    def test_delayed_self_window_move_rejects_stale_dispatch_generation(self) -> None:
        from agetha.commands import command_handlers

        settings = self._full_settings()
        app, controller, _notifications, _provider_calls = self._app(settings)
        callbacks: list[object] = []
        geometry_writes: list[str] = []

        def animate(_x, _y, *, effect_runner=None):
            self.assertTrue(callable(effect_runner))
            first, _result = effect_runner(lambda: geometry_writes.append("first"))
            self.assertTrue(first)
            controller.begin_compact_transition()
            second, _result = effect_runner(lambda: geometry_writes.append("second"))
            self.assertFalse(second)

        app.animate_geometry = animate

        with patch.object(
            command_handlers, "get_settings", return_value=settings,
        ), patch.object(
            command_handlers,
            "_schedule_app_ui",
            side_effect=lambda _app, callback: callbacks.append(callback),
        ):
            command_handlers.dispatch(
                app,
                {
                    "command": "target_window_move",
                    "target_app": "Agetha",
                    "x": 10,
                    "y": 20,
                    "segments": [],
                },
                "move yourself",
            )
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()

        self.assertEqual(geometry_writes, ["first"])

    def test_delayed_target_result_ui_rejects_stale_dispatch_generation(self) -> None:
        from agetha.commands import command_handlers

        settings = self._full_settings()
        app, controller, _notifications, _provider_calls = self._app(settings)
        workers: list[object] = []
        callbacks: list[object] = []
        delivered: list[str] = []
        app._show_op_success = lambda message: delivered.append(message)

        def move(_hwnd, _x, _y, *, effect_runner=None):
            performed, moved = effect_runner(lambda: True)
            return bool(performed and moved), "moved"

        with patch.object(
            command_handlers, "get_settings", return_value=settings,
        ), patch.object(
            command_handlers,
            "_start_app_worker",
            side_effect=lambda _app, worker, _name: workers.append(worker),
        ), patch.object(
            command_handlers,
            "_schedule_app_ui",
            side_effect=lambda _app, callback: callbacks.append(callback),
        ), patch.object(
            command_handlers,
            "resolve_target_hwnd",
            return_value=(202, "Untitled - Notepad"),
        ), patch.object(
            command_handlers, "move_window", side_effect=move,
        ), patch.object(
            command_handlers, "IS_WINDOWS", True,
        ), patch.object(
            command_handlers, "IS_LINUX", False,
        ):
            command_handlers.dispatch(
                app,
                {
                    "command": "target_window_move",
                    "target_app": "Notepad",
                    "x": 10,
                    "y": 20,
                    "segments": [],
                },
                "move it",
            )
            workers[0]()
            self.assertGreaterEqual(len(callbacks), 1)
            controller.begin_compact_transition()
            for callback in callbacks:
                callback()

        self.assertEqual(delivered, [])

    def test_direct_filesystem_effect_holds_dispatch_authorization(self) -> None:
        from agetha.commands import command_handlers

        settings = self._full_settings()
        app, controller, _notifications, _provider_calls = self._app(settings)
        transition_started = threading.Event()
        transition_finished = threading.Event()
        transition_was_blocked: list[bool] = []
        transition_threads: list[threading.Thread] = []

        def make_directory(_path, *, exist_ok=False) -> None:
            self.assertTrue(exist_ok)

            def downgrade() -> None:
                transition_started.set()
                controller.begin_compact_transition()
                transition_finished.set()

            worker = threading.Thread(target=downgrade)
            transition_threads.append(worker)
            worker.start()
            self.assertTrue(transition_started.wait(2.0))
            transition_was_blocked.append(not transition_finished.wait(0.05))

        with patch.object(
            command_handlers, "get_settings", return_value=settings,
        ), patch.object(command_handlers.os, "makedirs", side_effect=make_directory):
            command_handlers.dispatch(
                app,
                {
                    "command": "create_folder",
                    "path": "authorized-folder",
                    "segments": [],
                },
                "create it",
            )

        transition_threads[0].join(2.0)
        self.assertEqual(transition_was_blocked, [True])
        self.assertFalse(transition_threads[0].is_alive())
        self.assertTrue(transition_finished.is_set())

    def test_stale_direct_filesystem_handlers_have_no_effect(self) -> None:
        from agetha.commands import command_handlers

        settings = self._full_settings()
        ctx = command_handlers.DispatchCtx("do it", "neutral", [], False)

        def stale_response(command: str, **arguments):
            app, controller, _notifications, _provider_calls = self._app(settings)
            token = controller.authorize(Capability.ADVANCED_OS_INTEGRATION)
            self.assertIsNotNone(token)
            controller.begin_compact_transition()
            return app, {
                "command": command,
                "segments": [],
                command_handlers._CAPABILITY_AUTHORIZATION: token,
                **arguments,
            }

        effects: list[str] = []
        app, response = stale_response("create_folder", path="folder")
        with patch.object(
            command_handlers.os,
            "makedirs",
            side_effect=lambda *_a, **_k: effects.append("create_folder"),
        ):
            command_handlers.HANDLERS["create_folder"](app, response, ctx)

        app, response = stale_response("create_file", file_path="file.txt")
        fake_file = MagicMock()
        fake_file.__enter__.return_value.write.side_effect = (
            lambda *_a, **_k: effects.append("create_file")
        )
        with patch("builtins.open", return_value=fake_file):
            command_handlers.HANDLERS["create_file"](app, response, ctx)

        app, response = stale_response("delete_file", path="file.txt")
        with patch.object(Path, "is_dir", return_value=False), patch.object(
            Path, "exists", return_value=True,
        ), patch.object(
            Path, "unlink", side_effect=lambda *_a, **_k: effects.append("delete_file"),
        ):
            command_handlers.HANDLERS["delete_file"](app, response, ctx)

        app, response = stale_response(
            "rename_file", path="file.txt", new_name="renamed.txt",
        )
        with patch.object(
            Path, "rename", side_effect=lambda *_a, **_k: effects.append("rename_file"),
        ):
            command_handlers.HANDLERS["rename_file"](app, response, ctx)

        app, response = stale_response(
            "write_file", file_path="file.txt", content="data",
        )
        app._ai = SimpleNamespace(
            write_file=lambda *_a, **_k: effects.append("write_file") or "[written]",
        )
        command_handlers.HANDLERS["write_file"](app, response, ctx)

        self.assertEqual(effects, [])

    def test_move_window_animation_rechecks_dispatch_authorization_per_frame(self) -> None:
        from agetha.commands import command_handlers

        settings = self._full_settings()
        app, controller, _notifications, _provider_calls = self._app(settings)
        callbacks: list[object] = []
        geometry_writes: list[str] = []

        def animate(_x, _y, *, effect_runner):
            first, _result = effect_runner(lambda: geometry_writes.append("first"))
            self.assertTrue(first)
            controller.begin_compact_transition()
            second, _result = effect_runner(lambda: geometry_writes.append("second"))
            self.assertFalse(second)

        app.animate_geometry = animate
        app.root.winfo_screenwidth = lambda: 1920
        app.root.winfo_screenheight = lambda: 1080
        app.root.winfo_width = lambda: 420
        app.root.winfo_height = lambda: 520
        app.root.winfo_x = lambda: 100
        app.root.winfo_y = lambda: 100

        with patch.object(
            command_handlers, "get_settings", return_value=settings,
        ), patch.object(
            command_handlers,
            "_schedule_app_ui",
            side_effect=lambda _app, callback: callbacks.append(callback),
        ):
            command_handlers.dispatch(
                app,
                {
                    "command": "move_window",
                    "direction": "left",
                    "segments": [],
                },
                "move left",
            )
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()

        self.assertEqual(geometry_writes, ["first"])

    def test_main_geometry_animation_rechecks_each_scheduled_write(self) -> None:
        import main

        settings = self._full_settings()
        controller = CapabilityController(CapabilityPolicy.from_settings(settings))
        authorization = controller.authorize(Capability.APP_CONTROL)
        self.assertIsNotNone(authorization)
        geometry_writes: list[str] = []
        scheduled: list[object] = []
        root = SimpleNamespace(
            winfo_x=lambda: 0,
            winfo_y=lambda: 0,
            geometry=lambda value: geometry_writes.append(value),
            after=lambda _delay, callback: scheduled.append(callback) or "job",
            after_cancel=lambda _job: None,
        )
        app = main.CompanionApp.__new__(main.CompanionApp)
        app.root = root
        app._closing = False
        app._is_dragging = False
        app._is_minimized = False
        app._geom_anim_job = None
        app._win_x = 0
        app._win_y = 0

        def effect_runner(effect):
            performed, result = controller.perform_authorized(authorization, effect)
            if performed and len(geometry_writes) == 1:
                controller.begin_compact_transition()
            return performed, result

        runtime_settings = SimpleNamespace(
            window_move_smooth=True,
            window_move_duration_ms=1000,
        )
        with patch.object(main, "_SETTINGS", runtime_settings), patch.object(
            main.time, "perf_counter", side_effect=[0.0, 0.0, 0.1],
        ):
            app.animate_geometry(100, 100, effect_runner=effect_runner)
            self.assertEqual(len(scheduled), 1)
            scheduled[0]()

        self.assertEqual(len(geometry_writes), 1)
        self.assertIsNone(app._geom_anim_job)

    def test_reminder_timer_denies_stale_generation_before_ui_or_provider(self) -> None:
        from agetha.commands import command_handlers, system_commands

        settings = self._full_settings()
        app, controller, notifications, provider_calls = self._app(settings)
        timers: list[object] = []

        class FakeTimer:
            def __init__(self, _seconds, callback):
                self.callback = callback
                timers.append(self)

            def start(self):
                return None

        with patch.object(
            command_handlers, "get_settings", return_value=settings,
        ), patch.object(system_commands.threading, "Timer", FakeTimer):
            command_handlers.dispatch(
                app,
                {
                    "command": "set_reminder",
                    "seconds": 1,
                    "reminder_text": "tea",
                    "segments": [],
                },
                "remind me",
            )

        self.assertEqual(len(timers), 1)
        controller.begin_compact_transition()
        timers[0].callback()
        self.assertEqual(notifications, [])
        self.assertEqual(provider_calls, [])

    def test_reminder_timer_preserves_full_mode_behavior_for_current_generation(self) -> None:
        from agetha.commands import command_handlers, system_commands

        settings = self._full_settings()
        app, controller, notifications, provider_calls = self._app(settings)
        timers: list[object] = []
        provider_result_checks: list[object] = []

        def provider_query(message, **kwargs):
            provider_calls.append(message)
            provider_result_checks.append(kwargs.get("result_is_current"))
            return None

        app._ai_query = provider_query

        class FakeTimer:
            def __init__(self, _seconds, callback):
                self.callback = callback
                timers.append(self)

            def start(self):
                return None

        with patch.object(
            command_handlers, "get_settings", return_value=settings,
        ), patch.object(system_commands.threading, "Timer", FakeTimer):
            command_handlers.dispatch(
                app,
                {
                    "command": "set_reminder",
                    "seconds": 1,
                    "reminder_text": "tea",
                    "segments": [],
                },
                "remind me",
            )
            timers[0].callback()

        self.assertEqual(notifications, ["tea"])
        self.assertEqual(provider_calls, ["tea"])
        self.assertEqual(len(provider_result_checks), 1)
        self.assertTrue(callable(provider_result_checks[0]))
        self.assertTrue(provider_result_checks[0]())
        controller.begin_compact_transition()
        self.assertFalse(provider_result_checks[0]())

    def test_advanced_commands_have_one_central_classification(self) -> None:
        expected = {
            "computer_use": Capability.COMPUTER_USE,
            "type_text": Capability.OS_TYPING,
            "open_app": Capability.APP_CONTROL,
            "target_window_move": Capability.APP_CONTROL,
            "force_close": Capability.APP_CONTROL,
            "get_active_app": Capability.PROCESS_AWARENESS,
            "list_running_apps": Capability.PROCESS_AWARENESS,
            "monitor_process": Capability.PROCESS_AWARENESS,
            "create_file": Capability.ADVANCED_OS_INTEGRATION,
            "set_clipboard": Capability.ADVANCED_OS_INTEGRATION,
            "lock_screen": Capability.ADVANCED_OS_INTEGRATION,
        }
        for command, capability in expected.items():
            with self.subTest(command=command):
                self.assertEqual(capability_for_command(command), capability)


if __name__ == "__main__":
    unittest.main()
