"""The voice fingerprint: who spoke a turn, the user or Sage's own voice."""

from __future__ import annotations

import asyncio
import json
import types

import numpy as np

from openjarvis.speech import speaker_id
from openjarvis.speech.speaker_id import (
    MIN_SECONDS,
    MIN_USER_CLIPS,
    AudioTimeline,
    SpeakerId,
    verdict,
)

USER = np.eye(4)[0]
SAGE = np.eye(4)[1]


def _pcm(seconds: float, level: int = 2000, tag: int = 0) -> bytes:
    """A quarter of room hum, then speech; the first spoken sample carries
    `tag`, so clips of equal length are distinguishable."""
    n = int(seconds * 16000)
    quiet = n // 4 // 320 * 320
    samples = np.full(n, level, dtype="<i2")
    samples[:quiet] = level // 20
    if quiet < n:
        samples[quiet] = tag
    return samples.tobytes()


def _embedder(table):
    """Fingerprints looked up by the first sample: tag -> vector."""

    def embed(pcm: bytes):
        tag = int(np.frombuffer(pcm[:2], dtype="<i2")[0])
        return list(table.get(tag, USER))

    return embed


def _trained(tmp_path, table, sage=SAGE):
    sid = SpeakerId(
        tmp_path, _embedder(table), lambda: list(sage) if sage is not None else None
    )
    for i in range(MIN_USER_CLIPS):
        assert sid.learn_user(_pcm(2.0), f"clip{i}")
    return sid


def test_verdict_needs_a_clear_margin_and_enough_speech():
    assert verdict(0.85, 0.52, 2.0) == "user"
    assert verdict(0.52, 0.88, 2.0) == "sage"
    # Mixed audio sits between the two: neither is believed.
    assert verdict(0.64, 0.60, 2.0) == "unsure"
    # A lone "stop" is too short to fingerprint, however it scores.
    assert verdict(0.95, 0.10, MIN_SECONDS - 0.1) == "unsure"
    # With no fingerprint of Sage, only a clear match with the user counts.
    assert verdict(0.80, None, 2.0) == "user"
    assert verdict(0.40, None, 2.0) == "unsure"


def test_a_turn_in_sages_voice_is_called_sage(tmp_path):
    sid = _trained(tmp_path, {7: SAGE})
    assert sid.score(_pcm(2.0, tag=7))["verdict"] == "sage"
    assert sid.score(_pcm(2.0, tag=0))["verdict"] == "user"


def test_no_verdict_without_a_profile_or_on_silence_or_short_audio(tmp_path):
    fresh = SpeakerId(tmp_path, _embedder({}), lambda: list(SAGE))
    assert fresh.score(_pcm(2.0)) is None
    sid = _trained(tmp_path, {})
    assert sid.score(_pcm(2.0, level=0)) is None
    assert sid.score(_pcm(0.5)) is None


def test_the_profile_survives_a_restart_and_keeps_only_the_newest(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(speaker_id, "MAX_USER_CLIPS", 6)
    sid = _trained(tmp_path, {})
    for i in range(10):
        sid.learn_user(_pcm(2.0), f"late{i}")
    again = SpeakerId(tmp_path, _embedder({}), lambda: None)
    assert again.user_clips == 6
    # The same clip twice is learnt once.
    assert not again.learn_user(_pcm(2.0), "late9")


def test_a_sidecar_that_cannot_embed_learns_nothing(tmp_path):
    sid = SpeakerId(tmp_path, lambda pcm: None, lambda: None)
    assert not sid.learn_user(_pcm(2.0), "a")
    assert sid.user_clips == 0


def test_seeding_reads_only_confirmed_wake_clips(tmp_path):
    from openjarvis.speech.wake_word_verify import pcm_to_wav

    clips = tmp_path / "clips"
    clips.mkdir()
    for i in range(3):
        (clips / f"20260924_1200{i:02d}_ok_hey_sage_.wav").write_bytes(
            pcm_to_wav(_pcm(2.0))
        )
    (clips / "20260924_120100_rejected_the_stage_.wav").write_bytes(
        pcm_to_wav(_pcm(2.0))
    )
    sid = SpeakerId(tmp_path, _embedder({}), lambda: None)
    assert sid.seed_from_clips(clips) == 3


def test_the_timeline_follows_the_session_clock_after_trimming():
    timeline = AudioTimeline(keep_seconds=2.0)
    for second in range(5):
        timeline.append(_pcm(1.0, tag=second + 1))
    # Seconds 3-4 are still held and start where the session clock says.
    held = timeline.slice(3.0, 4.0)
    assert len(held) == 32000
    assert np.frombuffer(held, dtype="<i2")[16000 // 4 // 320 * 320] == 4
    # Trimmed audio is gone, not misplaced.
    assert timeline.slice(0.0, 1.0) == b""
    assert timeline.slice(2.0, 2.0) == b""


def test_the_relay_attaches_a_verdict_and_fails_open():
    from openjarvis.server.flux_routes import _who_spoke

    timeline = AudioTimeline()
    timeline.append(_pcm(3.0))
    event = types.SimpleNamespace(audio_window_start=0.5, audio_window_end=2.5)

    class Speakers:
        def score(self, pcm):
            return {"verdict": "user", "seconds": len(pcm) / 32000}

    class Broken:
        def score(self, pcm):
            raise RuntimeError("sidecar down")

    got = asyncio.run(_who_spoke(Speakers(), timeline, event))
    assert got == {"verdict": "user", "seconds": 2.0}
    assert asyncio.run(_who_spoke(Broken(), timeline, event)) is None
    assert asyncio.run(_who_spoke(None, timeline, event)) is None


def test_seeding_is_tried_again_when_the_sidecar_was_not_up(tmp_path, monkeypatch):
    from openjarvis.speech.wake_word_verify import pcm_to_wav

    clips = tmp_path / "clips"
    clips.mkdir()
    for i in range(MIN_USER_CLIPS):
        (clips / f"20260924_1200{i:02d}_ok_hey_sage_.wav").write_bytes(
            pcm_to_wav(_pcm(2.0))
        )
    monkeypatch.setenv("OPENJARVIS_WAKE_WORD_KEEP_CLIPS", str(clips))
    monkeypatch.setattr(speaker_id, "_last_seed", 0.0)
    up = {"now": False}
    sid = SpeakerId(
        tmp_path, lambda pcm: list(USER) if up["now"] else None, lambda: None
    )

    started = []
    monkeypatch.setattr(
        speaker_id.threading,
        "Thread",
        lambda target, **kw: types.SimpleNamespace(
            start=lambda: started.append(target())
        ),
    )
    clock = {"t": 1000.0}
    monkeypatch.setattr(speaker_id.time, "monotonic", lambda: clock["t"])
    speaker_id._maybe_seed(sid)
    assert sid.user_clips == 0
    # Too soon: not tried again.
    up["now"] = True
    speaker_id._maybe_seed(sid)
    assert len(started) == 1
    clock["t"] += speaker_id.SEED_RETRY + 1
    speaker_id._maybe_seed(sid)
    assert sid.user_clips == MIN_USER_CLIPS


def test_only_the_speech_in_a_long_quiet_turn_is_fingerprinted():
    from openjarvis.speech.speaker_id import KEEP_SECONDS, voiced_with_speech

    rng = np.random.default_rng(0)
    hum = rng.normal(0, 1500, 16000 * 10).astype("<i2")
    word = (8000 * np.sin(np.arange(8000) / 5)).astype("<i2")
    kept, speech = voiced_with_speech(np.concatenate([hum, word]).tobytes())
    # Half a second of speech is counted, not the ten of hum; the encoder
    # still gets the loudest 1.5 s.
    assert 0.4 < speech < 0.7
    assert len(kept) == int(KEEP_SECONDS * 16000 / 320) * 320 * 2
    assert voiced_with_speech(b"") == (b"", 0.0)


def test_sages_fingerprint_follows_the_selected_voice(monkeypatch):
    from openjarvis.speech import voice_choice

    selected = {"id": "chatterbox:jarvis"}
    monkeypatch.setattr(voice_choice, "chosen_voice_id", lambda cfg: selected["id"])
    asked = []

    class Reply:
        def __init__(self, name):
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"embedding": [len(self.name)]}).encode()

    def urlopen(url, timeout):
        asked.append(url)
        return Reply(url.rsplit("/", 1)[1])

    monkeypatch.setattr(speaker_id.urllib.request, "urlopen", urlopen)
    fetch = speaker_id._sidecar_voice("http://sidecar", None)
    assert fetch() == [6]
    assert fetch() == [6] and len(asked) == 1  # cached per voice
    selected["id"] = "chatterbox:jarvis-mix-a"
    assert fetch() == [12]
    selected["id"] = "cartesia:some-voice"
    assert fetch() is None
