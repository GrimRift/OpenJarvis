"""Long codes are spoken as their last characters, and spelled words get
their real time in the voice's budget."""

from __future__ import annotations

import pytest

from openjarvis.speech.spoken_text import to_spoken_text


@pytest.mark.parametrize(
    "written, spoken",
    [
        (
            "**Shopee:** Order `#260920HXVD7SX` needs confirmation"
            " by **September 25**.",
            "Shopee: Order ending in 7 S X needs confirmation by September 25.",
        ),
        (
            "Tracking number: SPXPH0123456789 is out for delivery.",
            "Tracking number ending in 7 8 9 is out for delivery.",
        ),
        ("Your code 4829103756 arrived.", "Your code ending in 7 5 6 arrived."),
        ("It came as 29481037561.", "It came as a code ending in 5 6 1."),
    ],
)
def test_a_long_code_is_spoken_as_its_last_three_characters(written, spoken):
    assert to_spoken_text(written) == spoken


@pytest.mark.parametrize(
    "text",
    [
        "John mentioned CEITGD11D - P4.",
        "Generated at 5:01 AM on 2026-09-25.",
        "Commit a1b2c3d4 landed.",
        "It is 25 degrees with a 47% chance of rain.",
    ],
)
def test_short_codes_dates_and_numbers_are_left_alone(text):
    assert to_spoken_text(text) == text


def test_a_spelled_code_gets_its_real_time_so_the_line_is_not_cut():
    engine = pytest.importorskip("voice_sidecar.engine")
    plain = "Order ending in S X needs confirmation by September twenty five."
    coded = "Order 260920HXVD7SX needs confirmation by September twenty five."
    per_second = engine.SAMPLE_RATE
    # Thirteen characters said one by one take ~5 s more than as a word.
    assert engine._budget_samples(coded) / per_second > (
        engine._budget_samples(plain) / per_second + 4
    )
    # Ordinary prose keeps the measured 90 ms a character.
    assert engine._budget_samples("Good evening, Sir.") == int(
        per_second * (len("Good evening, Sir.") * 0.09 + 1.0)
    )
