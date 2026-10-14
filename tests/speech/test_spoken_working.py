"""Typed arithmetic in a spoken reply (14 October).

The voice was handed "= 101.425 − 2.115 = 99.310 m" raw: every such line ran
away and came out cut off or as a shout. The working is now left to the
screen unless the user asked for a step-by-step solution, and what is still
said is said in words.
"""

from __future__ import annotations

from openjarvis.speech.spoken_math import decimal_words, is_working, speak_plain_math
from openjarvis.speech.spoken_text import (
    WORKING_NOTE,
    SpokenTextStream,
    to_spoken_text,
)

LEVELLING = """Yes, Sir. We use the **height-of-instrument method**. The rule is:

**New elevation = height of instrument − foresight**

And each time the instrument moves, we find its new height from the backsight.

1. **First setup:**
   Height of instrument = benchmark elevation + backsight
   = 100.000 + 1.425 = **101.425 m**
2. **Find TP1's elevation:**
   TP1 elevation = first height of instrument − foresight
   = 101.425 − 2.115 = **99.310 m**
3. **Second setup:**
   Height of instrument = TP1 elevation + its backsight
   = 99.310 + 1.735 = **101.045 m**

**The final point's elevation is 100.060 m.**

A quick check is to add the backsights and subtract the foresights from the benchmark:
100.000 + (1.425 + 1.735) − (2.115 + 0.985) = **100.060 m**.
"""

SUMMARY = [
    "Yes, Sir.",
    "We use the height-of-instrument method.",
    "The rule is:\n\nNew elevation equals height of instrument minus foresight",
    "And each time the instrument moves, we find its new height from the backsight.",
    WORKING_NOTE,
    "The final point's elevation is one hundred point zero six zero meters.",
    "A quick check is to add the backsights and subtract the foresights "
    "from the benchmark.",
]


def _stream(text: str, *, steps: bool = False, size: int = 7) -> list[str]:
    stream = SpokenTextStream(max_pending_chars=20_000, steps=steps)
    out: list[str] = []
    for start in range(0, len(text), size):
        out += stream.push(text[start : start + size])
    return out + stream.finish()


class TestWorkingLines:
    def test_arithmetic_is_working(self):
        assert is_working("   = 100.000 + 1.425 = **101.425 m**")
        assert is_working("100.000 + (1.425 + 1.735) − (2.115 + 0.985) = 100.060 m.")
        assert is_working("2. HI = 99.310 + 1.735")

    def test_a_rule_or_a_result_is_not(self):
        assert not is_working("**New elevation = height of instrument − foresight**")
        assert not is_working("x = 5")
        assert not is_working("The final point's elevation is 100.060 m.")
        assert not is_working("They won 3 - 1 in 2023-2024.")


class TestSaidInWords:
    def test_decimals_whole_then_digit_by_digit(self):
        assert decimal_words("101.425") == "one hundred one point four two five"
        assert decimal_words("100.060") == "one hundred point zero six zero"
        assert decimal_words("1,234.5") == (
            "one thousand two hundred thirty-four point five"
        )

    def test_a_line_of_working(self):
        assert speak_plain_math("= 101.425 − 2.115 = 99.310 m") == (
            "That's one hundred one point four two five minus two point one one"
            " five equals ninety-nine point three one zero meters"
        )

    def test_brackets_are_pauses(self):
        assert speak_plain_math("1.5 + (2 + 3) = 6.5") == (
            "one point five plus, 2 plus 3, equals six point five"
        )

    def test_units_after_a_number(self):
        assert speak_plain_math("a 1 m pole, 2.5 kN and 12 m/s") == (
            "a 1 meter pole, two point five kilonewtons and 12 meters per second"
        )
        # "5m" may be minutes or millions; a word "m" is no unit.
        assert speak_plain_math("5m and I'm here") == "5m and I'm here"

    def test_scores_and_ranges_stay(self):
        assert speak_plain_math("won 3 - 1 in 2023-2024") == "won 3 - 1 in 2023-2024"


class TestSummary:
    def test_the_working_is_left_to_the_screen(self):
        assert _stream(LEVELLING) == SUMMARY

    def test_the_whole_reply_reads_the_same(self):
        spoken = to_spoken_text(LEVELLING)
        assert "point four two five" not in spoken
        assert spoken.count(WORKING_NOTE) == 1
        assert spoken.endswith("subtract the foresights from the benchmark.")

    def test_one_line_of_working_is_the_answer_and_is_said(self):
        assert to_spoken_text("2 × 3 = 6, Sir.") == "2 times 3 equals 6, Sir."
        assert _stream("It is 2.5 + 1.5 = 4.0 m. Anything else?") == [
            "It is two point five plus one point five equals four point zero meters.",
            "Anything else?",
        ]

    def test_working_in_separate_paragraphs_is_left_out_too(self):
        text = (
            "Here it is.\n\nHI = 100 + 1.425 = 101.425\n\n"
            "TP1 = 101.425 - 2.115 = 99.310\n\nSo TP1 is at 99.310 m."
        )
        assert _stream(text) == [
            "Here it is.",
            WORKING_NOTE,
            "So TP1 is at ninety-nine point three one zero meters.",
        ]


class TestStepByStep:
    def test_every_step_is_said_in_words(self):
        spoken = _stream(LEVELLING, steps=True)
        assert WORKING_NOTE not in spoken
        assert (
            "That's one hundred one point four two five minus two point one one"
            " five equals ninety-nine point three one zero meters"
        ) in spoken
        # The step numbers are not read, and never end the line before.
        assert not any(s.rstrip().endswith(("2.", "3.")) for s in spoken)
        assert not any(s.startswith(("1", "2", "3")) for s in spoken)
