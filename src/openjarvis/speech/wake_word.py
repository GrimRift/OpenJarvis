"""Local wake-word detection via openWakeWord.

No bundled/default model — "Hey Sage" is a custom-trained model the user
provides (openWakeWord's own Colab notebook trains it; only the resulting
.onnx file needs to exist locally). Feature is disabled whenever
``model_path`` is unset or the file doesn't exist, never a hard failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

# openWakeWord's native frame size: 80ms @ 16kHz, 16-bit mono PCM.
CHUNK_SAMPLES = 1280
SAMPLE_RATE = 16000
# 0.5 is openWakeWord's usual default, but the verifier-backed classifier
# (see custom_verifier_models below) is trained on a small amount of real
# audio and runs "hot". Calibrated against live measurements through the
# actual capture pipeline (raw mic input, no browser AGC/noise suppression
# — see useWakeWord.ts): ambient silence peaked at 0.3, keyboard clicks at
# 0.6, genuine "Hey Sage" reaching 0.74-1.0 when it fires. Raised from 0.76
# to 0.79 for extra margin above every measured false-positive source
# (silence, keyboard) after a live false positive on ambient noise.
DEFAULT_THRESHOLD = 0.79
# How many consecutive 80ms frames must clear the threshold before a
# detection counts — a single high-scoring frame from a noise transient is
# common; a sustained ~160ms run of them is not.
DETECTION_PATIENCE = 2


class WakeWordDetector:
    def __init__(self, model_path: str = "", threshold: float = DEFAULT_THRESHOLD) -> None:
        self._model_path = model_path
        self._threshold = threshold
        self._model = None
        self._model_name = ""
        # Hand-rolled rather than openWakeWord's own predict(patience=...):
        # that mechanism checks its *own* history buffer, which stores the
        # already-patience-adjusted score, not the raw one — so once a
        # frame gets zeroed for lacking history, every later frame's
        # lookback sees that same zero and also gets zeroed, forever
        # (confirmed empirically: every frame reads 0.0, including on the
        # real "Hey Sage" recording). Tracking raw-score history ourselves
        # avoids that self-referential deadlock.
        self._consecutive_hits = 0

    @property
    def available(self) -> bool:
        return bool(self._model_path) and Path(self._model_path).exists()

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if not self.available:
            raise RuntimeError("Wake word model not configured or file missing")
        from openwakeword.model import Model
        from openwakeword.utils import download_models

        # openWakeWord's shared feature-extraction backbone (melspectrogram +
        # embedding models) ships separately from the pip package and must be
        # fetched once. Idempotent — skips any file that already exists. The
        # bogus model_names value avoids also pulling every bundled pretrained
        # wake word (alexa, hey_jarvis, etc.), which we don't use.
        download_models(model_names=["_none_"])
        self._model_name = Path(self._model_path).stem

        # A verifier trained on real recordings (see
        # openwakeword.custom_verifier_model.train_custom_verifier) can be
        # dropped in next to the model as "<stem>_verifier.pkl" — used when
        # the classifier itself, trained purely on synthetic TTS audio,
        # doesn't generalize to a real voice. custom_verifier_threshold=0.0
        # is required here (not the library's 0.1 default): the classifier
        # in that scenario never scores above ~0.001 on real speech, so any
        # nonzero gate would mean the verifier never actually gets consulted.
        verifier_name = f"{self._model_name}_verifier.pkl"
        verifier_path = Path(self._model_path).with_name(verifier_name)
        custom_verifier_models = {}
        if verifier_path.exists():
            custom_verifier_models[self._model_name] = str(verifier_path)

        self._model = Model(
            wakeword_models=[self._model_path],
            custom_verifier_models=custom_verifier_models,
            custom_verifier_threshold=0.0,
        )

    def score(self, pcm_frame: bytes) -> float:
        """Feed one 1280-sample (80ms) int16 PCM frame; return the latest score."""
        self._ensure_loaded()
        audio = np.frombuffer(pcm_frame, dtype=np.int16)
        self._model.predict(audio)
        scores = self._model.prediction_buffer.get(self._model_name)
        score = float(scores[-1]) if scores else 0.0

        if score > self._threshold:
            self._consecutive_hits += 1
        else:
            self._consecutive_hits = 0
        return score

    def is_detection(self, score: float) -> bool:
        return score > self._threshold and self._consecutive_hits >= DETECTION_PATIENCE

    def reset(self) -> None:
        """Forget all audio heard so far. Call when a listening session starts.

        One detector is built at startup and shared by every session, and the
        model scores a rolling window of recent audio rather than each frame
        alone. Listening stops while Sage answers, so the window is still
        holding the "Hey Sage" that began the exchange when it resumes: the
        first frames of the new session land in a buffer that already ends
        with the wake word, and it fires again on the strength of that.

        That is the mechanism behind the mic switching itself on after every
        spoken reply in silence — no new sound is involved, which is why
        waiting longer before re-arming never helped.
        """
        self._consecutive_hits = 0
        if self._model is not None:
            self._model.reset()


_DETECTOR: Optional[WakeWordDetector] = None


def get_wake_word_detector(model_path: str) -> Optional[WakeWordDetector]:
    """Build a detector from a config-supplied model path, or None if unset."""
    if not model_path:
        return None
    detector = WakeWordDetector(model_path=model_path)
    return detector if detector.available else None
