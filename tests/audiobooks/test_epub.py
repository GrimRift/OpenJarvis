"""EPUB import on small synthetic books (EPUB 3 nav and EPUB 2 NCX)."""

from __future__ import annotations

import pytest

from openjarvis.audiobooks import BookStore, import_book
from tests.audiobooks.builders import make_epub

STORY = " ".join(["The river ran on past the mill and the bridge."] * 6)

DOCS = [
    ("cover.xhtml", '<div><img src="images/cover.jpg" alt="cover"/></div>'),
    (
        "copyright.xhtml",
        "<p>Copyright 2026 A. Writer. All rights reserved worldwide.</p>",
    ),
    (
        "ch1.xhtml",
        "<h1>Chapter 1: Crimson</h1>"
        "<p>\u201cHello,\u201d she said."
        "<sup><a href='#n1' epub:type='noteref'>1</a></sup></p>"
        f"<p>{STORY}</p><hr/><p>After the break.</p>"
        "<aside epub:type='footnote' id='n1'><p>1. A greeting.</p></aside>",
    ),
    (
        "ch2-3.xhtml",
        f"<h2>Chapter 2</h2><p>{STORY}</p><p>Ch2 end.</p>"
        f"<h2 id='c3'>Chapter 3</h2><p>Ch3 start.</p><p>{STORY}</p>",
    ),
    ("map.xhtml", "<p><img src='m.png'/></p>"),
]
TOC = [
    ("Cover", "cover.xhtml"),
    ("Copyright", "copyright.xhtml"),
    ("Chapter 1: Crimson", "ch1.xhtml"),
    ("Chapter 2", "ch2-3.xhtml"),
    ("Chapter 3", "ch2-3.xhtml#c3"),
    ("Map", "map.xhtml"),
]
JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


@pytest.fixture(params=[True, False], ids=["epub3-nav", "epub2-ncx"])
def imported(request, tmp_path):
    path = make_epub(tmp_path / "b.epub", DOCS, TOC, cover=JPEG, epub3=request.param)
    store = BookStore(tmp_path / "books")
    return import_book(path, store), store


def _text(imported, index: int) -> str:
    book, store = imported
    return store.chapter_text(book["id"], index)


def test_toc_entries_become_chapters(imported):
    book, _ = imported
    assert [c["label"] for c in book["chapters"]] == [c[0] for c in TOC]
    assert [c["number"] for c in book["chapters"]] == [None, None, "1", "2", "3", None]
    assert book["chapters"][2]["title"] == "Crimson"
    assert book["chapters"][3]["title"] == "Chapter 2"


def test_front_and_back_matter_are_flagged_skip(imported):
    book, _ = imported
    skips = {c["label"]: c["skip_reason"] for c in book["chapters"] if c["skip"]}
    assert skips == {
        "Cover": "front/back matter",
        "Copyright": "front/back matter",
        "Map": "front/back matter",
    }
    assert book["narrated_chapters"] == 3


def test_fragment_splits_one_document_into_two_chapters(imported):
    assert _text(imported, 4).split("\n\n")[-1].strip() == "Ch2 end."
    ch3 = _text(imported, 5).split("\n\n")
    assert ch3[0] == "Ch3 start."
    assert "Chapter 3" not in ch3[0]


def test_heading_dropped_noteref_removed_footnote_read_last(imported):
    paras = _text(imported, 3).strip().split("\n\n")
    assert paras[0] == '"Hello," she said.'
    assert paras[1] == STORY
    assert paras[2] == "* * *" and paras[3] == "After the break."
    assert paras[-3:] == ["* * *", "Notes.", "1. A greeting."]
    book, _ = imported
    assert book["chapters"][2]["notes"] == 1


def test_metadata_and_cover(imported):
    book, store = imported
    assert (book["title"], book["author"], book["language"]) == (
        "Synthetic Book",
        "A. Writer",
        "en",
    )
    assert book["source"]["format"] == "epub"
    assert (store.root / book["id"] / book["cover"]).read_bytes() == JPEG


def test_no_toc_means_one_chapter_per_spine_document(tmp_path):
    path = make_epub(tmp_path / "n.epub", DOCS[2:4], [], epub3=True)
    book = import_book(path, BookStore(tmp_path / "books"))
    assert [c["label"] for c in book["chapters"]] == ["ch1", "ch2-3"]
    assert book["cover"] is None
