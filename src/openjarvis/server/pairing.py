"""Pairing a phone or iPad with Sage (M29).

A phone reaches Sage over Tailscale, but the API still wants the server's key,
and typing that by hand on a phone is the step nobody finishes. Instead the
desktop app -- already holding the key -- asks for a pairing code
(``POST /v1/pairing/code``), shows it, and the phone sends it back
(``POST /v1/pairing/redeem``) to receive the key.

A code is six digits, lives five minutes, is spent by one correct redeem, and
dies after ``MAX_WRONG`` wrong guesses. Only one is live at a time: asking for
another replaces it. Codes live in memory and die with the server.

Six digits is a small space on purpose (it is typed on a phone). What keeps it
safe is that a code exists only for five minutes after the user asked for one,
from an app that already had the key, and that guessing kills it: five tries
at a million codes is a 1 in 200,000 chance per code the user made.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

CODE_TTL_S = 300.0
MAX_WRONG = 5
CODE_DIGITS = 6


@dataclass
class _Live:
    code: str
    expires: float
    wrong: int = 0


class PairingCodes:
    """The one live pairing code, if any."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._live: Optional[_Live] = None
        self._lock = threading.Lock()

    def issue(self) -> tuple[str, float]:
        """A new code, replacing any live one; returns it and seconds to live."""
        code = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
        with self._lock:
            self._live = _Live(code, self._clock() + CODE_TTL_S)
        return code, CODE_TTL_S

    def redeem(self, guess: str) -> str:
        """``"ok"`` (the code is now spent), ``"wrong"``, or ``"none"`` when no
        code is live -- never made, expired, spent, or guessed to death."""
        guess = "".join(ch for ch in str(guess or "") if ch.isdigit())
        with self._lock:
            live = self._live
            if live is None or self._clock() >= live.expires:
                self._live = None
                return "none"
            if len(guess) == CODE_DIGITS and secrets.compare_digest(guess, live.code):
                self._live = None
                return "ok"
            live.wrong += 1
            if live.wrong >= MAX_WRONG:
                self._live = None
            return "wrong"

    def cancel(self) -> None:
        with self._lock:
            self._live = None


codes = PairingCodes()


def tailnet_address(run: Optional[Callable[[], str]] = None) -> str:
    """This PC's HTTPS address on the tailnet (``https://<name>.ts.net/``), or
    "" when Tailscale is not installed, not running, or has no name."""
    import json
    import shutil
    import subprocess

    def _default() -> str:
        exe = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
        out = subprocess.run(
            [exe, "status", "--json"],
            capture_output=True,
            # UTF-8, not the Windows code page: device names in the status
            # broke the cp1252 decode and the address came back empty.
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return out.stdout

    try:
        data = json.loads((run or _default)() or "{}")
    except Exception:
        return ""
    name = str((data.get("Self") or {}).get("DNSName") or "").rstrip(".")
    return f"https://{name}/" if name.endswith(".ts.net") else ""
