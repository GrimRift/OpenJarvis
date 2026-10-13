"""Reading a PDF book into chapters of raw paragraphs.

A PDF has no paragraphs, only positioned lines. The reference book (a calibre
export, 4,327 pages) marks a new paragraph only by a first-line indent:
continuation lines start at x=77, paragraph starts at x=100, and nothing
separates them in the text layer. So paragraphs come from line geometry --
indent, a short last line, a wider vertical gap -- not from the text.

Chapters come from two sources that are reconciled: the bookmarks (outline)
and ``CHAPTER N`` heading lines. In the reference book both agree exactly on
372 chapters; the numbering itself skips 7 numbers and splits 4 chapters into
``.1``/``.2`` halves, which is the source, not a reading error.
"""

from __future__ import annotations

import logging
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openjarvis.audiobooks.model import RawBook, RawChapter

logger = logging.getLogger(__name__)

#: A heading line: ``CHAPTER 12``, ``Chapter 32.1``, ``Chapter 7: The Well``.
HEADING_RE = re.compile(
    r"^(?:CHAPTER|Chapter)\s+(\d+(?:\.\d+)?)(?:\s*[:.\-\u2013\u2014]\s*(\S.*))?$"
)
#: Marks a line-end hyphen PDFium reports as U+FFFE (or a visible "-").
_HYPHEN_MARK = "\u00ad"


@dataclass
class _Line:
    text: str
    page: int
    left: float
    right: float
    top: float
    last_top: (
        float  # top of the last character: PDFium may report two visual lines as one
    )
    para_start: bool = False


@dataclass
class _Start:
    page: int
    line: int  # index into that page's lines; the heading line itself if any
    label: str
    title: str
    number: Optional[str]
    from_heading: bool
    from_bookmark: bool
    drop_line: bool = False  # the heading line is not narrated text


@dataclass
class _Report:
    bookmarks: int = 0
    headings: int = 0
    matched: int = 0
    heading_only: List[str] = field(default_factory=list)
    bookmark_only: List[str] = field(default_factory=list)


def _page_lines(page: Any, index: int) -> List[_Line]:
    textpage = page.get_textpage()
    try:
        text = textpage.get_text_range()
        lines: List[_Line] = []
        offset = 0  # PDFium indexes characters in UTF-16 code units
        for raw in re.split(r"(\r\n|\n|\r)", text):
            units = len(raw.encode("utf-16-le")) // 2
            if raw in ("\r\n", "\n", "\r"):
                offset += units
                continue
            stripped = raw.strip()
            if stripped:
                lead = len(raw) - len(raw.lstrip())
                first = offset + len(raw[:lead].encode("utf-16-le")) // 2
                tail = raw.rstrip()
                last = offset + len(tail.encode("utf-16-le")) // 2 - 1
                left, _, _, top = textpage.get_charbox(first)
                _, _, right, last_top = textpage.get_charbox(last)
                lines.append(
                    _Line(
                        stripped.replace("\ufffe", _HYPHEN_MARK),
                        index,
                        left,
                        right,
                        top,
                        last_top,
                    )
                )
            offset += units
        return lines
    finally:
        textpage.close()


def _norm_running(text: str) -> str:
    return re.sub(r"\d+", "#", text.strip().lower())


def _drop_running_lines(pages: List[List[_Line]]) -> int:
    """Remove running headers/footers and page numbers. Returns how many.

    The topmost or bottommost line of a page (by position: a PDF may emit
    its header and page number before the body text) whose text, digits
    ignored, repeats on at least 30% of pages is a running header or footer;
    that covers bare page numbers too.
    """
    with_text = [p for p in pages if len(p) >= 2]
    if len(with_text) < 5:
        return 0

    def edges(lines: List[_Line]) -> List[_Line]:
        top = max(lines, key=lambda ln: ln.top)
        bottom = min(lines, key=lambda ln: ln.top)
        return [top, bottom]

    counts: Counter = Counter()
    for lines in with_text:
        for text in {_norm_running(ln.text) for ln in edges(lines)}:
            counts[text] += 1
    threshold = max(3, 0.3 * len(with_text))
    # "chapter #" tops a page per chapter: in a book of short chapters that can
    # pass the threshold, and it is a heading, not a running header.
    running = {
        k
        for k, n in counts.items()
        if n >= threshold and not re.match(r"chapter #(?:\.#)?(?!\S)", k)
    }
    dropped = 0
    for lines in with_text:
        for edge in edges(lines):
            if edge in lines and _norm_running(edge.text) in running:
                lines.remove(edge)
                dropped += 1
    return dropped


def _margins(lines: List[_Line]) -> Tuple[float, float]:
    """(left, right) text margins: the outermost edges at least 2% of lines share.

    Not the most common edge: in a dialogue-heavy book most long lines are
    paragraph starts, so the commonest left edge is the indent itself.
    """
    floor = max(2, len(lines) // 50)
    lefts = Counter(round(ln.left) for ln in lines)
    rights = Counter(round(ln.right) for ln in lines)
    left = min((x for x, n in lefts.items() if n >= floor), default=min(lefts))
    right = max((x for x, n in rights.items() if n >= floor), default=max(rights))
    return float(left), float(right)


def _mark_paragraphs(pages: List[List[_Line]]) -> None:
    lines = [ln for page in pages for ln in page]
    long_lines = [ln for ln in lines if len(ln.text) >= 30]
    if not long_lines:
        for ln in lines:
            ln.para_start = True
        return
    # Printed books mirror their margins, so odd and even pages are measured apart.
    margins = {
        parity: _margins(
            [ln for ln in long_lines if ln.page % 2 == parity] or long_lines
        )
        for parity in (0, 1)
    }
    pitches = [
        a.last_top - b.top
        for a, b in zip(lines, lines[1:])
        if a.page == b.page and 0 < a.last_top - b.top < 60
    ]
    pitch = statistics.median(pitches) if pitches else 0.0
    prev: Optional[_Line] = None
    for ln in lines:
        body_left = margins[ln.page % 2][0]
        indented = ln.left >= body_left + 5
        if prev is None:
            ln.para_start = True
        else:
            prev_left, prev_right = margins[prev.page % 2]
            prev_width = max(prev_right - prev_left, 1.0)
            short_before = prev.right < prev_right - 0.15 * prev_width and re.search(
                r"[.!?\u2026\"'\u201d\u2019)*:]$", prev.text
            )
            gap = (
                pitch
                and prev.page == ln.page
                and (prev.last_top - ln.top) > 1.6 * pitch
            )
            ln.para_start = bool(indented or short_before or gap)
        prev = ln


def _join(lines: List[_Line], vocab: Counter) -> str:
    """Join one paragraph's lines, undoing line-end hyphenation.

    A hyphen at a line end is dropped only when the book also spells the
    joined word without one ("beau-/tiful" -> "beautiful"); otherwise it is a
    real compound ("wood-/fired", "Sung-/woon") and stays.
    """
    out = ""
    for ln in lines:
        text = ln.text
        if out.endswith(_HYPHEN_MARK) or (
            re.search(r"[a-z]-$", out) and text[:1].islower()
        ):
            out = out.rstrip(_HYPHEN_MARK).rstrip("-")
            head = re.search(r"([^\W\d_]+)$", out)
            tail = re.match(r"([^\W\d_]+)", text)
            keep = True
            if head and tail:
                joined = (head.group(1) + tail.group(1)).lower()
                hyphenated = f"{head.group(1)}-{tail.group(1)}".lower()
                keep = vocab[joined] == 0 or vocab[hyphenated] > vocab[joined]
            out += ("-" if keep else "") + text
        else:
            out = f"{out} {text}" if out else text
    return _resolve_marks(out, vocab)


def _resolve_marks(text: str, vocab: Counter) -> str:
    """U+FFFE inside a line (PDFium sometimes keeps both halves on one line)."""

    def repl(m: re.Match) -> str:
        a, b = m.group(1), m.group(2)
        joined = (a + b).lower()
        if vocab[joined] and vocab[f"{a}-{b}".lower()] <= vocab[joined]:
            return a + b
        return f"{a}-{b}"

    return re.sub(rf"([^\W\d_]+){_HYPHEN_MARK}\s*([^\W\d_]+)", repl, text).replace(
        _HYPHEN_MARK, "-"
    )


def _vocabulary(pages: List[List[_Line]]) -> Counter:
    vocab: Counter = Counter()
    for page in pages:
        for ln in page:
            for word in re.findall(r"[^\W\d_]+(?:-[^\W\d_]+)*", ln.text):
                vocab[word.lower()] += 1
    return vocab


def _bookmarks(doc: Any) -> List[Tuple[str, int]]:
    marks: List[Tuple[str, int]] = []
    for mark in doc.get_toc(max_depth=4):
        dest = mark.get_dest()
        index = dest.get_index() if dest is not None else None
        title = (mark.get_title() or "").strip()
        if index is None or not title:
            continue
        marks.append((title, index))
    # A part and its first chapter often point at the same page: keep the deeper
    # (later-listed) one so the chapter is not swallowed by the part.
    by_page: Dict[int, Tuple[str, int]] = {}
    for title, index in marks:
        by_page[index] = (title, index)
    return sorted(by_page.values(), key=lambda m: m[1])


def _number_of(title: str) -> Optional[str]:
    m = re.match(r"^\s*(?:chapter|ch\.?)\s*(\d+(?:\.\d+)?)\b", title, re.I)
    return m.group(1) if m else None


def _find_starts(
    pages: List[List[_Line]], marks: List[Tuple[str, int]]
) -> Tuple[List[_Start], _Report]:
    report = _Report(bookmarks=len(marks))
    headings: List[Tuple[int, int, str, Optional[str]]] = []
    for p, lines in enumerate(pages):
        for i, ln in enumerate(lines):
            m = HEADING_RE.match(ln.text)
            if m and (ln.para_start or i == 0):
                headings.append((p, i, m.group(1), m.group(2)))
    report.headings = len(headings)
    used = set()
    starts: List[_Start] = []
    for title, page in marks:
        number = _number_of(title)
        hit = next(
            (
                h
                for h in headings
                if h[0] == page and (number is None or h[2] == number) and h not in used
            ),
            None,
        )
        if hit:
            used.add(hit)
            report.matched += 1
            starts.append(
                _Start(
                    page,
                    hit[1],
                    f"Chapter {hit[2]}",
                    hit[3] or title,
                    hit[2],
                    True,
                    True,
                    drop_line=True,
                )
            )
        else:
            report.bookmark_only.append(title)
            starts.append(_Start(page, 0, title, title, number, False, True))
    claimed = {s.number for s in starts if s.number}
    for h in headings:
        if h in used or h[2] in claimed:
            continue
        label = f"Chapter {h[2]}"
        report.heading_only.append(label)
        starts.append(
            _Start(h[0], h[1], label, h[3] or label, h[2], True, False, drop_line=True)
        )
    starts.sort(key=lambda s: (s.page, s.line))
    return starts, report


def _cover(doc: Any) -> Optional[Tuple[bytes, str]]:
    """The first page's largest image, else the first page rendered."""
    import io

    import pypdfium2 as pdfium

    if not len(doc):
        return None
    page = doc[0]
    best, best_area = None, 0.0
    for obj in page.get_objects():
        if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE:
            left, bottom, right, top = obj.get_bounds()
            area = (right - left) * (top - bottom)
            if area > best_area:
                best, best_area = obj, area
    try:
        if best is not None:
            image = best.get_bitmap(render=True).to_pil()
        else:
            image = page.render(scale=2.0).to_pil()
    except Exception:  # a cover is a nicety: a broken image must not fail the import
        logger.warning("Could not extract a cover from the PDF", exc_info=True)
        return None
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue(), "jpg"


def read_pdf(path: Path) -> RawBook:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        meta = doc.get_metadata_dict()
        pages = [_page_lines(doc[i], i) for i in range(len(doc))]
        page_count = len(pages)
        running = _drop_running_lines(pages)
        _mark_paragraphs(pages)
        vocab = _vocabulary(pages)
        starts, report = _find_starts(pages, _bookmarks(doc))
        cover = _cover(doc)
    finally:
        doc.close()

    def paragraphs(lines: List[_Line]) -> List[str]:
        groups: List[List[_Line]] = []
        for ln in lines:
            if ln.para_start or not groups:
                groups.append([ln])
            else:
                groups[-1].append(ln)
        return [_join(g, vocab) for g in groups]

    flat: List[Tuple[int, int, _Line]] = [
        (p, i, ln) for p, lines in enumerate(pages) for i, ln in enumerate(lines)
    ]
    positions = [(p, i) for p, i, _ in flat]

    def index_of(page: int, line: int) -> int:
        import bisect

        return bisect.bisect_left(positions, (page, line))

    chapters: List[RawChapter] = []
    first = index_of(starts[0].page, starts[0].line) if starts else len(flat)
    if first > 0:
        front = [ln for _, _, ln in flat[:first]]
        chapters.append(
            RawChapter(
                "Front matter",
                "Front matter",
                None,
                paragraphs(front),
                source=f"pages 1-{flat[first - 1][0] + 1}",
                front_matter=True,
            )
        )
    for k, start in enumerate(starts):
        lo = index_of(start.page, start.line)
        hi = (
            index_of(starts[k + 1].page, starts[k + 1].line)
            if k + 1 < len(starts)
            else len(flat)
        )
        body = [ln for _, _, ln in flat[lo:hi]]
        if start.drop_line and body:
            body = body[1:]
            if body:
                body[0].para_start = True
        last_page = flat[hi - 1][0] if hi > lo else start.page
        chapters.append(
            RawChapter(
                start.label,
                start.title,
                start.number,
                paragraphs(body),
                source=f"pages {start.page + 1}-{last_page + 1}",
            )
        )
    if not starts and flat:
        chapters = [
            RawChapter(
                "Full text",
                "Full text",
                None,
                paragraphs([ln for _, _, ln in flat]),
                source="all pages",
            )
        ]

    return RawBook(
        title=(meta.get("Title") or "").strip() or path.stem,
        author=(meta.get("Author") or "").strip(),
        language="",
        chapters=chapters,
        cover=cover,
        source_format="pdf",
        pages=page_count,
        structure={
            "bookmarks": report.bookmarks,
            "headings": report.headings,
            "matched": report.matched,
            "heading_only": report.heading_only,
            "bookmark_only": report.bookmark_only,
            "running_lines_dropped": running,
        },
    )
