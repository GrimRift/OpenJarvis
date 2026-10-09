"""Model line and weather card rules in the chat route (2 October)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from openjarvis.core.types import Message, Role  # noqa: E402
from openjarvis.server import routes  # noqa: E402
from tests.server.test_routes import _test_config  # noqa: E402


def test_each_turn_states_the_model_answering():
    out = routes._ensure_identity_prompt(
        [Message(role=Role.USER, content="hi")],
        _test_config(),
        turn_context=True,
        model="gpt-6-luna",
    )
    turn = [m for m in out if m.metadata.get("turn_context")]
    assert turn, "no per-turn context message"
    assert "You are answering with gpt-6-luna (cloud)" in turn[0].content
    assert "describe an older setup" in turn[0].content
    # Kept out of the system prompt, so its cache is not broken by it.
    system = [m for m in out if m.role == Role.SYSTEM]
    assert all("gpt-6-luna" not in m.content for m in system)


def test_a_comparison_gets_the_grid_hint_next_to_the_question():
    """9 October: "airpods 5 vs airpods 4?" got a markdown table."""
    out = routes._ensure_identity_prompt(
        [Message(role=Role.USER, content="airpods 5 vs airpods 4?")],
        _test_config(),
        "auto",
        turn_context=True,
    )
    turn = [m for m in out if m.metadata.get("turn_context")]
    assert turn and "draws it as a grid" in turn[0].content
    system = [m for m in out if m.role == Role.SYSTEM]
    assert all("This question compares options" not in m.content for m in system)


def test_no_model_no_line():
    assert routes._model_line("") == ""


@pytest.mark.parametrize(
    ("text", "asked"),
    [
        ("What's the weather like?", True),
        ("Will it rain later at eleven?", True),
        ("Should I bring an umbrella tomorrow?", True),
        ("uulan ba mamaya?", True),
        ("may bagyo ba?", True),
        (
            "Can you search the ajax local ai model made by pewdiepie, how "
            "useful is that ai model",
            False,
        ),
        ("show me the photo of the initial prototype", False),
        ("What is the hot new AI model?", False),
        ("Help me write a cold email", False),
        ("Explain heat treatment of steel", False),
        ("What is the temperature of my GPU?", False),
        ("Explain a 90 degrees rotation", False),
        ("Show the sales forecast", False),
        ("Will it be hot tomorrow?", True),
        ("How cold is it outside?", True),
        ("mainit ba bukas?", True),
        ("ang lamig ngayon", True),
        # Widened 6 October: plain weather questions that showed no card.
        ("What's the forecast?", True),
        ("Give me the forecast", True),
        ("What's the temperature now?", True),
        ("Ilang degrees ngayon?", True),
        ("What temperature should I bake bread at?", False),
        ("convert 90 degrees to radians", False),
        ("what degree program is best for AI?", False),
        ("demand forecast for next month", False),
    ],
)
def test_the_weather_card_is_for_weather_questions(text, asked):
    assert routes._asks_about_weather(text) is asked


def test_timing_logs_safe_arguments_without_free_text_or_url_secrets(caplog):
    import json
    import logging
    from types import SimpleNamespace

    call = SimpleNamespace(
        name="web_search",
        arguments=json.dumps(
            {
                "query": "private-message-sentinel",
                "password": "credential-sentinel",
                "nested": {"token": "nested-secret"},
                "max_results": 3,
            }
        ),
    )
    result = SimpleNamespace(
        success=True,
        content="result",
        metadata={"url": "https://example.com/private-path?token=url-secret"},
    )
    with caplog.at_level(logging.INFO, logger="openjarvis.timing"):
        routes._log_tool_timing(call, result, 0.1)
    for private in (
        "private-message-sentinel",
        "credential-sentinel",
        "nested-secret",
        "private-path",
        "url-secret",
    ):
        assert private not in caplog.text
    assert "max_results" in caplog.text and "3" in caplog.text
    assert "example.com" in caplog.text


def test_timing_logs_keep_safe_weather_day(caplog):
    import logging
    from types import SimpleNamespace

    call = SimpleNamespace(name="weather", arguments='{"day":"tomorrow"}')
    result = SimpleNamespace(success=True, content="rain", metadata={})
    with caplog.at_level(logging.INFO, logger="openjarvis.timing"):
        routes._log_tool_timing(call, result, 0.1)
    assert "tomorrow" in caplog.text


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("assistant_tail", [False, True])
def test_real_chat_path_shares_worker_state_and_resets_next_turn(
    stream, assistant_tail
):
    """Exercise the ASGI middleware and real agent, with only the engine mocked."""
    from fastapi.testclient import TestClient

    from openjarvis.agents.orchestrator import OrchestratorAgent
    from openjarvis.core.types import ToolResult
    from openjarvis.engine._stubs import StreamChunk
    from openjarvis.security import page_access
    from openjarvis.server.app import create_app
    from openjarvis.tools import memory_budget
    from openjarvis.tools._stubs import BaseTool, ToolSpec
    from tests.server.test_routes import _make_engine

    executed = []
    offered = []

    class ProbeTool(BaseTool):
        def __init__(self, name):
            self.name = name

        @property
        def spec(self):
            return ToolSpec(
                name=self.name,
                description=self.name,
                parameters={"type": "object", "properties": {}},
            )

        def execute(self, **params):
            assert not page_access.is_allowed(
                "https://untrusted.example/assistant-link"
            )
            executed.append((page_access.social_wanted(), memory_budget.web_searched()))
            memory_budget.note_web_search()
            return ToolResult(tool_name=self.name, content="Evidence", success=True)

    def response(messages, **kwargs):
        names = {t["function"]["name"] for t in kwargs.get("tools") or []}
        offered.append(names)
        if not any(m.role == Role.TOOL for m in messages):
            assert not memory_budget.web_searched()
            return {
                "content": "",
                "tool_calls": [
                    {"id": "search_1", "name": "web_search", "arguments": "{}"}
                ],
            }
        assert memory_budget.web_searched(), "worker state did not reach the loop"
        return {"content": "Research complete.", "finish_reason": "stop"}

    async def stream_response(messages, **kwargs):
        result = response(messages, **kwargs)
        if result.get("tool_calls"):
            call = result["tool_calls"][0]
            yield StreamChunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": call["id"],
                        "function": {
                            "name": call["name"],
                            "arguments": call["arguments"],
                        },
                    }
                ],
                finish_reason="tool_calls",
            )
        else:
            yield StreamChunk(content=result["content"], finish_reason="stop")

    engine = _make_engine()
    engine.generate.side_effect = response
    engine.stream_full = stream_response
    agent = OrchestratorAgent(
        engine,
        "test-model",
        max_turns=3,
        tools=[ProbeTool(n) for n in ("web_search", "recall", "retrieval", "web_read")],
    )
    client = TestClient(
        create_app(engine, "test-model", agent=agent, config=_test_config())
    )
    for query in ("Search TikTok for memes", "Research an AI model"):
        messages = [{"role": "user", "content": query}]
        if assistant_tail:
            messages.append(
                {
                    "role": "assistant",
                    "content": "Read https://untrusted.example/assistant-link",
                }
            )
        result = client.post(
            "/v1/chat/completions",
            json={
                "model": "test-model",
                "messages": messages,
                "stream": stream,
            },
        )
        assert result.status_code == 200
        assert "Research complete." in result.text
    assert executed == [(True, False), (False, False)]
    assert len(offered) == 4
    for first, second in (offered[:2], offered[2:]):
        assert {"recall", "retrieval"} <= first
        assert not {"recall", "retrieval"} & second
        assert "web_read" in second


def test_overlapping_streams_keep_independent_research_state():
    """Force a second request to start while the first tool is still running."""
    import json
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    from openjarvis.agents.orchestrator import OrchestratorAgent
    from openjarvis.core.types import ToolResult
    from openjarvis.engine._stubs import StreamChunk
    from openjarvis.security import page_access
    from openjarvis.server.app import create_app
    from openjarvis.tools import memory_budget
    from openjarvis.tools._stubs import BaseTool, ToolSpec
    from tests.server.test_routes import _make_engine

    first_started, second_finished = threading.Event(), threading.Event()
    observations = {}

    class SearchTool(BaseTool):
        @property
        def spec(self):
            return ToolSpec(
                name="web_search",
                description="Search",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            )

        def execute(self, **params):
            query = params["query"]
            clean = not memory_budget.web_searched()
            memory_budget.note_web_search()
            page_access.note_read()
            if query == "Research an AI model":
                first_started.set()
                assert second_finished.wait(10), "second request never ran"
            observations[query] = (
                clean,
                page_access.social_wanted(),
                page_access.reads_used(),
                memory_budget.web_searched(),
            )
            if query == "Search TikTok memes":
                second_finished.set()
            return ToolResult(tool_name="web_search", content="Evidence", success=True)

    async def stream_response(messages, **kwargs):
        if any(m.role == Role.TOOL for m in messages):
            yield StreamChunk(content="Complete", finish_reason="stop")
            return
        query = messages[-1].content
        yield StreamChunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": "search_1",
                    "function": {
                        "name": "web_search",
                        "arguments": json.dumps({"query": query}),
                    },
                }
            ],
            finish_reason="tool_calls",
        )

    engine = _make_engine()
    engine.stream_full = stream_response
    agent = OrchestratorAgent(engine, "test-model", tools=[SearchTool()], max_turns=3)
    client = TestClient(
        create_app(engine, "test-model", agent=agent, config=_test_config())
    )

    def ask(query):
        return client.post(
            "/v1/chat/completions",
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": query}],
                "stream": True,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(ask, "Research an AI model")
        assert first_started.wait(10), "first tool never ran"
        second = pool.submit(ask, "Search TikTok memes")
        for response in (first.result(timeout=15), second.result(timeout=15)):
            assert response.status_code == 200
            assert "Complete" in response.text
    assert observations == {
        "Research an AI model": (True, False, 1, True),
        "Search TikTok memes": (True, True, 1, True),
    }


def test_one_round_of_page_reads_per_turn():
    """9 October: the AirPods answer read pages in three separate rounds.

    After a round with a successful read, web_read is withdrawn and the
    model answers; a round where every read was refused keeps it."""
    from fastapi.testclient import TestClient

    from openjarvis.agents.orchestrator import OrchestratorAgent
    from openjarvis.core.types import ToolResult
    from openjarvis.engine._stubs import StreamChunk
    from openjarvis.server.app import create_app
    from openjarvis.tools._stubs import BaseTool, ToolSpec
    from tests.server.test_routes import _make_engine

    offered: list[set] = []
    reads = {"n": 0}

    class Probe(BaseTool):
        def __init__(self, name):
            self.name = name

        @property
        def spec(self):
            return ToolSpec(
                name=self.name,
                description=self.name,
                parameters={"type": "object", "properties": {}},
            )

        def execute(self, **params):
            if self.name == "web_read":
                reads["n"] += 1
                # The first read is refused (a guessed link), the second works.
                ok = reads["n"] > 1
                return ToolResult(tool_name="web_read", content="page", success=ok)
            return ToolResult(tool_name=self.name, content="results", success=True)

    script = [
        ("web_search", "s1"),
        ("web_read", "r1"),
        ("web_read", "r2"),
        ("web_read", "r3"),
    ]

    async def stream_response(messages, **kwargs):
        offered.append({t["function"]["name"] for t in kwargs.get("tools") or []})
        step = len(offered) - 1
        if step < len(script) and script[step][0] in offered[-1]:
            name, call_id = script[step]
            yield StreamChunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": call_id,
                        "function": {
                            "name": name,
                            "arguments": f'{{"url": "https://example.com/{call_id}"}}',
                        },
                    }
                ],
                finish_reason="tool_calls",
            )
        else:
            yield StreamChunk(content="Answer.", finish_reason="stop")

    engine = _make_engine()
    engine.stream_full = stream_response
    agent = OrchestratorAgent(
        engine,
        "test-model",
        max_turns=6,
        tools=[Probe(n) for n in ("web_search", "web_read")],
    )
    client = TestClient(
        create_app(engine, "test-model", agent=agent, config=_test_config())
    )
    result = client.post(
        "/v1/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "Research the AirPods 5"}],
            "stream": True,
        },
    )
    assert result.status_code == 200
    assert "Answer." in result.text
    # Refused read: still offered. Successful read: gone, so it answers.
    assert "web_read" in offered[2]
    assert "web_read" not in offered[3]
    assert reads["n"] == 2
