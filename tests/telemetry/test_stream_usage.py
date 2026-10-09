"""Streamed turns record what they used (chat and voice both stream)."""

from __future__ import annotations

import asyncio

import pytest

from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import Message, Role, StepType
from openjarvis.engine._stubs import StreamChunk
from openjarvis.telemetry.instrumented_engine import InstrumentedEngine

USAGE = {"prompt_tokens": 9460, "completion_tokens": 5, "cached_tokens": 6003}


class _StreamingEngine:
    engine_id = "cloud"

    async def stream_full(self, messages, *, model, **kwargs):
        yield StreamChunk(content="Ok")
        yield StreamChunk(content=".")
        yield StreamChunk(usage=dict(USAGE))

    def list_models(self):
        return ["gpt-6-luna"]


def _records(bus):
    return [
        e.data["record"]
        for e in bus.history
        if e.event_type == EventType.TELEMETRY_RECORD
    ]


def _drain(engine, *, stop_after=None):
    async def run():
        seen = 0
        agen = engine.stream_full(
            [Message(role=Role.USER, content="ok?")], model="gpt-6-luna"
        )
        async for _ in agen:
            seen += 1
            if stop_after and seen >= stop_after:
                break
        await agen.aclose()

    asyncio.run(run())


class TestStreamFullTelemetry:
    def test_records_one_row_with_cached_tokens(self):
        bus = EventBus(record_history=True)
        _drain(InstrumentedEngine(_StreamingEngine(), bus))

        (record,) = _records(bus)
        assert record.model_id == "gpt-6-luna"
        assert record.prompt_tokens == 9460
        assert record.completion_tokens == 5
        assert record.prompt_tokens_evaluated == 9460 - 6003
        assert record.metadata["cached_tokens"] == 6003
        assert record.is_streaming
        assert record.ttft >= 0

    def test_cost_prices_cached_input_at_the_cached_rate(self):
        bus = EventBus(record_history=True)
        _drain(InstrumentedEngine(_StreamingEngine(), bus))

        (record,) = _records(bus)
        expected = (3457 * 0.10 + 6003 * 0.01 + 5 * 0.50) / 1_000_000
        assert record.cost_usd == pytest.approx(expected)

    def test_publishes_no_inference_end(self):
        """The trace for a streamed turn is written by the route; an
        INFERENCE_END here would add a second generate step to it."""
        bus = EventBus(record_history=True)
        _drain(InstrumentedEngine(_StreamingEngine(), bus))

        assert EventType.INFERENCE_END not in [e.event_type for e in bus.history]

    def test_no_usage_means_no_row(self):
        class _NoUsage(_StreamingEngine):
            async def stream_full(self, messages, *, model, **kwargs):
                yield StreamChunk(content="Ok")

        bus = EventBus(record_history=True)
        _drain(InstrumentedEngine(_NoUsage(), bus))

        assert _records(bus) == []


class TestEstimateCost:
    def test_cached_tokens_default_to_full_price(self):
        from openjarvis.engine.cloud import estimate_cost

        assert estimate_cost("gpt-6-luna", 1_000_000, 0) == pytest.approx(0.10)

    def test_cached_part_is_cheaper(self):
        from openjarvis.engine.cloud import estimate_cost

        assert estimate_cost("gpt-6-luna", 1_000_000, 0, 1_000_000) == pytest.approx(
            0.01
        )

    def test_model_without_cached_price_ignores_cached(self):
        from openjarvis.engine.cloud import estimate_cost

        assert estimate_cost("gpt-4o", 1_000_000, 0, 1_000_000) == pytest.approx(2.50)


class TestEventsPublishedOnce:
    def test_multi_engine_of_instrumented_engines_publishes_its_own(self):
        from openjarvis.engine.multi import MultiEngine

        bus = EventBus()
        multi = MultiEngine(
            [("cloud", InstrumentedEngine(_StreamingEngine(), bus))]
        )
        assert multi._publishes_events is True

    def test_multi_engine_with_a_raw_engine_does_not(self):
        from openjarvis.engine.multi import MultiEngine

        bus = EventBus()
        multi = MultiEngine(
            [
                ("cloud", InstrumentedEngine(_StreamingEngine(), bus)),
                ("raw", _StreamingEngine()),
            ]
        )
        assert multi._publishes_events is False

    def test_guardrails_passes_the_flag_through(self):
        from openjarvis.security.guardrails import GuardrailsEngine

        bus = EventBus()
        wrapped = GuardrailsEngine(InstrumentedEngine(_StreamingEngine(), bus))
        assert wrapped._publishes_events is True


class TestStreamedTrace:
    def test_usage_becomes_a_generate_step_and_the_total(self):
        from openjarvis.traces.collector import record_response_trace

        class _Store:
            def save(self, trace):
                self.trace = trace

        store = _Store()
        record_response_trace(
            store,
            query="ok?",
            result="Ok.",
            model="gpt-6-luna",
            started_at=1.0,
            ended_at=2.0,
            usage={**USAGE, "rounds": 1},
        )
        trace = store.trace
        assert [s.step_type for s in trace.steps] == [
            StepType.GENERATE,
            StepType.RESPOND,
        ]
        assert trace.steps[0].output["cached_tokens"] == 6003
        assert trace.steps[0].output["rounds"] == 1
        assert trace.total_tokens == 9465

    def test_without_usage_the_trace_is_as_before(self):
        from openjarvis.traces.collector import record_response_trace

        class _Store:
            def save(self, trace):
                self.trace = trace

        store = _Store()
        record_response_trace(
            store, query="q", result="r", started_at=1.0, ended_at=2.0
        )
        assert [s.step_type for s in store.trace.steps] == [StepType.RESPOND]
        assert store.trace.total_tokens == 0

    def test_each_tool_is_a_step_with_its_outcome(self):
        """9 October: the debugger showed no tools; now each one is a step
        with what was asked, whether it worked and why not."""
        from openjarvis.traces.collector import record_response_trace

        class _Store:
            def save(self, trace):
                self.trace = trace

        store = _Store()
        record_response_trace(
            store,
            query="play the latest Kurzgesagt video",
            result="Opera isn't available.",
            started_at=1.0,
            ended_at=5.0,
            tools=[
                {
                    "tool": "youtube_play",
                    "arguments": '{"query": "Kurzgesagt", "latest": true}',
                    "success": False,
                    "result": "Opera GX is not listening on port 9222. " * 40,
                    "seconds": 3.02,
                    "started": 1.5,
                    "metadata": {"mode": "browser", "secret": "x"},
                }
            ],
        )
        steps = store.trace.steps
        assert [s.step_type for s in steps] == [StepType.TOOL_CALL, StepType.RESPOND]
        tool = steps[0]
        assert tool.input == {
            "tool": "youtube_play",
            "arguments": '{"query": "Kurzgesagt", "latest": true}',
        }
        assert tool.output["success"] is False
        assert tool.output["error"].startswith("Opera GX is not listening")
        assert len(tool.output["result"]) == 500
        assert tool.duration_seconds == 3.02
        assert tool.metadata == {"mode": "browser"}
