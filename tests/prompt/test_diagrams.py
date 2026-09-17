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
    def test_every_mode_names_the_block_and_all_three_shapes(self, mode):
        text = instruction(mode)
        assert LANGUAGE in text
        for shape in ("flow", "parts", "comparison"):
            assert f"`{shape}`" in text

    def test_it_forbids_drawing_the_same_thing_in_text(self):
        """The diagram replaces the ASCII sketch; printing both is the
        duplication this feature exists to remove."""
        assert "text arrows, pipes or ASCII boxes" in instruction(AUTOMATIC)

    def test_the_example_it_shows_is_valid_json(self):
        """A malformed example teaches the model to send malformed JSON."""
        text = instruction(AUTOMATIC)
        block = text.split(f"```{LANGUAGE}", 1)[1].split("```", 1)[0]
        parsed = json.loads(block)
        assert parsed["shape"] == "flow"
        assert len(parsed["nodes"]) >= 2

    def test_colour_is_rationed(self):
        assert "at most two nodes" in instruction(AUTOMATIC)
