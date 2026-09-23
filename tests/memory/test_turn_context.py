"""Per-turn context rides its own message (23 September).

The provider serves a prompt from its cache only when the system prompt is
identical to the last one; the clock and the memory chosen per question
changed it on every turn.
"""

from __future__ import annotations

from openjarvis.core.types import Message, Role
from openjarvis.prompt.builder import SystemPromptBuilder
from openjarvis.tools.storage.context import TURN_CONTEXT_HEADER, add_turn_context


def test_the_stable_prompt_leaves_the_clock_out_and_hands_it_over() -> None:
    builder = SystemPromptBuilder(agent_template="You are Sage.")
    stable = builder.build(include_volatile=False)
    assert "Current Date and Time" not in stable
    assert "Reminder: today is really" not in stable
    assert "Current Date and Time" in builder.volatile_text()
    assert stable == builder.build(include_volatile=False)
    assert "Current Date and Time" in builder.build()


def test_context_goes_just_before_the_latest_user_message() -> None:
    messages = [
        Message(role=Role.SYSTEM, content="You are Sage."),
        Message(role=Role.USER, content="Hi"),
        Message(role=Role.ASSISTANT, content="Hello, Sir."),
        Message(role=Role.USER, content="What time is it?"),
    ]
    out = add_turn_context(messages, "It is 7:10 PM.")
    out = add_turn_context(out, "The user likes tea.")
    roles = [Role.SYSTEM, Role.USER, Role.ASSISTANT, Role.USER, Role.USER]
    assert [m.role for m in out] == roles
    context = out[3]
    assert context.content.startswith(TURN_CONTEXT_HEADER)
    assert "7:10 PM" in context.content and "likes tea" in context.content
    assert out[4].content == "What time is it?"
    assert out[0] is messages[0]
    assert len(messages) == 4


def test_nothing_to_add_adds_nothing() -> None:
    messages = [Message(role=Role.USER, content="Hi")]
    assert add_turn_context(messages, "  ") == messages
