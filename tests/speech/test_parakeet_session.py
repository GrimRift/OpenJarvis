"""ParakeetSession speaks Sage's turn events."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from openjarvis.speech.parakeet.config import ParakeetConfig
from openjarvis.speech.parakeet.decoder import Step, Token
from openjarvis.speech.parakeet.session import ParakeetEngine, ParakeetSession
from openjarvis.speech.turn_events import (
    EVENT_END_OF_TURN,
    EVENT_START_OF_TURN,
    EVENT_UPDATE,
)


class ScriptedDecoder:
    """``feed`` answers the n-th 160 ms chunk with ``script[n]``."""

    def __init__(self, script):
        self.script = script
        self.calls = 0
        self.utterance_resets = 0

    def feed(self, block):
        assert len(block) == 2560, "the session must hand the model 160 ms chunks"
        step = self.script.get(self.calls, Step(tokens=[]))
        self.calls += 1
        return step

    def reset_utterance(self):
        self.utterance_resets += 1


class ScriptedEngine(ParakeetEngine):
    def __init__(self, script):
        self.script = script
        self.config = ParakeetConfig()
        self.decoders = []

    @property
    def device(self):
        return "cpu"

    def new_decoder(self):
        decoder = ScriptedDecoder(self.script)
        self.decoders.append(decoder)
        return decoder


def _frame(ms=50):
    return np.zeros(16 * ms, dtype=np.int16).tobytes()


async def _drive(session, frames, *, settle=0.0):
    events = []

    async def consume():
        async for event in session.events():
            events.append(event)

    task = asyncio.create_task(consume())
    for _ in range(frames):
        await session.send_audio(_frame())
    await asyncio.sleep(settle)
    await session.close()
    await asyncio.wait_for(task, 2)
    return events


def test_fifty_ms_frames_are_batched_into_the_models_chunk():
    engine = ScriptedEngine({})
    session = ParakeetSession(engine)
    asyncio.run(_drive(session, 7))
    # 7 x 50 ms = 350 ms: two full 160 ms chunks, 30 ms left waiting.
    assert engine.decoders[0].calls == 2


def test_words_open_a_turn_update_it_and_eou_closes_it():
    tokens = [Token("▁what", 0.9, 0), Token("'s", 0.8, 0), Token("▁up", 0.7, 1)]
    script = {
        0: Step(tokens=tokens[:2]),
        1: Step(tokens=tokens[2:]),
        2: Step(tokens=[], end_of_utterance=True),
    }
    session = ParakeetSession(ScriptedEngine(script))
    events = asyncio.run(_drive(session, 12))
    kinds = [e.event for e in events]
    assert kinds == [EVENT_START_OF_TURN, EVENT_UPDATE, EVENT_UPDATE, EVENT_END_OF_TURN]
    final = events[-1]
    assert final.turn_index == 0
    assert final.transcript == "what's up"
    assert final.words == [
        {"word": "what's", "confidence": 0.8},
        {"word": "up", "confidence": 0.7},
    ]
    assert final.end_of_turn_confidence == 1.0
    assert events[1].words[0]["word"] == "what's"


def test_an_eou_with_no_words_is_not_a_turn():
    # The model marks the initial silence as an utterance end; that opens
    # nothing and must not count a turn.
    script = {
        0: Step(tokens=[], end_of_utterance=True),
        2: Step(tokens=[Token("▁hey", 0.9, 2)]),
    }
    session = ParakeetSession(ScriptedEngine(script))
    events = asyncio.run(_drive(session, 12))
    assert [e.event for e in events][:2] == [EVENT_START_OF_TURN, EVENT_UPDATE]
    assert events[0].turn_index == 0


def test_a_turn_that_trails_off_ends_on_the_token_silence_timeout():
    script = {0: Step(tokens=[Token("▁hey", 0.9, 0)])}
    engine = ScriptedEngine(script)
    session = ParakeetSession(engine, eot_timeout_ms=200)
    # Audio keeps flowing (silence), no more tokens, no <EOU> from the model.
    events = asyncio.run(_drive(session, 4, settle=0.5))
    assert [e.event for e in events] == [
        EVENT_START_OF_TURN,
        EVENT_UPDATE,
        EVENT_END_OF_TURN,
    ]
    assert events[-1].end_of_turn_confidence == 0.5
    assert engine.decoders[0].utterance_resets == 1


def test_turn_indices_advance_and_the_decoder_forgets_the_last_utterance():
    script = {
        0: Step(tokens=[Token("▁hey", 0.9, 0)]),
        1: Step(tokens=[], end_of_utterance=True),
        3: Step(tokens=[Token("▁up", 0.9, 3)]),
        4: Step(tokens=[], end_of_utterance=True),
    }
    engine = ScriptedEngine(script)
    session = ParakeetSession(engine)
    events = asyncio.run(_drive(session, 20))
    finals = [e for e in events if e.event == EVENT_END_OF_TURN]
    assert [(e.turn_index, e.transcript) for e in finals] == [(0, "hey"), (1, "up")]
    assert engine.decoders[0].utterance_resets == 2


def test_audio_after_close_is_dropped():
    session = ParakeetSession(ScriptedEngine({}))

    async def run():
        await session.close()
        await session.send_audio(_frame())
        assert session._audio.qsize() == 1  # only the close sentinel

    asyncio.run(run())


@pytest.mark.parametrize("kind", [EVENT_START_OF_TURN, EVENT_UPDATE])
def test_events_never_claim_flux_only_semantics(kind):
    session = ParakeetSession(ScriptedEngine({}))
    session._tokens = [Token("▁hey", 0.9, 0)]
    event = session._event(kind)
    assert not event.is_speculative and not event.cancels_speculation
    assert event.raw == {"provider": "parakeet"}
