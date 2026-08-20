from __future__ import annotations

import unittest

from agetha.computer_use.activation import (
    extract_local_activation,
    is_explicit_computer_use_request,
    parse_configured_apps,
    requested_application,
)


class ComputerUseActivationTests(unittest.TestCase):
    def test_exact_thai_payload_is_kept_local(self) -> None:
        result = extract_local_activation("Open Notepad and type สวัสดี")
        self.assertEqual(result.payloads["user_text_1"], "สวัสดี")
        self.assertNotIn("สวัสดี", result.sanitized_request)
        self.assertIn("payload:user_text_1", result.sanitized_request)
        self.assertEqual(result.requested_app.process_name, "notepad.exe")

    def test_quoted_payload_preserves_inner_text(self) -> None:
        result = extract_local_activation('Please type "  hello, world  " into Notepad')
        self.assertEqual(result.payloads["user_text_1"], "  hello, world  ")
        self.assertNotIn("hello, world", result.sanitized_request)

    def test_nested_quotes_cannot_leak_payload_suffix_to_provider_text(self) -> None:
        exact = 'He said "hunter2" today'
        result = extract_local_activation(
            'Open Notepad and type "He said "hunter2" today"',
        )

        self.assertEqual(result.payloads["user_text_1"], exact)
        self.assertNotIn("He said", result.sanitized_request)
        self.assertNotIn("hunter2", result.sanitized_request)
        self.assertNotIn("today", result.sanitized_request)
        self.assertIn("payload:user_text_1", result.sanitized_request)

    def test_no_typing_request_has_no_payload(self) -> None:
        result = extract_local_activation("Open Notepad")
        self.assertEqual(dict(result.payloads), {})
        self.assertFalse(result.typing_authorized)

    def test_submit_requires_explicit_words(self) -> None:
        plain = extract_local_activation("Open Notepad and type hello")
        submit = extract_local_activation("Open Notepad and type hello then press Enter")
        self.assertFalse(plain.submit_authorized)
        self.assertTrue(submit.submit_authorized)
        self.assertEqual(submit.payloads["user_text_1"], "hello")

    def test_configured_app_parser_rejects_paths_and_arguments(self) -> None:
        apps = parse_configured_apps(
            r"foo, BAR.EXE, C:\Tools\bad.exe, bad.exe --flag, ../oops.exe"
        )
        self.assertEqual(
            tuple(item.process_name.casefold() for item in apps),
            ("foo.exe", "bar.exe"),
        )

    def test_model_cannot_invent_unmentioned_configured_app(self) -> None:
        self.assertIsNone(
            requested_application("do the desktop task", configured_apps="trusted.exe"),
        )
        selected = requested_application(
            "Use Trusted for this task", configured_apps="trusted.exe",
        )
        self.assertEqual(selected.process_name.casefold(), "trusted.exe")

    def test_repr_contains_refs_not_payload(self) -> None:
        result = extract_local_activation("type secret-value into Notepad")
        self.assertNotIn("secret-value", repr(result))
        self.assertIn("user_text_1", repr(result))

    def test_plain_type_request_is_not_explicit_computer_use(self) -> None:
        result = extract_local_activation("type hello")
        self.assertFalse(is_explicit_computer_use_request("type hello", result))
        explicit = extract_local_activation("Computer Use: type hello")
        self.assertTrue(
            is_explicit_computer_use_request("Computer Use: type hello", explicit),
        )

    def test_app_name_inside_payload_does_not_create_target_authority(self) -> None:
        quoted = extract_local_activation('type "I use Notepad"')
        plain = extract_local_activation("type Notepad")

        self.assertIsNone(quoted.requested_app)
        self.assertIsNone(plain.requested_app)
        self.assertFalse(is_explicit_computer_use_request('type "I use Notepad"', quoted))
        self.assertFalse(is_explicit_computer_use_request("type Notepad", plain))

    def test_submit_words_inside_payload_do_not_authorize_enter(self) -> None:
        result = extract_local_activation('Open Notepad and type "press Enter"')

        self.assertEqual(result.payloads["user_text_1"], "press Enter")
        self.assertFalse(result.submit_authorized)

    def test_configured_app_suffix_is_not_part_of_exact_payload(self) -> None:
        result = extract_local_activation(
            "type secret into Foo.exe",
            configured_apps="foo.exe",
        )

        self.assertEqual(result.payloads["user_text_1"], "secret")
        self.assertEqual(result.requested_app.process_name.casefold(), "foo.exe")

    def test_submit_named_target_does_not_authorize_enter(self) -> None:
        result = extract_local_activation(
            "type hello into Submit.exe",
            configured_apps="submit.exe",
        )

        self.assertEqual(result.payloads["user_text_1"], "hello")
        self.assertEqual(result.requested_app.process_name.casefold(), "submit.exe")
        self.assertFalse(result.submit_authorized)

    def test_explicit_trailing_submit_clause_authorizes_enter(self) -> None:
        result = extract_local_activation(
            "type hello into Foo.exe then submit",
            configured_apps="foo.exe",
        )

        self.assertEqual(result.payloads["user_text_1"], "hello")
        self.assertTrue(result.submit_authorized)


if __name__ == "__main__":
    unittest.main()
