"""
tests/test_web_tools.py
Tests for web_search and fetch_url actions.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from conversation_engine.action_registry import ACTIONS, ActionResult


class TestWebToolRegistration:
    def test_web_search_registered(self):
        assert "web_search" in ACTIONS

    def test_fetch_url_registered(self):
        assert "fetch_url" in ACTIONS

    def test_web_search_is_callable(self):
        assert callable(ACTIONS["web_search"])

    def test_fetch_url_is_callable(self):
        assert callable(ACTIONS["fetch_url"])


class TestFetchUrl:
    def test_rejects_non_http_scheme(self):
        result = ACTIONS["fetch_url"]("file:///etc/passwd")
        assert not result.success

    def test_rejects_empty_url(self):
        result = ACTIONS["fetch_url"]("")
        assert not result.success

    def test_rejects_ftp_scheme(self):
        result = ACTIONS["fetch_url"]("ftp://example.com/file.txt")
        assert not result.success


class TestWebSearch:
    def test_empty_query_returns_result(self):
        result = ACTIONS["web_search"]("")
        assert isinstance(result, ActionResult)

    def test_max_results_clamped(self):
        result = ACTIONS["web_search"]("test", max_results=100)
        assert isinstance(result, ActionResult)
        if result.success:
            assert result.data["results"] is not None


class TestStripHtml:
    def test_strips_simple_tags(self):
        from conversation_engine.tools.web_tools import _strip_html
        result = _strip_html("<p>Hello world</p>")
        assert "Hello world" in result
        assert "<p>" not in result

    def test_strips_script_blocks(self):
        from conversation_engine.tools.web_tools import _strip_html
        html = "<script>alert('xss')</script><p>safe</p>"
        result = _strip_html(html)
        assert "alert" not in result
        assert "safe" in result

    def test_strips_style_blocks(self):
        from conversation_engine.tools.web_tools import _strip_html
        html = "<style>.foo{}</style><p>text</p>"
        result = _strip_html(html)
        assert ".foo" not in result
        assert "text" in result

    def test_decodes_entities(self):
        from conversation_engine.tools.web_tools import _strip_html
        result = _strip_html("&amp;&lt;&gt;&quot;")
        assert "&" in result
        assert "<" in result
        assert ">" in result
        assert '"' in result

    def test_preserves_line_structure(self):
        from conversation_engine.tools.web_tools import _strip_html
        html = "<p>line1</p><p>line2</p>"
        result = _strip_html(html)
        assert "line1" in result
        assert "line2" in result
        assert "\n" in result
