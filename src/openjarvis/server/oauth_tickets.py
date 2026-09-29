"""Single-use tokens for the browser half of connector OAuth.

The consent popup (``/oauth/start``) and the provider's redirect back
(``/oauth/callback``) are plain browser navigations, which cannot send the
``Authorization`` header, so with an API key set both got a 401. The auth
middleware therefore lets those two paths through, and these tokens are the
credential instead:

- a *ticket*, issued to the authenticated app (``POST /oauth/ticket``) and
  spent by ``/oauth/start``, so only the app can begin a sign-in;
- an OAuth *state*, issued by ``/oauth/start`` and spent by
  ``/oauth/callback``, so only the provider's answer to that sign-in is
  accepted.

Each is random (256 bits), bound to one connector, used once, and short-lived.
They live in memory and die with the server, like the ``/v1/speech/audio/``
tokens.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Dict, Tuple

TICKET_TTL_S = 300.0
# Consent can take a while (account picker, 2FA), so the state outlives the ticket.
STATE_TTL_S = 900.0


class OneTimeTokens:
    """Random tokens bound to a connector id, each valid once until it expires."""

    def __init__(self, ttl_s: float) -> None:
        self._ttl_s = ttl_s
        self._tokens: Dict[str, Tuple[str, float]] = {}
        self._lock = threading.Lock()

    def issue(self, connector_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._tokens[token] = (connector_id, now + self._ttl_s)
        return token

    def consume(self, token: str, connector_id: str) -> bool:
        """Spend *token*; true only if it was issued for *connector_id* and is live."""
        if not token:
            return False
        with self._lock:
            entry = self._tokens.pop(token, None)
        if entry is None:
            return False
        owner, expires = entry
        return owner == connector_id and time.monotonic() < expires

    def _prune(self, now: float) -> None:
        for token in [t for t, (_, exp) in self._tokens.items() if exp <= now]:
            del self._tokens[token]


tickets = OneTimeTokens(TICKET_TTL_S)
states = OneTimeTokens(STATE_TTL_S)
