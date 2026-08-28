"""Safety tests for EagerEndOfTurn speculation.

EagerEndOfTurn is a guess that TurnResumed can retract, so speculative work
must stay invisible and reversible. These assert the boundary directly: no
tool can be reached, nothing escapes before confirmation, and a retracted
guess leaves nothing behind.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from unittest.mock import MagicMock

from openjarvis.speech import speculative
from openjarvis.speech.speculative import (
    SpeculativeManager,
    generate_speculative,
    looks_tool_capable,
)


class TestToolBoundaryIsStructural:
    """The boundary is an absence of tool machinery, not an instruction."""

    def test_engine_is_called_with_no_tools_argument(self):
        engine = MagicMock()
        engine.generate.return_value = {"content": "Paris."}

        generate_speculative(engine, model="m", transcript="capital of France?")

        kwargs = engine.generate.call_args.kwargs
        assert "tools" not in kwargs, "a tools= argument would make invocation possible"

    def test_nothing_in_the_call_path_names_tool_machinery(self):
        """Checked against the parsed tree, not the text.

        A source grep would match the docstring explaining that there is no
        executor, so assert on identifiers the code actually evaluates.
        """
        tree = ast.parse(textwrap.dedent(inspect.getsource(generate_speculative)))
        names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

        forbidden_names = (
            "ToolExecutor",
            "tool_executor",
            "AgentRegistry",
            "run_agent",
        )
        for forbidden in forbidden_names:
            assert forbidden not in names

    def test_generate_is_the_only_engine_call(self):
        """An agent or executor call would be a second, tool-capable path."""
        tree = ast.parse(textwrap.dedent(inspect.getsource(generate_speculative)))
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert called <= {"generate", "extend", "append"}, called

    def test_module_never_imports_tool_machinery(self):
        """No import of the tools package anywhere in the module."""
        tree = ast.parse(inspect.getsource(speculative))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)

        assert not any(m.startswith("openjarvis.tools") for m in imported), imported
        assert not any(m.startswith("openjarvis.agents") for m in imported), imported

    def test_a_tool_shaped_request_is_never_speculated_on(self):
        mgr = SpeculativeManager()
        assert mgr.begin(1, "play a song on Spotify") is None
        assert mgr.current is None


class TestNothingEscapesBeforeConfirmation:
    def test_buffered_text_is_not_readable_through_release_until_confirmed(self):
        mgr = SpeculativeManager()
        spec = mgr.begin(1, "what is the capital of France")
        mgr.append(spec.generation_id, "Paris.")

        # A different turn must not unlock it.
        assert mgr.release(2, "what is the capital of France") is None

    def test_release_requires_the_transcript_actually_confirmed(self):
        mgr = SpeculativeManager()
        spec = mgr.begin(1, "what is the capital of France")
        mgr.append(spec.generation_id, "Paris.")

        assert mgr.release(1, "what is the capital of Germany") is None

    def test_release_tolerates_only_punctuation_and_case_changes(self):
        """Flux finalises by adding a full stop; that is the same utterance."""
        mgr = SpeculativeManager()
        spec = mgr.begin(1, "what is the capital of France")
        mgr.append(spec.generation_id, "Paris.")

        assert mgr.release(1, "What is the capital of France?") == "Paris."

    def test_exactly_one_answer_per_confirmed_turn(self):
        mgr = SpeculativeManager()
        spec = mgr.begin(1, "what is the capital of France")
        mgr.append(spec.generation_id, "Paris.")

        assert mgr.release(1, "what is the capital of France") == "Paris."
        assert mgr.release(1, "what is the capital of France") is None

    def test_a_transcript_that_becomes_tool_shaped_is_discarded(self):
        """The confirmed transcript can differ from the eager one."""
        mgr = SpeculativeManager()
        spec = mgr.begin(1, "what is the capital of France")
        mgr.append(spec.generation_id, "Paris.")

        assert mgr.release(1, "what is the capital of France, play a song") is None


class TestTurnResumedCancelsStaleWork:
    def test_cancel_discards_the_speculation(self):
        mgr = SpeculativeManager()
        spec = mgr.begin(1, "what is the capital of France")
        mgr.append(spec.generation_id, "Par")

        cancelled_id = mgr.cancel("TurnResumed")

        assert cancelled_id == spec.generation_id
        assert mgr.current is None
        assert mgr.release(1, "what is the capital of France") is None

    def test_tokens_from_a_cancelled_generation_are_ignored(self):
        """In-flight callbacks keep arriving after cancellation."""
        mgr = SpeculativeManager()
        spec = mgr.begin(1, "what is the capital of France")
        mgr.cancel("TurnResumed")

        mgr.append(spec.generation_id, "late tokens")

        assert mgr.current is None
        assert spec.text == ""

    def test_a_superseded_generation_stops_accumulating(self):
        mgr = SpeculativeManager()
        first = mgr.begin(1, "what is the capital of France")
        second = mgr.begin(1, "what is the capital of Spain")

        mgr.append(first.generation_id, "Paris.")
        mgr.append(second.generation_id, "Madrid.")

        assert first.text == ""
        assert second.text == "Madrid."

    def test_is_current_rejects_stale_ids(self):
        mgr = SpeculativeManager()
        spec = mgr.begin(1, "what is the capital of France")
        assert mgr.is_current(spec.generation_id)

        mgr.cancel()
        assert not mgr.is_current(spec.generation_id)

    def test_reset_clears_everything(self):
        mgr = SpeculativeManager()
        spec = mgr.begin(1, "what is the capital of France")
        mgr.append(spec.generation_id, "Paris.")
        mgr.release(1, "what is the capital of France")

        mgr.reset()

        # The released-turn guard is cleared too, so a new session starts fresh.
        again = mgr.begin(1, "what is the capital of France")
        assert again is not None


class TestToolShapeDetection:
    """Biased toward discarding: a wasted speculation only costs latency."""

    def test_plain_questions_are_speculatable(self):
        for text in (
            "what is the capital of France",
            "who are you",
            "how tall is Everest",
            "why is the sky blue",
        ):
            assert not looks_tool_capable(text), text

    def test_action_requests_are_not(self):
        for text in (
            "play a song",
            "remind me at 8",
            "schedule a task for tomorrow",
            "send a message to Mark",
            "delete that email",
            "open Obsidian",
            "what tasks do I have scheduled",
        ):
            assert looks_tool_capable(text), text

    def test_empty_input_is_treated_as_unsafe(self):
        assert looks_tool_capable("")
        assert looks_tool_capable("   ")
