"""Tests for the retrieval tool."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from openjarvis.tools.retrieval import RetrievalTool
from openjarvis.tools.storage._stubs import MemoryBackend, RetrievalResult


class _FakeBackend(MemoryBackend):
    """In-memory fake backend for testing."""

    backend_id = "fake"

    def __init__(self, results: Optional[List[RetrievalResult]] = None) -> None:
        self._results = results or []
        self.last_retrieve: Dict[str, Any] = {}

    def store(
        self,
        content: str,
        *,
        source: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        return "fake-id"

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        **kwargs: Any,
    ) -> List[RetrievalResult]:
        self.last_retrieve = {"query": query, "top_k": top_k, **kwargs}
        return self._results[:top_k]

    def delete(self, doc_id: str) -> bool:
        return False

    def clear(self) -> None:
        self._results.clear()


class _ErrorBackend(_FakeBackend):
    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        **kwargs: Any,
    ) -> List[RetrievalResult]:
        raise RuntimeError("backend error")


class TestRetrievalTool:
    def test_spec(self):
        tool = RetrievalTool()
        assert tool.spec.name == "retrieval"
        assert tool.spec.category == "memory"

    def test_spec_exposes_structured_recency_filters(self):
        properties = RetrievalTool().spec.parameters["properties"]

        assert "source" in properties
        assert "days_back" in properties
        assert "since" in properties
        assert "until" in properties
        assert "check my inbox" in properties["days_back"]["description"]

    def test_no_backend(self):
        tool = RetrievalTool()
        result = tool.execute(query="test")
        assert result.success is False
        assert "No memory backend" in result.content

    def test_empty_query(self):
        tool = RetrievalTool(backend=_FakeBackend())
        result = tool.execute(query="")
        assert result.success is False

    def test_no_results(self):
        tool = RetrievalTool(backend=_FakeBackend())
        result = tool.execute(query="test")
        assert result.success is True
        assert "No relevant results" in result.content

    def test_with_results(self):
        results = [
            RetrievalResult(content="Answer 1", score=0.9, source="doc.md"),
            RetrievalResult(content="Answer 2", score=0.8, source="other.md"),
        ]
        tool = RetrievalTool(backend=_FakeBackend(results))
        result = tool.execute(query="test")
        assert result.success is True
        assert "Answer 1" in result.content
        assert "[Source: doc.md]" in result.content
        assert result.metadata["num_results"] == 2

    def test_forwards_source_and_relative_recency_to_backend(self):
        backend = _FakeBackend(
            [RetrievalResult(content="Recent mail", source="gmail")]
        )
        before = datetime.now() - timedelta(days=7, seconds=1)

        result = RetrievalTool(backend=backend).execute(
            query="urgent inbox messages",
            source="gmail",
            days_back=7,
        )

        after = datetime.now() - timedelta(days=7) + timedelta(seconds=1)
        assert result.success is True
        assert backend.last_retrieve["source"] == "gmail"
        assert before <= backend.last_retrieve["since"] <= after

    def test_result_exposes_date_and_subject_to_the_model(self):
        result = RetrievalTool(
            backend=_FakeBackend(
                [
                    RetrievalResult(
                        content="Security alert",
                        source="gmail",
                        metadata={
                            "timestamp": "2018-07-01T10:00:00",
                            "title": "New device signed in",
                        },
                    )
                ]
            )
        ).execute(query="security alert")

        assert "Date: 2018-07-01T10:00:00" in result.content
        assert "ago" in result.content
        assert "Subject: New device signed in" in result.content

    def test_top_k_override(self):
        results = [
            RetrievalResult(content="A", score=0.9),
            RetrievalResult(content="B", score=0.8),
            RetrievalResult(content="C", score=0.7),
        ]
        tool = RetrievalTool(backend=_FakeBackend(results), top_k=10)
        result = tool.execute(query="test", top_k=1)
        assert result.success is True
        assert "A" in result.content

    def test_backend_error(self):
        tool = RetrievalTool(backend=_ErrorBackend())
        result = tool.execute(query="test")
        assert result.success is False
        assert "Retrieval error" in result.content

    def test_openai_function(self):
        tool = RetrievalTool()
        fn = tool.to_openai_function()
        assert fn["function"]["name"] == "retrieval"
