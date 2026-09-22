"""HTTP + WebSocket surface of the voice sidecar. See the package docstring."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import JSONResponse, Response
from starlette.websockets import WebSocketDisconnect, WebSocketState

from voice_sidecar import __version__
from voice_sidecar.engine import SAMPLE_RATE, ChatterboxEngine, VoiceParams, VoiceStore

logger = logging.getLogger("voice_sidecar")

# Audio goes out in slices this long so the first sound of a sentence is
# heard while the rest of its samples are still crossing the socket.
CHUNK_SAMPLES = SAMPLE_RATE // 5  # 200 ms
MAX_SEGMENT_CHARS = 1200
# Segments shorter than this are merged with what follows (see the worker).
SHORT_SEGMENT_CHARS = 28
SHORT_SEGMENT_WAIT = 0.2


def create_app(engine: ChatterboxEngine, default_voice: str) -> FastAPI:
    app = FastAPI(title="Sage voice sidecar", version=__version__)
    app.state.engine = engine
    app.state.default_voice = default_voice

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        voices = engine.voices.names()
        current = engine.current_voice or default_voice
        return {
            "ok": engine.loaded,
            "model": "chatterbox-nano",
            "version": __version__,
            "device": engine.device,
            "requested_device": engine.requested_device,
            "precision": engine.precision,
            "model_loaded": engine.loaded,
            "load_seconds": engine.load_seconds,
            "voice": current,
            "voice_ready": current in voices,
            "voices": voices,
            "sample_rate": SAMPLE_RATE,
            "generations": engine.generations,
            "last_generation_seconds": engine.last_generation_seconds,
        }

    @app.get("/voices")
    async def list_voices() -> Dict[str, Any]:
        return {
            "default": default_voice,
            "current": engine.current_voice,
            "voices": [asdict(engine.voices.info(n)) for n in engine.voices.names()],
        }

    @app.post("/voices/{name}")
    async def upload_voice(name: str, file: UploadFile = File(...)) -> Dict[str, Any]:
        suffix = Path(file.filename or "reference.wav").suffix or ".wav"
        data = await file.read()
        if len(data) > 50 * 1024 * 1024:
            raise HTTPException(413, "reference larger than 50 MB")
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            await asyncio.to_thread(engine.voices.store_reference, name, tmp_path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(400, f"could not read the recording: {exc}") from exc
        finally:
            tmp_path.unlink(missing_ok=True)
        # A re-uploaded current voice must be re-conditioned.
        if engine.current_voice == engine.voices.path(name).name:
            engine.current_voice = None
        if engine.loaded:
            await asyncio.to_thread(engine.use_voice, name)
        return asdict(engine.voices.info(name))

    @app.put("/voices/{name}/params")
    async def set_params(name: str, request: Request) -> Dict[str, Any]:
        body = await request.json()
        if name not in engine.voices.names():
            raise HTTPException(404, f"no voice {name!r}")
        engine.voices.save_params(name, VoiceParams.from_dict(body or {}))
        return asdict(engine.voices.info(name))

    @app.delete("/voices/{name}")
    async def delete_voice(name: str) -> Dict[str, Any]:
        removed = engine.voices.remove(name)
        if engine.current_voice == engine.voices.path(name).name:
            engine.current_voice = None
        return {"removed": removed}

    @app.post("/synthesize")
    async def synthesize(request: Request) -> Response:
        body = await request.json()
        text = str(body.get("text") or "").strip()
        voice = str(body.get("voice") or default_voice)
        if not text:
            raise HTTPException(400, "text is required")
        if len(text) > 5000:
            raise HTTPException(413, "text longer than 5000 characters")
        try:
            audio = await asyncio.to_thread(engine.generate, text, voice)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(content=_wav_bytes(audio), media_type="audio/wav")

    @app.websocket("/stream")
    async def stream(websocket: WebSocket) -> None:
        await websocket.accept()
        await _stream_reply(websocket, engine, default_voice)

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error")
        return JSONResponse({"error": str(exc)}, status_code=500)

    return app


async def _stream_reply(
    websocket: WebSocket, engine: ChatterboxEngine, default_voice: str
):
    """One reply: text segments in, audio out, in order, until finish/cancel.

    ``generation`` is the identity every piece of audio is stamped with. A
    cancel bumps it; the worker discards whatever it finishes afterwards.
    """
    voice = default_voice
    generation = 0
    segments: asyncio.Queue[Optional[str]] = asyncio.Queue()
    closed = asyncio.Event()

    async def send_json(payload: Dict[str, Any]) -> None:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json(payload)

    async def worker() -> None:
        nonlocal generation
        while not closed.is_set():
            item = await segments.get()
            if item is None:
                await send_json({"type": "done", "generation": generation})
                continue
            my_generation, text = item  # type: ignore[misc]
            if my_generation != generation:
                continue  # queued before a cancel
            # A one- or two-word segment ("Sir.", "Checking.") comes out
            # robotic on its own: too little text for the model to settle a
            # cadence. If the next segment of the same reply is already here
            # or arrives within a moment, say them together.
            while len(text) < SHORT_SEGMENT_CHARS:
                try:
                    following = await asyncio.wait_for(
                        segments.get(), timeout=SHORT_SEGMENT_WAIT
                    )
                except asyncio.TimeoutError:
                    break
                if following is None or following[0] != my_generation:
                    await segments.put(following)  # not ours to merge
                    break
                text = f"{text.rstrip()} {following[1].lstrip()}"
            # A long sentence is generated in clause-sized pieces. Measured
            # 22 September: a 1.1 s opener followed by a 6.6 s sentence left
            # 650 ms of silence mid-reply, because the whole sentence had to
            # finish generating (1.75 s) before any of it could play. Pieces
            # of ~1-2 s each arrive faster than they play out.
            total_seconds = 0.0
            gen_seconds = 0.0
            failed = False
            for piece in split_for_generation(text):
                try:
                    audio = await asyncio.to_thread(engine.generate, piece, voice)
                except Exception as exc:
                    logger.exception("generation failed")
                    await send_json(
                        {
                            "type": "error",
                            "reason": str(exc),
                            "generation": my_generation,
                        }
                    )
                    failed = True
                    break
                if my_generation != generation:
                    break  # cancelled while on the GPU: never send it
                gen_seconds += engine.last_generation_seconds
                total_seconds += len(audio) / SAMPLE_RATE
                for start in range(0, len(audio), CHUNK_SAMPLES):
                    if my_generation != generation:
                        break
                    if websocket.client_state != WebSocketState.CONNECTED:
                        return
                    await websocket.send_bytes(
                        audio[start : start + CHUNK_SAMPLES].tobytes()
                    )
            if failed:
                continue
            if my_generation == generation:
                await send_json(
                    {
                        "type": "segment_done",
                        "generation": my_generation,
                        "seconds": round(total_seconds, 3),
                        "generation_seconds": round(gen_seconds, 3),
                    }
                )

    task = asyncio.create_task(worker())
    try:
        while True:
            message = await websocket.receive_text()
            try:
                data = json.loads(message)
            except ValueError:
                await send_json({"type": "error", "reason": "malformed message"})
                continue
            kind = data.get("type")
            if kind == "begin":
                voice = str(data.get("voice") or default_voice)
                generation += 1
                try:
                    await asyncio.to_thread(engine.use_voice, voice)
                except Exception as exc:
                    await send_json(
                        {"type": "error", "reason": str(exc), "fatal": True}
                    )
                    break
                await send_json(
                    {
                        "type": "ready",
                        "generation": generation,
                        "sample_rate": SAMPLE_RATE,
                        "encoding": "pcm_f32le",
                        "voice": engine.current_voice,
                    }
                )
            elif kind == "text":
                text = str(data.get("text") or "")[:MAX_SEGMENT_CHARS]
                if text.strip():
                    await segments.put((generation, text))  # type: ignore[arg-type]
            elif kind == "finish":
                await segments.put(None)
            elif kind == "cancel":
                generation += 1
                _drain(segments)
                await send_json({"type": "cancelled", "generation": generation})
            else:
                await send_json(
                    {"type": "error", "reason": f"unknown message {kind!r}"}
                )
    except WebSocketDisconnect:
        pass
    finally:
        closed.set()
        generation += 1
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


# Above this many characters a segment is split at clause punctuation.
# ~55 ms of speech per character here, so 90 chars is about 5 s of audio and
# ~1.3 s of generation; the pieces it becomes are 1-2 s each.
SPLIT_OVER_CHARS = 90
_CLAUSE_BREAK = re.compile(r"(?<=[,;:])\s+|(?<=\s[-–—])\s+")


def split_for_generation(text: str) -> list[str]:
    """Clause-sized pieces of a long segment; a short one is returned whole.

    Splits only at punctuation a speaker would pause on, and never leaves a
    fragment under ~20 characters on its own (it would be voiced as a
    stranded word), so prosody survives the cut.
    """
    text = text.strip()
    if len(text) <= SPLIT_OVER_CHARS:
        return [text] if text else []
    pieces: list[str] = []
    current = ""
    for part in _CLAUSE_BREAK.split(text):
        part = part.strip()
        if not part:
            continue
        if (
            current
            and len(current) + 1 + len(part) > SPLIT_OVER_CHARS
            and len(current) >= 20
        ):
            pieces.append(current)
            current = part
        else:
            current = f"{current} {part}".strip()
    if current:
        if pieces and len(current) < 20:
            pieces[-1] = f"{pieces[-1]} {current}"
        else:
            pieces.append(current)
    return pieces


def _drain(queue: "asyncio.Queue[Any]") -> None:
    while not queue.empty():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break


def _wav_bytes(audio: np.ndarray) -> bytes:
    import soundfile as sf

    buffer = io.BytesIO()
    sf.write(buffer, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Sage voice sidecar (Chatterbox Nano)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("VOICE_SIDECAR_PORT", 8791))
    )
    parser.add_argument(
        "--device", default=os.environ.get("VOICE_SIDECAR_DEVICE", "cuda")
    )
    parser.add_argument(
        "--precision",
        choices=["fp32", "fp16"],
        default=os.environ.get("VOICE_SIDECAR_PRECISION", "fp32"),
    )
    parser.add_argument(
        "--voices-dir", default=os.environ.get("VOICE_SIDECAR_VOICES", "")
    )
    parser.add_argument(
        "--voice", default=os.environ.get("VOICE_SIDECAR_VOICE", "jarvis")
    )
    parser.add_argument("--no-warm-up", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    from voice_sidecar.engine import default_voices_dir

    voices = VoiceStore(
        Path(args.voices_dir) if args.voices_dir else default_voices_dir()
    )
    engine = ChatterboxEngine(args.device, voices, precision=args.precision)
    engine.load()
    if args.voice in voices.names():
        engine.use_voice(args.voice)
        if not args.no_warm_up:
            engine.warm_up(args.voice)
    else:
        logger.warning(
            "voice %r has no reference yet; upload one via POST /voices/%s",
            args.voice,
            args.voice,
        )

    app = create_app(engine, args.voice)
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",
        ws_max_size=32 * 1024 * 1024,
    )
    return 0
