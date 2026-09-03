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

Kept in a short-lived process-level table rather than a ``ContextVar``, and
that was measured rather than assumed. A ContextVar set in the request handler
is gone by the time a tool runs: ``AuthMiddleware`` is a
``BaseHTTPMiddleware``, so the endpoint runs in one task while the response
body streams from another, and the agent loop then crosses into worker
threads. Traced live, ``set_turn`` ran on ``MainThread`` and the check ran on
``ThreadPoolExecutor-3_0`` with nothing bound -- after binding it in three
separate places, it still never reached the tool.

What the table gives up is turn isolation: a URL stays readable for a few
minutes rather than for exactly one turn. What it keeps is the property that
actually matters -- a URL is only ever added when the user wrote it or a
search returned it, so a link found inside an email, or inside a page just
read, is never in the table and can never be followed.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, Iterable, List, Set
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

#: How long a read counts against the cap in ``web_read``.
READ_WINDOW_SECONDS = 300.0

_lock = threading.Lock()
_allowed: Dict[str, float] = {}
_reads: List[float] = []


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


def set_turn(user_text: Any) -> None:
    """Register the URLs the user just wrote. Safe to call repeatedly."""
    allow(urls_in(user_text))


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
        _reads.append(now + READ_WINDOW_SECONDS)


def reads_used() -> int:
    now = time.monotonic()
    with _lock:
        _expire(now)
        return len(_reads)


def _expire(now: float) -> None:
    for url in [url for url, until in _allowed.items() if until <= now]:
        del _allowed[url]
    _reads[:] = [until for until in _reads if until > now]


def clear() -> None:
    """Drop everything remembered. For tests."""
    with _lock:
        _allowed.clear()
        _reads.clear()


__all__ = [
    "ALLOW_SECONDS",
    "READ_WINDOW_SECONDS",
    "allow",
    "clear",
    "is_allowed",
    "normalise",
    "note_read",
    "reads_used",
    "set_turn",
    "urls_in",
]
