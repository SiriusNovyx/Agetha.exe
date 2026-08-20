from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agetha.core.capabilities import (
    CapabilityController,
    CapabilityPolicy,
    CapabilityProfile,
)
from agetha.core.continuation import AuthorizedResource, ToolOutcome
from agetha.core.read_only_tools import (
    READ_ONLY_TOOL_COMMANDS,
    ReadOnlyToolExecutor,
    UnsafePublicURL,
    validate_public_http_url,
)


EXPECTED_COMMANDS = frozenset({
    "search_web",
    "fetch_webpage",
    "search_memory",
    "view_memory",
    "read_document",
    "read_file",
    "list_dir",
    "list_directory",
    "read_notepad",
    "list_tasks",
    "view_dreams",
    "view_emotions",
    "system_info",
    "recycle_bin_status",
    "monitor_process",
    "get_active_app",
    "list_running_apps",
})


def settings(**changes):
    values = {
        "enable_web_rag": True,
        "enable_longterm_memory": True,
        "enable_tasks": True,
        "enable_dreams": True,
        "enable_emotion_engine": True,
        "enable_process_awareness": True,
        "process_context_mode": "visible_apps",
        "process_max_visible_apps": 8,
        "web_search_max_results": 5,
        "web_fetch_max_chars": 500,
        "agent_max_tool_result_chars": 500,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def resolver_for(mapping):
    def resolve(host, *_args, **_kwargs):
        answer = mapping.get(host)
        if isinstance(answer, BaseException):
            raise answer
        return answer if answer is not None else ["93.184.216.34"]
    return resolve


class URLValidationTests(unittest.TestCase):
    def test_accepts_only_public_http_urls_and_normalizes(self):
        resolve = resolver_for({
            "example.test": ["93.184.216.34"],
            "mixed.test": ["93.184.216.34", "10.0.0.7"],
            "private.test": ["192.168.1.4"],
            "missing.test": OSError("dns offline"),
        })

        accepted = validate_public_http_url(
            "HTTPS://Example.Test/path?q=1#fragment", resolver=resolve,
        )
        self.assertTrue(accepted.allowed)
        self.assertEqual(accepted.normalized_url, "https://example.test/path?q=1")
        self.assertEqual(accepted.addresses, ("93.184.216.34",))

        rejected = [
            "file:///etc/passwd",
            "http://user:password@example.test/",
            "http://localhost/",
            "http://127.0.0.1/",
            "http://10.1.2.3/",
            "http://169.254.169.254/latest/meta-data/",
            "http://192.0.2.1/",
            "http://[::1]/",
            "http://[2606:4700:4700::1111%25eth0]/",
            "http://[64:ff9b::a00:1]/",
            "http://private.test/",
            "http://mixed.test/",
            "http://missing.test/",
        ]
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(validate_public_http_url(url, resolver=resolve).allowed)

        literal = validate_public_http_url("https://8.8.8.8", resolver=resolve)
        self.assertTrue(literal.allowed)
        self.assertEqual(literal.normalized_url, "https://8.8.8.8/")


class ReadOnlyToolExecutorTests(unittest.TestCase):
    def make_executor(self, **kwargs):
        kwargs.setdefault("settings", settings())
        kwargs.setdefault("redactor", lambda value: value.replace("SECRET", "[REDACTED]"))
        kwargs.setdefault("resolver", resolver_for({}))
        return ReadOnlyToolExecutor(**kwargs)

    def test_allowlist_is_exact_and_effect_commands_never_run(self):
        self.assertEqual(READ_ONLY_TOOL_COMMANDS, EXPECTED_COMMANDS)
        effect = Mock(return_value="should never run")
        executor = self.make_executor(functions={
            "get_clipboard": effect,
            "take_screenshot": effect,
            "write_file": effect,
            "delete_file": effect,
        })

        for command in ("get_clipboard", "take_screenshot", "write_file", "delete_file"):
            with self.subTest(command=command):
                outcome = executor.execute(command, {}, lambda: False)
                self.assertIsInstance(outcome, ToolOutcome)
                self.assertFalse(outcome.success)
                self.assertFalse(outcome.continuation_allowed)
        effect.assert_not_called()

    def test_feature_gates_block_before_dependencies(self):
        cases = [
            ("search_web", {"query": "q"}, "enable_web_rag"),
            ("fetch_webpage", {"url": "https://example.test"}, "enable_web_rag"),
            ("search_memory", {"query": "q"}, "enable_longterm_memory"),
            ("list_tasks", {}, "enable_tasks"),
            ("view_dreams", {}, "enable_dreams"),
            ("view_emotions", {}, "enable_emotion_engine"),
            ("monitor_process", {"process_name": "app.exe"}, "enable_process_awareness"),
            ("get_active_app", {}, "enable_process_awareness"),
            ("list_running_apps", {}, "enable_process_awareness"),
        ]
        for command, arguments, gate in cases:
            with self.subTest(command=command):
                dependency = Mock(return_value=[])
                executor = self.make_executor(
                    settings=settings(**{gate: False}),
                    functions={command: dependency, "safe_fetch": dependency},
                )
                outcome = executor.execute(command, arguments, lambda: False)
                self.assertFalse(outcome.success)
                self.assertIn("disabled", outcome.provider_context)
                dependency.assert_not_called()

        episodic = Mock(return_value=["memory"])
        executor = self.make_executor(
            settings=settings(enable_longterm_memory=False),
            functions={"view_memory": episodic},
        )
        self.assertTrue(executor.execute("view_memory", {}, lambda: False).success)
        episodic.assert_called_once()

    def test_process_mode_off_blocks_process_readers(self):
        reader = Mock(return_value=[])
        executor = self.make_executor(
            settings=settings(process_context_mode="off"),
            functions={"list_running_apps": reader},
        )
        outcome = executor.execute("list_running_apps", {}, lambda: False)
        self.assertFalse(outcome.success)
        reader.assert_not_called()

    def test_compact_capability_blocks_process_reader_before_dependency(self):
        reader = Mock(return_value=[{"name": "private-app.exe"}])
        capabilities = CapabilityController(CapabilityPolicy(
            CapabilityProfile.COMPACT,
            {"ENABLE_PROCESS_AWARENESS": True},
        ))
        executor = self.make_executor(
            capability_policy=capabilities,
            functions={"list_running_apps": reader},
        )

        outcome = executor.execute("list_running_apps", {}, lambda: False)

        self.assertFalse(outcome.success)
        self.assertFalse(outcome.continuation_allowed)
        self.assertEqual(outcome.provider_context, "")
        reader.assert_not_called()

    def test_full_capability_allows_process_reader(self):
        capabilities = CapabilityController(CapabilityPolicy(
            CapabilityProfile.FULL,
            {"ENABLE_PROCESS_AWARENESS": True},
        ))
        executor = self.make_executor(
            capability_policy=capabilities,
            functions={"list_running_apps": lambda: [{"name": "editor.exe"}]},
        )

        outcome = executor.execute("list_running_apps", {}, lambda: False)

        self.assertTrue(outcome.success)
        self.assertIn("editor.exe", outcome.provider_context)

    def test_process_result_is_discarded_when_capability_generation_changes(self):
        capabilities = CapabilityController(CapabilityPolicy(
            CapabilityProfile.FULL,
            {"ENABLE_PROCESS_AWARENESS": True},
        ))

        def downgrade_while_reading():
            capabilities.begin_compact_transition()
            return [{"name": "private-app.exe"}]

        executor = self.make_executor(
            capability_policy=capabilities,
            functions={"list_running_apps": downgrade_while_reading},
        )

        outcome = executor.execute("list_running_apps", {}, lambda: False)

        self.assertFalse(outcome.success)
        self.assertFalse(outcome.continuation_allowed)
        self.assertEqual(outcome.provider_context, "")
        self.assertNotIn("private-app.exe", outcome.summary)

    def test_process_reader_uses_atomic_authorization_when_controller_supports_it(self):
        class TransitionWinsController:
            def authorize(self, _capability):
                return object()

            def is_authorized(self, _token):
                return True

            def perform_authorized(self, _token, _reader):
                return False, None

        reader = Mock(return_value=[{"name": "must-not-be-read.exe"}])
        executor = self.make_executor(
            capability_policy=TransitionWinsController(),
            functions={"list_running_apps": reader},
        )

        outcome = executor.execute("list_running_apps", {}, lambda: False)

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.provider_context, "")
        reader.assert_not_called()

    def test_callable_capability_policy_is_rechecked_after_process_reader(self):
        current = {
            "policy": CapabilityPolicy(
                CapabilityProfile.FULL,
                {"ENABLE_PROCESS_AWARENESS": True},
            ),
        }

        def downgrade_while_reading():
            current["policy"] = CapabilityPolicy(
                CapabilityProfile.COMPACT,
                {"ENABLE_PROCESS_AWARENESS": True},
            )
            return [{"name": "private-app.exe"}]

        executor = self.make_executor(
            capability_policy=lambda: current["policy"],
            functions={"list_running_apps": downgrade_while_reading},
        )

        outcome = executor.execute("list_running_apps", {}, lambda: False)

        self.assertFalse(outcome.success)
        self.assertFalse(outcome.continuation_allowed)
        self.assertEqual(outcome.provider_context, "")

    def test_compact_process_gate_does_not_block_ordinary_read_only_tools(self):
        capabilities = CapabilityController(CapabilityPolicy(
            CapabilityProfile.COMPACT,
            {"ENABLE_PROCESS_AWARENESS": True},
        ))
        executor = self.make_executor(
            capability_policy=capabilities,
            functions={"read_notepad": lambda: "ordinary note"},
        )

        outcome = executor.execute("read_notepad", {}, lambda: False)

        self.assertTrue(outcome.success)
        self.assertIn("ordinary note", outcome.provider_context)

    def test_cancellation_before_and_after_discards_results(self):
        reader = Mock(return_value="data")
        executor = self.make_executor(functions={"read_notepad": reader})

        before = executor.execute("read_notepad", {}, lambda: True)
        self.assertFalse(before.success)
        self.assertFalse(before.continuation_allowed)
        self.assertEqual(before.provider_context, "")
        reader.assert_not_called()

        state = {"cancelled": False}

        def read_then_cancel():
            state["cancelled"] = True
            return "late private data"

        executor = self.make_executor(functions={"read_notepad": read_then_cancel})
        after = executor.execute("read_notepad", {}, lambda: state["cancelled"])
        self.assertFalse(after.success)
        self.assertFalse(after.continuation_allowed)
        self.assertEqual(after.provider_context, "")

        broken = executor.execute(
            "read_notepad", {}, lambda: (_ for _ in ()).throw(RuntimeError("broken")),
        )
        self.assertFalse(broken.success)
        self.assertFalse(broken.continuation_allowed)

    def test_search_web_adds_only_validated_discovered_urls(self):
        results = [
            {
                "title": "Public SECRET",
                "url": "https://public.test/a#fragment",
                "snippet": "safe snippet",
            },
            {
                "title": "Duplicate",
                "url": "https://public.test/a",
                "snippet": "same URL",
            },
            {
                "title": "Loopback",
                "url": "http://127.0.0.1/admin",
                "snippet": "must not authorize",
            },
            {
                "title": "Private DNS",
                "url": "https://private.test/secret",
                "snippet": "must not authorize",
            },
        ]
        search = Mock(return_value=results)
        executor = self.make_executor(
            functions={"search_web": search},
            resolver=resolver_for({
                "public.test": ["93.184.216.34"],
                "private.test": ["10.0.0.9"],
            }),
        )

        outcome = executor.execute("search_web", {"query": "topic", "limit": 4})
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.sensitivity, "public")
        self.assertNotIn("SECRET", outcome.provider_context)
        self.assertIn("[REDACTED]", outcome.provider_context)
        self.assertIn("withheld: not validated as public", outcome.provider_context)
        self.assertEqual(
            outcome.discovered_resources,
            (AuthorizedResource("url", "https://public.test/a"),),
        )
        search.assert_called_once_with("topic", 4)

    def test_default_fetcher_ignores_legacy_redirect_following_helper(self):
        insecure_helper = Mock(return_value={"text": "should not run"})
        transport = Mock(return_value={
            "status": 200,
            "headers": {"Content-Type": "text/plain; charset=utf-8"},
            "body": b"safe public body",
        })
        executor = self.make_executor(functions={
            "fetch_webpage": insecure_helper,
            "safe_fetch_transport": transport,
        })
        outcome = executor.execute(
            "fetch_webpage", {"url": "https://public.test/page"}, lambda: False,
        )
        self.assertTrue(outcome.success)
        self.assertIn("safe public body", outcome.provider_context)
        insecure_helper.assert_not_called()
        transport.assert_called_once()

    def test_fetch_seam_validates_every_hop_and_rejects_private_redirect(self):
        visited = []

        def safe_fetch(url, *, validate_url, cancel_check, max_chars):
            self.assertFalse(cancel_check())
            visited.append(validate_url(url))
            # A safe client checks Location before making the next request.
            visited.append(validate_url("http://127.0.0.1/private"))
            self.fail("private redirect should raise before a request is made")

        executor = self.make_executor(safe_fetch=safe_fetch)
        outcome = executor.execute(
            "fetch_webpage", {"url": "https://public.test/start"}, lambda: False,
        )
        self.assertFalse(outcome.success)
        self.assertEqual(visited, ["https://public.test/start"])
        self.assertIn("not fetched", outcome.provider_context)

    def test_fetch_seam_public_redirect_workflow(self):
        calls = []

        def safe_fetch(url, *, validate_url, cancel_check, max_chars):
            calls.append((validate_url(url), max_chars, cancel_check()))
            redirected = validate_url("https://cdn.test/final#ignored")
            calls.append((redirected, max_chars, cancel_check()))
            return {
                "url": redirected,
                "redirect_chain": [url, redirected],
                "title": "Article SECRET",
                "text": "body",
                "truncated": False,
            }

        executor = self.make_executor(
            safe_fetch=safe_fetch,
            resolver=resolver_for({
                "public.test": ["93.184.216.34"],
                "cdn.test": ["142.250.72.14"],
            }),
        )
        outcome = executor.execute(
            "fetch_webpage", {"url": "https://public.test/start"}, lambda: False,
        )
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.sensitivity, "public")
        self.assertIn("https://cdn.test/final", outcome.provider_context)
        self.assertIn("[REDACTED]", outcome.provider_context)
        self.assertEqual(len(calls), 2)

    def test_local_readers_are_injected_redacted_bounded_and_sensitive(self):
        calls = []

        def with_argument(label):
            def read(value):
                calls.append((label, value))
                return f"{label} SECRET " + ("x" * 400)
            return read

        def without_argument(label, result=None):
            def read():
                calls.append((label, None))
                return result if result is not None else f"{label} SECRET"
            return read

        functions = {
            "search_memory": lambda query, limit: calls.append(("search_memory", (query, limit))) or ["memory SECRET"],
            "view_memory": with_argument("view_memory"),
            "read_document": with_argument("read_document"),
            "read_file": with_argument("read_file"),
            "list_dir": with_argument("list_dir"),
            "list_directory": with_argument("list_directory"),
            "read_notepad": without_argument("read_notepad"),
            "list_tasks": without_argument("list_tasks", ["task SECRET"]),
            "view_dreams": with_argument("view_dreams"),
            "view_emotions": with_argument("view_emotions"),
            "system_info": without_argument("system_info"),
            "recycle_bin_status": without_argument(
                "recycle_bin_status", (True, "2 items SECRET", {"items": 2}),
            ),
        }
        executor = self.make_executor(functions=functions, max_context_chars=120)
        cases = [
            ("search_memory", {"query": "needle", "limit": 3}, "private"),
            ("view_memory", {"limit": 2}, "private"),
            ("read_document", {"path": "virtual-doc"}, "private"),
            ("read_file", {"path": "virtual-file"}, "private"),
            ("list_dir", {"path": "virtual-dir"}, "private"),
            ("list_directory", {"path": "virtual-directory"}, "private"),
            ("read_notepad", {}, "private"),
            ("list_tasks", {}, "private"),
            ("view_dreams", {"limit": 2}, "private"),
            ("view_emotions", {"limit": 2}, "private"),
            ("system_info", {}, "internal"),
            ("recycle_bin_status", {}, "internal"),
        ]
        for command, arguments, sensitivity in cases:
            with self.subTest(command=command):
                outcome = executor.execute(command, arguments, lambda: False)
                self.assertTrue(outcome.success)
                self.assertEqual(outcome.sensitivity, sensitivity)
                self.assertLessEqual(len(outcome.provider_context), 120)
                self.assertNotIn("SECRET", outcome.provider_context)

        self.assertEqual(len(calls), len(cases))

    def test_ai_read_document_is_used_without_other_effects(self):
        ai = SimpleNamespace(read_document=Mock(return_value="document SECRET"))
        executor = self.make_executor(ai=ai)
        outcome = executor.execute("read_document", {"path": "authorized"})
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.sensitivity, "private")
        self.assertIn("[REDACTED]", outcome.provider_context)
        ai.read_document.assert_called_once_with("authorized")

    def test_process_results_are_minimized_and_never_expose_titles_or_pids(self):
        active = {
            "identity": {"pid": 123, "name": r"C:\Program Files\Browser\browser.exe"},
            "window_title": "Private SECRET title",
            "sensitive": False,
        }
        sensitive = {
            "identity": {"pid": 456, "name": "password-manager.exe"},
            "window_title": "Vault",
            "sensitive": True,
        }
        awareness = SimpleNamespace(
            get_active_app=Mock(return_value=active),
            list_running_apps=Mock(return_value=[active, sensitive]),
            monitor_process=Mock(return_value=[active["identity"]]),
        )
        executor = self.make_executor(process_awareness=awareness)

        active_outcome = executor.execute("get_active_app", {})
        listed = executor.execute("list_running_apps", {})
        monitored = executor.execute("monitor_process", {"process_name": "browser.exe"})
        for outcome in (active_outcome, listed, monitored):
            self.assertTrue(outcome.success)
            self.assertEqual(outcome.sensitivity, "private")
            self.assertNotIn("123", outcome.provider_context)
            self.assertNotIn("Private", outcome.provider_context)
            self.assertNotIn("Vault", outcome.provider_context)
        self.assertIn("browser.exe", active_outcome.provider_context)
        self.assertIn("Sensitive application", listed.provider_context)
        self.assertNotIn("password-manager.exe", listed.provider_context)

    def test_process_awareness_owner_can_be_detached_and_replaced(self):
        capabilities = CapabilityController(CapabilityPolicy(
            CapabilityProfile.FULL,
            {"ENABLE_PROCESS_AWARENESS": True},
        ))
        executor = self.make_executor(
            capability_policy=capabilities,
            process_awareness=SimpleNamespace(
                list_running_apps=lambda: [{"name": "first.exe"}],
            ),
        )
        first = executor.execute("list_running_apps", {})

        executor.set_process_awareness(None)
        detached = executor.execute("list_running_apps", {})
        executor.set_process_awareness(SimpleNamespace(
            list_running_apps=lambda: [{"name": "second.exe"}],
        ))
        second = executor.execute("list_running_apps", {})

        self.assertTrue(first.success)
        self.assertIn("first.exe", first.provider_context)
        self.assertFalse(detached.success)
        self.assertTrue(second.success)
        self.assertIn("second.exe", second.provider_context)
        self.assertNotIn("first.exe", second.provider_context)

    def test_setting_process_awareness_owner_does_not_probe_it(self):
        class ProbeRefusingOwner:
            def __getattribute__(self, _name):
                raise AssertionError("setter must not probe owner")

        executor = self.make_executor()

        executor.set_process_awareness(ProbeRefusingOwner())
        executor.set_process_awareness(None)

    def test_errors_do_not_echo_private_exception_payloads(self):
        def fail():
            raise PermissionError(r"C:\Users\Alice\SECRET\private.txt")

        executor = self.make_executor(functions={"read_notepad": fail})
        outcome = executor.execute("read_notepad", {})
        self.assertFalse(outcome.success)
        self.assertIn("PermissionError", outcome.provider_context)
        self.assertNotIn("Alice", outcome.provider_context)
        self.assertNotIn("SECRET", outcome.provider_context)

    def test_redaction_failure_blocks_continuation(self):
        def broken_redactor(_value):
            raise RuntimeError("redactor unavailable")

        executor = self.make_executor(
            redactor=broken_redactor,
            functions={"read_notepad": lambda: "private"},
        )
        outcome = executor.execute("read_notepad", {})
        self.assertFalse(outcome.success)
        self.assertFalse(outcome.continuation_allowed)
        self.assertEqual(outcome.provider_context, "")

    def test_missing_required_arguments_do_not_call_readers(self):
        reader = Mock(return_value="data")
        executor = self.make_executor(functions={
            "search_web": reader,
            "search_memory": reader,
            "read_file": reader,
            "list_dir": reader,
            "monitor_process": reader,
        })
        cases = [
            ("search_web", {}),
            ("search_memory", {}),
            ("read_file", {}),
            ("list_dir", {}),
            ("monitor_process", {}),
        ]
        for command, arguments in cases:
            with self.subTest(command=command):
                outcome = executor.execute(command, arguments)
                self.assertFalse(outcome.success)
                self.assertFalse(outcome.continuation_allowed)
        reader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
