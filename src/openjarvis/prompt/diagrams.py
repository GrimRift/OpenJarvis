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
* ``comparison`` -- two options side by side: a refractor against a reflector.

Colour carries meaning here, so the model may mark at most two nodes. The
user's rule, in their words: not everything needs a colour.
"""

from __future__ import annotations

#: The fenced-code language the browser looks for.
LANGUAGE = "sage-diagram"

#: How the user wants diagrams offered this turn.
AUTOMATIC = "auto"
ON_REQUEST = "on-request"
OFF = "off"
MODES = (AUTOMATIC, ON_REQUEST, OFF)

_WHEN_AUTOMATIC = """Draw one whenever the answer is a process, a structure, a
plan, or a set of parts -- "how does X work", "how is X made", "how do I build
X", "what goes into X", "X versus Y" -- and whenever the user asks to see it
("show me how", "illustrate that", "diagram it")."""

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
  thing is made of, `comparison` for two options weighed against each other.
* `title` -- a short phrase, no trailing full stop.
* `nodes` -- 3 to 8 of them. `label` is two or three words. `note` is one
  short sentence, under about nine words; leave it out if it would only
  repeat the label.
* `subject` -- REQUIRED for `parts` only: the thing being made ("Concrete").
* `sides` -- REQUIRED for `comparison` only: two short headings
  (`["Refractor", "Reflector"]`), and every node carries `"side": 0` or
  `"side": 1`.
* `mark` -- OPTIONAL, and at most two nodes in the whole diagram may carry
  one. `"trigger"` is what sets the process off or where the user decides;
  `"result"` is where the new thing comes into being. Marking everything
  makes the marks meaningless, so mark nothing unless it earns it.
* `icon` -- OPTIONAL, one of: {icons}.

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
]
