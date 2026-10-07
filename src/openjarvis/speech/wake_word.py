"""Local wake-word detection via openWakeWord.

No bundled/default model — "Hey Sage" is a custom-trained model the user
provides (openWakeWord's own Colab notebook trains it; only the resulting
.onnx file needs to exist locally). Feature is disabled whenever
``model_path`` is unset or the file doesn't exist, never a hard failure.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import numpy as np

# openWakeWord's native frame size: 80ms @ 16kHz, 16-bit mono PCM.
CHUNK_SAMPLES = 1280
SAMPLE_RATE = 16000
# 0.5 is openWakeWord's usual default, but the verifier-backed classifier
# (see custom_verifier_models below) is trained on a small amount of real
# audio and runs "hot". This was walked down to 0.71 while the training set
# was too small and too uniform to recognise anything but a careful, loud
# "Hey Sage" -- recall was the binding constraint, so false positives were
# traded for it.
#
# A deliberately varied recording session (normal/fast/quiet/far/loud, with
# typing, mouse and room tone captured at the same mic level so loudness
# could not stand in for the word) removed that constraint: held-out recall
# is 100% anywhere in 0.71-0.83, so the low threshold buys nothing and only
# widens what can trip it. Back up to 0.79 for margin against audio unlike
# anything in the training set.
#
# Then (17 September) a detection stopped being the final word: every
# firing is transcribed and must contain the phrase
# (speech/wake_word_verify.py), and the first day of that showed noise
# firings rejected fourteen times out of fourteen. With a second stage
# that cheap and that reliable, the detector's job is recall, and the
# margin above comes down: a firing costs ~200 ms of GPU, a miss costs
# the user a repeat.
DEFAULT_THRESHOLD = 0.65
# While another app is audible (a video, music). Over a talking video the
# user's voice reaches the mic about as loud as the video's (7 October, the
# `video_20261007` test set: phrase peaks 1.1-2.0k RMS, video ~2.0-2.2k), and
# the detector scores the phrase low: 1/12 woke at 0.65, 5/12 at 0.3. The
# video alone fires it either way; the strict transcript rule (name at the
# end, no outro) rejected every one, and no take of talk over the video got
# through at 0.3. Not used without media: in a quiet room 0.3 woke nothing
# more (15/15 at both) and let "I asked Sage about it earlier" through.
MEDIA_THRESHOLD = 0.3
# How many consecutive 80ms frames must clear the threshold before a
# detection counts — a single high-scoring frame from a noise transient is
# common; a sustained ~160ms run of them is not.
DETECTION_PATIENCE = 2
# A freshly reset detector needs `model_inputs` frames (16, ~1.28s) of real
# history before its rolling window is actually full, and the score spikes
# right as that happens regardless of what audio is arriving -- confirmed on
# this verifier's own quiet-room negative recordings, every one of which
# peaked (0.54-0.62) at exactly that frame, and in production by every page
# refresh (a fresh WebSocket -> fresh detector) firing on nothing but room
# noise. Not fixable by better training data since it isn't about audio
# content, only about the window's own fill state, so it's suppressed here
# instead: detections don't count until comfortably past that fill point.
WARMUP_FRAMES = 25
# ...and they are spent on silence (_prime) the moment a session starts or
# a detection resets it, not on the user. The page only sends frames that
# carry sound (MIN_FRAME_RMS), so in a quiet room -- a dynamic mic's raw
# floor is ~18 RMS, NVIDIA Broadcast's ~0 -- the warm-up waited for 2 s of
# SPEECH, however long the silence: the first "Hey Sage" after a reconnect
# or a conversation only warmed it up, and the user had to say it twice
# (6 October; near misses at 0.87 still warming_up=True minutes after a
# reconnect). Replayed over every recorded clip, a detector warmed on the
# clip itself caught 81/250 positives; primed on silence first, 214/250 --
# the same as one warmed on room noise (215/250), same negatives (51 vs 50).
# Digital silence, not noise: on the PD100X test set through the whole chain
# a noise-primed detector fired earlier, on "hey sa-", and woke 10/15 with
# two false wakes; silence-primed, 15/15 with one -- and silence is what a
# quiet room sends (nothing) and what NVIDIA Broadcast outputs.
# A score this high that never becomes a detection is logged as a near miss.
# A "Hey Sage" that does not fire leaves no clip and no trace line, so after
# a mic change (PD100X, 6 October) the user's "it ignores me" could not be
# told from a rejection or from nothing said. Room tone scores well under
# 0.1; the warm-up spike tops out ~0.6, so it is labelled, not filtered.
NEAR_MISS_SCORE = 0.3


# Guards the one-time openwakeword import/model download shared by every
# detector instance.
_LOAD_LOCK = threading.Lock()


class WakeWordDetector:
    def __init__(
        self, model_path: str = "", threshold: float = DEFAULT_THRESHOLD
    ) -> None:
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
        self._frames_since_reset = 0
        # Whether another app is audible (MEDIA_THRESHOLD), set by the socket.
        self.media = False

    @property
    def available(self) -> bool:
        return bool(self._model_path) and Path(self._model_path).exists()

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if not self.available:
            raise RuntimeError("Wake word model not configured or file missing")

        # Serialised across every detector, because each WebSocket connection
        # gets its own instance (clone()) and scores frames on its own thread
        # via asyncio.to_thread. Two sockets opening together therefore raced
        # this lazy import: one thread was still executing openwakeword's
        # __init__ while the other reached openwakeword.FEATURE_MODELS and got
        # "partially initialized module ... most likely due to a circular
        # import". It recovered on reconnect, so it looked like noise, but the
        # first wake word after a server restart could be lost.
        with _LOAD_LOCK:
            if self._model is not None:
                return
            self._load_locked()

    def _load_locked(self) -> None:
        # Imported before use so the package's __init__ has finished running;
        # importing only the submodules can leave the parent half-built.
        import openwakeword  # noqa: F401
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
        if self._frames_since_reset == 0:
            self._prime()
        audio = np.frombuffer(pcm_frame, dtype=np.int16)
        self._model.predict(audio)
        scores = self._model.prediction_buffer.get(self._model_name)
        score = float(scores[-1]) if scores else 0.0
        self._frames_since_reset += 1

        if score > self.threshold:
            self._consecutive_hits += 1
        else:
            self._consecutive_hits = 0
        return score

    def _prime(self) -> None:
        """Spend the warm-up on silence now, so the next real frame counts."""
        silence = np.zeros(CHUNK_SAMPLES, dtype=np.int16)
        for _ in range(WARMUP_FRAMES):
            self._model.predict(silence)
        self._frames_since_reset = WARMUP_FRAMES

    @property
    def threshold(self) -> float:
        """The threshold now: lower while another app is audible."""
        if self.media:
            return min(self._threshold, MEDIA_THRESHOLD)
        return self._threshold

    @property
    def base_threshold(self) -> float:
        return self._threshold

    @property
    def warming_up(self) -> bool:
        """Whether detections are still held back after a reset."""
        return self._frames_since_reset <= WARMUP_FRAMES

    def is_detection(self, score: float) -> bool:
        return (
            self._frames_since_reset > WARMUP_FRAMES
            and score > self.threshold
            and self._consecutive_hits >= DETECTION_PATIENCE
        )

    def clone(self) -> "WakeWordDetector":
        """A fresh detector with the same config and no audio history.

        The rolling-window buffer lives on the instance, so anything that
        needs its own independent listening session (each wake-word
        WebSocket connection) needs its own detector rather than sharing
        one: otherwise sessions inherit each other's buffered audio, and
        concurrent scoring races on the same model object.
        """
        return WakeWordDetector(model_path=self._model_path, threshold=self._threshold)

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
        self._frames_since_reset = 0
        if self._model is not None:
            self._model.reset()


class NearMiss:
    """One rise of the score toward the threshold that never became a detection.

    Fed every frame that could have fired; returns a summary once the score
    falls back under ``NEAR_MISS_SCORE`` without a detection in between.
    ``cancel`` drops the rise in progress: a detection, a pause or Sage's own
    voice are not misses.
    """

    def __init__(self, threshold: float, floor: float = NEAR_MISS_SCORE) -> None:
        self._threshold = threshold
        self._floor = floor
        self.cancel()

    def cancel(self) -> None:
        self._peak = 0.0
        self._frames = 0
        self._over = 0
        self._warm = False
        self._loud = 0.0

    def observe(
        self, score: float, *, warming_up: bool = False, level: float = 0.0
    ) -> Optional[dict]:
        if score >= self._floor:
            self._peak = max(self._peak, score)
            self._frames += 1
            self._over += score > self._threshold
            self._warm = self._warm or warming_up
            # The loudest frame the detector got (after the page's gain): a
            # low score from a quiet arrival reads differently from a low
            # score at full level (8 October, "dead after a stop").
            self._loud = max(self._loud, level)
            return None
        if not self._frames:
            return None
        miss = {
            "peak": round(self._peak, 3),
            "frames": self._frames,
            # Over the threshold, but not for DETECTION_PATIENCE frames in a
            # row, or only while warming up.
            "over": self._over,
            "warming_up": self._warm,
            "loud": round(self._loud),
        }
        self.cancel()
        return miss


_DETECTOR: Optional[WakeWordDetector] = None


def get_wake_word_detector(model_path: str) -> Optional[WakeWordDetector]:
    """Build a detector from a config-supplied model path, or None if unset."""
    if not model_path:
        return None
    detector = WakeWordDetector(model_path=model_path)
    return detector if detector.available else None
