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


@pytest.fixture(autouse=True)
def _no_small_model(monkeypatch):
    """The dedicated verifier model is never loaded in tests; "local" then
    falls back to whatever speech backend the app has, which is the fake."""

    def _unavailable(config):
        raise RuntimeError("no model in tests")

    monkeypatch.setattr(
        "openjarvis.speech.wake_word_verify.local_verifier_backend", _unavailable
    )
    # The route asks the machine whether media is playing and judges more
    # strictly if so: left to the real machine, these tests passed or failed
    # with whatever the user had playing (24 September, 2 runs in 5).
    monkeypatch.setattr(
        "openjarvis.speech.wake_word_verify.media_is_playing", lambda: False
    )


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


def _drive(app, frames=3, path="/v1/speech/wake-word"):
    """Send `frames` frames and read a reply for each. The third frame
    fires the fake detector; a verifying server then gathers more frames
    before it answers (the phrase is still being said when the detector
    fires), so those are sent too and, when the server did not need them,
    read back as ordinary scores."""
    from openjarvis.speech.wake_word_verify import VERIFY_STAGE_FRAMES, VERIFY_STAGES

    extra = VERIFY_STAGE_FRAMES * VERIFY_STAGES
    client = TestClient(app)
    with client.websocket_connect(path) as ws:
        out = []
        for i in range(frames):
            ws.send_bytes(bytes([i]) * 2560)
            if i == frames - 1:
                for j in range(extra):
                    ws.send_bytes(bytes([100 + j]) * 2560)
            out.append(ws.receive_json())
    return out


def test_a_firing_without_the_words_is_rejected_with_what_was_heard():
    app = _app("the stage")
    out = _drive(app)
    assert [m["type"] for m in out] == ["score", "score", "rejected"]
    assert out[-1]["heard"] == "the stage"
    # The clip handed to the verifier is the ring, as WAV -- and it was read
    # once per stage, since nothing confirmed it.
    assert app.state.speech_backend.audio[0][:4] == b"RIFF"
    from openjarvis.speech.wake_word_verify import VERIFY_STAGES

    assert len(app.state.speech_backend.audio) == VERIFY_STAGES
    assert app.state.wake_word_detector.resets == 1


def test_the_ring_grows_between_stages_and_a_confirmation_stops_them():
    """The frames after the firing are what the second stage hears."""
    app = _app("hey sage")
    out = _drive(app)
    assert out[-1]["type"] == "detected"
    # Confirmed at the first stage: one read, of a ring holding the three
    # scored frames plus the first stage's extra ones.
    from openjarvis.speech.wake_word_verify import VERIFY_STAGE_FRAMES

    [clip] = app.state.speech_backend.audio
    assert len(clip) == 44 + (3 + VERIFY_STAGE_FRAMES) * 2560


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
    out = _drive(app, path="/v1/speech/wake-word?verify=off")
    assert out[-1]["type"] == "detected"
    assert app.state.speech_backend.audio == []
