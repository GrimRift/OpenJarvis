"""Sage draws a diagram only when the user has left it switched on."""

from __future__ import annotations

import json

import pytest

from openjarvis.prompt.diagrams import (
    AUTOMATIC,
    LANGUAGE,
    OFF,
    ON_REQUEST,
    instruction,
    turn_hint,
)


class TestTheModeDecidesWhatIsAsked:
    def test_off_says_nothing_at_all(self):
        """Switched off means the instruction never reaches the prompt, so no
        tokens are spent describing a feature the user turned off."""
        assert instruction(OFF) == ""
        assert instruction("nonsense") == ""

    def test_automatic_lets_sage_choose(self):
        text = instruction(AUTOMATIC)
        assert "whenever the answer is a process" in text
        assert "Never draw one unasked" not in text

    def test_on_request_waits_to_be_asked(self):
        text = instruction(ON_REQUEST)
        assert "Never draw one unasked" in text
        assert "show me how" in text

    @pytest.mark.parametrize("mode", [AUTOMATIC, ON_REQUEST])
    def test_every_mode_names_the_block_and_both_diagram_shapes(self, mode):
        text = instruction(mode)
        assert LANGUAGE in text
        for shape in ("flow", "parts"):
            assert f"`{shape}`" in text
        # Comparisons are a table the screen draws as a grid (9 October).
        assert "EMPTY top-left cell" in text

    def test_it_forbids_drawing_the_same_thing_in_text(self):
        """The diagram replaces the ASCII sketch; printing both is the
        duplication this feature exists to remove."""
        assert "text arrows or ASCII-art boxes" in instruction(AUTOMATIC)

    def test_the_example_it_shows_is_valid_json(self):
        """A malformed example teaches the model to send malformed JSON."""
        text = instruction(AUTOMATIC)
        block = text.split(f"```{LANGUAGE}", 1)[1].split("```", 1)[0]
        parsed = json.loads(block)
        assert parsed["shape"] == "flow"
        assert len(parsed["nodes"]) >= 2

    def test_colour_is_rationed(self):
        assert "at most two nodes" in instruction(AUTOMATIC)


class TestComparisonsGetAGrid:
    """9 October: "airpods 5 vs airpods 4?" got a markdown table with
    diagrams on Automatic."""

    @pytest.mark.parametrize(
        "question",
        [
            "airpods 5 vs airpods 4?",
            "Mazda3 versus Civic",
            "compare the S24 and the Pixel 9",
            "what's the difference between ANC and transparency",
            "which one is better, the Kindle or the Kobo?",
        ],
    )
    def test_a_comparison_gets_the_hint(self, question):
        hint = turn_hint(AUTOMATIC, question)
        assert "markdown table with an empty top-left cell" in hint
        assert "draws it as a grid" in hint

    @pytest.mark.parametrize(
        "question", ["what's the weather", "play some music", "how does a CPU work"]
    )
    def test_other_questions_do_not(self, question):
        assert turn_hint(AUTOMATIC, question) == ""

    @pytest.mark.parametrize("mode", [ON_REQUEST, OFF])
    def test_only_on_automatic(self, mode):
        assert turn_hint(mode, "airpods 5 vs airpods 4?") == ""

    def test_nothing_tells_the_model_not_to_write_a_table(self):
        """9 October: "do NOT also write a markdown table" made the model argue
        with itself in the reply ("Wait no diagram required JSON comparison.
        Must comply...") and never finish. Say what to write instead."""
        text = instruction(AUTOMATIC) + turn_hint(AUTOMATIC, "a vs b?")
        assert "do NOT also write a markdown table" not in text
        assert "INSTEAD of a markdown table" not in text
        assert '"shape": "comparison"' not in text
