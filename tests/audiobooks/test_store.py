"""The book store: ids, replace-on-reimport, listing."""

from __future__ import annotations

import pytest

from openjarvis.audiobooks import BookStore, import_book
from tests.audiobooks.builders import make_epub

DOCS = [("c1.xhtml", "<p>" + "Words go here and here. " * 10 + "</p>")]
TOC = [("Chapter 1", "c1.xhtml")]


def test_id_is_title_slug_plus_hash():
    assert BookStore.make_id("The Artist Who Paints Dungeon", "459fda72abcdef") == (
        "the-artist-who-paints-dungeon-459fda72"
    )
    assert BookStore.make_id("\ucde8\ubbf8", "0123456789") == "book-01234567"


def test_default_root_is_books_in_the_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENJARVIS_HOME", str(tmp_path))
    assert BookStore().root == tmp_path.resolve() / "books"


def test_reimport_replaces_the_same_book(tmp_path):
    store = BookStore(tmp_path / "books")
    path = make_epub(tmp_path / "a.epub", DOCS, TOC)
    first = import_book(path, store)
    second = import_book(path, store)
    assert first["id"] == second["id"]
    assert [b["id"] for b in store.list()] == [first["id"]]
    assert "chapters" not in store.list()[0]
    assert not [p for p in store.root.iterdir() if p.name.startswith(".")]
    assert store.load(first["id"])["chapter_count"] == 1


def test_failed_save_keeps_the_old_book(tmp_path):
    store = BookStore(tmp_path / "books")
    book = import_book(make_epub(tmp_path / "a.epub", DOCS, TOC), store)
    broken = {**book, "chapters": book["chapters"] + [{"file": "chapters/0002.txt"}]}
    with pytest.raises(ValueError):
        store.save(broken, ["only one text"])  # texts and chapters disagree
    assert store.chapter_text(book["id"], 1).startswith("Words go here")


def test_bad_ids_are_refused(tmp_path):
    store = BookStore(tmp_path / "books")
    for bad in ("../x", "A-B", "", "x/y"):
        with pytest.raises(ValueError):
            store.load(bad)


def test_unknown_format_is_refused(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("hello")
    with pytest.raises(ValueError):
        import_book(path, BookStore(tmp_path / "books"))
