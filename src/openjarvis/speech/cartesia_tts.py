"""Cartesia text-to-speech backend.

Uses the Cartesia REST API for high-quality, low-latency voice synthesis.
Requires CARTESIA_API_KEY environment variable or config.
"""

from __future__ import annotations

import base64
import json
import os
from typing import AsyncIterator, List

import httpx

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.tts import TTSBackend, TTSResult
from openjarvis.speech.voice_profiles import (
    CARTESIA_API_VERSION,
    DEFAULT_TTS_MODEL,
    DEFAULT_VOICE,
)

_CARTESIA_API_BASE = "https://api.cartesia.ai"


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
