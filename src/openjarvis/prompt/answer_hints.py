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


def budget_hint(text: str) -> str:
    """The budget nudge when *text* names a budget or a peso amount."""
    return BUDGET_HINT if _BUDGET_RE.search(text or "") else ""


def numbers_hint(text: str) -> str:
    """The measured-versus-estimate rule when *text* asks about figures."""
    return NUMBERS_RULE if _NUMBERS_RE.search(text or "") else ""


__all__ = ["BUDGET_HINT", "NUMBERS_RULE", "budget_hint", "numbers_hint"]
