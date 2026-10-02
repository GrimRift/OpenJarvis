"""The app's streaming loop answers when out of rounds, and stops offering
memory search once three are spent (2 October)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from openjarvis.core.events import EventBus  # noqa: E402
from openjarvis.server.app import create_app  # noqa: E402
from tests.server.test_routes import _make_engine, _test_config  # noqa: E402


def _post(engine, tools, max_turns=3):
    from openjarvis.agents.orchestrator import OrchestratorAgent

    agent = OrchestratorAgent(
        engine,
        "test-model",
        tools=tools,
        bus=EventBus(),
        max_turns=max_turns,
        temperature=0.7,
        max_tokens=128,
        system_prompt="Use the configured tools.",
    )
    app = create_app(
        engine, "test-model", agent=agent, bus=EventBus(), config=_test_config()
    )
    resp = TestClient(app).post(
        "/v1/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "compare it to gpt luna"}],
            "stream": True,
        },
    )
    assert resp.status_code == 200
    content = ""
    for line in resp.text.strip().split("\n"):
        if not line.startswith("data:") or "[DONE]" in line:
            continue
        data = json.loads(line[5:].strip())
        content += data.get("choices", [{}])[0].get("delta", {}).get("content") or ""
    return content


def _memory_looping_engine(seen_tools, last_answer="Here is what I found."):
    from openjarvis.agents.orchestrator import LAST_ROUND_PROMPT
    from openjarvis.engine._stubs import StreamChunk

    engine = _make_engine(content="unused")

    async def stream_full(messages, *, model, **kwargs):
        names = [t["function"]["name"] for t in kwargs.get("tools") or []]
        seen_tools.append(names)
        if messages[-1].content == LAST_ROUND_PROMPT:
            assert not kwargs.get("tools")
            yield StreamChunk(content=last_answer)
            yield StreamChunk(finish_reason="stop", usage={})
            return
        yield StreamChunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": f"call_{len(seen_tools)}",
                    "function": {
                        "name": "recall",
                        "arguments": json.dumps({"query": f"luna {len(seen_tools)}"}),
                    },
                }
            ],
            finish_reason="tool_calls",
        )

    engine.stream_full = stream_full
    return engine


def test_out_of_rounds_still_answers(tmp_path, monkeypatch):
    from openjarvis.memory.store import LocalFactStore
    from openjarvis.tools import memory_tools

    store = LocalFactStore(tmp_path / "facts.jsonl")
    store.add("Sage uses GPT Luna as its cloud model.")
    monkeypatch.setattr(memory_tools, "_store", lambda: store)
    seen = []
    content = _post(_memory_looping_engine(seen), [memory_tools.RecallTool()], 5)
    assert content == "Here is what I found."
    assert "Maximum turns" not in content
    # Three searches, then recall is no longer offered.
    assert "recall" in seen[0] and "recall" in seen[2]
    assert all("recall" not in names for names in seen[3:])
