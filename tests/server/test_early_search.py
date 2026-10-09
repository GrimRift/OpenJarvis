"""Which messages start a web search before the model asks (9 October)."""

from __future__ import annotations

import pytest

from openjarvis.agents.early_search import early_search_query


@pytest.mark.parametrize(
    "text",
    [
        "What's the US dollar to peso exchange rate today?",
        "When is the next public holiday in the Philippines?",
        "Who won the most recent Formula 1 race?",
        "How much is the iPhone 17 in the Philippines right now?",
        "airpods 5 vs airpods 4?",
    ],
)
def test_a_public_lookup_is_searched_at_once(text):
    assert early_search_query(text, has_history=False) == text


@pytest.mark.parametrize(
    "text",
    [
        "Do I have any class today?",
        "What's on my calendar tomorrow?",
        "Play some music",
        "What's the weather today?",
        "Remind me at 5 to call mom",
        "Tell me something interesting",
        "Good morning.",
    ],
)
def test_the_users_own_things_and_actions_are_not(text):
    assert early_search_query(text, has_history=False) == ""


def test_a_follow_up_needs_the_chat_so_it_waits_for_the_model():
    text = "How much is that one now?"
    assert early_search_query(text, has_history=True) == ""
    assert early_search_query(text, has_history=False) == text


def test_a_long_message_is_left_to_the_model():
    text = "What is the price " + "and also the details " * 10 + "today?"
    assert early_search_query(text, has_history=False) == ""
