from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agetha.app_config import AppSettings
from agetha.commands.command_handlers import (
    guarded_launch_application,
    guarded_type_for_computer_use,
)
from agetha.core.capabilities import Capability, CapabilityController, CapabilityPolicy
from agetha.computer_use.activation import extract_local_activation
from agetha.computer_use.executor import ExecutorDependencies
from agetha.computer_use.integration import (
    _gate_effect_dependencies,
    build_runtime_bundle,
    select_initial_target,
)
from agetha.computer_use.models import LiveTargetState
from agetha.computer_use.runtime import running_application_to_window_identity
from agetha.platform.process_awareness import (
    ProcessIdentity,
    ProcessSnapshot,
    RunningApplication,
)
from agetha.platform.unicode_typing import (
    ClipboardSnapshot,
    NativeSendResult,
    TypingPreview,
    TypingTarget,
    UnicodeTypingDependencies,
)


def _app(
    name: str = "notepad.exe",
    *,
    foreground: bool = True,
    created_at: float | None = 10.0,
    sensitive: bool = False,
) -> RunningApplication:
    return RunningApplication(
        identity=ProcessIdentity(42, name, created_at),
        window_handle=100,
        window_title="private title",
        visible=True,
        foreground=foreground,
        bounds=(10, 20, 500, 400),
        sensitive=sensitive,
    )


def _snapshot(app: RunningApplication | None) -> ProcessSnapshot:
    return ProcessSnapshot(
        foreground=app if app is not None and app.foreground else None,
        visible_apps=(app,) if app is not None else (),
        total_process_count=1 if app is not None else 0,
        captured_at_monotonic=1.0,
    )


def _full_capability_controller() -> CapabilityController:
    return CapabilityController(CapabilityPolicy.from_settings(AppSettings({
        "COMPACT_MODE": "no",
        "ENABLE_COMMAND_EXECUTION": "yes",
    })))


class _Awareness:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.index = 0

    def snapshot(self):
        value = self.snapshots[min(self.index, len(self.snapshots) - 1)]
        self.index += 1
        return value


class _Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def wait(self, seconds):
        self.value += max(0.01, seconds)


class ComputerUseIntegrationTests(unittest.TestCase):
    def test_computer_use_typing_gates_primitives_not_guard_dialog(self) -> None:
        cancelled = threading.Event()
        captured = TypingTarget(
            "win:100:42",
            process_name="notepad.exe",
            window_handle=100,
        )
        native_calls: list[str] = []
        effect_calls: list[str] = []
        dependencies = UnicodeTypingDependencies(
            platform_name="windows",
            session_type="desktop",
            get_focused_target=lambda: captured,
            send_native_unicode=lambda value: (
                native_calls.append(value)
                or NativeSendResult(True, len(value), len(value))
            ),
            read_clipboard=lambda: ClipboardSnapshot(True, "before"),
            write_clipboard=lambda _value: True,
            send_paste_shortcut=lambda: True,
            activate_target=lambda _target: True,
        )

        def guard_check(*_args, **_kwargs) -> bool:
            self.assertEqual(effect_calls, [])
            return True

        def effect_runner(effect):
            effect_calls.append("primitive")
            return True, effect()

        app = SimpleNamespace(
            _guard=SimpleNamespace(check=guard_check),
            _capabilities=_full_capability_controller(),
            _closing=False,
            _cancel_event=threading.Event(),
            _screen=SimpleNamespace(_own_hwnd=None),
        )
        locked = running_application_to_window_identity(_app())
        settings = SimpleNamespace(
            unicode_typing_mode="auto",
            unicode_typing_restore_clipboard=True,
            unicode_typing_preview_threshold=300,
            unicode_typing_delay_ms=0,
        )

        def _prepare(_app_value, response, _settings) -> bool:
            response["_typing_target"] = captured
            response["_typing_preview"] = TypingPreview(
                "notepad.exe", "withheld", 5, 1, "native-unicode", False, False, (),
            )
            response["_typing_dependencies"] = dependencies
            return True

        with patch(
            "agetha.commands.command_handlers.get_settings",
            return_value=settings,
        ), patch(
            "agetha.commands.command_handlers._prepare_unicode_typing",
            side_effect=_prepare,
        ):
            result = guarded_type_for_computer_use(
                app,
                "hello",
                locked,
                cancelled,
                validate_locked_target=lambda _foreground: True,
                effect_runner=effect_runner,
            )

        self.assertTrue(result)
        self.assertTrue(effect_calls)
        self.assertEqual(native_calls, ["hello"])

    def test_target_change_during_typing_guard_has_no_input_or_clipboard_effect(self) -> None:
        cancelled = threading.Event()
        target_valid = {"value": True}
        native_calls: list[str] = []
        clipboard_writes: list[str] = []
        captured = TypingTarget(
            "win:100:42",
            process_name="notepad.exe",
            window_handle=100,
        )
        dependencies = UnicodeTypingDependencies(
            platform_name="windows",
            session_type="desktop",
            get_focused_target=lambda: captured,
            send_native_unicode=lambda value: (
                native_calls.append(value)
                or NativeSendResult(True, len(value), len(value))
            ),
            read_clipboard=lambda: ClipboardSnapshot(True, "before"),
            write_clipboard=lambda value: clipboard_writes.append(value) or True,
            send_paste_shortcut=lambda: True,
            activate_target=lambda _target: True,
        )

        def _guard_then_replace_target(*_args, **_kwargs) -> bool:
            target_valid["value"] = False
            return True

        app = SimpleNamespace(
            _guard=SimpleNamespace(check=_guard_then_replace_target),
            _closing=False,
            _cancel_event=threading.Event(),
            _screen=SimpleNamespace(_own_hwnd=None),
        )
        locked = running_application_to_window_identity(_app())
        self.assertIsNotNone(locked)
        settings = SimpleNamespace(
            unicode_typing_mode="auto",
            unicode_typing_restore_clipboard=True,
            unicode_typing_preview_threshold=300,
            unicode_typing_delay_ms=20,
        )

        def _prepare(_app_value, response, _settings) -> bool:
            response["_typing_target"] = captured
            response["_typing_preview"] = TypingPreview(
                "notepad.exe", "withheld", 6, 1, "native-unicode", False, False, (),
            )
            response["_typing_dependencies"] = dependencies
            return True

        with patch(
            "agetha.commands.command_handlers.get_settings",
            return_value=settings,
        ), patch(
            "agetha.commands.command_handlers._prepare_unicode_typing",
            side_effect=_prepare,
        ):
            result = guarded_type_for_computer_use(
                app,
                "secret",
                locked,
                cancelled,
                validate_locked_target=lambda _foreground: target_valid["value"],
            )

        self.assertFalse(result)
        self.assertEqual(native_calls, [])
        self.assertEqual(clipboard_writes, [])

    def test_target_change_during_typing_preview_has_no_input_or_clipboard_effect(self) -> None:
        cancelled = threading.Event()
        target_valid = {"value": True}
        native_calls: list[str] = []
        clipboard_writes: list[str] = []
        captured = TypingTarget(
            "win:100:42",
            process_name="notepad.exe",
            window_handle=100,
        )
        dependencies = UnicodeTypingDependencies(
            platform_name="windows",
            session_type="desktop",
            get_focused_target=lambda: captured,
            send_native_unicode=lambda value: (
                native_calls.append(value)
                or NativeSendResult(True, len(value), len(value))
            ),
            read_clipboard=lambda: ClipboardSnapshot(True, "before"),
            write_clipboard=lambda value: clipboard_writes.append(value) or True,
            send_paste_shortcut=lambda: True,
            activate_target=lambda _target: True,
        )
        app = SimpleNamespace(
            _guard=SimpleNamespace(check=lambda *_args, **_kwargs: True),
            _closing=False,
            _cancel_event=threading.Event(),
            _screen=SimpleNamespace(_own_hwnd=None),
        )
        locked = running_application_to_window_identity(_app())
        self.assertIsNotNone(locked)
        settings = SimpleNamespace(
            unicode_typing_mode="auto",
            unicode_typing_restore_clipboard=True,
            unicode_typing_preview_threshold=300,
            unicode_typing_delay_ms=20,
        )

        def _prepare(_app_value, response, _settings) -> bool:
            response["_typing_target"] = captured
            response["_typing_preview"] = TypingPreview(
                "notepad.exe",
                "withheld",
                6,
                1,
                "native-unicode",
                False,
                False,
                ("explicit-preview",),
            )
            response["_typing_dependencies"] = dependencies
            return True

        def _approve_then_replace(*_args, **_kwargs) -> bool:
            target_valid["value"] = False
            return True

        with patch(
            "agetha.commands.command_handlers.get_settings",
            return_value=settings,
        ), patch(
            "agetha.commands.command_handlers._prepare_unicode_typing",
            side_effect=_prepare,
        ), patch(
            "agetha.commands.command_handlers._confirm_typing_preview",
            side_effect=_approve_then_replace,
        ):
            result = guarded_type_for_computer_use(
                app,
                "secret",
                locked,
                cancelled,
                validate_locked_target=lambda _foreground: target_valid["value"],
            )

        self.assertFalse(result)
        self.assertEqual(native_calls, [])
        self.assertEqual(clipboard_writes, [])

    def test_stop_after_typing_guard_prevents_preview_and_input(self) -> None:
        cancelled = threading.Event()
        guard = MagicMock()

        def _approve_after_stop(*_args, **_kwargs):
            cancelled.set()
            return True

        guard.check.side_effect = _approve_after_stop
        app = SimpleNamespace(_guard=guard, _closing=False)
        locked = running_application_to_window_identity(_app())
        self.assertIsNotNone(locked)
        settings = SimpleNamespace(
            unicode_typing_mode="auto",
            unicode_typing_restore_clipboard=True,
        )

        def _prepare(_app, response, _settings):
            response["_typing_target"] = TypingTarget(
                "win:100:42",
                process_name="notepad.exe",
                window_handle=100,
            )
            response["_typing_preview"] = TypingPreview(
                "notepad.exe",
                "withheld",
                5,
                1,
                "native-unicode",
                False,
                False,
                ("explicit-preview",),
            )
            response["_typing_dependencies"] = object()
            return True

        with patch(
            "agetha.commands.command_handlers.get_settings",
            return_value=settings,
        ), patch(
            "agetha.commands.command_handlers._prepare_unicode_typing",
            side_effect=_prepare,
        ), patch(
            "agetha.commands.command_handlers._confirm_typing_preview",
        ) as preview, patch(
            "agetha.commands.command_handlers.type_unicode_text",
        ) as type_text:
            result = guarded_type_for_computer_use(
                app,
                "hello",
                locked,
                cancelled,
                validate_locked_target=lambda _foreground: True,
            )

        self.assertFalse(result)
        preview.assert_not_called()
        type_text.assert_not_called()

    def test_cancelled_completion_message_is_discarded_before_ui(self) -> None:
        import main

        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._computer_use_ui_epoch = 1
        app._finish_computer_use_message = MagicMock()
        callbacks = []
        app._schedule_ui = lambda callback: callbacks.append(callback) or callback
        cancelled = threading.Event()

        app._schedule_computer_use_message("done", cancelled, 1)
        cancelled.set()
        app._invalidate_computer_use_ui_delivery()
        callbacks.pop()()

        app._finish_computer_use_message.assert_not_called()

    def test_live_presence_change_blocks_focus_at_effect_boundary(self) -> None:
        allowed = {"value": True}
        focused = []
        target = running_application_to_window_identity(_app())
        self.assertIsNotNone(target)
        assert target is not None
        dependencies = ExecutorDependencies(
            validate_target=lambda value, foreground: LiveTargetState(
                value,
                True,
                foreground,
                True,
            ),
            move_pointer=lambda _x, _y: True,
            click=lambda _x, _y: True,
            double_click=lambda _x, _y: True,
            scroll=lambda _amount, _x, _y: True,
            keypress=lambda _key: True,
            hotkey=lambda _keys: True,
            focus_window=lambda value: focused.append(value) or True,
            guarded_type=lambda _text, _target, _cancel: True,
        )
        gated = _gate_effect_dependencies(
            dependencies,
            feature_gate=lambda: True,
            is_shutdown=lambda: False,
            focus_allowed=lambda: allowed["value"],
        )

        self.assertTrue(gated.focus_window(target))
        allowed["value"] = False
        self.assertFalse(gated.focus_window(target))
        self.assertEqual(focused, [target])

    def test_desktop_effect_holds_capability_authorization_through_input(self) -> None:
        settings = AppSettings({
            "COMPACT_MODE": "no",
            "ENABLE_COMMAND_EXECUTION": "yes",
            "ENABLE_PROCESS_AWARENESS": "yes",
            "ENABLE_COMPUTER_USE": "yes",
        })
        controller = CapabilityController(CapabilityPolicy.from_settings(settings))
        authorization = controller.authorize(Capability.COMPUTER_USE)
        self.assertIsNotNone(authorization)
        transition_started = threading.Event()
        transition_finished = threading.Event()
        transition_was_blocked: list[bool] = []
        transition_threads: list[threading.Thread] = []

        def click(_x: int, _y: int) -> bool:
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

        dependencies = ExecutorDependencies(
            validate_target=lambda value, foreground: LiveTargetState(
                value, True, foreground, True,
            ),
            move_pointer=lambda _x, _y: True,
            click=click,
            double_click=lambda _x, _y: True,
            scroll=lambda _amount, _x, _y: True,
            keypress=lambda _key: True,
            hotkey=lambda _keys: True,
            focus_window=lambda _target: True,
            guarded_type=lambda _text, _target, _cancel: True,
        )
        gated = _gate_effect_dependencies(
            dependencies,
            feature_gate=lambda: True,
            is_shutdown=lambda: False,
            effect_runner=lambda effect: controller.perform_authorized(
                authorization, effect,
            ),
        )

        self.assertTrue(gated.click(10, 20))
        transition_threads[0].join(2.0)
        self.assertEqual(transition_was_blocked, [True])
        self.assertFalse(transition_threads[0].is_alive())
        self.assertTrue(transition_finished.is_set())

    def test_typing_dialog_does_not_hold_capability_effect_lock(self) -> None:
        capability_lock = threading.Lock()
        dialog_entered = threading.Event()
        close_dialog = threading.Event()
        transition_finished = threading.Event()

        def effect_runner(effect):
            with capability_lock:
                return True, effect()

        def guarded_type(_text, _target, _cancel):
            dialog_entered.set()
            close_dialog.wait(2.0)
            return True

        dependencies = ExecutorDependencies(
            validate_target=lambda value, foreground: LiveTargetState(
                value, True, foreground, True,
            ),
            move_pointer=lambda _x, _y: True,
            click=lambda _x, _y: True,
            double_click=lambda _x, _y: True,
            scroll=lambda _amount, _x, _y: True,
            keypress=lambda _key: True,
            hotkey=lambda _keys: True,
            focus_window=lambda _target: True,
            guarded_type=guarded_type,
        )
        gated = _gate_effect_dependencies(
            dependencies,
            feature_gate=lambda: True,
            is_shutdown=lambda: False,
            effect_runner=effect_runner,
        )

        typing_thread = threading.Thread(
            target=lambda: gated.guarded_type("hello", object(), threading.Event()),
        )
        typing_thread.start()
        self.assertTrue(dialog_entered.wait(1.0))

        def transition() -> None:
            with capability_lock:
                transition_finished.set()

        transition_thread = threading.Thread(target=transition)
        transition_thread.start()
        transition_completed_while_dialog_open = transition_finished.wait(0.2)
        close_dialog.set()
        typing_thread.join(2.0)
        transition_thread.join(2.0)

        self.assertTrue(transition_completed_while_dialog_open)
        self.assertFalse(typing_thread.is_alive())
        self.assertFalse(transition_thread.is_alive())

    def test_stale_queued_status_cannot_touch_a_new_session_ui(self) -> None:
        import main

        old = SimpleNamespace(
            session_id="session:old",
            generation=1,
            state=SimpleNamespace(value="running"),
        )
        new = SimpleNamespace(
            session_id="session:new",
            generation=2,
            state=SimpleNamespace(value="running"),
        )
        manager = SimpleNamespace(snapshot=MagicMock(side_effect=(old, new)))
        app = main.CompanionApp.__new__(main.CompanionApp)
        app._closing = False
        app._computer_use = manager
        app._publish_computer_use_snapshot = MagicMock()
        app._show_computer_use_status = MagicMock()
        app._close_computer_use_status = MagicMock()
        callbacks = []
        app._schedule_ui = lambda callback: callbacks.append(callback) or callback

        app._on_computer_use_snapshot(old)
        callbacks.pop()()

        app._publish_computer_use_snapshot.assert_called_once_with(old, "running")
        app._show_computer_use_status.assert_not_called()
        app._close_computer_use_status.assert_not_called()

    def test_canonical_launcher_rechecks_open_app_guard(self) -> None:
        app = SimpleNamespace(
            _guard=SimpleNamespace(check=MagicMock(return_value=True)),
            _capabilities=_full_capability_controller(),
        )
        launched = []

        result = guarded_launch_application(
            app,
            ("notepad.exe",),
            launcher=lambda command: launched.append(command),
        )

        self.assertTrue(result)
        app._guard.check.assert_called_once_with(
            "open_app",
            {"app": "notepad.exe", "app_name": "notepad.exe"},
        )
        self.assertEqual(launched, [("notepad.exe",)])

    def test_canonical_launcher_denial_has_no_launch(self) -> None:
        app = SimpleNamespace(
            _guard=SimpleNamespace(check=MagicMock(return_value=False)),
            _capabilities=_full_capability_controller(),
        )
        launched = []

        self.assertFalse(guarded_launch_application(
            app,
            ("notepad.exe",),
            launcher=lambda command: launched.append(command),
        ))
        self.assertEqual(launched, [])

    def test_stop_during_guard_confirmation_prevents_late_launch(self) -> None:
        cancelled = threading.Event()

        def _approve_after_stop(_command, _response, **_kwargs):
            cancelled.set()
            return True

        app = SimpleNamespace(
            _guard=SimpleNamespace(check=MagicMock(side_effect=_approve_after_stop)),
            _capabilities=_full_capability_controller(),
        )
        launched = []

        self.assertFalse(guarded_launch_application(
            app,
            ("notepad.exe",),
            launcher=lambda command: launched.append(command),
            cancel_check=cancelled.is_set,
        ))
        self.assertEqual(launched, [])

    def test_named_notepad_is_launched_once_then_locked(self) -> None:
        activation = extract_local_activation("Open Notepad and type สวัสดี")
        awareness = _Awareness([_snapshot(None), _snapshot(_app())])
        launched = []
        clock = _Clock()
        result = select_initial_target(
            awareness,
            activation,
            threading.Event(),
            launcher=lambda command: launched.append(command) or True,
            monotonic=clock.monotonic,
            wait=clock.wait,
        )
        self.assertEqual(launched, [("notepad.exe",)])
        self.assertEqual(result.target.process.name.casefold(), "notepad.exe")
        self.assertTrue(result.launched)

    def test_current_foreground_can_be_locked_without_launch(self) -> None:
        activation = extract_local_activation("click the visible button")
        result = select_initial_target(
            _Awareness([_snapshot(_app("demo.exe"))]),
            activation,
            threading.Event(),
            launcher=lambda _command: self.fail("must not launch"),
        )
        self.assertEqual(result.target.process.name, "demo.exe")
        self.assertFalse(result.launched)

    def test_missing_creation_time_fails_closed(self) -> None:
        clock = _Clock()
        result = select_initial_target(
            _Awareness([_snapshot(_app(created_at=None))]),
            extract_local_activation("Open Notepad"),
            threading.Event(),
            launcher=lambda _command: True,
            timeout_seconds=0.2,
            monotonic=clock.monotonic,
            wait=clock.wait,
        )
        self.assertIsNone(result.target)

    def test_sensitive_target_is_never_selected(self) -> None:
        clock = _Clock()
        result = select_initial_target(
            _Awareness([_snapshot(_app(sensitive=True))]),
            extract_local_activation("Open Notepad"),
            threading.Event(),
            launcher=lambda _command: True,
            timeout_seconds=0.2,
            monotonic=clock.monotonic,
            wait=clock.wait,
        )
        self.assertIsNone(result.target)

    def test_cancelled_bootstrap_has_no_launch(self) -> None:
        cancelled = threading.Event()
        cancelled.set()
        calls = []
        result = select_initial_target(
            _Awareness([_snapshot(None)]),
            extract_local_activation("Open Notepad"),
            cancelled,
            launcher=lambda command: calls.append(command) or True,
        )
        self.assertEqual(calls, [])
        self.assertEqual(result.status, "cancelled")

    def test_cancellation_during_snapshot_prevents_focus_or_target_lock(self) -> None:
        cancelled = threading.Event()
        focused = []

        class CancellingAwareness:
            def snapshot(self):
                cancelled.set()
                return _snapshot(_app(foreground=False))

        result = select_initial_target(
            CancellingAwareness(),
            extract_local_activation("Open Notepad"),
            cancelled,
            focus_window=lambda target: focused.append(target) or True,
        )

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(focused, [])

    def test_cancellation_during_snapshot_prevents_launch(self) -> None:
        cancelled = threading.Event()
        launched = []

        class CancellingAwareness:
            def snapshot(self):
                cancelled.set()
                return _snapshot(None)

        result = select_initial_target(
            CancellingAwareness(),
            extract_local_activation("Open Notepad"),
            cancelled,
            launcher=lambda command: launched.append(command) or True,
        )

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(launched, [])

    def test_non_windows_runtime_degrades_without_composition(self) -> None:
        bundle = build_runtime_bundle(
            ai_engine=object(),
            screen_reader=object(),
            process_awareness=object(),
            planner_route="inherit",
            planner_model="",
            reserve_provider=lambda: object(),
            release_provider=lambda _token: None,
            guarded_type=lambda _text, _target, _cancel, _validate: False,
            feature_gate=lambda: True,
            is_shutdown=lambda: False,
            platform_name="linux",
        )
        self.assertIsNone(bundle.manager)
        self.assertFalse(bundle.available)


if __name__ == "__main__":
    unittest.main()
