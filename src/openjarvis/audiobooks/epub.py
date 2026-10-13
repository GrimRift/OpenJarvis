"""Reading an EPUB book into chapters of raw paragraphs.

Chapters are the table of contents (EPUB 3 nav, else the EPUB 2 NCX) laid
over the spine: each entry runs from its document (and ``#fragment``, if it
points into the middle of one) to the next entry. Spine documents before the
first entry are front matter. With no usable TOC, each spine document is a
chapter. Standard library only: the archive is a zip of XHTML.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from openjarvis.audiobooks.model import RawBook, RawChapter

_BLOCK_TAGS = {
    "p",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "blockquote",
    "br",
    "hr",
    "tr",
    "section",
    "article",
    "aside",
    "pre",
    "dt",
    "dd",
    "figcaption",
}
_SKIP_TAGS = {"script", "style", "head", "title", "svg", "math"}
_NOTE_TYPES = {"footnote", "endnote", "rearnote", "note"}


class _Blocks(HTMLParser):
    """Paragraph texts of one XHTML document, with fragment ids located.

    ``anchors`` maps an element id to the index of the paragraph it opens, so
    a TOC entry pointing at ``chapter.xhtml#part2`` can split the document.
    Footnote asides go to ``notes``; note-reference superscripts are dropped.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: List[str] = []
        self.notes: List[str] = []
        self.anchors: Dict[str, int] = {}
        self._buf: List[str] = []
        self._skip = 0
        self._note_depth = 0
        self._note_buf: List[str] = []
        self._stack: List[str] = []

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        if text:
            self.blocks.append(text)
        self._buf = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        etype = a.get("epub:type", "") + " " + a.get("role", "").replace("doc-", "")
        if tag in ("br", "hr", "img", "meta", "link", "input", "col", "wbr"):
            if tag in ("br", "hr") and not self._skip and not self._note_depth:
                self._flush()
                if tag == "hr":  # a rule between paragraphs is a scene break
                    self.blocks.append("***")
            return
        self._stack.append(tag)
        if self._skip or tag in _SKIP_TAGS:
            self._skip += 1
            return
        if "noteref" in etype or (tag == "sup" and not self._note_depth):
            self._skip += 1
            return
        if self._note_depth or (tag == "aside" and _NOTE_TYPES & set(etype.split())):
            if not self._note_depth:
                self._flush()
            self._note_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        if a.get("id") and a["id"] not in self.anchors:
            self.anchors[a["id"]] = len(self.blocks)

    def handle_endtag(self, tag: str) -> None:
        if tag not in self._stack:
            return
        while self._stack:
            open_tag = self._stack.pop()
            if self._skip:
                self._skip -= 1
            elif self._note_depth:
                self._note_depth -= 1
                if not self._note_depth:
                    note = re.sub(r"\s+", " ", "".join(self._note_buf)).strip()
                    if note:
                        self.notes.append(note)
                    self._note_buf = []
            elif open_tag in _BLOCK_TAGS:
                self._flush()
            if open_tag == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        (self._note_buf if self._note_depth else self._buf).append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _parse_html(data: bytes) -> _Blocks:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    parser = _Blocks()
    parser.feed(text)
    parser.close()
    return parser


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first(root: ET.Element, name: str) -> Optional[ET.Element]:
    return next((el for el in root.iter() if _local(el.tag) == name), None)


def _toc_from_nav(z: zipfile.ZipFile, path: str) -> List[Tuple[str, str]]:
    root = ET.fromstring(z.read(path))
    base = posixpath.dirname(path)
    navs = [el for el in root.iter() if _local(el.tag) == "nav"]
    toc = next(
        (
            n
            for n in navs
            if any(k.endswith("type") and "toc" in v for k, v in n.attrib.items())
        ),
        navs[0] if navs else None,
    )
    entries: List[Tuple[str, str]] = []
    if toc is None:
        return entries
    for a in toc.iter():
        if _local(a.tag) == "a" and a.get("href"):
            label = re.sub(r"\s+", " ", "".join(a.itertext())).strip()
            entries.append(
                (
                    label,
                    posixpath.normpath(
                        posixpath.join(base, unquote(a.get("href", "")))
                    ),
                )
            )
    return entries


def _toc_from_ncx(z: zipfile.ZipFile, path: str) -> List[Tuple[str, str]]:
    root = ET.fromstring(z.read(path))
    base = posixpath.dirname(path)
    entries: List[Tuple[str, str]] = []
    for point in root.iter():
        if _local(point.tag) != "navPoint":
            continue
        label_el = _first(point, "text")
        content = next((c for c in point if _local(c.tag) == "content"), None)
        if content is None or not content.get("src"):
            continue
        label = re.sub(
            r"\s+", " ", (label_el.text if label_el is not None else "") or ""
        ).strip()
        entries.append(
            (
                label,
                posixpath.normpath(
                    posixpath.join(base, unquote(content.get("src", "")))
                ),
            )
        )
    return entries


def _number_of(label: str) -> Optional[str]:
    m = re.match(r"^\s*(?:chapter|ch\.?)\s*(\d+(?:\.\d+)?)\b", label, re.I)
    return m.group(1) if m else None


def read_epub(path: Path) -> RawBook:
    with zipfile.ZipFile(path) as z:
        container = ET.fromstring(z.read("META-INF/container.xml"))
        rootfile = _first(container, "rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            raise ValueError("EPUB has no package document")
        opf_path = rootfile.get("full-path", "")
        base = posixpath.dirname(opf_path)
        opf = ET.fromstring(z.read(opf_path))

        def meta(name: str) -> str:
            el = _first(opf, name)
            return re.sub(r"\s+", " ", el.text or "").strip() if el is not None else ""

        creators = [
            re.sub(r"\s+", " ", el.text or "").strip()
            for el in opf.iter()
            if _local(el.tag) == "creator" and (el.text or "").strip()
        ]
        manifest: Dict[str, Tuple[str, str, str]] = {}
        manifest_el = _first(opf, "manifest")
        for item in manifest_el if manifest_el is not None else []:
            href = posixpath.normpath(
                posixpath.join(base, unquote(item.get("href", "")))
            )
            manifest[item.get("id", "")] = (
                href,
                item.get("media-type", ""),
                item.get("properties", ""),
            )
        spine_el = _first(opf, "spine")
        spine = [
            manifest[ref.get("idref", "")][0]
            for ref in (spine_el if spine_el is not None else [])
            if ref.get("idref", "") in manifest
        ]

        # Table of contents: EPUB 3 nav first, then the NCX.
        entries: List[Tuple[str, str]] = []
        nav = next(
            (h for h, _, props in manifest.values() if "nav" in props.split()), None
        )
        if nav:
            entries = _toc_from_nav(z, nav)
        if not entries:
            ncx_id = spine_el.get("toc") if spine_el is not None else None
            ncx = manifest.get(ncx_id or "", (None,))[0] or next(
                (
                    h
                    for h, mt, _ in manifest.values()
                    if mt == "application/x-dtbncx+xml"
                ),
                None,
            )
            if ncx:
                entries = _toc_from_ncx(z, ncx)

        # Cover: EPUB 3 property, EPUB 2 <meta name="cover">, then an image named cover.
        cover_href = next(
            (h for h, _, p in manifest.values() if "cover-image" in p.split()), None
        )
        if not cover_href:
            cover_meta = next(
                (
                    el.get("content")
                    for el in opf.iter()
                    if _local(el.tag) == "meta" and el.get("name") == "cover"
                ),
                None,
            )
            if cover_meta in manifest and manifest[cover_meta][1].startswith("image/"):
                cover_href = manifest[cover_meta][0]
        if not cover_href:
            cover_href = next(
                (
                    h
                    for i, (h, mt, _) in manifest.items()
                    if mt.startswith("image/") and "cover" in (i + h).lower()
                ),
                None,
            )
        cover = None
        if cover_href and cover_href in z.namelist():
            ext = posixpath.splitext(cover_href)[1].lstrip(".").lower() or "jpg"
            cover = (z.read(cover_href), "jpg" if ext == "jpeg" else ext)

        docs: Dict[str, _Blocks] = {}
        for href in spine:
            if href in z.namelist():
                docs[href] = _parse_html(z.read(href))

    spine = [h for h in spine if h in docs]
    position = {h: i for i, h in enumerate(spine)}
    # Each TOC entry -> (spine index, paragraph index) where it starts.
    starts: List[Tuple[int, int, str]] = []
    for label, target in entries:
        href, _, frag = target.partition("#")
        if href not in position:
            continue
        para = docs[href].anchors.get(frag, 0) if frag else 0
        key = (position[href], para)
        if starts and key <= starts[-1][:2]:
            continue  # nested or repeated entries pointing at the same spot
        starts.append((key[0], key[1], label))
    if not starts:
        starts = [
            (i, 0, posixpath.splitext(posixpath.basename(h))[0])
            for i, h in enumerate(spine)
        ]

    def collect(
        lo: Tuple[int, int], hi: Tuple[int, int]
    ) -> Tuple[List[str], List[str], List[str]]:
        paras: List[str] = []
        notes: List[str] = []
        files: List[str] = []
        for s in range(lo[0], min(hi[0], len(spine) - 1) + 1):
            blocks = docs[spine[s]]
            a = lo[1] if s == lo[0] else 0
            b = hi[1] if s == hi[0] else len(blocks.blocks)
            if a < b:
                paras.extend(blocks.blocks[a:b])
                files.append(spine[s])
            if a == 0:
                notes.extend(blocks.notes)
        return paras, notes, files

    chapters: List[RawChapter] = []
    if starts[0][:2] > (0, 0):
        paras, notes, files = collect((0, 0), starts[0][:2])
        if paras:
            chapters.append(
                RawChapter(
                    "Front matter",
                    "Front matter",
                    None,
                    paras,
                    source=", ".join(files),
                    front_matter=True,
                    notes=notes,
                )
            )
    for k, (s, p, label) in enumerate(starts):
        hi = starts[k + 1][:2] if k + 1 < len(starts) else (len(spine) - 1, 10**9)
        paras, notes, files = collect((s, p), hi)
        # The document's own heading repeats the TOC label: not narrated twice.
        if paras and _same_heading(paras[0], label):
            paras = paras[1:]
        number = _number_of(label)
        chapters.append(
            RawChapter(
                label or f"Section {k + 1}",
                label or f"Section {k + 1}",
                number,
                paras,
                source=", ".join(files),
                notes=notes,
            )
        )

    return RawBook(
        title=meta("title") or path.stem,
        author=", ".join(creators),
        language=meta("language"),
        chapters=chapters,
        cover=cover,
        source_format="epub",
        structure={"toc_entries": len(entries), "spine_documents": len(spine)},
    )


def _same_heading(paragraph: str, label: str) -> bool:
    def norm(s: str) -> str:
        return re.sub(r"[\W_]+", " ", s).strip().lower()

    p, lab = norm(paragraph), norm(label)
    return bool(p) and (p == lab or (len(p) <= 80 and bool(lab) and p.startswith(lab)))
