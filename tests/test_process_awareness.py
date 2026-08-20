from __future__ import annotations

import collections
import unittest
from pathlib import Path

from agetha.platform.process_awareness import (
    LinuxProcessBackend,
    ProcessAwareness,
    ProcessContextMode,
    ProcessIdentity,
    ProcessTransitionKind,
    RunningApplication,
    UnavailableProcessBackend,
    default_process_backend,
    identities_match,
)


class FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float = 1.0) -> None:
        self.value += seconds


def identity(pid: int, name: str, created: float | None) -> ProcessIdentity:
    return ProcessIdentity(pid, name, created)


def application(
    process: ProcessIdentity,
    *,
    hwnd: int,
    title: str = "Document",
    foreground: bool = False,
    visible: bool = True,
) -> RunningApplication:
    return RunningApplication(
        identity=process,
        window_handle=hwnd,
        window_title=title,
        bounds=(10, 20, 800, 600),
        visible=visible,
        foreground=foreground,
    )


class FakeBackend:
    status = "available_fake"

    def __init__(self) -> None:
        self.calls: collections.Counter[str] = collections.Counter()
        self.foreground: RunningApplication | None = None
        self.visible: tuple[RunningApplication, ...] = ()
        self.processes: tuple[ProcessIdentity, ...] = ()
        self.current: dict[int, ProcessIdentity] = {}
        self.count: int | None = 0
        self.fail_foreground = False
        self.fail_visible = False
        self.fail_processes = False

    def foreground_application(self) -> RunningApplication | None:
        self.calls["foreground"] += 1
        if self.fail_foreground:
            raise OSError("foreground unavailable")
        return self.foreground

    def visible_applications(self) -> tuple[RunningApplication, ...]:
        self.calls["visible"] += 1
        if self.fail_visible:
            raise OSError("window enumeration unavailable")
        return self.visible

    def identity_for_pid(self, pid: int) -> ProcessIdentity | None:
        self.calls["identity"] += 1
        return self.current.get(pid)

    def all_processes(self) -> tuple[ProcessIdentity, ...]:
        self.calls["processes"] += 1
        if self.fail_processes:
            raise OSError("process enumeration unavailable")
        return self.processes

    def process_count(self) -> int | None:
        self.calls["count"] += 1
        return self.count

    def process_is_current(self, expected: ProcessIdentity) -> bool:
        self.calls["liveness"] += 1
        current = self.current.get(expected.pid)
        return current is not None and identities_match(expected, current)

    def shutdown(self) -> None:
        self.calls["shutdown"] += 1


class TestProcessModels(unittest.TestCase):
    def test_identity_normalizes_to_basename_and_stable_creation_time(self) -> None:
        item = ProcessIdentity(42, r"C:\Program Files\Editor\Code.exe", 10.123456789)
        self.assertEqual(item.name, "Code.exe")
        self.assertEqual(item.created_at, 10.123457)
        self.assertEqual(item.key, (42, "code.exe", 10.123457))

    def test_pid_alone_never_matches_and_strict_requires_creation_time(self) -> None:
        expected = identity(42, "code.exe", 10.0)
        self.assertFalse(identities_match(expected, identity(42, "other.exe", 10.0)))
        self.assertFalse(identities_match(expected, identity(42, "code.exe", 20.0)))
        unknown_a = identity(42, "code.exe", None)
        unknown_b = identity(42, "CODE.EXE", None)
        self.assertTrue(identities_match(unknown_a, unknown_b, strict=False))
        self.assertFalse(identities_match(unknown_a, unknown_b, strict=True))


class TestProcessModes(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeBackend()
        self.clock = FakeClock()
        self.code = identity(10, "Code.exe", 1.0)
        self.code_app = application(
            self.code, hwnd=100, title=r"secret.txt - C:\Users\alice\Project", foreground=True
        )
        self.backend.foreground = self.code_app
        self.backend.visible = (self.code_app,)
        self.backend.current = {10: self.code}
        self.backend.processes = (self.code,)
        self.backend.count = 87

    def test_off_mode_performs_zero_backend_calls(self) -> None:
        awareness = ProcessAwareness(
            self.backend, mode="off", monotonic=self.clock
        )
        self.assertEqual(awareness.snapshot().status, "disabled")
        self.assertIsNone(awareness.get_active_app())
        self.assertEqual(awareness.list_running_apps(), ())
        self.assertEqual(awareness.monitor_process("code.exe"), ())
        self.assertFalse(awareness.validate_identity(self.code))
        self.assertEqual(awareness.poll(), ())
        self.assertEqual(awareness.provider_context(), "")
        self.assertEqual(self.backend.calls, collections.Counter())

        self.assertEqual(awareness.snapshot("all_processes").status, "disabled")
        self.assertEqual(self.backend.calls, collections.Counter())

    def test_foreground_only_does_not_enumerate_windows_or_processes(self) -> None:
        awareness = ProcessAwareness(
            self.backend, mode="foreground_only", monotonic=self.clock
        )
        snapshot = awareness.snapshot()
        self.assertIs(snapshot.foreground.identity, self.code)
        self.assertEqual(snapshot.visible_apps, (snapshot.foreground,))
        self.assertEqual(self.backend.calls, collections.Counter({"foreground": 1}))
        context = awareness.provider_context(snapshot)
        self.assertEqual(context, "Foreground: Visual Studio Code")
        self.assertNotIn("secret.txt", context)

        widened = awareness.snapshot("visible_apps")
        self.assertEqual(widened.visible_apps, (widened.foreground,))
        self.assertEqual(self.backend.calls["visible"], 0)
        self.assertEqual(self.backend.calls["processes"], 0)

    def test_visible_mode_is_foreground_first_deduplicated_and_limited(self) -> None:
        alpha = identity(11, "alpha.exe", 2.0)
        beta = identity(12, "beta.exe", 3.0)
        alpha_one = application(alpha, hwnd=101)
        alpha_two = application(alpha, hwnd=102, title="Second document")
        beta_app = application(beta, hwnd=103)
        self.backend.visible = (beta_app, alpha_one, self.code_app, alpha_two)
        awareness = ProcessAwareness(
            self.backend,
            mode=ProcessContextMode.VISIBLE_APPS,
            max_visible_apps=2,
            monotonic=self.clock,
        )
        snapshot = awareness.snapshot()
        self.assertEqual(
            [app.identity.name for app in snapshot.visible_apps],
            ["Code.exe", "alpha.exe"],
        )
        self.assertEqual(snapshot.total_process_count, 87)
        self.assertEqual(self.backend.calls["visible"], 1)
        self.assertEqual(self.backend.calls["count"], 1)

    def test_all_processes_inventory_is_separate_and_not_ordinary_context(self) -> None:
        background = identity(20, r"C:\Tools\background.exe", 4.0)
        self.backend.processes = (self.code, background)
        awareness = ProcessAwareness(
            self.backend, mode="all_processes", monotonic=self.clock
        )
        snapshot = awareness.snapshot()
        self.assertEqual(snapshot.total_process_count, 2)
        self.assertEqual(
            [item.name for item in awareness.local_inventory.processes],
            ["background.exe", "Code.exe"],
        )
        ordinary = awareness.provider_context(snapshot)
        explicit = awareness.provider_context(
            snapshot, explicit_all_processes=True
        )
        self.assertNotIn("background", ordinary)
        self.assertIn("background", explicit)
        self.assertNotIn("C:\\Tools", explicit)
        self.assertNotIn("PID", explicit)

    def test_temporary_mode_does_not_change_configured_mode(self) -> None:
        awareness = ProcessAwareness(
            self.backend, mode="visible_apps", monotonic=self.clock
        )
        snapshot = awareness.snapshot("foreground_only")
        self.assertEqual(snapshot.visible_apps[0].identity, self.code)
        self.assertEqual(awareness.mode, ProcessContextMode.VISIBLE_APPS)


class TestProcessPrivacy(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeBackend()
        self.clock = FakeClock()

    def test_builtin_sensitive_foreground_is_coarse(self) -> None:
        vault = identity(30, r"C:\Apps\KeePassXC.exe", 5.0)
        app = application(
            vault,
            hwnd=300,
            title="Personal Password Database.kdbx",
            foreground=True,
        )
        self.backend.foreground = app
        self.backend.visible = (app,)
        self.backend.count = 1
        awareness = ProcessAwareness(self.backend, monotonic=self.clock)
        snapshot = awareness.snapshot()
        self.assertTrue(snapshot.foreground.sensitive)
        context = awareness.provider_context(snapshot)
        self.assertIn("Sensitive application active", context)
        self.assertNotIn("KeePass", context)
        self.assertNotIn("Database", context)

    def test_configured_exclusion_matches_executable_without_extension(self) -> None:
        private = identity(31, "PrivateEditor.exe", 6.0)
        app = application(private, hwnd=301, foreground=True)
        self.backend.foreground = app
        self.backend.visible = (app,)
        awareness = ProcessAwareness(
            self.backend,
            excluded_apps="privateeditor",
            monotonic=self.clock,
        )
        self.assertTrue(awareness.snapshot().foreground.sensitive)

    def test_sensitive_title_is_suppressed_even_for_an_ordinary_browser(self) -> None:
        browser = identity(32, "chrome.exe", 7.0)
        app = application(
            browser,
            hwnd=302,
            title="Example Bank - Account 1234",
            foreground=True,
        )
        self.backend.foreground = app
        self.backend.visible = (app,)
        awareness = ProcessAwareness(self.backend, monotonic=self.clock)
        self.assertTrue(awareness.snapshot().foreground.sensitive)

    def test_context_never_contains_titles_paths_pid_or_creation_time(self) -> None:
        editor = identity(33, r"C:\Users\alice\Apps\Editor.exe", 1700000000.0)
        app = application(
            editor,
            hwnd=303,
            title=r"C:\Users\alice\private\notes.txt",
            foreground=True,
        )
        self.backend.foreground = app
        self.backend.visible = (app,)
        awareness = ProcessAwareness(self.backend, monotonic=self.clock)
        context = awareness.provider_context(awareness.snapshot())
        self.assertIn("Editor", context)
        for forbidden in ("alice", "notes.txt", "1700000000", "303", "C:\\"):
            self.assertNotIn(forbidden, context)


class TestProcessQueriesAndValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeBackend()
        self.clock = FakeClock()

    def test_monitor_process_uses_exact_basename_aliases(self) -> None:
        exact = identity(40, "notepad.exe", 8.0)
        near = identity(41, "mynotepad.exe", 9.0)
        self.backend.processes = (near, exact)
        awareness = ProcessAwareness(self.backend, monotonic=self.clock)
        self.assertEqual(awareness.monitor_process("notepad"), (exact,))
        self.assertEqual(awareness.monitor_process(r"C:\Windows\notepad.exe"), (exact,))

    def test_strict_validation_detects_pid_reuse_and_missing_creation_time(self) -> None:
        original = identity(50, "editor.exe", 10.0)
        awareness = ProcessAwareness(self.backend, monotonic=self.clock)
        self.backend.current[50] = original
        self.assertTrue(awareness.validate_identity(original, strict=True))
        self.backend.current[50] = identity(50, "editor.exe", 20.0)
        self.assertFalse(awareness.validate_identity(original, strict=True))
        unknown = identity(51, "editor.exe", None)
        self.backend.current[51] = unknown
        self.assertFalse(awareness.validate_identity(unknown, strict=True))
        self.assertTrue(awareness.validate_identity(unknown, strict=False))

    def test_partial_foreground_failure_preserves_visible_results(self) -> None:
        editor = identity(52, "editor.exe", 11.0)
        self.backend.fail_foreground = True
        self.backend.visible = (application(editor, hwnd=500),)
        self.backend.count = 9
        awareness = ProcessAwareness(self.backend, monotonic=self.clock)
        snapshot = awareness.snapshot()
        self.assertIsNone(snapshot.foreground)
        self.assertEqual(snapshot.visible_apps[0].identity, editor)
        self.assertIn("degraded:foreground", snapshot.status)

    def test_process_enumeration_failure_is_honest_and_empty(self) -> None:
        self.backend.fail_processes = True
        awareness = ProcessAwareness(
            self.backend, mode="all_processes", monotonic=self.clock
        )
        snapshot = awareness.snapshot()
        self.assertIsNone(snapshot.total_process_count)
        self.assertEqual(awareness.local_inventory.processes, ())
        self.assertEqual(awareness.local_inventory.status, "degraded")


class TestProcessTransitions(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeBackend()
        self.clock = FakeClock()
        self.original = identity(60, "alpha.exe", 12.0)
        self.original_app = application(
            self.original, hwnd=600, title="Alpha", foreground=True
        )
        self.backend.foreground = self.original_app
        self.backend.visible = (self.original_app,)
        self.backend.current = {60: self.original}
        self.backend.count = 1
        self.awareness = ProcessAwareness(self.backend, monotonic=self.clock)

    def test_first_poll_is_silent_and_unchanged_poll_is_deduplicated(self) -> None:
        self.assertEqual(self.awareness.poll(), ())
        self.clock.advance()
        self.assertEqual(self.awareness.poll(), ())

    def test_new_visible_foreground_process_produces_start_focus_and_appearance(self) -> None:
        self.awareness.poll()
        newcomer = identity(61, "beta.exe", 13.0)
        newcomer_app = application(
            newcomer, hwnd=601, title="Beta", foreground=True
        )
        old_background = application(
            self.original, hwnd=600, title="Alpha", foreground=False
        )
        self.backend.foreground = newcomer_app
        self.backend.visible = (old_background, newcomer_app)
        self.backend.current[61] = newcomer
        self.clock.advance()
        kinds = {item.kind for item in self.awareness.poll()}
        self.assertEqual(kinds, {
            ProcessTransitionKind.PROCESS_STARTED,
            ProcessTransitionKind.FOREGROUND_APP_CHANGED,
            ProcessTransitionKind.VISIBLE_APP_APPEARED,
        })

    def test_hidden_window_is_not_reported_as_process_exit_when_still_alive(self) -> None:
        self.awareness.poll()
        self.backend.foreground = None
        self.backend.visible = ()
        self.clock.advance()
        kinds = [item.kind for item in self.awareness.poll()]
        self.assertIn(ProcessTransitionKind.VISIBLE_APP_HIDDEN, kinds)
        self.assertIn(ProcessTransitionKind.FOREGROUND_APP_CHANGED, kinds)
        self.assertNotIn(ProcessTransitionKind.PROCESS_EXITED, kinds)

    def test_process_exit_is_distinct_from_visibility_loss(self) -> None:
        self.awareness.poll()
        self.backend.foreground = None
        self.backend.visible = ()
        self.backend.current.clear()
        self.clock.advance()
        kinds = [item.kind for item in self.awareness.poll()]
        self.assertIn(ProcessTransitionKind.PROCESS_EXITED, kinds)
        self.assertIn(ProcessTransitionKind.VISIBLE_APP_HIDDEN, kinds)

    def test_pid_reuse_is_exit_plus_start_and_invalidates_identity(self) -> None:
        self.awareness.poll()
        reused = identity(60, "alpha.exe", 99.0)
        reused_app = application(reused, hwnd=600, title="Alpha", foreground=True)
        self.backend.foreground = reused_app
        self.backend.visible = (reused_app,)
        self.backend.current[60] = reused
        self.clock.advance()
        kinds = [item.kind for item in self.awareness.poll()]
        self.assertIn(ProcessTransitionKind.PROCESS_EXITED, kinds)
        self.assertIn(ProcessTransitionKind.PROCESS_STARTED, kinds)
        self.assertFalse(self.awareness.validate_identity(self.original))

    def test_publisher_receives_coarse_sensitive_records(self) -> None:
        published = []
        awareness = ProcessAwareness(
            self.backend, monotonic=self.clock, publisher=published.append
        )
        awareness.poll()
        vault = identity(62, "Bitwarden.exe", 14.0)
        vault_app = application(
            vault, hwnd=602, title="Personal Vault", foreground=True
        )
        self.backend.foreground = vault_app
        self.backend.visible = (vault_app,)
        self.backend.current[62] = vault
        self.clock.advance()
        awareness.poll()
        sensitive = [item for item in published if item.sensitive]
        self.assertTrue(sensitive)
        self.assertTrue(all(item.identity is None for item in sensitive))
        self.assertTrue(all(item.summary == "Sensitive application state changed" for item in sensitive))


class TestPlatformDegradationAndShutdown(unittest.TestCase):
    def test_wayland_never_runs_global_window_discovery(self) -> None:
        commands: list[list[str]] = []

        def fail_run(command, **_kwargs):
            commands.append(command)
            raise AssertionError("Wayland window command must not run")

        backend = LinuxProcessBackend(
            env={"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"},
            which=lambda _name: "/usr/bin/tool",
            run=fail_run,
            psutil_module=None,
            proc_root=Path("missing-proc-root-for-process-awareness-test"),
        )
        self.assertIsNone(backend.foreground_application())
        self.assertEqual(backend.visible_applications(), ())
        self.assertEqual(commands, [])
        self.assertIn("wayland", backend.status)

    def test_x11_without_tools_degrades_without_guessing_windows(self) -> None:
        backend = LinuxProcessBackend(
            env={"XDG_SESSION_TYPE": "x11", "DISPLAY": ":1"},
            which=lambda _name: None,
            psutil_module=None,
        )
        self.assertIsNone(backend.foreground_application())
        self.assertEqual(backend.visible_applications(), ())
        self.assertEqual(backend.status, "degraded_x11_tools_unavailable")

    def test_unsupported_platform_factory_is_import_safe(self) -> None:
        backend = default_process_backend(platform_name="plan9")
        self.assertIsInstance(backend, UnavailableProcessBackend)
        self.assertIsNone(backend.foreground_application())

    def test_shutdown_is_idempotent_and_discards_state(self) -> None:
        backend = FakeBackend()
        clock = FakeClock()
        item = identity(70, "editor.exe", 15.0)
        app = application(item, hwnd=700, foreground=True)
        backend.foreground = app
        backend.visible = (app,)
        awareness = ProcessAwareness(backend, monotonic=clock)
        awareness.poll()
        awareness.shutdown()
        awareness.shutdown()
        self.assertTrue(awareness.is_shutdown)
        self.assertEqual(backend.calls["shutdown"], 1)
        calls_before = backend.calls.copy()
        self.assertEqual(awareness.poll(), ())
        self.assertEqual(awareness.snapshot().status, "shutdown")
        self.assertEqual(backend.calls, calls_before)
        self.assertIsNone(awareness.local_inventory)


if __name__ == "__main__":
    unittest.main()
