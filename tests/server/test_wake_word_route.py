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
    # at the firing and once per stage, since nothing confirmed it.
    assert app.state.speech_backend.audio[0][:4] == b"RIFF"
    from openjarvis.speech.wake_word_verify import VERIFY_STAGES

    assert len(app.state.speech_backend.audio) == VERIFY_STAGES + 1
    # A rejection waits for the score to dip; a reset would cost the warm-up.
    assert app.state.wake_word_detector.resets == 0


def test_a_phrase_already_whole_at_the_firing_is_confirmed_at_once():
    """A late firing holds the whole phrase: the check at the firing
    confirms it without waiting for more frames."""
    app = _app("hey sage")
    out = _drive(app)
    assert out[-1]["type"] == "detected"
    # The first read confirmed, on the three scored frames alone. (The
    # frames the drive sends after it are scored again and may fire the
    # fake detector a second time; only the first firing is under test.)
    clip = app.state.speech_backend.audio[0]
    assert len(clip) == 44 + 3 * 2560


class _Unfolding(_Backend):
    """Hears "Hey." at the firing, the whole phrase once more has arrived."""

    def transcribe(self, audio, **kwargs):
        super().transcribe(audio, **kwargs)
        self.text = "Hey Sage." if len(self.audio) > 1 else "Hey."

        class R:
            pass

        r = R()
        r.text = self.text
        return r


def test_a_phrase_still_being_said_waits_for_the_staged_check():
    """The frames after the firing are what the first stage hears."""
    from openjarvis.speech.wake_word_verify import VERIFY_STAGE_FRAMES

    app = _app("hey sage")
    app.state.speech_backend = _Unfolding("")
    out = _drive(app)
    assert out[-1]["type"] == "detected"
    first, second = app.state.speech_backend.audio[:2]
    assert len(first) == 44 + 3 * 2560
    assert len(second) == 44 + (3 + VERIFY_STAGE_FRAMES) * 2560


def test_a_firing_with_the_words_is_detected_and_marked_verified():
    out = _drive(_app("Hey Sage, what's up"))
    assert out[-1]["type"] == "detected"
    assert out[-1]["verified"] is True and out[-1]["heard"].startswith("Hey Sage")


class _Doubting(_Backend):
    """Whisper doubting there was speech (no-speech 0.64), as it did for the
    user two inches from the mic on 25 September."""

    def transcribe(self, audio, **kwargs):
        r = super().transcribe(audio, **kwargs)

        class Segment:
            no_speech = 0.64

        r.segments = [Segment()]
        return r


def test_a_muffled_firing_loud_enough_counts_as_verified():
    app = _app(None)
    app.state.speech_backend = _Doubting("Hey Sage.")
    out = _drive(app)
    assert out[-1]["type"] == "detected" and out[-1]["note"] == "muffled"
    assert out[-1]["verified"] is True


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


class _Scripted:
    """Scores from a list, one per frame; fires on any score over 0.5."""

    available = True
    threshold = 0.5

    def __init__(self, scores):
        self.scores = list(scores)
        self.resets = 0

    def clone(self):
        return self

    def score(self, frame):
        return self.scores.pop(0) if self.scores else 0.1

    def is_detection(self, score):
        return score > self.threshold

    def reset(self):
        self.resets += 1


def _run(app, script):
    """Send ("frame" | "pause" | "arm") steps; return the replies, in order."""
    client = TestClient(app)
    replies = []
    with client.websocket_connect("/v1/speech/wake-word?verify=off") as ws:
        for step in script:
            if step == "frame":
                ws.send_bytes(b"\x00" * 2560)
                replies.append(ws.receive_json())
            else:
                ws.send_text('{"type": "%s"}' % step)
    return replies


def test_a_paused_wake_word_hears_but_never_fires():
    app = _app("hey sage", verify="off")
    app.state.wake_word_detector = _Scripted([0.9, 0.9, 0.9])
    replies = _run(app, ["pause", "frame", "frame", "frame"])
    assert [r["type"] for r in replies] == ["score"] * 3
    assert all(r.get("muted") for r in replies)


def test_rearmed_it_fires_only_after_the_score_dips():
    # A "Sage" still in the window when it re-arms (0.9, 0.9) must not fire;
    # after a dip, a real phrase does -- and no reset, so no warm-up.
    app = _app("hey sage", verify="off")
    detector = _Scripted([0.9, 0.9, 0.2, 0.9])
    app.state.wake_word_detector = detector
    # One frame past the detection: the route sends "detected" and only then
    # resets (the reply must not wait), so closing the socket right after
    # "detected" could cancel the reset before it ran -- CI failed on exactly
    # that, twice, and waiting afterwards could not help (29-30 September).
    # Frames are handled in order, so this frame's reply proves the reset ran.
    replies = _run(app, ["pause", "arm", "frame", "frame", "frame", "frame", "frame"])
    assert [r["type"] for r in replies][:4] == ["score", "score", "score", "detected"]
    assert len(replies) == 5
    # Re-arming never reset the detector (the reset is what cost the
    # warm-up); the only reset is the one after the detection itself.
    assert detector.resets == 1


def test_after_a_rejection_the_repeat_fires_once_the_score_dips():
    # 6 October: a reset after each rejection held the detector in its 2 s
    # warm-up, and the user's repeat (scoring 0.97) went unheard.
    from openjarvis.speech.wake_word_verify import VERIFY_STAGE_FRAMES, VERIFY_STAGES

    staged = VERIFY_STAGE_FRAMES * VERIFY_STAGES
    app = _app("the stage")
    # The frames gathered for a check are not scored.
    detector = _Scripted([0.1, 0.9, 0.9, 0.9, 0.2, 0.9])
    app.state.wake_word_detector = detector
    frame = b"\x00" * 2560
    with TestClient(app).websocket_connect("/v1/speech/wake-word") as ws:
        for _ in range(2 + staged):
            ws.send_bytes(frame)
        assert [ws.receive_json()["type"] for _ in range(2)] == ["score", "rejected"]
        assert detector.resets == 0
        app.state.speech_backend.text = "Hey Sage."
        # Still over the threshold from the rejected sound: no firing until
        # the score dips; then the repeat fires.
        for _ in range(4):
            ws.send_bytes(frame)
        assert [ws.receive_json()["type"] for _ in range(3)] == ["score"] * 3
        for _ in range(staged):
            ws.send_bytes(frame)
        reply = ws.receive_json()
        assert reply["type"] == "detected" and reply["verified"] is True


def _near_misses(caplog):
    return [r.getMessage() for r in caplog.records if "near miss" in r.getMessage()]


def test_a_rise_that_never_fires_is_logged_as_a_near_miss(caplog):
    caplog.set_level("INFO", logger="openjarvis.wake")
    app = _app("hey sage", verify="off")
    app.state.wake_word_detector = _Scripted([0.35, 0.45, 0.1])
    replies = _run(app, ["frame", "frame", "frame"])
    assert [r["type"] for r in replies] == ["score"] * 3
    assert _near_misses(caplog) == [
        "Wake word near miss: peak=0.45 frames=2 over=0 warming_up=False"
    ]


def test_paused_or_fired_is_not_a_near_miss(caplog):
    caplog.set_level("INFO", logger="openjarvis.wake")
    app = _app("hey sage", verify="off")
    app.state.wake_word_detector = _Scripted([0.45, 0.1])
    _run(app, ["pause", "frame", "frame"])
    app.state.wake_word_detector = _Scripted([0.4, 0.9, 0.1])
    _run(app, ["frame", "frame", "frame"])
    assert _near_misses(caplog) == []


class _Room(_Backend):
    """Text and Whisper's no-speech doubt set per firing."""

    def __init__(self):
        super().__init__("")
        self.no_speech = 0.64

    def transcribe(self, audio, **kwargs):
        r = super().transcribe(audio, **kwargs)
        doubt = self.no_speech

        class Segment:
            no_speech = doubt

        r.segments = [Segment()]
        return r


def _fire(ws):
    """One firing of the fake detector (third frame) with its staged frames;
    replies up to and including the verdict."""
    from openjarvis.speech.wake_word_verify import VERIFY_STAGE_FRAMES, VERIFY_STAGES

    for i in range(3):
        ws.send_bytes(bytes([i]) * 2560)
    for j in range(VERIFY_STAGE_FRAMES * VERIFY_STAGES):
        ws.send_bytes(bytes([100 + j]) * 2560)
    while True:
        reply = ws.receive_json()
        if reply["type"] != "score":
            return reply


def _after_two_rejections(heard, no_speech):
    app = _app(None)
    room = app.state.speech_backend = _Room()
    with TestClient(app).websocket_connect("/v1/speech/wake-word") as ws:
        for _ in range(2):
            assert _fire(ws)["type"] == "rejected"
        room.text, room.no_speech = heard, no_speech
        return _fire(ws)


def test_a_muffled_hey_sage_in_a_noisy_room_is_rejected():
    # 29 September: a piano fired the detector every few seconds and tiny.en,
    # prompted with the phrase, wrote "Hey Sage." for some of it (muffled).
    assert _after_two_rejections("Hey Sage.", 0.64)["type"] == "rejected"


def test_a_clear_hey_sage_in_a_noisy_room_still_wakes():
    reply = _after_two_rejections("Hey Sage.", 0.01)
    assert reply["type"] == "detected" and reply["verified"] is True


def _fire_then_silence(app):
    """Three frames (the third fires), then nothing: a quiet room sends no
    frames after the phrase."""
    client = TestClient(app)
    with client.websocket_connect("/v1/speech/wake-word") as ws:
        out = []
        for i in range(3):
            ws.send_bytes(bytes([i]) * 2560)
            out.append(ws.receive_json())
    return out


def test_a_staged_check_does_not_wait_for_the_next_sound():
    """6 October: only 1-3 frames followed a firing in a quiet room, and the
    staged check waited for the user's next sound. Now the missing frames
    are silence once the stage is due."""
    from openjarvis.speech.wake_word_verify import VERIFY_STAGE_FRAMES

    app = _app("hey sage")
    app.state.speech_backend = _Unfolding("")
    out = _fire_then_silence(app)

    assert out[-1]["type"] == "detected"
    first, second = app.state.speech_backend.audio[:2]
    assert len(first) == 44 + 3 * 2560
    # The phrase, then silence where no frame came.
    assert len(second) == 44 + (3 + VERIFY_STAGE_FRAMES) * 2560
    assert second[-VERIFY_STAGE_FRAMES * 2560 :] == bytes(VERIFY_STAGE_FRAMES * 2560)


def test_a_rejection_in_a_quiet_room_still_answers():
    from openjarvis.speech.wake_word_verify import VERIFY_STAGES

    app = _app("the stage")
    out = _fire_then_silence(app)

    assert out[-1]["type"] == "rejected"
    assert len(app.state.speech_backend.audio) == VERIFY_STAGES + 1
