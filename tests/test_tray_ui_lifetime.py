"""Tray callbacks use the real UI queue with fake tray/Tk surfaces only."""

from __future__ import annotations

import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from main import CompanionApp
from agetha.features import tray_scaffold as tray


class FakeRoot:
    def __init__(self, owner, effects, violations):
        self.owner, self.effects, self.violations = owner, effects, violations

    def _check(self):
        if threading.get_ident() != self.owner:
            self.violations.append("worker Tk access")
            raise AssertionError("worker touched Tk")

    def after(self, *_args):
        self._check()
        return "fake-job"

    def deiconify(self):
        self._check()
        self.effects.append("restore")

    def attributes(self, *args):
        self._check()
        self.effects.append(args)


class FakeIcon:
    def __init__(self, _name, _image, _title, menu):
        self.menu, self.stops = menu, 0

    def run(self):
        pass

    def stop(self):
        self.stops += 1


class TestTrayUiLifetime(unittest.TestCase):
    def setUp(self):
        self.effects, self.violations = [], []
        self.app = CompanionApp.__new__(CompanionApp)
        self.app._closing = False
        self.app._ui_owner_thread_id = threading.get_ident()
        self.app._ui_callback_queue = queue.SimpleQueue()
        self.app._start_ui_dispatcher = lambda: None
        self.app.root = FakeRoot(threading.get_ident(), self.effects, self.violations)
        self.app._restore_from_tray = self.app.root.deiconify
        self.app._open_dashboard = lambda: self.effects.append("settings")

        def shutdown():
            self.effects.append("shutdown")
            self.app._closing = True

        self.app._shutdown = shutdown
        fake = SimpleNamespace(
            Menu=lambda *items: dict(items),
            MenuItem=lambda label, callback, **_kwargs: (label, callback),
            Icon=FakeIcon,
        )
        self.enterContext(patch.dict("sys.modules", pystray=fake))
        self.enterContext(patch("agetha.app_config.get_settings", return_value=SimpleNamespace(enable_tray=True)))
        self.enterContext(patch.object(tray, "is_tray_available", return_value=True))
        self.enterContext(patch.object(tray, "_tray_icon", None))
        self.enterContext(patch.object(tray, "_tray_thread", None))
        self.enterContext(patch("PIL.Image.open", return_value=object()))
        self.enterContext(patch("PIL.Image.new", return_value=object()))
        self.addCleanup(tray.stop_tray)
        self.assertTrue(tray.start_tray(self.app))
        self.icon = tray._tray_icon
        tray._tray_thread.join(1)

    def _worker_action(self, label):
        worker = threading.Thread(target=self.icon.menu[label], daemon=True)
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.violations, [], "tray invoked Tk from its worker")

    def test_open_queues_restore_and_topmost_on_owner_only(self):
        with patch.object(tray.sys, "platform", "win32"):
            self._worker_action("Open Agetha")
            self.assertEqual(self.effects, [])
            self.assertFalse(self.app._ui_callback_queue.empty())
            self.app._drain_ui_queue()
            self.app._drain_ui_queue()
        self.assertEqual(self.effects, ["restore", ("-topmost", True)])

    def test_settings_queues_one_owner_action(self):
        self._worker_action("Settings")
        self.assertEqual(self.effects, [])
        self.app._drain_ui_queue()
        self.assertEqual(self.effects, ["settings"])

    def test_exit_stops_tray_and_queues_owner_shutdown(self):
        self._worker_action("Exit")
        self.assertEqual(self.icon.stops, 1)
        self.assertFalse(tray.is_tray_running())
        self.assertEqual(self.effects, [])
        self.assertFalse(self.app._ui_callback_queue.empty())
        self.app._drain_ui_queue()
        self.assertEqual(self.effects, ["shutdown"])

    def test_close_before_delivery_discards_settings(self):
        self._worker_action("Settings")
        self.app._closing = True
        self.app._drain_ui_queue()
        self.assertEqual(self.effects, [])
        self.assertTrue(self.app._ui_callback_queue.empty())

    def test_callback_delivered_after_close_is_inert(self):
        self._worker_action("Open Agetha")
        self.assertFalse(self.app._ui_callback_queue.empty())
        callback = self.app._ui_callback_queue.get_nowait()
        self.app._closing = True
        callback()
        self.assertEqual(self.effects, [])

    def test_stopped_tray_cannot_deliver_old_settings(self):
        self._worker_action("Settings")
        tray.stop_tray()
        self.app._drain_ui_queue()
        self.assertEqual(self.effects, [])

    def test_restarted_tray_does_not_revive_old_open(self):
        self._worker_action("Open Agetha")
        tray.stop_tray()
        self.assertTrue(tray.start_tray(self.app))
        tray._tray_thread.join(1)
        self.app._drain_ui_queue()
        self.assertEqual(self.effects, [])

    def test_exit_delivered_after_close_does_not_repeat_shutdown(self):
        self._worker_action("Exit")
        self.assertFalse(self.app._ui_callback_queue.empty())
        callback = self.app._ui_callback_queue.get_nowait()
        self.app._closing = True
        callback()
        self.assertEqual(self.effects, [])

    def test_pause_toggle_keeps_existing_status_behavior(self):
        from agetha.features import status_providers

        paused = [False]
        with patch.object(status_providers, "is_paused", side_effect=lambda: paused[0]), \
             patch.object(status_providers, "set_paused", side_effect=lambda value: paused.__setitem__(0, value)):
            label = next(k for k in self.icon.menu if callable(k))
            worker = threading.Thread(target=self.icon.menu[label], daemon=True)
            worker.start()
            worker.join(1)
            self.assertFalse(worker.is_alive())
        self.assertEqual(paused, [True])
        self.assertEqual(self.effects, [])


if __name__ == "__main__":
    unittest.main()
