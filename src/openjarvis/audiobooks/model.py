"""What the PDF and EPUB readers hand the importer: chapters of raw paragraphs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class RawChapter:
    label: str  # "Chapter 32.1", or the TOC entry for unnumbered ones
    title: str
    number: Optional[str]  # "32.1"; None for "Prologue", "Copyright"...
    paragraphs: List[str]
    source: str = ""  # "pages 430-438" / "OEBPS/Chapter-033.xhtml"
    front_matter: bool = False  # text before the first TOC entry or heading
    notes: List[str] = field(default_factory=list)  # EPUB footnote asides


@dataclass
class RawBook:
    title: str
    author: str
    language: str
    chapters: List[RawChapter]
    cover: Optional[Tuple[bytes, str]]  # (image bytes, file extension)
    source_format: str  # "pdf" | "epub"
    pages: Optional[int] = None
    structure: Dict[str, Any] = field(default_factory=dict)
