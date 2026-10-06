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

from openjarvis.speech.spoken_math import speak_math

# A row of only pipes, dashes, colons and spaces — the bar under a table's
# header. Spoken aloud it is a long run of "dash".
_TABLE_DIVIDER = re.compile(r"^\s*\|?[\s:|-]*\|[\s:|-]*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")

_FENCED_CODE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]*)\)")
_BARE_URL = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"']+")
_WINDOWS_PATH = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/]+|\\{2,})[^\s<>\"|?*]+")
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
#: An order number, tracking code or the like: 10+ characters mixing
#: letters and digits, or 8+ digits, optionally after "#". Read letter by
#: letter it ran past the voice's time limit and was cut mid-word ("Order
#: 260920HXVD7SX ... by Sep-", 25 September); spoken as its last three
#: characters, the screen keeps the whole of it.
_CODE_LABEL = (
    r"(?:order|tracking|invoice|booking|ticket|reference|ref|receipt|parcel"
    r"|shipment|transaction|confirmation|code)"
)
_LONG_CODE = re.compile(
    rf"(?i)(?P<label>\b{_CODE_LABEL}(?:\s+(?:no\.?|number|code|id))?\s*:?\s*)?"
    r"#?(?P<code>(?=[A-Za-z0-9]*\d)(?:(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{10,19}|\d{8,19}))\b"
)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BULLET = re.compile(r"^\s*[-*+•]\s+", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)
_RULE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_BLANK_RUN = re.compile(r"\n{3,}")

_TRAILING_VALUE_PUNCTUATION = ".,;:!?)]}"


class SpokenTextOverflow(ValueError):
    """Raised when an unfinished speech segment exceeds its memory bound."""


def _has_unfinished_emphasis(text: str) -> bool:
    """Detect emphasis delimiters that still need a closing model delta."""
    without_bullets = re.sub(r"^\s*[-*+•]\s+", "", text, flags=re.MULTILINE)
    without_complete = _EMPHASIS.sub("", without_bullets)
    return bool(
        re.search(
            r"(?<!\w)(?:\*{1,3}|_{1,3})(?=\S)"
            r"|(?<=\S)(?:\*{1,3}|_{1,3})(?!\w)",
            without_complete,
        )
    )


def _completed_speech_boundaries(text: str) -> list[int]:
    """Return stable sentence/clause ends outside URLs and Markdown spans.

    A boundary is only stable once whitespace after its punctuation has
    arrived. This is deliberately conservative: delaying one sentence is
    harmless, while releasing half of a URL, path, code, identifier, or
    Markdown target can make a value audible before the sanitizer sees it.
    """
    boundaries: list[int] = []
    fence = ""
    inline_code = False
    link_label_depth = 0
    link_target_depth = 0
    unsafe_token = False
    token_start = 0
    segment_start = 0
    index = 0

    while index < len(text):
        if not inline_code and not fence and text.startswith("```", index):
            fence = "```"
            index += 3
            continue
        if not inline_code and not fence and text.startswith("~~~", index):
            fence = "~~~"
            index += 3
            continue
        if fence:
            if text.startswith(fence, index):
                index += len(fence)
                fence = ""
            else:
                index += 1
            continue
        char = text[index]
        if char == "`":
            inline_code = not inline_code
            index += 1
            continue
        if inline_code:
            index += 1
            continue

        if char == "[" and link_target_depth == 0:
            link_label_depth += 1
        elif char == "]" and link_label_depth:
            link_label_depth -= 1
            if index + 1 < len(text) and text[index + 1] == "(":
                link_target_depth = 1
                index += 2
                continue
        elif link_target_depth:
            if char == "(":
                link_target_depth += 1
            elif char == ")":
                link_target_depth -= 1
                if link_target_depth == 0:
                    # The URL/path inside the Markdown target is complete;
                    # punctuation after the closing parenthesis belongs to
                    # the prose and may safely end a sentence.
                    unsafe_token = False
                    token_start = index + 1

        if char.isspace():
            unsafe_token = False
            token_start = index + 1
        elif index == token_start:
            unsafe_token = char in "/\\" or (
                index + 2 < len(text)
                and char.isalpha()
                and text[index + 1] == ":"
                and text[index + 2] in "/\\"
            )
        elif not unsafe_token:
            token = text[token_start : index + 1].lower()
            unsafe_token = "://" in token or token.startswith("www.")

        next_is_space = index + 1 < len(text) and text[index + 1].isspace()
        outside_markup = link_label_depth == 0 and link_target_depth == 0
        if next_is_space and outside_markup and not unsafe_token:
            if char in ".!?":
                if not _has_unfinished_emphasis(text[segment_start : index + 1]):
                    boundaries.append(index + 1)
                    segment_start = index + 1
            elif char in ";:" and index + 1 - segment_start >= 80:
                if not _has_unfinished_emphasis(text[segment_start : index + 1]):
                    boundaries.append(index + 1)
                    segment_start = index + 1
            elif char == "\n" and index + 1 - segment_start >= 40:
                if not _has_unfinished_emphasis(text[segment_start : index + 1]):
                    boundaries.append(index + 1)
                    segment_start = index + 1
        index += 1
    return boundaries


def _safe_final_text(text: str) -> str:
    """Drop an unfinished Markdown tail rather than reading its syntax."""
    for marker in ("```", "~~~"):
        if text.count(marker) % 2:
            text = text[: text.rfind(marker)]
    if text.count("`") % 2:
        text = text[: text.rfind("`")]
    open_label = text.rfind("[")
    close_label = text.rfind("]")
    if open_label > close_label:
        text = text[:open_label]
    open_target = text.rfind("](")
    close_target = text.rfind(")")
    if open_target > close_target:
        text = text[: text.rfind("[", 0, open_target + 1)]
    return text


_SPEAKABLE = re.compile(r"[^\W_]", re.UNICODE)


def _is_speakable(text: str) -> bool:
    """Whether a segment carries speech rather than leftover punctuation.

    A delta boundary can land so that a segment holds nothing but the
    sentence-ending mark ("Closed, Sir. " then "."). Cartesia reads that lone
    mark aloud -- the user heard "Sir dot" after a sentence that ended
    cleanly in the transcript.
    """
    return bool(_SPEAKABLE.search(text))


#: How many times the usual pending bound a table being held may reach.
TABLE_HOLD_FACTOR = 4


def _hold_tables(text: str, boundaries: list[int]) -> tuple[list[int], bool]:
    """Keep each markdown table in one segment, released once it has ended.

    Whether a table is read row by row or described depends on how many rows
    it has, and a sentence end inside a cell ("number. |") split rows across
    segments, so a table is never cut: a finished one is one segment, an
    unfinished one holds everything from its first row. Returns the
    boundaries and whether a table is still open.
    """
    blocks: list[tuple[int, int]] = []
    offset = 0
    start: int | None = None
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        stripped = line.lstrip(" \t")
        is_row = stripped.startswith("|")
        # A last line still arriving with nothing but indentation may yet
        # turn out to be a row.
        undecided = index == len(lines) - 1 and not stripped
        if is_row and start is None:
            start = offset
        elif not is_row and not undecided and start is not None:
            blocks.append((start, offset))
            start = None
        offset += len(line)

    kept = [b for b in boundaries if not any(s < b < e for s, e in blocks)]
    kept.extend(e for _s, e in blocks)
    if start is not None:
        kept = [b for b in kept if b <= start]
    return sorted(set(kept)), start is not None


class SpokenTextStream:
    """Buffer raw model deltas and release only stable sanitized speech."""

    def __init__(self, *, max_pending_chars: int = 4096) -> None:
        if max_pending_chars <= 0:
            raise ValueError("max_pending_chars must be positive")
        self._pending = ""
        self._max_pending_chars = max_pending_chars
        self._finished = False

    @property
    def pending_chars(self) -> int:
        return len(self._pending)

    def push(self, delta: str) -> list[str]:
        if self._finished or not delta:
            return []
        self._pending += delta
        boundaries, table_open = _hold_tables(
            self._pending, _completed_speech_boundaries(self._pending)
        )
        segments: list[str] = []
        consumed = 0
        for boundary in boundaries:
            raw = self._pending[consumed:boundary].strip()
            consumed = boundary
            spoken = to_spoken_text(raw)
            if spoken and _is_speakable(spoken):
                segments.append(spoken)
        if consumed:
            self._pending = self._pending[consumed:].lstrip()
        # A table is held whole until it ends, so it may be several sentences
        # long; the turn's own character cap still bounds it.
        limit = self._max_pending_chars * (TABLE_HOLD_FACTOR if table_open else 1)
        if len(self._pending) > limit:
            raise SpokenTextOverflow("unfinished speech segment too long")
        return segments

    def flush(self) -> list[str]:
        """Release what is held as if the text ended, and keep accepting
        more: the model has paused to run a tool, not finished."""
        if self._finished:
            return []
        raw = _safe_final_text(self._pending).strip()
        self._pending = ""
        spoken = to_spoken_text(raw)
        return [spoken] if spoken and _is_speakable(spoken) else []

    def finish(self) -> list[str]:
        if self._finished:
            return []
        self._finished = True
        raw = _safe_final_text(self._pending).strip()
        self._pending = ""
        spoken = to_spoken_text(raw)
        return [spoken] if spoken and _is_speakable(spoken) else []


#: Up to this many rows a table is read out, one sentence per row. A longer
#: one is described instead: reading 14 rows of a study table took longer
#: than reading it, and the user asked for short tables read, long ones
#: summarized (7 October).
SPOKEN_TABLE_MAX_ROWS = 5
#: How many rows a summary names before "and N more".
_SUMMARY_NAMED_ROWS = 3

#: A cell that is only numbers, like a problem list "4, 21, 84".
_NUMBER_LIST = re.compile(r"^\d+(?:\s*[,–-]\s*\d+)*$")
_HEADER_NUMBER = re.compile(r"(?i)^(?:#|no\.?|num\.?)$")


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _spoken_list(items: list[str]) -> str:
    """'a, b and c' -- how a list is said, not how it is typed."""
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _row_lead(header: str, cell: str) -> str:
    """The row's first cell, named by its column when it is only a number.

    "4, 21, 84" alone is three numbers out of nowhere; under a
    "Problem(s)" header it is said "Problems 4, 21 and 84".
    """
    if not header or not _NUMBER_LIST.match(cell):
        return cell
    numbers = [n.strip() for n in cell.split(",")]
    if _HEADER_NUMBER.match(header):
        name = "Number"
    else:
        name = re.sub(r"\((?:e?s)\)$", "", header).strip()
        if len(numbers) > 1 and not name.endswith("s"):
            name += "s"
    return f"{name} {_spoken_list(numbers)}"


def _row_sentence(header: list[str], cells: list[str]) -> str:
    """One row as a sentence: its lead, then what the row says about it."""
    lead = _row_lead(header[0] if header else "", cells[0]) if cells else ""
    rest = [cell for cell in cells[1:] if cell]
    if not lead:
        lead, rest = (rest[0], rest[1:]) if rest else ("", [])
    if not lead:
        return ""
    if not rest:
        return _ended(lead)
    # Short values run on with commas ("Daily at 10 PM, Aug 28"); a cell
    # that is itself an instruction gets its own sentence, or "Arithmetic
    # sequence term, STAT, then Lin" sounds like one long list.
    prose = any(len(cell.split()) >= 6 for cell in rest)
    joiner = ". " if prose else ", "
    if prose:
        rest = [cell.rstrip(".") for cell in rest]
    return _ended(f"{lead}: " + joiner.join(rest))


def _label_column(rows: list[list[str]]) -> int:
    """The column that names each row: the first not made of numbers."""
    width = max(len(row) for row in rows)
    for column in range(width):
        values = [row[column] for row in rows if column < len(row) and row[column]]
        numeric = sum(bool(_NUMBER_LIST.match(v)) for v in values)
        if values and numeric * 2 < len(values):
            return column
    return 0


def _speak_table(lines: list[str]) -> list[str]:
    """A markdown table as speech: short ones read, long ones described."""
    rows = [_table_cells(line) for line in lines if not _TABLE_DIVIDER.match(line)]
    rows = [row for row in rows if any(row)]
    has_header = len(lines) > 1 and bool(_TABLE_DIVIDER.match(lines[1]))
    header = rows.pop(0) if has_header and rows else []
    if not rows:
        return []
    if len(rows) <= SPOKEN_TABLE_MAX_ROWS:
        return [s for s in (_row_sentence(header, row) for row in rows) if s]
    column = _label_column(rows)
    labels = [
        row[column] for row in rows if column < len(row) and row[column]
    ][:_SUMMARY_NAMED_ROWS]
    more = len(rows) - len(labels)
    named = _spoken_list(labels + ([f"{more} more"] if more > 0 else []))
    return [f"The table on screen has {len(rows)} rows, covering {named}."]


#: Calculator and maths symbols as they are said. Only the speech changes;
#: the screen keeps "STAT → Lin" and "ŷ".
_SPOKEN_SYMBOLS = (
    (re.compile(r"\s*(?:→|⇒|⟶|(?<=\s)->(?=\s))\s*"), ", then "),
    (re.compile(r"°\s*['’′]\s*[\"”″]"), " degrees-minutes-seconds "),
    (re.compile("(?:ŷ|ŷ)"), "y-hat"),
    (re.compile("(?:x̂)"), "x-hat"),
    (re.compile("(?:x̄)"), "x-bar"),
    (re.compile("(?:ȳ|ȳ)"), "y-bar"),
    (re.compile(r"\bn([CP])r\b"), r"n \1 r"),
    (re.compile(r"[Σ∑]"), " sigma "),
    (re.compile(r"∫"), " integral "),
    (re.compile(r"√"), " square root of "),
    (re.compile(r"π"), " pi "),
    (re.compile(r"×"), " times "),
    (re.compile(r"÷"), " divided by "),
    (re.compile(r"±"), " plus or minus "),
    (re.compile(r"≈"), " about "),
    (re.compile(r"≤"), " less than or equal to "),
    (re.compile(r"≥"), " greater than or equal to "),
    (re.compile(r"≠"), " not equal to "),
)


def _speak_symbols(text: str) -> str:
    for pattern, words in _SPOKEN_SYMBOLS:
        text = pattern.sub(words, text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([,.:;!?)])", r"\1", text)
    return re.sub(r"([(])[ \t]+", r"\1", text)


def _shorten_code(match: re.Match[str]) -> str:
    """'Order #260920HXVD7SX' -> 'Order ending in 7 S X'."""
    tail = " ".join(match.group("code")[-3:].upper())
    label = (match.group("label") or "").strip().rstrip(":").strip()
    return f"{label} ending in {tail}" if label else f"a code ending in {tail}"


def _looks_like_identifier(value: str) -> bool:
    """Reject long ordinary words while retaining token-like values."""
    has_letter = any(char.isalpha() for char in value)
    has_digit = any(char.isdigit() for char in value)
    has_separator = any(char in "_-" for char in value)
    is_long_hex = (
        len(value) >= 16
        and has_digit
        and all(char in "0123456789abcdefABCDEF" for char in value)
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


#: A dash used as punctuation between words, spaced or not. A dash between
#: digits is deliberately not matched: "2023-2024" is a range, and a comma
#: there would be read as two separate years.
_SPEECH_DASH = re.compile(r"(?<=[^\W\d_])\s*[–—]+\s*(?=[^\W\d_])")


_LINE_END = ".!?:;,"


def _ended(line: str) -> str:
    """*line* with a full stop if it ends on a word rather than a mark."""
    if not line or line[-1] in _LINE_END or not line[-1].isalnum():
        return line
    return line + "."


def to_spoken_text(markdown: str) -> str:
    """Create speech-only prose without changing the source chat reply."""
    if not markdown:
        return ""

    hidden_counts = {
        "raw_link": 0,
        "labeled_link": 0,
        "path": 0,
        "sensitive": 0,
    }

    def hide_value(kind: str, replacement: str, trailing: str = "") -> str:
        hidden_counts[kind] += 1
        return replacement + trailing

    def replace_link(match: re.Match[str]) -> str:
        label, target = match.groups()
        if re.match(r"(?i)^(?:https?://|www\.)", target.strip()):
            hide_value("labeled_link", "")
        return label

    def replace_bare_value(match: re.Match[str], kind: str, replacement: str) -> str:
        _value, trailing = _split_trailing_punctuation(match.group(0))
        return hide_value(kind, replacement, trailing)

    def replace_inline_code(match: re.Match[str]) -> str:
        value = match.group(1)
        if _looks_like_file_path(value):
            return hide_value("path", "a file path")
        return value

    def replace_auth_code(match: re.Match[str]) -> str:
        return (
            match.group("label")
            + match.group("separator")
            + hide_value("sensitive", "the authentication code")
        )

    def replace_context_identifier(match: re.Match[str]) -> str:
        value = match.group("value")
        is_long_number = len(value) >= 8 and value.isdigit()
        if not (is_long_number or _looks_like_identifier(value)):
            return match.group(0)
        return (
            match.group("label")
            + match.group("separator")
            + hide_value("sensitive", "an identifier")
        )

    def replace_long_identifier(match: re.Match[str]) -> str:
        value = match.group(0)
        if not _looks_like_identifier(value):
            return value
        return hide_value("sensitive", "an identifier")

    text = _FENCED_CODE.sub(" ", markdown)
    # Formulas become words before anything else looks at them: the
    # identifier and path rules below would otherwise read "\sum F_x" as an
    # identifier to hide, and the dash rule would break "a - b".
    text = speak_math(text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(replace_link, text)
    text = _INLINE_CODE.sub(replace_inline_code, text)

    # Sanitize before emphasis processing because token-like identifiers often
    # contain underscores, which markdown otherwise consumes as formatting.
    text = _BARE_URL.sub(
        lambda match: replace_bare_value(match, "raw_link", "a link"), text
    )
    text = _WINDOWS_PATH.sub(
        lambda match: replace_bare_value(match, "path", "a file path"), text
    )
    text = _POSIX_PATH.sub(
        lambda match: replace_bare_value(match, "path", "a file path"), text
    )
    text = _RELATIVE_FILE_PATH.sub(
        lambda match: replace_bare_value(match, "path", "a file path"), text
    )
    text = _AUTH_CODE.sub(replace_auth_code, text)
    text = _UUID.sub(lambda match: hide_value("sensitive", "an identifier"), text)
    text = _CONTEXT_IDENTIFIER.sub(replace_context_identifier, text)
    text = _LONG_IDENTIFIER.sub(replace_long_identifier, text)
    text = _LONG_CODE.sub(_shorten_code, text)

    lines: list[str] = []
    table: list[str] = []
    for line in [*text.splitlines(), ""]:
        if _TABLE_ROW.match(line) or (_TABLE_DIVIDER.match(line) and "|" in line):
            table.append(line)
            continue
        if table:
            lines.extend(_speak_table(table))
            table = []
        lines.append(line)
    text = "\n".join(lines[:-1])

    text = _RULE.sub("", text)
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _BLOCKQUOTE.sub("", text)
    # Applied after the line rules so a bullet's "*" is already gone and
    # cannot be mistaken for the opening of an emphasis span.
    text = _EMPHASIS.sub(r"\2", text)
    # After emphasis, or a padded "** sigma **" no longer reads as a span.
    text = _speak_symbols(text)

    # Dashes are punctuation to the eye and a hazard to the ear. An en dash
    # wedged between words ("Management–Drafting") is read with no gap at
    # all, so the two run together as one word; an em dash mid-sentence
    # ("relax—preferably") gets no pause either. A comma is the pause a
    # reader hears in both.
    text = _SPEECH_DASH.sub(", ", text)

    text = _BLANK_RUN.sub("\n\n", text)
    # A list item or heading ends where its line does but carries no full
    # stop, so the voice ran "COA audit findings and unresolved notices
    # procurement and infrastructure risks" together as one sentence and
    # paused wherever it happened to breathe. The line end is the pause.
    lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(
        _ended(line) if i < len(lines) - 1 else line for i, line in enumerate(lines)
    ).strip()
    # No trailing notice announcing what was withheld. The inline "a link",
    # "a file path" and "an identifier" already say that something was left
    # unspoken, and the value is on screen regardless; the extra sentence
    # after every such reply was the thing the user asked to have removed.
    # hidden_counts is still kept by the substitutions above for callers that
    # want to know what was withheld.
    return text


__all__ = ["SpokenTextOverflow", "SpokenTextStream", "to_spoken_text"]
