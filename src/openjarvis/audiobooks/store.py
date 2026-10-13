"""The book store: one folder per imported book under ``<data>/books``.

::

    books/<id>/book.json          title, author, cover, source, chapters[]
    books/<id>/chapters/0001.txt  cleaned text, paragraphs split by blank lines
    books/<id>/cover.jpg          when the book has one

The id is the title's slug plus the first 8 hex digits of the source file's
SHA-256, so re-importing the same file replaces the book, and a different
edition of the same title gets its own folder. A save is written beside the
old folder and swapped in, so a failed import never leaves half a book.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openjarvis.core.paths import get_data_dir


class BookStore:
    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root is not None else get_data_dir() / "books"

    @staticmethod
    def make_id(title: str, sha256: str) -> str:
        ascii_title = (
            unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
        )
        slug = (
            re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:48].strip("-")
        )
        return f"{slug or 'book'}-{sha256[:8]}"

    def _dir(self, book_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", book_id):
            raise ValueError(f"Bad book id: {book_id!r}")
        return self.root / book_id

    def save(
        self,
        book: Dict[str, Any],
        texts: List[str],
        cover: Optional[Tuple[bytes, str]] = None,
    ) -> Dict[str, Any]:
        final = self._dir(book["id"])
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".tmp-{book['id']}-{os.getpid()}"
        old = self.root / f".old-{book['id']}-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True)
        (tmp / "chapters").mkdir(parents=True)
        try:
            for chapter, text in zip(book["chapters"], texts, strict=True):
                (tmp / chapter["file"]).write_text(text, encoding="utf-8")
            if cover:
                data, ext = cover
                name = f"cover.{re.sub(r'[^a-z0-9]', '', ext.lower()) or 'jpg'}"
                (tmp / name).write_bytes(data)
                book = {**book, "cover": name}
            (tmp / "book.json").write_text(
                json.dumps(book, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if final.exists():
                final.rename(old)
            tmp.rename(final)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            if old.exists() and not final.exists():
                old.rename(final)
            raise
        shutil.rmtree(old, ignore_errors=True)
        return book

    def load(self, book_id: str) -> Dict[str, Any]:
        return json.loads(
            (self._dir(book_id) / "book.json").read_text(encoding="utf-8")
        )

    def chapter_text(self, book_id: str, index: int) -> str:
        book = self.load(book_id)
        chapter = next((c for c in book["chapters"] if c["index"] == index), None)
        if chapter is None:
            raise KeyError(f"{book_id} has no chapter {index}")
        return (self._dir(book_id) / chapter["file"]).read_text(encoding="utf-8")

    def list(self) -> List[Dict[str, Any]]:
        """Every stored book without its chapter list, newest import first."""
        books = []
        if not self.root.is_dir():
            return books
        for folder in self.root.iterdir():
            meta = folder / "book.json"
            if folder.name.startswith(".") or not meta.is_file():
                continue
            book = json.loads(meta.read_text(encoding="utf-8"))
            book.pop("chapters", None)
            books.append(book)
        return sorted(books, key=lambda b: b.get("imported_at", ""), reverse=True)
