"""Which pages Sage is allowed to open and read.

``web_read`` renders a page in the user's own logged-in browser, so the URL it
is handed decides what enters the model's context. The tool itself receives
only a string and cannot tell where that string came from -- and the dangerous
case is precisely a URL that came from somewhere untrusted: a link in an
email, a document, or a page Sage read a moment ago. Following one of those is
how a page gets to choose what Sage fetches next.

So provenance is tracked where it is known rather than asserted in the tool
description. A URL is permitted when the user typed it, or when a search
returned it; anything else is refused by construction. Prompt-level rules have
not held in this codebase before, which is why this is a check and not a
sentence in a tool spec.

URL allowances remain in a short-lived process-level table so a follow-up
can read a recent source. Read counts and user intent belong to one turn.
Their context is bound inside the response-body generator, not just the
endpoint: BaseHTTPMiddleware streams in a different task. asyncio.to_thread
and both the agent and ToolExecutor's copy_context submissions then share
that turn's mutable state, while overlapping turns have separate states.
"""

from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Set
from urllib.parse import urlsplit, urlunsplit

#: Bare URLs in a user's message. Deliberately permissive about what follows
#: the scheme -- trailing punctuation is trimmed by ``normalise`` instead.
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

#: Trailing characters that are almost always sentence punctuation rather than
#: part of the address ("see https://example.com/page.").
_TRAILING = ".,;:!?'\")]}"

#: How long a URL stays readable after the user names it or a search returns
#: it. Long enough to cover the follow-up ("and what about the 8pm one?"),
#: short enough that a link from an hour ago is not still open to being read.
ALLOW_SECONDS = 300.0

#: Longest a read counts against the cap in ``web_read``. Each new message
#: resets the count (``set_turn``); this only bounds calls with no turn.
READ_WINDOW_SECONDS = 300.0

_lock = threading.Lock()
_allowed: Dict[str, float] = {}
#: The subset a search returned, which may be handed to the search provider's
#: page reader. A URL the user typed may be private (a shared doc, an
#: intranet page), so it is never sent there (the user's rule, 6 October).
_searched: Dict[str, float] = {}


@dataclass
class _ReadState:
    text: str = ""
    reads: List[float] = field(default_factory=list)
    urls: Set[str] = field(default_factory=set)


_current: ContextVar[_ReadState | None] = ContextVar("page_read_turn", default=None)


def _state() -> _ReadState:
    state = _current.get()
    if state is None:
        state = _ReadState()
        _current.set(state)
    return state


def normalise(url: str) -> str:
    """A comparable form of *url*.

    Compares scheme, host and path only. A search result and the same link
    typed by the user routinely differ by tracking parameters or a fragment,
    and refusing over ``?utm_source=`` would make the check feel arbitrary
    without making it safer -- the page reached is the same page.
    """
    text = (url or "").strip().strip(_TRAILING)
    if not text:
        return ""
    if not text.lower().startswith(("http://", "https://")):
        text = "https://" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    path = (parts.path or "/").rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), host, path, "", ""))


def urls_in(text: Any) -> Set[str]:
    """Every URL written out in *text*, normalised."""
    if not isinstance(text, str) or not text:
        return set()
    found = {normalise(match) for match in _URL_RE.findall(text)}
    return {url for url in found if url}


def allow(urls: Iterable[str]) -> None:
    """Permit *urls* to be read for the next few minutes.

    Called with what the user wrote and with what a search returned. Reading a
    search result is the same intent as clicking it -- unlike a link lifted
    out of the content of a page already read, which is never passed here.
    """
    now = time.monotonic()
    with _lock:
        _expire(now)
        for url in urls or ():
            normalised = normalise(url)
            if normalised:
                _allowed[normalised] = now + ALLOW_SECONDS


def allow_search_results(urls: Iterable[str]) -> None:
    """Permit *urls*, which a search returned, and remember where they came
    from (see :func:`from_search`)."""
    listed = [url for url in urls or ()]
    allow(listed)
    now = time.monotonic()
    with _lock:
        for url in listed:
            normalised = normalise(url)
            if normalised:
                _searched[normalised] = now + ALLOW_SECONDS


def from_search(url: str) -> bool:
    """Whether *url* came from a search and not from the user's own message.

    Only these may go to the search provider's page reader. One the user also
    typed counts as theirs.
    """
    normalised = normalise(url)
    if not normalised or normalised in urls_in(turn_text()):
        return False
    now = time.monotonic()
    with _lock:
        _expire(now)
        return normalised in _searched


def set_turn(user_text: Any) -> Token:
    """Register the URLs the user just wrote, and start a fresh read budget.

    Safe to call repeatedly: every call site runs as a request starts, before
    any tool. The budget used to be shared over ``READ_WINDOW_SECONDS``, so an
    answer that read two pages left a follow-up a minute later -- "read the
    reddit post more thoroughly" -- refused at the reading limit (29 September).
    """
    allow(urls_in(user_text))
    return _current.set(_ReadState(text=str(user_text or "")))


@contextmanager
def scope(user_text: Any) -> Iterator[None]:
    """Bind in the task/thread that actually runs this turn's tools."""
    token = set_turn(user_text)
    try:
        yield
    finally:
        _current.reset(token)


def turn_text() -> str:
    """The user's latest message, as given to :func:`set_turn`."""
    with _lock:
        return _state().text


#: Social and meme sites (2 October: 7 s spent reading an x.com photo page
#: about a memecoin for a question about an AI model). Not opened, and listed
#: after articles, unless the user's message is about social media itself.
_SOCIAL_HOSTS = (
    "x.com",
    "twitter.com",
    "facebook.com",
    "fb.com",
    "fb.watch",
    "tiktok.com",
    "instagram.com",
    "threads.net",
    "9gag.com",
    "imgur.com",
    "knowyourmeme.com",
)
_ASKS_SOCIAL = re.compile(
    r"\b(x\.com|on x|twitter|tweets?|facebook|fb|tiktok|instagram|ig|threads|"
    r"social(?:s| media)?|memes?|viral|9gag|imgur|reels?)\b",
    re.IGNORECASE,
)


def is_social(url: str) -> bool:
    try:
        host = (
            urlsplit(url if "://" in str(url) else "https://" + str(url)).hostname or ""
        ).lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in _SOCIAL_HOSTS)


def social_wanted() -> bool:
    """Whether this message asks about social media, so social pages count."""
    return bool(_ASKS_SOCIAL.search(turn_text()))


def is_allowed(url: str) -> bool:
    """Whether *url* was named by the user or returned by a recent search."""
    normalised = normalise(url)
    if not normalised:
        return False
    now = time.monotonic()
    with _lock:
        _expire(now)
        return normalised in _allowed


def note_read() -> None:
    now = time.monotonic()
    with _lock:
        _expire(now)
        _state().reads.append(now + READ_WINDOW_SECONDS)


def reserve_read(url: str, limit: int) -> str:
    """Take one of this message's *limit* reads for *url*, atomically.

    Returns ``"ok"``, ``"limit"`` or ``"duplicate"``. Reads asked for in one
    response run at the same time, so checking the count and recording the
    read separately let every one of them through the check.
    """
    now = time.monotonic()
    normalised = normalise(url)
    with _lock:
        _expire(now)
        state = _state()
        if normalised and normalised in state.urls:
            return "duplicate"
        if len(state.reads) >= limit:
            return "limit"
        state.reads.append(now + READ_WINDOW_SECONDS)
        if normalised:
            state.urls.add(normalised)
        return "ok"


def readable_urls(limit: int = 8) -> List[str]:
    """Pages that may be read right now, newest permission first."""
    now = time.monotonic()
    with _lock:
        _expire(now)
        ordered = sorted(_allowed.items(), key=lambda item: item[1], reverse=True)
    return [url for url, _ in ordered[:limit]]


def reads_used() -> int:
    now = time.monotonic()
    with _lock:
        _expire(now)
        return len(_state().reads)


def _expire(now: float) -> None:
    for url in [url for url, until in _allowed.items() if until <= now]:
        del _allowed[url]
    for url in [url for url, until in _searched.items() if until <= now]:
        del _searched[url]
    state = _state()
    state.reads[:] = [until for until in state.reads if until > now]


def clear() -> None:
    """Drop everything remembered. For tests."""
    with _lock:
        _allowed.clear()
        _searched.clear()
        _current.set(None)


__all__ = [
    "ALLOW_SECONDS",
    "READ_WINDOW_SECONDS",
    "allow",
    "allow_search_results",
    "clear",
    "from_search",
    "is_allowed",
    "normalise",
    "note_read",
    "reads_used",
    "set_turn",
    "scope",
    "urls_in",
]
