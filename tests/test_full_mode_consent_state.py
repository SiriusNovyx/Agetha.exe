from __future__ import annotations

import unittest

from agetha.core.capability_consent import (
    CapabilityConsentFlow,
    ConsentState,
)


class CapabilityConsentFlowTests(unittest.TestCase):
    def test_previously_persisted_full_profile_starts_full_without_reconsent(self) -> None:
        flow = CapabilityConsentFlow(initial_full=True)

        self.assertEqual(flow.snapshot.state, ConsentState.FULL)
        self.assertTrue(flow.snapshot.full_active)
        self.assertEqual(flow.begin_enable(), flow.snapshot)

    def test_full_mode_remains_inactive_until_the_final_confirmation(self) -> None:
        flow = CapabilityConsentFlow()

        compact = flow.snapshot
        first = flow.begin_enable()
        demo = flow.confirm_first(first.generation)
        final = flow.finish_demo(demo.generation)

        self.assertEqual(compact.state, ConsentState.COMPACT)
        self.assertEqual(first.state, ConsentState.FIRST_CONFIRMATION)
        self.assertEqual(demo.state, ConsentState.CONSENT_DEMO)
        self.assertEqual(final.state, ConsentState.FINAL_CONFIRMATION)
        self.assertFalse(compact.full_active)
        self.assertFalse(first.full_active)
        self.assertFalse(demo.full_active)
        self.assertFalse(final.full_active)

        enabled = flow.confirm_final(final.generation)

        self.assertEqual(enabled.state, ConsentState.FULL)
        self.assertTrue(enabled.full_active)

    def test_out_of_order_confirmations_cannot_enable_full_mode(self) -> None:
        flow = CapabilityConsentFlow()

        first = flow.begin_enable()
        still_first = flow.confirm_final(first.generation)
        still_first_again = flow.finish_demo(first.generation)

        self.assertEqual(still_first.state, ConsentState.FIRST_CONFIRMATION)
        self.assertEqual(still_first_again.state, ConsentState.FIRST_CONFIRMATION)
        self.assertFalse(flow.snapshot.full_active)

    def test_cancel_and_close_return_to_compact_and_invalidate_generation(self) -> None:
        for closer_name, drive in (
            ("cancel", lambda flow, token: None),
            ("close", lambda flow, token: flow.confirm_first(token)),
            (
                "cancel",
                lambda flow, token: flow.finish_demo(
                    flow.confirm_first(token).generation
                ),
            ),
        ):
            with self.subTest(closer=closer_name, drive=drive):
                flow = CapabilityConsentFlow()
                first = flow.begin_enable()
                drive(flow, first.generation)
                old_generation = flow.snapshot.generation

                compact = getattr(flow, closer_name)(old_generation)

                self.assertEqual(compact.state, ConsentState.COMPACT)
                self.assertFalse(compact.full_active)
                self.assertGreater(compact.generation, old_generation)

    def test_stale_demo_and_confirmation_callbacks_are_ignored(self) -> None:
        flow = CapabilityConsentFlow()
        old_first = flow.begin_enable()
        old_demo = flow.confirm_first(old_first.generation)
        flow.cancel(old_demo.generation)
        new_first = flow.begin_enable()

        stale_demo = flow.finish_demo(old_demo.generation)
        stale_final = flow.confirm_final(old_demo.generation)

        self.assertEqual(stale_demo.state, ConsentState.FIRST_CONFIRMATION)
        self.assertEqual(stale_final.state, ConsentState.FIRST_CONFIRMATION)
        self.assertEqual(flow.snapshot.generation, new_first.generation)
        self.assertFalse(flow.snapshot.full_active)

    def test_generation_bound_demo_predicate_rejects_cancelled_flow(self) -> None:
        flow = CapabilityConsentFlow()
        first = flow.begin_enable()
        demo = flow.confirm_first(first.generation)

        self.assertTrue(
            flow.is_current(demo.generation, ConsentState.CONSENT_DEMO)
        )

        flow.cancel(demo.generation)

        self.assertFalse(
            flow.is_current(demo.generation, ConsentState.CONSENT_DEMO)
        )

    def test_full_to_compact_downgrade_invalidates_full_generation(self) -> None:
        flow = CapabilityConsentFlow()
        first = flow.begin_enable()
        demo = flow.confirm_first(first.generation)
        final = flow.finish_demo(demo.generation)
        full = flow.confirm_final(final.generation)

        compact = flow.downgrade_to_compact()

        self.assertEqual(compact.state, ConsentState.COMPACT)
        self.assertFalse(compact.full_active)
        self.assertGreater(compact.generation, full.generation)
        self.assertFalse(flow.is_current(full.generation, ConsentState.FULL))

    def test_shutdown_is_compact_idempotent_and_permanently_rejects_callbacks(self) -> None:
        flow = CapabilityConsentFlow()
        first = flow.begin_enable()
        demo = flow.confirm_first(first.generation)

        stopped = flow.shutdown()
        stopped_again = flow.shutdown()
        rejected_start = flow.begin_enable()

        self.assertEqual(stopped.state, ConsentState.COMPACT)
        self.assertTrue(stopped.shutdown)
        self.assertGreater(stopped.generation, demo.generation)
        self.assertEqual(stopped_again, stopped)
        self.assertEqual(rejected_start, stopped)
        self.assertFalse(flow.is_current(stopped.generation, ConsentState.COMPACT))


if __name__ == "__main__":
    unittest.main()
