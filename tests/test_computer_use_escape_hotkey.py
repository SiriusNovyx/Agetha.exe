from __future__ import annotations

import threading
import unittest

from agetha.computer_use.escape_hotkey import SessionEscapeHotkey


class SessionEscapeHotkeyTests(unittest.TestCase):
    def test_non_windows_never_starts_runner(self) -> None:
        called = []
        hotkey = SessionEscapeHotkey(
            lambda: None,
            platform_name="linux",
            runner=lambda *_args: called.append(True),
        )

        self.assertFalse(hotkey.start())
        self.assertEqual(called, [])

    def test_registered_hotkey_delivers_escape_and_stops_cleanly(self) -> None:
        escaped = threading.Event()
        runner_stopped = threading.Event()

        def runner(callback, stop_event, ready, registered):
            registered[0] = True
            ready.set()
            callback()
            stop_event.wait(1.0)
            runner_stopped.set()

        hotkey = SessionEscapeHotkey(
            escaped.set,
            platform_name="win32",
            runner=runner,
        )

        self.assertTrue(hotkey.start())
        self.assertTrue(escaped.wait(0.5))
        self.assertTrue(hotkey.registered)
        hotkey.stop()
        self.assertTrue(runner_stopped.wait(0.5))
        self.assertFalse(hotkey.registered)

    def test_stop_from_hotkey_thread_does_not_self_join(self) -> None:
        stopped = threading.Event()
        holder = []

        def runner(callback, _stop_event, ready, registered):
            registered[0] = True
            ready.set()
            callback()
            stopped.set()

        hotkey = SessionEscapeHotkey(
            lambda: holder[0].stop(),
            platform_name="win32",
            runner=runner,
        )
        holder.append(hotkey)

        self.assertTrue(hotkey.start())
        self.assertTrue(stopped.wait(0.5))
        self.assertFalse(hotkey.registered)


if __name__ == "__main__":
    unittest.main()
