"""A note on "Redo chapter": what the user wants changed, as settings.

User request 2026-10-14: say how a chapter should come out differently when
asking for it again. Sage's model reads the note and picks from what the
renderer can actually change; anything else is listed back as not possible
(tone and emotion: the voice has no reliable control for them -- a redo is
a fresh take of every line, nothing more).

What a note can set:

* ``pronunciations`` -- a word and how to say it ("Gio" -> "Jee-oh"); kept
  for the whole book unless the note limits it to the chapter.
* ``pause_scale`` -- pauses between sentences and paragraphs, 0.6-1.6.
* ``tempo`` -- narration speed, 0.85-1.15 (pitch kept).
* ``sfx`` -- sound effects "on" / "off", and ``sfx_level`` 0.3-2.5.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

#: Messages -> the model's reply text.
Generate = Callable[[List[Dict[str, str]]], str]

LIMITS = {"pause_scale": (0.6, 1.6), "tempo": (0.85, 1.15), "sfx_level": (0.3, 2.5)}

PROMPT = """You turn a listener's note about an audiobook chapter into settings \
for re-narrating it. The narrator is a text-to-speech voice. Reply with JSON \
only, no prose, in exactly this shape:

{"pronunciations": [{"word": "...", "say": "...", "book_wide": true}],
 "pause_scale": null, "tempo": null, "sfx": null, "sfx_level": null,
 "understood": ["short sentences saying what will change"],
 "not_possible": ["what the note asks that these settings cannot do"]}

Rules:
- pronunciations: a word from the book and a respelling the voice will say \
right ("Gio" -> "Jee-oh"). book_wide false only if the note limits it to this chapter.
- pause_scale: 1 = as now; 1.3 = longer pauses, 0.75 = shorter. Range 0.6-1.6.
- tempo: 1 = as now; 0.9 = slower, 1.1 = faster. Range 0.85-1.15.
- sfx: "on" or "off" (sound effects under words like "Rustle..."); sfx_level \
1 = as now, 1.8 = louder, 0.5 = softer.
- Leave a field null when the note does not ask about it.
- Emotion, tone, acting, a different voice, or changing the text cannot be \
done: put them in not_possible. A fresh take of every line always happens, \
so "this line sounded wrong" needs no setting; say so in understood.
- Write understood/not_possible for the listener, plainly, no jargon."""


def _json_in(text: str) -> Dict[str, Any]:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON in the reply")
    return json.loads(text[start : end + 1])


def _number(value: Any, key: str) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    lo, hi = LIMITS[key]
    x = min(hi, max(lo, x))
    return None if abs(x - 1.0) < 1e-3 else round(x, 2)


def clean(raw: Dict[str, Any]) -> Dict[str, Any]:
    """The model's answer, checked: known fields only, numbers in range."""
    out: Dict[str, Any] = {"pronunciations": [], "understood": [], "not_possible": []}
    for p in raw.get("pronunciations") or []:
        if not isinstance(p, dict):
            continue
        word, say = str(p.get("word") or "").strip(), str(p.get("say") or "").strip()
        if word and say and len(word) <= 40 and len(say) <= 60 and word != say:
            out["pronunciations"].append(
                {"word": word, "say": say, "book_wide": p.get("book_wide") is not False}
            )
    for key in ("pause_scale", "tempo", "sfx_level"):
        value = _number(raw.get(key), key) if raw.get(key) is not None else None
        if value is not None:
            out[key] = value
    if raw.get("sfx") in ("on", "off"):
        out["sfx"] = raw["sfx"]
    for key in ("understood", "not_possible"):
        out[key] = [str(s)[:200] for s in raw.get(key) or [] if str(s).strip()][:6]
    return out


def interpret(
    note: str,
    chapter_label: str,
    flags: List[Dict[str, Any]],
    generate: Generate,
) -> Dict[str, Any]:
    """Settings for a redo from the user's note; never raises: if the model
    cannot be asked, the note is kept and the redo still happens."""
    flagged = "; ".join(
        f'"{f.get("text")}" ({", ".join(f.get("why") or [])})' for f in flags
    )
    user = f"Chapter: {chapter_label}\nListener's note: {note.strip()}"
    if flagged:
        user += f"\nLines the automatic check flagged: {flagged}"
    try:
        reply = generate(
            [{"role": "system", "content": PROMPT}, {"role": "user", "content": user}]
        )
        settings = clean(_json_in(reply))
    except Exception as exc:  # the model is down, slow, or answered oddly
        settings = {
            "pronunciations": [],
            "understood": ["A fresh take of every line."],
            "not_possible": [
                f"Sage couldn't read the note this time ({exc}); it is kept."
            ],
        }
    settings["note"] = note.strip()
    settings["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return settings


def respell(text: str, pronunciations: List[Dict[str, str]]) -> str:
    """Each word said the way the user asked ("Gio" -> "Jee-oh"), keeping a
    capital at the start of a sentence."""
    for p in pronunciations:
        word, say = p.get("word") or "", p.get("say") or ""
        if not word or not say:
            continue

        def swap(m: "re.Match[str]", say: str = say) -> str:
            got = m.group(0)
            return say[0].upper() + say[1:] if got[0].isupper() else say

        text = re.sub(rf"(?<![\w']){re.escape(word)}(?![\w])", swap, text, flags=re.I)
    return text
