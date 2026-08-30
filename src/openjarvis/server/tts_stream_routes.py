"""Streaming text-to-speech relay.

The batch endpoint (``/tts/bytes``) returns nothing until the whole clip is
synthesized: measured at 1.55s for a one-line reply and 6.92s for a paragraph,
all of it silence. Streaming puts the first audio at 0.41s, so Sage starts
speaking while the rest is still being generated.

The Cartesia key stays server-side, exactly as the Flux proxy does — the
browser talks only to this endpoint.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from openjarvis.speech.spoken_text import to_spoken_text
from openjarvis.speech.voice_profiles import DEFAULT_VOICE

logger = logging.getLogger(__name__)

router = APIRouter(tags=["speech"])

CLOSE_UNAUTHORIZED = 1008
CLOSE_UNAVAILABLE = 1011

# A reply is a couple of hundred words at most. Anything beyond this is a bug
# or an attempt to run up a bill, so it is refused rather than truncated —
# a half-spoken answer is worse than a clear failure.
MAX_TEXT_CHARS = 5000


def _resolve_voice(config: Any, requested: str) -> str:
    if requested:
        return requested
    speech = getattr(config, "speech", None)
    return getattr(speech, "voice_id", "") or DEFAULT_VOICE.voice_id


def _resolve_speed(config: Any, requested: Optional[float]) -> float:
    if isinstance(requested, (int, float)) and requested > 0:
        return float(requested)
    speech = getattr(config, "speech", None)
    speed = getattr(speech, "voice_speed", 1.0)
    return float(speed) if isinstance(speed, (int, float)) and speed > 0 else 1.0


def _resolve_volume(config: Any, requested: Optional[float]) -> float:
    if isinstance(requested, (int, float)) and requested > 0:
        return float(requested)
    speech = getattr(config, "speech", None)
    volume = getattr(speech, "voice_volume", 1.0)
    return float(volume) if isinstance(volume, (int, float)) and volume > 0 else 1.0


@router.websocket("/v1/speech/tts-stream")
async def tts_stream(websocket: WebSocket) -> None:
    """Relay Cartesia's PCM stream to the browser as it is produced."""
    from openjarvis.server.auth_middleware import authenticate_websocket

    expected_key = getattr(websocket.app.state, "api_key", "")
    authorized, subprotocol = authenticate_websocket(websocket, expected_key)
    if not authorized:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return

    await websocket.accept(subprotocol=subprotocol)

    api_key = os.environ.get("CARTESIA_API_KEY", "")
    if not api_key:
        # Reported rather than closed silently: the client falls back to the
        # batch endpoint on this message, so the user still hears the reply.
        await websocket.send_json(
            {"type": "error", "reason": "CARTESIA_API_KEY not set"}
        )
        await websocket.close(code=CLOSE_UNAVAILABLE)
        return

    config = getattr(websocket.app.state, "config", None)

    try:
        while True:
            request = await websocket.receive_json()
            # Flattened before the length check so a markdown table is not
            # refused for characters that would never be spoken anyway.
            text = to_spoken_text(request.get("text") or "")
            if not text:
                await websocket.send_json({"type": "error", "reason": "empty text"})
                continue
            if len(text) > MAX_TEXT_CHARS:
                await websocket.send_json({"type": "error", "reason": "text too long"})
                continue

            await _speak(websocket, config, api_key, text, request)
    except WebSocketDisconnect:
        return
    except Exception:  # noqa: BLE001
        logger.debug("TTS stream socket error", exc_info=True)
        try:
            await websocket.close(code=CLOSE_UNAVAILABLE)
        except Exception:  # noqa: BLE001
            pass


async def _speak(
    websocket: WebSocket,
    config: Any,
    api_key: str,
    text: str,
    request: dict,
) -> None:
    """Stream one utterance. Errors before the first chunk are recoverable."""
    from openjarvis.speech.cartesia_tts import (
        STREAM_ENCODING,
        STREAM_SAMPLE_RATE,
        astream_pcm,
    )

    started = False
    try:
        stream = astream_pcm(
            api_key,
            text,
            _resolve_voice(config, request.get("voice_id", "")),
            speed=_resolve_speed(config, request.get("speed")),
            volume=_resolve_volume(config, request.get("volume")),
        )
        async for chunk in stream:
            if not started:
                started = True
                await websocket.send_json(
                    {
                        "type": "start",
                        "sample_rate": STREAM_SAMPLE_RATE,
                        "encoding": STREAM_ENCODING,
                    }
                )
            await websocket.send_bytes(chunk)
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("TTS stream failed: %s", exc)
        # Only useful before audio started. Once the user is hearing the
        # reply, restarting it from the top through the batch path would
        # speak the beginning twice.
        await websocket.send_json(
            {"type": "error", "reason": str(exc), "started": started}
        )
        return

    await websocket.send_json({"type": "done"})


__all__ = ["router", "MAX_TEXT_CHARS"]
