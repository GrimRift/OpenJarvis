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
        assert "Sleep reminder: Daily at 10:00 PM, Aug 28 at 10:00 PM." in spoken

    def test_the_surrounding_prose_is_kept(self):
        spoken = to_spoken_text(self.TABLE)
        assert "You currently have 4 active scheduled tasks:" in spoken
        assert "Malay Peninsula Standard Time" in spoken

    def test_a_row_of_only_separators_yields_nothing(self):
        assert to_spoken_text("| --- | --- |") == ""


def _study_table(rows: int) -> str:
    """The calculator-shortcut table from the 7 October chat, cut to *rows*."""
    body = [
        "| 1 | Arithmetic sequence term | **STAT → Lin**: "
        "enter term numbers as x, use ŷ at the target. |",
        "| 2, 24 | Sum of a sequence | **Σ**: "
        "enter the term expression, start, and end. |",
        "| 3 | Geometric sequence term | **STAT → ab EXP**: "
        "enter term numbers and term values. |",
        "| 4, 21, 84 | Simultaneous linear equations | **EQN → 2 unknowns**: "
        "enter the coefficients. |",
        "| 22, 83, 88 | Combinations | Use **nCr**. |",
        "| 45 | Degrees-minutes-seconds | Use the ° ' ” conversion key. |",
        "| 51, 58, 69 | Polynomial roots | Use **EQN → Quad/Cubic**. |",
    ][:rows]
    return "\n".join(
        ["Here is the list.", "", "| Problem(s) | Topic | Shortcut |", "|---|---|---|"]
        + body
        + ["", "Next, the formulas."]
    )


class TestSpokenTables:
    """Reported 7 October: a study table was read cell by cell, left to right,
    and the user could not follow it by ear."""

    def test_a_short_table_is_one_sentence_per_row(self):
        spoken = to_spoken_text(_study_table(3))
        assert (
            "Problem 1: Arithmetic sequence term. STAT, then Lin: enter term "
            "numbers as x, use y-hat at the target." in spoken
        )
        assert "Problems 2 and 24: Sum of a sequence. sigma: enter" in spoken

    def test_a_number_list_is_said_as_a_list(self):
        spoken = to_spoken_text(_study_table(4))
        assert "Problems 4, 21 and 84: Simultaneous linear equations." in spoken

    def test_a_long_table_is_described_not_read(self):
        spoken = to_spoken_text(_study_table(7))
        assert (
            "The table on screen has 7 rows, covering Arithmetic sequence term, "
            "Sum of a sequence, Geometric sequence term and 4 more." in spoken
        )
        assert "Combinations" not in spoken
        assert spoken.startswith("Here is the list.")
        assert spoken.endswith("Next, the formulas.")

    def test_a_table_without_a_header_still_reads(self):
        assert to_spoken_text("| Speed | 50 km/h |") == "Speed: 50 km/h."


class TestSpokenSymbols:
    def test_calculator_notation_is_said_as_words(self):
        assert to_spoken_text("Press **STAT → Lin**, then read ŷ.") == (
            "Press STAT, then Lin, then read y-hat."
        )
        assert to_spoken_text("Use nCr or nPr.") == "Use n C r or n P r."
        assert to_spoken_text("Use the ° ' ” key.") == (
            "Use the degrees-minutes-seconds key."
        )

    def test_operators_are_said_as_words(self):
        assert to_spoken_text("2 × 3 ≈ 6 ± 1") == "2 times 3 about 6 plus or minus 1"


class TestOtherMarkdown:
    def test_headings_lose_their_hashes(self):
        assert to_spoken_text("## Today's plan") == "Today's plan"

    def test_bullets_lose_their_markers(self):
        spoken = to_spoken_text("- first\n* second\n+ third\n• fourth")
        # Each item ends with a stop: without one the voice ran a whole
        # list together as a sentence, pausing wherever it breathed.
        assert spoken == "first.\nsecond.\nthird.\nfourth"

    def test_emphasis_is_unwrapped(self):
        assert to_spoken_text("**bold** and *italic* and _under_") == (
            "bold and italic and under"
        )

    def test_links_are_read_as_their_text(self):
        assert to_spoken_text("See [the docs](https://example.com/a/b).") == (
            "See the docs."
        )

    def test_many_labeled_links_get_one_source_notice(self):
        text = " ".join(
            f"[Source {index}](https://example.com/{index})." for index in range(1, 4)
        )
        spoken = to_spoken_text(text)
        assert "in chat" not in spoken
        assert "exact value" not in spoken

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
            "one.",
            "two",
        ]

    def test_a_bare_asterisk_bullet_is_not_read_as_emphasis(self):
        # "* a *" would otherwise unwrap into "a" and eat the second line.
        assert to_spoken_text("* alpha\n* beta") == "alpha.\nbeta"


class TestLineEnds:
    def test_a_line_that_ends_on_a_word_gets_a_stop(self):
        # Heard 22 September: "COA audit findings and unresolved notices
        # procurement and infrastructure risks" as one breathless sentence.
        spoken = to_spoken_text(
            "A report should cover:\n- COA audit findings\n- Procurement risks\n\nSo."
        )
        assert (
            spoken
            == "A report should cover:\nCOA audit findings.\nProcurement risks.\n\nSo."
        )

    def test_a_line_already_ended_is_left_alone(self):
        assert (
            to_spoken_text("First, sir:\nsecond?\nthird")
            == "First, sir:\nsecond?\nthird"
        )


class TestEdges:
    def test_empty_input(self):
        assert to_spoken_text("") == ""

    def test_plain_prose_is_untouched(self):
        assert to_spoken_text("Good evening, sir.") == "Good evening, sir."

    def test_multiplication_is_not_treated_as_emphasis(self):
        assert to_spoken_text("2 * 3 * 4") == "2 * 3 * 4"


class TestAwkwardPrivateValues:
    def test_bare_url_is_referred_to_without_being_read(self):
        url = "https://example.com/reset?token=abc123"
        spoken = to_spoken_text(f"Open {url} to continue.")

        assert url not in spoken
        assert "a link" in spoken
        assert "in chat" not in spoken

    def test_file_paths_are_referred_to_without_being_read(self):
        windows_path = r"C:\AI\OpenJarvis-Lab\src\openjarvis\server\api_routes.py"
        posix_path = "/home/grim/sage/config.toml"
        spoken = to_spoken_text(
            f"The Windows file is `{windows_path}` and the Linux file is "
            f"`{posix_path}`."
        )

        assert windows_path not in spoken
        assert posix_path not in spoken
        assert spoken.count("a file path") == 2
        assert "in chat" not in spoken

    def test_an_inline_file_path_with_spaces_is_fully_omitted(self):
        path = r"C:\Program Files\Sage\user profile.json"
        spoken = to_spoken_text(f"Open `{path}` now.")

        assert path not in spoken
        assert spoken == "Open a file path now."

    def test_authentication_code_is_never_spoken(self):
        code = "739204"
        spoken = to_spoken_text(f"Your verification code is {code}.")

        assert code not in spoken
        assert "verification code is the authentication code" in spoken
        assert "in chat" not in spoken

    def test_long_identifiers_are_never_spoken(self):
        uuid = "123e4567-e89b-12d3-a456-426614174000"
        token = "sk_live_51N8LongIdentifier90876"
        spoken = to_spoken_text(f"Use session {uuid} and token {token}.")

        assert uuid not in spoken
        assert token not in spoken
        assert spoken.count("an identifier") == 2
        assert "in chat" not in spoken

    def test_a_long_numeric_id_is_never_spoken(self):
        identifier = "73018492017462"
        spoken = to_spoken_text(f"The account ID is {identifier}.")

        assert identifier not in spoken
        assert "an identifier" in spoken

    def test_ordinary_numbers_and_short_ids_are_preserved(self):
        text = "Version 2.4.1 is ready; order ID 12345 remains valid."
        assert to_spoken_text(text) == text

    def test_a_long_plain_word_is_not_mistaken_for_an_identifier(self):
        text = "a" * 5001
        assert to_spoken_text(text) == text


class TestDashesAreHeardAsPauses:
    """A dash is punctuation to the eye and a hazard to the ear.

    From a real reply on 18 September: "Project Management–Drafting" and
    "relax—preferably". Read aloud, an en dash between words gives no gap at
    all, so the two words run together as one.
    """

    def test_a_dash_between_words_becomes_a_pause(self):
        assert to_spoken_text("Project Management–Drafting") == (
            "Project Management, Drafting"
        )
        assert to_spoken_text("relax—preferably somewhere dry") == (
            "relax, preferably somewhere dry"
        )
        assert to_spoken_text("wait — what?") == "wait, what?"

    def test_a_range_of_numbers_is_left_alone(self):
        # "2023, 2024" would be heard as two separate years.
        assert to_spoken_text("the 2023-2024 term") == "the 2023-2024 term"
        assert to_spoken_text("pages 10–12") == "pages 10–12"

    def test_the_whole_reply_reads_cleanly(self):
        spoken = to_spoken_text(
            "What's up, Sir? You've got one urgent item: **Account Activation "
            "for Construction Methods and Project Management–Drafting**, due "
            "**today at 11:59 PM**."
        )
        assert "**" not in spoken
        assert "–" not in spoken and "—" not in spoken
        assert "Management, Drafting" in spoken
        # And nothing was duplicated on the way through.
        assert spoken.lower().count("sir") == 1
