"""Asking Sage to draw a diagram instead of sketching one in text.

Sage used to answer "show me how" with an arrow-and-pipe sketch inside a code
block -- readable, but it sat in the transcript looking like output from a
program. The browser can draw the same thing properly, so the model emits a
small JSON description and the UI renders it (``lib/diagram.ts``).

Three shapes, because the questions are not all the same shape:

* ``flow`` -- one thing leads to the next: how a CPU is made, how to build a
  telescope, the steps of a plan.
* ``parts`` -- a thing and what it is made of: the materials in concrete.
  A list of ingredients is not a chain of steps, and drawing it as one reads
  as nonsense.
* ``comparison`` -- a grid: up to four things weighed across the dimensions
  that decide between them. Two columns was not enough -- asked to compare
  three cars it crushed two into one heading, and the result read thinner
  than the table the same answer already had in the chat.

Colour carries meaning here, so the model may mark at most two nodes. The
user's rule, in their words: not everything needs a colour.
"""

from __future__ import annotations

import re

#: The fenced-code language the browser looks for.
LANGUAGE = "sage-diagram"

#: How the user wants diagrams offered this turn.
AUTOMATIC = "auto"
ON_REQUEST = "on-request"
OFF = "off"
MODES = (AUTOMATIC, ON_REQUEST, OFF)

_WHEN_AUTOMATIC = """Draw one whenever the answer is a process, a structure, a
plan, or a set of parts -- "how does X work", "how is X made", "how do I build
X", "what goes into X" -- whenever options are compared ("X versus Y",
"which is better") -- and whenever the user asks to see it ("show me how",
"illustrate that", "diagram it"). A comparison is drawn as a `comparison`
grid INSTEAD of a markdown table, never both."""

_WHEN_ON_REQUEST = """Draw one ONLY when the user asks to see it in words like
"show me how", "illustrate that", "draw that", "diagram it". Never draw one
unasked, however diagram-shaped the answer is."""

_BODY = """
## Diagrams

You can draw a diagram. Emit it as a fenced code block tagged `{language}`
holding one JSON object. The browser renders it; the user never sees the
JSON.

{when}

Never also draw the same thing with text arrows, pipes or ASCII boxes, and do
not describe the diagram's layout in the prose. Write the answer as you
normally would -- the diagram accompanies it.

The object:

```{language}
{{
  "shape": "flow",
  "title": "How Bacillus subtilis heals a crack",
  "nodes": [
    {{"label": "A crack forms", "note": "Stress opens a gap.", "icon": "crack"}},
    {{"label": "Water gets in", "icon": "droplet", "mark": "trigger"}},
    {{"label": "Limestone forms", "icon": "crystal", "mark": "result"}}
  ]
}}
```

* `shape` -- `flow` for steps that lead to one another, `parts` for what a
  thing is made of, `comparison` for 2 to 4 options weighed side by side (it replaces a
  markdown table; do not write both).
* `title` -- a short phrase, no trailing full stop.
* `nodes` -- 3 to 8 of them. `label` is two or three words. `note` is one
  short sentence, under about nine words; leave it out if it would only
  repeat the label.
* `fact` -- a few words of hard detail: a quantity, a size, a duration, a
  spec. "2.0L gasoline", "about 40% by volume", "300 mm wafer", "5 seconds".
  Include one on every box you actually know a figure for -- it is what
  makes the diagram worth more than the labels alone. Omit it rather than
  invent a number you are unsure of.
* `subject` -- REQUIRED for `parts` only: the thing being made ("Concrete").

* `mark` -- OPTIONAL, and at most two nodes in the whole diagram may carry
  one. `"trigger"` is what sets the process off or where the user decides;
  `"result"` is where the new thing comes into being. Marking everything
  makes the marks meaningless, so mark nothing unless it earns it.
* `icon` -- OPTIONAL, one of: {icons}.

A `comparison` is a GRID, not boxes, and carries `columns` and `rows`
instead of `nodes`:

```{language}
{{
  "shape": "comparison",
  "title": "Mazda3 versus Vios and Civic",
  "columns": ["Mazda3", "Toyota Vios", "Honda Civic"],
  "rows": [
    {{"label": "Driving feel",
     "cells": ["Most refined", "Comfortable, not sporty", "Sporty, stable"]}},
    {{"label": "Running cost",
     "cells": ["Moderate", "Cheapest to keep", "Turbo adds cost"],
     "mark": "trigger"}}
  ]
}}
```

* `columns` -- 2 to 4 things being weighed up. THREE things get three
  columns; never crush two of them into one heading.
* `rows` -- 4 to 6 dimensions that actually decide the question, each with
  one cell per column, 3 to 6 words each. Pick the rows that separate the
  options; a row where every cell says the same thing is wasted.
* `mark` on a row highlights the dimension that decides it, same two-mark
  budget.

Keep it to what the diagram can carry: a diagram with eleven boxes is a list,
so pick the steps that matter and say the rest in prose.
"""

#: What the browser can draw. An unknown name falls back to a plain dot, so a
#: wrong guess costs nothing -- but the list keeps the model near the mark.
ICONS = (
    "crack",
    "droplet",
    "spark",
    "crystal",
    "rod",
    "close",
    "flame",
    "layers",
    "tool",
    "gear",
    "clock",
    "check",
    "bolt",
    "beaker",
    "scale",
    "eye",
    "cpu",
    "box",
    "leaf",
    "wave",
)


#: A question that weighs options against each other.
_COMPARISON_RE = re.compile(
    r"\b(?:vs\.?|versus|compare[ds]?|comparing|comparison"
    r"|differences?\s+between|which\s+(?:one\s+|of\s+them\s+)?is\s+(?:better|best)"
    r"|which\s+should\s+i\s+(?:get|buy|choose|pick))\b",
    re.IGNORECASE,
)

_COMPARISON_HINT = (
    "This question compares options: draw a `comparison` "
    f"{LANGUAGE} grid (one column per option, the rows that separate them) "
    "and do NOT also write a markdown table -- the grid is the table. Rows "
    "where every option is the same go in one prose sentence instead."
)


def turn_hint(mode: str, question: str) -> str:
    """A nudge for this turn only, or "".

    "airpods 5 vs airpods 4?" (9 October) got a markdown table with
    diagrams on Automatic: the system prompt's "X versus Y" alone was not
    enough. Said next to the question, it is.
    """
    if mode != AUTOMATIC or not _COMPARISON_RE.search(question or ""):
        return ""
    return _COMPARISON_HINT


def instruction(mode: str) -> str:
    """The prompt section for *mode*, or "" when diagrams are switched off."""
    if mode not in (AUTOMATIC, ON_REQUEST):
        return ""
    when = _WHEN_AUTOMATIC if mode == AUTOMATIC else _WHEN_ON_REQUEST
    return _BODY.format(
        language=LANGUAGE,
        when=when.replace("\n", " ").strip(),
        icons=", ".join(f"`{name}`" for name in ICONS),
    ).strip()


__all__ = [
    "AUTOMATIC",
    "ICONS",
    "LANGUAGE",
    "MODES",
    "OFF",
    "ON_REQUEST",
    "instruction",
    "turn_hint",
]
