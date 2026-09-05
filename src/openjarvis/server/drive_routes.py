"""Private phone handoff: one shared navigation call and optional Sage audio."""

from __future__ import annotations

import asyncio
import base64
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from openjarvis.server.auth_middleware import _api_keys_match
from openjarvis.speech.spoken_text import to_spoken_text
from openjarvis.speech.voice_profiles import FRIEREN, JARVIS
from openjarvis.tools.navigate import NavigateTool

router = APIRouter(prefix="/v1/drive", tags=["drive"])


class Coordinates(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class DriveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    destination: str = Field(min_length=1, max_length=300)
    origin: Coordinates
    destination_coordinates: Coordinates | None = None
    place_id: str | None = Field(default=None, max_length=300)
    voice: Literal["jarvis", "frieren"] = "jarvis"
    include_audio: bool = True


def authorize_drive(request: Request) -> None:
    # Unlike local chat, the phone endpoint must never run with auth disabled.
    key = getattr(request.app.state, "api_key", "")
    if not key:
        raise HTTPException(
            503, "Configure Sage server authentication before phone use."
        )
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not _api_keys_match(token, key):
        raise HTTPException(401, "Valid Sage authorization is required.")


def _audio(text: str, voice: str) -> dict:
    from openjarvis.speech.cartesia_tts import CartesiaTTSBackend

    profile = FRIEREN if voice == "frieren" else JARVIS
    result = CartesiaTTSBackend().synthesize(
        to_spoken_text(text),
        voice_id=profile.voice_id,
        speed=profile.speed,
        volume=profile.volume,
        output_format="mp3",
    )
    if not result.audio or result.format != "mp3":
        raise ValueError("No playable MP3 returned")
    # Inline audio avoids another authenticated download and persistent files.
    return {
        "base64": base64.b64encode(result.audio).decode("ascii"),
        "mime_type": "audio/mpeg",
        "voice": profile.name,
    }


@router.post("", dependencies=[Depends(authorize_drive)])
async def drive(body: DriveRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    params = body.model_dump(exclude_none=True, exclude={"voice", "include_audio"})
    result = await asyncio.to_thread(NavigateTool().execute, **params)
    if not result.success:
        raise HTTPException(400, result.content)
    payload = {
        **result.metadata,
        "message": result.content,
        "audio": None,
        "audio_status": "not_requested",
    }
    if payload.get("status") != "ready":
        payload["audio_status"] = "needs_selection"
        return payload
    if body.include_audio:
        try:
            payload["audio"] = await asyncio.to_thread(
                _audio,
                result.metadata["briefing"],
                body.voice,
            )
            payload["audio_status"] = "ready"
        except Exception:
            # Keep usable navigation if TTS fails; never return provider errors.
            payload["audio_status"] = "unavailable"
    return payload
