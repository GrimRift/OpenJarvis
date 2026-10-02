"""How many times one message may search memory (2 October).

Asked how PewDiePie's model compared "to gpt luna", Sage searched its own
memory twelve times -- ``recall`` twice, ``retrieval`` ten -- and used up all
fifteen rounds without answering. Memory holds dozens of facts that mention
GPT Luna, and every search returned something, so nothing ever said stop.

Counted like page reads (``security.page_access``): a process-level table,
not a ContextVar, because the tool runs in a worker thread the request's
context never reaches. ``start_message`` resets it as each message arrives;
paths with no message (scheduled runs) are covered by the window instead, so
a count can never pin memory shut.
"""

from __future__ import annotations

import threading
import time
from typing import List

#: Searches one message may make, ``recall`` and ``retrieval`` together.
MAX_SEARCHES_PER_MESSAGE = 3
#: A search stops counting after this long, message or not.
WINDOW_SECONDS = 300.0

MEMORY_SEARCH_TOOLS = frozenset({"recall", "retrieval"})

SPENT_MESSAGE = (
    f"Memory has already been searched {MAX_SEARCHES_PER_MESSAGE} times for "
    "this message. Do not search again: answer now with what you have, and "
    "say plainly what you could not find."
)

_lock = threading.Lock()
_searches: List[float] = []


def start_message() -> None:
    """A new message from the user: a fresh budget."""
    with _lock:
        _searches.clear()


def take() -> bool:
    """Use one search if any are left; whether it may run."""
    now = time.monotonic()
    with _lock:
        _searches[:] = [until for until in _searches if until > now]
        if len(_searches) >= MAX_SEARCHES_PER_MESSAGE:
            return False
        _searches.append(now + WINDOW_SECONDS)
        return True


def spent() -> bool:
    now = time.monotonic()
    with _lock:
        return sum(1 for until in _searches if until > now) >= (
            MAX_SEARCHES_PER_MESSAGE
        )


__all__ = [
    "MAX_SEARCHES_PER_MESSAGE",
    "MEMORY_SEARCH_TOOLS",
    "SPENT_MESSAGE",
    "WINDOW_SECONDS",
    "spent",
    "start_message",
    "take",
]
