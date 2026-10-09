"""Budget and numbers nudges (9 October, the user's rules)."""

from __future__ import annotations

import pytest

from openjarvis.prompt.answer_hints import (
    BUDGET_HINT,
    NUMBERS_RULE,
    budget_hint,
    numbers_hint,
)


@pytest.mark.parametrize(
    "text",
    [
        "best value rtx gpu w 16gb vram or more under 50000peso",
        "gaming laptop under ₱60,000",
        "phone below 20k",
        "what can I get for 15000 php",
    ],
)
def test_a_budget_asks_for_local_prices_first(text):
    assert budget_hint(text) == BUDGET_HINT


@pytest.mark.parametrize("text", ["what is a black hole?", "will it rain later?"])
def test_no_budget_no_nudge(text):
    assert budget_hint(text) == ""


@pytest.mark.parametrize(
    "text",
    [
        "how fast is the rtx 5060 ti in tokens/s",
        "so the 4060ti is twice slower than 5060ti?",
        "rtx 5060 vs rx 9060 xt benchmarks",
    ],
)
def test_a_numbers_question_gets_the_measured_or_estimate_rule(text):
    assert numbers_hint(text) == NUMBERS_RULE


def test_the_rule_still_allows_comparing_estimates():
    """The user: with no measured data, still compare -- say they are estimates."""
    assert "compare them anyway" in NUMBERS_RULE
    assert "estimates, not measured data" in NUMBERS_RULE


@pytest.mark.parametrize(
    "text",
    [
        "Who won the most recent Formula 1 race?",
        "what was the score of the Gilas game",
        "who won the election in Japan",
    ],
)
def test_an_event_question_gets_the_one_source_rule(text):
    """9 October: "the Bahrain Grand Prix in Malaysia"."""
    from openjarvis.prompt.answer_hints import EVENTS_RULE, events_hint

    assert events_hint(text) == EVENTS_RULE
    assert "from one source" in EVENTS_RULE


def test_no_event_no_rule():
    from openjarvis.prompt.answer_hints import events_hint

    assert events_hint("how much is the iPhone 17 in the Philippines?") == ""


def test_a_follow_up_uses_the_chats_figures_first():
    """9 October: "is it still possible for Lewis to win?" swapped the chat's
    standings for a months-old page's, with no maths."""
    from openjarvis.prompt.answer_hints import (
        FOLLOW_UP_RULE,
        TITLE_MATH_RULE,
        follow_up_hint,
        title_math_hint,
    )

    question = "Is it still possible for lewis to win?"
    assert follow_up_hint(question, has_history=True) == FOLLOW_UP_RULE
    assert follow_up_hint(question, has_history=False) == ""
    assert title_math_hint(question) == TITLE_MATH_RULE
    assert "25 per Grand Prix, 8 per sprint" in TITLE_MATH_RULE
    assert "never swap numbers silently" in FOLLOW_UP_RULE


def test_the_events_rule_does_not_call_a_real_race_wrong():
    """The "Bahrain Grand Prix in Malaysia" was real (moved to Sepang)."""
    from openjarvis.prompt.answer_hints import EVENTS_RULE

    assert "can be real" in EVENTS_RULE
