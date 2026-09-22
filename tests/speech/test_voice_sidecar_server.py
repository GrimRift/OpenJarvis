"""The sidecar's stream: pieces go out as made when the queue is dry, a
whole sentence at once when the audio already sent will cover its
generation, and every segment is acknowledged."""

from __future__ import annotations

import json
import time

import numpy as np
import pytest

server = pytest.importorskip("voice_sidecar.server")
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

SR = server.SAMPLE_RATE


class FakeEngine:
    """Silence at the measured 75 ms per character, instantly."""

    def __init__(self):
        self.pieces = []
        self.last_generation_seconds = 0.0
        self.device = "cpu"
        self.precision = "fp32"
        self.requested_device = "cpu"
        self.model = object()
        self.current_voice = "v"
        self.load_seconds = 0.0
        self.generations = 0

    @property
    def loaded(self):
        return True

    def use_voice(self, name):
        self.current_voice = name

    def generate(self, text, voice):
        self.pieces.append(text)
        self.generations += 1
        return np.zeros(int(SR * 0.075 * len(text)), dtype=np.float32)


class FakeStore:
    def names(self):
        return ["v"]

    def info(self, name):
        raise KeyError(name)


def _drive(engine, segments):
    engine.voices = FakeStore()
    app = server.create_app(engine, "v")
    events = []
    with TestClient(app).websocket_connect("/stream") as ws:
        ws.send_text(json.dumps({"type": "begin", "voice": "v"}))
        for segment in segments:
            ws.send_text(json.dumps({"type": "text", "text": segment}))
            time.sleep(0.25)  # past the short-segment merge window
        ws.send_text(json.dumps({"type": "finish"}))
        while True:
            message = ws.receive()
            if "bytes" in message and message["bytes"] is not None:
                events.append(("audio", len(message["bytes"])))
                continue
            payload = json.loads(message["text"])
            events.append((payload["type"], payload))
            if payload["type"] == "done":
                break
    return events


LONG = (
    "Lord of the Mysteries is a dark fantasy mystery novel by Cuttlefish That "
    "Loves Diving, set in a world that resembles a strange, supernatural "
    "version of Victorian-era Europe, and it rewards patience."
)


def test_every_segment_is_acknowledged_and_audio_arrives():
    engine = FakeEngine()
    events = _drive(engine, ["Certainly, Sir.", LONG])
    dones = [p for kind, p in events if kind == "segment_done"]
    assert sum(p.get("segments", 1) for p in dones) == 2
    assert any(kind == "audio" for kind, _ in events)
    # The long sentence was generated in clause-sized pieces.
    assert len(engine.pieces) > 2


def test_a_long_sentence_is_held_whole_when_the_queue_covers_it(caplog):
    # Two long sentences: the first streams piece by piece (nothing is
    # buffered yet); by the second, seconds of audio are in hand, so it is
    # generated whole and its audio arrives in one run after generation.
    engine = FakeEngine()
    caplog.set_level("INFO", logger="voice_sidecar")
    events = _drive(engine, [LONG, LONG])
    held = [r for r in caplog.records if "(held)" in r.getMessage()]
    streamed = [
        r
        for r in caplog.records
        if r.getMessage().startswith("piece") and "(held)" not in r.getMessage()
    ]
    assert streamed and held
    # The first sentence's pieces were streamed, the second's held.
    assert caplog.records.index(streamed[0]) < caplog.records.index(held[0])
    # Both acknowledged, in order, with audio before each acknowledgement.
    kinds = [kind for kind, _ in events]
    assert kinds.count("segment_done") == 2
    assert kinds.index("audio") < kinds.index("segment_done")
