"""Paragraph cleaning for TTS, on made-up text shaped like the reference book's."""

from __future__ import annotations

import pytest

from openjarvis.audiobooks.text import (
    SCENE_BREAK,
    clean_paragraph,
    is_scene_break,
    note_text,
    word_count,
)


def test_curly_quotes_and_ellipses_become_plain():
    raw = "\u201cWait\u2026\u201d she said. \u2018Fine\u2019 \u201c\u2026\u2026.\u201d"
    assert clean_paragraph(raw) == '"Wait..." she said. \'Fine\' "..."'


@pytest.mark.parametrize(
    "dots", ["......", ".......", "\u2026\u2026", "\u2026\u2026.", ". . ."]
)
def test_every_ellipsis_shape_becomes_three_dots(dots):
    assert clean_paragraph(f"\u201c{dots}\u201d") == '"..."'


def test_an_ellipsis_only_line_is_kept_as_a_beat():
    assert clean_paragraph("\u2026\u2026") == "..."


def test_footnote_markers_are_dropped_including_superscript_four():
    assert clean_paragraph("The kiln\u00b9 was warm\u2074.") == "The kiln was warm."


def test_download_site_watermark_is_removed():
    assert clean_paragraph("The end.OceanofPDF.com") == "The end."
    assert clean_paragraph("_OceanofPDF.com_") == ""


@pytest.mark.parametrize(
    "tag",
    [
        # Cyrillic o/e/i/h look-alikes, as in the reference book
        "N\u043ev\u0435l\u0456g\u04bbt",
        # math-alphabet bold
        "\U0001d40d\U0001d428\U0001d42f\U0001d41e\U0001d425\U0001d422\U0001d420\U0001d421\U0001d42d",
        # spaced letters with math italics and a bullet wrapper
        "\u00b0\u2022 N \U0001d45c v \U0001d452 l i g h t \u2022\u00b0",
        # bullets between letters inside braces
        "{N\u2022o\u2022v\u2022e\u2022l\u2022i\u2022g\u2022h\u2022t}",
        # capital I for l, with arrows and the "read more" aside
        "\u27a4 N\u043ev\u0435 \u2160ight \u27a4 (Read more on our source)",
        # Greek iota
        "~N\u043ev\u0435l\u03b9ght~",
    ],
)
def test_web_novel_watermark_variants_are_removed(tag):
    assert (
        clean_paragraph(f"He smiled {tag} at the painting.")
        == "He smiled at the painting."
    )


@pytest.mark.parametrize(
    "aside",
    [
        "(Exclusive on Somewhere)",
        "(Continue reading)",
        "(Original source)",
        "(Don\u2019t copy, read here)",
        "(Read the full story)",
        "(Only on Somewhere)",
    ],
)
def test_spam_asides_are_removed(aside):
    assert clean_paragraph(f"She nodded {aside} slowly.") == "She nodded slowly."


def test_look_alike_letters_in_a_real_name_are_folded():
    # a name typed with a Cyrillic capital En
    assert clean_paragraph("Mi-\u041dee waved.") == "Mi-Hee waved."


def test_gibberish_and_kaomoji_go_but_the_closing_quote_stays():
    assert (
        clean_paragraph("She hummed. \U0001d445 \u2115 \u03b2\u0401\u1e60 Then left.")
        == "She hummed. Then left."
    )
    assert clean_paragraph("\u201cYay \u1fa5\u00b4)o\u201d") == '"Yay"'


def test_symbols_emoji_and_urls_are_removed_but_degrees_kept():
    raw = (
        "\u21b3 Plop is great \U0001f62d\U0001f62d (https://example.com/v?x=1). "
        "It was 30\u00b0."
    )
    assert clean_paragraph(raw) == "Plop is great. It was 30\u00b0."


def test_long_letter_and_bang_runs_are_capped():
    assert clean_paragraph("Haaaaaaaaaaah!!!!!!") == "Haaaah!!"


def test_cjk_corner_brackets_become_quotes():
    assert clean_paragraph("\u300eNew Notice\u300f") == '"New Notice"'


def test_non_latin_book_keeps_its_script():
    assert clean_paragraph(
        "\u041f\u0440\u0438\u0432\u0435\u0442, \u043c\u0438\u0440", latin_script=False
    ) == ("\u041f\u0440\u0438\u0432\u0435\u0442, \u043c\u0438\u0440")


@pytest.mark.parametrize("text", ["***", "* * *", "\u25c7\u25c7\u25c7", "---", "#" * 3])
def test_scene_breaks(text):
    assert is_scene_break(text)


@pytest.mark.parametrize("text", ["...", "\u201c...\u201d", "Yes.", "*Ding*"])
def test_not_scene_breaks(text):
    assert not is_scene_break(text)


def test_note_text_finds_footnotes_and_translator_notes():
    assert note_text("\u00b9: a name note") == "a name note"
    assert note_text("\u00b2 another note") == "another note"
    assert note_text("t/n : liked this line") == "liked this line"
    assert note_text("TL Note: meaning") == "meaning"
    assert note_text("A/N - thanks") == "thanks"
    assert note_text("Note: careful") == "careful"
    assert note_text("Nothing to note: here") is None
    assert note_text("t/n :") is None


def test_word_count_counts_contractions_and_compounds_once():
    assert word_count("It's a well-kept house, 30 years old.") == 7


def test_scene_break_marker_is_what_the_render_step_expects():
    assert SCENE_BREAK == "* * *"


def test_spaces_inside_quotes_are_tightened():
    assert clean_paragraph('" Squeak ." "Yeah."') == '"Squeak." "Yeah."'
    assert clean_paragraph('He said "hi" and " bye ".') == 'He said "hi" and "bye".'


def test_footnote_numbers_glued_to_words_are_dropped():
    assert clean_paragraph("A bowl of samgyetang1 and sikhye3, Speedwagon1.") == (
        "A bowl of samgyetang and sikhye, Speedwagon."
    )
    # a number with its own space, a code and a year stay
    assert (
        clean_paragraph("Level 10, room B12, mp3, in 2026.")
        == "Level 10, room B12, mp3, in 2026."
    )
