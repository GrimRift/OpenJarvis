"""The wake word must be heard as words, not just as a shape (wake-word-verify)."""

from __future__ import annotations

import array
import asyncio
import io
import time
import wave

import pytest

from openjarvis.speech.wake_word_verify import (
    RING_FRAMES,
    AudioRing,
    WakeWordVerifier,
    heard_wake_phrase,
    make_verifier,
    pcm_to_wav,
)


class TestPhrase:
    @pytest.mark.parametrize(
        "text",
        [
            "Hey Sage.",
            "hey sage",
            "Hey, Sage, what time is it?",
            "Hi Sage",
            "hey stage",
            "Hey Sayge",
            "he says",
            "Okay Sage",
            "hey uh sage",
            "Hey Sage's here",
        ],
    )
    def test_the_usual_mishearings_count(self, text):
        assert heard_wake_phrase(text)

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "sage",
            "Sage, are you there?",
            "the stage is set",
            "hey there",
            "hey what a mess",
            "Thank you.",
        ],
    )
    def test_the_name_alone_or_no_name_does_not(self, text):
        assert not heard_wake_phrase(text)


class TestRing:
    def test_keeps_two_seconds_and_clears(self):
        ring = AudioRing()
        for i in range(RING_FRAMES + 10):
            ring.push(bytes([i % 256]) * 4)
        pcm = ring.pcm()
        assert len(pcm) == RING_FRAMES * 4
        assert pcm[0] == 10  # the oldest ten frames fell off
        ring.clear()
        assert ring.pcm() == b""

    def test_wav_is_16k_mono_int16(self):
        wav = pcm_to_wav(b"\x00\x01" * 1600)
        with wave.open(io.BytesIO(wav)) as f:
            shape = (f.getnchannels(), f.getsampwidth(), f.getframerate())
            assert shape == (1, 2, 16000)
            assert f.getnframes() == 1600


class _Backend:
    def __init__(self, text="", delay=0.0, error=None):
        self.text, self.delay, self.error = text, delay, error
        self.calls = []

    def transcribe(self, audio, *, format="wav", language=None, initial_prompt=None):
        self.calls.append((format, language, initial_prompt))
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error

        class R:
            pass

        r = R()
        r.text = self.text
        return r


class TestVerifier:
    def test_confirms_the_phrase_and_reports_what_it_heard(self):
        backend = _Backend("Hey Sage.")
        verdict = asyncio.run(WakeWordVerifier(backend).verify(b"\x00" * 3200))
        assert (verdict.confirmed, verdict.heard) == (True, "Hey Sage.")
        assert verdict.note == ""
        assert backend.calls == [("wav", "en", "Hey Sage.")]

    def test_rejects_a_transcript_without_the_phrase(self):
        pcm = b"\x00" * 3200
        verdict = asyncio.run(WakeWordVerifier(_Backend("the stage")).verify(pcm))
        assert (verdict.confirmed, verdict.heard) == (False, "the stage")
        silent = asyncio.run(WakeWordVerifier(_Backend("")).verify(pcm))
        assert (silent.confirmed, silent.heard) == (False, "")

    def test_fails_open_on_every_kind_of_failure_but_a_timeout(self):
        """Verification may only remove firings, never make the wake word deaf
        -- except that a timeout rejects (see below)."""
        no_backend = asyncio.run(WakeWordVerifier(None).verify(b"\x00" * 3200))
        assert no_backend.confirmed and no_backend.note

        failing = WakeWordVerifier(_Backend(error=RuntimeError("boom")))
        broken = asyncio.run(failing.verify(b"\x00" * 3200))
        assert broken.confirmed and "boom" in broken.note

        slow = asyncio.run(
            WakeWordVerifier(_Backend("hey sage", delay=0.3), timeout=0.05).verify(
                b"\x00" * 3200
            )
        )
        # A timeout is the one failure that rejects: a check that cannot
        # finish is usually the GPU busy while Sage's own voice tripped the
        # detector (a class reminder woke Sage through the old fail-open).
        assert not slow.confirmed and "slower" in slow.note

        empty = asyncio.run(WakeWordVerifier(_Backend("x")).verify(b""))
        assert empty.confirmed and "no audio" in empty.note


class TestConfig:
    def test_off_disables_and_local_is_the_default(self, monkeypatch):
        def _unavailable(config):
            raise RuntimeError("no model in tests")

        monkeypatch.setattr(
            "openjarvis.speech.wake_word_verify.local_verifier_backend", _unavailable
        )

        class Speech:
            initial_prompt = "Hey Sage."

        class Cfg:
            speech = Speech()

        assert make_verifier(Cfg(), _Backend()) is not None
        Speech.wake_word_verify = "off"
        assert make_verifier(Cfg(), _Backend()) is None
        Speech.wake_word_verify = "local"
        assert make_verifier(Cfg(), None) is not None  # fails open at verify time
        assert make_verifier(None, None) is not None


class TestPhonetic:
    """How Whisper actually wrote "hey sage" in the 16 September recordings."""

    @pytest.mark.parametrize(
        "text",
        [
            "A sage.",
            "He's in.",
            "Hey, see you.",
            "He said,",
            "A-Seage.",
            "Haysage",
            "Be sage.",
            "Easy",
            "He sees",
            "A seed.",
            "He sings.",
            "he's age",
            "Haseage",
            "Hey, save",
            "best song. Hey Sage.",
            # Deepgram, live clips: the s voiced or a soft c.
            "hazage",
            "hazy",
            "acid",
            "Peace",
            # One short word ahead is still the phrase.
            "Oh, he's in.",
        ],
    )
    def test_the_recorded_shapes_pass(self, text):
        assert heard_wake_phrase(text)

    @pytest.mark.parametrize(
        "text",
        [
            "is it",
            "is blue",
            "change",
            "ac",
            "he's",
            "He changed.",
            "I see you soon.",
            "Sage",
            # 23 September, a conversation downstairs: the shape at the end
            # of a sentence, not the start.
            "Look, he's in.",
            "I think he said",
        ],
    )
    def test_other_words_do_not(self, text):
        assert not heard_wake_phrase(text)


def test_quiet_audio_is_brought_up_and_loud_audio_left_alone():
    from openjarvis.speech.wake_word_verify import normalise_level

    quiet = array.array("h", [0, 100, -200, 50]).tobytes()
    out = array.array("h")
    out.frombytes(normalise_level(quiet))
    assert max(abs(v) for v in out) == 20000
    loud = array.array("h", [0, 30000, -30000]).tobytes()
    assert normalise_level(loud) == loud
    assert normalise_level(b"") == b""


class TestTheShapeNeedsAHeyLead:
    """First day live: "Class starting soon. See you", "You see?" and "Can
    you see?" confirmed on the shape alone. The lead before the s must be
    hey-shaped."""

    @pytest.mark.parametrize(
        "text",
        ["see you", "You see?", "Can you see?", "I see", "I see you soon.", "soon see"],
    )
    def test_see_you_and_friends_do_not(self, text):
        assert not heard_wake_phrase(text)

    @pytest.mark.parametrize(
        "text",
        [
            "hazage",
            "hazy",
            "Easy",
            "Peace Sage.",
            "Peace in Sage.",
            "He said",
            "A sage.",
        ],
    )
    def test_the_real_shapes_still_do(self, text):
        assert heard_wake_phrase(text)

    def test_strict_wants_the_name_spelt_out(self):
        """With music on, the recogniser gets the name right and the "hey"
        wrong ("Thank you, Sage", "And Sage"); a lyric has the shape but
        rarely the word."""
        yes = [
            "Hey Sage.",
            "hey stage",
            "Thank you, Sage.",
            "And Sage.",
            "In the Sage.",
        ]
        for said in yes:
            assert heard_wake_phrase(said, strict=True), said
        for said in ["hazage", "Easy", "Peace English.", "Peace in you."]:
            assert not heard_wake_phrase(said, strict=True), said


def test_verify_passes_strict_through_to_the_verdict():
    pcm = bytes(3200)
    loose = asyncio.run(WakeWordVerifier(_Backend("hazage")).verify(pcm))
    assert loose.confirmed and loose.strict is False
    strict = asyncio.run(WakeWordVerifier(_Backend("hazage")).verify(pcm, strict=True))
    assert not strict.confirmed and strict.strict is True
