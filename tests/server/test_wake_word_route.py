"""The wake-word socket asks the transcript before announcing a detection."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="openjarvis[server] not installed")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.core.config import JarvisConfig
from openjarvis.server.api_routes import websocket_router


class _Detector:
    """Fires on every third frame; nothing acoustic about it."""

    available = True

    def __init__(self):
        self.frames = 0
        self.resets = 0

    def clone(self):
        return self

    def score(self, frame):
        self.frames += 1
        return 0.9 if self.frames % 3 == 0 else 0.1

    def is_detection(self, score):
        return score > 0.5

    def reset(self):
        self.resets += 1


class _Backend:
    def __init__(self, text):
        self.text = text
        self.audio = []

    def transcribe(self, audio, **kwargs):
        self.audio.append(audio)

        class R:
            pass

        r = R()
        r.text = self.text
        return r


def _app(heard, verify="local"):
    app = FastAPI()
    app.include_router(websocket_router)
    cfg = JarvisConfig()
    cfg.speech.wake_word_verify = verify
    app.state.config = cfg
    app.state.api_key = ""
    app.state.wake_word_detector = _Detector()
    app.state.speech_backend = _Backend(heard) if heard is not None else None
    return app


def _drive(app, frames=3):
    client = TestClient(app)
    with client.websocket_connect("/v1/speech/wake-word") as ws:
        out = []
        for i in range(frames):
            ws.send_bytes(bytes([i]) * 2560)
            out.append(ws.receive_json())
    return out


def test_a_firing_without_the_words_is_rejected_with_what_was_heard():
    app = _app("the stage")
    out = _drive(app)
    assert [m["type"] for m in out] == ["score", "score", "rejected"]
    assert out[-1]["heard"] == "the stage"
    # The clip handed to the verifier is the ring: every frame so far, as WAV.
    assert app.state.speech_backend.audio[0][:4] == b"RIFF"
    assert app.state.wake_word_detector.resets == 1


def test_a_firing_with_the_words_is_detected_and_marked_verified():
    out = _drive(_app("Hey Sage, what's up"))
    assert out[-1]["type"] == "detected"
    assert out[-1]["verified"] is True and out[-1]["heard"].startswith("Hey Sage")


def test_no_backend_confirms_unverified_rather_than_going_deaf():
    out = _drive(_app(None))
    assert out[-1]["type"] == "detected"
    assert out[-1]["verified"] is False and "no speech backend" in out[-1]["note"]


def test_off_skips_verification_entirely():
    app = _app("the stage", verify="off")
    out = _drive(app)
    assert out[-1]["type"] == "detected" and out[-1]["verified"] is False
    assert app.state.speech_backend.audio == []


def test_the_browser_picks_the_verifier_per_socket():
    """`?verify=off` on the socket beats the server default."""
    app = _app("the stage", verify="local")
    client = TestClient(app)
    with client.websocket_connect("/v1/speech/wake-word?verify=off") as ws:
        out = [ws.send_bytes(bytes([i]) * 2560) or ws.receive_json() for i in range(3)]
    assert out[-1]["type"] == "detected"
    assert app.state.speech_backend.audio == []


def test_deepgram_without_a_key_falls_back_to_the_local_backend(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.setattr("openjarvis.speech.wake_word_verify._deepgram_key", lambda: "")
    app = _app("hazage", verify="deepgram")
    out = _drive(app)
    assert out[-1]["type"] == "detected" and out[-1]["verified"] is True
    assert len(app.state.speech_backend.audio) == 1
