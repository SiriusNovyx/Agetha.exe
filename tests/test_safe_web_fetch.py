from __future__ import annotations

import unittest
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

import agetha.core.read_only_tools as read_only_tools
from agetha.core.read_only_tools import (
    ReadOnlyToolExecutor,
    safe_fetch_public_webpage,
    validate_public_http_url,
)


def _settings():
    return SimpleNamespace(
        enable_web_rag=True,
        web_fetch_max_chars=200,
        agent_max_tool_result_chars=500,
    )


def _resolver(mapping=None):
    answers = dict(mapping or {})

    def resolve(host, *_args, **_kwargs):
        return answers.get(host, ["8.8.8.8"])

    return resolve


class SafeWebFetchTests(unittest.TestCase):
    def _executor(self, transport, *, resolver=None, **kwargs):
        return ReadOnlyToolExecutor(
            settings=_settings(),
            redactor=lambda value: value,
            resolver=resolver or _resolver(),
            functions={"safe_fetch_transport": transport},
            **kwargs,
        )

    def test_public_redirect_is_validated_before_each_transport_call(self):
        calls = []

        def transport(url, *, addresses, cancel_check, timeout_sec, max_bytes):
            calls.append((url, tuple(addresses), timeout_sec, max_bytes))
            self.assertFalse(cancel_check())
            if len(calls) == 1:
                return {
                    "status": 302,
                    "headers": {"Location": "https://cdn.test/final#fragment"},
                }
            return {
                "status": 200,
                "headers": {"Content-Type": "text/html; charset=UTF-8"},
                "body": (
                    b"<html><head><title>Safe title</title>"
                    b"<script>hidden instruction</script></head>"
                    b"<body>Visible article</body></html>"
                ),
            }

        executor = self._executor(transport)
        outcome = executor.execute(
            "fetch_webpage", {"url": "https://origin.test/start"}, lambda: False,
        )

        self.assertTrue(outcome.success)
        self.assertEqual(
            [call[0] for call in calls],
            ["https://origin.test/start", "https://cdn.test/final"],
        )
        self.assertEqual(calls[0][1], ("8.8.8.8",))
        self.assertIn("Safe title", outcome.provider_context)
        self.assertIn("Visible article", outcome.provider_context)
        self.assertNotIn("hidden instruction", outcome.provider_context)

    def test_redirect_to_private_dns_is_rejected_before_second_request(self):
        calls = []

        def transport(url, **_kwargs):
            calls.append(url)
            return {
                "status": 302,
                "headers": {"Location": "http://private.test/admin"},
            }

        executor = self._executor(
            transport,
            resolver=_resolver({
                "origin.test": ["8.8.8.8"],
                "private.test": ["10.0.0.8"],
            }),
        )
        outcome = executor.execute(
            "fetch_webpage", {"url": "https://origin.test/start"}, lambda: False,
        )

        self.assertFalse(outcome.success)
        self.assertEqual(calls, ["https://origin.test/start"])
        self.assertIn("not fetched", outcome.provider_context)

    def test_dns_rebinding_to_private_is_rejected_before_transport(self):
        calls = {"count": 0}

        def rebinding_resolver(_host, *_args, **_kwargs):
            calls["count"] += 1
            # Executor validation and the outer per-hop callback see public DNS;
            # the immediately pre-connect resolution sees the changed answer.
            return ["8.8.8.8"] if calls["count"] < 3 else ["192.168.1.9"]

        transport = Mock()
        executor = self._executor(transport, resolver=rebinding_resolver)
        outcome = executor.execute(
            "fetch_webpage", {"url": "https://rebind.test/"}, lambda: False,
        )

        self.assertFalse(outcome.success)
        transport.assert_not_called()
        self.assertIn("not fetched", outcome.provider_context)

    def test_body_text_and_transport_limits_are_bounded(self):
        observed = {}

        def transport(url, *, addresses, cancel_check, timeout_sec, max_bytes):
            observed.update(
                url=url,
                addresses=tuple(addresses),
                timeout_sec=timeout_sec,
                max_bytes=max_bytes,
            )
            return {
                "status": 200,
                "headers": {"Content-Type": "text/plain; charset=unknown-codec"},
                "body": ("é" + "x" * 500).encode("utf-8"),
            }

        resolve = _resolver()

        def validate(url):
            result = validate_public_http_url(url, resolver=resolve)
            if not result.allowed:
                raise AssertionError(result.reason)
            return result.normalized_url

        page = safe_fetch_public_webpage(
            "https://bounded.test/",
            validate_url=validate,
            cancel_check=lambda: False,
            max_chars=40,
            max_bytes=80,
            resolver=resolve,
            request_hop=transport,
        )

        self.assertLessEqual(len(str(page["text"])), 40)
        self.assertTrue(page["truncated"])
        self.assertEqual(observed["max_bytes"], 80)
        self.assertEqual(observed["addresses"], ("8.8.8.8",))
        self.assertGreater(observed["timeout_sec"], 0)

    def test_binary_content_type_fails_without_exposing_body(self):
        transport = Mock(return_value={
            "status": 200,
            "headers": {"Content-Type": "application/octet-stream"},
            "body": b"SECRET BINARY BODY",
        })
        outcome = self._executor(transport).execute(
            "fetch_webpage", {"url": "https://files.test/archive"}, lambda: False,
        )

        self.assertFalse(outcome.success)
        self.assertIn("unsupported_content_type", outcome.provider_context)
        self.assertNotIn("SECRET", outcome.provider_context)

    def test_cancellation_prevents_transport(self):
        transport = Mock()
        outcome = self._executor(transport).execute(
            "fetch_webpage", {"url": "https://public.test/"}, lambda: True,
        )
        self.assertFalse(outcome.success)
        self.assertFalse(outcome.continuation_allowed)
        transport.assert_not_called()

    def test_slow_resolver_times_out_without_transport(self):
        entered = threading.Event()
        release = threading.Event()

        def slow_resolver(_host, *_args, **_kwargs):
            entered.set()
            release.wait(2.0)
            return ["8.8.8.8"]

        transport = Mock()
        executor = self._executor(
            transport,
            resolver=slow_resolver,
            fetch_timeout_sec=0.05,
        )
        started = time.monotonic()
        try:
            outcome = executor.execute(
                "fetch_webpage", {"url": "https://slow-dns.test/"}, lambda: False,
            )
        finally:
            release.set()

        self.assertTrue(entered.is_set())
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertFalse(outcome.success)
        self.assertIn("TimeoutError", outcome.provider_context)
        transport.assert_not_called()

    def test_cancellation_during_resolver_returns_without_transport(self):
        cancelled = threading.Event()
        release = threading.Event()

        def resolver_cancelled_in_flight(_host, *_args, **_kwargs):
            cancelled.set()
            release.wait(2.0)
            return ["8.8.8.8"]

        transport = Mock()
        executor = self._executor(
            transport,
            resolver=resolver_cancelled_in_flight,
            fetch_timeout_sec=1.0,
        )
        started = time.monotonic()
        try:
            outcome = executor.execute(
                "fetch_webpage",
                {"url": "https://cancelled-dns.test/"},
                cancelled.is_set,
            )
        finally:
            release.set()

        self.assertLess(time.monotonic() - started, 0.5)
        self.assertFalse(outcome.success)
        self.assertFalse(outcome.continuation_allowed)
        self.assertEqual(outcome.provider_context, "")
        transport.assert_not_called()

    def test_resolution_runner_cannot_return_after_deadline_then_call_transport(self):
        now = {"value": 0.0}

        def clock():
            return now["value"]

        def late_runner(resolve, *, deadline, **_kwargs):
            answer = resolve()
            now["value"] = deadline
            return answer

        transport = Mock()
        executor = self._executor(
            transport,
            clock=clock,
            resolution_runner=late_runner,
            fetch_timeout_sec=1.0,
        )
        outcome = executor.execute(
            "fetch_webpage", {"url": "https://late-dns.test/"}, lambda: False,
        )

        self.assertFalse(outcome.success)
        self.assertIn("TimeoutError", outcome.provider_context)
        transport.assert_not_called()

    def test_builtin_result_does_not_reresolve_during_postflight(self):
        resolver_calls = []

        def resolver(host, *_args, **_kwargs):
            resolver_calls.append(host)
            return ["8.8.8.8"]

        transport = Mock(return_value={
            "status": 200,
            "headers": {"Content-Type": "text/plain"},
            "body": b"body",
        })
        outcome = self._executor(transport, resolver=resolver).execute(
            "fetch_webpage", {"url": "https://one-hop.test/"}, lambda: False,
        )

        self.assertTrue(outcome.success)
        # Initial policy validation, per-hop callback, and immediately
        # pre-transport pinning.  There is intentionally no fourth postflight DNS call.
        self.assertEqual(resolver_calls, ["one-hop.test"] * 3)

    def test_mismatched_skip_end_tag_cannot_expose_script_text(self):
        transport = Mock(return_value={
            "status": 200,
            "headers": {"Content-Type": "text/html"},
            "body": (
                b"<script>hidden<style>also hidden</style></noscript>"
                b"must remain hidden</script><p>visible text</p>"
            ),
        })
        outcome = self._executor(transport).execute(
            "fetch_webpage", {"url": "https://html.test/"}, lambda: False,
        )

        self.assertTrue(outcome.success)
        self.assertIn("visible text", outcome.provider_context)
        self.assertNotIn("must remain hidden", outcome.provider_context)
        self.assertNotIn("also hidden", outcome.provider_context)

    def test_custom_fetcher_plain_text_result_keeps_validated_initial_url(self):
        custom_fetch = Mock(return_value="plain adapter result")
        executor = ReadOnlyToolExecutor(
            settings=_settings(),
            redactor=lambda value: value,
            resolver=_resolver(),
            safe_fetch=custom_fetch,
        )
        outcome = executor.execute(
            "fetch_webpage", {"url": "https://custom.test/page"}, lambda: False,
        )

        self.assertTrue(outcome.success)
        self.assertIn("https://custom.test/page", outcome.provider_context)
        self.assertIn("plain adapter result", outcome.provider_context)
        custom_fetch.assert_called_once()

    def test_pinned_transport_uses_one_absolute_deadline_for_slow_body(self):
        now = {"value": 0.0}
        timeouts = []

        def clock():
            return now["value"]

        class FakeSocket:
            def settimeout(self, value):
                timeouts.append(value)

        class FakeResponse:
            status = 200

            @staticmethod
            def getheaders():
                return [("Content-Type", "text/plain")]

            @staticmethod
            def read(_size):
                now["value"] += 0.25
                return b"x"

        class FakeConnection:
            def __init__(self, *_args, **_kwargs):
                self.sock = None

            def connect(self):
                now["value"] += 0.2
                self.sock = FakeSocket()

            def request(self, *_args, **_kwargs):
                now["value"] += 0.2

            def getresponse(self):
                now["value"] += 0.2
                return FakeResponse()

            def close(self):
                return None

        with patch.object(read_only_tools, "_PinnedHTTPConnection", FakeConnection):
            with self.assertRaises(TimeoutError):
                read_only_tools._request_public_http_hop(
                    "http://public.test/",
                    addresses=("8.8.8.8",),
                    cancel_check=lambda: False,
                    timeout_sec=1.0,
                    max_bytes=100,
                    deadline=1.0,
                    clock=clock,
                )

        self.assertGreaterEqual(len(timeouts), 3)
        self.assertEqual(timeouts, sorted(timeouts, reverse=True))
        self.assertGreater(timeouts[0], timeouts[-1])


if __name__ == "__main__":
    unittest.main()
