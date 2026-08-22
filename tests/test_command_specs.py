from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from agetha.commands.specs import (
    ALL_REQUEST_ORIGINS,
    BASE_RISK_BY_COMMAND,
    CAPABILITY_BY_COMMAND,
    COMMAND_NAMES,
    COMMAND_SPECS,
    TRUSTED_EVENT_ORIGINS,
    CommandSpec,
    DispatchKind,
    RiskTier,
    build_command_specs,
    get_command_spec,
    validate_handler_bindings,
)
from agetha.app_config import AppSettings
from agetha.core.capabilities import Capability


EXPECTED_COMMANDS = frozenset({
    "add_task",
    "analyze_screen_deep",
    "change_mood",
    "clear_emotions",
    "clear_memory",
    "complete_task",
    "computer_use",
    "copy_to_clipboard",
    "create_file",
    "create_folder",
    "delete_file",
    "fetch_webpage",
    "force_close",
    "get_active_app",
    "get_clipboard",
    "glitch_overlay",
    "idle",
    "list_dir",
    "list_directory",
    "list_running_apps",
    "list_tasks",
    "lock_screen",
    "monitor_process",
    "move_window",
    "open_app",
    "open_browser",
    "open_file",
    "open_folder",
    "open_settings",
    "open_url",
    "play_emotion_sound",
    "play_sound",
    "play_virus_trivia",
    "popup",
    "read_document",
    "read_file",
    "read_notepad",
    "recycle_bin_status",
    "rename_file",
    "request_path",
    "request_screen_read",
    "restart",
    "run_command",
    "search_files",
    "search_memory",
    "search_web",
    "set_autostart",
    "set_clipboard",
    "set_reminder",
    "set_theme",
    "set_volume",
    "set_wallpaper",
    "show_dialog",
    "show_error_gif",
    "show_notification",
    "shutdown",
    "snap_to_center",
    "speak",
    "system_info",
    "take_screenshot",
    "target_window_close",
    "target_window_move",
    "target_window_resize",
    "type_text",
    "view_dreams",
    "view_emotions",
    "view_memory",
    "wake_user",
    "write_file",
})

EXPECTED_RISK_GROUPS = {
    RiskTier.SAFE: frozenset({
        "add_task", "change_mood", "complete_task", "get_active_app",
        "get_clipboard", "glitch_overlay", "idle", "list_running_apps",
        "list_tasks", "monitor_process", "move_window", "open_browser",
        "open_folder", "open_url", "play_emotion_sound",
        "play_virus_trivia", "popup", "read_document", "read_notepad",
        "recycle_bin_status", "request_path", "request_screen_read",
        "search_memory", "set_reminder", "show_error_gif",
        "show_notification", "snap_to_center", "speak", "system_info",
        "take_screenshot", "view_dreams", "view_emotions", "view_memory",
        "wake_user",
    }),
    RiskTier.CAUTION: frozenset({
        "analyze_screen_deep", "clear_emotions", "clear_memory",
        "computer_use", "copy_to_clipboard", "fetch_webpage", "list_dir",
        "list_directory", "open_app", "open_file", "open_settings",
        "play_sound", "read_file", "search_files", "search_web",
        "set_clipboard", "set_volume", "set_wallpaper", "show_dialog",
        "target_window_close", "target_window_move", "target_window_resize",
        "type_text",
    }),
    RiskTier.DANGER: frozenset({
        "create_file", "create_folder", "delete_file", "force_close",
        "lock_screen", "rename_file", "restart", "run_command",
        "set_autostart", "set_theme", "shutdown", "write_file",
    }),
}

EXPECTED_CAPABILITY_GROUPS = {
    Capability.CHAT: frozenset({"idle", "popup", "speak", "wake_user"}),
    Capability.MEMORY: frozenset({
        "add_task", "clear_memory", "complete_task", "list_tasks",
        "read_notepad", "search_memory", "view_dreams", "view_memory",
    }),
    Capability.EMOTION_PERSONALITY: frozenset({
        "change_mood", "clear_emotions", "view_emotions",
    }),
    Capability.WEB_RAG: frozenset({"fetch_webpage", "search_web"}),
    Capability.READ_ONLY_CONTINUATION: frozenset({
        "list_dir", "list_directory", "read_document", "read_file",
        "recycle_bin_status", "system_info",
    }),
    Capability.PROCESS_AWARENESS: frozenset({
        "get_active_app", "list_running_apps", "monitor_process",
    }),
    Capability.COMPUTER_USE: frozenset({"computer_use"}),
    Capability.OS_TYPING: frozenset({"type_text"}),
    Capability.APP_CONTROL: frozenset({
        "force_close", "open_app", "target_window_close",
        "target_window_move", "target_window_resize",
    }),
    Capability.ADVANCED_OS_INTEGRATION: frozenset({
        "analyze_screen_deep", "copy_to_clipboard", "create_file",
        "create_folder", "delete_file", "get_clipboard", "glitch_overlay",
        "lock_screen", "move_window", "open_browser", "open_file",
        "open_folder", "open_settings", "open_url", "play_emotion_sound",
        "play_sound", "play_virus_trivia", "rename_file", "request_path",
        "request_screen_read", "restart", "run_command", "search_files",
        "set_autostart", "set_clipboard", "set_reminder", "set_theme",
        "set_volume", "set_wallpaper", "show_dialog", "show_error_gif",
        "show_notification", "shutdown", "snap_to_center",
        "take_screenshot", "write_file",
    }),
}

CORE_COMMANDS = frozenset({"idle", "popup", "speak", "wake_user"})
DIRECT_USER_ONLY = frozenset({"analyze_screen_deep", "computer_use"})


class CommandSpecRegistryTests(unittest.TestCase):
    def test_registry_has_one_explicit_spec_for_every_current_command(self) -> None:
        self.assertEqual(COMMAND_NAMES, EXPECTED_COMMANDS)
        self.assertEqual(frozenset(COMMAND_SPECS), EXPECTED_COMMANDS)
        self.assertEqual(len(COMMAND_SPECS), 69)
        self.assertTrue(all(name == spec.name for name, spec in COMMAND_SPECS.items()))

    def test_every_command_has_an_intentional_base_risk(self) -> None:
        actual = {
            risk: frozenset(
                name for name, spec in COMMAND_SPECS.items()
                if spec.base_risk is risk
            )
            for risk in RiskTier
        }
        self.assertEqual(actual, EXPECTED_RISK_GROUPS)
        self.assertEqual(set().union(*actual.values()), EXPECTED_COMMANDS)

    def test_every_command_has_an_intentional_capability(self) -> None:
        actual = {
            capability: frozenset(
                name for name, spec in COMMAND_SPECS.items()
                if spec.capability is capability
            )
            for capability in EXPECTED_CAPABILITY_GROUPS
        }
        self.assertEqual(actual, EXPECTED_CAPABILITY_GROUPS)
        self.assertEqual(set().union(*actual.values()), EXPECTED_COMMANDS)

    def test_dispatch_kind_preserves_real_core_and_handler_ownership(self) -> None:
        actual_core = frozenset(
            name for name, spec in COMMAND_SPECS.items()
            if spec.dispatch_kind is DispatchKind.CORE
        )
        self.assertEqual(actual_core, CORE_COMMANDS)
        for name, spec in COMMAND_SPECS.items():
            with self.subTest(command=name):
                if name in CORE_COMMANDS:
                    self.assertIsNone(spec.handler_key)
                else:
                    self.assertEqual(spec.dispatch_kind, DispatchKind.HANDLER)
                    self.assertEqual(spec.handler_key, name)

    def test_origin_metadata_is_explicit_and_restrictive(self) -> None:
        self.assertEqual(COMMAND_SPECS["idle"].allowed_origins, ALL_REQUEST_ORIGINS)
        self.assertEqual(COMMAND_SPECS["speak"].allowed_origins, ALL_REQUEST_ORIGINS)
        for name, spec in COMMAND_SPECS.items():
            with self.subTest(command=name):
                self.assertTrue(spec.allowed_origins)
                self.assertLessEqual(spec.allowed_origins, ALL_REQUEST_ORIGINS)
                if name in DIRECT_USER_ONLY:
                    self.assertEqual(spec.allowed_origins, frozenset({"user"}))
                elif name not in {"idle", "speak"}:
                    self.assertEqual(spec.allowed_origins, TRUSTED_EVENT_ORIGINS)

    def test_execution_and_command_specific_feature_gate_metadata_is_intentional(self) -> None:
        expected_execution = (
            EXPECTED_CAPABILITY_GROUPS[Capability.COMPUTER_USE]
            | EXPECTED_CAPABILITY_GROUPS[Capability.OS_TYPING]
            | EXPECTED_CAPABILITY_GROUPS[Capability.APP_CONTROL]
            | EXPECTED_CAPABILITY_GROUPS[Capability.ADVANCED_OS_INTEGRATION]
        )
        self.assertEqual(
            frozenset(name for name, spec in COMMAND_SPECS.items() if spec.requires_execution),
            expected_execution,
        )
        expected_gates = {
            "add_task": ("ENABLE_TASKS",),
            "clear_emotions": ("ENABLE_EMOTION_ENGINE",),
            "complete_task": ("ENABLE_TASKS",),
            "computer_use": ("ENABLE_COMPUTER_USE",),
            "force_close": ("ENABLE_WINDOW_CONTROL",),
            "glitch_overlay": ("ENABLE_GLITCH_EFFECTS",),
            "list_tasks": ("ENABLE_TASKS",),
            "search_memory": ("ENABLE_LONGTERM_MEMORY",),
            "set_autostart": ("ENABLE_AUTOSTART_CONTROL",),
            "set_theme": ("ENABLE_THEME_CONTROL",),
            "target_window_close": ("ENABLE_WINDOW_CONTROL",),
            "target_window_move": ("ENABLE_WINDOW_CONTROL",),
            "target_window_resize": ("ENABLE_WINDOW_CONTROL",),
            "type_text": ("ENABLE_UNICODE_TYPING",),
            "view_dreams": ("ENABLE_DREAMS",),
            "view_emotions": ("ENABLE_EMOTION_ENGINE",),
        }
        self.assertEqual(
            {
                name: spec.feature_gates
                for name, spec in COMMAND_SPECS.items()
                if spec.feature_gates
            },
            expected_gates,
        )

    def test_registry_and_specs_are_immutable(self) -> None:
        with self.assertRaises(TypeError):
            COMMAND_SPECS["new_command"] = COMMAND_SPECS["idle"]  # type: ignore[index]
        with self.assertRaises(AttributeError):
            COMMAND_SPECS["idle"].name = "changed"  # type: ignore[misc]

    def test_duplicate_specs_fail_deterministically(self) -> None:
        duplicate = CommandSpec(
            name="duplicate",
            base_risk=RiskTier.SAFE,
            capability=Capability.CHAT,
            allowed_origins=frozenset({"user"}),
            dispatch_kind=DispatchKind.CORE,
            handler_key=None,
        )
        with self.assertRaisesRegex(ValueError, "duplicate command specification: duplicate"):
            build_command_specs((duplicate, duplicate))

    def test_invalid_core_handler_shape_fails_deterministically(self) -> None:
        contradictory = CommandSpec(
            name="contradictory",
            base_risk=RiskTier.SAFE,
            capability=Capability.CHAT,
            allowed_origins=frozenset({"user"}),
            dispatch_kind=DispatchKind.CORE,
            handler_key="contradictory",
        )
        with self.assertRaisesRegex(ValueError, "core command cannot declare a handler"):
            build_command_specs((contradictory,))

    def test_handler_key_must_match_the_canonical_command_name(self) -> None:
        contradictory = CommandSpec(
            name="canonical_name",
            base_risk=RiskTier.SAFE,
            capability=Capability.CHAT,
            allowed_origins=frozenset({"user"}),
            dispatch_kind=DispatchKind.HANDLER,
            handler_key="different_name",
        )
        with self.assertRaisesRegex(
            ValueError,
            "handler key must match command name: canonical_name",
        ):
            build_command_specs((contradictory,))

    def test_policy_enum_fields_are_validated_at_registry_construction(self) -> None:
        invalid = CommandSpec(
            name="invalid_risk",
            base_risk="safe",  # type: ignore[arg-type]
            capability=Capability.CHAT,
            allowed_origins=frozenset({"user"}),
            dispatch_kind=DispatchKind.HANDLER,
            handler_key="invalid_risk",
        )
        with self.assertRaisesRegex(ValueError, "invalid base risk: invalid_risk"):
            build_command_specs((invalid,))

    def test_lookup_normalizes_known_names_and_rejects_unknown_names(self) -> None:
        self.assertIs(get_command_spec(" SPEAK "), COMMAND_SPECS["speak"])
        self.assertIsNone(get_command_spec("not-a-command"))
        self.assertIsNone(get_command_spec(None))


class CompatibilityViewTests(unittest.TestCase):
    def test_ai_engine_valid_commands_is_the_canonical_name_view(self) -> None:
        from agetha.core.ai_engine import VALID_COMMANDS

        self.assertIs(VALID_COMMANDS, COMMAND_NAMES)

    def test_command_guard_tiers_are_the_canonical_base_risk_view(self) -> None:
        from agetha.commands.command_guard import CommandGuard

        self.assertIs(CommandGuard.TIER_MAP, BASE_RISK_BY_COMMAND)
        self.assertEqual(CommandGuard.TIER_MAP["popup"], "safe")
        self.assertNotIn("change_animation_speed", CommandGuard.TIER_MAP)

    def test_capability_lookup_is_derived_and_unknown_names_fail_closed(self) -> None:
        from agetha.core.capabilities import capability_for_command

        for name, capability in CAPABILITY_BY_COMMAND.items():
            with self.subTest(command=name):
                self.assertIs(capability_for_command(name), capability)
        self.assertIs(
            capability_for_command("not-a-command"),
            Capability.ADVANCED_OS_INTEGRATION,
        )
        self.assertIs(
            capability_for_command(None),
            Capability.ADVANCED_OS_INTEGRATION,
        )

    def test_unknown_guard_tier_remains_danger(self) -> None:
        from agetha.commands.command_guard import CommandGuard

        guard = CommandGuard()
        self.assertEqual(guard._resolve_tier("not-a-command", {}), CommandGuard.DANGER)

    def test_force_close_dynamic_risk_remains_outside_static_metadata(self) -> None:
        from agetha.commands.command_guard import CommandGuard

        guard = CommandGuard()
        guard._settings = SimpleNamespace(
            enable_command_confirmations=True,
            force_close_auto_allow=True,
            protected_processes=lambda: {"explorer.exe"},
        )
        with patch.object(guard, "_show_dialog", return_value=False) as dialog:
            self.assertTrue(guard.check("force_close", {"app": "notepad.exe"}))
            dialog.assert_not_called()
            self.assertFalse(guard.check("force_close", {"app": "explorer.exe"}))
            dialog.assert_called_once()


def _dispatch_test_app() -> SimpleNamespace:
    return SimpleNamespace(
        STATE_IDLE="idle",
        _ATTENTION_MOODS=set(),
        _guard=SimpleNamespace(
            check=lambda *_args, **_kwargs: True,
            check_dry_run=lambda *_args, **_kwargs: True,
            describe=lambda *_args, **_kwargs: "test command",
        ),
        _subtitle=SimpleNamespace(
            clear=lambda: None,
            show_message=lambda *_args, **_kwargs: None,
        ),
        _set_state=lambda *_args, **_kwargs: None,
        _reschedule_screen_poll=lambda: None,
        _speak_and_continue=lambda *_args, **_kwargs: None,
        _try_short_mood_speak=lambda *_args, **_kwargs: False,
        root=SimpleNamespace(after=lambda _delay, callback: callback()),
    )


class HandlerBindingTests(unittest.TestCase):
    def test_handler_registry_matches_handler_backed_specs_bidirectionally(self) -> None:
        from agetha.commands.command_handlers import HANDLERS

        validate_handler_bindings(HANDLERS)
        expected = {
            spec.handler_key
            for spec in COMMAND_SPECS.values()
            if spec.dispatch_kind is DispatchKind.HANDLER
        }
        self.assertEqual(set(HANDLERS), expected)

    def test_duplicate_handler_registration_fails_without_overwrite(self) -> None:
        from agetha.commands.command_handlers import HANDLERS, register

        original = HANDLERS["open_url"]
        replacement = lambda *_args: True
        with self.assertRaisesRegex(ValueError, "duplicate handler registration: open_url"):
            register("open_url")(replacement)
        self.assertIs(HANDLERS["open_url"], original)

    def test_handler_without_spec_and_core_handler_both_fail(self) -> None:
        from agetha.commands.command_handlers import register

        with self.assertRaisesRegex(ValueError, "handler has no CommandSpec: rogue"):
            register("rogue")(lambda *_args: True)
        with self.assertRaisesRegex(ValueError, "command is not handler-backed: idle"):
            register("idle")(lambda *_args: True)

    def test_dispatch_rejects_unknown_command_even_if_a_handler_is_injected(self) -> None:
        from agetha.commands import command_handlers

        called: list[str] = []
        settings = AppSettings({
            "COMPACT_MODE": "no",
            "ENABLE_COMMAND_EXECUTION": "yes",
        })
        with (
            patch.dict(
                command_handlers.HANDLERS,
                {"rogue": lambda *_args: called.append("rogue") or True},
            ),
            patch.object(command_handlers, "get_settings", return_value=settings),
        ):
            command_handlers.dispatch(
                _dispatch_test_app(),
                {"command": "rogue", "mood": "neutral", "segments": []},
                "do it",
                origin="user",
            )
        self.assertEqual(called, [])

    def test_spec_origin_can_narrow_a_centrally_permitted_event(self) -> None:
        from agetha.commands import command_handlers

        called: list[str] = []
        settings = AppSettings({
            "COMPACT_MODE": "no",
            "ENABLE_COMMAND_EXECUTION": "yes",
        })
        user_only = replace(
            COMMAND_SPECS["open_url"],
            allowed_origins=frozenset({"user"}),
        )
        with (
            patch.dict(
                command_handlers.HANDLERS,
                {"open_url": lambda *_args: called.append("open_url") or True},
            ),
            patch.object(command_handlers, "get_settings", return_value=settings),
            patch.object(
                command_handlers,
                "get_command_spec",
                return_value=user_only,
                create=True,
            ),
        ):
            command_handlers.dispatch(
                _dispatch_test_app(),
                {"command": "open_url", "url": "https://example.com"},
                "open it",
                origin="touch",
            )
        self.assertEqual(called, [])

    def test_compact_capability_denial_precedes_static_origin_narrowing(self) -> None:
        from agetha.commands import command_handlers

        events: list[str] = []
        app = _dispatch_test_app()
        app._speak_and_continue = lambda *_args, **_kwargs: events.append("origin")
        settings = AppSettings({
            "COMPACT_MODE": "yes",
            "ENABLE_COMMAND_EXECUTION": "yes",
            "ENABLE_COMPUTER_USE": "yes",
        })
        with (
            patch.object(command_handlers, "get_settings", return_value=settings),
            patch.object(
                command_handlers,
                "_deny_capability",
                side_effect=lambda *_args: events.append("capability"),
            ),
        ):
            command_handlers.dispatch(
                app,
                {"command": "computer_use", "goal": "inspect the screen"},
                "inspect it",
                origin="touch",
            )

        self.assertEqual(events, ["capability"])

    def test_ambient_and_tool_result_still_cannot_reach_effect_handlers(self) -> None:
        from agetha.commands import command_handlers

        settings = AppSettings({
            "COMPACT_MODE": "no",
            "ENABLE_COMMAND_EXECUTION": "yes",
        })
        for origin in ("ambient", "tool_result"):
            called: list[str] = []
            with (
                self.subTest(origin=origin),
                patch.dict(
                    command_handlers.HANDLERS,
                    {"run_command": lambda *_args: called.append(origin) or True},
                ),
                patch.object(command_handlers, "get_settings", return_value=settings),
            ):
                command_handlers.dispatch(
                    _dispatch_test_app(),
                    {
                        "command": "run_command",
                        "cmd": "echo blocked",
                        "ambient_relevance": "important",
                        "segments": [],
                    },
                    None,
                    origin=origin,
                )
            self.assertEqual(called, [])


class GeneratedCommandMatrixTests(unittest.TestCase):
    def test_renderer_emits_one_deterministic_row_per_command(self) -> None:
        from agetha.commands.generate_command_matrix import render_command_matrix

        rendered = render_command_matrix()
        self.assertTrue(rendered.startswith("# Generated command matrix\n"))
        self.assertEqual(rendered.count("\n| `"), 69)
        self.assertIn(
            "| `computer_use` | caution | computer_use | yes | user | handler | "
            "`computer_use` | `ENABLE_COMPUTER_USE` |",
            rendered,
        )
        self.assertIn(
            "| `idle` | safe | chat | no | user, touch, file_drop, reminder, "
            "ambient, tool_result, terminal_sentinel | core | - | - |",
            rendered,
        )
        self.assertEqual(rendered, render_command_matrix())

    def test_check_detects_stale_file_and_writer_restores_it(self) -> None:
        from agetha.commands.generate_command_matrix import (
            command_matrix_matches,
            render_command_matrix,
            write_command_matrix,
        )

        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "command_matrix.md"
            target.write_text("stale\n", encoding="utf-8")
            self.assertFalse(command_matrix_matches(target))
            write_command_matrix(target)
            self.assertTrue(command_matrix_matches(target))
            self.assertEqual(target.read_text(encoding="utf-8"), render_command_matrix())


if __name__ == "__main__":
    unittest.main()
