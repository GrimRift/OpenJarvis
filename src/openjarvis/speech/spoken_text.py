"""Turn a full chat reply into a safe, natural spoken rendering.

TTS reads punctuation literally: a markdown table becomes "vertical bar Task
vertical bar Schedule vertical bar", headings become "hash hash", and emphasis
becomes "asterisk asterisk". Sage answers in markdown because the chat pane
renders it, so every spoken reply has to be flattened first. Values that are
useful on screen but awkward or unsafe to read aloud are replaced only in this
derived speech string; the original reply is never modified.

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
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]*)\)")
_BARE_URL = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"']+")
_WINDOWS_PATH = re.compile(
    r"(?<![\w])(?:[A-Za-z]:[\\/]+|\\{2,})[^\s<>\"|?*]+"
)
_POSIX_PATH = re.compile(r"(?<![\w:])/(?:[^/\s<>\"']+/)+[^/\s<>\"']*")
_RELATIVE_FILE_PATH = re.compile(
    r"(?<![\w./-])(?:\.{1,2}[\\/])?"
    r"(?:[A-Za-z0-9_.@+-]+[\\/])+"
    r"[A-Za-z0-9_.@+-]+\.[A-Za-z0-9]{1,10}(?![\w./-])"
)
_AUTH_CODE = re.compile(
    r"(?i)\b(?P<label>"
    r"(?:authentication|verification|security|one[- ]time|2fa|mfa)\s+"
    r"(?:code|password)|otp(?:\s+code)?"
    r")(?P<separator>\s*(?:is|:|=)\s*|\s+)"
    r"(?P<value>"
    r"(?:\d[\d -]{2,}\d|[A-Za-z0-9]{4,12}(?:-[A-Za-z0-9]{2,12}){0,3})"
    r")"
)
_UUID = re.compile(
    r"(?i)(?<![A-F0-9])"
    r"[A-F0-9]{8}-[A-F0-9]{4}-[1-5A-F0-9][A-F0-9]{3}-"
    r"[89AB0-9][A-F0-9]{3}-[A-F0-9]{12}"
    r"(?![A-F0-9])"
)
_CONTEXT_IDENTIFIER = re.compile(
    r"(?i)\b(?P<label>id|identifier|token|key|reference|session)"
    r"(?P<separator>\s*(?:is|:|=)?\s*)"
    r"(?P<value>[A-Za-z0-9][A-Za-z0-9._-]{6,}[A-Za-z0-9])"
)
_LONG_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9][A-Za-z0-9_-]{18,}[A-Za-z0-9]"
    r"(?![A-Za-z0-9_-])"
)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BULLET = re.compile(r"^\s*[-*+•]\s+", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)
_RULE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_BLANK_RUN = re.compile(r"\n{3,}")

_TRAILING_VALUE_PUNCTUATION = ".,;:!?)]}"


def _flatten_table_row(line: str) -> str:
    """Read a table row as its cells, so the bars are never spoken."""
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    spoken = ", ".join(cell for cell in cells if cell)
    if not spoken:
        return ""
    return spoken if spoken.endswith((".", "!", "?", ":")) else spoken + "."


def _looks_like_identifier(value: str) -> bool:
    """Reject long ordinary words while retaining token-like values."""
    has_letter = any(char.isalpha() for char in value)
    has_digit = any(char.isdigit() for char in value)
    has_separator = any(char in "_-" for char in value)
    is_long_hex = len(value) >= 16 and has_digit and all(
        char in "0123456789abcdefABCDEF" for char in value
    )
    return is_long_hex or (
        has_letter and has_digit and (len(value) >= 20 or has_separator)
    )


def _split_trailing_punctuation(value: str) -> tuple[str, str]:
    trailing = ""
    while value and value[-1] in _TRAILING_VALUE_PUNCTUATION:
        trailing = value[-1] + trailing
        value = value[:-1]
    return value, trailing


def _looks_like_file_path(value: str) -> bool:
    value = value.strip()
    if re.match(r"^(?:[A-Za-z]:[\\/]|\\{2,})", value):
        return True
    if value.startswith("/") and "/" in value[1:]:
        return True
    return bool(
        re.match(
            r"^(?:\.{1,2}[\\/])?(?:[^\\/]+[\\/])+[^\\/]+\.[A-Za-z0-9]{1,10}$",
            value,
        )
    )


def to_spoken_text(markdown: str) -> str:
    """Create speech-only prose without changing the source chat reply."""
    if not markdown:
        return ""

    hidden_value_count = 0

    def hide_value(replacement: str, trailing: str = "") -> str:
        nonlocal hidden_value_count
        hidden_value_count += 1
        return replacement + trailing

    def replace_link(match: re.Match[str]) -> str:
        label, target = match.groups()
        if re.match(r"(?i)^(?:https?://|www\.)", target.strip()):
            hide_value("")
        return label

    def replace_bare_value(match: re.Match[str], replacement: str) -> str:
        _value, trailing = _split_trailing_punctuation(match.group(0))
        return hide_value(replacement, trailing)

    def replace_inline_code(match: re.Match[str]) -> str:
        value = match.group(1)
        if _looks_like_file_path(value):
            return hide_value("a file path")
        return value

    def replace_auth_code(match: re.Match[str]) -> str:
        return (
            match.group("label")
            + match.group("separator")
            + hide_value("the authentication code")
        )

    def replace_context_identifier(match: re.Match[str]) -> str:
        value = match.group("value")
        is_long_number = len(value) >= 8 and value.isdigit()
        if not (is_long_number or _looks_like_identifier(value)):
            return match.group(0)
        return (
            match.group("label")
            + match.group("separator")
            + hide_value("an identifier")
        )

    def replace_long_identifier(match: re.Match[str]) -> str:
        value = match.group(0)
        if not _looks_like_identifier(value):
            return value
        return hide_value("an identifier")

    text = _FENCED_CODE.sub(" ", markdown)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(replace_link, text)
    text = _INLINE_CODE.sub(replace_inline_code, text)

    # Sanitize before emphasis processing because token-like identifiers often
    # contain underscores, which markdown otherwise consumes as formatting.
    text = _BARE_URL.sub(lambda match: replace_bare_value(match, "a link"), text)
    text = _WINDOWS_PATH.sub(
        lambda match: replace_bare_value(match, "a file path"), text
    )
    text = _POSIX_PATH.sub(
        lambda match: replace_bare_value(match, "a file path"), text
    )
    text = _RELATIVE_FILE_PATH.sub(
        lambda match: replace_bare_value(match, "a file path"), text
    )
    text = _AUTH_CODE.sub(replace_auth_code, text)
    text = _UUID.sub(lambda match: hide_value("an identifier"), text)
    text = _CONTEXT_IDENTIFIER.sub(replace_context_identifier, text)
    text = _LONG_IDENTIFIER.sub(replace_long_identifier, text)

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
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if hidden_value_count:
        noun = "value is" if hidden_value_count == 1 else "values are"
        notice = f"The exact {noun} visible in chat."
        text = f"{text}\n\n{notice}" if text else notice
    return text


__all__ = ["to_spoken_text"]
