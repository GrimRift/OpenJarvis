"""Adding chapters to a stored book: the ones the source skipped and new ones.

Web novels are often bought or downloaded before they are finished, and the
reference book's source skips seven numbers (88, 321, 322, ...). The user
(2026-10-14) wanted three ways in: a newer PDF/EPUB of the same book (only
the chapters the book does not have yet are taken), text pasted on the
Books page, and a web page link (read with the web_read tool's fetch, then
shown in the paste form to check before saving).

A chapter's ``index`` names its text and audio files and "where you are",
so an added chapter never renumbers the others: it gets the next free index
and is placed in the chapter list by its number. Everything that walks the
book in reading order uses the list order, not the index.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openjarvis.audiobooks.importer import (
    _bare_title,
    _clean_chapter,
    _latin_script,
    _skip_reason,
    chapter_text,
    read_book,
)
from openjarvis.audiobooks.model import RawChapter
from openjarvis.audiobooks.store import BookStore
from openjarvis.audiobooks.text import SCENE_BREAK, word_count

#: Longest pasted or fetched chapter accepted (the longest stored is ~40k).
MAX_CHARS = 200_000

_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")
_HEADING = re.compile(r"^\s*(?:chapter|ch\.?|episode|ep\.?)\s*(\d+(?:\.\d+)?)\b", re.I)


def _number_value(number: Optional[str]) -> Optional[float]:
    try:
        return float(number) if number else None
    except ValueError:
        return None


def missing_numbers(chapters: List[Dict[str, Any]]) -> List[int]:
    whole = {
        int(v) for c in chapters if (v := _number_value(c.get("number"))) is not None
    }
    if not whole:
        return []
    return sorted(set(range(min(whole), max(whole) + 1)) - whole)


def _place(chapters: List[Dict[str, Any]], number: Optional[str]) -> int:
    """List position for a chapter numbered *number*: after the last one
    with a number at or below it; at the end when it has none."""
    value = _number_value(number)
    if value is None:
        return len(chapters)
    at = 0
    for k, c in enumerate(chapters):
        v = _number_value(c.get("number"))
        if v is not None and v <= value:
            at = k + 1
    return at


def _entry(raw: RawChapter, index: int, latin: bool) -> Tuple[Dict[str, Any], str]:
    body, notes = _clean_chapter(raw, latin)
    text = chapter_text(body, notes)
    reason = _skip_reason(raw, word_count("\n".join(body)))
    return (
        {
            "index": index,
            "label": raw.label,
            "number": raw.number,
            "title": _bare_title(raw),
            "words": word_count(text),
            "chars": len(text),
            "paragraphs": sum(1 for p in body if p != SCENE_BREAK),
            "notes": len(notes),
            "skip": reason is not None,
            "skip_reason": reason,
            "source": raw.source,
            "file": f"chapters/{index:04d}.txt",
            "added": True,
        },
        text,
    )


def _save(
    store: BookStore, book: Dict[str, Any], made: List[Tuple[Dict[str, Any], str]]
) -> Dict[str, Any]:
    """Write the new chapters' text, then book.json with them in place."""
    folder = store.root / book["id"]
    chapters = list(book["chapters"])
    for entry, text in made:
        (folder / entry["file"]).write_text(text, encoding="utf-8")
        chapters.insert(_place(chapters, entry["number"]), entry)
    narrated = [c for c in chapters if not c["skip"]]
    return store.update(
        book["id"],
        chapters=chapters,
        chapter_count=len(chapters),
        narrated_chapters=len(narrated),
        words=sum(c["words"] for c in narrated),
        missing_numbers=missing_numbers(chapters),
    )


def _next_index(book: Dict[str, Any]) -> int:
    return max((c["index"] for c in book["chapters"]), default=0) + 1


def _latin(book: Dict[str, Any], sample: str) -> bool:
    return _latin_script(book.get("language") or "", sample)


def add_from_file(
    book_id: str, path: str | Path, store: Optional[BookStore] = None
) -> Dict[str, Any]:
    """Chapters of a newer PDF/EPUB whose numbers the book does not have.
    Returns ``added`` (labels), ``already`` (count of numbers it had) and
    ``unnumbered`` (entries without a number, left out: front matter)."""
    store = store or BookStore()
    book = store.load(book_id)
    raw = read_book(Path(path))
    have = {_number_value(c.get("number")) for c in book["chapters"]}
    have.discard(None)
    sample = " ".join(p for c in raw.chapters[:5] for p in c.paragraphs[:40])
    latin = _latin(book, sample)
    made: List[Tuple[Dict[str, Any], str]] = []
    index = _next_index(book)
    already = unnumbered = 0
    for rc in raw.chapters:
        value = _number_value(rc.number)
        if value is None:
            unnumbered += 1
            continue
        if value in have:
            already += 1
            continue
        entry, text = _entry(rc, index, latin)
        made.append((entry, text))
        have.add(value)
        index += 1
    if made:
        _save(store, book, made)
    return {
        "added": [e["label"] for e, _ in made],
        "already": already,
        "unnumbered": unnumbered,
    }


def split_paragraphs(text: str) -> List[str]:
    """Pasted text -> paragraphs: blank lines split them; text with no blank
    lines at all (most web pages) is split at every line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = (
        re.split(r"\n\s*\n", text) if re.search(r"\n\s*\n", text) else text.split("\n")
    )
    return [" ".join(b.split()) for b in blocks if b.strip()]


def add_text(
    book_id: str,
    number: str,
    text: str,
    title: str = "",
    store: Optional[BookStore] = None,
) -> Dict[str, Any]:
    """One chapter from text (pasted, or fetched and checked by the user)."""
    store = store or BookStore()
    book = store.load(book_id)
    number = str(number or "").strip()
    if number and not _NUMBER.match(number):
        raise ValueError(f"Chapter number should look like 88 or 32.1, not {number!r}")
    if len(text) > MAX_CHARS:
        raise ValueError("That is longer than any chapter; paste one chapter at a time")
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        raise ValueError("There is no text to add")
    # A heading pasted along with the text ("Chapter 88: The Well") is not
    # read twice: the chapter announcement says it.
    first = paragraphs[0]
    m = _HEADING.match(first)
    if m and len(first) < 120:
        number = number or m.group(1)
        if not title:
            title = re.sub(r"^[\s:.\-–—]+", "", first[m.end() :]).strip()
        paragraphs = paragraphs[1:]
    value = _number_value(number)
    if value is not None and any(
        _number_value(c.get("number")) == value for c in book["chapters"]
    ):
        raise ValueError(f"{book['title']} already has chapter {number}")
    label = f"Chapter {number}" if number else (title or "Extra chapter")
    raw = RawChapter(
        label=label,
        title=f"{label}: {title}" if number and title else label,
        number=number or None,
        paragraphs=paragraphs,
        source="added on the Books page",
    )
    latin = _latin(book, " ".join(paragraphs[:40]))
    entry, chapter = _entry(raw, _next_index(book), latin)
    if entry["words"] == 0:
        raise ValueError("Nothing readable was left after cleaning the text")
    _save(store, book, [(entry, chapter)])
    return entry


def fetch_page(url: str) -> Dict[str, Any]:
    """A chapter's text from a web page, for the user to check before adding:
    ``title``, ``number`` (guessed from the page), ``text``."""
    from openjarvis.tools.web_read import WebReadTool

    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    tool = WebReadTool()
    tool._description = ""
    tool._walled = False
    got = tool._fetch_static(url, "")
    if got is None:
        # Needs a browser (a script-built page or a bot check): Opera.
        text, _, title = tool._render_capped(url, "")
    else:
        text, title = got
    if not text or not text.strip():
        raise ValueError("That page has no readable text (it may need a sign-in)")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines, heading = _chapter_part(lines)
    number = ""
    m = _HEADING.match(heading or title or "")
    if m:
        number = m.group(1)
    return {
        "url": url,
        "title": (heading or title or "").strip(),
        "number": number,
        "text": "\n\n".join(lines)[:MAX_CHARS],
    }


_NAV = re.compile(
    r"^(?:<?\s*prev(?:ious)?(?: chapter)?|next(?: chapter)?\s*>?|table of contents"
    r"|index|home|report|comments?|chapter list|bookmark|settings)$",
    re.I,
)


def _chapter_part(lines: List[str]) -> Tuple[List[str], Optional[str]]:
    """The chapter in a page's lines: from its "Chapter N" heading (when
    there is one in the first third) to the first navigation line after the
    text, so menus, "Next chapter" and comments are left out."""
    start, heading = 0, None
    for k, line in enumerate(lines[: max(10, len(lines) // 3)]):
        if _HEADING.match(line) and len(line) < 120:
            start, heading = k + 1, line
            break
    out: List[str] = []
    for line in lines[start:]:
        if _NAV.match(line) and len(out) > 5:
            break
        if _NAV.match(line):
            continue
        out.append(line)
    return out, heading
