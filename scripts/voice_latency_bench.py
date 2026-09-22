"""Latency of each speech provider pair, through Sage's real sockets.

STT: a recording is streamed as 50 ms frames in real time to
``/v1/speech/flux`` (the socket both providers share) and the time from
the last audio frame to ``EndOfTurn`` is reported, with the transcript.
TTS: a fixed three-sentence reply is pushed to ``/v1/speech/tts-stream`` as
deltas at a realistic rate and the time from ``begin`` to the first audio
byte, and to ``done``, is reported.

    .venv\\Scripts\\python.exe scripts\\voice_latency_bench.py
    .venv\\Scripts\\python.exe scripts\\voice_latency_bench.py ^
        --stt-wav path\\to\\question.wav --rounds 3

Needs a running server (the Start Menu "Sage" shortcut) and, for the
local pair, the Parakeet weights and the Chatterbox sidecar environment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

REPLY = [
    "Good evening, Sir. ",
    "All YouTube tabs are closed, and tomorrow's schedule shows one class at nine forty. ",  # noqa: E501
    "Is there anything else you need?",
]
DEFAULT_WAV = r"C:\AI\OpenJarvis-Data\wake_word_samples\live_debug\positive\live_1787499098274.wav"  # noqa: E501


def _ws_url(base: str, path: str) -> str:
    return base.replace("http://", "ws://").replace("https://", "wss://") + path


def _protocols() -> list[str]:
    """The browser's WebSocket handshake: a marker plus the key, b64url."""
    key = os.environ.get("OPENJARVIS_API_KEY", "")
    if not key:
        return []
    import base64

    encoded = base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii").rstrip("=")
    return ["openjarvis.auth.v1", f"openjarvis.key.b64url.{encoded}"]


async def bench_stt(base: str, provider: str, pcm: bytes, key_hdr: dict) -> dict:
    import websockets

    url = _ws_url(base, f"/v1/speech/flux?provider={provider}")
    async with websockets.connect(
        url, subprotocols=_protocols() or None, additional_headers=key_hdr
    ) as ws:
        ready = json.loads(await ws.recv())
        if ready.get("type") != "FluxReady":
            return {"error": ready.get("reason") or ready}
        frame = 1600  # 50 ms of int16 at 16 kHz
        finals: list[tuple[float, str]] = []
        last_audio_at = None

        async def reader():
            while True:
                raw = await ws.recv()
                msg = json.loads(raw)
                if msg.get("type") == "TurnInfo" and msg.get("event") == "EndOfTurn":
                    finals.append((time.perf_counter(), msg.get("transcript", "")))
                elif msg.get("type") in ("FluxError", "FluxUnavailable"):
                    finals.append((time.perf_counter(), f"<{msg.get('reason')}>"))
                    return

        task = asyncio.create_task(reader())
        started = time.perf_counter()
        for i in range(0, len(pcm), frame):
            await ws.send(pcm[i : i + frame])
            last_audio_at = time.perf_counter()
            # Real time: 50 ms per frame, minus what sending took.
            await asyncio.sleep(
                max(0.0, started + (i // frame + 1) * 0.05 - time.perf_counter())
            )
        # Silence so the last turn can end.
        for _ in range(60):
            await ws.send(bytes(frame * 2))
            await asyncio.sleep(0.05)
        await asyncio.sleep(1.0)
        task.cancel()
        await ws.send("stop")
    if not finals:
        return {"turns": 0, "last_turn_end_ms": None, "transcripts": []}
    return {
        "turns": len(finals),
        "last_turn_end_ms": round((finals[-1][0] - last_audio_at) * 1000)
        if last_audio_at
        else None,
        "transcripts": [t for _, t in finals],
    }


async def bench_tts(base: str, provider: str, voice_id: str, key_hdr: dict) -> dict:
    import websockets

    url = _ws_url(base, "/v1/speech/tts-stream")
    async with websockets.connect(
        url,
        subprotocols=_protocols() or None,
        additional_headers=key_hdr,
        max_size=32 * 1024 * 1024,
    ) as ws:
        t0 = time.perf_counter()
        await ws.send(
            json.dumps(
                {
                    "type": "begin",
                    "provider": provider,
                    "voice_id": voice_id,
                    "speed": 1.0,
                    "volume": 1.9,
                }
            )
        )
        ready = json.loads(await ws.recv())
        if ready.get("type") != "ready":
            return {"error": ready.get("reason") or ready}
        first = None
        samples = 0

        async def feed():
            for chunk in REPLY:
                for piece in chunk.split(" "):
                    await ws.send(json.dumps({"type": "text", "delta": piece + " "}))
                    await asyncio.sleep(0.04)  # ~25 tokens/s
            await ws.send(json.dumps({"type": "finish"}))

        feeder = asyncio.create_task(feed())
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
            if isinstance(raw, (bytes, bytearray)):
                if first is None:
                    first = time.perf_counter() - t0
                samples += len(raw) // 4
                continue
            msg = json.loads(raw)
            if msg.get("type") in ("done", "error"):
                break
        await feeder
        return {
            "first_audio_ms": round(first * 1000) if first is not None else None,
            "done_ms": round((time.perf_counter() - t0) * 1000),
            "audio_seconds": round(samples / 24000, 1),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base", default=os.environ.get("OPENJARVIS_BASE", "http://127.0.0.1:8000")
    )
    parser.add_argument("--stt-wav", default=DEFAULT_WAV)
    parser.add_argument(
        "--stt-seconds", type=float, default=6.0, help="how much of the wav to stream"
    )
    parser.add_argument(
        "--stt-offset", type=float, default=38.0, help="where in the wav to start"
    )
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--api-key", default=os.environ.get("OPENJARVIS_API_KEY", ""))
    args = parser.parse_args()
    key_hdr = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}

    with wave.open(args.stt_wav) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        w.setpos(int(args.stt_offset * 16000))
        pcm = w.readframes(int(args.stt_seconds * 16000))

    print(
        f"server {args.base}; STT clip {args.stt_seconds:.0f}s from {Path(args.stt_wav).name}\n"  # noqa: E501
    )
    print(
        f"{'STT provider':14s} {'turns':>5s} {'end→final ms':>13s}  transcript (last)"
    )
    for provider in ("flux", "parakeet"):
        ends = []
        last = {}
        for _ in range(args.rounds):
            last = asyncio.run(bench_stt(args.base, provider, pcm, key_hdr))
            if last.get("last_turn_end_ms") is not None:
                ends.append(last["last_turn_end_ms"])
        if "error" in last:
            print(f"{provider:14s} unavailable: {last['error']}")
            continue
        end = f"{statistics.median(ends):.0f}" if ends else "-"
        transcript = (last.get("transcripts") or ["-"])[-1]
        print(
            f"{provider:14s} {last.get('turns', 0):5d} {end:>13s}  {transcript[:70]!r}"
        )

    print(
        f"\n{'TTS provider':14s} {'first audio ms':>14s} {'done ms':>8s} {'audio s':>8s}"  # noqa: E501
    )
    for provider, voice in (
        ("cartesia", "78a05d7d-268b-4a18-aad7-7a96902a95ee"),
        ("chatterbox", "chatterbox:jarvis"),
    ):
        firsts, dones, secs = [], [], []
        last = {}
        for _ in range(args.rounds):
            last = asyncio.run(bench_tts(args.base, provider, voice, key_hdr))
            if last.get("first_audio_ms") is not None:
                firsts.append(last["first_audio_ms"])
                dones.append(last["done_ms"])
                secs.append(last["audio_seconds"])
        if "error" in last or not firsts:
            print(f"{provider:14s} unavailable: {last.get('error', 'no audio')}")
            continue
        print(
            f"{provider:14s} {statistics.median(firsts):14.0f} {statistics.median(dones):8.0f} {statistics.median(secs):8.1f}"  # noqa: E501
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
