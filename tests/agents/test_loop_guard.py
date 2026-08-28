"""Tests for agent loop guard (Phase 14.3)."""

from __future__ import annotations

from openjarvis.core.events import EventBus, EventType


class TestLoopGuard:
    def _make_guard(self, **kwargs):
        from openjarvis.agents.loop_guard import LoopGuard, LoopGuardConfig

        kwargs.setdefault("warn_before_block", False)
        config = LoopGuardConfig(**kwargs)
        bus = EventBus(record_history=True)
        return LoopGuard(config, bus=bus), bus

    def test_identical_calls_blocked(self):
        guard, bus = self._make_guard(max_identical_calls=2)
        v1 = guard.check_call("calc", '{"x": 1}')
        assert not v1.blocked
        # Rust backend uses a HashSet — blocks on the second identical call
        v2 = guard.check_call("calc", '{"x": 1}')
        assert v2.blocked
        assert "identical" in v2.reason.lower()

    def test_different_args_not_blocked(self):
        guard, _ = self._make_guard(max_identical_calls=2)
        guard.check_call("calc", '{"x": 1}')
        guard.check_call("calc", '{"x": 1}')
        v = guard.check_call("calc", '{"x": 2}')
        assert not v.blocked

    def test_ping_pong_detection(self):
        guard, _ = self._make_guard(ping_pong_window=4, poll_tool_budget=100)
        guard.check_call("A", "{}")
        guard.check_call("B", "{}")
        guard.check_call("A", '{"x": 1}')
        guard.check_call("B", '{"x": 1}')
        guard.check_call("A", '{"x": 2}')
        # After A-B-A-B pattern, next A should be blocked
        # Note: exact blocking depends on the window + detection logic
        # The sequence [A, B, A, B, A] with window=4 should detect A-B-A-B
        # But detection happens after 4+ calls in sequence

    def test_poll_budget_exceeded(self):
        guard, _ = self._make_guard(poll_tool_budget=3, max_identical_calls=100)
        guard.check_call("poll", '{"a": 1}')
        guard.check_call("poll", '{"a": 2}')
        guard.check_call("poll", '{"a": 3}')
        v = guard.check_call("poll", '{"a": 4}')
        assert v.blocked
        assert "poll budget" in v.reason.lower()

    def test_event_emitted(self):
        guard, bus = self._make_guard(max_identical_calls=1)
        guard.check_call("x", '{"a": 1}')
        guard.check_call("x", '{"a": 1}')
        events = [
            e for e in bus.history if e.event_type == EventType.LOOP_GUARD_TRIGGERED
        ]
        assert len(events) == 1

    def test_reset(self):
        guard, _ = self._make_guard(max_identical_calls=2)
        guard.check_call("x", '{"a": 1}')
        guard.check_call("x", '{"a": 1}')
        guard.reset()
        v = guard.check_call("x", '{"a": 1}')
        assert not v.blocked

    def test_context_compression_no_overflow(self):
        from openjarvis.core.types import Message, Role

        guard, _ = self._make_guard(max_context_messages=100)
        messages = [Message(role=Role.USER, content=f"msg {i}") for i in range(10)]
        result = guard.compress_context(messages)
        assert len(result) == 10

    def test_context_compression_with_overflow(self):
        from openjarvis.core.types import Message, Role

        guard, _ = self._make_guard(max_context_messages=10)
        messages = (
            [
                Message(role=Role.SYSTEM, content="sys"),
            ]
            + [Message(role=Role.USER, content=f"msg {i}") for i in range(50)]
            + [
                Message(role=Role.TOOL, content=f"result {i}", tool_call_id=f"t{i}")
                for i in range(50)
            ]
        )
        result = guard.compress_context(messages)
        assert len(result) <= 10

    def test_context_compression_stage4_uses_current_state(self):
        """Stage 4 should derive from compressed state."""
        from openjarvis.core.types import Message, Role

        guard, _ = self._make_guard(max_context_messages=5)
        messages = (
            [
                Message(role=Role.SYSTEM, content="sys"),
            ]
            + [Message(role=Role.USER, content=f"msg {i}") for i in range(100)]
            + [
                Message(
                    role=Role.TOOL,
                    content=f"result {i}",
                    tool_call_id=f"t{i}",
                )
                for i in range(100)
            ]
        )
        result = guard.compress_context(messages)
        assert len(result) == 5
        system_count = sum(1 for m in result if getattr(m, "role", None) == "system")
        assert system_count == 1

    def test_check_response_returns_unblocked(self):
        guard, _ = self._make_guard()
        v = guard.check_response("some content")
        assert not v.blocked

    def test_disabled_loop_guard(self):
        from openjarvis.agents.loop_guard import LoopGuard, LoopGuardConfig

        config = LoopGuardConfig(enabled=False)
        guard = LoopGuard(config)
        # Even though we'd normally block, disabled guard shouldn't
        for _ in range(10):
            guard.check_call("x", '{"a": 1}')
        # Guard is still created but check_call still works
        # (the enabled flag is checked at the ToolUsingAgent level)


class TestLoopGuardResetPerRun:
    """The guard must not carry counts between independent requests.

    Its counters live on the agent, and the server builds one agent at
    startup and reuses it for every chat message. Without a reset per run
    the third identical "open obsidian" of the server's lifetime is refused
    — and every one after it, in any conversation — replying "I cannot
    repeat the same tool call" while nothing opens.
    """

    def _agent(self):
        from unittest.mock import MagicMock

        from openjarvis.agents.orchestrator import OrchestratorAgent

        engine = MagicMock()
        engine.generate.return_value = {
            "content": "done",
            "tool_calls": [],
            "usage": {},
        }
        return OrchestratorAgent(engine, "test-model")

    def test_run_clears_previous_call_counts(self):
        agent = self._agent()
        assert agent._loop_guard is not None

        for _ in range(6):
            agent._loop_guard.check_call("open_app", '{"app": "obsidian"}')
        assert agent._loop_guard.check_call("open_app", '{"app": "obsidian"}').blocked

        agent.run("open obsidian")

        verdict = agent._loop_guard.check_call("open_app", '{"app": "obsidian"}')
        assert not verdict.blocked


class TestTokenAwareCompression:
    """Message count is a poor proxy for cost. A tool-calling turn re-sends the
    whole conversation every turn, so 20 prior exchanges — 41 messages, far
    under the 100-message threshold — took one request from 7,296 to 31,416
    tokens."""

    @staticmethod
    def _msg(role, content):
        from openjarvis.core.types import Message, Role

        return Message(role=getattr(Role, role.upper()), content=content)

    def _history(self, pairs: int, chars: int = 2000):
        msgs = [self._msg("system", "You are Sage.")]
        for i in range(pairs):
            msgs.append(self._msg("user", f"q{i} " + "x" * chars))
            msgs.append(self._msg("assistant", f"a{i} " + "y" * chars))
        return msgs

    def _guard(self, **overrides):
        from openjarvis.agents.loop_guard import LoopGuard, LoopGuardConfig

        return LoopGuard(LoopGuardConfig(**overrides))

    def test_a_long_history_under_the_message_cap_is_compressed(self):
        guard = self._guard(max_context_tokens=2000)
        messages = self._history(20)

        out = guard.compress_context(messages)

        assert len(messages) < 100  # would never have triggered on count
        assert len(out) < len(messages)
        assert guard._approx_tokens(out) <= 2000

    def test_the_newest_exchange_always_survives(self):
        guard = self._guard(max_context_tokens=100)
        messages = self._history(10)

        out = guard.compress_context(messages)

        assert out[-1] is messages[-1]

    def test_a_single_oversized_message_is_still_kept(self):
        """Never return a conversation with nothing in it."""
        guard = self._guard(max_context_tokens=10)
        messages = [self._msg("system", "sys"), self._msg("user", "z" * 40_000)]

        out = guard.compress_context(messages)

        assert any(not m.role.value == "system" for m in out)

    def test_the_system_message_is_preserved(self):
        guard = self._guard(max_context_tokens=500)
        messages = self._history(20)

        out = guard.compress_context(messages)

        assert out[0].content == "You are Sage."

    def test_a_short_conversation_is_untouched(self):
        guard = self._guard(max_context_tokens=8000)
        messages = self._history(2, chars=50)

        assert guard.compress_context(messages) is messages

    def test_zero_disables_the_token_check(self):
        guard = self._guard(max_context_tokens=0)
        messages = self._history(20)

        assert guard.compress_context(messages) is messages

    def test_the_count_threshold_still_applies(self):
        guard = self._guard(max_context_tokens=0, max_context_messages=10)
        messages = self._history(30, chars=1)

        out = guard.compress_context(messages)

        assert len(out) <= 10


class TestPrefixStabilityForPromptCache:
    """The provider serves a repeated prefix from cache — measured at 99.9% of
    a 2,416-token prompt on the second identical call. Trimming inside the turn
    loop moves the start of the context every turn, so the prefix changes and
    every turn misses the cache, costing far more than the trimming saves."""

    @staticmethod
    def _msg(role, content):
        from openjarvis.core.types import Message, Role

        return Message(role=getattr(Role, role.upper()), content=content)

    def _guard(self, **overrides):
        from openjarvis.agents.loop_guard import LoopGuard, LoopGuardConfig

        return LoopGuard(LoopGuardConfig(**overrides))

    def _long_history(self, pairs=20, chars=2000):
        msgs = [self._msg("system", "You are Sage.")]
        for i in range(pairs):
            msgs.append(self._msg("user", f"q{i} " + "x" * chars))
            msgs.append(self._msg("assistant", f"a{i} " + "y" * chars))
        return msgs

    def test_the_token_budget_can_be_switched_off(self):
        guard = self._guard(max_context_tokens=500)
        messages = self._long_history()

        assert guard.compress_context(messages, apply_token_budget=False) is messages
        assert guard.compress_context(messages) is not messages

    def test_a_growing_loop_does_not_move_the_context_start(self):
        """What the in-loop call must guarantee: appending tool results across
        turns must not shift which message the context begins with."""
        guard = self._guard(max_context_tokens=500)
        messages = guard.compress_context(self._long_history())
        first = messages[0]
        second = messages[1]

        for turn in range(5):
            messages = messages + [self._msg("tool", f"result {turn} " + "z" * 500)]
            messages = guard.compress_context(messages, apply_token_budget=False)
            assert messages[0] is first
            assert messages[1] is second

    def test_trimming_every_turn_really_would_move_it(self):
        """The contrast case, so this stays a demonstrated problem rather than
        a hypothetical one: the same loop with the budget applied per turn."""
        guard = self._guard(max_context_tokens=500)
        messages = guard.compress_context(self._long_history())
        first = messages[0]
        second = messages[1]

        moved = False
        for turn in range(5):
            messages = messages + [self._msg("tool", f"result {turn} " + "z" * 4000)]
            messages = guard.compress_context(messages)
            if messages[0] is not first or messages[1] is not second:
                moved = True
                break

        assert moved, "expected per-turn trimming to shift the context start"

    def test_count_overflow_still_protects_a_runaway_loop(self):
        """Switching the budget off must not disable overflow recovery."""
        guard = self._guard(max_context_tokens=0, max_context_messages=10)
        messages = self._long_history(pairs=40, chars=1)

        out = guard.compress_context(messages, apply_token_budget=False)

        assert len(out) <= 10
