"""The Parakeet decoding loop and its confidences, without any model files.

A scripted joint network stands in for the ONNX sessions: it answers each
encoder frame with a fixed sequence of symbols, so the tests pin how tokens
become words with confidences and when an utterance ends.
"""

from __future__ import annotations

import numpy as np
import pytest

from openjarvis.speech.parakeet.config import ParakeetConfig
from openjarvis.speech.parakeet.decoder import (
    StreamingDecoder,
    Token,
    assemble_words,
)

VOCAB = ["<unk>", "▁hey", "▁sa", "ge", "▁what", "'s", "▁up", "<EOU>", "<EOB>"]
EOU = VOCAB.index("<EOU>")
BLANK = VOCAB.index("<EOB>")


class FakeTokenizer:
    vocab_size = len(VOCAB)

    def token_to_id(self, token):
        return VOCAB.index(token)

    def id_to_token(self, index):
        return VOCAB[index] if 0 <= index < len(VOCAB) else None


class ScriptedModel:
    """Emits ``script[frame]`` (a list of (symbol, prob)) then blank."""

    device = "cpu"

    def __init__(self, script):
        self.script = script
        self.frame = 0
        self.emitted_in_frame = 0

    def run_encoder(self, features, length, cache):
        # One encoder frame per 16 mel frames, like the real export.
        frames = max(1, features.shape[2] // 16)
        return np.zeros((1, 4, frames), dtype=np.float32), cache

    def run_decoder(self, encoder_frame, state):
        plan = self.script.get(self.frame, [])
        logits = np.full(len(VOCAB), -20.0, dtype=np.float32)
        if self.emitted_in_frame < len(plan):
            symbol, prob = plan[self.emitted_in_frame]
            # Put `prob` of the mass on the symbol, the rest on blank.
            logits[symbol] = np.log(prob)
            logits[BLANK] = np.log(max(1e-6, 1 - prob))
            self.emitted_in_frame += 1
        else:
            logits[BLANK] = 0.0
            self.frame += 1
            self.emitted_in_frame = 0
        return logits, state.h, state.c


def _decoder(script):
    cfg = ParakeetConfig(min_buffer_seconds=0.1)
    return StreamingDecoder(ScriptedModel(script), FakeTokenizer(), cfg)


def _feed(dec, chunks):
    tokens, eou_at = [], None
    for i in range(chunks):
        step = dec.feed(np.zeros(2560, dtype=np.float32))
        tokens += step.tokens
        if step.end_of_utterance and eou_at is None:
            eou_at = i
    return tokens, eou_at


def test_subword_tokens_become_words_with_the_least_sure_piece_as_confidence():
    tokens = [
        Token("▁hey", 0.95, 0),
        Token("▁sa", 0.9, 1),
        Token("ge", 0.4, 1),
        Token("▁what", 0.99, 3),
        Token("'s", 0.8, 3),
        Token("▁up", 0.7, 4),
    ]
    words = assemble_words(tokens)
    assert [(w.word, w.confidence) for w in words] == [
        ("hey", 0.95),
        ("sage", 0.4),
        ("what's", 0.8),
        ("up", 0.7),
    ]
    assert (words[1].start_frame, words[1].end_frame) == (1, 1)


def test_the_probability_is_read_off_the_joint_softmax():
    script = {
        0: [(VOCAB.index("▁hey"), 0.9)],
        1: [(VOCAB.index("▁sa"), 0.6), (VOCAB.index("ge"), 0.97)],
    }
    tokens, _ = _feed(_decoder(script), 4)
    assert [t.text for t in tokens] == ["▁hey", "▁sa", "ge"]
    assert [round(t.prob, 2) for t in tokens] == [0.9, 0.6, 0.97]
    assert [w.confidence for w in assemble_words(tokens)] == pytest.approx(
        [0.9, 0.6], abs=0.01
    )


def test_eou_ends_the_step_and_is_not_repeated_until_speech_returns():
    script = {
        0: [(VOCAB.index("▁up"), 0.9)],
        1: [(EOU, 1.0)],
        2: [(EOU, 1.0)],
        3: [(VOCAB.index("▁hey"), 0.9)],
        4: [(EOU, 1.0)],
    }
    dec = _decoder(script)
    steps = [dec.feed(np.zeros(2560, dtype=np.float32)) for _ in range(8)]
    ends = [i for i, s in enumerate(steps) if s.end_of_utterance]
    # Frame 2's repeat is the same boundary; frame 4's follows new speech.
    # (The scripted model spends one extra step per frame on its blank, so
    # frame 4 is reached on the seventh feed.)
    assert ends == [1, 6]


def test_nothing_is_decoded_before_the_minimum_buffer():
    dec = _decoder({0: [(VOCAB.index("▁hey"), 0.9)]})
    assert dec.feed(np.zeros(800, dtype=np.float32)).tokens == []
    assert dec.frames_seen == 0


def test_reset_utterance_keeps_the_frame_clock():
    dec = _decoder({0: [(VOCAB.index("▁hey"), 0.9)]})
    _feed(dec, 3)
    seen = dec.frames_seen
    dec.reset_utterance()
    assert dec.frames_seen == seen
    dec.reset()
    assert dec.frames_seen == 0
