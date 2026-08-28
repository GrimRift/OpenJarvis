"""Turn a markdown reply into something worth hearing.

TTS reads punctuation literally: a markdown table becomes "vertical bar Task
vertical bar Schedule vertical bar", headings become "hash hash", and emphasis
becomes "asterisk asterisk". Sage answers in markdown because the chat pane
renders it, so every spoken reply has to be flattened first.

Tables are the reason this exists as a shared function rather than three
copies of a regex: the digest stripped headings, bullets and emphasis but not
tables, and the two server speech paths stripped nothing at all.
"""

from __future__ import annotations

import re

# A row of only pipes, dashes, colons and spaces — the bar under a table's
# header. Spoken aloud it is a long run of "dash".
_TABLE_DIVIDER = re.compile(r"^\s*\|?[\s:|-]*\|[\s:|-]*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")

_FENCED_CODE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BULLET = re.compile(r"^\s*[-*+•]\s+", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)
_RULE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_BLANK_RUN = re.compile(r"\n{3,}")


def _flatten_table_row(line: str) -> str:
    """Read a table row as its cells, so the bars are never spoken."""
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    spoken = ", ".join(cell for cell in cells if cell)
    if not spoken:
        return ""
    return spoken if spoken.endswith((".", "!", "?", ":")) else spoken + "."


def to_spoken_text(markdown: str) -> str:
    """Flatten markdown into plain prose for a speech backend."""
    if not markdown:
        return ""

    text = _FENCED_CODE.sub(" ", markdown)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)

    lines: list[str] = []
    for line in text.splitlines():
        if _TABLE_DIVIDER.match(line) and "|" in line:
            continue
        if _TABLE_ROW.match(line):
            flattened = _flatten_table_row(line)
            if flattened:
                lines.append(flattened)
            continue
        lines.append(line)
    text = "\n".join(lines)

    text = _RULE.sub("", text)
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _BLOCKQUOTE.sub("", text)
    # Applied after the line rules so a bullet's "*" is already gone and
    # cannot be mistaken for the opening of an emphasis span.
    text = _EMPHASIS.sub(r"\2", text)

    text = _BLANK_RUN.sub("\n\n", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


__all__ = ["to_spoken_text"]
