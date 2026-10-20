"""Tests for the switchable search engines (Tavily, Serper, Exa).

No test makes a real call: ``httpx.post`` and the tavily package are faked.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.tools import search_engines
from openjarvis.tools.web_search import WebSearchTool, clear_cache


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    names = [*search_engines.KEY_NAMES.values()]
    names += search_engines.BACKUP_KEY_NAMES.values()
    for name in names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        search_engines, "settings_path", lambda: tmp_path / "search.json"
    )
    search_engines.reset_skips()
    clear_cache()


def _http(status: int = 200, body: dict | None = None):
    return SimpleNamespace(
        status_code=status, json=lambda: body or {}, text=str(body or "")
    )


class _Fake:
    def __init__(self, name, response=None, error=None):
        self.name = name
        self.response = response
        self.error = error
        self.calls = 0

    def search(self, query, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return dict(self.response or {"results": []})


class TestChoice:
    def test_default_is_exa(self):
        assert search_engines.load_engine() == "exa"

    def test_exa_chosen_falls_over_to_tavily_then_serper(self, monkeypatch):
        for name in ("TAVILY_API_KEY", "SERPER_API_KEY", "EXA_API_KEY"):
            monkeypatch.setenv(name, "k")
        assert search_engines.order() == ["exa", "tavily", "serper"]

    def test_saved_choice_round_trips(self):
        search_engines.save_engine("Serper")
        assert search_engines.load_engine() == "serper"

    def test_unknown_engine_is_refused(self):
        with pytest.raises(ValueError):
            search_engines.save_engine("bing")

    def test_order_is_chosen_first_and_only_engines_with_keys(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "t")
        monkeypatch.setenv("EXA_API_KEY", "e")
        assert search_engines.order("exa") == ["exa", "tavily"]
        # Chosen without a key: the others still answer.
        assert search_engines.order("serper") == ["tavily", "exa"]

    def test_status_reports_keys(self, monkeypatch):
        monkeypatch.setenv("SERPER_API_KEY", "s")
        state = search_engines.status()
        keys = {e["id"]: e["key_set"] for e in state["engines"]}
        assert keys == {"tavily": False, "serper": True, "exa": False}


class TestSerper:
    def test_maps_answer_box_first_then_organic(self):
        data = {
            "answerBox": {
                "title": "Showtimes",
                "answer": "7:30 PM",
                "link": "https://cinema.example/today",
            },
            "organic": [
                {
                    "title": "Film",
                    "link": "https://a.example/film",
                    "snippet": "A film.",
                    "date": "Oct 19, 2026",
                },
                {"title": "No link", "snippet": "dropped"},
            ],
            "credits": 1,
        }
        with patch("httpx.post", return_value=_http(200, data)) as post:
            out = search_engines.SerperEngine("k").search(
                "film showtimes", max_results=4
            )
        body = post.call_args.kwargs["json"]
        assert body == {"q": "film showtimes", "num": 4, "gl": "ph"}
        assert post.call_args.kwargs["headers"]["X-API-KEY"] == "k"
        assert [r["url"] for r in out["results"]] == [
            "https://cinema.example/today",
            "https://a.example/film",
        ]
        assert out["results"][0]["content"] == "7:30 PM"
        assert out["results"][1]["published_date"] == "Oct 19, 2026"
        assert out["usage"]["credits"] == 1
        assert out["engine"] == "serper"

    def test_news_topic_uses_news_endpoint_and_time_range(self):
        data = {"news": [{"title": "T", "link": "https://n.example", "source": "GMA"}]}
        with patch("httpx.post", return_value=_http(200, data)) as post:
            out = search_engines.SerperEngine("k").search(
                "pasig news", topic="news", time_range="week"
            )
        assert post.call_args.args[0].endswith("/news")
        assert post.call_args.kwargs["json"]["tbs"] == "qdr:w"
        assert "(GMA)" in out["results"][0]["content"]

    def test_explicit_pictures_also_search_images(self):
        def post(url, **kwargs):
            if url.endswith("/images"):
                return _http(
                    200,
                    {"images": [{"imageUrl": "https://i.example/a.jpg", "title": "A"}]},
                )
            return _http(200, {"organic": []})

        with patch("httpx.post", side_effect=post):
            out = search_engines.SerperEngine("k").search(
                "photos of mayon", include_images=True, include_image_descriptions=True
            )
        assert out["images"] == [{"url": "https://i.example/a.jpg", "description": "A"}]

    def test_out_of_credits_is_marked_exhausted(self):
        with patch("httpx.post", return_value=_http(402, {"message": "no credits"})):
            with pytest.raises(search_engines.SearchEngineError) as caught:
                search_engines.SerperEngine("k").search("q")
        assert caught.value.exhausted

    def test_timeout_is_a_timeout(self):
        import httpx

        with patch("httpx.post", side_effect=httpx.ReadTimeout("slow")):
            with pytest.raises(TimeoutError):
                search_engines.SerperEngine("k").search("q")


class TestExa:
    def test_maps_text_and_asks_for_page_text_on_exact(self):
        data = {
            "results": [
                {
                    "title": "Price",
                    "url": "https://shop.example/p",
                    "text": "The price is ₱1,299 today.",
                    "publishedDate": "2026-10-18T00:00:00.000Z",
                    "image": "https://shop.example/p.jpg",
                }
            ],
            "costDollars": {"total": 0.007},
        }
        with patch("httpx.post", return_value=_http(200, data)) as post:
            out = search_engines.ExaEngine("k").search(
                "price", include_raw_content="text", time_range="day", topic="news"
            )
        body = post.call_args.kwargs["json"]
        assert body["contents"]["text"]["maxCharacters"] == 8000
        assert body["category"] == "news"
        assert "startPublishedDate" in body
        result = out["results"][0]
        assert result["raw_content"].startswith("The price")
        assert result["images"] == ["https://shop.example/p.jpg"]
        assert out["cost_usd"] == 0.007


def _fake_tavily(behaviour):
    """A tavily module whose client for each key does ``behaviour[key]``."""
    module = ModuleType("tavily")
    clients = {}

    def make(api_key):
        client = MagicMock()
        outcome = behaviour[api_key]
        if isinstance(outcome, BaseException):
            client.search.side_effect = outcome
        else:
            client.search.return_value = outcome
        clients[api_key] = client
        return client

    module.TavilyClient = make
    return patch.dict(sys.modules, {"tavily": module}), clients


class TestTavilyBackupKey:
    USED_UP = RuntimeError("This request exceeds your plan's set usage limit")

    def test_backup_answers_when_main_key_is_used_up(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "main")
        monkeypatch.setenv("TAVILY_API_KEY_BACKUP", "spare")
        fake, clients = _fake_tavily(
            {"main": self.USED_UP, "spare": {"results": [{"url": "https://x"}]}}
        )
        with fake:
            out = search_engines.chain().search("q")
        assert out["engine"] == "tavily"
        assert "passed_over" not in out
        assert clients["spare"].search.call_count == 1

    def test_used_up_main_key_is_skipped_next_time(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "main")
        monkeypatch.setenv("TAVILY_API_KEY_BACKUP", "spare")
        fake, clients = _fake_tavily({"main": self.USED_UP, "spare": {"results": []}})
        with fake:
            engine = search_engines.chain()
            engine.search("q")
            engine.search("q")
        assert clients["main"].search.call_count == 1
        assert clients["spare"].search.call_count == 2

    def test_main_key_failing_otherwise_does_not_spend_the_backup(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "main")
        monkeypatch.setenv("TAVILY_API_KEY_BACKUP", "spare")
        fake, clients = _fake_tavily(
            {"main": RuntimeError("connection reset"), "spare": {"results": []}}
        )
        with fake, pytest.raises(RuntimeError, match="connection reset"):
            search_engines.chain().search("q")
        assert clients["spare"].search.call_count == 0

    def test_both_used_up_falls_over_to_the_next_engine(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "main")
        monkeypatch.setenv("TAVILY_API_KEY_BACKUP", "spare")
        monkeypatch.setenv("SERPER_API_KEY", "s")
        fake, _ = _fake_tavily({"main": self.USED_UP, "spare": self.USED_UP})
        organic = {"organic": [{"title": "T", "link": "https://y"}]}
        with fake, patch("httpx.post", return_value=_http(200, organic)):
            out = search_engines.chain().search("q")
        assert out["engine"] == "serper"
        assert "Tavily (out of credits or key refused)" in out["passed_over"]

    def test_backup_alone_counts_as_a_tavily_key(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY_BACKUP", "spare")
        assert search_engines.order("tavily") == ["tavily"]
        tavily = search_engines.status()["engines"][0]
        assert tavily["key_set"] is False
        assert tavily["backup_key_set"] is True


class TestChain:
    def test_falls_over_on_failure_and_names_the_engine(self):
        first = _Fake("tavily", error=RuntimeError("Usage limit exceeded"))
        second = _Fake("serper", response={"results": [{"url": "https://x"}]})
        out = search_engines.EngineChain([first, second], "tavily").search("q")
        assert out["engine"] == "serper"
        assert "Tavily (out of credits or key refused)" in out["passed_over"]

    def test_exhausted_engine_is_skipped_next_time(self):
        first = _Fake("tavily", error=RuntimeError("Usage limit exceeded"))
        second = _Fake("serper")
        chain = search_engines.EngineChain([first, second], "tavily")
        chain.search("q")
        chain.search("q")
        assert first.calls == 1
        assert second.calls == 2

    def test_saving_a_choice_forgets_skips(self):
        first = _Fake("tavily", error=RuntimeError("Usage limit exceeded"))
        search_engines.EngineChain([first, _Fake("serper")], "tavily").search("q")
        search_engines.save_engine("tavily")
        search_engines.EngineChain([first, _Fake("serper")], "tavily").search("q")
        assert first.calls == 2

    def test_timeout_is_not_fallen_over(self):
        first = _Fake("tavily", error=TimeoutError("slow"))
        second = _Fake("serper")
        with pytest.raises(TimeoutError):
            search_engines.EngineChain([first, second], "tavily").search("q")
        assert second.calls == 0

    def test_all_failing_raises_the_last_error(self):
        chain = search_engines.EngineChain(
            [
                _Fake("tavily", error=RuntimeError("a")),
                _Fake("exa", error=RuntimeError("b")),
            ],
            "tavily",
        )
        with pytest.raises(RuntimeError, match="b"):
            chain.search("q")

    def test_chosen_engine_without_key_adds_no_note(self):
        """Settings shows a missing key; every answer saying so would be noise."""
        out = search_engines.EngineChain([_Fake("exa")], "serper").search("q")
        assert out["engine"] == "exa"
        assert "passed_over" not in out


class TestWebSearchWithOtherEngines:
    def _results(self):
        return {
            "organic": [
                {
                    "title": f"R{i}",
                    "link": f"https://site{i}.example/a",
                    "snippet": "OpenJarvis is a framework " * 3,
                }
                for i in range(4)
            ],
            "credits": 1,
        }

    def test_serper_chosen_answers_and_is_recorded(self, monkeypatch):
        monkeypatch.setenv("SERPER_API_KEY", "s")
        search_engines.save_engine("serper")
        with patch("httpx.post", return_value=_http(200, self._results())):
            result = WebSearchTool().execute(query="OpenJarvis framework")
        assert result.success
        assert result.metadata["engine"] == "serper"
        assert result.metadata["engine_passed_over"] == []
        assert "Search engine note" not in result.content

    def test_tavily_out_of_credits_falls_over_to_serper_and_says_so(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "t")
        monkeypatch.setenv("SERPER_API_KEY", "s")
        fake = ModuleType("tavily")
        client = MagicMock()
        client.search.side_effect = RuntimeError(
            "This request exceeds your plan's set usage limit"
        )
        fake.TavilyClient = MagicMock(return_value=client)
        with (
            patch.dict(sys.modules, {"tavily": fake}),
            patch("httpx.post", return_value=_http(200, self._results())),
        ):
            result = WebSearchTool().execute(query="OpenJarvis framework")
        assert result.success
        assert result.metadata["engine"] == "serper"
        assert "Search engine note" in result.content
        assert "Serper" in result.content.split("\n\n")[0]
