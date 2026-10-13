"""PDF/EPUB -> chapters of cleaned, speakable text in the book store.

The readers (``pdf.py``, ``epub.py``) find chapters and paragraphs; this
module cleans each paragraph (``text.py``), separates footnotes and
translator's notes, flags entries not worth narrating, and saves the book.

Decisions (user, 2026-10-13): chapters are kept as the source has them (the
reference book's ``32.1``/``32.2`` halves stay separate, numbers the source
skips are recorded as missing); front/back matter and near-empty entries are
imported but flagged ``skip``; footnotes and translator's notes are kept and
read at the end of their chapter, after the story text.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openjarvis.audiobooks.model import RawBook, RawChapter
from openjarvis.audiobooks.store import BookStore
from openjarvis.audiobooks.text import (
    SCENE_BREAK,
    clean_paragraph,
    is_scene_break,
    note_text,
    word_count,
)

#: Bumped when re-importing would give different text, so stale books can be found.
IMPORTER_VERSION = 1

#: Heading spoken before the notes at a chapter's end.
NOTES_HEADING = "Notes."

#: Marked notes starting within this many paragraphs of a chapter's end (or
#: its last seventh) make everything after them notes too.
NOTES_TAIL = 20

#: Fewer narrated words than this and an entry is flagged skip (a title page,
#: "To be continued", an image-only page).
MIN_WORDS = 30

_FRONT_BACK = re.compile(
    r"^(?:front cover|full cover|back cover|cover|title page|half title|copyright"
    r"|copyright page|table of contents|contents|toc|image gallery|gallery"
    r"|illustrations?|maps?|map of .*|characters|character list|locations|glossary"
    r"|index|dedication|acknowledge?ments?|also by .*"
    r"|about the (?:author|translator|publisher)"
    r"|newsletter|synopsis|insert|colou?r inserts?)$",
    re.I,
)
_GLUED_BREAK = re.compile(r"^(?:\*{3,}|(?:\* ){2,}\*)\s*(?=\S)")


def _latin_script(language: str, sample: str) -> bool:
    if language:
        return language.split("-")[0].lower() in {
            "en",
            "fr",
            "de",
            "es",
            "it",
            "pt",
            "nl",
            "sv",
            "da",
            "no",
            "nb",
            "fi",
            "pl",
            "cs",
            "ro",
            "hu",
            "id",
            "ms",
            "tl",
            "fil",
            "tr",
            "vi",
            "ca",
            "la",
        }
    letters = [c for c in sample if c.isalpha()]
    if not letters:
        return True
    import unicodedata

    latin = sum(unicodedata.name(c, "").startswith("LATIN") for c in letters)
    return latin / len(letters) > 0.9


def _split_glued_breaks(paragraphs: List[str]) -> List[str]:
    out: List[str] = []
    for p in paragraphs:
        m = _GLUED_BREAK.match(p)
        if m:
            out.extend([SCENE_BREAK, p[m.end() :]])
        else:
            out.append(p)
    return out


def _clean_chapter(raw: RawChapter, latin: bool) -> Tuple[List[str], List[str]]:
    """(story paragraphs, notes), both cleaned; scene breaks as SCENE_BREAK."""
    body: List[str] = []
    notes: List[str] = []
    in_note = False
    paragraphs = _split_glued_breaks(raw.paragraphs)
    # Translators close a chapter with their notes and then chat ("anyways, hope
    # you enjoyed..."), unmarked. Once marked notes start in the closing stretch,
    # the rest of the chapter is notes. (Reference book: always the last 12.)
    marked = [i for i, p in enumerate(paragraphs) if note_text(p) is not None]
    tail = len(paragraphs)
    if marked and len(paragraphs) - marked[0] <= max(NOTES_TAIL, len(paragraphs) // 7):
        tail = marked[0]
    for i, para in enumerate(paragraphs):
        note = note_text(para)
        if note is None and i >= tail and not is_scene_break(para):
            note = para
        if note is None and in_note and re.match(r"^\*\s", para):
            note = para.lstrip("* ")
        if note is not None:
            cleaned = clean_paragraph(note, latin_script=latin)
            if cleaned:
                notes.append(cleaned)
            in_note = True
            continue
        in_note = False
        if i >= tail:
            continue
        if is_scene_break(para):
            if body and body[-1] != SCENE_BREAK:
                body.append(SCENE_BREAK)
            continue
        cleaned = clean_paragraph(para, latin_script=latin)
        if cleaned:
            body.append(cleaned)
    while body and body[-1] == SCENE_BREAK:
        body.pop()
    for extra in raw.notes:
        cleaned = clean_paragraph(extra, latin_script=latin)
        if cleaned:
            notes.append(cleaned)
    return body, notes


def chapter_text(body: List[str], notes: List[str]) -> str:
    """The stored, narrated form: paragraphs split by blank lines, notes last."""
    parts = list(body)
    if notes:
        parts += [SCENE_BREAK, NOTES_HEADING, *notes]
    return "\n\n".join(parts) + "\n"


def _bare_title(raw: RawChapter) -> str:
    """The title without its number ("Chapter 1: Crimson" -> "Crimson"), if any."""
    bare = re.sub(
        r"^\s*(?:chapter|ch\.?)\s*\d+(?:\.\d+)?\s*[:.\-\u2013\u2014]?\s*",
        "",
        raw.title,
        flags=re.I,
    )
    return bare.strip() or raw.title.strip() or raw.label


def _skip_reason(raw: RawChapter, words: int) -> Optional[str]:
    if raw.front_matter:
        return "front matter"
    if _FRONT_BACK.match(raw.label.strip()):
        return "front/back matter"
    if words < MIN_WORDS:
        return "almost no text"
    return None


def _missing_numbers(chapters: List[RawChapter]) -> List[int]:
    whole = {int(float(c.number)) for c in chapters if c.number}
    if not whole:
        return []
    return sorted(set(range(min(whole), max(whole) + 1)) - whole)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_book(path: Path) -> RawBook:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from openjarvis.audiobooks.pdf import read_pdf

        return read_pdf(path)
    if suffix == ".epub":
        from openjarvis.audiobooks.epub import read_epub

        return read_epub(path)
    raise ValueError(f"Not a PDF or EPUB: {path.name}")


def import_book(path: str | Path, store: Optional[BookStore] = None) -> Dict[str, Any]:
    """Import a PDF or EPUB into the book store and return its ``book.json``.

    Importing the same file again replaces the stored book (same id).
    """
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    store = store or BookStore()
    raw = read_book(path)
    sample = " ".join(p for c in raw.chapters[:5] for p in c.paragraphs[:40])
    latin = _latin_script(raw.language, sample)

    chapters: List[Dict[str, Any]] = []
    texts: List[str] = []
    for index, rc in enumerate(raw.chapters, start=1):
        body, notes = _clean_chapter(rc, latin)
        text = chapter_text(body, notes)
        words = word_count(text)
        reason = _skip_reason(rc, word_count("\n".join(body)))
        chapters.append(
            {
                "index": index,
                "label": rc.label,
                "number": rc.number,
                "title": _bare_title(rc),
                "words": words,
                "chars": len(text),
                "paragraphs": sum(1 for p in body if p != SCENE_BREAK),
                "notes": len(notes),
                "skip": reason is not None,
                "skip_reason": reason,
                "source": rc.source,
                "file": f"chapters/{index:04d}.txt",
            }
        )
        texts.append(text)

    sha = _sha256(path)
    book = {
        "id": BookStore.make_id(raw.title, sha),
        "title": raw.title,
        "author": raw.author,
        "language": raw.language,
        "cover": None,
        "source": {
            "path": str(path),
            "format": raw.source_format,
            "sha256": sha,
            "bytes": path.stat().st_size,
            "pages": raw.pages,
        },
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "importer_version": IMPORTER_VERSION,
        "chapter_count": len(chapters),
        "narrated_chapters": sum(1 for c in chapters if not c["skip"]),
        "words": sum(c["words"] for c in chapters if not c["skip"]),
        "missing_numbers": _missing_numbers(raw.chapters),
        "structure": raw.structure,
        "chapters": chapters,
    }
    return store.save(book, texts, raw.cover)
