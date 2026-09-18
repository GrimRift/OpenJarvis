"""Cartesia text-to-speech backend.

Uses the Cartesia REST API for high-quality, low-latency voice synthesis.
Requires CARTESIA_API_KEY environment variable or config.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import time
from types import TracebackType
from typing import Any, AsyncIterator, List
from uuid import uuid4

import httpx
import websockets

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.tts import TTSBackend, TTSResult
from openjarvis.speech.voice_profiles import (
    CARTESIA_API_VERSION,
    DEFAULT_TTS_MODEL,
    DEFAULT_VOICE,
)

logger = logging.getLogger(__name__)

_CARTESIA_API_BASE = "https://api.cartesia.ai"
_CARTESIA_WEBSOCKET_URL = "wss://api.cartesia.ai/tts/websocket"

#: A spoken reply used to open a brand new websocket to Cartesia, and the
#: TLS handshake alone measured 0.59 s of the ~1 s before any audio could
#: start (18 September; Cartesia's edge is 9 ms away, so this is handshake
#: cost, not distance). During a voice conversation one socket is opened
#: ahead of time and handed to the next turn.
#:
#: Parked rather than pooled: a Cartesia socket carries one turn's context
#: and is closed with it, so what is reused is the *connection*, once.
_warm_socket: Any = None
_warm_opened_at: float = 0.0

#: Past this a parked socket is assumed stale and is replaced. Cartesia ends
#: idle connections on its own schedule, and a socket that died quietly costs
#: a failed turn, which is worse than the handshake it saved.
WARM_TTL_SECONDS = 120.0


def _warm_is_fresh() -> bool:
    if _warm_socket is None:
        return False
    if time.monotonic() - _warm_opened_at > WARM_TTL_SECONDS:
        return False
    # `state` is OPEN(1) while usable; websockets sets it on close.
    closed = getattr(_warm_socket, "close_code", None) is not None
    return not closed


async def warm_connection(api_key: str) -> bool:
    """Open a socket now so the next spoken turn does not pay for one.

    Safe to call repeatedly: a fresh parked socket is kept as it is. Returns
    whether a usable socket is parked afterwards. Never raises -- warming is
    an optimisation, and a failure here must not cost a reply.
    """
    global _warm_socket, _warm_opened_at
    if _warm_is_fresh():
        return True
    await drop_warm_connection()
    try:
        _warm_socket = await _connect(api_key)
        _warm_opened_at = time.monotonic()
        return True
    except Exception:
        logger.debug("Cartesia warm-up failed; turns will connect on demand")
        _warm_socket = None
        return False


async def drop_warm_connection() -> None:
    """Close the parked socket, if any. Called when a conversation ends."""
    global _warm_socket
    socket, _warm_socket = _warm_socket, None
    if socket is not None:
        with contextlib.suppress(Exception):
            await socket.close()


def _take_warm() -> Any:
    """Hand the parked socket to a turn, or None."""
    global _warm_socket
    if not _warm_is_fresh():
        return None
    socket, _warm_socket = _warm_socket, None
    return socket


async def _connect(api_key: str) -> Any:
    return await websockets.connect(
        _CARTESIA_WEBSOCKET_URL,
        additional_headers={
            "X-API-Key": api_key,
            "Cartesia-Version": CARTESIA_API_VERSION,
        },
        open_timeout=10,
        close_timeout=5,
        ping_interval=20,
        ping_timeout=20,
        max_size=1_048_576,
        max_queue=16,
        write_limit=32_768,
    )


def _cartesia_synthesize(
    api_key: str,
    text: str,
    voice_id: str,
    model: str = DEFAULT_TTS_MODEL,
    output_format: str = "mp3",
    speed: float = 1.0,
    volume: float = 1.0,
    emotion: str = "",
    language: str = "en",
    bit_rate: int | None = None,
) -> bytes:
    """Call the Cartesia TTS API and return raw audio bytes."""
    resp = httpx.post(
        f"{_CARTESIA_API_BASE}/tts/bytes",
        headers={
            "X-API-Key": api_key,
            "Cartesia-Version": CARTESIA_API_VERSION,
        },
        json={
            "model_id": model,
            "transcript": text,
            "voice": {"mode": "id", "id": voice_id},
            "output_format": {
                "container": output_format,
                "sample_rate": 24000,
                "encoding": "mp3" if output_format == "mp3" else "pcm_f32le",
                # Cartesia accepts only 32000 and 64000 for mp3; 48000 is
                # rejected outright, so callers pass one of the two or nothing.
                **({"bit_rate": bit_rate} if bit_rate else {}),
            },
            "language": language,
            "generation_config": {
                "speed": speed,
                "volume": volume,
                **({"emotion": emotion} if emotion else {}),
            },
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.content


# The SSE endpoint rejects mp3 — "only 'raw' container is supported" — so
# streaming always yields raw PCM and the caller is responsible for playback.
STREAM_SAMPLE_RATE = 24000
STREAM_ENCODING = "pcm_f32le"


class CartesiaTTSContext:
    """One turn-scoped Cartesia WebSocket continuation context.

    The API key is sent only as a server-side connection header. Every text
    submission repeats identical synthesis settings, as Cartesia requires;
    only the transcript and continuation marker vary within the context.
    """

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        *,
        context_id: str = "",
        model: str = DEFAULT_TTS_MODEL,
        speed: float = 1.0,
        volume: float = 1.0,
        language: str = "en",
    ) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self._context_id = context_id or str(uuid4())
        self._model = model
        self._speed = speed
        self._volume = volume
        self._language = language
        self._socket: Any = None
        self._inputs_finished = False
        self._done = False
        self._cancelled = False
        self._flushes = 0

    @property
    def context_id(self) -> str:
        return self._context_id

    @property
    def flushes(self) -> int:
        """How many submitted transcripts Cartesia has finished speaking.

        Lets a caller tell which of its sends actually became audio. When an
        idle context is rebuilt mid-turn, the transcripts sent to it but never
        flushed are the ones that would otherwise be lost silently.
        """
        return self._flushes

    #: Whether this turn was handed an already-open socket. Read by the
    #: route's timing log: "slow to start" is answered by which of the two
    #: happened, not by guessing.
    used_warm: bool = False

    async def __aenter__(self) -> "CartesiaTTSContext":
        # A socket warmed during this conversation, if one is waiting: it is
        # the handshake that made the voice late, not the audio.
        warm = _take_warm()
        self.used_warm = warm is not None
        self._socket = warm or await _connect(self._api_key)
        if warm is not None:
            # Warmth carries through the conversation: this turn used the
            # parked socket, so open the next one now rather than making the
            # user's follow-up pay the handshake again. A conversation that
            # ends simply leaves one socket to expire on its TTL.
            with contextlib.suppress(RuntimeError):
                asyncio.get_running_loop().create_task(
                    warm_connection(self._api_key)
                )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._socket is not None:
            await self._socket.close()
            self._socket = None

    def _generation_payload(self, transcript: str, continue_: bool) -> dict[str, Any]:
        return {
            "model_id": self._model,
            "transcript": transcript,
            "voice": {"mode": "id", "id": self._voice_id},
            "output_format": {
                "container": "raw",
                "sample_rate": STREAM_SAMPLE_RATE,
                "encoding": STREAM_ENCODING,
            },
            "language": self._language,
            "context_id": self._context_id,
            "continue": continue_,
            # Sage already buffers complete sentences/clauses. Asking
            # Cartesia to wait another three seconds defeats the latency win.
            "max_buffer_delay_ms": 0,
            "generation_config": {
                "speed": self._speed,
                "volume": self._volume,
            },
        }

    async def send_text(self, text: str) -> None:
        if self._inputs_finished or self._cancelled:
            raise RuntimeError("Cartesia context is closed")
        if not text:
            return
        await self._socket.send(json.dumps(self._generation_payload(text, True)))

    async def finish(self) -> None:
        if self._inputs_finished or self._cancelled:
            return
        self._inputs_finished = True
        await self._socket.send(json.dumps(self._generation_payload("", False)))

    async def cancel(self) -> None:
        if self._cancelled or self._done or self._socket is None:
            return
        self._cancelled = True
        await self._socket.send(
            json.dumps({"context_id": self._context_id, "cancel": True})
        )

    async def receive_audio(self) -> AsyncIterator[bytes]:
        if self._socket is None:
            raise RuntimeError("Cartesia context is not connected")
        async for raw in self._socket:
            try:
                event = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if event.get("context_id") not in (None, self._context_id):
                continue
            if event.get("type") == "error":
                message = event.get("message") or event.get("error") or "unknown error"
                raise RuntimeError(f"Cartesia stream failed: {message}")
            if event.get("type") == "flush_done":
                self._flushes += 1
            data = event.get("data")
            if event.get("type") == "chunk" and data:
                yield base64.b64decode(data)
            if event.get("type") == "done" or event.get("done") is True:
                self._done = True
                return


async def astream_pcm(
    api_key: str,
    text: str,
    voice_id: str,
    *,
    model: str = DEFAULT_TTS_MODEL,
    speed: float = 1.0,
    volume: float = 1.0,
    language: str = "en",
) -> AsyncIterator[bytes]:
    """Yield raw PCM chunks as Cartesia produces them.

    Measured against the batch endpoint on the same paragraph: first audio at
    0.41s versus 6.92s of silence, because ``/tts/bytes`` returns nothing until
    the whole clip is synthesized. Total time barely moves; when playback can
    *start* is the entire point.

    Generation runs about 4.3x faster than playback (29.6s of audio in 6.95s),
    so once the first chunk lands the stream cannot starve.
    """
    body = {
        "model_id": model,
        "transcript": text,
        "voice": {"mode": "id", "id": voice_id},
        "output_format": {
            "container": "raw",
            "sample_rate": STREAM_SAMPLE_RATE,
            "encoding": STREAM_ENCODING,
        },
        "language": language,
        "generation_config": {"speed": speed, "volume": volume},
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            f"{_CARTESIA_API_BASE}/tts/sse",
            headers={"X-API-Key": api_key, "Cartesia-Version": CARTESIA_API_VERSION},
            json=body,
        ) as resp:
            if resp.status_code != 200:
                await resp.aread()
                raise RuntimeError(
                    f"Cartesia stream failed ({resp.status_code}): {resp.text[:200]}"
                )
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except ValueError:
                    continue
                chunk = event.get("data")
                if chunk:
                    yield base64.b64decode(chunk)
                if event.get("done"):
                    break


@TTSRegistry.register("cartesia")
class CartesiaTTSBackend(TTSBackend):
    """Cartesia TTS backend — fast, high-quality synthesis."""

    backend_id = "cartesia"

    def __init__(
        self, *, api_key: str = "", model: str = DEFAULT_TTS_MODEL, language: str = "en"
    ) -> None:
        self._api_key = api_key or os.environ.get("CARTESIA_API_KEY", "")
        self._model = model
        self._language = language or os.environ.get("CARTESIA_LANGUAGE", "en")

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        volume: float = 1.0,
        emotion: str = "",
        output_format: str = "mp3",
        language: str = "",
        bit_rate: int | None = None,
    ) -> TTSResult:
        if not self._api_key:
            raise RuntimeError("CARTESIA_API_KEY not set")

        if not voice_id:
            voice_id = DEFAULT_VOICE.voice_id

        audio = _cartesia_synthesize(
            self._api_key,
            text,
            voice_id=voice_id,
            model=self._model,
            output_format=output_format,
            speed=speed,
            volume=volume,
            emotion=emotion,
            language=language or self._language,
            bit_rate=bit_rate,
        )

        return TTSResult(
            audio=audio,
            format=output_format,
            voice_id=voice_id,
            metadata={"backend": "cartesia", "model": self._model},
        )

    def available_voices(self) -> List[str]:
        if not self._api_key:
            return []
        resp = httpx.get(
            f"{_CARTESIA_API_BASE}/voices",
            headers={
                "X-API-Key": self._api_key,
                "Cartesia-Version": CARTESIA_API_VERSION,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        return [v["id"] for v in resp.json()]

    def health(self) -> bool:
        return bool(self._api_key)
