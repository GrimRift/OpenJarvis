"""Reading a document well enough to answer questions about it.

A text-layer extractor is right most of the time and catastrophically wrong
some of the time, and the failure is silent -- it returns plenty of characters,
so nothing downstream can tell. Measured on the user's own files: of five real
documents, four extract cleanly, while a two-column journal paper comes back
with table rows spliced into body prose::

    FeO 11.0 7.1 3.5 12.41 4.20 concrete construction ranging from 10 to 50%

subscripts orphaned onto their own lines (``NaO`` then ``2`` for Na2O), and
word spacing destroyed (``stratedbyadropindensityandincreasedwaterabsorption``).
A model reading that attributes composition values to prose.

So the cheap path runs first and a second look is bought only when it is
needed -- the same shape as the bounded search escalating on thin evidence,
and ``web_search`` handing off to ``web_read`` when a summary cannot answer.
"""

from __future__ import annotations

import concurrent.futures
import contextvars
import io
import logging
import re
import statistics
from dataclasses import dataclass
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

#: Alphabetic runs no human writes. The signature of lost inter-word spacing.
_GLUED = re.compile(r"[A-Za-z]{25,}")

#: Glued runs per 1000 characters above which the text is not trustworthy.
#:
#: Measured: the two-column paper scores 6.97, every other real document
#: <= 0.02. The threshold sits in a ~350x gap, so it is not delicate.
GLUED_PER_1K_LIMIT = 1.0

#: Mean word length is recorded but is NOT a trigger, and that was measured
#: rather than assumed. At document level it looked like a good second signal
#: (paper 9.9, others 4.0-6.7), but per page it fires on any table of numbers:
#: pages 8-14 of a lab report scored 15 to 26 with *zero* glued runs, because
#: measurement codes and long decimals are legitimately long tokens. Using it
#: would have sent 12 clean pages of a 22-page report to the vision model.
MEAN_WORD_RECORDED_ONLY = True

#: Mean characters per page below which the *document* has no usable text
#: layer -- a scan. Judged across the document, never per page: a nearly empty
#: page inside a healthy document is a figure or a section divider, and there
#: is nothing for a second look to recover. Judging per page flagged 48 pages
#: of a 2,061-page novel. Measured: the thinnest real document still averaged
#: 706 characters per page.
SCANNED_CHARS_PER_PAGE = 120

#: Pages one document may send to the vision model. The user's cap.
MAX_VISION_PAGES = 20

#: Output budget for transcribing one page, and the retry behind it.
#:
#: The vision defaults are tuned for "describe this screenshot in four
#: bullets". Transcribing a dense two-column page is the opposite job: ~1,000
#: words of output plus the reasoning to lay it out. At the default 3,000 the
#: model returned *nothing* for 1-2 pages of a 15-page paper on every run --
#: no exception, no rate limit, just an empty answer, which is the documented
#: "spent the budget thinking" failure.
PAGE_MAX_TOKENS = 8000
PAGE_RETRY_MAX_TOKENS = 16000

#: How many pages are read at once.
#:
#: Measured at 31.1s for a single page of a dense two-column paper, so the
#: 20-page cap would be ten minutes of staring at an upload spinner. The pages
#: are independent, so they are read concurrently; four keeps the wait near a
#: minute without opening twenty simultaneous requests to the provider.
VISION_CONCURRENCY = 4

#: Long edge of a rendered page. Small enough to keep the request sane, large
#: enough that body text in a two-column paper stays legible.
RENDER_MAX_EDGE = 1600

#: pypdfium2 renders at 72dpi * scale. 2.0 gives ~144dpi, which is readable
#: for 9pt body text without producing an enormous image.
RENDER_SCALE = 2.0

_PAGE_QUESTION = (
    "Transcribe this page faithfully as Markdown. Preserve reading order "
    "across columns, keep tables as Markdown tables, and keep subscripts and "
    "superscripts with the symbol they belong to. Output only the transcription."
)


@dataclass
class PageQuality:
    """What the cheap extractor produced for one page, and whether to trust it."""

    index: int
    text: str
    glued_per_1k: float
    mean_word: float
    document_scanned: bool = False

    @property
    def scanned(self) -> bool:
        """Whether this page is unreadable because the document is a scan."""
        return self.document_scanned

    @property
    def mangled(self) -> bool:
        """Glued runs only. See MEAN_WORD_RECORDED_ONLY for why not word length."""
        return self.glued_per_1k >= GLUED_PER_1K_LIMIT

    @property
    def needs_eyes(self) -> bool:
        return self.scanned or self.mangled


def assess_page(index: int, text: str) -> PageQuality:
    """Judge one page's extraction without reading its meaning."""
    body = text or ""
    words = body.split()
    glued = len(_GLUED.findall(body))
    per_1k = round(glued / (len(body) / 1000), 2) if body else 0.0
    mean_word = round(statistics.mean(len(word) for word in words), 1) if words else 0.0
    return PageQuality(index=index, text=body, glued_per_1k=per_1k, mean_word=mean_word)


def assess(pages: List[str]) -> List[PageQuality]:
    """Judge every page, with "is this a scan" decided across the document."""
    if not pages:
        return []
    mean_chars = sum(len(text or "") for text in pages) / len(pages)
    scanned = mean_chars < SCANNED_CHARS_PER_PAGE
    judged = [assess_page(i, text) for i, text in enumerate(pages)]
    for page in judged:
        page.document_scanned = scanned
    return judged


def render_pages(data: bytes, indices: List[int], max_edge: int) -> List[Any]:
    """Render the given zero-based page indices to PIL images.

    ``pypdfium2`` and Pillow are both already installed, which is the reason
    this path costs no new dependency at all -- the alternative considered was
    Docling, two neural models and a first-use download, to fix one document
    class in five.
    """
    import pypdfium2 as pdfium

    from openjarvis.vision.ask import downscale

    images: List[Any] = []
    document = pdfium.PdfDocument(io.BytesIO(data))
    try:
        for index in indices:
            if index < 0 or index >= len(document):
                continue
            page = document[index]
            bitmap = page.render(scale=RENDER_SCALE)
            images.append(downscale(bitmap.to_pil(), max_edge))
    finally:
        document.close()
    return images


def read_pages_with_vision(
    data: bytes,
    pages: List[PageQuality],
    *,
    config: Any = None,
    engine: Any = None,
    max_pages: int = MAX_VISION_PAGES,
    max_edge: int = RENDER_MAX_EDGE,
) -> tuple[dict, str]:
    """Re-read the untrustworthy pages by looking at them.

    Returns ``{page_index: markdown}`` plus a note for the caller describing
    what was and was not done, because a silently partial read is the failure
    this whole module exists to stop.
    """
    from openjarvis.vision.ask import ask_vision, resolve_vision_model, to_data_url

    wanted = [page.index for page in pages if page.needs_eyes]
    if not wanted:
        return {}, ""

    capped = wanted[:max_pages]
    skipped = len(wanted) - len(capped)

    if config is None:
        try:
            from openjarvis.core.config import load_config

            config = load_config()
        except Exception:
            config = None
    model = resolve_vision_model(config)

    results: dict = {}
    try:
        images = render_pages(data, capped, max_edge)
    except Exception:
        logger.warning("Could not render pages for a second look", exc_info=True)
        return {}, "\n\n[Some pages could not be re-read; text may be garbled.]"

    def _read(index: int, image: Any) -> tuple:
        try:
            return index, ask_vision(
                to_data_url(image),
                _PAGE_QUESTION,
                model=model,
                engine=engine,
                max_tokens=PAGE_MAX_TOKENS,
                retry_max_tokens=PAGE_RETRY_MAX_TOKENS,
            )
        except Exception:
            logger.warning("Vision read failed for page %s", index + 1, exc_info=True)
            return index, None

    # Each submission carries a copy of the calling context: contextvars do
    # not cross `ThreadPoolExecutor.submit` the way they cross
    # `asyncio.to_thread`, and a tool losing its per-request state that way
    # cost real debugging on 2026-09-03.
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(VISION_CONCURRENCY, len(capped))
    ) as pool:
        futures = [
            pool.submit(contextvars.copy_context().run, _read, index, image)
            for index, image in zip(capped, images)
        ]
        for future in concurrent.futures.as_completed(futures):
            index, answer = future.result()
            if answer:
                results[index] = answer

    note = ""
    if results:
        note = (
            f"\n\n[{len(results)} page(s) were re-read by looking at them, "
            "because the text layer came out garbled or absent.]"
        )
    if skipped:
        note += (
            f"\n\n[{skipped} further page(s) needed the same treatment but the "
            f"limit is {max_pages} pages per document.]"
        )
    return results, note


def assemble(pages: List[PageQuality], replacements: Optional[dict] = None) -> str:
    """The document as text, preferring a re-read page where one exists."""
    swaps = replacements or {}
    return "\n\n".join(swaps.get(page.index, page.text) for page in pages).strip()


__all__ = [
    "GLUED_PER_1K_LIMIT",
    "MAX_VISION_PAGES",
    "PageQuality",
    "SCANNED_CHARS_PER_PAGE",
    "assemble",
    "assess",
    "assess_page",
    "read_pages_with_vision",
    "render_pages",
]
