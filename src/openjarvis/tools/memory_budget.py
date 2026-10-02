"""How many times one message may search memory (2 October).

Asked how PewDiePie's model compared "to gpt luna", Sage searched its own
memory twelve times -- ``recall`` twice, ``retrieval`` ten -- and used up all
fifteen rounds without answering. Memory holds dozens of facts that mention
GPT Luna, and every search returned something, so nothing ever said stop.

The executing agent run or streaming-body task owns the budget. Worker
threads receive the same state through copied context; other turns receive
a fresh state. The time window also covers standalone tool callers.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Iterator, List

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

#: Said when a web search already ran for this message (the user's choice,
#: 2 October): a web question is not answered from notes and mail.
AFTER_WEB_MESSAGE = (
    "A web search already ran for this message, so it is a web question: do "
    "not search the user's notes or mail. Answer from the web results."
)

_lock = threading.Lock()


@dataclass
class _SearchState:
    searches: List[float] = field(default_factory=list)
    web_searched: bool = False
    explicit_turn: bool = False
    web_until: float = 0.0


_current: ContextVar[_SearchState | None] = ContextVar(
    "memory_search_turn", default=None
)


def _state() -> _SearchState:
    state = _current.get()
    if state is None:
        state = _SearchState()
        _current.set(state)
    return state


def start_message() -> Token:
    """A new message from the user: a fresh budget."""
    return _current.set(_SearchState(explicit_turn=True))


@contextmanager
def scope() -> Iterator[None]:
    """An independent budget, restored even when the run is cancelled."""
    token = start_message()
    try:
        yield
    finally:
        _current.reset(token)


def note_web_search() -> None:
    """A web search started; the rule lasts until this message ends."""
    with _lock:
        state = _state()
        state.web_searched = True
        state.web_until = time.monotonic() + WINDOW_SECONDS


def web_searched() -> bool:
    with _lock:
        state = _state()
        return state.web_searched and (
            state.explicit_turn or state.web_until > time.monotonic()
        )


def take() -> bool:
    """Use one search if any are left; whether it may run."""
    now = time.monotonic()
    with _lock:
        searches = _state().searches
        searches[:] = [until for until in searches if until > now]
        if len(searches) >= MAX_SEARCHES_PER_MESSAGE:
            return False
        searches.append(now + WINDOW_SECONDS)
        return True


def spent() -> bool:
    now = time.monotonic()
    with _lock:
        return sum(1 for until in _state().searches if until > now) >= (
            MAX_SEARCHES_PER_MESSAGE
        )


def available_tools(tools: List[dict[str, Any]]) -> List[dict[str, Any]]:
    """Retire unavailable memory tools in both orchestration loops."""
    if not (spent() or web_searched()):
        return tools
    return [
        tool
        for tool in tools
        if (tool.get("function") or {}).get("name") not in MEMORY_SEARCH_TOOLS
    ]


__all__ = [
    "MAX_SEARCHES_PER_MESSAGE",
    "AFTER_WEB_MESSAGE",
    "MEMORY_SEARCH_TOOLS",
    "SPENT_MESSAGE",
    "WINDOW_SECONDS",
    "spent",
    "start_message",
    "note_web_search",
    "take",
    "web_searched",
    "scope",
    "available_tools",
]
