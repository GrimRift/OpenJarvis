"""The sidecar's runaway guard: the repeats the user hears are a phrase
said twice, which shows up as audio far longer than the text warrants."""

from __future__ import annotations

import numpy as np
import pytest

engine = pytest.importorskip("voice_sidecar.engine")

SR = engine.SAMPLE_RATE


def _seconds(n: float) -> np.ndarray:
    return np.zeros(int(SR * n), dtype=np.float32)


class TestBudget:
    def test_a_line_said_twice_is_a_runaway(self):
        # Measured live: 37 characters came back as 6.6 s, a repeat.
        text = "It is three o'clock in the afternoon."
        assert len(text) == 37
        assert engine._ran_away(_seconds(6.6), text)

    def test_a_slow_clean_delivery_is_not(self):
        # Every clean clip in the bench fit 75 ms/char + 0.4 s; the budget
        # leaves room above that for pauses.
        text = "It is three o'clock in the afternoon."
        assert not engine._ran_away(_seconds(0.075 * len(text) + 0.4), text)

    def test_long_text_keeps_the_same_margin(self):
        text = "x" * 107
        assert engine._ran_away(_seconds(15.3), text)
        assert not engine._ran_away(_seconds(0.075 * 107 + 0.4), text)


class TestRetry:
    def _engine(self, outputs):
        torch = pytest.importorskip("torch")

        class Model:
            def __init__(self):
                self.calls = []

            def generate(self, text, **kw):
                self.calls.append(kw)
                return torch.from_numpy(outputs.pop(0))[None, :]

        eng = engine.ChatterboxEngine.__new__(engine.ChatterboxEngine)
        eng.precision = "fp32"
        eng.device = "cpu"
        eng.requested_device = "cpu"
        eng.model = Model()
        eng.lock = __import__("threading").Lock()
        eng.generations = 0
        eng.last_generation_seconds = 0.0
        eng.current_voice = "v"
        eng.voices = type(
            "Store", (), {"params": lambda self, name: engine.VoiceParams()}
        )()
        eng.use_voice = lambda name: None
        return eng

    def test_a_runaway_is_resampled_at_library_defaults(self):
        text = "It is three o'clock in the afternoon."
        eng = self._engine([_seconds(6.6), _seconds(2.6)])
        audio = eng.generate(text, "v")
        assert len(audio) == int(SR * 2.6)
        # First call carries the voice's sampling; the retry passes none.
        assert eng.model.calls[0]["temperature"] == 0.7
        assert eng.model.calls[1] == {}

    def test_after_the_retries_the_audio_is_cut_at_the_budget(self):
        text = "It is three o'clock in the afternoon."
        eng = self._engine([_seconds(6.6), _seconds(7.0), _seconds(8.0)])
        audio = eng.generate(text, "v")
        assert len(eng.model.calls) == 1 + engine.RUNAWAY_RETRIES
        assert len(audio) == engine._budget_samples(text)


class TestMergedAcknowledgement:
    """The sidecar says a short segment together with the next one and
    acknowledges both in one ``segment_done``. The route counts spoken
    segments against sent ones and re-sends the difference, so a single
    count made it say the second segment twice."""

    def test_segment_done_counts_every_segment_it_spoke(self):
        import asyncio
        import json

        from openjarvis.speech.chatterbox_tts import ChatterboxContext

        class Socket:
            def __init__(self, frames):
                self._frames = frames

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._frames:
                    raise StopAsyncIteration
                return self._frames.pop(0)

        context = ChatterboxContext.__new__(ChatterboxContext)
        context._flushes = 0
        context._cancelled = False
        context._done = False
        context._socket = Socket(
            [
                b"\x00\x00\x00\x00",
                json.dumps({"type": "segment_done", "segments": 2}),
                json.dumps({"type": "segment_done"}),
                json.dumps({"type": "done"}),
            ]
        )

        async def drain():
            return [chunk async for chunk in context.receive_audio()]

        chunks = asyncio.run(drain())
        assert chunks == [b"\x00\x00\x00\x00"]
        assert context.flushes == 3
