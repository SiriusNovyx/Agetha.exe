"""Focused tests for datetime context and cancellable UI effect controllers."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone, tzinfo
from types import SimpleNamespace
from unittest.mock import MagicMock

from agetha.app_config import AppSettings
from agetha.core.ai_engine import AIEngine
from agetha.core.time_context import build_datetime_context
from agetha.ui.mood_effects import BLACK, MOOD_COLOURS, MoodGlowController, mood_colour
from agetha.ui.motion_effects import MoodMotionController
from agetha.ui.window_effects import CRTCloseController
from agetha.ui.display_scale import resolve_ui_scale, scale_px


class FakeTk:
    def call(self, *_args):
        return ()


class FakeRoot:
    def __init__(self) -> None:
        self.jobs: dict[str, tuple[int, object]] = {}
        self.cancelled: list[str] = []
        self.geometry_calls: list[str] = []
        self.attribute_calls: list[tuple] = []
        self.destroy_count = 0
        self.fail_geometry = False
        self.tk = FakeTk()
        self._next = 0
        self._x, self._y, self._width, self._height = 100, 200, 340, 520

    def after(self, delay, callback):
        self._next += 1
        job = f"job-{self._next}"
        self.jobs[job] = (int(delay), callback)
        return job

    def after_cancel(self, job):
        self.cancelled.append(job)
        self.jobs.pop(job, None)

    def run_next(self):
        job = min(self.jobs, key=lambda key: (self.jobs[key][0], int(key.split("-")[1])))
        _delay, callback = self.jobs.pop(job)
        callback()

    def run_all(self):
        while self.jobs:
            self.run_next()

    def update_idletasks(self):
        return None

    def winfo_x(self): return self._x
    def winfo_y(self): return self._y
    def winfo_width(self): return self._width
    def winfo_height(self): return self._height
    def winfo_vrootx(self): return 0
    def winfo_vrooty(self): return 0
    def winfo_vrootwidth(self): return 1920
    def winfo_vrootheight(self): return 1080
    def winfo_screenwidth(self): return 1920
    def winfo_screenheight(self): return 1080

    def geometry(self, value):
        if self.fail_geometry:
            raise RuntimeError("geometry unavailable")
        self.geometry_calls.append(value)
        if value.startswith("+"):
            x, y = value[1:].split("+")
            self._x, self._y = int(x), int(y)

    def attributes(self, *args):
        self.attribute_calls.append(args)

    def destroy(self):
        self.destroy_count += 1


class FakeWidget:
    def __init__(self) -> None:
        self.options = {}

    def configure(self, **kwargs):
        self.options.update(kwargs)


class TestDatetimeContext(unittest.TestCase):
    def setUp(self) -> None:
        self.fixed = datetime(
            2026, 7, 27, 19, 42, 31,
            tzinfo=timezone(timedelta(hours=7), "ICT"),
        )

    def test_weekday_date_offset_and_no_seconds_by_default(self):
        text = build_datetime_context(clock=lambda: self.fixed)
        self.assertIn("Local time: Monday, 2026-07-27 19:42", text)
        self.assertNotIn("19:42:31", text)
        self.assertIn("Time zone: ICT (UTC+07:00)", text)

    def test_seconds_can_be_enabled(self):
        text = build_datetime_context(include_seconds=True, clock=lambda: self.fixed)
        self.assertIn("19:42:31", text)

    def test_missing_timezone_metadata_falls_back_safely(self):
        class MissingZone(tzinfo):
            def utcoffset(self, _dt): return None
            def dst(self, _dt): return None
            def tzname(self, _dt): return None

        unknown = datetime(2026, 7, 27, 19, 42, tzinfo=MissingZone())
        text = build_datetime_context(clock=lambda: unknown)
        self.assertIn("Time zone: Local time zone (UTC offset unavailable)", text)

    def test_prompt_contains_datetime_for_direct_and_ambient_turns(self):
        engine = AIEngine.__new__(AIEngine)
        settings = MagicMock()
        settings.enable_datetime_context = True
        settings.datetime_include_seconds = False
        settings.datetime_include_timezone = True
        settings.enable_companion_stats_context = False
        settings.enable_emotion_engine = False
        settings.enable_circadian_rhythm = False
        settings.enable_dreams = False
        settings.enable_tasks = False
        settings.enable_status_providers = False
        settings.episodic_prompt_limit = 0
        engine._app_settings = settings
        engine._datetime_provider = lambda: self.fixed
        engine._faster_mode = True
        engine._system_path = "C:\\Users\\test"
        engine._compact_chars = ""
        engine._get_inactivity_seconds = lambda: 0
        engine._load_memories = lambda: ""
        engine._build_history = lambda: []
        engine._session_recap_pending = False
        for user_message in ("hello", ""):
            _system, turn, _messages = engine._build_prompt("screen", user_message, "")
            self.assertIn("Monday, 2026-07-27 19:42", turn)
            self.assertIn("UTC+07:00", turn)


class TestNewSettings(unittest.TestCase):
    def test_defaults_and_boolean_spellings(self):
        defaults = AppSettings({})
        self.assertTrue(defaults.enable_datetime_context)
        self.assertTrue(defaults.enable_crt_close_animation)
        self.assertFalse(defaults.enable_mood_glow)
        self.assertTrue(defaults.enable_mood_motion)
        self.assertFalse(defaults.reduced_motion)
        for value in ("yes", "true", "1", "on"):
            self.assertTrue(AppSettings({"ENABLE_MOOD_GLOW": value}).enable_mood_glow)

    def test_numeric_values_are_clamped_or_fall_back(self):
        low = AppSettings({
            "MOOD_GLOW_INTERVAL_MS": "5",
            "MOOD_MOTION_COOLDOWN_SECONDS": "0",
        })
        self.assertEqual(low.mood_glow_interval_ms, 100)
        self.assertEqual(low.mood_motion_cooldown_seconds, 1)
        invalid = AppSettings({
            "MOOD_GLOW_INTERVAL_MS": "bad",
            "MOOD_MOTION_COOLDOWN_SECONDS": "bad",
        })
        self.assertEqual(invalid.mood_glow_interval_ms, 150)
        self.assertEqual(invalid.mood_motion_cooldown_seconds, 4)

    def test_ui_scale_auto_manual_and_invalid(self):
        self.assertIsNone(AppSettings({}).ui_scale)
        self.assertEqual(AppSettings({"UI_SCALE": "1.5"}).ui_scale, 1.5)
        self.assertEqual(AppSettings({"UI_SCALE": "9"}).ui_scale, 2.5)
        self.assertEqual(AppSettings({"UI_SCALE": "0.1"}).ui_scale, 0.75)
        self.assertIsNone(AppSettings({"UI_SCALE": "huge"}).ui_scale)


class TestDisplayScale(unittest.TestCase):
    def test_auto_scale_preserves_standard_displays(self):
        self.assertEqual(resolve_ui_scale(1920, 1080), 1.0)
        self.assertEqual(resolve_ui_scale(1366, 768), 1.0)

    def test_auto_scale_for_user_resolution(self):
        self.assertEqual(resolve_ui_scale(2880, 1920), 1.5)
        self.assertEqual(scale_px(340, 1.5), 510)
        self.assertEqual(scale_px(560, 1.5), 840)

    def test_auto_scale_accounts_for_surface_dpi(self):
        self.assertEqual(resolve_ui_scale(2880, 1920, dpi_scale=1.75), 1.75)
        self.assertEqual(scale_px(560, 1.75), 980)

    def test_manual_scale_is_clamped(self):
        self.assertEqual(resolve_ui_scale(2880, 1920, 1.25), 1.25)
        self.assertEqual(resolve_ui_scale(2880, 1920, 9), 2.5)


class TestMoodGlow(unittest.TestCase):
    def test_disabled_retains_normal_black_border(self):
        root, widget = FakeRoot(), FakeWidget()
        glow = MoodGlowController(root, widget, enabled=False)
        glow.set_mood("angry")
        self.assertEqual(widget.options["bg"], BLACK)
        self.assertEqual(widget.options["highlightthickness"], 0)
        self.assertFalse(root.jobs)

    def test_static_and_unknown_mood_colours(self):
        root, widget = FakeRoot(), FakeWidget()
        glow = MoodGlowController(root, widget, enabled=True, animated=False)
        glow.set_mood("angry")
        self.assertEqual(widget.options["bg"], MOOD_COLOURS["angry"])
        self.assertEqual(mood_colour("unknown"), MOOD_COLOURS["neutral"])
        self.assertEqual(
            set(MOOD_COLOURS),
            {
                "neutral", "happy", "excited", "sad", "surprised", "thinking",
                "whisper", "angry", "sleeping", "manic", "melancholic",
                "paranoid", "vulnerable", "dominant",
            },
        )

    def test_animated_loop_is_single_and_shutdown_cancels(self):
        root, widget = FakeRoot(), FakeWidget()
        glow = MoodGlowController(root, widget, enabled=True, animated=True)
        glow.set_mood("happy")
        first = glow.job_id
        glow.set_mood("angry")
        self.assertEqual(glow.job_id, first)
        self.assertEqual(len(root.jobs), 1)
        glow.close()
        self.assertIn(first, root.cancelled)
        self.assertIsNone(glow.job_id)

    def test_reduced_motion_uses_static_border(self):
        root, widget = FakeRoot(), FakeWidget()
        glow = MoodGlowController(root, widget, enabled=True, animated=True, reduced_motion=True)
        glow.set_mood("happy")
        self.assertEqual(widget.options["bg"], MOOD_COLOURS["happy"])
        self.assertFalse(root.jobs)


class TestMoodMotion(unittest.TestCase):
    def make(self, root=None, clock=None, **guards):
        return MoodMotionController(
            root or FakeRoot(), cooldown_seconds=4,
            clock=clock or (lambda: 10.0), random_value=lambda: 0.0,
            is_dragging=lambda: guards.get("dragging", False),
            is_closing=lambda: guards.get("closing", False),
            is_minimized=lambda: guards.get("minimized", False),
            geometry_busy=lambda: guards.get("busy", False),
        )

    def test_guards_reduced_motion_and_unknown_name(self):
        for guard in ("dragging", "closing", "minimized", "busy"):
            self.assertFalse(self.make(**{guard: True}).play_motion("gentle_bounce"))
        self.assertFalse(MoodMotionController(FakeRoot(), reduced_motion=True).play_motion("angry_shake"))
        self.assertFalse(self.make().play_motion("missing"))

    def test_requests_do_not_overlap_and_cooldown_is_enforced(self):
        root = FakeRoot()
        now = [10.0]
        motion = self.make(root, clock=lambda: now[0])
        self.assertTrue(motion.play_motion("gentle_bounce"))
        self.assertFalse(motion.play_motion("angry_shake"))
        root.run_all()
        self.assertFalse(motion.play_motion("gentle_bounce"))
        now[0] = 15.0
        self.assertTrue(motion.play_motion("gentle_bounce"))

    def test_cancel_restores_final_position_and_cancels_jobs(self):
        root = FakeRoot()
        motion = self.make(root)
        self.assertTrue(motion.play_motion("angry_shake"))
        scheduled = set(motion.job_ids)
        root.run_next()
        motion.cancel_motion()
        self.assertEqual(root.geometry_calls[-1], "+100+200")
        self.assertTrue((scheduled - set(root.jobs)) & set(root.cancelled))
        self.assertFalse(motion.active)

    def test_completed_motion_restores_origin(self):
        root = FakeRoot()
        motion = self.make(root)
        motion.play_motion("surprise_jump")
        root.run_all()
        self.assertEqual(root.geometry_calls[-1], "+100+200")


class TestCRTClose(unittest.TestCase):
    def test_duplicate_close_is_ignored_and_cleanup_once(self):
        root = FakeRoot()
        cleanup = MagicMock()
        close = CRTCloseController(root, cleanup)
        self.assertTrue(close.request_close())
        self.assertFalse(close.request_close())
        self.assertTrue(close.job_ids)
        root.run_all()
        cleanup.assert_called_once_with()

    def test_reduced_motion_skips_animation(self):
        root = FakeRoot()
        cleanup = MagicMock()
        close = CRTCloseController(root, cleanup, reduced_motion=True)
        close.request_close()
        cleanup.assert_called_once_with()
        self.assertFalse(root.jobs)
        self.assertFalse(root.geometry_calls)

    def test_animation_failure_falls_back_to_cleanup(self):
        root = FakeRoot()
        root.fail_geometry = True
        cleanup = MagicMock()
        close = CRTCloseController(root, cleanup)
        close.request_close()
        cleanup.assert_called_once_with()

    def test_scheduled_callbacks_are_cancellable(self):
        root = FakeRoot()
        close = CRTCloseController(root, MagicMock())
        close.request_close()
        jobs = set(close.job_ids)
        close.cancel()
        self.assertTrue(jobs.issubset(set(root.cancelled)))
        self.assertFalse(close.job_ids)


class TestGracefulShutdown(unittest.TestCase):
    def test_application_cleanup_is_idempotent(self):
        import threading
        from main import CompanionApp

        app = CompanionApp.__new__(CompanionApp)
        app.root = FakeRoot()
        app._shutdown_complete = False
        app._closing = False
        app._cancel_event = threading.Event()
        app._close_effect = MagicMock()
        app._mood_glow = MagicMock()
        app._motion = MagicMock()
        app._geom_anim_job = None
        app._talking_rotate_job = None
        app._poll_job = app._placeholder_refresh_job = None
        app._restore_job = app._wake_job = app._motion_request_job = None
        app._loaf_job = app._sleep_job = None
        app._subtitle = MagicMock()
        player = MagicMock()
        app._gif_cache = {"idle": player}
        app._voice = MagicMock()
        app._voice_out = MagicMock()
        app._bleep = MagicMock()

        app._graceful_shutdown()
        app._graceful_shutdown()

        self.assertTrue(app._cancel_event.is_set())
        self.assertEqual(app.root.destroy_count, 1)
        app._subtitle.stop.assert_called_once_with()
        player.stop.assert_called_once_with()
        app._voice.stop.assert_called_once_with()
        app._voice_out.stop.assert_called_once_with()
        app._bleep.stop.assert_called_once_with()


class TestRealTkSmoke(unittest.TestCase):
    def test_effects_run_on_real_tk_event_loop(self):
        import tkinter as tk

        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        root.withdraw()
        root.geometry("360x240+100+100")
        border = tk.Frame(root, bg=BLACK)
        border.pack(fill="both", expand=True)
        root.update_idletasks()

        glow = MoodGlowController(root, border, enabled=True, animated=False)
        for mood in MOOD_COLOURS:
            glow.set_mood(mood)
        animated = MoodGlowController(root, border, enabled=True, animated=True)
        animated.set_mood("manic")
        root.update()
        self.assertIsNotNone(animated.job_id)
        animated.close()

        motion = MoodMotionController(
            root, cooldown_seconds=1, random_value=lambda: 0.0,
        )
        self.assertTrue(motion.play_motion("angry_shake"))
        root.after(350, root.quit)
        root.mainloop()
        self.assertFalse(motion.active)

        cleaned: list[bool] = []
        close = CRTCloseController(root, lambda: (cleaned.append(True), root.destroy()))
        close.request_close()
        root.mainloop()
        self.assertEqual(cleaned, [True])


if __name__ == "__main__":
    unittest.main()
