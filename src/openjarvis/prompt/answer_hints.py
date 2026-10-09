"""Per-question nudges about prices and numbers (the user's rules, 9 October).

Both came from one chat about GPUs for local AI models:

* "best value RTX GPU with 16GB under 50000 peso" was answered with a pick
  and "I couldn't verify current Philippine prices", and the user had to ask
  again ("then verify its current prices"). A budget means the price IS the
  question, so local prices are searched from the start.
* A token/s table put one measured test (41-47 tok/s on a 5060 Ti) beside
  a modelled estimate (~21 tok/s on a 4060 Ti), which read as "twice as
  fast"; the next answer had to take it back. A measured figure and an
  estimate are now labelled, and no ratio is drawn across the two. When
  only estimates exist and the user asks to compare, Sage still compares --
  saying plainly that they are estimates, not measured data.
"""

from __future__ import annotations

import re

_BUDGET_RE = re.compile(
    # No \b before peso/php: the user wrote "under 50000peso".
    r"(?:₱|php\b|pesos?\b)"
    r"|\b(?:under|below|less\s+than|within|up\s+to|max(?:imum)?|budget(?:\s+of)?)"
    r"\s*(?:₱|php|p)?\s*\d[\d,.]*\s*k?\b",
    re.IGNORECASE,
)

_NUMBERS_RE = re.compile(
    r"\b(?:fast|faster|slower|speed|speeds|benchmarks?|performance|fps|"
    r"tok(?:en)?s?\s*/\s*s|tokens?\s+per\s+second|twice|times\s+(?:faster|slower)|"
    r"\d+\s*x\b|percent|%|how\s+much\s+(?:faster|better|slower)|vs\.?|versus|"
    r"compare|comparison)\b",
    re.IGNORECASE,
)

BUDGET_HINT = (
    "The user gave a budget: the answer depends on current prices in the"
    " Philippines. Put local prices into your FIRST web_search (Philippine"
    " retailers, the peso amount, the candidates by name) and give a price"
    " with its shop for each pick; say plainly where a price could not be"
    " found."
)

NUMBERS_RULE = (
    "Numbers: label each figure as measured (a real test or benchmark) or an"
    " estimate (a calculator, a model, a spec-sheet calculation). Never put a"
    " measured figure and an estimate side by side as if comparable, and never"
    " draw a ratio ('twice as fast') across the two. If only estimates exist"
    " and the user asks for a comparison, compare them anyway and say plainly"
    " that these are estimates, not measured data."
)


_EVENT_RE = re.compile(
    r"\b(?:who\s+won|winner|won|win|wins|result|results|score|scores|final|"
    r"race|grand\s+prix|gp|match|game|fight|bout|election|elected|tournament|"
    r"championship|cup|league|launch|launched|announced|happened|award|awards)\b",
    re.IGNORECASE,
)

EVENTS_RULE = (
    "Events: take the event's name, date and place TOGETHER from one source"
    " and name that source; never combine a name from one result with a"
    " date or place from another. If sources disagree, say so plainly"
    ' ("BBC says X, ESPN says Y") instead of blending them. An unusual'
    ' name can be real (2026\'s "Bahrain Grand Prix in Malaysia" was held'
    " at Sepang): report what the source says."
)

# Follow-ups (the user's choices, 9 October). After a full standings table
# (Antonelli 320, Hamilton 214), "is it still possible for Lewis to win?"
# searched again, got a months-old page (219 / 169), answered with figures
# from neither (224 / 171) under the formula1.com link, and did no maths.
FOLLOW_UP_RULE = (
    "Follow-up: figures already given earlier in this chat come first. Work"
    " from them and search only for what is missing. If a source gives"
    " different figures, check its date: use the newer one and say plainly"
    ' that the figures changed ("after Sepang it was X; now it is Y"),'
    " never swap numbers silently. Quote a figure only from the source you"
    " link it to."
)

TITLE_MATH_RULE = (
    "Can-they-still-win: show the maths before the opinion -- the gap in"
    " points, the rounds left (and sprints), the most points still available"
    " (in F1: 25 per Grand Prix, 8 per sprint), and whether the gap can"
    " still be closed. Then the view."
)

_REFERS_BACK = re.compile(
    r"\b(he|she|they|him|her|them|it|that|those|these|still|now|then|so|"
    r"instead|again|same|also)\b",
    re.IGNORECASE,
)
_TITLE_MATH_RE = re.compile(
    r"\b(still\s+(?:possible|win|make|catch|qualify|have\s+a\s+chance)|"
    r"mathematically|can\s+\w+\s+still|chance(?:s)?\s+(?:of|to)\s+win|"
    r"clinch|catch\s+up|title\s+race|win\s+the\s+(?:title|championship|league))",
    re.IGNORECASE,
)


def events_hint(text: str) -> str:
    """The one-source rule when *text* asks about an event or a result."""
    return EVENTS_RULE if _EVENT_RE.search(text or "") else ""


def follow_up_hint(text: str, has_history: bool) -> str:
    """Earlier figures first, for a question that builds on the chat."""
    if not has_history:
        return ""
    if _REFERS_BACK.search(text or "") or _TITLE_MATH_RE.search(text or ""):
        return FOLLOW_UP_RULE
    return ""


def title_math_hint(text: str) -> str:
    """Show the maths for a 'can they still win?' question."""
    return TITLE_MATH_RULE if _TITLE_MATH_RE.search(text or "") else ""


def budget_hint(text: str) -> str:
    """The budget nudge when *text* names a budget or a peso amount."""
    return BUDGET_HINT if _BUDGET_RE.search(text or "") else ""


def numbers_hint(text: str) -> str:
    """The measured-versus-estimate rule when *text* asks about figures."""
    return NUMBERS_RULE if _NUMBERS_RE.search(text or "") else ""


__all__ = [
    "BUDGET_HINT",
    "EVENTS_RULE",
    "FOLLOW_UP_RULE",
    "TITLE_MATH_RULE",
    "NUMBERS_RULE",
    "budget_hint",
    "events_hint",
    "follow_up_hint",
    "title_math_hint",
    "numbers_hint",
]
