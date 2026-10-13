"""Chapter text -> pieces and pauses, by the render rules from the 10-13 runs."""

from __future__ import annotations

from openjarvis.audiobooks.pieces import (
    GAPS,
    HARD_MAX,
    MERGE_UNDER,
    SPLIT_OVER,
    announcement,
    build,
)


def _said(items):
    return [i.text for i in items if i.text is not None]


def test_title_first_then_paragraph_gap():
    items = build(
        "The river ran past the mill.\n\nIt was cold that day.\n", "Chapter 1."
    )
    assert _said(items) == [
        "Chapter 1.",
        "The river ran past the mill.",
        "It was cold that day.",
    ]
    assert items[0].gap == GAPS["title"]
    assert items[1].gap == GAPS["paragraph"]


def test_scene_break_and_ellipsis_lines_are_pauses_never_said():
    text = 'He waited.\n\n"..."\n\n* * *\n\nMorning came.'
    items = build(text, "")
    assert _said(items) == ["He waited.", "Morning came."]
    pauses = [i.gap for i in items if i.text is None]
    assert pauses == [GAPS["beat"], GAPS["scene"]]


def test_short_sentences_merge_up_to_the_live_piece_size():
    para = "He stopped. He looked. The door was open and the hall was dark beyond it."
    said = _said(build(para, ""))
    assert said == [para]
    assert len(para) <= SPLIT_OVER


def test_long_sentences_split_at_clauses_and_nothing_passes_the_hard_cap():
    long = (
        ", ".join(["the painter mixed another colour on the old wooden board"] * 6)
        + "."
    )
    words = " ".join(["word"] * 80) + "."  # no commas at all
    said = _said(build(f"{long}\n\n{words}", ""))
    assert len(said) > 3
    assert max(len(s) for s in said) <= HARD_MAX
    assert " ".join(said).replace("  ", " ").count("painter") == 6


def test_short_pieces_are_merged_with_a_neighbour():
    text = '"Yes."\n\nShe nodded and turned toward the window.\n\nHmm.'
    said = _said(build(text, "Chapter 2."))
    assert said == [
        "Chapter 2.",
        '"Yes." She nodded and turned toward the window. Hmm.',
    ]
    assert all(len(s) >= MERGE_UNDER for s in said[1:])


def test_announcement_uses_label_and_title():
    assert announcement({"label": "Chapter 1", "title": "Chapter 1"}) == "Chapter 1."
    # EPUB labels carry the title already: said once
    assert announcement({"label": "Chapter 7: Crimson", "title": "Crimson"}) == (
        "Chapter 7: Crimson."
    )
    assert (
        announcement({"label": "Chapter 7", "title": "Crimson"})
        == "Chapter 7: Crimson."
    )
    assert announcement({"label": "Prologue", "title": "Prologue"}) == "Prologue."
