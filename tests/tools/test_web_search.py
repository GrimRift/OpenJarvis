"""Tests for the web_search tool (Tavily only, no DuckDuckGo fallback)."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

from openjarvis.core.registry import ToolRegistry
from openjarvis.tools.web_search import WebSearchTool


def _fake_tavily_module(search_return=None, search_side_effect=None):
    """Build a fake ``tavily`` module for injection into sys.modules."""
    fake_module = ModuleType("tavily")
    mock_client_cls = MagicMock()
    mock_client_instance = MagicMock()
    if search_side_effect is not None:
        mock_client_instance.search.side_effect = search_side_effect
    else:
        mock_client_instance.search.return_value = search_return or {}
    mock_client_cls.return_value = mock_client_instance
    fake_module.TavilyClient = mock_client_cls
    return fake_module, mock_client_cls


class TestWebSearchTool:
    def test_spec(self):
        tool = WebSearchTool(api_key="key")
        assert tool.spec.name == "web_search"
        assert tool.spec.category == "search"
        assert tool.spec.metadata == {"requires_api_key": "TAVILY_API_KEY"}
        assert "fallback" not in tool.spec.metadata

    def test_no_query(self):
        tool = WebSearchTool(api_key="key")
        result = tool.execute(query="")
        assert result.success is False
        assert "No query" in result.content

    def test_spec_parameters_require_query(self):
        tool = WebSearchTool(api_key="key")
        assert "query" in tool.spec.parameters["properties"]
        assert "query" in tool.spec.parameters["required"]

    def test_no_query_param(self):
        tool = WebSearchTool(api_key="key")
        result = tool.execute()
        assert result.success is False
        assert "No query" in result.content

    def test_url_query_fetches_directly(self):
        tool = WebSearchTool(api_key="key")
        mock_resp = MagicMock()
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.text = "<html><body><p>Hello world</p></body></html>"
        mock_resp.raise_for_status.return_value = None
        with (
            patch("openjarvis.tools.web_search.check_ssrf", return_value=None),
            patch("httpx.get", return_value=mock_resp),
        ):
            result = tool.execute(query="https://example.com/page")
        assert result.success is True
        assert "Hello world" in result.content
        assert result.metadata["mode"] == "fetch"

    def test_url_query_blocked_by_ssrf(self):
        tool = WebSearchTool(api_key="key")
        with patch(
            "openjarvis.tools.web_search.check_ssrf",
            return_value="blocked: private address",
        ):
            result = tool.execute(query="http://169.254.169.254/latest/meta-data")
        assert result.success is False
        assert "Failed to fetch URL" in result.content

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        fake_module, _ = _fake_tavily_module()
        with patch.dict(sys.modules, {"tavily": fake_module}):
            tool = WebSearchTool(api_key=None)
            result = tool.execute(query="latest openjarvis release")
        assert result.success is False
        assert "TAVILY_API_KEY is not configured" in result.content

    def test_tavily_success_no_fallback_attempted(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={
                "results": [
                    {
                        "title": "Example",
                        "url": "https://example.com",
                        "content": "Example summary",
                        "images": ["https://example.com/preview.jpg"],
                    }
                ],
                "usage": {"credits": 1},
            }
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            tool = WebSearchTool(api_key="key")
            result = tool.execute(query="latest openjarvis release")
        assert result.success is True
        assert result.metadata["engine"] == "tavily"
        assert result.metadata["num_results"] == 1
        assert result.metadata["credits"] == 1
        assert result.metadata["sources"] == [
            {
                "title": "Example",
                "url": "https://example.com",
                "summary": "Example summary",
                "image_url": "https://example.com/preview.jpg",
            }
        ]
        assert "Example" in result.content
        assert "example.com" in result.content
        mock_client_cls.assert_called_once_with(api_key="key")
        _, search_kwargs = mock_client_cls.return_value.search.call_args
        assert search_kwargs["include_images"] is True

    def test_tavily_error_returns_failure_not_duckduckgo(self):
        fake_module, _ = _fake_tavily_module(
            search_side_effect=RuntimeError("Tavily is down")
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            tool = WebSearchTool(api_key="key")
            result = tool.execute(query="latest openjarvis release")
        assert result.success is False
        assert "Tavily search error" in result.content
        assert "duckduckgo" not in result.content.lower()
        assert result.metadata.get("engine") != "duckduckgo"

    def test_tavily_not_installed(self, monkeypatch):
        # Force the import to fail regardless of whether tavily-python is
        # actually installed in this environment — sys.modules[name] = None
        # is the standard way to make `import tavily` raise ImportError.
        monkeypatch.setitem(sys.modules, "tavily", None)
        tool = WebSearchTool(api_key="key")
        result = tool.execute(query="latest openjarvis release")
        assert result.success is False
        assert "tavily-python not installed" in result.content
        assert "ddgs" not in result.content.lower()

    def test_max_results_forwarded(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={"results": []}
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            tool = WebSearchTool(api_key="key")
            tool.execute(query="latest openjarvis release", max_results=3)
        _, kwargs = mock_client_cls.return_value.search.call_args
        assert kwargs["max_results"] == 3

    def test_to_openai_function(self):
        tool = WebSearchTool(api_key="key")
        function = tool.to_openai_function()
        assert function["type"] == "function"
        assert function["function"]["name"] == "web_search"
        assert "query" in function["function"]["parameters"]["properties"]

    def test_empty_results(self):
        fake_module, _ = _fake_tavily_module(search_return={"results": []})
        with patch.dict(sys.modules, {"tavily": fake_module}):
            tool = WebSearchTool(api_key="key")
            result = tool.execute(query="obscure query")
        assert result.success is True
        assert result.content == "No results found."

    def test_tool_id(self):
        tool = WebSearchTool(api_key="key")
        assert tool.tool_id == "web_search"

    def test_registry_registration(self):
        ToolRegistry.register_value("web_search", WebSearchTool)
        assert ToolRegistry.contains("web_search")

    def test_tavily_results_use_labeled_content_format(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={
                "results": [
                    {
                        "title": "Result 1",
                        "url": "https://example.com/1",
                        "content": "Content about test.",
                    }
                ]
            }
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            tool = WebSearchTool(api_key="key")
            result = tool.execute(query="test query")
        assert result.success is True
        assert "### Result 1" in result.content
        assert "Source: https://example.com/1" in result.content
        assert "Summary: Content about test." in result.content
        _, kwargs = mock_client_cls.return_value.search.call_args
        assert kwargs["search_depth"] == "advanced"

    def test_tavily_uses_snippet_when_content_missing(self):
        fake_module, _ = _fake_tavily_module(
            search_return={
                "results": [
                    {
                        "title": "Snippet Only",
                        "url": "https://example.com/s",
                        "snippet": "Fallback snippet text.",
                    }
                ]
            }
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            tool = WebSearchTool(api_key="key")
            result = tool.execute(query="test query")
        assert result.success is True
        assert "Summary: Fallback snippet text." in result.content

class TestUrlDetection:
    def test_is_url_https(self):
        assert WebSearchTool._is_url("https://example.com") is True

    def test_is_url_http(self):
        assert WebSearchTool._is_url("http://example.com") is True

    def test_is_url_with_whitespace(self):
        assert WebSearchTool._is_url("  https://example.com  ") is True

    def test_is_url_plain_text(self):
        assert WebSearchTool._is_url("what are punic wars") is False

    def test_is_url_empty(self):
        assert WebSearchTool._is_url("") is False

    def test_extract_url_from_text(self):
        url = WebSearchTool._extract_url(
            "Summarize this: https://example.com/page please"
        )
        assert url == "https://example.com/page"

    def test_extract_url_none_when_absent(self):
        assert WebSearchTool._extract_url("no urls here") is None

    def test_extract_url_strips_trailing_punctuation(self):
        url = WebSearchTool._extract_url("See https://example.com/page.")
        assert url == "https://example.com/page"

    def test_extract_url_from_complex_text(self):
        url = WebSearchTool._extract_url(
            "Read https://arxiv.org/abs/2310.03714 and summarize"
        )
        assert url == "https://arxiv.org/abs/2310.03714"


class TestUrlNormalization:
    def test_arxiv_pdf_to_abs(self):
        url = WebSearchTool._normalize_url("https://arxiv.org/pdf/2310.03714")
        assert url == "https://arxiv.org/abs/2310.03714"

    def test_arxiv_pdf_with_extension(self):
        url = WebSearchTool._normalize_url("https://arxiv.org/pdf/2310.03714.pdf")
        assert url == "https://arxiv.org/abs/2310.03714"

    def test_non_arxiv_unchanged(self):
        url = WebSearchTool._normalize_url("https://example.com/page")
        assert url == "https://example.com/page"

    def test_arxiv_abs_unchanged(self):
        url = WebSearchTool._normalize_url("https://arxiv.org/abs/2310.03714")
        assert url == "https://arxiv.org/abs/2310.03714"


class TestUrlFetching:
    @staticmethod
    def _allow_public_url(monkeypatch):
        import openjarvis.tools.web_search as web_search

        monkeypatch.setattr(web_search, "check_ssrf", lambda url: None)

    @staticmethod
    def _response(html: str, content_type: str = "text/html"):
        response = MagicMock()
        response.text = html
        response.headers = {"content-type": content_type}
        response.raise_for_status = MagicMock()
        return response

    def test_fetch_url_strips_scripts(self, monkeypatch):
        import httpx

        self._allow_public_url(monkeypatch)
        response = self._response(
            "<html><script>var x=1;</script><body>Content</body></html>"
        )
        monkeypatch.setattr(httpx, "get", MagicMock(return_value=response))

        content = WebSearchTool._fetch_url("https://example.com")
        assert "var x" not in content
        assert "Content" in content

    def test_fetch_url_truncates_long_content(self, monkeypatch):
        import httpx

        self._allow_public_url(monkeypatch)
        response = self._response("<p>" + "x" * 10000 + "</p>")
        monkeypatch.setattr(httpx, "get", MagicMock(return_value=response))

        content = WebSearchTool._fetch_url("https://example.com", max_chars=100)
        assert len(content) < 200
        assert "[Content truncated]" in content

    def test_fetch_url_pdf_content_type(self, monkeypatch):
        import httpx

        self._allow_public_url(monkeypatch)
        response = self._response(
            "%PDF-1.4 binary data", content_type="application/pdf"
        )
        monkeypatch.setattr(httpx, "get", MagicMock(return_value=response))

        content = WebSearchTool._fetch_url("https://example.com/file.pdf")
        assert "PDF" in content
        assert "cannot be read" in content


class TestExecuteWithUrl:
    @staticmethod
    def _allow_public_url(monkeypatch):
        import openjarvis.tools.web_search as web_search

        monkeypatch.setattr(web_search, "check_ssrf", lambda url: None)

    def test_execute_with_embedded_url(self, monkeypatch):
        import httpx

        self._allow_public_url(monkeypatch)
        response = MagicMock()
        response.text = "<html><body>Article text</body></html>"
        response.headers = {"content-type": "text/html"}
        response.raise_for_status = MagicMock()
        monkeypatch.setattr(httpx, "get", MagicMock(return_value=response))

        tool = WebSearchTool(api_key="key")
        result = tool.execute(
            query="Summarize https://example.com/article please"
        )
        assert result.success is True
        assert "Article text" in result.content
        assert result.metadata.get("mode") == "fetch"

    def test_execute_url_fetch_failure(self, monkeypatch):
        import httpx

        self._allow_public_url(monkeypatch)
        monkeypatch.setattr(
            httpx,
            "get",
            MagicMock(side_effect=httpx.HTTPError("Connection failed")),
        )

        tool = WebSearchTool(api_key="key")
        result = tool.execute(query="https://example.com/broken")
        assert result.success is False
        assert "Failed to fetch URL" in result.content


class TestResultHygiene:
    """Two defects seen in a live research transcript: literal "&#xA0;" in the
    rendered answer, and the same source card shown twice."""

    def _search(self, results):
        fake_module, _ = _fake_tavily_module(search_return={"results": results})
        tool = WebSearchTool(api_key="key")
        with patch.dict(sys.modules, {"tavily": fake_module}):
            return tool.execute(query="anything")

    def test_html_entities_are_decoded_in_titles_and_snippets(self):
        result = self._search(
            [
                {
                    "title": "Tom&#xA0;&amp; Jerry",
                    "url": "https://example.com/a",
                    "content": "Costs&#xA0;rose&nbsp;sharply &amp; fell.",
                }
            ]
        )

        source = result.metadata["sources"][0]
        assert source["title"] == "Tom & Jerry"
        assert "&#xA0;" not in source["summary"]
        assert "&amp;" not in source["summary"]
        assert "&" in source["summary"]
        # The model reads result.content, so it must be clean too.
        assert "&#xA0;" not in result.content

    def test_the_same_page_is_only_listed_once(self):
        result = self._search(
            [
                {"title": "A", "url": "https://example.com/p", "content": "one"},
                {"title": "A dup", "url": "https://example.com/p/", "content": "one"},
                {"title": "A frag", "url": "https://example.com/p#x", "content": "one"},
                {"title": "B", "url": "https://example.com/q", "content": "two"},
            ]
        )

        urls = [s["url"] for s in result.metadata["sources"]]
        assert urls == ["https://example.com/p", "https://example.com/q"]
        assert result.content.count("Source: ") == 2

    def test_distinct_pages_are_all_kept(self):
        result = self._search(
            [
                {"title": "A", "url": "https://example.com/a", "content": "one"},
                {"title": "B", "url": "https://example.com/b", "content": "two"},
            ]
        )
        assert len(result.metadata["sources"]) == 2

    def test_a_result_without_a_url_is_still_reported(self):
        """Missing URLs must not all collapse into one deduplicated entry."""
        result = self._search(
            [
                {"title": "A", "url": "", "content": "one"},
                {"title": "B", "url": "", "content": "two"},
            ]
        )
        assert len(result.metadata["sources"]) == 2


__all__ = ["TestWebSearchTool", "TestResultHygiene"]
