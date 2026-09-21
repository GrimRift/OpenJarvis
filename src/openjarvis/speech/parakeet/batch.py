"""Parakeet as a whole-clip transcriber, for the wake-word verifier.

The verifier hands a two-second WAV to something with ``transcribe`` and
reads ``.text`` back. This runs the streaming engine over the clip (padded
with silence so the last word is committed) so the already-loaded model can
be that something, instead of a second Whisper. Whether it should be is a
measured question -- see ``scripts/wake_word_verify_compare.py``.
"""

from __future__ import annotations

import io
import wave
from typing import List, Optional

import numpy as np

from openjarvis.speech._stubs import TranscriptionResult
from openjarvis.speech.parakeet.decoder import assemble_words
from openjarvis.speech.parakeet.session import ParakeetEngine

TAIL_SECONDS = 0.8


def _pcm_from_wav(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data)) as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit PCM, got {width * 8}-bit")
    pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    if rate != 16000:
        from scipy.signal import resample_poly

        pcm = resample_poly(pcm, 16000, rate).astype(np.float32)
    return pcm


class ParakeetBatchTranscriber:
    backend_id = "parakeet"

    def __init__(self, engine: ParakeetEngine) -> None:
        self._engine = engine

    def transcribe(
        self,
        audio: bytes,
        *,
        format: str = "wav",
        language: Optional[str] = None,
        **_: object,
    ) -> TranscriptionResult:
        if format != "wav":
            raise ValueError("ParakeetBatchTranscriber reads WAV only")
        pcm = _pcm_from_wav(audio)
        chunk = self._engine.config.samples_per_chunk
        pcm = np.concatenate([pcm, np.zeros(int(16000 * TAIL_SECONDS), np.float32)])
        pad = (-len(pcm)) % chunk
        if pad:
            pcm = np.concatenate([pcm, np.zeros(pad, np.float32)])
        decoder = self._engine.new_decoder()
        tokens: List = []
        for start in range(0, len(pcm), chunk):
            tokens.extend(decoder.feed(pcm[start : start + chunk]).tokens)
        words = assemble_words(tokens)
        text = " ".join(w.word for w in words)
        confidence = min((w.confidence for w in words), default=0.0)
        return TranscriptionResult(
            text=text,
            language=language or "en",
            confidence=confidence,
            duration_seconds=len(pcm) / 16000.0,
        )

    def health(self) -> bool:
        return True
