"""Tiny synthetic books for the importer tests: a PDF and an EPUB writer.

The PDF writer emits positioned lines in Helvetica (WinAnsi, so curly quotes
and superscript one work) with an optional outline, which is all the PDF
reader looks at: line text, x position, y position and bookmarks.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

Line = Tuple[float, float, str]  # (x, y, text)


def _pdf_string(text: str) -> bytes:
    out = bytearray(b"(")
    for byte in text.encode("cp1252"):
        if byte in b"()\\":
            out += b"\\" + bytes([byte])
        elif byte < 32 or byte > 126:
            out += b"\\%03o" % byte
        else:
            out.append(byte)
    return bytes(out + b")")


def make_pdf(
    path: Path,
    pages: Sequence[Sequence[Line]],
    outline: Sequence[Tuple[str, int]] = (),
    info: Dict[str, str] | None = None,
) -> Path:
    objects: List[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # filled in last
    pages_id = add(b"")
    font = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )
    page_ids = []
    for lines in pages:
        stream = b"".join(
            b"BT /F1 11 Tf %.1f %.1f Td %s Tj ET\n" % (x, y, _pdf_string(text))
            for x, y, text in lines
        )
        content = add(
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
        )
        page_ids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                % (pages_id, font, content)
            )
        )
    objects[pages_id - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % p for p in page_ids),
        len(page_ids),
    )
    outline_ref = b""
    if outline:
        root = add(b"")
        items = [add(b"") for _ in outline]
        for k, ((title, page), item) in enumerate(zip(outline, items)):
            links = b""
            if k:
                links += b" /Prev %d 0 R" % items[k - 1]
            if k + 1 < len(items):
                links += b" /Next %d 0 R" % items[k + 1]
            objects[item - 1] = (
                b"<< /Title %s /Parent %d 0 R /Dest [%d 0 R /XYZ 0 792 0]%s >>"
                % (_pdf_string(title), root, page_ids[page], links)
            )
        objects[root - 1] = (
            b"<< /Type /Outlines /First %d 0 R /Last %d 0 R /Count %d >>"
            % (items[0], items[-1], len(items))
        )
        outline_ref = b" /Outlines %d 0 R" % root
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R%s >>" % (
        pages_id,
        outline_ref,
    )
    info_id = add(
        b"<< %s >>"
        % b" ".join(
            b"/%s %s" % (k.encode(), _pdf_string(v)) for k, v in (info or {}).items()
        )
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += (
        b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, catalog, info_id, xref)
    )
    path.write_bytes(bytes(out))
    return path


def make_epub(
    path: Path,
    docs: Sequence[Tuple[str, str]],
    toc: Sequence[Tuple[str, str]],
    *,
    title: str = "Synthetic Book",
    author: str = "A. Writer",
    language: str = "en",
    cover: bytes | None = None,
    epub3: bool = True,
) -> Path:
    """docs: (file name, <body> inner XHTML); toc: (label, href with optional #frag)."""
    manifest = [
        f'<item id="d{i}" href="{name}" media-type="application/xhtml+xml"/>'
        for i, (name, _) in enumerate(docs)
    ]
    if cover is not None:
        props = ' properties="cover-image"' if epub3 else ""
        manifest.append(
            '<item id="cover-img" href="images/cover.jpg" '
            f'media-type="image/jpeg"{props}/>'
        )
    if epub3:
        manifest.append(
            '<item id="nav" href="nav.xhtml" '
            'media-type="application/xhtml+xml" properties="nav"/>'
        )
    else:
        manifest.append(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        )
    refs = "".join(f'<itemref idref="d{i}"/>' for i in range(len(docs)))
    cover_meta = (
        '<meta name="cover" content="cover-img"/>'
        if cover is not None and not epub3
        else ""
    )
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{"3.0" if epub3 else "2.0"}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title><dc:creator>{author}</dc:creator>
    <dc:language>{language}</dc:language>{cover_meta}
  </metadata>
  <manifest>{"".join(manifest)}</manifest>
  <spine{"" if epub3 else ' toc="ncx"'}>{refs}</spine>
</package>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        z.writestr("OEBPS/content.opf", opf)
        for name, body in docs:
            z.writestr(
                f"OEBPS/{name}",
                '<?xml version="1.0" encoding="utf-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml" '
                'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>x</title>'
                f"<style>p {{ margin: 0 }}</style></head><body>{body}</body></html>",
            )
        if cover is not None:
            z.writestr("OEBPS/images/cover.jpg", cover)
        if epub3:
            items = "".join(
                f'<li><a href="{href}">{label}</a></li>' for label, href in toc
            )
            z.writestr(
                "OEBPS/nav.xhtml",
                '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" '
                'xmlns:epub="http://www.idpf.org/2007/ops"><body>'
                f'<nav epub:type="toc"><ol>{items}</ol></nav></body></html>',
            )
        else:
            points = "".join(
                f'<navPoint id="n{i}" playOrder="{i + 1}">'
                f"<navLabel><text>{label}</text></navLabel>"
                f'<content src="{href}"/></navPoint>'
                for i, (label, href) in enumerate(toc)
            )
            z.writestr(
                "OEBPS/toc.ncx",
                '<?xml version="1.0"?>'
                '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
                f'version="2005-1"><navMap>{points}</navMap></ncx>',
            )
    return path
