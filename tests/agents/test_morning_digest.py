"""Tests for MorningDigestAgent."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

from openjarvis.agents._stubs import AgentResult
from openjarvis.core.registry import AgentRegistry
from openjarvis.core.types import ToolResult


def test_morning_digest_registered():
    from openjarvis.agents.morning_digest import MorningDigestAgent

    AgentRegistry.register_value("morning_digest", MorningDigestAgent)
    assert AgentRegistry.contains("morning_digest")


def test_morning_digest_run(tmp_path):
    from openjarvis.agents.morning_digest import MorningDigestAgent

    mock_engine = MagicMock()
    mock_engine.generate.return_value = {
        "content": "Good morning sir. AtlasDB 1.0 was released.",
        "finish_reason": "stop",
        "usage": {},
    }

    # Mock collect result
    mock_collect_result = ToolResult(
        tool_name="digest_collect",
        content="=== WORLD ===\n[hackernews] AtlasDB 1.0 Released — 241 points\n",
        success=True,
        metadata={"total_items": 2},
    )

    # Mock TTS result
    mock_tts_result = ToolResult(
        tool_name="text_to_speech",
        content=str(tmp_path / "digest.mp3"),
        success=True,
        metadata={"audio_path": str(tmp_path / "digest.mp3")},
    )

    agent = MorningDigestAgent(
        mock_engine,
        "test-model",
        tools=[],
        persona="jarvis",
        sections=["world"],
        section_sources={"world": ["hackernews", "news_rss"]},
        digest_store_path=str(tmp_path / "digest.db"),
    )

    with patch.object(
        agent._executor,
        "execute",
        side_effect=[mock_collect_result, mock_tts_result],
    ):
        result = agent.run("Generate morning digest")

    assert isinstance(result, AgentResult)
    assert "Good morning" in result.content
    assert result.turns == 1
    assert len(result.tool_results) == 2
    assert set(result.metadata["sources_used"]) == {"hackernews", "news_rss"}
    prompt = "\n".join(
        message.text for message in mock_engine.generate.call_args.args[0]
    ).casefold()
    assert "world —" in prompt
    for forbidden in (
        "messages —|calendar —|health —|rebuttal|dinner at|group chat|"
        "slack|next meeting|readiness|hrv|weather"
    ).split("|"):
        assert forbidden not in prompt


def test_resolve_sources_maps_notes_to_obsidian():
    from openjarvis.agents.morning_digest import MorningDigestAgent

    agent = MorningDigestAgent(
        MagicMock(), "test-model", tools=[], sections=["notes"]
    )
    assert agent._resolve_sources() == ["obsidian"]


def test_load_persona():
    from openjarvis.agents.morning_digest import _load_persona

    # Nonexistent persona returns empty string
    result = _load_persona("nonexistent_persona_xyz")
    assert result == ""


def test_morning_digest_inherits_global_sage_persona(tmp_path):
    from openjarvis.agents.morning_digest import MorningDigestAgent

    (tmp_path / "SOUL.md").write_text(
        "GLOBAL_SAGE_PERSONA_SENTINEL", encoding="utf-8"
    )
    agent = MorningDigestAgent(
        MagicMock(), "test-model", tools=[], persona="jarvis", sections=["world"]
    )

    with patch(
        "openjarvis.agents.morning_digest.get_config_dir", return_value=tmp_path
    ):
        prompt = agent._build_system_prompt()

    assert "GLOBAL_SAGE_PERSONA_SENTINEL" in prompt
    assert "You are Sage" in prompt
    assert "spoken briefing" in prompt
    assert "You are Jarvis" not in prompt


def test_morning_digest_run_tts_failure_yields_no_audio_path(tmp_path):
    """A failed/unconfigured TTS backend must persist audio_path=None, not
    Path("") — the latter resolves to the CWD and always "exists"."""
    from openjarvis.agents.digest_store import DigestStore
    from openjarvis.agents.morning_digest import MorningDigestAgent

    mock_engine = MagicMock()
    mock_engine.generate.return_value = {
        "content": "Good morning sir.",
        "finish_reason": "stop",
        "usage": {},
    }
    mock_collect_result = ToolResult(
        tool_name="digest_collect",
        content="=== WORLD ===\n[hackernews] Something happened.\n",
        success=True,
        metadata={"total_items": 1},
    )
    mock_tts_result = ToolResult(
        tool_name="text_to_speech",
        content="TTS backend not available.",
        success=False,
    )

    db_path = str(tmp_path / "digest.db")
    agent = MorningDigestAgent(
        mock_engine,
        "test-model",
        tools=[],
        persona="jarvis",
        sections=["world"],
        section_sources={"world": ["hackernews"]},
        digest_store_path=db_path,
    )

    with patch.object(
        agent._executor,
        "execute",
        side_effect=[mock_collect_result, mock_tts_result],
    ):
        result = agent.run("Generate morning digest")

    assert result.metadata["audio_path"] == ""

    store = DigestStore(db_path=db_path)
    artifact = store.get_latest()
    store.close()
    assert artifact is not None
    assert artifact.audio_path is None


def test_morning_digest_can_defer_audio_for_interactive_delivery(tmp_path):
    """Web chat may return text first and let its audio player synthesize later."""
    from openjarvis.agents.digest_store import DigestStore
    from openjarvis.agents.morning_digest import MorningDigestAgent

    mock_engine = MagicMock()
    mock_engine.generate.return_value = {
        "content": "Good morning sir.",
        "finish_reason": "stop",
        "usage": {},
    }
    collect_result = ToolResult(
        tool_name="digest_collect",
        content="=== WORLD ===\n[hackernews] Something happened.\n",
        success=True,
    )
    db_path = str(tmp_path / "digest.db")
    agent = MorningDigestAgent(
        mock_engine,
        "test-model",
        tools=[],
        sections=["world"],
        section_sources={"world": ["hackernews"]},
        digest_store_path=db_path,
        generate_audio=False,
    )

    with patch.object(agent._executor, "execute", return_value=collect_result) as run:
        result = agent.run("Generate morning digest")

    run.assert_called_once()
    assert [item.tool_name for item in result.tool_results] == ["digest_collect"]
    assert result.metadata["audio_path"] == ""
    store = DigestStore(db_path=db_path)
    artifact = store.get_latest()
    store.close()
    assert artifact is not None
    assert artifact.audio_path is None


def test_build_morning_digest_agent_returns_none_when_unregistered():
    from openjarvis.agents.morning_digest import build_morning_digest_agent

    with patch(
        "openjarvis.agents.morning_digest.AgentRegistry.contains",
        return_value=False,
    ):
        result = build_morning_digest_agent(MagicMock(), "test-model", MagicMock())

    assert result is None


def test_build_morning_digest_agent_applies_digest_config():
    from openjarvis.agents.morning_digest import (
        MorningDigestAgent,
        build_morning_digest_agent,
    )
    from openjarvis.core.config import JarvisConfig

    AgentRegistry.register_value("morning_digest", MorningDigestAgent)
    config = JarvisConfig()
    config.digest.sections = ["world", "music"]
    config.digest.honorific = "boss"

    agent = build_morning_digest_agent(MagicMock(), "test-model", config)

    assert agent is not None
    assert isinstance(agent, MorningDigestAgent)
    assert agent._sections == ["world", "music"]
    assert agent._honorific == "boss"


def test_build_morning_digest_agent_can_disable_eager_audio():
    from openjarvis.agents.morning_digest import (
        MorningDigestAgent,
        build_morning_digest_agent,
    )
    from openjarvis.core.config import JarvisConfig

    AgentRegistry.register_value("morning_digest", MorningDigestAgent)
    agent = build_morning_digest_agent(
        MagicMock(), "test-model", JarvisConfig(), generate_audio=False
    )

    assert agent is not None
    assert agent._generate_audio is False


class TestEmptyGeneration:
    """A reasoning model can burn its whole completion budget thinking and
    return nothing. One live gpt-5.6-luna digest did exactly that at 1,024
    tokens with finish_reason="length"; the next attempt succeeded. An empty
    generation must never be delivered as an empty news day."""

    def _agent(self, tmp_path, engine):
        from openjarvis.agents.morning_digest import MorningDigestAgent

        return MorningDigestAgent(
            engine,
            "test-model",
            tools=[],
            persona="jarvis",
            sections=["world"],
            section_sources={"world": ["hackernews"]},
            digest_store_path=str(tmp_path / "digest.db"),
            generate_audio=False,
        )

    def _collect(self):
        return ToolResult(
            tool_name="digest_collect",
            content="=== WORLD ===\n[hackernews] AtlasDB 1.0 — 241 points\n",
            success=True,
            metadata={"total_items": 1},
        )

    def test_an_empty_first_attempt_is_retried_with_more_headroom(self, tmp_path):
        engine = MagicMock()
        engine.generate.side_effect = [
            {"content": "", "finish_reason": "length", "usage": {}},
            {"content": "Good morning sir.", "finish_reason": "stop", "usage": {}},
        ]
        agent = self._agent(tmp_path, engine)

        with patch.object(agent._executor, "execute", side_effect=[self._collect()]):
            result = agent.run("Generate morning digest")

        assert result.content == "Good morning sir."
        assert engine.generate.call_count == 2
        first, second = engine.generate.call_args_list
        assert second.kwargs["max_tokens"] > first.kwargs["max_tokens"]

    def test_a_successful_first_attempt_is_not_retried(self, tmp_path):
        engine = MagicMock()
        engine.generate.return_value = {
            "content": "Good morning sir.",
            "finish_reason": "stop",
            "usage": {},
        }
        agent = self._agent(tmp_path, engine)

        with patch.object(agent._executor, "execute", side_effect=[self._collect()]):
            agent.run("Generate morning digest")

        assert engine.generate.call_count == 1

    def test_content_that_is_only_thinking_counts_as_empty(self, tmp_path):
        engine = MagicMock()
        engine.generate.side_effect = [
            {"content": "<think>planning</think>", "finish_reason": "stop"},
            {"content": "Good morning sir.", "finish_reason": "stop"},
        ]
        agent = self._agent(tmp_path, engine)

        with patch.object(agent._executor, "execute", side_effect=[self._collect()]):
            result = agent.run("Generate morning digest")

        assert result.content == "Good morning sir."

    def test_two_empty_attempts_fail_closed(self, tmp_path):
        """No artifact, no speech, and a message that cannot be mistaken for
        'you have nothing waiting'."""
        engine = MagicMock()
        engine.generate.return_value = {"content": "", "finish_reason": "length"}
        agent = self._agent(tmp_path, engine)

        with patch.object(agent._executor, "execute", side_effect=[self._collect()]):
            result = agent.run("Generate morning digest")

        assert result.metadata["error"] == "empty_generation"
        assert "not a report" in result.content
        assert engine.generate.call_count == 2
        assert not (tmp_path / "digest.db").exists()

    def test_the_retry_is_bounded_to_one(self, tmp_path):
        engine = MagicMock()
        engine.generate.return_value = {"content": "", "finish_reason": "length"}
        agent = self._agent(tmp_path, engine)

        with patch.object(agent._executor, "execute", side_effect=[self._collect()]):
            agent.run("Generate morning digest")

        assert engine.generate.call_count == 2

    def test_an_empty_revision_keeps_the_scored_briefing(self, tmp_path):
        """A low quality score must not be able to cost the whole briefing.

        ``openjarvis.agents.digest_evaluator`` does not exist -- the import in
        ``run()`` raises ImportError on every real run and is swallowed, so the
        evaluate/regenerate branch is currently dead. A stub module is
        installed here so the guard is actually exercised rather than skipped
        by the same ImportError.
        """
        evaluator = MagicMock()
        evaluator.evaluate.return_value = (3.0, "Too terse.")
        stub = ModuleType("openjarvis.agents.digest_evaluator")
        stub.DigestEvaluator = MagicMock(return_value=evaluator)

        engine = MagicMock()
        engine.generate.side_effect = [
            {"content": "Good morning sir.", "finish_reason": "stop"},
            {"content": "", "finish_reason": "length"},
            {"content": "", "finish_reason": "length"},
        ]
        agent = self._agent(tmp_path, engine)

        with (
            patch.object(agent._executor, "execute", side_effect=[self._collect()]),
            patch.dict(
                sys.modules,
                {"openjarvis.agents.digest_evaluator": stub},
            ),
        ):
            result = agent.run("Generate morning digest")

        evaluator.evaluate.assert_called_once()
        assert result.content == "Good morning sir."

    def test_the_evaluator_module_is_absent_and_the_digest_still_ships(
        self, tmp_path
    ):
        """Documents live behaviour: the evaluate/regenerate step never runs,
        so every stored digest carries quality_score 0.0. Delete this test if
        digest_evaluator is ever actually written."""
        import importlib.util

        assert importlib.util.find_spec("openjarvis.agents.digest_evaluator") is None

        engine = MagicMock()
        engine.generate.return_value = {
            "content": "Good morning sir.",
            "finish_reason": "stop",
        }
        agent = self._agent(tmp_path, engine)

        with patch.object(agent._executor, "execute", side_effect=[self._collect()]):
            result = agent.run("Generate morning digest")

        assert result.content == "Good morning sir."
        assert engine.generate.call_count == 1
