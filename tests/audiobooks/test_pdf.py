"""PDF import on a synthetic book laid out like the reference one.

Paragraphs are marked only by a first-line indent, every page has a running
header and a page number, chapter 2 has a heading but no bookmark, and the
text carries the watermark, footnotes and hyphenation the cleaner must undo.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

import pytest

pytest.importorskip("pypdfium2")

from openjarvis.audiobooks import BookStore, import_book  # noqa: E402
from tests.audiobooks.builders import Line, make_pdf  # noqa: E402

LEFT, INDENT, STEP = 72.0, 96.0, 14.0
HEADER = "SYNTHETIC BOOK"

# (kind, payload): para = lines of one paragraph, heading = centred line,
# center = centred non-heading line, page = force a new page
SCRIPT: Sequence[Tuple[str, object]] = (
    [
        ("para", ["A note from the publisher about this edition."]),
        ("page", None),
        ("heading", "CHAPTER 1"),
        (
            "para",
            [
                "The painter opened the shutters and looked out",
                "over the quiet valley below the old mill.",
            ],
        ),
        ("para", ["\u201cMorning,\u201d he said to nobody in particular.\u00b9"]),
        (
            "para",
            [
                "It was a beau-",
                "tiful day, and the wood-",
                "fired oven was already warm.",
            ],
        ),
        ("para", ["A beautiful morning, he thought, and went down."]),
        ("center", "***"),
        (
            "para",
            [
                "He smiled ~Novelight~ at the canvas and began",
                "the first wash of blue.",
            ],
        ),
    ]
    + [
        (
            "para",
            [
                f"Filler line {n} for the first chapter goes on and",
                "keeps going until the page runs out of room.",
            ],
        )
        for n in range(40)
    ]
    + [
        ("para", ["\u00b9: The mill is older than the village."]),
        ("para", ["t/n : I enjoyed this chapter."]),
        ("para", ["anyways, see you next time"]),
        ("para", ["OceanofPDF.com"]),
        ("heading", "CHAPTER 2"),
        (
            "para",
            [
                "The second chapter starts mid-page and has no",
                "bookmark of its own at all.",
            ],
        ),
    ]
    + [
        (
            "para",
            [
                f"Second chapter line {n} continues the story here",
                "with more words for the narrator.",
            ],
        )
        for n in range(20)
    ]
    + [
        ("page", None),
        ("heading", "CHAPTER 3"),
        ("para", ["The last chapter is short and ends the book."]),
    ]
    + [
        (
            "para",
            [
                f"Closing line {n} of the book is here and it",
                "wraps onto a second line as well.",
            ],
        )
        for n in range(30)
    ]
)


def _layout() -> Tuple[List[List[Line]], List[Tuple[str, int]]]:
    pages: List[List[Line]] = []
    outline: List[Tuple[str, int]] = []
    y = 0.0

    def new_page() -> None:
        nonlocal y
        pages.append([(250.0, 760.0, HEADER), (300.0, 40.0, str(len(pages) + 1))])
        y = 700.0

    def room(lines: int) -> None:
        if not pages or y - STEP * lines < 80:
            new_page()

    for kind, payload in SCRIPT:
        if kind == "page":
            new_page()
        elif kind in ("heading", "center"):
            room(3)
            if kind == "heading" and payload in ("CHAPTER 1", "CHAPTER 3"):
                outline.append((str(payload).title(), len(pages) - 1))
            y -= STEP if kind == "center" else 2 * STEP
            pages[-1].append((260.0, y, str(payload)))
            y -= STEP if kind == "center" else 2 * STEP
        else:
            for k, text in enumerate(payload):  # type: ignore[arg-type]
                room(1)
                pages[-1].append((INDENT if k == 0 else LEFT, y, text))
                y -= STEP
    return pages, outline


@pytest.fixture(scope="module")
def imported(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pdfbook")
    pages, outline = _layout()
    assert outline[0][1] < outline[1][1]
    pdf = make_pdf(
        tmp / "book.pdf",
        pages,
        outline,
        {"Title": "Synthetic Book", "Author": "A. Writer"},
    )
    store = BookStore(tmp / "books")
    return import_book(pdf, store), store


def _text(imported, label: str) -> str:
    book, store = imported
    chapter = next(c for c in book["chapters"] if c["label"] == label)
    return store.chapter_text(book["id"], chapter["index"])


def test_chapters_come_from_bookmarks_and_headings(imported):
    book, _ = imported
    assert [c["label"] for c in book["chapters"]] == [
        "Front matter",
        "Chapter 1",
        "Chapter 2",
        "Chapter 3",
    ]
    assert [c["number"] for c in book["chapters"]] == [None, "1", "2", "3"]
    structure = book["structure"]
    assert structure["bookmarks"] == 2 and structure["headings"] == 3
    assert structure["matched"] == 2
    assert structure["heading_only"] == ["Chapter 2"]
    assert structure["bookmark_only"] == []
    assert book["missing_numbers"] == []


def test_front_matter_is_kept_but_skipped(imported):
    book, _ = imported
    front = book["chapters"][0]
    assert front["skip"] and front["skip_reason"] == "front matter"
    assert all(not c["skip"] for c in book["chapters"][1:])
    assert book["narrated_chapters"] == 3


def test_paragraphs_from_indents_and_across_lines(imported):
    paras = _text(imported, "Chapter 1").split("\n\n")
    assert paras[0] == (
        "The painter opened the shutters and looked out "
        "over the quiet valley below the old mill."
    )
    assert paras[1] == '"Morning," he said to nobody in particular.'
    # hyphen dropped where the book spells the word whole elsewhere, kept for a compound
    assert (
        paras[2] == "It was a beautiful day, and the wood-fired oven was already warm."
    )
    assert paras[4] == "* * *"
    assert paras[5] == "He smiled at the canvas and began the first wash of blue."


def test_running_headers_page_numbers_and_headings_are_not_narrated(imported):
    book, _ = imported
    assert book["structure"]["running_lines_dropped"] == 2 * 7
    for label in ("Chapter 1", "Chapter 2", "Chapter 3"):
        text = _text(imported, label)
        assert HEADER not in text
        assert "CHAPTER" not in text
        assert not any(p.strip().isdigit() for p in text.split("\n\n"))


def test_a_paragraph_continues_across_a_page_break(imported):
    paras = _text(imported, "Chapter 1").split("\n\n")
    fillers = [p for p in paras if p.startswith("Filler line")]
    assert len(fillers) == 40
    assert all(
        p.endswith("keeps going until the page runs out of room.") for p in fillers
    )


def test_notes_and_translator_chatter_are_read_last(imported):
    text = _text(imported, "Chapter 1")
    body, _, notes = text.partition("* * *\n\nNotes.\n\n")
    assert notes.split("\n\n") == [
        "The mill is older than the village.",
        "I enjoyed this chapter.",
        "anyways, see you next time\n",
    ]
    assert "OceanofPDF" not in text and "Novelight" not in text
    book, _ = imported
    assert book["chapters"][1]["notes"] == 3


def test_metadata_cover_and_counts(imported):
    book, store = imported
    assert book["title"] == "Synthetic Book" and book["author"] == "A. Writer"
    assert book["source"]["format"] == "pdf" and book["source"]["pages"] == 7
    cover = store.root / book["id"] / book["cover"]
    assert cover.read_bytes()[:2] == b"\xff\xd8"  # first page rendered to JPEG
    second = book["chapters"][2]
    assert second["words"] == len(_text(imported, "Chapter 2").split())
    assert book["words"] == sum(c["words"] for c in book["chapters"] if not c["skip"])


def test_chapter_headings_topping_most_pages_are_not_running_headers():
    from openjarvis.audiobooks.pdf import _drop_running_lines, _Line

    pages = [
        [
            _Line(f"CHAPTER {n}", n, 260, 330, 760, 760),
            _Line("Body text of the page goes here.", n, 72, 500, 700, 700),
            _Line(str(n + 1), n, 300, 306, 40, 40),
        ]
        for n in range(6)
    ]
    assert _drop_running_lines(pages) == 6  # the page numbers only
    assert all(p[0].text.startswith("CHAPTER") and len(p) == 2 for p in pages)
