"""Strict model and planner-request tests for Computer Use Lite."""

from __future__ import annotations

import json
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agetha.computer_use.models import (  # noqa: E402
    ComputerAction,
    ComputerActionKind,
    ComputerObservation,
    ControlSource,
    ObservedControl,
    PlannerRequest,
    ProcessIdentity,
    Rect,
    WindowIdentity,
    process_identities_match,
)


def target(*, created_at: float | None = 10.0) -> WindowIdentity:
    return WindowIdentity(
        hwnd=44,
        process=ProcessIdentity(123, "notepad.exe", created_at),
        bounds=Rect(100, 100, 500, 400),
        title="Notes",
    )


def observation() -> ComputerObservation:
    return ComputerObservation(
        observation_id="obs:1",
        target=target(),
        foreground=True,
        screen_bounds=Rect(0, 0, 1920, 1080),
        cursor=(10, 20),
        controls=(
            ObservedControl(
                control_id="ocr:1",
                source=ControlSource.OCR,
                label="Editor",
                bounds=Rect(120, 130, 200, 100),
                confidence=0.9,
            ),
        ),
        captured_at=100.0,
    )


class TestComputerUseModels(unittest.TestCase):
    def test_action_parser_accepts_exactly_one_typed_action(self) -> None:
        action = ComputerAction.parse(
            json.dumps(
                {
                    "action": "click_control",
                    "observation_id": "obs:1",
                    "target_id": "ocr:1",
                    "expected_result": "editor focused",
                    "reason": "select visible editor",
                    "confidence": 4.2,
                }
            )
        )

        self.assertEqual(action.action, ComputerActionKind.CLICK_CONTROL)
        self.assertEqual(action.target_id, "ocr:1")
        self.assertEqual(action.confidence, 1.0)
        with self.assertRaises(FrozenInstanceError):
            action.reason = "changed"  # type: ignore[misc]

    def test_action_parser_rejects_arrays_unknown_actions_and_executable_fields(self) -> None:
        invalid = (
            "[]",
            '{"action":"run_shell","observation_id":"obs:1"}',
            '{"action":"finish","observation_id":"obs:1","command":"calc.exe"}',
            '{"action":"click_point","observation_id":"obs:1","x":1}',
            '{"action":"finish","observation_id":"obs:1","confidence":"high"}',
        )
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                ComputerAction.parse(raw)

    def test_type_payload_never_accepts_inline_text_or_foreign_ref(self) -> None:
        with self.assertRaises(ValueError):
            ComputerAction.parse(
                {
                    "action": "type_payload",
                    "observation_id": "obs:1",
                    "payload_ref": "user_text_1",
                    "text": "private text",
                }
            )
        with self.assertRaises(ValueError):
            ComputerAction.parse(
                {
                    "action": "type_payload",
                    "observation_id": "obs:1",
                    "payload_ref": "../../secret",
                }
            )

        action = ComputerAction.parse(
            {
                "action": "type_payload",
                "observation_id": "obs:1",
                "payload_ref": "payload:user_text_1",
                "confidence": 0.8,
            }
        )
        self.assertEqual(action.payload_ref, "user_text_1")

    def test_manual_action_construction_cannot_mix_action_fields(self) -> None:
        with self.assertRaises(ValueError):
            ComputerAction(
                action=ComputerActionKind.FINISH,
                observation_id="obs:1",
                x=10,
            )
        with self.assertRaises(ValueError):
            ComputerAction(
                action=ComputerActionKind.HOTKEY,
                observation_id="obs:1",
                keys=("ctrl",),
            )

    def test_process_identity_detects_pid_reuse_and_missing_creation_time(self) -> None:
        original = ProcessIdentity(100, "App.EXE", 10.0)
        self.assertTrue(process_identities_match(original, ProcessIdentity(100, "app.exe", 10.0)))
        self.assertFalse(process_identities_match(original, ProcessIdentity(100, "app.exe", 11.0)))
        self.assertFalse(process_identities_match(original, ProcessIdentity(100, "app.exe", None)))
        self.assertFalse(
            process_identities_match(
                ProcessIdentity(100, "app.exe", None),
                ProcessIdentity(100, "APP.EXE", None),
            )
        )

    def test_rect_edges_and_control_ids_are_bounded(self) -> None:
        rect = Rect(10, 20, 30, 40)
        self.assertTrue(rect.contains_point(10, 20))
        self.assertFalse(rect.contains_point(40, 60))
        self.assertTrue(rect.contains_rect(Rect(11, 21, 2, 3)))
        with self.assertRaises(ValueError):
            ObservedControl(
                control_id="stable-global-id",
                source=ControlSource.OCR,
                label="bad",
                bounds=rect,
                confidence=1,
            )

    def test_planner_payload_is_compact_json_serializable_and_has_refs_only(self) -> None:
        request = PlannerRequest(
            request_id="request:1",
            session_id="session:1",
            generation=1,
            observation_id="obs:1",
            step=0,
            goal="type payload:user_text_1 into editor",
            payload_refs=("user_text_1",),
            recent_actions=("focus_window:success",),
            failure_reason="",
            allowed_actions=("type_payload", "finish"),
        )
        payload = request.as_payload(observation())
        encoded = json.dumps(payload, ensure_ascii=False)

        self.assertIn("payload:user_text_1", encoded)
        self.assertNotIn("personality", encoded.casefold())
        self.assertNotIn("memory", encoded.casefold())
        self.assertEqual(payload["controls"][0]["id"], "ocr:1")  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
