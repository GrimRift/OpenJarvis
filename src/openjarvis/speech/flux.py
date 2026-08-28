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

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import urlencode

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

    async def connect(self) -> None:
        if not self._key:
            raise RuntimeError("DEEPGRAM_API_KEY is not set")
        import websockets

        self._ws = await websockets.connect(
            self._url,
            additional_headers={"Authorization": f"Token {self._key}"},
        )

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
                    f"Deepgram Flux error: {data.get('code')} "
                    f"{data.get('description')}"
                )


__all__ = [
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
    "is_available",
    "validate_thresholds",
]
