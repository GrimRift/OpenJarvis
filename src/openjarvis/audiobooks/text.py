"""Turning a book paragraph into text a TTS voice can read.

Everything here works on one paragraph at a time; the PDF and EPUB readers
decide where paragraphs are. What the reference book needed (measured
2026-10-13, ``docs/m42-audiobooks.md``):

* A download-site watermark (``OceanofPDF.com``) closing every chapter.
* A second, inline watermark copied from a web-novel site, written to dodge
  search-and-replace: Cyrillic and Greek look-alike letters, math-alphabet
  letters, letter-by-letter spacing, symbol wrappers and an aside such as
  "(Read more on our source)". Left in, a voice reads it mid-sentence.
* Footnote markers (superscript digits, U+2074 among them) after words.
* Curly quotes and ellipsis characters in every shape (``……``, ``.......``).
* Decorative symbols, emoji and kaomoji.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

#: How a scene break is written in stored chapter text. The render step turns
#: it into a long pause; it is never sent to the voice.
SCENE_BREAK = "* * *"

#: Superscript digits as footnote markers (U+00B9/B2/B3 and U+2070-2079).
_SUPERSCRIPTS = "\u00b9\u00b2\u00b3\u2070\u2071\u2074\u2075\u2076\u2077\u2078\u2079"
_SUPERSCRIPT_RUN = re.compile(f"[{_SUPERSCRIPTS}]+")

#: A footnote number typed as a plain digit against its word ("sikhye3",
#: "Speedwagon1"): all 21 such words in the reference book were markers. A
#: number written "Level 10" (with its space) is untouched.
_GLUED_MARKER = re.compile(r"\b(?=[A-Za-z]*[a-z])([A-Za-z]{3,})\d{1,2}\b")

#: Look-alike letters used to hide a watermark from search, mapped to the Latin
#: letter they imitate. Applied only inside a word that also has Latin letters,
#: so text that is genuinely Cyrillic or Greek is not rewritten.
_HOMOGLYPHS = str.maketrans(
    {
        "\u0430": "a",
        "\u0432": "b",
        "\u0435": "e",
        "\u0451": "e",
        "\u0433": "r",
        "\u04bb": "h",
        "\u0456": "i",
        "\u0457": "i",
        "\u0458": "j",
        "\u043a": "k",
        "\u043c": "m",
        "\u043d": "h",
        "\u043e": "o",
        "\u043f": "n",
        "\u0440": "p",
        "\u0441": "c",
        "\u0442": "t",
        "\u0443": "y",
        "\u0445": "x",
        "\u0455": "s",
        "\u0501": "d",
        "\u0410": "A",
        "\u0412": "B",
        "\u0415": "E",
        "\u0401": "E",
        "\u041d": "H",
        "\u0406": "I",
        "\u0408": "J",
        "\u041a": "K",
        "\u041c": "M",
        "\u041e": "O",
        "\u0420": "P",
        "\u0421": "C",
        "\u0422": "T",
        "\u0425": "X",
        "\u0405": "S",
        "\u03b9": "i",
        "\u03bf": "o",
        "\u03bd": "v",
        "\u03b1": "a",
        "\u03b5": "e",
        "\u03c4": "t",
        "\u0399": "I",
        "\u039f": "O",
        "\u0391": "A",
        "\u0392": "B",
        "\u0395": "E",
        "\u0397": "H",
        "\u039a": "K",
        "\u039c": "M",
        "\u039d": "N",
        "\u03a1": "P",
        "\u03a4": "T",
        "\u03a7": "X",
        "\u03a5": "Y",
        "\u0396": "Z",
    }
)

#: The web-novel watermark once look-alikes are folded: "Novelight" with a
#: capital I for the l, an r for the g, or spaces or bullets between letters.
_SEP = r"[\s\u2022\u00b7*._-]?"
_NOVELIGHT = _SEP.join(["n", "o", "v", "e", "[li]", "[li]?", "[gr]?", "h", "t"])
#: Symbols a watermark is wrapped in, e.g. "°• N o v e l i g h t •°", "{...}".
_WRAP = r"[~\u00b0\u2022*{}<>|\[\]\u2190-\u2bff\u2700-\u27bf]"
_SITE_WATERMARK = re.compile(
    rf"(?:{_WRAP}\s*){{0,3}}\b{_NOVELIGHT}\b(?:\s*{_WRAP}){{0,3}}", re.I
)
#: Links (a translator's note pointing at a video): never worth reading out.
_URL = re.compile(r"\(?\b(?:https?://|www\.)\S+?\)?(?=[\s,;]|[.!?]?$|[.!?]\s)", re.I)
_OCEAN = re.compile(r"_?ocean\s*of\s*pdf\s*\.\s*com_?", re.I)
#: Asides the web-novel watermark comes with. Each is spam in any book.
_SPAM_ASIDE = re.compile(
    r"\(\s*(?:read more on our source|exclusive on\b[^()]{0,30}|continue reading"
    r"|original source|don'?t copy,? read here|read the full story"
    r"|only on\b[^()]{0,30})\s*\)",
    re.I,
)

_QUOTES = str.maketrans(
    {
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2033": '"',
        "\u00ab": '"',
        "\u00bb": '"',
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u2032": "'",
        "\u2012": "\u2014",
        "\u2015": "\u2014",
        # CJK corner brackets: quoted signs and messages in translated novels
        "\u300c": '"',
        "\u300d": '"',
        "\u300e": '"',
        "\u300f": '"',
    }
)
_DOTS = re.compile(r"(?:\s?[.\u2026]){2,}")
#: Characters kept even though Unicode files them as symbols.
_KEEP_SYMBOLS = set(
    "\u00b0\u00a9\u00ae\u2122%&+=<>\u00d7\u00f7\u00b1$\u20ac\u00a3\u00a5\u20a9#@/|"
)

_NOTE_LEAD = re.compile(
    r"^(?:t\s*/\s*n|tl\s*/?\s*n(?:ote)?|translator'?s?\s+note|a\s*/\s*n"
    r"|author'?s?\s+note|ed(?:itor)?'?s?\s+note|pr\s+note|note)\s*[:\-\u2013\u2014]\s*",
    re.I,
)
_FOOTNOTE_LEAD = re.compile(rf"^[{_SUPERSCRIPTS}]+\s*[:.)]?\s*")
_SCENE_BREAK_RE = re.compile(r"^[\s*#~\-_=+.\u2022\u25c6\u25c7\u2756\u2605\u2606]{3,}$")


def is_scene_break(text: str) -> bool:
    """A paragraph that is only a divider: ``***``, ``* * *``, ``◇◇◇``..."""
    return bool(_SCENE_BREAK_RE.match(text.strip())) and "." not in text.strip(" *")


def note_text(paragraph: str) -> Optional[str]:
    """The note in a footnote or translator's-note paragraph, else ``None``.

    Footnotes start with a superscript (``¹: ...``); notes with ``t/n :``,
    ``TL note:``, ``A/N -`` and the like. The returned text has the lead-in
    removed but is not yet cleaned.
    """
    text = paragraph.strip()
    for lead in (_FOOTNOTE_LEAD, _NOTE_LEAD):
        m = lead.match(text)
        if m and text[m.end() :].strip():
            return text[m.end() :].strip()
    return None


def _is_latin_letter(ch: str) -> bool:
    return unicodedata.name(ch, "").startswith("LATIN")


def _fold_word(word: str) -> str:
    """Fold look-alike letters in a word that mixes them with Latin ones."""
    letters = [c for c in word if c.isalpha()]
    if not letters or not any(_is_latin_letter(c) for c in letters):
        return word
    return word.translate(_HOMOGLYPHS)


def _is_foreign(word: str) -> bool:
    """A word that still holds non-Latin letters once look-alikes are folded."""
    folded = _fold_word(unicodedata.normalize("NFKC", word))
    return any(c.isalpha() and not _is_latin_letter(c) for c in folded)


def _edges(word: str) -> str:
    """Only the quotes and sentence punctuation at a word's edges."""
    m = re.match(r"^([\"'(\[\u201c\u2018]*).*?([\"'.,!?;:)\]\u201d\u2019]*)$", word)
    return (m.group(1) + m.group(2)) if m else ""


def _drop_foreign_words(text: str) -> str:
    """Remove kaomoji and gibberish words in a Latin-script book.

    A word still carrying non-Latin letters goes, keeping the quotes and
    sentence punctuation at its edges (so ``ᾥ´)o"`` still closes its
    quotation), and so do single math-alphabet letters standing next to it:
    the watermark ``𝑅 ℕ βЁṠ`` would otherwise leave "R N" behind.
    """
    words = text.split(" ")
    foreign = [_is_foreign(w) for w in words]
    mathy = [
        len(w) == 1 and w.isalpha() and unicodedata.normalize("NFKC", w) != w
        for w in words
    ]
    drop = list(foreign)
    for i in range(len(words)):
        if not foreign[i]:
            continue
        for step in (-1, 1):
            j = i + step
            while 0 <= j < len(words) and mathy[j]:
                drop[j] = True
                j += step
    return " ".join(_edges(w) if d else w for w, d in zip(words, drop))


def _strip_symbols(text: str) -> str:
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if ch in _KEEP_SYMBOLS:
            out.append(ch)
        elif cat in ("So", "Sk", "Cs", "Co", "Cn") or (
            cat == "Sm" and ord(ch) >= 0x2190
        ):
            out.append(" ")
        elif cat in ("Cf", "Mn") or ch in "\u2022\u2023\u2043\u2219\u25e6":
            # zero-width joiners, variation selectors, stray combining marks, bullets
            out.append("" if cat in ("Cf", "Mn") else " ")
        else:
            out.append(ch)
    return "".join(out)


def clean_paragraph(text: str, *, latin_script: bool = True) -> str:
    """One paragraph of book text made speakable. Returns ``""`` if nothing is left.

    ``latin_script`` is the book's script: when true, look-alike letters are
    folded and words still holding other scripts (kaomoji, gibberish) dropped.
    """
    # Footnote markers go before NFKC, which would turn them into plain digits.
    text = _SUPERSCRIPT_RUN.sub("", text)
    text = _GLUED_MARKER.sub(r"\1", text)
    text = re.sub(r"\s+", " ", text.replace("\u00ad", "")).strip()
    if latin_script:
        text = _drop_foreign_words(text)
    text = unicodedata.normalize("NFKC", text)
    if latin_script:
        text = re.sub(r"\S+", lambda m: _fold_word(m.group(0)), text)
    text = text.translate(_QUOTES)
    text = _URL.sub(" ", text)
    text = _OCEAN.sub(" ", text)
    text = _SPAM_ASIDE.sub(" ", text)
    text = _SITE_WATERMARK.sub(" ", text)
    text = _strip_symbols(text)
    text = _DOTS.sub("...", text)
    # "Haaaaaaaaaaaaaaah", "!!!!!!": a voice drags or garbles a long run
    text = re.sub(r"([^\W\d_])\1{4,}", r"\1\1\1\1", text)
    text = re.sub(r"([!?])\1{2,}", r"\1\1", text)
    text = re.sub(r"\(\s*\)|\[\s*\]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.!?;:])(?=\s|$|[\"')\]])", r"\1", text)
    text = _tighten_quotes(text)
    # Nothing speakable left (a bare quote mark, a lone symbol) -- but an
    # ellipsis-only line is a beat the render step turns into a pause.
    if not re.search(r"[^\W_]", text) and "..." not in text:
        return ""
    return text


def _tighten_quotes(text: str) -> str:
    """No space just inside a double quote: the source's `" Squeak ."`, or
    `"Yay "` after a removed word. Whether a quote opens or closes is told
    by how many came before it."""
    out: list = []
    count = 0
    for ch in text:
        if ch == " " and out and out[-1] == '"' and count % 2 == 1:
            continue  # just after an opening quote
        if ch == '"':
            count += 1
            if count % 2 == 0:
                while out and out[-1] == " ":
                    out.pop()  # just before a closing quote
        out.append(ch)
    return "".join(out)


def word_count(text: str) -> int:
    return len(re.findall(r"[^\W_]+(?:['\-][^\W_]+)*", text))
