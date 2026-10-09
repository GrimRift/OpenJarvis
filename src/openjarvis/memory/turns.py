"""The chat turn a request is answering, for the facts saved during it.

The app sends its id for the user's message (``turn_id``). Every fact learned
in that turn -- extracted afterwards or saved by the ``remember`` tool --
carries it, so rewinding the chat to before that message can forget them
(``MemoryService.forget_turns``). Request-local, like ``page_access``.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

_current: ContextVar[str] = ContextVar("sage_chat_turn", default="")


def set_turn(turn: str) -> Token:
    return _current.set(str(turn or ""))


def current() -> str:
    """The app's id of the user message this request answers ("" if none)."""
    return _current.get()


@contextmanager
def scope(turn: str) -> Iterator[None]:
    """Bind in the task that actually runs this turn's tools."""
    token = set_turn(turn)
    try:
        yield
    finally:
        _current.reset(token)
