"""Tests for bounded Tavily-only web search routing."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.core.registry import ToolRegistry
from openjarvis.tools.web_search import WebSearchTool, _build_plan


def _fake_tavily_module(search_return=None, search_side_effect=None):
    fake_module = ModuleType("tavily")
    mock_client_cls = MagicMock()
    mock_client = MagicMock()
    if search_side_effect is not None:
        mock_client.search.side_effect = search_side_effect
    else:
        mock_client.search.return_value = search_return or {}
    mock_client_cls.return_value = mock_client
    fake_module.TavilyClient = mock_client_cls
    return fake_module, mock_client_cls


def _result(
    title: str,
    url: str,
    content: str,
    **extra,
) -> dict:
    return {"title": title, "url": url, "content": content, **extra}


class TestWebSearchTool:
    def test_spec_is_small_and_tool_owned(self):
        tool = WebSearchTool(api_key="key")
        assert tool.spec.name == "web_search"
        assert tool.spec.category == "search"
        assert tool.spec.metadata == {"requires_api_key": "TAVILY_API_KEY"}
        assert set(tool.spec.parameters["properties"]) == {"query", "max_results"}
        assert tool.spec.parameters["required"] == ["query"]
        assert "one call is enough" in tool.spec.description

        research_tool = WebSearchTool(api_key="key", force_advanced=True)
        assert research_tool.spec.parameters == tool.spec.parameters

    def test_no_query(self):
        result = WebSearchTool(api_key="key").execute(query="")
        assert result.success is False
        assert "No query" in result.content
        assert result.metadata["bounded_search_complete"] is True

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        fake_module, _ = _fake_tavily_module()
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key=None).execute(query="OpenJarvis")
        assert result.success is False
        assert "TAVILY_API_KEY is not configured" in result.content

    def test_tavily_not_installed(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "tavily", None)
        result = WebSearchTool(api_key="key").execute(query="OpenJarvis")
        assert result.success is False
        assert "tavily-python not installed" in result.content

    def test_tavily_error_fails_closed_without_fallback(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_side_effect=RuntimeError("Tavily is down")
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(query="OpenJarvis")

        assert result.success is False
        assert "Tavily search error" in result.content
        assert "search engine's page" in result.content
        assert "duckduckgo" not in result.content.lower()
        # First call, advanced escalation, then the plain fallback.
        assert mock_client_cls.return_value.search.call_count == 3
        assert result.metadata["provider_calls"] == 3

    def test_to_openai_function(self):
        function = WebSearchTool(api_key="key").to_openai_function()
        assert function["type"] == "function"
        assert function["function"]["name"] == "web_search"
        assert set(function["function"]["parameters"]["properties"]) == {
            "query",
            "max_results",
        }

    def test_registry_registration(self):
        ToolRegistry.register_value("web_search", WebSearchTool)
        assert ToolRegistry.contains("web_search")

    def test_max_results_is_bounded_and_forwarded(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={
                "results": [
                    _result(
                        "OpenJarvis overview",
                        "https://example.com/openjarvis",
                        "OpenJarvis overview.",
                    )
                ]
            }
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            WebSearchTool(api_key="key").execute(
                query="Give me 10 sources for the OpenJarvis overview",
                max_results=99,
            )
        assert mock_client_cls.return_value.search.call_args.kwargs["max_results"] == 10


class TestRouting:
    def test_simple_named_game_uses_one_basic_search(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={
                # Three sources across three domains: what Tavily actually
                # returns for a query like this (measured at 4). A one-result
                # fixture now reads as thin evidence and escalates, which is
                # not what this test is about.
                "results": [
                    _result(
                        "Crimson Desert - official game overview",
                        "https://crimsondesert.pearlabyss.com/",
                        "Crimson Desert is an open-world action game.",
                    ),
                    _result(
                        "Crimson Desert review",
                        "https://games.example/crimson-desert",
                        "Crimson Desert gameplay and platforms.",
                    ),
                    _result(
                        "Crimson Desert on Steam",
                        "https://store.steam.example/crimson-desert",
                        "Crimson Desert store page and release information.",
                    ),
                ]
            }
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(
                query="Can you search for the game called Crimson Desert"
            )

        search = mock_client_cls.return_value.search
        assert search.call_count == 1
        assert search.call_args.args[0].endswith("Crimson Desert")
        assert search.call_args.kwargs["search_depth"] == "basic"
        assert result.metadata["provider_calls"] == 1
        assert result.metadata["initial_depth"] == "basic"
        assert result.metadata["final_depth"] == "basic"
        assert result.metadata["quality_passed"] is True

    def test_expanded_game_overview_stays_basic_and_forwards_max_results(self):
        """The requested count is sent as asked.

        This used to assert the opposite -- a basic search was silently capped
        at 3 unless the query text happened to name a number, so passing
        max_results=5 sent 3 and nothing explained why.
        """
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={
                "results": [
                    _result(
                        "Crimson Desert overview",
                        "https://games.example/crimson-desert",
                        "Crimson Desert gameplay, platforms, and information.",
                    ),
                    _result(
                        "Crimson Desert official",
                        "https://crimsondesert.pearlabyss.com/",
                        "Official Crimson Desert information.",
                    ),
                    _result(
                        "Crimson Desert news",
                        "https://news.example/crimson-desert",
                        "Crimson Desert release coverage.",
                    ),
                ]
            }
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            WebSearchTool(api_key="key").execute(
                query=(
                    "Crimson Desert game latest information release date "
                    "platforms gameplay official website 2026"
                ),
                max_results=5,
            )

        kwargs = mock_client_cls.return_value.search.call_args.kwargs
        assert kwargs["search_depth"] == "basic"
        assert kwargs["max_results"] == 5

    def test_simple_fact_uses_basic(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={
                "results": [
                    _result(
                        "Mount Apo",
                        "https://example.com/mount-apo",
                        "Mount Apo is the highest mountain in the Philippines.",
                    ),
                    _result(
                        "Mount Apo facts",
                        "https://britannica.example/mount-apo",
                        "Mount Apo is the highest mountain in the Philippines "
                        "at 2,954 metres.",
                    ),
                    _result(
                        "Climbing Mount Apo",
                        "https://travel.example/mount-apo",
                        "The highest mountain in the Philippines, Mount Apo, "
                        "is on Mindanao.",
                    ),
                ]
            }
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            WebSearchTool(api_key="key").execute(
                query="What is the highest mountain in the Philippines?"
            )
        assert (
            mock_client_cls.return_value.search.call_args.kwargs["search_depth"]
            == "basic"
        )

    def test_news_starts_advanced_without_retry(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={"results": []}
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(
                query="latest news around the Philippines this week"
            )

        search = mock_client_cls.return_value.search
        assert search.call_count == 1
        assert search.call_args.kwargs["search_depth"] == "advanced"
        assert search.call_args.kwargs["topic"] == "news"
        assert search.call_args.kwargs["time_range"] == "week"
        assert result.metadata["provider_calls"] == 1
        assert result.metadata["quality_passed"] is False

    def test_world_news_starts_advanced_without_special_query(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={
                "results": [
                    _result(
                        f"International headline {index}",
                        f"https://news{index}.example/story-{index}",
                        "A major international development.",
                    )
                    for index in range(3)
                ]
            }
        )
        query = "latest trending news around the world"
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(query=query)

        search = mock_client_cls.return_value.search
        assert search.call_count == 1
        assert search.call_args.args[0] == query
        assert search.call_args.kwargs["search_depth"] == "advanced"
        assert result.metadata["quality_passed"] is True

    def test_irrelevant_basic_rewrites_once_with_advanced(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_side_effect=[
                {
                    "results": [
                        _result(
                            "Unrelated tennis result",
                            "https://sports.example/tennis",
                            "A match result.",
                        )
                    ]
                },
                {
                    "results": [
                        _result(
                            "Crimson Desert game overview",
                            "https://games.example/crimson-desert",
                            "Crimson Desert is an action adventure game.",
                        )
                    ]
                },
            ]
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(
                query="game called Crimson Desert"
            )

        search = mock_client_cls.return_value.search
        assert search.call_count == 2
        assert [call.kwargs["search_depth"] for call in search.call_args_list] == [
            "basic",
            "advanced",
        ]
        assert '"Crimson Desert"' in search.call_args_list[1].args[0]
        assert result.metadata["provider_calls"] == 2
        assert result.metadata["escalated"] is True
        assert result.metadata["bounded_search_complete"] is True
        assert "tennis" not in result.content.lower()

    def test_advanced_intent_never_gets_third_or_corrective_call(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={"results": []}
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(
                query="official iPhone announcement"
            )
        assert mock_client_cls.return_value.search.call_count == 1
        assert result.metadata["provider_calls"] == 1
        assert result.metadata["bounded_search_complete"] is True

    def test_exact_current_figure_starts_advanced(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={"results": []}
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            WebSearchTool(api_key="key").execute(
                query="What is the exact current gasoline price in Manila?"
            )
        assert (
            mock_client_cls.return_value.search.call_args.kwargs["search_depth"]
            == "advanced"
        )

    def test_verification_starts_advanced(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={"results": []}
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            WebSearchTool(api_key="key").execute(
                query="Verify whether this product claim is accurate"
            )
        assert (
            mock_client_cls.return_value.search.call_args.kwargs["search_depth"]
            == "advanced"
        )

    def test_deep_research_forces_one_advanced_search(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={"results": []}
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            tool = WebSearchTool(api_key="key", force_advanced=True)
            result = tool.execute(query="ordinary background information")

        search = mock_client_cls.return_value.search
        assert search.call_count == 1
        assert search.call_args.kwargs["search_depth"] == "advanced"
        assert result.metadata["provider_calls"] == 1

    def test_official_product_announcement_is_general_not_ai_specific(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={
                "results": [
                    _result(
                        "Apple announces iPhone 18",
                        "https://www.apple.com/newsroom/iphone-18/",
                        "Apple announced the iPhone 18.",
                    )
                ]
            }
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(
                query="official iPhone 18 announcement"
            )

        search = mock_client_cls.return_value.search
        assert search.call_count == 1
        assert search.call_args.kwargs["search_depth"] == "advanced"
        assert result.metadata["quality_passed"] is True
        assert result.metadata["sources"][0]["official_source"] is True

    def test_secondary_report_is_not_labeled_official(self):
        fake_module, _ = _fake_tavily_module(
            search_return={
                "results": [
                    _result(
                        "Apple announces iPhone 18",
                        "https://www.reuters.com/technology/apple-iphone-18/",
                        "Reuters reports that Apple announced the iPhone 18.",
                    )
                ]
            }
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(
                query="official iPhone 18 announcement"
            )
        assert result.metadata["sources"][0]["official_source"] is False
        assert result.metadata["quality_passed"] is False
        assert "insufficient or off-topic" in result.content

    def test_news_homepage_is_not_accepted_as_a_direct_result(self):
        fake_module, _ = _fake_tavily_module(
            search_return={
                "results": [
                    _result(
                        "Philippines News",
                        "https://example.com/news",
                        "Latest Philippines news.",
                    )
                ]
            }
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(
                query="latest Philippines news"
            )
        assert result.metadata["sources"] == []
        assert result.metadata["quality_passed"] is False

    def test_query_year_filters_conflicting_results(self):
        fake_module, _ = _fake_tavily_module(
            search_return={
                "results": [
                    _result(
                        "Device specification 2025",
                        "https://example.com/device-2025",
                        "Device specification from 2025.",
                        published_date="2025-01-01",
                    ),
                    _result(
                        "Device specification 2026",
                        "https://example.com/device-2026",
                        "Device specification from 2026.",
                        published_date="2026-01-01",
                    ),
                ]
            }
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(
                query="device specification 2026"
            )
        assert [source["published_date"] for source in result.metadata["sources"]] == [
            "2026-01-01"
        ]


class TestImages:
    def test_ordinary_search_keeps_only_source_bound_https_thumbnails(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={
                "images": ["https://images.example/global.jpg"],
                "results": [
                    _result(
                        "Crimson Desert overview",
                        "https://games.example/crimson-desert",
                        "Crimson Desert game overview.",
                        images=["https://games.example/crimson.jpg"],
                    ),
                    _result(
                        "Crimson Desert interview",
                        "https://interviews.example/crimson-desert",
                        "Crimson Desert developer interview.",
                        images=["http://insecure.example/image.jpg"],
                    ),
                ],
            }
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(query="Crimson Desert")

        kwargs = mock_client_cls.return_value.search.call_args.kwargs
        assert kwargs["include_images"] is True
        assert "include_image_descriptions" not in kwargs
        assert result.metadata["images"] == []
        assert result.metadata["sources"][0]["image_url"] == (
            "https://games.example/crimson.jpg"
        )
        assert result.metadata["sources"][1]["image_url"] is None

    def test_result_image_dict_can_supply_thumbnail(self):
        fake_module, _ = _fake_tavily_module(
            search_return={
                "results": [
                    _result(
                        "Crimson Desert overview",
                        "https://games.example/crimson-desert",
                        "Crimson Desert overview.",
                        images=[
                            {
                                "url": "https://games.example/crimson.jpg",
                                "description": "Screenshot",
                            }
                        ],
                    )
                ]
            }
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(query="Crimson Desert")
        assert result.metadata["sources"][0]["image_url"].endswith("crimson.jpg")

    def test_missing_result_image_falls_back_to_none(self):
        fake_module, _ = _fake_tavily_module(
            search_return={
                "results": [
                    _result(
                        "Crimson Desert overview",
                        "https://games.example/crimson-desert",
                        "Crimson Desert overview.",
                    )
                ]
            }
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(query="Crimson Desert")
        assert result.metadata["sources"][0]["image_url"] is None

    def test_explicit_image_request_preserves_full_tavily_images(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={
                "images": [
                    {
                        "url": "https://images.example/crimson.jpg",
                        "description": "Crimson Desert screenshot",
                    },
                    "http://insecure.example/crimson.jpg",
                ],
                "results": [
                    _result(
                        "Crimson Desert gallery",
                        "https://games.example/crimson-desert/gallery",
                        "Official screenshots from Crimson Desert.",
                    )
                ],
            }
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(
                query="Show me images of Crimson Desert"
            )

        kwargs = mock_client_cls.return_value.search.call_args.kwargs
        assert kwargs["search_depth"] == "advanced"
        assert kwargs["include_images"] is True
        assert kwargs["include_image_descriptions"] is True
        assert result.metadata["explicit_image_search"] is True
        assert result.metadata["images"] == [
            {
                "url": "https://images.example/crimson.jpg",
                "description": "Crimson Desert screenshot",
            }
        ]


class TestResultHygiene:
    @staticmethod
    def _search(results: list[dict]):
        fake_module, _ = _fake_tavily_module(search_return={"results": results})
        with patch.dict(sys.modules, {"tavily": fake_module}):
            return WebSearchTool(api_key="key").execute(query="example")

    def test_html_entities_are_decoded(self):
        result = self._search(
            [
                _result(
                    "Example&#xA0;&amp; Test",
                    "https://example.com/a",
                    "Example costs&#xA0;rose&nbsp;sharply &amp; fell.",
                )
            ]
        )
        source = result.metadata["sources"][0]
        assert source["title"] == "Example & Test"
        assert source["summary"] == "Example costs rose sharply & fell."
        assert "&#" not in result.content

    def test_same_page_is_only_listed_once(self):
        result = self._search(
            [
                _result("Example A", "https://example.com/p", "Example one"),
                _result("Example duplicate", "https://example.com/p/", "Example one"),
                _result("Example fragment", "https://example.com/p#x", "Example one"),
                _result("Example B", "https://example.com/q", "Example two"),
            ]
        )
        assert [source["url"] for source in result.metadata["sources"]] == [
            "https://example.com/p",
            "https://example.com/q",
        ]

    def test_snippet_is_used_when_content_is_missing(self):
        result = self._search(
            [
                {
                    "title": "Example snippet",
                    "url": "https://example.com/snippet",
                    "snippet": "Example fallback snippet.",
                }
            ]
        )
        assert "Summary: Example fallback snippet." in result.content

    def test_publication_date_and_score_are_preserved(self):
        result = self._search(
            [
                _result(
                    "Example announcement",
                    "https://example.com/news/item",
                    "Example release.",
                    published_date="2026-08-31T04:00:00Z",
                    score=0.98,
                )
            ]
        )
        source = result.metadata["sources"][0]
        assert source["published_date"] == "2026-08-31T04:00:00Z"
        assert source["score"] == 0.98
        assert "Published: 2026-08-31T04:00:00Z" in result.content

    def test_low_quality_score_and_missing_url_are_rejected(self):
        result = self._search(
            [
                _result(
                    "Example low score",
                    "https://example.com/low",
                    "Example low-quality result.",
                    score=0.05,
                ),
                _result("Example missing URL", "", "Example result."),
            ]
        )
        assert result.metadata["sources"] == []


class TestUrlDetection:
    def test_is_url(self):
        assert WebSearchTool._is_url("https://example.com") is True
        assert WebSearchTool._is_url("  http://example.com  ") is True
        assert WebSearchTool._is_url("what are the Punic wars") is False

    def test_extract_url(self):
        assert (
            WebSearchTool._extract_url("Summarize https://example.com/page.")
            == "https://example.com/page"
        )
        assert WebSearchTool._extract_url("no URLs") is None

    def test_normalize_arxiv_pdf(self):
        assert (
            WebSearchTool._normalize_url("https://arxiv.org/pdf/2310.03714.pdf")
            == "https://arxiv.org/abs/2310.03714"
        )
        assert (
            WebSearchTool._normalize_url("https://example.com/page")
            == "https://example.com/page"
        )

    def test_url_query_fetches_directly(self):
        response = MagicMock()
        response.headers = {"content-type": "text/html"}
        response.text = "<html><script>bad()</script><body>Hello world</body></html>"
        response.raise_for_status.return_value = None
        with (
            patch("openjarvis.tools.web_search.check_ssrf", return_value=None),
            patch("httpx.get", return_value=response),
        ):
            result = WebSearchTool(api_key="key").execute(
                query="https://example.com/page"
            )
        assert result.success is True
        assert result.content == "Hello world"
        assert result.metadata["mode"] == "fetch"
        assert result.metadata["bounded_search_complete"] is True

    def test_url_query_blocked_by_ssrf(self):
        with patch(
            "openjarvis.tools.web_search.check_ssrf",
            return_value="blocked: private address",
        ):
            result = WebSearchTool(api_key="key").execute(
                query="http://169.254.169.254/latest/meta-data"
            )
        assert result.success is False
        assert "Failed to fetch URL" in result.content

    def test_fetch_url_reports_pdf(self):
        response = MagicMock()
        response.headers = {"content-type": "application/pdf"}
        response.raise_for_status.return_value = None
        with (
            patch("openjarvis.tools.web_search.check_ssrf", return_value=None),
            patch("httpx.get", return_value=response),
        ):
            content = WebSearchTool._fetch_url("https://example.com/report.pdf")
        assert "PDF file" in content

    def test_fetch_url_truncates(self):
        response = MagicMock()
        response.headers = {"content-type": "text/html"}
        response.text = "<p>" + ("x" * 200) + "</p>"
        response.raise_for_status.return_value = None
        with (
            patch("openjarvis.tools.web_search.check_ssrf", return_value=None),
            patch("httpx.get", return_value=response),
        ):
            content = WebSearchTool._fetch_url("https://example.com/long", max_chars=20)
        assert content == ("x" * 20) + "\n\n[Content truncated]"


class TestImageIntentSurvivesModelRewording:
    """The tool classifies the *model's* query, not the user's words.

    Live, "Find images of Crimson Desert gameplay" reached the tool as
    "Crimson Desert gameplay images" and came back with no gallery, because the
    pattern required a verb before the noun. Noun-led and noun-trailing forms
    are the model's natural rewording, so they count as image intent too.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "Find images of Crimson Desert gameplay",
            "Crimson Desert gameplay images",
            "images of Crimson Desert",
            "show pictures of Crimson Desert",
            "image search Crimson Desert",
            "Crimson Desert screenshots",
        ],
    )
    def test_image_requests_are_recognised(self, query):
        assert _build_plan(query, force_advanced=False).explicit_images is True

    @pytest.mark.parametrize(
        "query",
        [
            "Crimson Desert gameplay",
            "iPhone 17 camera image quality review",
            "latest world news",
        ],
    )
    def test_ordinary_queries_do_not_become_image_searches(self, query):
        """A noun buried mid-query is not a request for pictures."""
        assert _build_plan(query, force_advanced=False).explicit_images is False


class TestThinEvidenceEscalates:
    """A thin result set must buy a wider second look, not be served as-is.

    Sufficiency used to be ``bool(results)``: one result from one site passed,
    so the escalation below never fired for an ordinary query and a thin
    answer was returned as though it were a good one.
    """

    def _search(self, results, **params):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={"results": results}
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(
                query="what is the melting point of gallium", **params
            )
        return result, mock_client_cls.return_value.search

    def _gallium(self, n, *, domains=None):
        return [
            _result(
                f"Gallium melting point {i}",
                f"https://{(domains or [f'site{i}.example'] * n)[i]}/gallium-{i}",
                "The melting point of gallium is 29.76 degrees Celsius.",
            )
            for i in range(n)
        ]

    def test_two_results_are_not_enough(self):
        result, search = self._search(self._gallium(2))
        assert search.call_count == 2
        assert result.metadata["final_depth"] == "advanced"

    def test_three_results_from_one_site_are_not_enough(self):
        """Three pages of one blog is one claim repeated, not corroboration."""
        result, search = self._search(self._gallium(3, domains=["one.example"] * 3))
        assert search.call_count == 2

    def test_three_results_across_two_sites_are_enough(self):
        result, search = self._search(
            self._gallium(3, domains=["a.example", "b.example", "b.example"])
        )
        assert search.call_count == 1
        assert result.metadata["final_depth"] == "basic"

    def test_the_retry_asks_for_more_than_the_first_attempt(self):
        _result_, search = self._search(self._gallium(1))
        first, second = search.call_args_list
        assert first.kwargs["max_results"] == 4
        assert second.kwargs["max_results"] == 8
        assert second.kwargs["search_depth"] == "advanced"

    def test_the_retry_never_narrows_a_wider_request(self):
        """Asking for 10 must not be answered with 8 on the retry."""
        _result_, search = self._search(self._gallium(1), max_results=10)
        assert search.call_args_list[1].kwargs["max_results"] == 10

    def test_the_two_call_ceiling_still_holds(self):
        """Thin twice is still only two calls; the bound is deliberate."""
        _result_, search = self._search(self._gallium(1))
        assert search.call_count == 2


class TestMaxResultsIsHonoured:
    def test_the_default_is_four(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={"results": []}
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            WebSearchTool(api_key="key").execute(query="anything at all")
        assert (
            mock_client_cls.return_value.search.call_args_list[0].kwargs["max_results"]
            == 4
        )

    def test_a_requested_count_is_not_silently_capped(self):
        """max_results=9 used to reach Tavily as 3 unless the query said so."""
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={"results": []}
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            WebSearchTool(api_key="key").execute(query="anything at all", max_results=9)
        assert (
            mock_client_cls.return_value.search.call_args_list[0].kwargs["max_results"]
            == 9
        )


class TestASiteTheUserNamedCountsAsEnough:
    """One domain is the right answer when the query asked for that domain.

    "Search reddit for X" can only come back from reddit, so the two-domain
    rule escalated every single one -- two provider calls for a search that
    did exactly what was asked. Measured before this exemption: the reddit
    query took 2 calls, an open-web query took 1.
    """

    def _search(self, query, hosts):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_return={
                "results": [
                    _result(
                        f"Mechanical keyboard thread {i}",
                        f"https://{host}/thread-{i}",
                        "Discussion about mechanical keyboards and switches.",
                    )
                    for i, host in enumerate(hosts)
                ]
            }
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            WebSearchTool(api_key="key").execute(query=query)
        return mock_client_cls.return_value.search

    def test_naming_the_site_makes_one_domain_enough(self):
        search = self._search(
            "reddit discussions about mechanical keyboards",
            ["www.reddit.com"] * 3,
        )
        assert search.call_count == 1

    def test_a_site_named_with_a_space_counts(self):
        """ "GasWatch PH fuel prices" came back all from gaswatchph.com and
        searched again: the name is two words, the host one (29 September)."""
        search = self._search(
            "GasWatch PH mechanical keyboards",
            ["gaswatchph.com"] * 3,
        )
        assert search.call_count == 1

    def test_an_unnamed_single_domain_still_escalates(self):
        """The rule still catches one site quietly dominating an open search."""
        search = self._search(
            "discussions about mechanical keyboards",
            ["www.reddit.com"] * 3,
        )
        assert search.call_count == 2

    def test_a_generic_label_does_not_count_as_naming_a_site(self):
        """A word like "blog" must not excuse one host serving every result.

        Deliberately not "news": that word routes the search to advanced depth
        before sufficiency is ever consulted, so it would pass for a reason
        this test is not about.
        """
        search = self._search(
            "mechanical keyboard blog posts",
            ["blog.example.com"] * 3,
        )
        assert search.call_count == 2

    def test_the_exemption_does_not_rescue_too_few_results(self):
        """Naming a site earns a domain pass, not a result-count pass."""
        search = self._search(
            "reddit discussions about mechanical keyboards",
            ["www.reddit.com"] * 2,
        )
        assert search.call_count == 2


class TestItPointsAtThePageWhenSummariesCannotHelp:
    """A summary is the provider's precis, not the page.

    Asked for cinema showtimes, search found the right listings page and Sage
    still could not answer: the times are drawn by JavaScript, so the served
    HTML holds no showtime, no film title, not even the word "Showtimes".
    No summary will ever contain that, which is why the result now says where
    the answer actually lives instead of leaving the model to guess.
    """

    def _run(self, results):
        fake_module, _ = _fake_tavily_module(search_return={"results": results})
        with patch.dict(sys.modules, {"tavily": fake_module}):
            return WebSearchTool(api_key="key").execute(
                query="SM City Calamba showtimes today"
            )

    def _thin(self, n=3):
        return [
            _result(
                f"SM City Calamba schedule {i}",
                f"https://cinema{i}.example/sm-city-calamba",
                "Movie schedules for today.",
            )
            for i in range(n)
        ]

    def test_a_thin_result_names_the_page_to_read(self):
        result = self._run(self._thin())
        assert "web_read" in result.content
        assert "cinema0.example" in result.content

    def test_a_good_result_does_not_suggest_browsing(self):
        """An ordinary answered lookup must not grow a nudge to go and read."""
        rich = [
            _result(
                f"Mount Apo {i}",
                f"https://site{i}.example/mount-apo",
                "Mount Apo is the highest mountain in the Philippines, standing "
                "2,954 metres above sea level on the island of Mindanao, and it "
                "is the country's most prominent peak by a considerable margin, "
                "drawing climbers from across the region throughout the year.",
            )
            for i in range(3)
        ]
        result = self._run(rich)
        assert "web_read" not in result.content

    def test_no_results_means_no_suggestion(self):
        result = self._run([])
        assert "web_read" not in result.content


class TestSearchResultsBecomeReadable:
    """Reading a search result is the same intent as clicking it."""

    def test_returned_urls_are_permitted_for_the_turn(self):
        from openjarvis.security import page_access

        page_access.clear()
        try:
            fake_module, _ = _fake_tavily_module(
                search_return={
                    "results": [
                        _result(
                            "SM City Calamba movie schedule",
                            "https://clickthecity.example/sm-city-calamba",
                            "SM City Calamba showtimes and movie schedule for today.",
                        )
                    ]
                }
            )
            with patch.dict(sys.modules, {"tavily": fake_module}):
                WebSearchTool(api_key="key").execute(
                    query="SM City Calamba showtimes today"
                )
            assert page_access.is_allowed(
                "https://clickthecity.example/sm-city-calamba"
            )
            assert not page_access.is_allowed("https://elsewhere.example/page")
        finally:
            page_access.clear()


def test_a_results_table_reads_as_words_on_a_card():
    # 24 September: a card showed "| | | | --- --- | Pos | Driver | Team |".
    from openjarvis.tools.web_search import _card_text

    assert (
        _card_text("| | --- --- | Pos | Driver | 1 | Lewis Hamilton | Ferrari |")
        == "Pos Driver 1 Lewis Hamilton Ferrari"
    )


class TestSearchBudget:
    """2 October: every search ends within 8 s (the user's choice, from 163
    timed searches: 5 took longer, up to 13.6 s)."""

    class _TavilyTimeout(Exception):
        pass

    _TavilyTimeout.__name__ = "TimeoutError"

    def test_each_call_gets_at_most_the_budget(self):
        from openjarvis.tools.web_search import SEARCH_BUDGET_SECONDS

        thin = {"results": [_result("One", "https://a.example/x", "short")]}
        fake_module, mock_client_cls = _fake_tavily_module(search_return=thin)
        with patch.dict(sys.modules, {"tavily": fake_module}):
            WebSearchTool(api_key="key").execute(query="OpenJarvis")
        search = mock_client_cls.return_value.search
        assert search.call_count == 2  # first + better-results retry
        timeouts = [c.kwargs["timeout"] for c in search.call_args_list]
        assert timeouts[0] == SEARCH_BUDGET_SECONDS
        assert 0 < timeouts[1] <= SEARCH_BUDGET_SECONDS

    def test_a_timeout_twice_says_so(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_side_effect=self._TavilyTimeout(8.0)
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(query="OpenJarvis")
        # The first call, then one plain fallback.
        assert mock_client_cls.return_value.search.call_count == 2
        assert result.success is False
        assert "timed out twice" in result.content
        assert "search engine's page" in result.content
        assert result.metadata["timed_out"] is True

    def test_a_timeout_falls_back_to_a_plain_query(self):
        """2 October: '"SUV driver na nakabangga sa Pasig"' timed out and the
        turn ended. The fallback drops the quotes and Tagalog filler."""
        from openjarvis.tools.web_search import FALLBACK_SECONDS

        found = {
            "results": [
                _result(
                    "SUV fatally hits 4-year-old girl in Pasig",
                    "https://mb.com.ph/2026/09/14/suv-fatally-hits-girl-in-pasig",
                    "A four-year-old girl died after she was hit by an SUV in Pasig.",
                ),
                _result(
                    "LTO summons SUV driver in fatal Pasig crash",
                    "https://newsinfo.inquirer.net/2305616/lto-pasig-crash",
                    "The LTO summoned the SUV driver in the Pasig crash.",
                ),
                _result(
                    "Pasig SUV crash",
                    "https://www.gmanetwork.com/news/pasig-suv-crash",
                    "GMA: the SUV driver returned and took the girl to hospital.",
                ),
            ]
        }
        fake_module, mock_client_cls = _fake_tavily_module(
            search_side_effect=[self._TavilyTimeout(8.0), found]
        )
        with (
            patch.dict(sys.modules, {"tavily": fake_module}),
            patch("openjarvis.tools.web_search._wants_recent", return_value=False),
        ):
            result = WebSearchTool(api_key="key").execute(
                query='"SUV driver na nakabangga sa Pasig" Philippines news'
            )
        search = mock_client_cls.return_value.search
        assert search.call_count == 2
        fallback = search.call_args_list[1]
        assert fallback.args[0] == "SUV driver nakabangga Pasig Philippines news"
        assert fallback.kwargs["search_depth"] == "basic"
        assert fallback.kwargs["timeout"] == FALLBACK_SECONDS
        assert "topic" not in fallback.kwargs
        assert result.success is True
        assert "mb.com.ph" in result.content

    def test_a_slow_retry_keeps_the_first_results(self):
        thin = {
            "results": [
                _result(
                    "OpenJarvis notes", "https://a.example/x", "OpenJarvis kept result"
                )
            ]
        }
        fake_module, mock_client_cls = _fake_tavily_module(
            search_side_effect=[thin, self._TavilyTimeout(3.0)]
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(query="OpenJarvis")
        assert mock_client_cls.return_value.search.call_count == 2
        assert result.success is True
        assert "kept result" in result.content
        assert result.metadata["provider_calls"] == 2

    def test_no_retry_when_the_budget_is_spent(self, monkeypatch):
        import openjarvis.tools.web_search as web_search

        clock = iter([100.0, 107.5, 107.5, 107.5, 107.5])
        monkeypatch.setattr(web_search.time, "monotonic", lambda: next(clock, 107.5))
        thin = {
            "results": [
                _result(
                    "OpenJarvis notes", "https://a.example/x", "OpenJarvis kept result"
                )
            ]
        }
        fake_module, mock_client_cls = _fake_tavily_module(search_return=thin)
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(query="OpenJarvis")
        assert mock_client_cls.return_value.search.call_count == 1
        assert "kept result" in result.content


_CLANCY_EVENT = [
    _result(
        "Mistrial declared in Lindsay Clancy case",
        "https://www.pbs.org/newshour/nation/clancy-mistrial",
        "The judge declared a mistrial in the Lindsay Clancy case on September 4.",
    ),
    _result(
        "Lindsay Clancy jury deadlocks",
        "https://www.nytimes.com/2026/09/04/us/clancy-deadlock",
        "Jurors in the Lindsay Clancy case could not agree.",
    ),
    _result(
        "Clancy trial explained",
        "https://en.wikipedia.org/wiki/Killing_of_the_Clancy_children",
        "Lindsay Clancy was tried for the deaths of her three children.",
    ),
]
_CLANCY_RECENT = [
    _result(
        "Judge denies Lindsay Clancy motion for not guilty finding",
        "https://apnews.com/article/clancy-motion-denied",
        "On October 1 the judge denied Lindsay Clancy's motion; next hearing Nov 2.",
        published_date="Thu, 01 Oct 2026 18:00:00 GMT",
    ),
    # Already in the main results: listed once, as recent.
    _result(
        "Mistrial declared in Lindsay Clancy case",
        "https://www.pbs.org/newshour/nation/clancy-mistrial",
        "The judge declared a mistrial in the Lindsay Clancy case on September 4.",
    ),
]


def _by_kind(main, recent=None, *, main_error=None):
    """A search fake that answers the past-week pass and the main call apart.

    The two run on different threads, so their order is not fixed.
    """

    def search(query, **kwargs):
        if kwargs.get("time_range") == "week" and kwargs.get("topic") == "news":
            if isinstance(recent, BaseException):
                raise recent
            return {"results": recent or []}
        if main_error is not None:
            raise main_error
        return {"results": main}

    return search


class TestRecentNewsPass:
    """6 October: an ongoing story also gets the past week's news, so the
    1 October developments the pinned chats missed come first."""

    def test_wanted_for_ongoing_stories_only(self):
        from openjarvis.tools.web_search import _build_plan, _wants_recent

        def wants(query):
            return _wants_recent(query, _build_plan(query, force_advanced=False))

        assert wants("Lindsay Clancy case current status")
        assert wants("Pasig SUV driver trending news")
        assert wants("Saudi Arabia news")
        assert not wants("NVIDIA stock price NVDA")
        assert not wants("Bujairami Hectic perfume price Philippines")
        # Already limited to a period, or a picture search: nothing to add.
        assert not wants("Saudi Arabia news today")
        assert not wants("show me photos of the Medina attack")

    def test_recent_results_lead_and_are_marked(self):
        fake_module, mock_client_cls = _fake_tavily_module(
            search_side_effect=_by_kind(_CLANCY_EVENT, _CLANCY_RECENT)
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(
                query="Lindsay Clancy case current status"
            )
        search = mock_client_cls.return_value.search
        assert search.call_count == 2
        recent_call = next(
            c for c in search.call_args_list if c.kwargs.get("time_range") == "week"
        )
        assert recent_call.kwargs["search_depth"] == "basic"
        assert recent_call.kwargs["topic"] == "news"

        urls = [source["url"] for source in result.metadata["sources"]]
        assert urls[0] == "https://apnews.com/article/clancy-motion-denied"
        assert urls.count("https://www.pbs.org/newshour/nation/clancy-mistrial") == 1
        assert result.content.startswith("Results marked 'From the past week's")
        assert result.content.count("\nFrom the past week's news: yes") == 2
        assert result.metadata["provider_calls"] == 2

    def test_a_failed_recent_pass_keeps_the_main_results(self):
        fake_module, _ = _fake_tavily_module(
            search_side_effect=_by_kind(_CLANCY_EVENT, RuntimeError("down"))
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(
                query="Lindsay Clancy case current status"
            )
        assert result.success is True
        assert "From the past week" not in result.content
        assert len(result.metadata["sources"]) == 3

    def test_recent_results_alone_answer_when_the_main_call_fails(self):
        recent = _CLANCY_RECENT + [
            _result(
                "Clancy prosecutors weigh retrial",
                "https://www.bostonglobe.com/2026/10/02/clancy-retrial",
                "Prosecutors in the Lindsay Clancy case are weighing a retrial.",
            )
        ]
        fake_module, _ = _fake_tavily_module(
            search_side_effect=_by_kind(
                [], recent, main_error=TestSearchBudget._TavilyTimeout(8.0)
            )
        )
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(
                query="Lindsay Clancy case current status"
            )
        assert result.success is True
        assert "apnews.com" in result.content


class TestExactFactHint:
    """2-3 October: the latest F1 race, NVIDIA's price and Black Clover's
    premiere time were each answered, or refused, from summaries alone."""

    def test_exact_figure_points_at_the_official_page(self):
        from openjarvis.tools.web_search import _read_hint

        sources = [
            {"url": "https://fan.example/f1", "summary": "x" * 400},
            {
                "url": "https://www.formula1.com/en/racing/2026",
                "summary": "y" * 400,
                "official_source": True,
            },
        ]
        hint = _read_hint(sources, True, "latest Formula One race completed 2026")
        assert "exact figure" in hint
        assert "https://www.formula1.com/en/racing/2026" in hint
        assert "could not be confirmed" in hint

    def test_ordinary_question_with_full_summaries_gets_no_hint(self):
        from openjarvis.tools.web_search import _read_hint

        sources = [{"url": "https://a.example/x", "summary": "z" * 400}]
        assert _read_hint(sources, True, "what is postpartum psychosis") == ""


def test_fallback_query_drops_quotes_operators_and_filler():
    from openjarvis.tools.web_search import _fallback_query

    assert (
        _fallback_query('"SUV driver na nakabangga sa Pasig" trending')
        == "SUV driver nakabangga Pasig"
    )
    assert _fallback_query("site:crunchyroll.com Black Clover release date") == (
        "crunchyroll.com Black Clover release date"
    )
    assert _fallback_query('"the"') == "the"


class TestExactFactPageText:
    """6 October: an exact-figure search brings the page text with it (same
    credit, about the same time), so the answer needs no web_read round."""

    _MENU = "Home News Crunchyroll Log In Crunchyroll Premium Crunchyroll Shop " * 60
    _ARTICLE = (
        "Here's the Exact Release Date and Time. Black Clover Season 2 premieres "
        "on Crunchyroll starting October 3 at 8:00 a.m. PT. "
    )

    def test_only_exact_questions_ask_for_page_text(self):
        exact = _build_plan("Black Clover exact release date", force_advanced=False)
        plain = _build_plan("what is Black Clover about", force_advanced=False)
        assert exact.exact and not plain.exact
        assert WebSearchTool._search_kwargs(exact, "basic", 4)[
            "include_raw_content"
        ] == ("text")
        assert "include_raw_content" not in WebSearchTool._search_kwargs(
            plain, "basic", 4
        )

    def test_excerpt_skips_the_menu_for_the_answer(self):
        from openjarvis.tools.web_search import PAGE_EXCERPT_CHARS, _page_excerpt

        page = self._MENU + self._ARTICLE + "Related stories. " * 300
        excerpt = _page_excerpt(page, "Black Clover Crunchyroll premiere date")
        assert len(excerpt) == PAGE_EXCERPT_CHARS
        assert "October 3 at 8:00 a.m. PT" in excerpt

    def test_short_page_is_given_whole(self):
        from openjarvis.tools.web_search import _page_excerpt

        assert _page_excerpt("  Price:\n $240.20  ", "NVDA price") == "Price: $240.20"
        assert _page_excerpt(None, "NVDA price") == ""

    def test_excerpts_reach_the_model_for_two_results_only(self):
        pages = [
            _result(
                f"Black Clover premiere {n}",
                f"https://site{n}.example/black-clover",
                "Black Clover Season 2 premieres in October.",
                raw_content=self._ARTICLE,
            )
            for n in range(3)
        ]
        fake_module, _ = _fake_tavily_module(search_return={"results": pages})
        with patch.dict(sys.modules, {"tavily": fake_module}):
            result = WebSearchTool(api_key="key").execute(
                query="Black Clover exact release date"
            )
        assert result.content.count("Page text (excerpt):") == 2
        assert "8:00 a.m. PT" in result.content


def test_product_photo_requests_are_image_searches():
    """2 October: "show me a picture of it" reached the tool as "...bottle
    photo official listing", got no gallery, and the model drew a bottle."""
    for query in (
        "Bujairami Hectic perfume bottle photo official listing",
        "what the Bujairami Hectic bottle looks like",
    ):
        assert _build_plan(query, force_advanced=False).explicit_images, query
    for query in (
        "camera image quality review",
        "product photography lighting",
        "real estate photography tips",
    ):
        assert not _build_plan(query, force_advanced=False).explicit_images, query


def test_a_picture_the_user_asked_for_survives_the_models_query():
    """6 October: "...in the philippines, and show me a picture of it"
    reached the tool as "...Philippines price bottle": no gallery."""
    from openjarvis.security import page_access

    found = {
        "results": [
            _result("Hectic", "https://shop.example/hectic", "Bujairami Hectic bottle")
        ],
        "images": [{"url": "https://img.example/hectic.jpg"}],
    }
    fake_module, mock_client_cls = _fake_tavily_module(search_return=found)
    page_access.clear()
    token = page_access.set_turn(
        "Look up bujairami hectic, how much in the philippines, show me a picture of it"
    )
    try:
        with (
            patch.dict(sys.modules, {"tavily": fake_module}),
            patch("openjarvis.tools.web_search._wants_recent", return_value=False),
        ):
            result = WebSearchTool(api_key="key").execute(
                query="Bujairami Hectic perfume Philippines price bottle"
            )
    finally:
        page_access._current.reset(token)
        page_access.clear()
    assert result.metadata["explicit_image_search"] is True
    assert result.metadata["images"]
    first = mock_client_cls.return_value.search.call_args_list[0]
    assert first.kwargs["include_image_descriptions"] is True


def test_recent_query_is_the_subject_words():
    from openjarvis.tools.web_search import _recent_query

    assert (
        _recent_query('current trending news in the Philippines "SUV driver" sa Pasig')
        == "philippines suv pasig"
    )
    # No names: the content words, filler dropped.
    assert _recent_query("what is going on") == "what going"


class TestTheShortCache:
    """The user's choice (9 October): the same search again answers at once,
    for a time that depends on how fast its subject changes."""

    RESULT = {
        "results": [
            _result(
                f"RTX 5060 Ti 16GB price Philippines ({shop})",
                f"https://{shop}.example/rtx-5060-ti-price",
                f"The RTX 5060 Ti 16GB price in the Philippines at {shop}: "
                "37,995 pesos. " * 6,
            )
            for shop in ("gameone", "datablitz", "pcx")
        ]
    }

    def _search(self, fake_module, query):
        with patch.dict(sys.modules, {"tavily": fake_module}):
            return WebSearchTool(api_key="key").execute(query=query)

    def test_the_same_search_is_answered_from_the_cache(self):
        from openjarvis.security import page_access

        fake_module, mock_client_cls = _fake_tavily_module(search_return=self.RESULT)
        first = self._search(fake_module, "RTX 5060 Ti 16GB price Philippines")
        calls = mock_client_cls.return_value.search.call_count
        page_access.clear()
        again = self._search(fake_module, "  rtx 5060 ti 16gb price PHILIPPINES ")
        assert mock_client_cls.return_value.search.call_count == calls
        assert again.metadata["cached"] is True
        assert again.content == first.content
        # Its pages are readable again, as after a fresh search.
        assert page_access.is_allowed("https://pcx.example/rtx-5060-ti-price")

    def test_an_expired_entry_searches_again(self, monkeypatch):
        from openjarvis.tools import web_search

        fake_module, mock_client_cls = _fake_tavily_module(search_return=self.RESULT)
        self._search(fake_module, "RTX 5060 Ti 16GB price Philippines")
        calls = mock_client_cls.return_value.search.call_count
        later = web_search.time.monotonic() + 3601
        monkeypatch.setattr(web_search.time, "monotonic", lambda: later)
        self._search(fake_module, "RTX 5060 Ti 16GB price Philippines")
        assert mock_client_cls.return_value.search.call_count > calls

    @pytest.mark.parametrize(
        "query,seconds",
        [
            ("latest news West Philippine Sea", 600),
            ("who won the F1 race", 600),
            ("RTX 5060 Ti price Philippines", 3600),
            ("USD to PHP exchange rate", 3600),
            ("Philippine holidays 2026 dates", 86400),
        ],
    )
    def test_how_long_a_search_is_kept(self, query, seconds):
        from openjarvis.tools.web_search import cache_seconds

        assert cache_seconds(query) == seconds


def test_a_site_web_read_cannot_open_is_flagged_in_the_results():
    """mb.com.ph walls every reader: said in the results, so the one reading
    step goes to a page that opens (the result itself stays listed)."""
    from openjarvis.tools import unreadable_hosts

    unreadable_hosts.mark("https://mb.com.ph/x", "bot check")
    fake_module, _ = _fake_tavily_module(
        search_return={
            "results": [
                _result(
                    "LTO bans driver",
                    "https://mb.com.ph/2026/10/06/lto-ban",
                    "The LTO revoked the driver's license for life in Pasig.",
                ),
                _result(
                    "Pasig road rage",
                    "https://www.gmanetwork.com/news/pasig",
                    "The LTO revoked the driver's license for life in Pasig.",
                ),
            ]
        }
    )
    with patch.dict(sys.modules, {"tavily": fake_module}):
        result = WebSearchTool(api_key="key").execute(query="Pasig road rage LTO")
    blocks = result.content.split("---")
    mb = next(b for b in blocks if "mb.com.ph" in b)
    gma = next(b for b in blocks if "gmanetwork" in b)
    assert "Cannot be opened with web_read: bot check" in mb
    assert "Cannot be opened" not in gma
