"""Authenticated proxy between the browser and Deepgram Flux.

The browser never sees ``DEEPGRAM_API_KEY``. It opens an authenticated socket
here, sends the same 16 kHz mono PCM frames the wake-word socket already
uses, and receives turn events back as JSON. This process holds the key and
talks to Deepgram.

Only turn events cross back. Deepgram's ``Connected``/``ConfigureSuccess``
housekeeping is dropped, and a Flux failure closes the socket with a reason
the client can show while it falls back to local transcription — losing the
utterance silently would be worse than a visible downgrade.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from openjarvis.speech import flux
from openjarvis.speech.speculative import SpeculativeManager, generate_speculative

logger = logging.getLogger(__name__)

router = APIRouter(tags=["speech"])

# Close codes the client distinguishes. 1008 is reserved for auth failure by
# the existing wake-word socket, so reuse it for consistency.
CLOSE_UNAUTHORIZED = 1008
CLOSE_UNAVAILABLE = 1011


@router.websocket("/v1/speech/flux")
async def flux_stream(websocket: WebSocket) -> None:
    """Stream microphone audio to Deepgram Flux and relay turn events back.

    Query parameter ``eager=1`` enables speculative ``EagerEndOfTurn`` events;
    absent, the eager threshold is never sent, so Deepgram does not speculate.
    """
    from openjarvis.server.auth_middleware import authenticate_websocket

    expected_key = getattr(websocket.app.state, "api_key", "")
    authorized, subprotocol = authenticate_websocket(websocket, expected_key)
    if not authorized:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return

    config = getattr(websocket.app.state, "config", None)
    speech_cfg = getattr(config, "speech", None) if config else None

    if not flux.is_available() or not getattr(speech_cfg, "flux_enabled", True):
        # Accept then close with a reason, so the client can show "Flux
        # unavailable, using local" rather than a bare handshake failure.
        await websocket.accept(subprotocol=subprotocol)
        await websocket.send_json(
            {"type": "FluxUnavailable", "reason": _unavailable_reason(speech_cfg)}
        )
        await websocket.close(code=CLOSE_UNAVAILABLE)
        return

    # The client asks for speculation; the server config can forbid it. Both
    # must agree, and the server flag allows by default for the same reason
    # flux_enabled does — otherwise the Settings toggle could never take
    # effect without hand-editing config.toml.
    eager_requested = websocket.query_params.get("eager") in ("1", "true", "yes")
    eager_threshold: Optional[float] = None
    if eager_requested and getattr(speech_cfg, "flux_eager_enabled", True):
        eager_threshold = float(
            getattr(speech_cfg, "flux_eager_eot_threshold", 0.6)
        )

    try:
        session = flux.FluxSession(
            model=getattr(speech_cfg, "flux_model", "flux-general-en"),
            eot_threshold=float(getattr(speech_cfg, "flux_eot_threshold", 0.7)),
            eager_eot_threshold=eager_threshold,
            eot_timeout_ms=int(getattr(speech_cfg, "flux_eot_timeout_ms", 5000)),
        )
    except flux.FluxConfigError as exc:
        await websocket.accept(subprotocol=subprotocol)
        await websocket.send_json({"type": "FluxUnavailable", "reason": str(exc)})
        await websocket.close(code=CLOSE_UNAVAILABLE)
        return

    await websocket.accept(subprotocol=subprotocol)

    try:
        await session.connect()
    except Exception as exc:
        logger.warning("Flux connect failed: %s", exc)
        with contextlib.suppress(Exception):
            await websocket.send_json(
                {"type": "FluxUnavailable", "reason": f"connect failed: {exc}"}
            )
            await websocket.close(code=CLOSE_UNAVAILABLE)
        return

    # Guarded separately from the main loop below, whose own
    # `except WebSocketDisconnect` starts only after this point. A client that
    # navigates away between accept() and this first frame — routine when
    # switching between the Chat and Voice pages — escaped that guard and
    # surfaced as an unhandled ASGI exception in the server log.
    try:
        await websocket.send_json(
            {"type": "FluxReady", "eager": eager_threshold is not None}
        )
    except WebSocketDisconnect:
        await session.close()
        return

    async def pump_audio() -> None:
        """Browser -> Deepgram. Ends when the client stops or disconnects."""
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            data = message.get("bytes")
            if data:
                await session.send_audio(data)
                continue
            text = message.get("text")
            if text == "stop":
                # The client ends transmission between turns rather than
                # streaming idle microphone audio.
                return

    # Speculation is managed here rather than in the browser, so speculative
    # text never crosses the wire at all until a turn is confirmed. The
    # client cannot display, speak, or act on what it never receives.
    speculator = SpeculativeManager()
    spec_task: Optional[asyncio.Task] = None

    def cancel_speculation() -> None:
        nonlocal spec_task
        speculator.cancel("TurnResumed")
        if spec_task is not None and not spec_task.done():
            spec_task.cancel()
        spec_task = None

    async def speculate(turn_index: int, transcript: str) -> None:
        """Generate an answer that is buffered and may never be used."""
        spec = speculator.begin(turn_index, transcript)
        if spec is None:
            return
        engine = getattr(websocket.app.state, "engine", None)
        model = getattr(websocket.app.state, "model", "")
        if engine is None or not model:
            return
        try:
            result = await asyncio.to_thread(
                generate_speculative,
                engine,
                model=model,
                transcript=transcript,
                # Without this the speculative path answered at 0.3 while
                # every other reply used the configured temperature, so an
                # open-ended request ("tell me a joke") came back with the
                # same wording every time and looked like a cached answer.
                temperature=_configured_temperature(config),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Speculative generation failed", exc_info=True)
            return
        # Late completions from a cancelled generation are dropped by
        # generation id rather than by whether the task was awaited.
        speculator.append(spec.generation_id, str(result.get("content") or ""))

    async def pump_events() -> None:
        """Deepgram -> browser. Only turn events are relayed."""
        nonlocal spec_task
        async for event in session.events():
            if event.cancels_speculation:
                cancel_speculation()
            elif event.is_speculative and eager_threshold is not None:
                cancel_speculation()
                spec_task = asyncio.create_task(
                    speculate(event.turn_index, event.transcript)
                )

            payload: dict = {
                "type": "TurnInfo",
                "event": event.event,
                "turn_index": event.turn_index,
                "transcript": event.transcript,
                "end_of_turn_confidence": event.end_of_turn_confidence,
                "audio_window_start": event.audio_window_start,
                "audio_window_end": event.audio_window_end,
            }

            if event.is_final:
                # Release only against the confirmed turn and transcript.
                # Anything else means the client runs the normal agent path.
                if spec_task is not None:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(spec_task, timeout=0.25)
                answer = speculator.release(event.turn_index, event.transcript)
                spec_task = None
                if answer:
                    payload["speculative_answer"] = answer

            await websocket.send_json(payload)

    audio_task = asyncio.create_task(pump_audio())
    events_task = asyncio.create_task(pump_events())
    try:
        done, pending = await asyncio.wait(
            {audio_task, events_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            # Surface a Flux-side error so the client can fall back rather
            # than waiting on a turn that will never end.
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                logger.warning("Flux stream ended with an error: %s", exc)
                with contextlib.suppress(Exception):
                    await websocket.send_json(
                        {"type": "FluxError", "reason": str(exc)}
                    )
    except WebSocketDisconnect:
        pass
    finally:
        for task in (audio_task, events_task):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await session.close()


def _configured_temperature(config: Any) -> float:
    """The same temperature ordinary replies use."""
    intelligence = getattr(config, "intelligence", None)
    value = getattr(intelligence, "temperature", None)
    return float(value) if isinstance(value, (int, float)) else 0.7


def _unavailable_reason(speech_cfg: Any) -> str:
    """Why Flux cannot be used. Never includes the key itself.

    Order matters: the missing key is the common case and the one the user
    can act on, so it is reported before the server-side kill switch.
    """
    if not flux.api_key():
        return "DEEPGRAM_API_KEY is not configured on the server"
    if not getattr(speech_cfg, "flux_enabled", True):
        return "Flux is disabled on the server ([speech] flux_enabled)"
    return "Flux dependencies are not installed"


__all__ = ["router"]
