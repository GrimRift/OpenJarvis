"""A chapter's stored text -> the pieces a voice says and the pauses between.

The rules come from the 2026-10-13 renders (docs/m42-audiobooks.md):

* Sentences are merged up to the live sidecar's 90-character piece size and
  split at clause punctuation past it, as live replies are; nothing goes to
  the voice over 160 characters (Nano's VRAM cap ran out on ~230).
* A piece under 14 characters is merged with a neighbour: Turbo says short
  lines twice ("It's nice. It's nice."). With this rule the lotm-narrator
  benchmark had 3 runaway retries where the old one had 21, and no repeats.
* A line with nothing to say (``"..."``) is a pause, never sent: Turbo voices
  it as "Uh" or "Huh". ``* * *`` is a longer pause.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from openjarvis.audiobooks.text import SCENE_BREAK

#: The live sidecar's piece size (voice_sidecar.server.SPLIT_OVER_CHARS).
SPLIT_OVER = 90
#: Never more than this to the voice in one piece.
HARD_MAX = 160
#: Pieces shorter than this are merged with a neighbour.
MERGE_UNDER = 14

#: Seconds of silence after each kind of piece.
GAPS = {
    "piece": 0.08,
    "sentence": 0.3,
    "paragraph": 0.6,
    "beat": 0.9,
    "scene": 1.6,
    "title": 1.2,
}

_SENTENCE = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")
_CLAUSE = re.compile(r"(?<=[,;:])\s+|(?<=\s[-\u2013\u2014])\s+")


@dataclass
class Item:
    """One step of the chapter: something to say, or a pause (``text`` None)."""

    text: Optional[str]
    gap: float  # silence after it, or the pause itself


def _clauses(text: str) -> List[str]:
    """voice_sidecar.server.split_for_generation (the sidecar has its own env)."""
    text = text.strip()
    if len(text) <= SPLIT_OVER:
        return [text] if text else []
    pieces: List[str] = []
    current = ""
    for part in _CLAUSE.split(text):
        part = part.strip()
        if not part:
            continue
        if current and len(current) + 1 + len(part) > SPLIT_OVER and len(current) >= 20:
            pieces.append(current)
            current = part
        else:
            current = f"{current} {part}".strip()
    if current:
        if pieces and len(current) < 20:
            pieces[-1] = f"{pieces[-1]} {current}"
        else:
            pieces.append(current)
    return pieces


def _hard_split(piece: str) -> List[str]:
    out: List[str] = []
    while len(piece) > HARD_MAX:
        cut = piece.rfind(" ", 0, HARD_MAX)
        cut = cut if cut > 40 else HARD_MAX
        out.append(piece[:cut].strip())
        piece = piece[cut:].strip()
    return out + ([piece] if piece else [])


def speakable(text: str) -> bool:
    return bool(re.search(r"[^\W_]", text))


def _paragraph_pieces(para: str) -> List[Tuple[str, str]]:
    """(piece, kind of gap after it) for one paragraph, short pieces merged."""
    merged: List[str] = []
    for sentence in (s for s in _SENTENCE.split(para) if s.strip()):
        if merged and len(merged[-1]) + 1 + len(sentence) <= SPLIT_OVER:
            merged[-1] = f"{merged[-1]} {sentence}"
        else:
            merged.append(sentence)
    pieces: List[Tuple[str, str]] = []
    for sentence in merged:
        parts = [h for c in _clauses(sentence) for h in _hard_split(c) if h]
        for k, part in enumerate(parts):
            if speakable(part):
                pieces.append((part, "piece" if k + 1 < len(parts) else "sentence"))
    k = 0
    while k < len(pieces) and len(pieces) > 1:
        text, gap = pieces[k]
        if len(text) >= MERGE_UNDER:
            k += 1
        elif k + 1 < len(pieces):
            pieces[k : k + 2] = [(f"{text} {pieces[k + 1][0]}", pieces[k + 1][1])]
        else:
            pieces[k - 1 : k + 1] = [(f"{pieces[k - 1][0]} {text}", gap)]
    return pieces


def build(text: str, title: str) -> List[Item]:
    """The chapter as pieces and pauses; *title* is announced first."""
    items: List[Item] = [Item(title, GAPS["title"])] if title else []
    for para in (p.strip() for p in text.split("\n\n")):
        if not para:
            continue
        if para == SCENE_BREAK:
            items.append(Item(None, GAPS["scene"]))
            continue
        if not speakable(para):
            items.append(Item(None, GAPS["beat"]))
            continue
        pieces = _paragraph_pieces(para)
        for k, (piece, gap) in enumerate(pieces):
            last = k + 1 == len(pieces)
            items.append(Item(piece, GAPS["paragraph"] if last else GAPS[gap]))
    return _merge_short_paragraphs(items)


def _merge_short_paragraphs(items: List[Item]) -> List[Item]:
    """A one-piece paragraph under MERGE_UNDER ('"Yes."') joins the next one,
    or the previous one when a pause or the chapter's end follows it."""
    out: List[Item] = []
    k = 0
    while k < len(items):
        item = items[k]
        short = item.text is not None and len(item.text) < MERGE_UNDER and k > 0
        if short:
            nxt = items[k + 1] if k + 1 < len(items) else None
            if nxt is not None and nxt.text is not None:
                items[k + 1] = Item(f"{item.text} {nxt.text}", nxt.gap)
                k += 1
                continue
            if out and out[-1].text is not None:
                out[-1] = Item(f"{out[-1].text} {item.text}", item.gap)
                k += 1
                continue
        out.append(item)
        k += 1
    return out


def announcement(chapter: dict) -> str:
    """'Chapter 1.' or 'Chapter 7: The Well.' -- said before the text."""
    label, title = chapter.get("label") or "", chapter.get("title") or ""
    text = label if not title or title in label else f"{label}: {title}"
    return text if text.endswith((".", "!", "?")) else f"{text}."
