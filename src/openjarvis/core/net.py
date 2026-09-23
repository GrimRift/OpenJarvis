"""Outbound connections try IPv4 before IPv6.

This machine resolves AAAA first and its IPv6 route is poor: measured on 23
September to api.openai.com, a TCP and TLS handshake took ~80 ms over IPv4
and 0.45 to 4 s over IPv6. The OpenAI client drops a connection idle for
5 s, so in conversation nearly every turn opened a new one over IPv6 --
the first word of a one-line answer took 3.5 to 10 s, and a bare
``models.retrieve`` 6.6 s on a cold connection against 0.6 s warm.
Sorting the addresses keeps IPv6 as the fallback; nothing that only has
IPv6 is lost.
"""

from __future__ import annotations

import socket
from typing import Any

_original_getaddrinfo = socket.getaddrinfo


def _ipv4_first(*args: Any, **kwargs: Any):
    results = _original_getaddrinfo(*args, **kwargs)
    return sorted(results, key=lambda info: 0 if info[0] == socket.AF_INET else 1)


def prefer_ipv4() -> None:
    """Install the ordering for this process. Safe to call more than once."""
    if socket.getaddrinfo is not _ipv4_first:
        socket.getaddrinfo = _ipv4_first  # type: ignore[assignment]


__all__ = ["prefer_ipv4"]
