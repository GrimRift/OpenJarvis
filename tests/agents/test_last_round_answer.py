"""Out of rounds, Sage still answers (2 October).

A turn spent all fifteen rounds searching memory and the user got "Maximum
turns reached without a final answer" though it had read two articles. Now
the last step is one call without tools that has to answer.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from openjarvis.agents.orchestrator import LAST_ROUND_PROMPT, OrchestratorAgent
from tests.agents.test_orchestrator import _CalculatorStub

_TOOL_ROUND = {
    "content": "",
    "tool_calls": [
        {"id": "c1", "name": "calculator", "arguments": '{"expression":"1+1"}'}
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    "model": "m",
    "finish_reason": "tool_calls",
}


def _engine(last_answer):
    engine = MagicMock()
    engine.engine_id = "mock"
    calls = []

    def generate(messages, **kwargs):
        calls.append((list(messages), kwargs))
        if messages and messages[-1].content == LAST_ROUND_PROMPT:
            if isinstance(last_answer, Exception):
                raise last_answer
            return {"content": last_answer, "usage": {"prompt_tokens": 7}}
        return dict(_TOOL_ROUND)

    engine.generate.side_effect = generate
    return engine, calls


def test_the_last_round_answers_without_tools():
    engine, calls = _engine("From what I read: it is a small local model.")
    agent = OrchestratorAgent(engine, "m", tools=[_CalculatorStub()], max_turns=3)
    result = agent.run("Compare it")
    assert result.content == "From what I read: it is a small local model."
    assert result.metadata["max_turns_exceeded"] is True
    assert len(calls) == 4  # three rounds, then the answer
    assert "tools" not in calls[-1][1]


def test_falls_back_when_the_last_call_fails():
    engine, _ = _engine(RuntimeError("provider down"))
    agent = OrchestratorAgent(engine, "m", tools=[_CalculatorStub()], max_turns=2)
    result = agent.run("Compare it")
    assert result.content == "Maximum turns reached without a final answer."
