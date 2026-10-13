"""The M42 reference book (and two EPUBs) on this machine. Skipped elsewhere.

Structure only: counts, labels, lengths and the absence of what must be
cleaned. No book text is reproduced here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from openjarvis.audiobooks import BookStore, import_book

DOWNLOADS = Path("D:/Downloads")
STEM = "_OceanofPDF.com_The_Artist_Who_Paints_Dungeon_-_Hobby_Writing_Principle"
REFERENCE_PDF = DOWNLOADS / f"{STEM}.pdf"
REFERENCE_EPUB = DOWNLOADS / f"{STEM}.epub"
LOTM_EPUB = DOWNLOADS / "Clown - LotM Vol. 1.epub"

#: Numbers the source itself skips (both the PDF and the EPUB edition).
MISSING = [88, 321, 322, 331, 332, 351, 352]
SPLIT = ["32.1", "32.2", "33.1", "33.2", "35.1", "35.2", "41.1", "41.2"]

LEFTOVERS = re.compile(
    r"(?i)ocean\s*of\s*pdf|n\s?o\s?v\s?e\s?l\s?i\s?g\s?h\s?t|read more on our source"
    r"|exclusive on|copy, read here|original source"
    "|[\u201c\u201d\u2018\u2019\u2026\ufffe\u00ad\u00b9\u00b2\u00b3\u2070-\u2079]"
)


def _require(path: Path) -> None:
    if not path.is_file():
        pytest.skip(f"reference book not on this machine: {path.name}")
    pytest.importorskip("pypdfium2")


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    _require(REFERENCE_PDF)
    store = BookStore(tmp_path_factory.mktemp("ref") / "books")
    return import_book(REFERENCE_PDF, store), store


def test_reference_pdf_has_372_chapters_as_the_source_does(reference):
    book, _ = reference
    assert book["chapter_count"] == 372 == book["narrated_chapters"]
    assert book["structure"]["bookmarks"] == 372 == book["structure"]["matched"]
    assert book["structure"]["heading_only"] == [] == book["structure"]["bookmark_only"]
    assert book["missing_numbers"] == MISSING
    numbers = [c["number"] for c in book["chapters"]]
    assert numbers[0] == "1" and numbers[-1] == "375"
    assert all(n in numbers for n in SPLIT)
    assert len(numbers) == len(set(numbers))


def test_reference_pdf_metadata_cover_and_size(reference):
    book, store = reference
    assert book["title"] == "The Artist Who Paints Dungeon"
    assert "Hobby Writing Principle" in book["author"]
    assert (store.root / book["id"] / "cover.jpg").read_bytes()[:2] == b"\xff\xd8"
    assert book["source"]["pages"] == 4327
    assert 840_000 < book["words"] < 880_000
    assert min(c["words"] for c in book["chapters"]) > 250


def test_reference_pdf_text_is_clean(reference):
    book, store = reference
    for chapter in book["chapters"]:
        text = store.chapter_text(book["id"], chapter["index"])
        hit = LEFTOVERS.search(text)
        assert hit is None, (chapter["label"], hit.group(0))
        assert not text.startswith(("CHAPTER", "Chapter"))
        assert "\n\n\n" not in text


def test_reference_pdf_notes_are_at_chapter_ends(reference):
    book, store = reference
    with_notes = [c for c in book["chapters"] if c["notes"]]
    assert 15 <= len(with_notes) <= 25
    assert all(
        c["label"] in {f"Chapter {n}" for n in range(1, 62)} | {"Chapter 32.1"}
        for c in with_notes
    )
    for c in with_notes:
        assert "\n\n* * *\n\nNotes.\n\n" in store.chapter_text(book["id"], c["index"])


def test_reference_epub_matches_the_pdf(reference, tmp_path):
    _require(REFERENCE_EPUB)
    pdf_book, _ = reference
    epub_book = import_book(REFERENCE_EPUB, BookStore(tmp_path / "books"))
    epub = {c["label"]: c for c in epub_book["chapters"] if not c["skip"]}
    assert len(epub) == 372
    same_paragraphs = 0
    for c in pdf_book["chapters"]:
        other = epub[c["label"]]
        assert abs(c["words"] - other["words"]) <= 5, c["label"]
        same_paragraphs += c["paragraphs"] == other["paragraphs"]
    # paragraphs come from indents in the PDF and <p> in the EPUB: they agree
    assert same_paragraphs >= 360


def test_lotm_epub_flags_front_and_back_matter(tmp_path):
    _require(LOTM_EPUB)
    book = import_book(LOTM_EPUB, BookStore(tmp_path / "books"))
    skipped = {c["label"] for c in book["chapters"] if c["skip"]}
    assert {
        "Front Cover",
        "Copyright",
        "Table of Contents",
        "Image Gallery",
        "Back Cover",
    } <= skipped
    narrated = [c for c in book["chapters"] if not c["skip"]]
    assert narrated[0]["label"].startswith("Chapter 1")
    assert all(c["words"] > 1000 for c in narrated if c["number"])
    assert book["cover"]
