"""One streaming conversation with Parakeet, spoken in Sage's turn events.

Parakeet emits tokens as it hears them and an ``<EOU>`` when the speaker
stops. That is mapped onto the vocabulary ``server/flux_routes.py`` already
proxies: the first word opens a turn (``StartOfTurn``), every new word is an
``Update`` carrying the words heard so far with their confidence, and
``<EOU>`` closes it (``EndOfTurn``). There is no eager end and no
resumption -- those are Flux ideas, and speculation simply never starts.

The one thing added on top of the model is a backstop: an utterance that
trails off without an ``<EOU>`` (a clipped word, a cough) still ends after a
quiet ``eot_timeout_ms``, as Flux's own timeout would, so the turn never
hangs on the model's judgement alone.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import numpy as np

from openjarvis.speech.parakeet.config import ParakeetConfig
from openjarvis.speech.parakeet.decoder import (
    StreamingDecoder,
    Token,
    Word,
    assemble_words,
)
from openjarvis.speech.parakeet.model import OnnxModel
from openjarvis.speech.parakeet.tokenizer import Tokenizer
from openjarvis.speech.turn_events import (
    EVENT_END_OF_TURN,
    EVENT_START_OF_TURN,
    EVENT_UPDATE,
    TurnEvent,
)

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
# Measured 22 September on this room's microphone: the model's own <EOU>
# fires ~300 ms after speech only when what follows is near-digital
# silence, and this room's floor never is, so across a 40 s conversation it
# closed 0 of 6 phrases (gating the audio to zeros recovered 2 and cost
# words). The token-silence timeout below is therefore the usual turn end;
# tokens only appear on speech, so noise cannot hold a turn open.
DEFAULT_EOT_TIMEOUT_MS = 900
# How often the timeout is checked while no audio arrives.
_IDLE_TICK_SECONDS = 0.1


class ParakeetEngine:
    """The loaded model, shared by every session: ~460 MB of encoder is not
    something to open per socket. ONNX sessions are safe to run from several
    threads; each conversation keeps its own decoder state."""

    def __init__(
        self, model: OnnxModel, tokenizer: Tokenizer, config: ParakeetConfig
    ) -> None:
        self.model = model
        self.tokenizer = tokenizer
        self.config = config

    @property
    def device(self) -> str:
        return self.model.device

    def new_decoder(self) -> StreamingDecoder:
        return StreamingDecoder(self.model, self.tokenizer, self.config)


_engine: Optional[ParakeetEngine] = None
_engine_lock = threading.Lock()


def load_engine(
    model_dir: str, *, device: str = "cuda", quant: str = ""
) -> ParakeetEngine:
    """Open (once) the shared engine. Blocking; call off the event loop."""
    global _engine
    with _engine_lock:
        if _engine is None:
            started = time.monotonic()
            model = OnnxModel.load(model_dir, device=device, quant=quant)
            tokenizer = Tokenizer.load(model_dir)
            _engine = ParakeetEngine(model, tokenizer, ParakeetConfig())
            logger.info(
                "Parakeet loaded from %s on %s in %.1fs",
                model_dir,
                model.device,
                time.monotonic() - started,
            )
        return _engine


def loaded_engine() -> Optional[ParakeetEngine]:
    return _engine


def _word_payload(word: Word) -> Dict[str, Any]:
    return {"word": word.word, "confidence": round(word.confidence, 4)}


class ParakeetSession:
    """Same surface as ``FluxSession``: ``send_audio``, ``events``, ``close``."""

    def __init__(
        self,
        engine: ParakeetEngine,
        *,
        eot_timeout_ms: int = DEFAULT_EOT_TIMEOUT_MS,
    ) -> None:
        self._engine = engine
        self._decoder = engine.new_decoder()
        self._eot_timeout = max(0.2, eot_timeout_ms / 1000.0)
        self._chunk = engine.config.samples_per_chunk
        self._audio: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self._pending = np.zeros(0, dtype=np.float32)
        self._closed = False
        # One decode at a time per session; the encoder cache is sequential.
        self._decode_lock = asyncio.Lock()
        # Turn state.
        self._turn_index = -1
        self._tokens: List[Token] = []
        self._turn_open = False
        self._last_token_at = 0.0
        self._audio_seconds = 0.0

    @property
    def connected(self) -> bool:
        return not self._closed

    @property
    def device(self) -> str:
        return self._engine.device

    async def connect(self, address: str = "") -> None:
        # Nothing to open: the engine is shared and already loaded.
        return None

    async def send_audio(self, chunk: bytes) -> None:
        """Accept one PCM chunk (int16 mono 16 kHz). No-op once closed."""
        if self._closed:
            return
        await self._audio.put(chunk)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._audio.put(None)

    async def events(self) -> AsyncIterator[TurnEvent]:
        while True:
            try:
                chunk = await asyncio.wait_for(
                    self._audio.get(), timeout=_IDLE_TICK_SECONDS
                )
            except asyncio.TimeoutError:
                timed_out = self._timeout_due()
                if timed_out is not None:
                    yield timed_out
                continue
            if chunk is None:
                return
            for event in await self._ingest(chunk):
                yield event

    # ----------------------------------------------------------------- turn

    async def _ingest(self, chunk: bytes) -> List[TurnEvent]:
        samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
        self._pending = np.concatenate([self._pending, samples])
        events: List[TurnEvent] = []
        while len(self._pending) >= self._chunk:
            block, self._pending = (
                self._pending[: self._chunk],
                self._pending[self._chunk :],
            )
            self._audio_seconds += len(block) / SAMPLE_RATE
            async with self._decode_lock:
                step = await asyncio.to_thread(self._decoder.feed, block)
            events.extend(self._apply(step.tokens, step.end_of_utterance))
            # Audio keeps arriving through silence (the browser never stops
            # sending), so the idle path above is not where a quiet turn
            # usually ends; it ends here, on a chunk that added no words.
            timed_out = self._timeout_due()
            if timed_out is not None:
                events.append(timed_out)
        return events

    def _apply(self, tokens: List[Token], end_of_utterance: bool) -> List[TurnEvent]:
        events: List[TurnEvent] = []
        now = time.monotonic()
        if tokens:
            if not self._turn_open:
                self._turn_index += 1
                self._turn_open = True
                self._tokens = []
                events.append(self._event(EVENT_START_OF_TURN))
            self._tokens.extend(tokens)
            self._last_token_at = now
            events.append(self._event(EVENT_UPDATE))
        if end_of_utterance and self._turn_open:
            events.append(self._end_turn(confidence=1.0))
        return events

    def _timeout_due(self) -> Optional[TurnEvent]:
        if not self._turn_open or not self._tokens:
            return None
        if time.monotonic() - self._last_token_at < self._eot_timeout:
            return None
        return self._end_turn(confidence=0.5)

    def _end_turn(self, *, confidence: float) -> TurnEvent:
        event = self._event(EVENT_END_OF_TURN, end_of_turn_confidence=confidence)
        self._turn_open = False
        self._decoder.reset_utterance()
        return event

    def _event(self, kind: str, *, end_of_turn_confidence: float = 0.0) -> TurnEvent:
        words = assemble_words(self._tokens)
        frame_ms = self._engine.config.frame_ms / 1000.0
        return TurnEvent(
            event=kind,
            turn_index=max(self._turn_index, 0),
            transcript=" ".join(w.word for w in words),
            end_of_turn_confidence=end_of_turn_confidence,
            audio_window_start=(words[0].start_frame * frame_ms) if words else 0.0,
            audio_window_end=(words[-1].end_frame + 1) * frame_ms if words else 0.0,
            words=[_word_payload(w) for w in words],
            raw={"provider": "parakeet"},
        )
