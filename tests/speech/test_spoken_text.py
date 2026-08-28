"""Markdown must not be read aloud as punctuation.

Reported from a live reply: a scheduled-tasks table was spoken as "vertical
bar Task vertical bar Schedule vertical bar".
"""

from __future__ import annotations

from openjarvis.speech.spoken_text import to_spoken_text


class TestTables:
    TABLE = (
        "You currently have **4 active scheduled tasks**:\n"
        "\n"
        "| Task | Schedule | Next run |\n"
        "|------|----------|----------|\n"
        "| Sleep reminder | Daily at **10:00 PM** | Aug 28 at 10:00 PM |\n"
        "| Class check | Every **10 minutes** | Aug 28 at 9:11 PM |\n"
        "\n"
        "The scheduler reports times as *Malay Peninsula Standard Time*.\n"
    )

    def test_no_pipes_survive(self):
        assert "|" not in to_spoken_text(self.TABLE)

    def test_the_divider_row_is_dropped(self):
        # "|------|------|" is a long run of "dash" when spoken.
        assert "---" not in to_spoken_text(self.TABLE)

    def test_cells_become_a_readable_sentence(self):
        spoken = to_spoken_text(self.TABLE)
        assert "Sleep reminder, Daily at 10:00 PM, Aug 28 at 10:00 PM." in spoken

    def test_the_surrounding_prose_is_kept(self):
        spoken = to_spoken_text(self.TABLE)
        assert "You currently have 4 active scheduled tasks:" in spoken
        assert "Malay Peninsula Standard Time" in spoken

    def test_a_row_of_only_separators_yields_nothing(self):
        assert to_spoken_text("| --- | --- |") == ""


class TestOtherMarkdown:
    def test_headings_lose_their_hashes(self):
        assert to_spoken_text("## Today's plan") == "Today's plan"

    def test_bullets_lose_their_markers(self):
        spoken = to_spoken_text("- first\n* second\n+ third\n• fourth")
        assert spoken == "first\nsecond\nthird\nfourth"

    def test_emphasis_is_unwrapped(self):
        assert to_spoken_text("**bold** and *italic* and _under_") == (
            "bold and italic and under"
        )

    def test_links_are_read_as_their_text(self):
        assert to_spoken_text("See [the docs](https://example.com/a/b).") == (
            "See the docs."
        )

    def test_images_are_read_as_their_alt_text(self):
        assert to_spoken_text("![a chart](chart.png)") == "a chart"

    def test_inline_code_keeps_its_contents(self):
        assert to_spoken_text("Run `jarvis serve` now.") == "Run jarvis serve now."

    def test_fenced_code_is_not_read_out(self):
        spoken = to_spoken_text("Before\n\n```py\nx = 1\nprint(x)\n```\n\nAfter")
        assert "print" not in spoken
        assert "Before" in spoken and "After" in spoken

    def test_blockquotes_lose_their_markers(self):
        assert to_spoken_text("> quoted line") == "quoted line"

    def test_horizontal_rules_are_dropped(self):
        assert to_spoken_text("one\n\n---\n\ntwo").replace("\n", " ").split() == [
            "one",
            "two",
        ]

    def test_a_bare_asterisk_bullet_is_not_read_as_emphasis(self):
        # "* a *" would otherwise unwrap into "a" and eat the second line.
        assert to_spoken_text("* alpha\n* beta") == "alpha\nbeta"


class TestEdges:
    def test_empty_input(self):
        assert to_spoken_text("") == ""

    def test_plain_prose_is_untouched(self):
        assert to_spoken_text("Good evening, sir.") == "Good evening, sir."

    def test_multiplication_is_not_treated_as_emphasis(self):
        assert to_spoken_text("2 * 3 * 4") == "2 * 3 * 4"
