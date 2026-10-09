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
