"""Joining a reply that ran out of tokens to the rest of it.

When a reply hits the token limit the route asks the model to go on. Asked
to "continue from where you left off", it started over politely -- "Sir,
continuing from the Sun's formation and the faint-young-Sun problem:" --
glued straight onto a sentence cut at "despite", and elsewhere wrote a whole
section again from its heading ("The radiative## 5.3 The tachocline", 23
September). The prompt now asks for the words after the last one, and
``seam`` removes what the model does anyway: a line announcing that it is
continuing, and text it repeats from before the cut.
"""

from __future__ import annotations

import re

CONTINUE_PROMPT = (
    "Your reply was cut off by the length limit. Carry on from the exact point "
    "where it stopped: if it stopped mid-sentence, finish that sentence. Do not "
    "repeat anything already written, and do not add a greeting, an "
    "acknowledgement or a heading that was already given."
)

#: Continuation text held back until the seam can be judged.
HOLD_CHARS = 800

#: Shortest repeated run treated as a repeat rather than a coincidence.
MIN_OVERLAP = 12

_ANNOUNCEMENT = re.compile(
    r"^\s*(?:(?:sir|of course|certainly)[,.!]?\s*)*"
    r"(?:continuing|to continue|picking up|resuming|carrying on)\b[^\n]*?[:.]"
    r"[ \t]*\n*",
    re.IGNORECASE,
)


def seam(before: str, after: str) -> str:
    """*after*, trimmed to follow *before* without repeating it."""
    text = _ANNOUNCEMENT.sub("", after, count=1)
    text = _drop_repeat(before, text)
    if before and text and before[-1].isalnum() and text[0].isalnum():
        return " " + text
    return text


def _drop_repeat(before: str, after: str) -> str:
    # The model restarting a stretch it already wrote: the last words before
    # the cut turn up again near the start. Cut through their last showing.
    tail = before.rstrip()[-40:]
    if len(tail.strip()) >= MIN_OVERLAP:
        found = after.rfind(tail, 0, HOLD_CHARS + len(tail))
        if found >= 0:
            return after[found + len(tail) :]
    # Or it repeats only the words just before the cut.
    limit = min(len(before), len(after), 300)
    for size in range(limit, MIN_OVERLAP - 1, -1):
        if before.endswith(after[:size]):
            return after[size:]
    return after


__all__ = ["CONTINUE_PROMPT", "HOLD_CHARS", "seam"]
