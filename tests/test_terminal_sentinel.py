"""Focused safety tests for the event-driven Terminal Sentinel MVP."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from agetha import app_config
from agetha.features.terminal_sentinel import (
    SentinelEventContext,
    SentinelOutcome,
    TerminalErrorEvent,
    TerminalSentinel,
    TerminalSentinelConfig,
)
from agetha.ui import dashboard


class _Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _config(**overrides) -> TerminalSentinelConfig:
    base = TerminalSentinelConfig(
        enabled=True,
        allowed_apps=("Code", "WindowsTerminal"),
        allowed_title_patterns=("terminal", "re:visual studio code$"),
        cooldown_seconds=60,
        minimum_confidence=45,
        max_context_chars=400,
    )
    return replace(base, **overrides)


def _event(snippet: str = "Traceback (most recent call last): ValueError: boom") -> TerminalErrorEvent:
    return TerminalErrorEvent(
        category="py_runtime",
        label="Python runtime error",
        snippet=snippet,
        confidence=90,
    )


def _context(**overrides) -> SentinelEventContext:
    values = dict(
        window_title="project — Visual Studio Code",
        process_name="Code.exe",
        window_identity="hwnd:44",
        ocr_context="Traceback (most recent call last):\nValueError: boom",
        validated=True,
    )
    values.update(overrides)
    return SentinelEventContext(**values)


class TestTerminalSentinel(unittest.TestCase):
    def test_default_config_has_conservative_developer_app_preset(self) -> None:
        raw = app_config.default_config_dict()
        self.assertEqual(raw["ENABLE_TERMINAL_SENTINEL"], "no")
        configured = TerminalSentinelConfig.from_settings(SimpleNamespace(raw=raw))
        apps = {value.casefold() for value in configured.allowed_apps}
        for required in (
            "windowsterminal.exe", "powershell.exe", "pwsh.exe", "cmd.exe",
            "code.exe", "vscodium.exe", "devenv.exe", "pycharm64.exe",
            "idea64.exe", "wezterm-gui.exe", "alacritty.exe", "mintty.exe",
        ):
            with self.subTest(required=required):
                self.assertIn(required, apps)
        for forbidden in ("chrome.exe", "discord.exe", "notepad.exe", "*"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, apps)

    def test_dashboard_exposes_all_restart_required_sentinel_settings(self) -> None:
        sections = {
            title: {key: needs_restart for key, _kind, needs_restart, _choices in items}
            for title, items in dashboard._SETTING_SECTIONS
        }
        sentinel = sections["Terminal Sentinel — restart required"]
        self.assertEqual(
            set(sentinel),
            {
                "ENABLE_TERMINAL_SENTINEL",
                "TERMINAL_SENTINEL_APPS",
                "TERMINAL_SENTINEL_TITLE_PATTERNS",
                "TERMINAL_SENTINEL_COOLDOWN_SEC",
            },
        )
        self.assertTrue(all(sentinel.values()))

    def test_disabled_by_default_and_empty_allowlist_fail_closed(self) -> None:
        self.assertFalse(TerminalSentinelConfig().enabled)
        disabled = TerminalSentinel(ignore_store_path=None)
        result = disabled.evaluate_event(_event(), _context())
        self.assertEqual(result.reason, "disabled")

        enabled_but_open = TerminalSentinel(
            TerminalSentinelConfig(enabled=True), ignore_store_path=None,
        )
        result = enabled_but_open.evaluate_event(_event(), _context())
        self.assertEqual(result.reason, "not_allowlisted")

    def test_allowlist_and_validated_event_create_local_notification(self) -> None:
        sentinel = TerminalSentinel(_config(), ignore_store_path=None)
        result = sentinel.evaluate_event(_event(), _context())
        self.assertEqual(result.outcome, SentinelOutcome.NOTIFY)
        self.assertIsNotNone(result.notification)
        self.assertFalse(result.notification.request_focus)

    def test_allowlist_ocr_and_own_window_exclusions(self) -> None:
        sentinel = TerminalSentinel(_config(), ignore_store_path=None)
        denied = sentinel.evaluate_event(
            _event(), _context(process_name="notepad.exe", window_title="notes"),
        )
        self.assertEqual(denied.reason, "not_allowlisted")

        excluded = TerminalSentinel(
            _config(excluded_apps=("Code.exe",)), ignore_store_path=None,
        ).evaluate_event(_event(), _context())
        self.assertEqual(excluded.reason, "ocr_exclusion")

        own = sentinel.evaluate_event(
            _event(), _context(is_agetha_window=True, window_title="Agetha.exe"),
        )
        self.assertEqual(own.reason, "private_or_own_window")

    def test_application_allowlist_does_not_match_arbitrary_window_titles(self) -> None:
        sentinel = TerminalSentinel(
            _config(
                allowed_apps=("Code",),
                allowed_title_patterns=(),
            ),
            ignore_store_path=None,
        )
        result = sentinel.evaluate_event(
            _event(),
            _context(
                process_name="chrome.exe",
                window_title="How to code — browser",
            ),
        )
        self.assertEqual(result.reason, "not_allowlisted")

    def test_unvalidated_low_confidence_and_bare_error_are_rejected(self) -> None:
        sentinel = TerminalSentinel(_config(), ignore_store_path=None)
        self.assertEqual(
            sentinel.evaluate_event(_event(), _context(validated=False)).reason,
            "event_not_validated",
        )
        self.assertEqual(
            sentinel.evaluate_event(replace(_event(), confidence=10), _context()).reason,
            "low_confidence",
        )
        generic = TerminalErrorEvent(
            category="py_runtime", label="error", snippet="one error in ordinary prose",
            confidence=90,
        )
        self.assertEqual(
            sentinel.evaluate_event(generic, _context()).reason,
            "low_signal_text",
        )

    def test_duplicate_cooldown_and_changed_snippet(self) -> None:
        clock = _Clock()
        sentinel = TerminalSentinel(_config(), clock=clock, ignore_store_path=None)
        first = sentinel.evaluate_event(_event(), _context())
        self.assertTrue(first.should_notify)
        sentinel.dismiss(first.notification.notification_id)

        clock.value += 10
        duplicate = sentinel.evaluate_event(_event(), _context())
        self.assertEqual(duplicate.reason, "duplicate_or_cooldown")

        changed = sentinel.evaluate_event(
            _event("Traceback (most recent call last): TypeError: changed"), _context(),
        )
        self.assertTrue(changed.should_notify)
        sentinel.dismiss(changed.notification.notification_id)

        clock.value += 60
        after_cooldown = sentinel.evaluate_event(_event(), _context())
        self.assertTrue(after_cooldown.should_notify)

    def test_dismiss_and_persisted_ignore_store_no_raw_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "ignored.json"
            sentinel = TerminalSentinel(_config(), ignore_store_path=store)
            first = sentinel.evaluate_event(_event(), _context())
            self.assertTrue(sentinel.ignore_pattern(first.notification.notification_id))
            payload = store.read_text(encoding="utf-8")
            self.assertNotIn("ValueError", payload)
            self.assertRegex(json.loads(payload)[0]["snippet_hash"], r"^[0-9a-f]{64}$")

            restarted = TerminalSentinel(_config(), ignore_store_path=store)
            ignored = restarted.evaluate_event(_event(), _context())
            self.assertEqual(ignored.reason, "ignored_pattern")

            other = restarted.evaluate_event(
                _event("Traceback (most recent call last): TypeError: different"), _context(),
            )
            self.assertTrue(other.should_notify)
            self.assertTrue(restarted.dismiss(other.notification.notification_id))

    def test_explain_is_explicit_redacted_bounded_and_non_executing(self) -> None:
        sentinel = TerminalSentinel(
            _config(max_context_chars=256), ignore_store_path=None,
        )
        context = _context(
            ocr_context=(
                "Traceback (most recent call last):\n"
                "api_key=VERY_SECRET_VALUE_123456789\n"
                "ValueError: boom\n" + "x" * 1000
            ),
        )
        notice = sentinel.evaluate_event(_event(), context).notification
        # Evaluation itself returns only local data; it takes no provider callback.
        request = sentinel.explain(notice.notification_id)
        self.assertEqual(request.origin, "terminal_sentinel")
        self.assertFalse(request.allow_command_execution)
        self.assertIn("UNTRUSTED LOCAL OCR", request.screen_context)
        self.assertIn("[REDACTED]", request.screen_context)
        self.assertNotIn("VERY_SECRET_VALUE", request.screen_context)
        self.assertLess(len(request.screen_context), 900)
        self.assertIsNone(sentinel.explain(notice.notification_id))

    def test_presence_etiquette_queues_without_focus_steal(self) -> None:
        sentinel = TerminalSentinel(_config(), ignore_store_path=None)
        restricted = SimpleNamespace(
            allow_popup=False,
            allow_focus_request=False,
            queue_nonurgent=True,
            reason="presentation mode",
        )
        result = sentinel.evaluate_event(_event(), _context(), etiquette=restricted)
        self.assertEqual(result.outcome, SentinelOutcome.QUEUED)
        self.assertFalse(result.notification.request_focus)
        self.assertEqual(sentinel.drain_queued(etiquette=restricted), ())

        allowed = SimpleNamespace(
            allow_popup=True,
            allow_focus_request=False,
            queue_nonurgent=False,
            reason="clear",
        )
        ready = sentinel.drain_queued(etiquette=allowed)
        self.assertEqual(len(ready), 1)
        self.assertFalse(ready[0].request_focus)

    def test_queued_notice_expires_and_revalidates_before_display(self) -> None:
        clock = _Clock()
        sentinel = TerminalSentinel(
            _config(queue_ttl_seconds=30),
            clock=clock,
            ignore_store_path=None,
        )
        restricted = SimpleNamespace(
            allow_popup=False,
            allow_focus_request=False,
            queue_nonurgent=True,
            reason="presentation mode",
        )
        allowed = SimpleNamespace(
            allow_popup=True,
            allow_focus_request=False,
            queue_nonurgent=False,
            reason="clear",
        )
        queued = sentinel.evaluate_event(_event(), _context(), etiquette=restricted)
        clock.value += 31
        self.assertEqual(sentinel.drain_queued(etiquette=allowed), ())

        fresh = sentinel.evaluate_event(
            _event("Traceback (most recent call last): TypeError: fresh"),
            _context(),
            etiquette=restricted,
        )
        notice = sentinel.drain_queued(etiquette=allowed)[0]
        self.assertEqual(notice.notification_id, fresh.notification.notification_id)
        self.assertTrue(sentinel.notification_is_current(
            notice,
            window_identity="hwnd:44",
            matches=(_event("Traceback (most recent call last): TypeError: fresh"),),
        ))
        self.assertFalse(sentinel.notification_is_current(
            notice,
            window_identity="hwnd:99",
            matches=(_event("Traceback (most recent call last): TypeError: fresh"),),
        ))

    def test_stop_is_idempotent_and_discards_pending(self) -> None:
        sentinel = TerminalSentinel(_config(), ignore_store_path=None)
        sentinel.stop()
        sentinel.stop()
        self.assertEqual(sentinel.evaluate_event(_event(), _context()).reason, "stopped")


if __name__ == "__main__":
    unittest.main()
