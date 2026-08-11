"""Deterministic tests for local Presence Etiquette rules."""

from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agetha.core.presence_etiquette import (  # noqa: E402
    PresenceEtiquette,
    PresenceState,
    PresenceUrgency,
    decide_presence,
)


class FakeClock:
    def __init__(self, hour: int = 12) -> None:
        self.current = datetime(2026, 8, 3, hour, 0, tzinfo=timezone.utc)
        self.monotonic_value = 500.0

    def now(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        return self.monotonic_value

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)
        self.monotonic_value += seconds


class PresenceMixin:
    def setUp(self) -> None:
        self.clock = FakeClock()

    def etiquette(self, **kwargs: object) -> PresenceEtiquette:
        return PresenceEtiquette(
            clock=self.clock.now,
            monotonic=self.clock.monotonic,
            **kwargs,
        )


class TestPurePresenceRules(PresenceMixin, unittest.TestCase):
    def test_state_and_decision_are_immutable(self) -> None:
        state = PresenceState(user_idle=True)
        decision = decide_presence(state)
        with self.assertRaises(FrozenInstanceError):
            state.user_idle = False  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            decision.allow_popup = False  # type: ignore[misc]

    def test_presentation_suppresses_popup_voice_focus_and_motion(self) -> None:
        decision = decide_presence(PresenceState(presentation_mode=True))
        self.assertFalse(decision.allow_popup)
        self.assertFalse(decision.allow_voice)
        self.assertFalse(decision.allow_focus_request)
        self.assertFalse(decision.allow_window_motion)
        self.assertTrue(decision.queue_nonurgent)

    def test_fullscreen_and_game_are_silent(self) -> None:
        for state in (
            PresenceState(fullscreen_active=True),
            PresenceState(active_game=True),
        ):
            with self.subTest(state=state):
                decision = decide_presence(state)
                self.assertFalse(decision.allow_popup)
                self.assertFalse(decision.allow_voice)
                self.assertFalse(decision.allow_focus_request)
                self.assertFalse(decision.allow_window_motion)
                self.assertTrue(decision.queue_nonurgent)

    def test_fullscreen_silent_setting_can_allow_visual_only(self) -> None:
        decision = decide_presence(
            PresenceState(fullscreen_active=True),
            fullscreen_silent=False,
        )
        self.assertTrue(decision.allow_popup)
        self.assertFalse(decision.allow_voice)
        self.assertFalse(decision.allow_focus_request)
        self.assertFalse(decision.allow_window_motion)

    def test_quiet_hours_allow_subtitles_but_not_voice(self) -> None:
        decision = decide_presence(
            PresenceState(),
            quiet_hours_active=True,
            quiet_retry_after_seconds=600,
        )
        self.assertTrue(decision.allow_popup)
        self.assertFalse(decision.allow_voice)
        self.assertFalse(decision.allow_focus_request)
        self.assertFalse(decision.allow_window_motion)
        self.assertFalse(decision.queue_nonurgent)
        self.assertEqual(decision.retry_after_seconds, 600)

    def test_idle_user_is_more_available_but_focus_is_never_stolen(self) -> None:
        decision = decide_presence(PresenceState(user_idle=True))
        self.assertTrue(decision.allow_popup)
        self.assertTrue(decision.allow_voice)
        self.assertTrue(decision.allow_window_motion)
        self.assertFalse(decision.allow_focus_request)
        self.assertFalse(decision.queue_nonurgent)

    def test_recently_active_and_media_states_stay_visual_only(self) -> None:
        for state in (
            PresenceState(user_recently_active=True),
            PresenceState(media_playing=True),
        ):
            with self.subTest(state=state):
                decision = decide_presence(state)
                self.assertTrue(decision.allow_popup)
                self.assertFalse(decision.allow_voice)
                self.assertFalse(decision.allow_focus_request)
                self.assertFalse(decision.allow_window_motion)

    def test_dangerous_warning_bypasses_ordinary_backoff_calmly(self) -> None:
        state = PresenceState(
            rapid_typing=True,
            repeated_dismissals=True,
            agetha_minimized=True,
            dangerous_condition=True,
        )
        decision = decide_presence(state, quiet_hours_active=True)
        self.assertTrue(decision.allow_popup)
        self.assertFalse(decision.allow_voice)
        self.assertFalse(decision.allow_focus_request)
        self.assertFalse(decision.allow_window_motion)
        self.assertFalse(decision.queue_nonurgent)

    def test_presentation_remains_authoritative_for_dangerous_warning(self) -> None:
        decision = decide_presence(
            PresenceState(presentation_mode=True, dangerous_condition=True)
        )
        self.assertFalse(decision.allow_popup)
        self.assertFalse(decision.allow_voice)
        self.assertFalse(decision.allow_focus_request)
        self.assertFalse(decision.allow_window_motion)

    def test_minimized_and_sleeping_queue_nonurgent_messages(self) -> None:
        for state in (
            PresenceState(agetha_minimized=True),
            PresenceState(agetha_sleeping=True),
        ):
            with self.subTest(state=state):
                decision = decide_presence(state)
                self.assertFalse(decision.allow_popup)
                self.assertFalse(decision.allow_voice)
                self.assertTrue(decision.queue_nonurgent)

    def test_shutdown_drops_every_nonessential_reaction(self) -> None:
        decision = decide_presence(
            PresenceState(shutdown_in_progress=True, dangerous_condition=True)
        )
        self.assertFalse(decision.allow_popup)
        self.assertFalse(decision.allow_voice)
        self.assertFalse(decision.allow_focus_request)
        self.assertFalse(decision.allow_window_motion)
        self.assertFalse(decision.queue_nonurgent)


class TestPresenceCooldowns(PresenceMixin, unittest.TestCase):
    def test_rapid_typing_cooldown_uses_monotonic_clock(self) -> None:
        etiquette = self.etiquette(rapid_typing_cooldown_seconds=30)
        active = etiquette.decide(PresenceState(rapid_typing=True))
        self.assertTrue(active.queue_nonurgent)
        self.assertAlmostEqual(active.retry_after_seconds or 0.0, 30.0)

        self.clock.advance(29)
        cooling = etiquette.decide(PresenceState())
        self.assertTrue(cooling.queue_nonurgent)
        self.assertAlmostEqual(cooling.retry_after_seconds or 0.0, 1.0)

        self.clock.advance(2)
        ready = etiquette.decide(PresenceState())
        self.assertFalse(ready.queue_nonurgent)
        self.assertTrue(ready.allow_popup)

    def test_repeated_dismissals_create_bounded_cooldown(self) -> None:
        etiquette = self.etiquette(dismiss_cooldown_seconds=90)
        self.assertFalse(etiquette.record_dismissal())
        self.clock.advance(1)
        self.assertTrue(etiquette.record_dismissal())
        cooling = etiquette.decide(PresenceState())
        self.assertTrue(cooling.queue_nonurgent)
        self.assertAlmostEqual(cooling.retry_after_seconds or 0.0, 90.0)

        self.clock.advance(91)
        ready = etiquette.decide(PresenceState())
        self.assertFalse(ready.queue_nonurgent)
        self.assertTrue(ready.allow_popup)

    def test_explicit_repeated_dismissal_state_is_respected(self) -> None:
        decision = self.etiquette().decide(
            PresenceState(repeated_dismissals=True)
        )
        self.assertTrue(decision.queue_nonurgent)
        self.assertFalse(decision.allow_popup)


class TestQuietHours(PresenceMixin, unittest.TestCase):
    def test_cross_midnight_quiet_hours_are_deterministic(self) -> None:
        self.clock = FakeClock(hour=23)
        etiquette = self.etiquette(
            quiet_hours_start="22:00",
            quiet_hours_end="07:00",
        )
        decision = etiquette.decide(PresenceState())
        self.assertTrue(decision.allow_popup)
        self.assertFalse(decision.allow_voice)
        self.assertEqual(decision.reason, "quiet hours")
        self.assertAlmostEqual(decision.retry_after_seconds or 0.0, 8 * 3600)

        self.clock.advance(8 * 3600 + 1)
        after = etiquette.decide(PresenceState())
        self.assertTrue(after.allow_voice)

    def test_state_can_supply_known_quiet_hours_without_configuration(self) -> None:
        decision = self.etiquette().decide(PresenceState(quiet_hours=True))
        self.assertEqual(decision.reason, "quiet hours")
        self.assertFalse(decision.allow_voice)

    def test_half_configured_quiet_hours_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.etiquette(quiet_hours_start="22:00")


class TestPresenceQueue(PresenceMixin, unittest.TestCase):
    def test_queued_message_expires_without_sleeping(self) -> None:
        etiquette = self.etiquette()
        self.assertTrue(etiquette.queue_message("later", ttl_seconds=5))
        self.assertEqual(len(etiquette.pending_messages()), 1)
        self.clock.advance(6)
        self.assertEqual(etiquette.pending_messages(), ())

    def test_queue_waits_during_rapid_typing_then_drains(self) -> None:
        etiquette = self.etiquette(rapid_typing_cooldown_seconds=10)
        etiquette.decide(PresenceState(rapid_typing=True))
        self.assertTrue(etiquette.queue_message("build failed", ttl_seconds=60))
        self.assertEqual(
            etiquette.drain_ready(PresenceState()),
            (),
        )
        self.clock.advance(11)
        ready = etiquette.drain_ready(PresenceState())
        self.assertEqual([item.message for item in ready], ["build failed"])

    def test_queue_is_bounded_and_deduplicated(self) -> None:
        etiquette = self.etiquette(max_queue_size=2)
        self.assertTrue(etiquette.queue_message("one", dedup_key="one"))
        self.assertFalse(etiquette.queue_message("one again", dedup_key="one"))
        self.assertTrue(etiquette.queue_message("two", dedup_key="two"))
        self.assertTrue(etiquette.queue_message("three", dedup_key="three"))
        self.assertEqual(
            [item.message for item in etiquette.pending_messages()],
            ["two", "three"],
        )

    def test_shutdown_cancels_queue_and_is_idempotent(self) -> None:
        etiquette = self.etiquette()
        etiquette.queue_message("pending")
        etiquette.shutdown()
        etiquette.shutdown()
        self.assertTrue(etiquette.is_shutdown)
        self.assertEqual(etiquette.pending_messages(), ())
        self.assertFalse(etiquette.queue_message("late"))
        decision = etiquette.decide(PresenceState())
        self.assertEqual(decision.reason, "shutdown in progress")

    def test_shutdown_state_drops_queue_on_drain(self) -> None:
        etiquette = self.etiquette()
        etiquette.queue_message("pending")
        self.assertEqual(
            etiquette.drain_ready(PresenceState(shutdown_in_progress=True)),
            (),
        )
        self.assertEqual(etiquette.pending_messages(), ())

    def test_message_ttl_and_size_are_bounded(self) -> None:
        etiquette = self.etiquette()
        self.assertFalse(etiquette.queue_message("gone", ttl_seconds=0))
        with self.assertRaises(ValueError):
            etiquette.queue_message("x" * 1001)
        with self.assertRaises(ValueError):
            etiquette.queue_message("bad", ttl_seconds=float("inf"))


class TestNoProviderDependency(PresenceMixin, unittest.TestCase):
    def test_rules_and_queue_are_complete_with_local_state_only(self) -> None:
        etiquette = self.etiquette()
        decision = etiquette.decide(PresenceState(user_idle=True))
        self.assertTrue(decision.allow_popup)
        self.assertTrue(etiquette.queue_message("local reaction"))
        self.assertEqual(
            etiquette.drain_ready(PresenceState(user_idle=True))[0].message,
            "local reaction",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
