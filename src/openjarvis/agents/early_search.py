"""Start the web search before the model asks for it.

A lookup turn spends its first model round only deciding to search: 2.5-3 s
before the search even starts (9 October bench: "When is the next public
holiday?" searched at 7.2 s, answered at 11.2 s). For a message that is
plainly a public lookup, the search starts with the message itself as the
query while that round runs; when the model then calls web_search, the
result is already there or nearly so.

Kept narrow on purpose, because a search the model never asks for is a paid
call for nothing: a question about something public, not the user's own
things, not an action, and not a follow-up that only makes sense with the
chat before it.
"""

from __future__ import annotations

import re

_QUESTION_START = re.compile(
    r"^\s*(?:what(?:'s|\s+is|\s+are|\s+was)?|who|when|where|which|how\s+(?:much|many|long|old|big|far)"
    r"|is\s+there|are\s+there|is\s+it|did|does|do|has|have|will|can\s+you\s+(?:check|find|look\s+up)"
    r"|check|find|look\s+up|search(?:\s+for)?|price\s+of|latest|current)\b",
    re.IGNORECASE,
)

_LOOKUP_CUE = re.compile(
    r"\b(?:price|prices|cost|costs|how\s+much|rate|exchange|today|tonight|this\s+week|"
    r"latest|current|currently|now|right\s+now|news|won|winner|score|result|results|"
    r"release|released|launch|holiday|holidays|schedule|when\s+(?:is|does|will|did)|"
    r"who\s+(?:is|won|was)|stock|available|availability|opening\s+hours|open\s+today|"
    r"population|capital|ceo|president|vs\.?|versus|compare|difference|better)\b",
    re.IGNORECASE,
)

#: The user's own things, actions for Sage, and the weather (its own tool).
_NOT_A_LOOKUP = re.compile(
    r"\b(?:my|mine|me|i|i'm|i've|we|our|remind|reminder|play|pause|resume|open|"
    r"close|launch|schedule\s+a|set\s+a|timer|alarm|email|emails|inbox|mail|"
    r"calendar|meeting|class|classes|note|notes|file|files|folder|send|message|"
    r"text|call|teams|outlook|spotify|youtube|netflix|weather|rain|raining|"
    r"umbrella|forecast|temperature|typhoon|bagyo|ulan)\b",
    re.IGNORECASE,
)

#: Words that only mean something with the conversation before them.
_FOLLOW_UP = re.compile(
    r"\b(?:it|its|it's|that|this|these|those|they|them|their|one|ones|he|she|"
    r"him|her|there|same|other|another|again|instead|also)\b",
    re.IGNORECASE,
)

MAX_WORDS = 24


def early_search_query(text: str, *, has_history: bool) -> str:
    """The query to search with right away, or "" when this is not a lookup."""
    text = " ".join(str(text or "").split())
    if not text or len(text.split()) > MAX_WORDS:
        return ""
    if not (text.endswith("?") or _QUESTION_START.search(text)):
        return ""
    if not _LOOKUP_CUE.search(text) or _NOT_A_LOOKUP.search(text):
        return ""
    if has_history and _FOLLOW_UP.search(text):
        return ""
    return text


__all__ = ["early_search_query"]
