"""Deepgram Flux streaming speech-to-text — model-based end-of-turn detection.

Distinct from ``speech/deepgram.py``, which posts a finished recording to the
prerecorded API. Flux is a live socket: audio goes up continuously and the
server decides when a turn ended, replacing the client-side silence timer.

Nothing here talks to the browser. The key is read from the environment on the
server, and ``server/flux_routes.py`` proxies audio so it never reaches the
client. This module is transport only — it reports what Deepgram said and
takes no view on what to do about it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import urlencode, urlparse

logger = logging.getLogger(__name__)

FLUX_URL = "wss://api.deepgram.com/v2/listen"

# Deepgram rejects the connection outright when these are out of range, or
# when eager exceeds eot. Checked here so a misconfiguration surfaces as a
# clear local error instead of an opaque socket close mid-turn.
EOT_THRESHOLD_RANGE = (0.5, 0.9)
EAGER_EOT_THRESHOLD_RANGE = (0.3, 0.9)
EOT_TIMEOUT_MS_RANGE = (500, 60000)

# Flux expects raw 16-bit mono PCM. 16 kHz matches what the wake-word socket
# already captures, so the browser needs no second capture format.
SAMPLE_RATE = 16000
ENCODING = "linear16"

# Server event names, from the Flux reference. Kept as constants because a
# typo in one of these silently means "turn never ends".
EVENT_START_OF_TURN = "StartOfTurn"
EVENT_UPDATE = "Update"
EVENT_EAGER_END_OF_TURN = "EagerEndOfTurn"
EVENT_TURN_RESUMED = "TurnResumed"
EVENT_END_OF_TURN = "EndOfTurn"


class FluxConfigError(ValueError):
    """Raised when threshold settings would be rejected by Deepgram."""


@dataclass
class TurnEvent:
    """One ``TurnInfo`` message, normalised.

    ``turn_index`` is the identity everything downstream keys on: speculative
    work started for one turn must never be released against another.
    """

    event: str
    turn_index: int
    transcript: str = ""
    end_of_turn_confidence: float = 0.0
    audio_window_start: float = 0.0
    audio_window_end: float = 0.0
    words: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_final(self) -> bool:
        return self.event == EVENT_END_OF_TURN

    @property
    def is_speculative(self) -> bool:
        return self.event == EVENT_EAGER_END_OF_TURN

    @property
    def cancels_speculation(self) -> bool:
        return self.event == EVENT_TURN_RESUMED

    @classmethod
    def from_message(cls, data: Dict[str, Any]) -> "TurnEvent":
        def _f(key: str) -> float:
            # Deepgram sends these as strings ("0.85"), not numbers.
            try:
                return float(data.get(key) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        return cls(
            event=str(data.get("event") or ""),
            turn_index=int(data.get("turn_index") or 0),
            transcript=str(data.get("transcript") or ""),
            end_of_turn_confidence=_f("end_of_turn_confidence"),
            audio_window_start=_f("audio_window_start"),
            audio_window_end=_f("audio_window_end"),
            words=list(data.get("words") or []),
            raw=data,
        )


def api_key() -> str:
    """Server-side key. Never returned to a client or written to disk."""
    return os.environ.get("DEEPGRAM_API_KEY", "")


def is_available() -> bool:
    """Whether Flux could be used at all, without attempting a connection."""
    if not api_key():
        return False
    try:
        import websockets  # noqa: F401
    except ImportError:
        return False
    return True


def validate_thresholds(
    eot_threshold: float,
    eager_eot_threshold: Optional[float],
    eot_timeout_ms: int,
) -> None:
    """Reject settings Deepgram would reject, with a local message.

    ``eager_eot_threshold`` above ``eot_threshold`` is called out explicitly
    in Deepgram's docs as an error, and it is the easy one to get wrong.
    """
    lo, hi = EOT_THRESHOLD_RANGE
    if not lo <= eot_threshold <= hi:
        raise FluxConfigError(
            f"eot_threshold {eot_threshold} outside Deepgram's range {lo}-{hi}"
        )
    lo, hi = EOT_TIMEOUT_MS_RANGE
    if not lo <= eot_timeout_ms <= hi:
        raise FluxConfigError(
            f"eot_timeout_ms {eot_timeout_ms} outside Deepgram's range {lo}-{hi}"
        )
    if eager_eot_threshold is None:
        return
    lo, hi = EAGER_EOT_THRESHOLD_RANGE
    if not lo <= eager_eot_threshold <= hi:
        raise FluxConfigError(
            f"eager_eot_threshold {eager_eot_threshold} outside Deepgram's "
            f"range {lo}-{hi}"
        )
    if eager_eot_threshold > eot_threshold:
        raise FluxConfigError(
            f"eager_eot_threshold ({eager_eot_threshold}) must be <= "
            f"eot_threshold ({eot_threshold}); Deepgram rejects the connection "
            "otherwise"
        )


def build_url(
    *,
    model: str,
    eot_threshold: float,
    eager_eot_threshold: Optional[float],
    eot_timeout_ms: int,
) -> str:
    """Compose the Flux socket URL. Eager is omitted entirely when disabled."""
    validate_thresholds(eot_threshold, eager_eot_threshold, eot_timeout_ms)
    params: Dict[str, Any] = {
        "model": model,
        "encoding": ENCODING,
        "sample_rate": SAMPLE_RATE,
        "eot_threshold": eot_threshold,
        "eot_timeout_ms": eot_timeout_ms,
    }
    # Presence of the parameter is what turns speculation on, so it must be
    # absent — not zero, not empty — in Standard mode.
    if eager_eot_threshold is not None:
        params["eager_eot_threshold"] = eager_eot_threshold
    return f"{FLUX_URL}?{urlencode(params)}"


#: How long to wait for Deepgram's handshake before giving up and letting the
#: caller fall back to local transcription.
#:
#: The library default is long enough that a bad path to Deepgram is felt as
#: Sage simply not responding: measured on one machine, one connection in five
#: succeeded and that one took 19s, the rest timing out at 21-30s. Every voice
#: turn then began with a stall before local transcription took over, which
#: reads as "it didn't hear me". A short bound turns that into a fallback the
#: user barely notices; anything slower than this is useless for live speech
#: anyway, so there is nothing to lose by not waiting for it.
CONNECT_TIMEOUT_SECONDS = 4.0


#: How long an address that failed to connect is passed over when a
#: known-good alternative exists. Deepgram's bad address stayed bad across
#: two days of measurement, so this is deliberately much longer than a
#: transient blip: it is remembering a broken route, not rate-limiting.
BAD_ADDRESS_SECONDS = 600.0

#: How long an address that connected successfully stays a retry candidate.
#: The point of remembering one at all is that a *fresh* resolution cannot be
#: obtained -- see ``connect_candidates`` -- so this has to outlive the DNS
#: TTL that caused the problem (measured at 853s on this machine).
GOOD_ADDRESS_SECONDS = 3600.0

#: ip -> last time a connection to it succeeded / failed.
_address_ok: Dict[str, float] = {}
_address_bad: Dict[str, float] = {}


def note_address_ok(address: str) -> None:
    """Record that *address* accepted a connection."""
    if not address:
        return
    _address_ok[address] = time.monotonic()
    _address_bad.pop(address, None)


def note_address_failed(address: str) -> None:
    """Record that *address* did not accept a connection."""
    if not address:
        return
    _address_bad[address] = time.monotonic()
    _address_ok.pop(address, None)


def _is_recently_bad(address: str, now: Optional[float] = None) -> bool:
    stamp = _address_bad.get(address)
    if stamp is None:
        return False
    current = time.monotonic() if now is None else now
    return (current - stamp) < BAD_ADDRESS_SECONDS


def _reset_address_memory() -> None:
    """Clear both tables. Process-global state, so tests must reset it."""
    _address_ok.clear()
    _address_bad.clear()


def flux_host(url: str = FLUX_URL) -> str:
    """The hostname the Flux socket connects to."""
    return urlparse(url).hostname or ""


def resolve_addresses(host: str = "") -> List[str]:
    """Every address the resolver currently returns for *host*.

    Deliberately not the whole story, and that is the bug this exists to work
    around: ``api.deepgram.com`` answers with a *single* rotating A record and
    Windows caches it for the record's TTL (measured at 853s here), so every
    call inside that window returns the same one address. Resolving again is
    therefore not a way to reach a different server.
    """
    name = host or flux_host()
    if not name:
        return []
    try:
        infos = socket.getaddrinfo(name, 443, type=socket.SOCK_STREAM)
    except OSError:
        logger.debug("Could not resolve %s", name, exc_info=True)
        return []
    seen: List[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in seen:
            seen.append(addr)
    return seen


def connect_candidates(limit: int = 2, host: str = "") -> List[str]:
    """Addresses to try, in order, for one connection.

    One of Deepgram's rotating addresses is unreachable from some networks --
    ``38.68.64.132`` timed out 0/4 here while three siblings connected in
    ~0.4s -- and the resolver hands out one address at a time, cached for its
    TTL. So a plain retry re-dials the *same* dead address for the whole TTL
    window, which is what turned a bad rotation into ~14 minutes of voice
    turns that never started.

    Breaking that needs an address the resolver is not currently offering, so
    addresses that have connected before are remembered and used as the
    fallback. A currently-resolved address that recently failed is demoted
    rather than dropped: if every address is failing, the network is down and
    trying the obvious one is still the right move.
    """
    now = time.monotonic()
    resolved = resolve_addresses(host)

    preferred = [a for a in resolved if not _is_recently_bad(a, now)]
    demoted = [a for a in resolved if _is_recently_bad(a, now)]

    remembered = sorted(
        (
            addr
            for addr, stamp in _address_ok.items()
            if addr not in resolved
            and (now - stamp) < GOOD_ADDRESS_SECONDS
            and not _is_recently_bad(addr, now)
        ),
        key=lambda addr: _address_ok[addr],
        reverse=True,
    )

    ordered: List[str] = []
    for addr in preferred + remembered + demoted:
        if addr not in ordered:
            ordered.append(addr)
    return ordered[:limit] if limit > 0 else ordered


async def open_socket(address: str, timeout: float) -> socket.socket:
    """A connected TCP socket to *address*, without blocking the loop.

    Bound separately from the TLS/WebSocket handshake so a dead address costs
    only the TCP timeout, and so the caller knows which address it got.
    """
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(loop.sock_connect(sock, (address, 443)), timeout)
    except BaseException:
        sock.close()
        raise
    return sock


class FluxSession:
    """One Deepgram Flux connection for one voice session.

    Audio in, :class:`TurnEvent` out. Deliberately holds no policy about
    speculation or tools — the caller decides what an event means.
    """

    def __init__(
        self,
        *,
        model: str = "flux-general-en",
        eot_threshold: float = 0.7,
        eager_eot_threshold: Optional[float] = None,
        eot_timeout_ms: int = 5000,
        key: Optional[str] = None,
    ) -> None:
        self._url = build_url(
            model=model,
            eot_threshold=eot_threshold,
            eager_eot_threshold=eager_eot_threshold,
            eot_timeout_ms=eot_timeout_ms,
        )
        self._key = key if key is not None else api_key()
        self._ws: Any = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def connect(self, address: str = "") -> None:
        """Open the Flux socket, optionally against a specific *address*.

        Passing ``sock=`` bypasses the resolver for this connection only.
        ``websockets`` still takes SNI and certificate validation from the
        URI's hostname, so pinning the address does not weaken TLS -- a
        wrong or hostile address fails the handshake exactly as it should.
        """
        if not self._key:
            raise RuntimeError("DEEPGRAM_API_KEY is not set")
        import websockets

        kwargs: Dict[str, Any] = {
            "additional_headers": {"Authorization": f"Token {self._key}"},
            "open_timeout": CONNECT_TIMEOUT_SECONDS,
        }
        sock: Optional[socket.socket] = None
        if address:
            sock = await open_socket(address, CONNECT_TIMEOUT_SECONDS)
            kwargs["sock"] = sock

        try:
            self._ws = await websockets.connect(self._url, **kwargs)
        except BaseException:
            if sock is not None:
                sock.close()
            raise

    async def send_audio(self, chunk: bytes) -> None:
        """Forward one PCM chunk. No-op once closed, so a late frame from a
        finished turn cannot resurrect the socket."""
        if self._ws is None:
            return
        await self._ws.send(chunk)

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                logger.debug("Flux socket close failed", exc_info=True)

    async def events(self) -> AsyncIterator[TurnEvent]:
        """Yield turn events until the socket closes.

        Only ``TurnInfo`` is surfaced. ``Connected``/``ConfigureSuccess`` are
        housekeeping, and an ``Error`` is raised so the caller can fall back
        to local rather than waiting on a turn that will never end.
        """
        if self._ws is None:
            raise RuntimeError("Flux session is not connected")
        async for raw in self._ws:
            if isinstance(raw, bytes):
                continue
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                logger.debug("Unparseable Flux message: %r", raw[:200])
                continue
            kind = data.get("type")
            if kind == "TurnInfo":
                yield TurnEvent.from_message(data)
            elif kind in ("Error", "ConfigureFailure"):
                raise RuntimeError(
                    f"Deepgram Flux error: {data.get('code')} {data.get('description')}"
                )


__all__ = [
    "BAD_ADDRESS_SECONDS",
    "GOOD_ADDRESS_SECONDS",
    "EAGER_EOT_THRESHOLD_RANGE",
    "ENCODING",
    "EOT_THRESHOLD_RANGE",
    "EOT_TIMEOUT_MS_RANGE",
    "EVENT_EAGER_END_OF_TURN",
    "EVENT_END_OF_TURN",
    "EVENT_START_OF_TURN",
    "EVENT_TURN_RESUMED",
    "EVENT_UPDATE",
    "FLUX_URL",
    "SAMPLE_RATE",
    "FluxConfigError",
    "FluxSession",
    "TurnEvent",
    "api_key",
    "build_url",
    "connect_candidates",
    "flux_host",
    "is_available",
    "note_address_failed",
    "note_address_ok",
    "open_socket",
    "resolve_addresses",
    "validate_thresholds",
]
