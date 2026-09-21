"""Greedy streaming RNNT decoding for Parakeet, with a probability per token.

Sage's barge-in judges a partial transcript by per-word confidence: a stop
word must be heard at 0.8, counted words at 0.5, and a mode sets how many
sure words cut Sage off. Deepgram sends those numbers; Parakeet's runtime
does not. Owning the greedy loop makes them available -- the joint network's
softmax at the symbol it emitted is that symbol's probability, and a word's
confidence is the least sure of its pieces.

Derived from Parakeet-ONNX (Apache-2.0, github.com/thomas097/Parakeet-ONNX),
which decodes the same export without probabilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import numpy as np
from numpy.typing import NDArray

from openjarvis.speech.parakeet.config import ParakeetConfig
from openjarvis.speech.parakeet.features import MelFrontEnd
from openjarvis.speech.parakeet.model import DecoderState, EncoderCache, OnnxModel
from openjarvis.speech.parakeet.tokenizer import Tokenizer

WORD_START = "▁"  # SentencePiece's word boundary marker


@dataclass(frozen=True)
class Token:
    text: str
    prob: float
    frame: int  # absolute encoder frame index; one frame is 80 ms


@dataclass(frozen=True)
class Word:
    word: str
    confidence: float
    start_frame: int
    end_frame: int


@dataclass
class Step:
    tokens: List[Token]
    end_of_utterance: bool = False


def assemble_words(tokens: Sequence[Token]) -> List[Word]:
    """Join sub-word tokens into words. Confidence is the minimum over the
    word's tokens: a word half-heard is not a word heard."""
    words: List[Word] = []
    current: List[Token] = []

    def flush() -> None:
        if not current:
            return
        text = "".join(t.text for t in current).replace(WORD_START, "").strip()
        if text:
            words.append(
                Word(
                    word=text,
                    confidence=min(t.prob for t in current),
                    start_frame=current[0].frame,
                    end_frame=current[-1].frame,
                )
            )
        current.clear()

    for token in tokens:
        if token.text.startswith(WORD_START) and current:
            flush()
        current.append(token)
    flush()
    return words


def _softmax_prob(logits: NDArray, index: int) -> float:
    finite = np.where(np.isfinite(logits), logits, -np.inf)
    peak = float(np.max(finite))
    total = float(np.sum(np.exp(finite - peak)))
    return float(np.exp(finite[index] - peak) / total) if total > 0 else 0.0


class StreamingDecoder:
    """Feed audio in any chunk size; get back the tokens it produced."""

    def __init__(
        self,
        model: OnnxModel,
        tokenizer: Tokenizer,
        config: ParakeetConfig | None = None,
    ) -> None:
        self._model = model
        self._tokenizer = tokenizer
        self._cfg = config or ParakeetConfig()
        self._front_end = MelFrontEnd(self._cfg)
        self._blank_id = tokenizer.token_to_id("<EOB>")
        self._eou_id = tokenizer.token_to_id("<EOU>")
        self._vocab_size = tokenizer.vocab_size
        # Only the audio behind the frames the encoder is about to see is
        # kept: the mel of an 8 s buffer cost 3.6 ms a step against 0.3 ms
        # for this tail, and the frames come out identical as long as every
        # chunk fed is a multiple of the 160-sample hop (the session's are).
        self._min_samples = int(self._cfg.sample_rate * self._cfg.min_buffer_seconds)
        self._buffer = np.zeros(0, dtype=np.float32)
        self._primed = False
        self._cache = EncoderCache.empty(self._cfg)
        self._state = DecoderState.initial(self._cfg, self._blank_id)
        self._last_non_blank: int | None = None
        self._frames_seen = 0

    @property
    def frames_seen(self) -> int:
        return self._frames_seen

    def feed(self, audio: NDArray[np.float32]) -> Step:
        self._buffer = np.concatenate(
            [self._buffer, np.asarray(audio, dtype=np.float32).ravel()]
        )
        if not self._primed:
            if len(self._buffer) < self._min_samples:
                return Step(tokens=[])
            self._primed = True
        if len(self._buffer) > self._min_samples:
            self._buffer = self._buffer[-self._min_samples :]

        features = self._front_end(self._buffer)
        total = features.shape[2]
        start = max(0, total - self._cfg.pre_encode_cache - self._cfg.frames_per_chunk)
        window = features[:, :, start:]

        encoded, self._cache = self._model.run_encoder(
            window, window.shape[2], self._cache
        )
        return self._decode_frames(encoded)

    def _decode_frames(self, encoded: NDArray) -> Step:
        tokens: List[Token] = []
        for t in range(encoded.shape[2]):
            frame = encoded[:, :, t : t + 1]
            frame_index = self._frames_seen
            self._frames_seen += 1
            emitted = 0
            while emitted < self._cfg.max_symbols:
                logits, new_h, new_c = self._model.run_decoder(frame, self._state)
                finite = np.where(np.isfinite(logits), logits, -np.inf)
                best = int(np.argmax(finite))

                if best == self._eou_id:
                    # The model marks the end once; a repeat without new
                    # speech between is the same boundary heard again.
                    if self._last_non_blank == self._eou_id:
                        break
                    self._last_non_blank = self._eou_id
                    return Step(tokens=tokens, end_of_utterance=True)

                if best in (self._blank_id, 0) or best >= self._vocab_size:
                    break

                self._state = DecoderState(
                    h=new_h, c=new_c, last_token=np.full((1, 1), best, np.int32)
                )
                self._last_non_blank = best
                text = self._tokenizer.id_to_token(best) or ""
                if text:
                    tokens.append(
                        Token(
                            text=text,
                            prob=_softmax_prob(logits, best),
                            frame=frame_index,
                        )
                    )
                emitted += 1
        return Step(tokens=tokens)

    def reset_utterance(self) -> None:
        """Forget the prediction network's history at an utterance boundary,
        keeping the encoder's acoustic context. Without this the LSTM
        conditions the next sentence on the last one."""
        self._state = DecoderState.initial(self._cfg, self._blank_id)
        self._last_non_blank = None

    def reset(self) -> None:
        self.reset_utterance()
        self._cache = EncoderCache.empty(self._cfg)
        self._buffer = np.zeros(0, dtype=np.float32)
        self._primed = False
        self._frames_seen = 0
