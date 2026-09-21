"""Streaming text-to-speech relay.

The batch endpoint (``/tts/bytes``) returns nothing until the whole clip is
synthesized: measured at 1.55s for a one-line reply and 6.92s for a paragraph,
all of it silence. Streaming puts the first audio at 0.41s, so Sage starts
speaking while the rest is still being generated.

The Cartesia key stays server-side, exactly as the Flux proxy does — the
browser talks only to this endpoint.

Two providers speak the same protocol to the browser: Cartesia (cloud) and
Chatterbox (a local sidecar, see ``speech/chatterbox_tts``). ``begin``
names one; the server's ``[speech] tts_provider`` is the default. Both
deliver ``pcm_f32le`` at 24 kHz, so the player is none the wiser.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from openjarvis.core import activity
from openjarvis.speech.cartesia_tts import (
    STREAM_ENCODING,
    STREAM_SAMPLE_RATE,
    CartesiaTTSContext,
)
from openjarvis.speech.providers import default_tts_provider
from openjarvis.speech.spoken_text import (
    SpokenTextOverflow,
    SpokenTextStream,
    to_spoken_text,
)
from openjarvis.speech.voice_profiles import DEFAULT_VOICE

logger = logging.getLogger(__name__)

router = APIRouter(tags=["speech"])

CLOSE_UNAUTHORIZED = 1008
CLOSE_UNAVAILABLE = 1011

# A reply is a couple of hundred words at most. Anything beyond this is a bug
# or an attempt to run up a bill, so it is refused rather than truncated —
# a half-spoken answer is worse than a clear failure.
MAX_TEXT_CHARS = 5000
MAX_TURN_CHARS = 20_000
MAX_PENDING_RAW_CHARS = 4096
MAX_PENDING_SEGMENTS = 8


class _ClientCancelled(Exception):
    pass


PROVIDER_CARTESIA = "cartesia"
PROVIDER_CHATTERBOX = "chatterbox"
PROVIDERS = (PROVIDER_CARTESIA, PROVIDER_CHATTERBOX)


def _resolve_provider(config: Any, requested: Any) -> str:
    value = str(requested or "").strip().lower()
    if value in PROVIDERS:
        return value
    default = default_tts_provider(getattr(config, "speech", None))
    return default if default in PROVIDERS else PROVIDER_CARTESIA


def _open_context(provider: str, config: Any, api_key: str, request: dict) -> Any:
    """A fresh streaming context for *provider*; both share one contract."""
    voice = _resolve_voice(config, request.get("voice_id", ""))
    if provider == PROVIDER_CHATTERBOX:
        from openjarvis.speech.chatterbox_tts import ChatterboxContext

        return ChatterboxContext(getattr(config, "speech", None), voice)
    return CartesiaTTSContext(
        api_key,
        voice,
        speed=_resolve_speed(config, request.get("speed")),
        volume=_resolve_volume(config, request.get("volume")),
    )


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


#: A reply's audio waits this long for a server-side voice (a greeting, a
#: reminder) to finish before it starts; the text streams on screen
#: regardless. Past this it plays anyway rather than hang the reply.
SERVER_VOICE_WAIT_SECONDS = 30.0


async def wait_for_server_voice(timeout: float = SERVER_VOICE_WAIT_SECONDS) -> None:
    """The reverse of ``player._wait_for_the_floor``: the browser's reply
    does not start over a voice the server is already playing."""
    from openjarvis.speech.player import is_speaking

    deadline = asyncio.get_running_loop().time() + timeout
    while is_speaking() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.1)


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

    config = getattr(websocket.app.state, "config", None)
    api_key = os.environ.get("CARTESIA_API_KEY", "")

    try:
        request = await websocket.receive_json()
        provider = _resolve_provider(config, request.get("provider"))
        if provider == PROVIDER_CARTESIA and not api_key:
            # Reported rather than closed silently: the client falls back to
            # the batch endpoint on this message, so the user still hears
            # the reply.
            await websocket.send_json(
                {"type": "error", "reason": "CARTESIA_API_KEY not set"}
            )
            await websocket.close(code=CLOSE_UNAVAILABLE)
            return

        if request.get("type") == "begin":
            # Initiative (M37) must not start a conversation over a reply
            # being spoken; this is the one place the server sees one.
            activity.tts_begin()
            try:
                await _stream_incremental_turn(
                    websocket, config, api_key, request, provider=provider
                )
            except (WebSocketDisconnect, _ClientCancelled):
                raise
            except Exception as exc:  # noqa: BLE001
                # Opening the context failed (typically the local sidecar is
                # not up). Said out loud so the browser takes its batch path
                # rather than reading a bare close as a network fault.
                logger.warning("TTS stream could not open %s: %s", provider, exc)
                with contextlib.suppress(Exception):
                    await websocket.send_json(
                        {"type": "error", "reason": str(exc), "started": False}
                    )
                    await websocket.close(code=CLOSE_UNAVAILABLE)
            finally:
                activity.tts_end()
            return

        # Backward-compatible whole-transcript request for older clients and
        # focused fallback tests. The current browser uses the incremental
        # begin/text/finish protocol below.
        while True:
            # Flattened before the length check so a markdown table is not
            # refused for characters that would never be spoken anyway.
            text = to_spoken_text(request.get("text") or "")
            if not text:
                await websocket.send_json({"type": "error", "reason": "empty text"})
                request = await websocket.receive_json()
                continue
            if len(text) > MAX_TEXT_CHARS:
                await websocket.send_json({"type": "error", "reason": "text too long"})
                request = await websocket.receive_json()
                continue

            activity.tts_begin()
            try:
                await _speak(
                    websocket, config, api_key, text, request, provider=provider
                )
            finally:
                activity.tts_end()
            request = await websocket.receive_json()
            provider = _resolve_provider(config, request.get("provider"))
    except WebSocketDisconnect:
        return
    except Exception:  # noqa: BLE001
        logger.debug("TTS stream socket error", exc_info=True)
        try:
            await websocket.close(code=CLOSE_UNAVAILABLE)
        except Exception:  # noqa: BLE001
            pass


#: How many times one turn may rebuild its Cartesia context.
#:
#: Cartesia ends an idle continuation context on its own and sends ``done``.
#: Measured directly against the API: with no further text, ``done`` arrived
#: 6.14s after the transcript on three consecutive runs, and 4.05s after
#: ``flush_done`` on another -- the window is real but not a fixed number, so
#: it cannot be out-waited by tuning a keepalive interval.
#:
#: That mattered because a spoken reply is fed incrementally as the model
#: streams. A reply that opens with "Let me search for that", then stalls on a
#: tool call, hands Cartesia nothing for the length of that call -- and a
#: *second* Tavily search (the escalation for thin evidence) reliably outruns
#: the window. The context ended, ``receive_audio`` returned, and the socket
#: was torn down mid-sentence with the rest of the answer never spoken.
#:
#: Bounded rather than unbounded so a genuinely broken context cannot spin.
MAX_CONTEXT_REBUILDS = 5


async def _stream_incremental_turn(
    websocket: WebSocket,
    config: Any,
    api_key: str,
    begin: dict,
    *,
    provider: str = PROVIDER_CARTESIA,
) -> None:
    """Buffer model deltas into safe speech on one streaming context."""
    segment_queue: asyncio.Queue[tuple[str, bool] | None] = asyncio.Queue(
        maxsize=MAX_PENDING_SEGMENTS
    )
    segmenter = SpokenTextStream(max_pending_chars=MAX_PENDING_RAW_CHARS)
    total_chars = 0
    started = False
    finished = False
    inputs_finished = False
    tasks: list[asyncio.Task[Any]] = []

    context = _open_context(provider, config, api_key, begin)

    # ``context`` stays the object the `async with` will close. The live one
    # can be replaced mid-turn when Cartesia times out an idle context, so
    # every send goes through ``current`` under the lock rather than closing
    # over the original.
    current = context
    context_lock = asyncio.Lock()
    rebuilds = 0
    # Transcripts handed to the live context, in order. Cartesia acknowledges
    # each one with `flush_done`, so anything past `current.flushes` was sent
    # but never spoken -- which is exactly what a rebuild would otherwise
    # drop. Seen as a 3s gap still failing while 6s and 20s recovered: the
    # next sentence landed inside the ~0.2s window between the send and the
    # context ending.
    sent_to_current: list[str] = []
    # Whether the queue's end-of-input sentinel has actually been consumed.
    # `inputs_finished` only says the *client* said finish; segments queued
    # behind that flag are still on their way, and finishing a rebuilt
    # context before they land closes it under them ("Cartesia context is
    # closed", the whole turn lost).
    finish_consumed = False

    def _fresh_context() -> Any:
        return _open_context(provider, config, api_key, begin)

    async with context:
        await websocket.send_json(
            {
                "type": "ready",
                "sample_rate": STREAM_SAMPLE_RATE,
                "encoding": STREAM_ENCODING,
            }
        )

        async def receive_text() -> None:
            nonlocal inputs_finished, total_chars
            while True:
                message = await websocket.receive_json()
                kind = message.get("type")
                if kind == "cancel":
                    raise _ClientCancelled
                if kind == "finish":
                    if inputs_finished:
                        continue
                    inputs_finished = True
                    for tail in segmenter.finish():
                        await segment_queue.put((tail, True))
                    await segment_queue.put(None)
                    # Keep receiving until Cartesia's audio is fully drained.
                    # Stop must still cancel this context after model text has
                    # finished but while queued speech remains audible.
                    continue
                if kind != "text":
                    continue
                if inputs_finished:
                    continue
                delta = message.get("delta")
                if not isinstance(delta, str) or not delta:
                    continue
                total_chars += len(delta)
                if total_chars > MAX_TURN_CHARS:
                    raise SpokenTextOverflow("speech turn too long")
                for segment in segmenter.push(delta):
                    # Cartesia concatenates continuations verbatim. Preserve a
                    # word boundary after every completed sentence/clause.
                    await segment_queue.put((segment + " ", False))

        async def send_segments() -> None:
            nonlocal finish_consumed
            while True:
                item = await segment_queue.get()
                try:
                    if item is None:
                        async with context_lock:
                            await current.finish()
                            finish_consumed = True
                        return
                    segment, _is_tail = item
                    async with context_lock:
                        sent_to_current.append(segment)
                        await current.send_text(segment)
                finally:
                    segment_queue.task_done()

        async def relay_audio() -> None:
            nonlocal started, current, rebuilds
            while True:
                speaking = current
                async for chunk in speaking.receive_audio():
                    if not started:
                        started = True
                        await wait_for_server_voice()
                        await websocket.send_json(
                            {
                                "type": "start",
                                "sample_rate": STREAM_SAMPLE_RATE,
                                "encoding": STREAM_ENCODING,
                            }
                        )
                    await websocket.send_bytes(chunk)

                # The audio stream ended. Whether the turn is over is not
                # "did the client say finish" -- it is "has everything handed
                # to Cartesia actually been spoken". A sentence submitted in
                # the ~0.2s before an idle context ends is acknowledged by
                # nothing, and treating `finish` as the end dropped it: a 3.0s
                # gap kept failing while 2.5s and 3.2s recovered.
                async with context_lock:
                    if current is not speaking:
                        continue
                    unspoken = sent_to_current[speaking.flushes :]
                    # Over only when no more text is coming *and* everything
                    # sent has been spoken. Either half alone is wrong: after
                    # `finish` a sentence can still be unacknowledged, and an
                    # idle context that spoke everything is still needed for
                    # the text that has not arrived yet.
                    if inputs_finished and not unspoken:
                        return
                    if rebuilds >= MAX_CONTEXT_REBUILDS:
                        return
                    replacement = _fresh_context()
                    await replacement.__aenter__()
                    current = replacement
                    sent_to_current.clear()
                    rebuilds += 1
                    for segment in unspoken:
                        sent_to_current.append(segment)
                        await replacement.send_text(segment)
                    if finish_consumed:
                        # The sentinel is already spent, so nothing else will
                        # finish this one. Only safe here: while segments are
                        # still queued, `send_segments` keeps feeding the
                        # replacement and finishes it when it reaches the end.
                        await replacement.finish()
                if speaking is not context:
                    with contextlib.suppress(Exception):
                        await speaking.__aexit__(None, None, None)
                logger.info(
                    "Cartesia context timed out mid-turn; rebuilt (%d/%d), "
                    "re-sent %d unspoken segment(s)",
                    rebuilds,
                    MAX_CONTEXT_REBUILDS,
                    len(unspoken),
                )

        input_task = asyncio.create_task(receive_text())
        sender_task = asyncio.create_task(send_segments())
        audio_task = asyncio.create_task(relay_audio())

        async def await_output() -> None:
            await asyncio.gather(sender_task, audio_task)

        output_task = asyncio.create_task(await_output())
        tasks = [input_task, sender_task, audio_task, output_task]
        try:
            done, _pending = await asyncio.wait(
                {input_task, output_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if input_task in done:
                # Normal input completion intentionally keeps listening for
                # Stop, so this path is a cancellation, disconnect, or error.
                await input_task
                raise RuntimeError("incremental TTS input ended unexpectedly")
            await output_task
            finished = True
            input_task.cancel()
            await asyncio.gather(input_task, return_exceptions=True)
            await websocket.send_json({"type": "done"})
        except _ClientCancelled:
            await current.cancel()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await websocket.send_json({"type": "cancelled"})
        except WebSocketDisconnect:
            await current.cancel()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        except Exception as exc:  # noqa: BLE001
            await current.cancel()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.warning("Incremental TTS stream failed: %s", exc)
            try:
                await websocket.send_json(
                    {"type": "error", "reason": str(exc), "started": started}
                )
            except Exception:  # noqa: BLE001
                pass
        finally:
            if not finished:
                await current.cancel()


async def _whole_utterance(
    provider: str, config: Any, api_key: str, text: str, request: dict
):
    """One utterance as a PCM stream, whichever provider."""
    if provider == PROVIDER_CHATTERBOX:
        context = _open_context(provider, config, api_key, request)
        async with context:
            await context.send_text(text)
            await context.finish()
            async for chunk in context.receive_audio():
                yield chunk
        return
    from openjarvis.speech.cartesia_tts import astream_pcm

    async for chunk in astream_pcm(
        api_key,
        text,
        _resolve_voice(config, request.get("voice_id", "")),
        speed=_resolve_speed(config, request.get("speed")),
        volume=_resolve_volume(config, request.get("volume")),
    ):
        yield chunk


async def _speak(
    websocket: WebSocket,
    config: Any,
    api_key: str,
    text: str,
    request: dict,
    *,
    provider: str = PROVIDER_CARTESIA,
) -> None:
    """Stream one utterance. Errors before the first chunk are recoverable."""
    started = False
    try:
        stream = _whole_utterance(provider, config, api_key, text, request)
        async for chunk in stream:
            if not started:
                started = True
                await wait_for_server_voice()
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


__all__ = [
    "MAX_PENDING_SEGMENTS",
    "MAX_TEXT_CHARS",
    "MAX_TURN_CHARS",
    "router",
]
