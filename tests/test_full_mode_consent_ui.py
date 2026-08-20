from __future__ import annotations

import threading
import unittest

from agetha.ui.full_mode_consent import (
    MAX_SHAKE_AMPLITUDE_PX,
    MAX_SHAKE_DURATION_MS,
    ConsentDialogKind,
    FullModeConsentUI,
    TkConsentDialogView,
)


class FakeRoot:
    def __init__(self) -> None:
        self.jobs: dict[str, tuple[int, object]] = {}
        self.cancelled: list[str] = []
        self._next_job = 0

    def after(self, delay_ms: int, callback):
        self._next_job += 1
        job_id = f"after-{self._next_job}"
        self.jobs[job_id] = (delay_ms, callback)
        return job_id

    def after_cancel(self, job_id: str) -> None:
        self.cancelled.append(job_id)
        self.jobs.pop(job_id, None)

    def run_all(self) -> None:
        while self.jobs:
            job_id = min(
                self.jobs,
                key=lambda item: (self.jobs[item][0], int(item.split("-")[1])),
            )
            _delay, callback = self.jobs.pop(job_id)
            callback()


class FakeDialog:
    def __init__(self, spec, decision) -> None:
        self.spec = spec
        self._decision = decision
        self.positions: list[tuple[int, int]] = [(200, 120)]
        self.static_attention = False
        self.closed = False

    def get_position(self) -> tuple[int, int]:
        return self.positions[-1]

    def move_to(self, x: int, y: int) -> None:
        self.positions.append((x, y))

    def show_static_attention(self) -> None:
        self.static_attention = True

    def close(self) -> None:
        self.closed = True

    def decide(self, approved: bool) -> None:
        self._decision(approved)


class FakeFactory:
    def __init__(self) -> None:
        self.views: list[FakeDialog] = []

    def __call__(self, _root, spec, decision) -> FakeDialog:
        view = FakeDialog(spec, decision)
        self.views.append(view)
        return view


class FullModeConsentUITests(unittest.TestCase):
    def make_ui(self, *, reduced_motion: bool = False):
        root = FakeRoot()
        factory = FakeFactory()
        ui = FullModeConsentUI(
            root,
            reduced_motion=reduced_motion,
            dialog_factory=factory,
        )
        return root, factory, ui

    def test_first_warning_is_accurate_and_uses_yes_no_actions(self) -> None:
        _root, factory, ui = self.make_ui()

        self.assertTrue(ui.show_first_confirmation(lambda _approved: None))

        spec = factory.views[-1].spec
        self.assertEqual(spec.kind, ConsentDialogKind.FIRST_CONFIRMATION)
        self.assertEqual(
            spec.heading,
            "Are you sure you want to enter Agetha Full Mode?",
        )
        self.assertEqual(spec.negative_label, "No")
        self.assertEqual(spec.affirmative_label, "Yes")
        self.assertIn("advanced OS integration", spec.message)
        self.assertIn("Process Awareness", spec.message)
        self.assertIn("Computer Use", spec.message)
        self.assertIn("Safety restrictions remain enabled", spec.message)

    def test_no_escape_and_window_close_each_report_false_exactly_once(self) -> None:
        for source in ("No", "Escape", "window close"):
            with self.subTest(source=source):
                _root, factory, ui = self.make_ui()
                decisions: list[bool] = []
                ui.show_first_confirmation(decisions.append)
                view = factory.views[-1]

                view.decide(False)
                view.decide(False)

                self.assertEqual(decisions, [False])
                self.assertTrue(view.closed)

    def test_yes_reports_true_exactly_once(self) -> None:
        _root, factory, ui = self.make_ui()
        decisions: list[bool] = []
        ui.show_first_confirmation(decisions.append)
        view = factory.views[-1]

        view.decide(True)
        view.decide(False)

        self.assertEqual(decisions, [True])

    def test_only_literal_true_can_report_affirmative_consent(self) -> None:
        _root, factory, ui = self.make_ui()
        decisions: list[bool] = []
        ui.show_first_confirmation(decisions.append)

        factory.views[-1].decide("yes")  # type: ignore[arg-type]

        self.assertEqual(decisions, [False])

    def test_final_confirmation_uses_exact_mode_labels(self) -> None:
        _root, factory, ui = self.make_ui()

        ui.show_final_confirmation(lambda _approved: None)

        spec = factory.views[-1].spec
        self.assertEqual(spec.kind, ConsentDialogKind.FINAL_CONFIRMATION)
        self.assertEqual(spec.heading, "Still want to enable Agetha Full Mode?")
        self.assertEqual(spec.negative_label, "Stay Compact")
        self.assertEqual(spec.affirmative_label, "Enable Full Mode")
        self.assertIn("does not bypass", spec.message)
        self.assertIn("feature settings", spec.message)

    def test_demo_fallback_is_in_app_and_uses_cancel_continue(self) -> None:
        _root, factory, ui = self.make_ui()

        ui.show_demo_fallback(
            "Notepad could not be launched.",
            lambda _approved: None,
        )

        spec = factory.views[-1].spec
        self.assertEqual(spec.kind, ConsentDialogKind.DEMO_FALLBACK)
        self.assertEqual(spec.negative_label, "Cancel")
        self.assertEqual(spec.affirmative_label, "Continue")
        self.assertIn("Notepad could not be launched.", spec.message)
        self.assertIn(
            "ARE YOU REALLY SURE YOU WANT TO CONTINUE THIS?",
            spec.message,
        )
        self.assertIn("No warning was typed", spec.message)
        self.assertIn("final confirmation", spec.message)

    def test_first_warning_shake_is_bounded_and_returns_to_origin(self) -> None:
        root, factory, ui = self.make_ui()

        ui.show_first_confirmation(lambda _approved: None)
        view = factory.views[-1]
        scheduled = list(root.jobs.values())

        self.assertTrue(scheduled)
        self.assertLessEqual(max(delay for delay, _callback in scheduled), MAX_SHAKE_DURATION_MS)
        root.run_all()

        origin_x, origin_y = view.positions[0]
        self.assertEqual(view.positions[-1], (origin_x, origin_y))
        self.assertLessEqual(
            max(abs(x - origin_x) for x, _y in view.positions),
            MAX_SHAKE_AMPLITUDE_PX,
        )
        self.assertTrue(all(y == origin_y for _x, y in view.positions))
        self.assertEqual(ui.pending_after_ids, frozenset())

    def test_reduced_motion_uses_static_attention_and_no_after_jobs(self) -> None:
        root, factory, ui = self.make_ui(reduced_motion=True)

        ui.show_first_confirmation(lambda _approved: None)

        self.assertTrue(factory.views[-1].static_attention)
        self.assertEqual(root.jobs, {})
        self.assertEqual(ui.pending_after_ids, frozenset())

    def test_cancel_all_invalidates_callbacks_and_cancels_owned_after_ids(self) -> None:
        root, factory, ui = self.make_ui()
        decisions: list[bool] = []
        ui.show_first_confirmation(decisions.append)
        view = factory.views[-1]
        old_generation = ui.generation
        pending = set(root.jobs)

        ui.cancel_all()
        view.decide(True)

        self.assertGreater(ui.generation, old_generation)
        self.assertTrue(view.closed)
        self.assertEqual(decisions, [])
        self.assertEqual(set(root.cancelled), pending)
        self.assertEqual(ui.pending_after_ids, frozenset())

    def test_close_is_idempotent_and_prevents_new_or_stale_dialogs(self) -> None:
        _root, factory, ui = self.make_ui()
        decisions: list[bool] = []
        ui.show_first_confirmation(decisions.append)
        stale = factory.views[-1]

        ui.close()
        generation = ui.generation
        ui.close()

        self.assertFalse(ui.show_final_confirmation(decisions.append))
        stale.decide(True)
        self.assertEqual(ui.generation, generation)
        self.assertEqual(decisions, [])
        self.assertEqual(len(factory.views), 1)

    def test_public_methods_reject_non_owner_thread_calls_before_touching_tk(self) -> None:
        _root, factory, ui = self.make_ui()
        errors: list[BaseException] = []

        def invoke() -> None:
            try:
                ui.show_first_confirmation(lambda _approved: None)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=invoke)
        worker.start()
        worker.join()

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RuntimeError)
        self.assertEqual(factory.views, [])

    def test_tk_view_routes_all_rejection_surfaces_to_false(self) -> None:
        for method_name in ("_on_negative", "_on_escape", "_on_window_close"):
            with self.subTest(method=method_name):
                decisions: list[bool] = []
                view = object.__new__(TkConsentDialogView)
                view._decision = decisions.append

                result = getattr(view, method_name)(None)

                self.assertEqual(decisions, [False])
                if method_name == "_on_escape":
                    self.assertEqual(result, "break")

    def test_tk_view_routes_affirmative_surface_to_true(self) -> None:
        decisions: list[bool] = []
        view = object.__new__(TkConsentDialogView)
        view._decision = decisions.append

        view._on_affirmative()

        self.assertEqual(decisions, [True])

    def test_tk_view_formats_negative_monitor_coordinates_without_runaway(self) -> None:
        geometries: list[str] = []
        view = object.__new__(TkConsentDialogView)
        view.win = type(
            "FakeWindow",
            (),
            {"geometry": lambda _self, value: geometries.append(value)},
        )()

        view.move_to(-3, -4)

        self.assertEqual(geometries, ["-3-4"])


if __name__ == "__main__":
    unittest.main()
