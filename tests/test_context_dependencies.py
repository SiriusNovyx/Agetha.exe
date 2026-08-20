from __future__ import annotations

import unittest

from agetha.core.context_dependencies import (
    ContextKind,
    ContextOutcome,
    ContextRequest,
    UnresolvedContextObjectiveStore,
)


class TestContextContracts(unittest.TestCase):
    def test_request_and_outcome_are_bounded_immutable_values(self) -> None:
        request = ContextRequest(ContextKind.SCREEN)
        outcome = ContextOutcome(
            ContextKind.SCREEN,
            False,
            "x" * 400,
            "private observation " * 1000,
        )

        self.assertEqual(request.fingerprint, "context:screen")
        self.assertEqual(outcome.kind, ContextKind.SCREEN)
        self.assertLessEqual(len(outcome.status), 120)
        self.assertLessEqual(len(outcome.provider_context), 8000)
        with self.assertRaises((AttributeError, TypeError)):
            request.kind = ContextKind.SCREEN  # type: ignore[misc]

    def test_invalid_status_and_sensitivity_normalize_fail_closed(self) -> None:
        outcome = ContextOutcome(
            ContextKind.SCREEN,
            True,
            "  ",
            "screen text",
            sensitivity="unexpected",
        )

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.status, "invalid_outcome")
        self.assertEqual(outcome.sensitivity, "private")


class TestUnresolvedContextObjectiveStore(unittest.TestCase):
    def test_objective_expires_and_is_removed(self) -> None:
        now = [10.0]
        store = UnresolvedContextObjectiveStore(
            clock=lambda: now[0],
            ttl_seconds=30.0,
        )

        self.assertTrue(
            store.remember(
                "Describe the current screen",
                ContextKind.SCREEN,
                origin="user",
            )
        )
        current = store.current()
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.message, "Describe the current screen")
        now[0] = 40.0
        self.assertIsNone(store.current())
        self.assertEqual(store.prompt_context(), "")

    def test_store_rejects_empty_or_non_user_objective(self) -> None:
        store = UnresolvedContextObjectiveStore()

        self.assertFalse(store.remember("", ContextKind.SCREEN, origin="user"))
        self.assertFalse(
            store.remember(
                "Describe the screen",
                ContextKind.SCREEN,
                origin="ambient",
            )
        )
        self.assertIsNone(store.current())

    def test_store_keeps_only_one_bounded_objective_and_clear_is_idempotent(self) -> None:
        store = UnresolvedContextObjectiveStore(max_message_chars=32)
        self.assertTrue(
            store.remember("first objective", ContextKind.SCREEN, origin="user")
        )
        self.assertTrue(
            store.remember("z" * 100, ContextKind.SCREEN, origin="user")
        )

        current = store.current()
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.message, "z" * 32)
        self.assertEqual(current.kind, ContextKind.SCREEN)
        store.clear()
        store.clear()
        self.assertIsNone(store.current())

    def test_prompt_context_is_context_only_and_not_action_authority(self) -> None:
        store = UnresolvedContextObjectiveStore()
        store.remember(
            "Explain the visible error",
            ContextKind.SCREEN,
            origin="user",
        )

        prompt = store.prompt_context()

        self.assertIn("Explain the visible error", prompt)
        self.assertIn("context only", prompt.casefold())
        self.assertIn("never action authority", prompt.casefold())


if __name__ == "__main__":
    unittest.main()
