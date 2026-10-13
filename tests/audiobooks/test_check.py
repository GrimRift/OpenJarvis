"""Judging a rendered piece: real voice failures flagged, recogniser slips not."""

from __future__ import annotations

import pytest

from openjarvis.audiobooks.check import DEFAULT_RATE, chapter_rate, judge

RATE = DEFAULT_RATE


def _secs(text: str, factor: float = 1.0) -> float:
    return len(text) * RATE * factor


@pytest.mark.parametrize(
    "said, heard",
    [
        # 10-13 benchmark: names and quotes are the recogniser's, not the voice's
        ("Seo Gio was an art teacher.", "COGO was an art teacher."),
        ("'I'm freed from food expenses.'", "I'm freed from food expenses."),
        ("The fruit is 1cm to 2cm wide.", "The fruit is 1 cm to 2 cm wide."),
        ("It feels like fibre,", "It feels like fiber."),
        ("He said it very, very quietly.", "He said it very, very quietly."),
    ],
)
def test_recogniser_slips_pass(said, heard):
    assert judge(said, heard, _secs(said), RATE) == []


@pytest.mark.parametrize(
    "said, heard",
    [
        # what Turbo did on 10-13 with lines under 14 characters
        ("It's nice.", "It's nice. It's nice."),
        ("Squeak.", "Squeak. Squeak."),
        ("It's small, it's small.", "It's small, it's small. It's small."),
    ],
)
def test_a_line_said_twice_is_flagged(said, heard):
    assert any(r.startswith("repeated") for r in judge(said, heard, _secs(said), RATE))


@pytest.mark.parametrize(
    "said, heard",
    [
        # 2026-10-14: Turbo (Kurzgesagt narrator) spelled the name out
        ("Seo Gio was an art teacher.", "S-E-O Gio was an art teacher."),
        ("Seo Gio was an art teacher.", "S.E.O.G.O. was an art teacher."),
    ],
)
def test_a_name_spelled_out_is_flagged(said, heard):
    reasons = judge(said, heard, _secs(said), RATE)
    assert any(r.startswith("spelled out") for r in reasons)


@pytest.mark.parametrize(
    "said, heard",
    [
        ("He kept a plan B and a plan C.", "He kept a plan B and a plan C."),
        ("The letters were A, B and C.", "The letters were A, B and C."),
        ("Seo Gio was an art teacher.", "C.O.G.O was an art teacher."),
        ("I said I would.", "I, I said I would."),
    ],
)
def test_letters_that_spell_nothing_in_the_text_pass(said, heard):
    reasons = judge(said, heard, _secs(said), RATE)
    assert not any(r.startswith("spelled out") for r in reasons)


def test_near_silent_and_runaway_pieces_are_flagged_by_length():
    said = "The well-ripened apple emitted a pleasant fragrance."
    assert "too short" in judge(said, "", _secs(said, 0.2), RATE)
    assert "too long" in judge(said, None, _secs(said, 4.0), RATE)


def test_garbled_piece_is_flagged():
    said = "The light and soft spring breeze carried the smell of paint."
    assert "garbled" in judge(said, "Uh huh my", _secs(said), RATE)


def test_no_transcriber_means_length_checks_only():
    assert judge("A perfectly normal sentence here.", None, _secs("x" * 33), RATE) == []


def test_chapter_rate_needs_enough_pieces():
    assert chapter_rate([("x" * 30, 3.0)] * 3) == DEFAULT_RATE
    assert chapter_rate([("x" * 30, 3.0)] * 12) == pytest.approx(0.1)
