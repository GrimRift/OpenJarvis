"""A reply that ran out of tokens, joined to the rest of it (23 September:
"despiteSir, continuing from..." and "The radiative## 5.3 The tachocline")."""

from __future__ import annotations

from openjarvis.server.continuation import seam


def test_an_announcement_of_continuing_is_dropped_and_the_words_join():
    before = "early Earth had liquid water despite"
    after = (
        "Sir, continuing from the Sun's formation and the faint-young-Sun "
        "problem:\n\na fainter Sun."
    )
    assert (
        before + seam(before, after)
        == "early Earth had liquid water despite a fainter Sun."
    )


def test_a_section_written_again_from_its_heading_is_not_repeated():
    before = (
        "## 5.3 The tachocline\n\nNear the boundary between the radiative and "
        "convective zones is a thin region called the tachocline.\n\nThe radiative"
    )
    after = (
        "## 5.3 The tachocline\n\nNear the boundary between the radiative and "
        "convective zones is a thin region called the tachocline.\n\nThe radiative "
        "interior rotates more like a solid body."
    )
    assert seam(before, after) == " interior rotates more like a solid body."


def test_repeated_last_words_are_dropped():
    before = "It is powered by nuclear fusion in the core"
    after = "fusion in the core, where hydrogen becomes helium."
    assert seam(before, after) == ", where hydrogen becomes helium."


def test_a_clean_continuation_is_left_alone():
    before = "The Sun formed about 4.6 billion years ago."
    after = "\n\n## 3. Energy\n\nIt shines because of fusion."
    assert seam(before, after) == after
