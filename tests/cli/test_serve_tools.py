"""Regression tests for tool selection during ``jarvis serve`` startup."""

from __future__ import annotations

import pytest

from openjarvis.cli.serve import _resolve_allowed_tools
from openjarvis.core.config import JarvisConfig


@pytest.mark.parametrize(
    "configured",
    [
        "code_interpreter,file_read",
        ["code_interpreter", "file_read"],
    ],
)
def test_tools_enabled_is_used_by_serve(configured):
    config = JarvisConfig()
    config.tools.enabled = configured

    allowed, explicit = _resolve_allowed_tools(config)

    assert allowed == {"code_interpreter", "file_read"}
    assert explicit is True


def test_tools_enabled_takes_precedence_over_legacy_agent_tools():
    config = JarvisConfig()
    config.tools.enabled = "file_read"
    config.agent.tools = "calculator"

    allowed, explicit = _resolve_allowed_tools(config)

    assert allowed == {"file_read"}
    assert explicit is True


def test_agent_tools_remains_a_backward_compatible_fallback():
    config = JarvisConfig()
    config.agent.tools = "file_read"

    allowed, explicit = _resolve_allowed_tools(config)

    assert allowed == {"file_read"}
    assert explicit is True


def test_serve_defaults_tools_when_no_selection_is_configured():
    allowed, explicit = _resolve_allowed_tools(JarvisConfig())

    assert allowed == {"think", "calculator", "web_search"}
    assert explicit is False


class TestRetrievalBackendInjection:
    """serve.py avoids SystemBuilder, so it must inject what builder would.

    Without a backend, RetrievalTool answers every query with "the memory
    backend isn't currently configured" — which in the web UI made the whole
    ingested corpus unreachable while looking like the model had simply
    declined to search.
    """

    def test_retrieval_is_built_with_a_backend(self):
        from openjarvis.cli.serve import _build_tool
        from openjarvis.tools.retrieval import RetrievalTool

        tool = _build_tool(RetrievalTool)

        assert isinstance(tool, RetrievalTool)
        assert tool._backend is not None

    def test_the_backend_is_the_ingested_corpus_not_the_scratchpad(self):
        """knowledge.db holds connector data; memory.db is the memory_* scratchpad."""
        from openjarvis.cli.serve import _build_tool
        from openjarvis.tools.retrieval import RetrievalTool

        tool = _build_tool(RetrievalTool)

        assert type(tool._backend).__name__ == "KnowledgeStore"

    def test_other_tools_are_built_unchanged(self):
        from openjarvis.cli.serve import _build_tool
        from openjarvis.tools.calculator import CalculatorTool

        assert isinstance(_build_tool(CalculatorTool), CalculatorTool)
