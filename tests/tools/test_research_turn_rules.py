"""A research question stays a research question (2 October).

Asked how PewDiePie's Ajax model compared "to gpt luna", Sage searched the
user's notes three times after the web search, said it ran on GPT-5.6 Luna
(old notes), read an x.com memecoin photo page, and showed a weather card.
"""

from __future__ import annotations

import pytest

from openjarvis.security import page_access
from openjarvis.tools import memory_budget


@pytest.fixture(autouse=True)
def _fresh():
    page_access.clear()
    memory_budget.start_message()
    yield
    page_access.clear()
    memory_budget.start_message()


def test_overlapping_turns_keep_their_own_rules_and_budgets():
    from contextvars import Context

    first, second = Context(), Context()

    def start(text):
        page_access.set_turn(text)
        memory_budget.start_message()

    first.run(start, "Research an AI model")
    first.run(memory_budget.note_web_search)
    first.run(page_access.note_read)
    second.run(start, "Search TikTok for memes")
    assert first.run(memory_budget.web_searched)
    assert not second.run(memory_budget.web_searched)
    assert not first.run(page_access.social_wanted)
    assert second.run(page_access.social_wanted)
    assert first.run(page_access.reads_used) == 1
    assert second.run(page_access.reads_used) == 0


def test_standalone_agent_runs_start_fresh_and_restore_the_callers_context():
    from unittest.mock import MagicMock

    from openjarvis.agents.orchestrator import OrchestratorAgent

    page_access.set_turn("Search TikTok")
    memory_budget.note_web_search()

    def generate(messages, **kwargs):
        assert page_access.turn_text() == "Research an AI model"
        assert not memory_budget.web_searched()
        memory_budget.note_web_search()
        return {"content": "Done", "usage": {}}

    engine = MagicMock()
    engine.generate.side_effect = generate
    agent = OrchestratorAgent(engine, "test-model")
    for _ in range(2):
        assert agent.run("Research an AI model").content == "Done"
        assert page_access.social_wanted()
        assert memory_budget.web_searched()


def test_standalone_executor_keeps_budgets_across_its_timeout_workers():
    from contextvars import Context

    from openjarvis.core.types import ToolCall, ToolResult
    from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec

    class BudgetTool(BaseTool):
        @property
        def spec(self):
            return ToolSpec(name="budget_probe", description="Probe")

        def execute(self, **params):
            return ToolResult(
                tool_name="budget_probe", content="Probe", success=memory_budget.take()
            )

    context = Context()
    executor = ToolExecutor([BudgetTool()])
    call = ToolCall(id="probe", name="budget_probe", arguments="{}")
    assert [context.run(executor.execute, call).success for _ in range(4)] == [
        True,
        True,
        True,
        False,
    ]


def test_unscoped_web_flag_expires_but_a_running_turn_keeps_it(monkeypatch):
    from contextvars import Context

    now = [1000.0]
    monkeypatch.setattr(memory_budget.time, "monotonic", lambda: now[0])
    unscoped = Context()
    unscoped.run(memory_budget.note_web_search)
    memory_budget.start_message()
    memory_budget.note_web_search()
    assert unscoped.run(memory_budget.web_searched)
    now[0] += memory_budget.WINDOW_SECONDS + 1
    assert not unscoped.run(memory_budget.web_searched)
    assert memory_budget.web_searched()


class TestNoNotesAfterAWebSearch:
    def test_failed_web_search_still_ends_memory_search(self, monkeypatch):
        import sys

        from openjarvis.tools.web_search import WebSearchTool
        from tests.tools.test_web_search import _fake_tavily_module

        fake, _ = _fake_tavily_module(search_side_effect=TimeoutError("timeout"))
        monkeypatch.setitem(sys.modules, "tavily", fake)
        result = WebSearchTool(api_key="test-key").execute(query="Research an AI model")
        assert not result.success
        assert memory_budget.web_searched()

    def test_direct_url_fetch_also_ends_memory_search(self, monkeypatch):
        from openjarvis.tools.web_search import WebSearchTool

        tool = WebSearchTool(api_key="test-key")
        monkeypatch.setattr(tool, "_fetch_url", lambda url: "Page text")
        assert tool.execute(query="https://example.com/article").success
        assert memory_budget.web_searched()

    def test_recall_refuses_once_the_web_was_searched(self, tmp_path, monkeypatch):
        from openjarvis.memory.store import LocalFactStore
        from openjarvis.tools import memory_tools

        store = LocalFactStore(tmp_path / "facts.jsonl")
        store.add("Sage uses GPT Luna.")
        monkeypatch.setattr(memory_tools, "_store", lambda: store)
        assert memory_tools.RecallTool().execute(query="luna").success
        memory_budget.note_web_search()
        refused = memory_tools.RecallTool().execute(query="luna")
        assert refused.success is False
        assert refused.content == memory_budget.AFTER_WEB_MESSAGE

    def test_retrieval_refuses_too(self):
        from openjarvis.tools.retrieval import RetrievalTool

        backend = type("B", (), {"retrieve": lambda self, *a, **k: []})()
        memory_budget.note_web_search()
        result = RetrievalTool(backend=backend).execute(query="gpt luna")
        assert result.content == memory_budget.AFTER_WEB_MESSAGE

    def test_a_new_message_starts_clean(self):
        memory_budget.note_web_search()
        memory_budget.start_message()
        assert not memory_budget.web_searched()


class TestSocialPages:
    @pytest.mark.parametrize("social", [False, True])
    def test_search_order_respects_the_turn_intent(self, social, monkeypatch):
        import sys

        from openjarvis.tools.web_search import WebSearchTool
        from tests.tools.test_web_search import _fake_tavily_module, _result

        page_access.set_turn("Search TikTok" if social else "Research an AI model")
        urls = ["https://x.com/a/status/1", "https://example.com/article"]
        fake, _ = _fake_tavily_module(
            search_return={
                "results": [
                    _result("AI model research", url, "AI model research evidence")
                    for url in urls
                ]
            }
        )
        monkeypatch.setitem(sys.modules, "tavily", fake)
        result = WebSearchTool(api_key="test-key").execute(query="AI model research")
        assert result.success
        assert [s["url"] for s in result.metadata["sources"]] == (
            urls if social else list(reversed(urls))
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://x.com/GeckoTerminal/status/2105559304057266220/photo/2",
            "https://www.facebook.com/groups/x/posts/1",
            "https://m.tiktok.com/@user/video/1",
            "twitter.com/a/status/1",
        ],
    )
    def test_social_hosts_are_recognised(self, url):
        assert page_access.is_social(url)

    def test_articles_are_not(self):
        assert not page_access.is_social("https://www.popularai.org/p/ajax")
        assert not page_access.is_social("https://www.reddit.com/r/LocalLLaMA/")
        assert not page_access.is_social("https://box.com/x")

    def test_web_read_skips_them_without_using_a_read(self):
        from openjarvis.tools.web_read import WebReadTool

        url = "https://x.com/GeckoTerminal/status/2105559304057266220/photo/2"
        page_access.set_turn("How useful is PewDiePie's Ajax model?")
        page_access.allow([url])
        result = WebReadTool().execute(url=url)
        assert result.success is False
        assert "social media" in result.content
        assert page_access.reads_used() == 0

    @pytest.mark.parametrize(
        "message",
        [
            "What are people saying on X about Ajax?",
            "search tiktok for the ajax memes",
            "check facebook posts about it",
            "what's the social media reaction?",
        ],
    )
    def test_asking_about_social_media_is_not_affected(self, message):
        page_access.set_turn(message)
        assert page_access.social_wanted()

    def test_a_plain_research_question_does_not_want_them(self):
        page_access.set_turn(
            "Can you search the ajax local ai model made by pewdiepie, how "
            "useful is that ai model"
        )
        assert not page_access.social_wanted()
