"""Phase 3A web RAG tests — run: python test_phase3_web_rag.py"""

from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import MagicMock, patch

from ai_engine import AIEngine
import web_rag


class TestWebRagImports(unittest.TestCase):
    def test_module_imports_without_network(self) -> None:
        with patch("app_config.get_settings") as mock_settings:
            mock_settings.return_value.enable_web_rag = False
            self.assertFalse(web_rag.is_web_rag_enabled())
        self.assertEqual(web_rag.search_web(""), [])
        page = web_rag.fetch_webpage("")
        self.assertIn("error", page)


class TestFormatUntrustedWarnings(unittest.TestCase):
    def test_format_search_includes_warning(self) -> None:
        text = web_rag.format_search_results_for_prompt(
            [{"title": "Example", "url": "https://example.com", "snippet": "hi"}]
        )
        self.assertIn("untrusted", text.lower())
        self.assertIn("example.com", text)

    def test_format_fetch_includes_warning(self) -> None:
        text = web_rag.format_fetched_page_for_prompt(
            {"url": "https://example.com", "title": "Ex", "text": "body", "truncated": False}
        )
        self.assertIn("untrusted", text.lower())
        self.assertIn("body", text)


class TestDisabledPath(unittest.TestCase):
    def test_handler_skips_search_when_disabled(self) -> None:
        from command_handlers import handle_search_web

        app = MagicMock()
        app._ai = MagicMock()
        app._speak_and_continue = MagicMock()
        app._ai_query = MagicMock(return_value=None)
        app._dispatch_response = MagicMock()
        ctx = MagicMock()
        ctx.segments = []
        ctx.mood = "neutral"
        ctx.user_message = "latest news"
        ctx.shutdown_requested = False
        response = {"query": "news", "command": "search_web"}

        with patch("command_handlers.get_settings") as mock_settings:
            mock_settings.return_value.enable_web_rag = False
            with patch("web_rag.search_web") as mock_search:
                ok = handle_search_web(app, response, ctx)
                self.assertTrue(ok)
                mock_search.assert_not_called()

        threading.Event().wait(0.05)

    def test_parse_blocks_web_when_disabled(self) -> None:
        engine = AIEngine.__new__(AIEngine)
        engine._app_settings = MagicMock()
        engine._app_settings.enable_web_rag = False
        raw = json.dumps(
            {
                "command": "search_web",
                "query": "news",
                "mood": "thinking",
                "segments": [{"text": "Searching.", "pause": 0.0}],
            }
        )
        result = AIEngine._parse(engine, raw)
        self.assertEqual(result["command"], "speak")
        self.assertIn("disabled", result["segments"][0]["text"].lower())


class TestMockedNetwork(unittest.TestCase):
    def test_search_web_parses_ddg_html(self) -> None:
        html = """
        <div class="result">
          <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com">Example Site</a>
          <a class="result__snippet">A sample snippet.</a>
        </div>
        </div>
        """
        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()

        with patch("web_rag._requests_post", return_value=mock_resp):
            hits = web_rag.search_web("example", limit=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["url"], "https://example.com")
        self.assertIn("Example", hits[0]["title"])

    def test_fetch_webpage_extracts_text(self) -> None:
        html = "<html><head><title>Test</title></head><body><p>Hello world</p></body></html>"
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content = lambda **kw: [html.encode("utf-8")]

        with patch("web_rag._requests_get", return_value=mock_resp):
            page = web_rag.fetch_webpage("https://example.com")
        self.assertNotIn("error", page)
        self.assertIn("Hello world", page["text"])
        self.assertEqual(page["title"], "Test")


class TestWebRagAntiRecursion(unittest.TestCase):
    def test_parse_coerces_search_web_when_suppressed(self) -> None:
        engine = AIEngine.__new__(AIEngine)
        engine._app_settings = MagicMock()
        engine._app_settings.enable_web_rag = True
        raw = json.dumps(
            {
                "command": "search_web",
                "query": "news",
                "mood": "thinking",
                "segments": [{"text": "Again.", "pause": 0.0}],
            }
        )
        result = AIEngine._parse(engine, raw, suppress_web_rag=True)
        self.assertIn(result["command"], ("speak", "idle"))
        self.assertNotEqual(result["command"], "search_web")

    def test_parse_coerces_fetch_when_suppressed(self) -> None:
        engine = AIEngine.__new__(AIEngine)
        engine._app_settings = MagicMock()
        engine._app_settings.enable_web_rag = True
        raw = json.dumps(
            {
                "command": "fetch_webpage",
                "url": "https://example.com",
                "mood": "thinking",
                "segments": [],
            }
        )
        result = AIEngine._parse(engine, raw, suppress_web_rag=True)
        self.assertNotEqual(result["command"], "fetch_webpage")


if __name__ == "__main__":
    unittest.main()
