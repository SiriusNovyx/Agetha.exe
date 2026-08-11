from __future__ import annotations

import unittest
from unittest.mock import patch

from agetha.platform import unicode_typing as ut


TARGET = ut.TypingTarget(
    stable_id="win:100:10",
    title="Untitled - Notepad",
    process_name="notepad.exe",
    window_handle=100,
)
OTHER_TARGET = ut.TypingTarget(
    stable_id="win:200:20",
    title="Other editor",
    process_name="editor.exe",
    window_handle=200,
)


class FakePlatform:
    def __init__(self, *, platform_name: str = "windows", session_type: str = "desktop") -> None:
        self.platform_name = platform_name
        self.session_type = session_type
        self.target: ut.TypingTarget | None = TARGET
        self.target_reads = 0
        self.target_sequence: list[ut.TypingTarget | None] | None = None
        self.clipboard = "previous clipboard"
        self.clipboard_readable = True
        self.clipboard_writable = True
        self.clipboard_reads = 0
        self.clipboard_writes: list[str] = []
        self.native_calls: list[str] = []
        self.native_results: list[ut.NativeSendResult] = []
        self.paste_calls = 0
        self.paste_ok = True
        self.paste_hook = None
        self.activation_calls: list[ut.TypingTarget] = []
        self.activation_ok = True
        self.sleep_calls: list[float] = []
        self.sleep_hook = None
        self.cancelled = False
        self.shutting_down = False

    def get_target(self) -> ut.TypingTarget | None:
        self.target_reads += 1
        if self.target_sequence:
            index = min(self.target_reads - 1, len(self.target_sequence) - 1)
            return self.target_sequence[index]
        return self.target

    def native_send(self, text: str) -> ut.NativeSendResult:
        self.native_calls.append(text)
        if self.native_results:
            return self.native_results.pop(0)
        return ut.NativeSendResult(True, len(text), len(ut.utf16_code_units(text)))

    def read_clipboard(self) -> ut.ClipboardSnapshot:
        self.clipboard_reads += 1
        if not self.clipboard_readable:
            return ut.ClipboardSnapshot(False, None)
        return ut.ClipboardSnapshot(True, self.clipboard)

    def write_clipboard(self, text: str) -> bool:
        self.clipboard_writes.append(text)
        if not self.clipboard_writable:
            return False
        self.clipboard = text
        return True

    def paste(self) -> bool:
        self.paste_calls += 1
        if self.paste_hook is not None:
            self.paste_hook()
        return self.paste_ok

    def activate(self, target: ut.TypingTarget) -> bool:
        self.activation_calls.append(target)
        if self.activation_ok:
            self.target = target
        return self.activation_ok

    def sleep(self, delay: float) -> None:
        self.sleep_calls.append(delay)
        if self.sleep_hook is not None:
            self.sleep_hook()

    def dependencies(self, *, native: bool = True, paste: bool = True) -> ut.UnicodeTypingDependencies:
        return ut.UnicodeTypingDependencies(
            platform_name=self.platform_name,
            session_type=self.session_type,
            get_focused_target=self.get_target,
            send_native_unicode=self.native_send if native else None,
            read_clipboard=self.read_clipboard,
            write_clipboard=self.write_clipboard,
            send_paste_shortcut=self.paste if paste else None,
            activate_target=self.activate,
            sleep=self.sleep,
            cancel_requested=lambda: self.cancelled,
            shutdown_requested=lambda: self.shutting_down,
        )

    def engine(self, *, native: bool = True, paste: bool = True, **kwargs) -> ut.UnicodeTypingEngine:
        return ut.UnicodeTypingEngine(
            self.dependencies(native=native, paste=paste),
            clipboard_settle_seconds=0.0,
            **kwargs,
        )


class TestUnicodeHelpers(unittest.TestCase):
    def test_utf16_supplementary_characters_become_surrogate_pairs(self) -> None:
        self.assertEqual(ut.utf16_code_units("A"), (0x0041,))
        self.assertEqual(ut.utf16_code_units("👩"), (0xD83D, 0xDC69))
        self.assertEqual(ut.utf16_code_units("🩷"), (0xD83E, 0xDE77))

    def test_safe_clusters_keep_required_sequences_together(self) -> None:
        sequences = (
            "a\u0301",       # combining mark
            "✌\ufe0f",       # variation selector
            "👍🏽",           # emoji modifier
            "👩‍💻",          # zero-width joiner
            "🇹🇭",           # regional-indicator pair
            "\ud83d\udc69",  # explicitly supplied UTF-16 surrogate pair
        )
        for sequence in sequences:
            with self.subTest(sequence=sequence.encode("unicode_escape")):
                self.assertEqual(list(ut.iter_safe_clusters(sequence)), [sequence])
                self.assertEqual(list(ut.iter_safe_chunks(sequence, 1)), [sequence])

    def test_safe_chunks_round_trip_without_normalization(self) -> None:
        text = "Aก\u0e49-👩‍💻-e\u0301-🇺🇳-مرحبا"
        chunks = list(ut.iter_safe_chunks(text, 4))
        self.assertEqual("".join(chunks), text)
        self.assertIn("ก\u0e49", list(ut.iter_safe_clusters(text)))

    def test_mode_and_speed_validation(self) -> None:
        self.assertEqual(ut.parse_mode(" AUTO "), ut.TypingMode.AUTO)
        self.assertEqual(ut.parse_speed("slow"), ut.TypingSpeed.SLOW)
        with self.assertRaises(ValueError):
            ut.parse_mode("keyboard-layout")
        with self.assertRaises(ValueError):
            ut.parse_speed("unbounded")

    def test_preview_contains_metadata_but_not_typed_text(self) -> None:
        secret = "password=do-not-display"
        preview = ut.build_typing_preview(
            secret,
            TARGET,
            platform_name="windows",
            session_type="desktop",
        )
        self.assertEqual(preview.character_count, len(secret))
        self.assertEqual(preview.line_count, 1)
        self.assertFalse(preview.reversible)
        self.assertIn("potentially-sensitive-text", preview.reasons)
        self.assertNotIn(secret, repr(preview))

    def test_preview_reuses_authoritative_secret_redaction_for_text_and_titles(self) -> None:
        secrets = (
            "AKIAABCDEFGHIJKLMNOP",
            "eyJabcdefghij.abcdefghijkl.abcdefghijk",
            "ABCD-EFGH-IJKL-MNOP",
        )
        for secret in secrets:
            with self.subTest(secret=secret[:4]):
                preview = ut.build_typing_preview(secret, TARGET)
                self.assertIn("potentially-sensitive-text", preview.reasons)
                titled = ut.build_typing_preview(
                    "safe",
                    ut.TypingTarget(
                        stable_id="secret-title",
                        title=f"Editor — {secret}",
                        process_name="editor.exe",
                    ),
                )
                self.assertEqual(titled.target_window_title, "[sensitive title hidden]")


class TestWindowsSendInputEncoding(unittest.TestCase):
    def test_sendinput_uses_unicode_scan_codes_and_no_virtual_enter_or_tab(self) -> None:
        batches: list[list[tuple[int, int, int]]] = []

        def fake_send(events) -> int:
            batches.append(
                [(int(event.ki.wVk), int(event.ki.wScan), int(event.ki.dwFlags)) for event in events]
            )
            return len(events)

        with patch.object(ut.sys, "platform", "win32"), patch.object(
            ut, "_send_input_batch", side_effect=fake_send
        ):
            result = ut.send_windows_unicode("A👩\n\t")

        self.assertTrue(result.success)
        events = [event for batch in batches for event in batch]
        self.assertTrue(events)
        self.assertTrue(all(virtual_key == 0 for virtual_key, _scan, _flags in events))
        self.assertNotIn(0x0D, [virtual_key for virtual_key, _scan, _flags in events])
        self.assertNotIn(0x09, [virtual_key for virtual_key, _scan, _flags in events])
        scans = [scan for _virtual_key, scan, flags in events if not flags & ut._KEYEVENTF_KEYUP]
        self.assertEqual(scans, list(ut.utf16_code_units("A👩\n\t")))

    def test_partial_sendinput_reports_only_complete_characters(self) -> None:
        def partial(events) -> int:
            # One complete UTF-16 unit (down/up), insufficient for the emoji.
            return 2

        with patch.object(ut.sys, "platform", "win32"), patch.object(
            ut, "_send_input_batch", side_effect=partial
        ):
            result = ut.send_windows_unicode("👩")
        self.assertFalse(result.success)
        self.assertEqual(result.characters_sent, 0)
        self.assertEqual(result.utf16_units_sent, 1)


class TestUnicodeTypingEngine(unittest.TestCase):
    LANGUAGE_CASES = (
        "Hello, world!",
        "สวัสดี",
        "ขอบคุณครับ",
        "こんにちは世界",
        "你好，世界",
        "안녕하세요",
        "مرحباً بالعالم",
        "שלום עולם",
        "नमस्ते दुनिया",
        "Привет, мир",
        "👩‍💻🩷✨",
        "Agetha สวัสดี こんにちは مرحباً 👋",
    )

    def test_exact_multilingual_text_is_never_rewritten(self) -> None:
        for text in self.LANGUAGE_CASES:
            with self.subTest(text=text):
                fake = FakePlatform()
                result = fake.engine().type_text(text)
                self.assertTrue(result.success)
                self.assertEqual(fake.native_calls, [text])
                self.assertEqual(result.characters_requested, len(text))
                self.assertEqual(result.characters_sent, len(text))

    def test_combining_zwj_and_modifier_text_remains_exact(self) -> None:
        text = "e\u0301 ✌\ufe0f 👩🏽‍💻"
        fake = FakePlatform()
        result = fake.engine().type_text(text)
        self.assertTrue(result.success)
        self.assertEqual("".join(fake.native_calls), text)

    def test_empty_input_and_unknown_options_fail_without_effects(self) -> None:
        cases = (
            ("", "auto", "normal"),
            ("hello", "unknown", "normal"),
            ("hello", "auto", "warp"),
        )
        for text, mode, speed in cases:
            with self.subTest(text=text, mode=mode, speed=speed):
                fake = FakePlatform()
                result = fake.engine().type_text(text, mode=mode, speed=speed)
                self.assertFalse(result.success)
                self.assertEqual(fake.native_calls, [])
                self.assertEqual(fake.clipboard_writes, [])
                self.assertEqual(fake.paste_calls, 0)

    def test_preview_mode_never_enters_text(self) -> None:
        fake = FakePlatform()
        result = fake.engine().type_text("show only", mode="preview")
        self.assertTrue(result.success)
        self.assertEqual(result.method, "preview")
        self.assertEqual(result.characters_sent, 0)
        self.assertEqual(fake.native_calls, [])
        self.assertEqual(fake.clipboard_writes, [])

    def test_multiline_and_long_text_require_preview_then_remain_exact(self) -> None:
        cases = ("line one\nline two", "x" * 301)
        for text in cases:
            with self.subTest(length=len(text)):
                fake = FakePlatform()
                engine = fake.engine()
                blocked = engine.type_text(text)
                self.assertFalse(blocked.success)
                self.assertEqual(blocked.method, "preview-required")
                allowed = engine.type_text(text, preview_approved=True)
                self.assertTrue(allowed.success)
                self.assertEqual(fake.native_calls, [text])

    def test_direct_unicode_failure_falls_back_to_compare_and_restore_paste(self) -> None:
        fake = FakePlatform()
        fake.native_results = [ut.NativeSendResult(False, 0, 0)]
        result = fake.engine().type_text("สวัสดี", mode="auto")
        self.assertTrue(result.success)
        self.assertEqual(result.method, "clipboard-paste-fallback")
        self.assertEqual(fake.native_calls, ["สวัสดี"])
        self.assertEqual(fake.paste_calls, 1)
        self.assertEqual(fake.clipboard, "previous clipboard")
        self.assertTrue(result.clipboard_restored)

    def test_partial_native_failure_never_pastes_full_text_over_partial_prefix(self) -> None:
        fake = FakePlatform()
        fake.native_results = [ut.NativeSendResult(False, 2, 2)]
        result = fake.engine().type_text("hello", mode="auto")
        self.assertFalse(result.success)
        self.assertEqual(result.characters_sent, 2)
        self.assertEqual(fake.paste_calls, 0)
        self.assertEqual(fake.clipboard_writes, [])

    def test_explicit_unicode_mode_never_uses_clipboard_fallback(self) -> None:
        fake = FakePlatform()
        fake.native_results = [ut.NativeSendResult(False, 0, 0)]
        result = fake.engine().type_text("hello", mode="unicode")
        self.assertFalse(result.success)
        self.assertEqual(fake.paste_calls, 0)
        self.assertEqual(fake.clipboard_writes, [])

    def test_explicit_paste_mode_never_calls_native_sender(self) -> None:
        fake = FakePlatform()
        result = fake.engine().type_text("hello", mode="paste")
        self.assertTrue(result.success)
        self.assertEqual(fake.native_calls, [])
        self.assertEqual(fake.paste_calls, 1)

    def test_clipboard_is_restored_only_when_value_is_still_agethas(self) -> None:
        fake = FakePlatform()
        result = fake.engine(native=False).type_text("hello", mode="paste")
        self.assertTrue(result.success)
        self.assertTrue(result.clipboard_restored)
        self.assertEqual(fake.clipboard_writes, ["hello", "previous clipboard"])

        changed = FakePlatform()
        changed.paste_hook = lambda: setattr(changed, "clipboard", "user copied this")
        result = changed.engine(native=False).type_text("hello", mode="paste")
        self.assertTrue(result.success)
        self.assertFalse(result.clipboard_restored)
        self.assertEqual(changed.clipboard, "user copied this")
        self.assertEqual(changed.clipboard_writes, ["hello"])

    def test_unreadable_clipboard_is_never_blindly_restored(self) -> None:
        fake = FakePlatform()
        fake.clipboard_readable = False
        result = fake.engine(native=False).type_text("hello", mode="paste")
        self.assertTrue(result.success)
        self.assertFalse(result.clipboard_restored)
        self.assertEqual(fake.clipboard_writes, ["hello"])

    def test_focus_change_before_input_causes_no_clipboard_write(self) -> None:
        fake = FakePlatform()
        fake.target_sequence = [TARGET, OTHER_TARGET]
        result = fake.engine(native=False).type_text("hello", mode="paste")
        self.assertFalse(result.success)
        self.assertIn("focused window changed", result.message.lower())
        self.assertEqual(fake.clipboard_writes, [])
        self.assertEqual(fake.paste_calls, 0)

    def test_focus_change_after_clipboard_write_restores_without_pasting(self) -> None:
        fake = FakePlatform()
        fake.target_sequence = [TARGET, TARGET, OTHER_TARGET]
        result = fake.engine(native=False).type_text("hello", mode="paste")
        self.assertFalse(result.success)
        self.assertTrue(result.clipboard_restored)
        self.assertEqual(fake.clipboard, "previous clipboard")
        self.assertEqual(fake.paste_calls, 0)

    def test_focus_change_during_paced_native_input_stops_at_boundary(self) -> None:
        fake = FakePlatform()

        def send_then_change(text: str) -> ut.NativeSendResult:
            fake.native_calls.append(text)
            fake.target = OTHER_TARGET
            return ut.NativeSendResult(True, len(text), len(ut.utf16_code_units(text)))

        dependencies = fake.dependencies()
        dependencies.send_native_unicode = send_then_change
        engine = ut.UnicodeTypingEngine(
            dependencies,
            paced_chunk_utf16_units=1,
            clipboard_settle_seconds=0.0,
        )
        result = engine.type_text("abc", mode="paced", speed="instant")
        self.assertFalse(result.success)
        self.assertEqual(result.characters_sent, 1)
        self.assertEqual(fake.native_calls, ["a"])

    def test_pre_guard_intended_target_is_safely_reactivated_and_revalidated(self) -> None:
        fake = FakePlatform()
        # A guard dialog temporarily left another (for example Agetha's) window
        # focused, while TARGET was captured before the dialog appeared.
        fake.target = OTHER_TARGET
        result = fake.engine().type_text("hello", intended_target=TARGET)
        self.assertTrue(result.success)
        self.assertEqual(fake.activation_calls, [TARGET])
        self.assertEqual(fake.native_calls, ["hello"])

    def test_pre_guard_target_activation_failure_fails_closed(self) -> None:
        fake = FakePlatform()
        fake.target = OTHER_TARGET
        fake.activation_ok = False
        result = fake.engine().type_text("hello", intended_target=TARGET)
        self.assertFalse(result.success)
        self.assertEqual(result.method, "target-unavailable")
        self.assertEqual(fake.native_calls, [])
        self.assertEqual(fake.clipboard_writes, [])

    def test_capture_intended_target_is_explicit_and_exception_safe(self) -> None:
        fake = FakePlatform()
        dependencies = fake.dependencies()
        self.assertEqual(ut.capture_intended_target(dependencies), TARGET)

        dependencies.get_focused_target = lambda: (_ for _ in ()).throw(RuntimeError("closed"))
        self.assertIsNone(ut.capture_intended_target(dependencies))

    def test_user_clipboard_change_during_paced_paste_stops_without_overwrite(self) -> None:
        fake = FakePlatform(platform_name="linux", session_type="x11")

        def user_copy_during_delay() -> None:
            fake.clipboard = "new user value"

        fake.sleep_hook = user_copy_during_delay
        result = fake.engine(native=False, paced_chunk_utf16_units=1).type_text(
            "ab", mode="paced", speed="slow"
        )
        self.assertFalse(result.success)
        self.assertEqual(result.characters_sent, 1)
        self.assertEqual(fake.clipboard, "new user value")
        self.assertEqual(fake.clipboard_writes, ["a"])

    def test_cancellation_and_shutdown_stop_cleanly(self) -> None:
        for state in ("cancelled", "shutting_down"):
            with self.subTest(state=state):
                fake = FakePlatform()
                setattr(fake, state, True)
                result = fake.engine().type_text("hello")
                self.assertFalse(result.success)
                self.assertEqual(result.characters_sent, 0)
                self.assertEqual(fake.native_calls, [])

    def test_cancellation_during_paced_input_stops_at_next_boundary(self) -> None:
        fake = FakePlatform()

        def send_then_cancel(text: str) -> ut.NativeSendResult:
            fake.native_calls.append(text)
            fake.cancelled = True
            return ut.NativeSendResult(True, len(text), len(ut.utf16_code_units(text)))

        dependencies = fake.dependencies()
        dependencies.send_native_unicode = send_then_cancel
        engine = ut.UnicodeTypingEngine(
            dependencies,
            paced_chunk_utf16_units=1,
            clipboard_settle_seconds=0.0,
        )
        result = engine.type_text("abc", mode="paced", speed="instant")
        self.assertFalse(result.success)
        self.assertEqual(result.characters_sent, 1)
        self.assertEqual(fake.native_calls, ["a"])

    def test_shutdown_interrupts_windows_sendinput_at_a_bounded_batch(self) -> None:
        stopped = False
        calls = 0

        def send_then_shutdown(events) -> int:
            nonlocal stopped, calls
            calls += 1
            stopped = True
            return len(events)

        with patch.object(ut.sys, "platform", "win32"), patch.object(
            ut, "_send_input_batch", side_effect=send_then_shutdown,
        ):
            result = ut.send_windows_unicode(
                "a" * 256,
                stop_requested=lambda: stopped,
            )
        self.assertFalse(result.success)
        self.assertEqual(result.characters_sent, 128)
        self.assertEqual(calls, 1)

    def test_own_window_is_rejected_even_when_marked_by_handle(self) -> None:
        fake = FakePlatform()
        engine = ut.UnicodeTypingEngine(
            fake.dependencies(),
            own_window_handles={100},
            clipboard_settle_seconds=0.0,
        )
        result = engine.type_text("hello")
        self.assertFalse(result.success)
        self.assertEqual(result.method, "target-rejected")
        self.assertEqual(fake.native_calls, [])

    def test_terminal_target_requires_preview_and_admin_terminal_is_restricted(self) -> None:
        terminal = ut.TypingTarget(
            stable_id="terminal:1",
            title="PowerShell",
            process_name="pwsh.exe",
        )
        fake = FakePlatform()
        fake.target = terminal
        blocked = fake.engine().type_text("Write-Output hello")
        self.assertFalse(blocked.success)
        self.assertEqual(blocked.method, "preview-required")
        allowed = fake.engine().type_text("Write-Output hello", preview_approved=True)
        self.assertTrue(allowed.success)

        fake.target = ut.TypingTarget(
            stable_id="terminal:admin",
            title="Administrator: PowerShell",
            process_name="powershell.exe",
        )
        restricted = fake.engine().type_text("Write-Output hello", preview_approved=True)
        self.assertFalse(restricted.success)
        self.assertEqual(restricted.method, "target-rejected")
        confirmed = fake.engine().type_text(
            "Write-Output hello",
            preview_approved=True,
            allow_restricted_target=True,
        )
        self.assertTrue(confirmed.success)

    def test_wayland_copies_only_and_never_claims_typing_success(self) -> None:
        fake = FakePlatform(platform_name="linux", session_type="wayland")
        result = fake.engine(native=False, paste=False).type_text("สวัสดี")
        self.assertFalse(result.success)
        self.assertEqual(result.method, "clipboard-copy-only")
        self.assertEqual(result.characters_sent, 0)
        self.assertEqual(fake.clipboard, "สวัสดี")
        self.assertEqual(fake.paste_calls, 0)
        self.assertIn("manual paste", result.message.lower())

    def test_x11_uses_optional_clipboard_and_normal_paste_callbacks(self) -> None:
        fake = FakePlatform(platform_name="linux", session_type="x11")
        result = fake.engine(native=False).type_text("こんにちは")
        self.assertTrue(result.success)
        self.assertEqual(result.method, "clipboard-paste")
        self.assertEqual(fake.paste_calls, 1)
        self.assertEqual(fake.clipboard, "previous clipboard")

    def test_missing_target_copies_for_manual_paste_instead_of_claiming_success(self) -> None:
        fake = FakePlatform(platform_name="linux", session_type="x11")
        fake.target = None
        result = fake.engine(native=False, paste=False).type_text("שלום")
        self.assertFalse(result.success)
        self.assertEqual(result.method, "clipboard-copy-only")
        self.assertEqual(result.characters_sent, 0)
        self.assertEqual(fake.clipboard, "שלום")

    def test_messages_do_not_echo_secret_text_or_platform_exceptions(self) -> None:
        secret = "password=NeverEchoThis123!"
        fake = FakePlatform()
        result = fake.engine().type_text(secret)
        self.assertFalse(result.success)
        self.assertNotIn(secret, result.message)
        self.assertNotIn("NeverEchoThis", repr(result))

        dependencies = fake.dependencies()

        def leak_if_rendered(_text: str) -> ut.NativeSendResult:
            raise RuntimeError(secret)

        dependencies.send_native_unicode = leak_if_rendered
        failed = ut.UnicodeTypingEngine(
            dependencies,
            clipboard_settle_seconds=0.0,
        ).type_text(secret, preview_approved=True)
        self.assertNotIn(secret, failed.message)
        self.assertNotIn("NeverEchoThis", repr(failed))


if __name__ == "__main__":
    unittest.main()
