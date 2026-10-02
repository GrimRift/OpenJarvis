"""At most three memory searches per message (2 October: twelve in one turn)."""

from __future__ import annotations

import pytest

from openjarvis.tools import memory_budget


@pytest.fixture(autouse=True)
def _fresh():
    memory_budget.start_message()
    yield
    memory_budget.start_message()


def test_three_then_refused_until_the_next_message():
    assert [memory_budget.take() for _ in range(4)] == [True, True, True, False]
    assert memory_budget.spent()
    memory_budget.start_message()
    assert memory_budget.take() and not memory_budget.spent()


def test_old_searches_stop_counting(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(memory_budget.time, "monotonic", lambda: now[0])
    for _ in range(3):
        memory_budget.take()
    assert memory_budget.spent()
    now[0] += memory_budget.WINDOW_SECONDS + 1
    # A scheduled run never calls start_message; the window frees it.
    assert not memory_budget.spent() and memory_budget.take()


def test_recall_says_so_once_spent(tmp_path, monkeypatch):
    from openjarvis.memory.store import LocalFactStore
    from openjarvis.tools import memory_tools

    store = LocalFactStore(tmp_path / "facts.jsonl")
    store.add("User likes Monster Hunter.")
    monkeypatch.setattr(memory_tools, "_store", lambda: store)
    tool = memory_tools.RecallTool()
    for _ in range(3):
        assert tool.execute(query="Monster Hunter").success
    spent = tool.execute(query="Monster Hunter")
    assert spent.success is False
    assert spent.content == memory_budget.SPENT_MESSAGE


def test_retrieval_counts_against_the_same_budget():
    from openjarvis.tools.retrieval import RetrievalTool

    backend = type("B", (), {"retrieve": lambda self, *a, **k: []})()
    tool = RetrievalTool(backend=backend)
    for _ in range(3):
        memory_budget.take()
    result = tool.execute(query="gpt luna")
    assert result.success is False
    assert result.content == memory_budget.SPENT_MESSAGE
