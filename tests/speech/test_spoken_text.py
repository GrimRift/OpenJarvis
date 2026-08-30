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
            "See the docs.\n\nThe exact value is visible in chat."
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


class TestAwkwardPrivateValues:
    def test_bare_url_is_referred_to_without_being_read(self):
        url = "https://example.com/reset?token=abc123"
        spoken = to_spoken_text(f"Open {url} to continue.")

        assert url not in spoken
        assert "a link" in spoken
        assert spoken.endswith("The exact value is visible in chat.")

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
        assert spoken.endswith("The exact values are visible in chat.")

    def test_an_inline_file_path_with_spaces_is_fully_omitted(self):
        path = r"C:\Program Files\Sage\user profile.json"
        spoken = to_spoken_text(f"Open `{path}` now.")

        assert path not in spoken
        assert spoken == (
            "Open a file path now.\n\nThe exact value is visible in chat."
        )

    def test_authentication_code_is_never_spoken(self):
        code = "739204"
        spoken = to_spoken_text(f"Your verification code is {code}.")

        assert code not in spoken
        assert "verification code is the authentication code" in spoken
        assert spoken.endswith("The exact value is visible in chat.")

    def test_long_identifiers_are_never_spoken(self):
        uuid = "123e4567-e89b-12d3-a456-426614174000"
        token = "sk_live_51N8LongIdentifier90876"
        spoken = to_spoken_text(f"Use session {uuid} and token {token}.")

        assert uuid not in spoken
        assert token not in spoken
        assert spoken.count("an identifier") == 2
        assert spoken.endswith("The exact values are visible in chat.")

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
