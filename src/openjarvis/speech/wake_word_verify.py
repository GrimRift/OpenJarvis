"""Second opinion on a wake-word detection: did anyone say the words?

The detector (``wake_word.py``) judges acoustic shape in a 1.28 s window,
and a loud transient of the right length can clear it. This transcribes
the last two seconds of audio and confirms only if the phrase is in the
text. Speech-to-text mishears the name in the same few ways every time
("stage", "sayge"), so the match is a written-down list, not a learned
one. Verification can only remove firings: any failure -- no backend,
timeout, exception -- confirms, so the wake word never goes deaf because
the verifier did (docs/wake-word-verify.md).
"""

from __future__ import annotations

import array
import asyncio
import io
import logging
import re
import wave
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Optional

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280
#: Two seconds of 80 ms frames: enough for "Hey Sage" said slowly plus the
#: detector's own lag behind the end of the phrase.
RING_FRAMES = 25
VERIFY_TIMEOUT_SECONDS = 1.5

#: The modes ``[speech] wake_word_verify`` accepts.
VERIFY_MODES = ("local", "off")

_HEY = {"hey", "hi", "hay", "he", "a", "eh", "ay", "ok", "okay", "hei", "hej", "yo"}
_SAGE = {
    "sage",
    "sages",
    "stage",
    "sayge",
    "saige",
    "seige",
    "siege",
    "sadge",
    "sege",
    "says",
    "sais",
    "sedge",
    "sagey",
    "save",
}


#: How "hey sage" sounds to Whisper once the letters are run together,
#: measured on 189 recordings: heysage, asage, hesin, heyseeyou, hesaid,
#: aseage, haysage, besage, easy, hesees, aseed, hesings, hesage, haseage.
#: One to four letters of lead (the "hey"), an s, a vowel run, then a
#: consonant tail from the small set those recordings used -- so "is it"
#: (a t) and "is blue" (a b after the s) do not pass. Anchored to the end
#: of the transcript: the detector fires as the phrase ends, so the phrase
#: is the last thing in the two-second ring.
_PHONETIC_TAIL = re.compile(
    r"[a-z]{1,4}s[aeiy]+(?:[gjdnmzv]+(?:e|s|es)?|ch|you|s|es)?$"
)
#: Letters of transcript the phonetic rule may look at, so a long sentence
#: that happens to end in the shape is not mistaken for the phrase.
_PHONETIC_LETTERS = 12


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z']+", text.lower()) if t]


def heard_wake_phrase(text: str) -> bool:
    """Whether the transcript is "hey sage" as Whisper usually writes it.

    Two rules, either suffices. By words: a hey-word followed by a
    sage-word, at most one token between them ("hey, uh, sage"). By sound:
    the run-together letters end in the phrase's shape (see
    ``_PHONETIC_TAIL``). "sage" alone passes neither: the detector already
    required the whole phrase acoustically, and the decision was to hold
    the transcript to the same standard.
    """
    words = [w.rstrip("'s") if w.endswith("'s") else w for w in _tokens(text)]
    for i, word in enumerate(words):
        if word not in _HEY:
            continue
        for j in (i + 1, i + 2):
            if j < len(words) and words[j] in _SAGE:
                return True
    letters = re.sub(r"[^a-z]", "", text.lower())[-_PHONETIC_LETTERS:]
    return bool(_PHONETIC_TAIL.search(letters))


#: Peak the clip is brought to before transcription. Whisper's VAD hears
#: nothing in a quiet recording -- two of the recorded sessions, at peaks
#: of 223 and 2,549 against ~22,000 for speech at the desk, transcribed as
#: empty every time -- and the detector fired on them all the same.
_TARGET_PEAK = 20000


def normalise_level(pcm: bytes) -> bytes:
    """Scale int16 PCM so its peak sits at ``_TARGET_PEAK``; never louder."""
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if not samples:
        return pcm
    peak = max(abs(v) for v in samples)
    if peak == 0 or peak >= _TARGET_PEAK:
        return pcm
    gain = _TARGET_PEAK / peak
    scaled = array.array("h", (int(v * gain) for v in samples))
    return scaled.tobytes()


def pcm_to_wav(pcm: bytes) -> bytes:
    """16 kHz mono int16 PCM as a WAV file, which every backend reads."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return buf.getvalue()


@dataclass(frozen=True)
class Verdict:
    confirmed: bool
    heard: str
    #: Why a firing was confirmed without the words -- empty when the
    #: transcript itself decided.
    note: str = ""


class AudioRing:
    """The last ``RING_FRAMES`` frames the socket received."""

    def __init__(self, frames: int = RING_FRAMES) -> None:
        self._frames: Deque[bytes] = deque(maxlen=frames)

    def push(self, frame: bytes) -> None:
        self._frames.append(frame)

    def pcm(self) -> bytes:
        return b"".join(self._frames)

    def clear(self) -> None:
        self._frames.clear()


class WakeWordVerifier:
    """Runs the configured speech backend over the ring and decides."""

    def __init__(
        self,
        backend: Any,
        *,
        timeout: float = VERIFY_TIMEOUT_SECONDS,
        language: str = "en",
        initial_prompt: str = "Hey Sage.",
    ) -> None:
        self._backend = backend
        self._timeout = timeout
        self._language = language
        self._initial_prompt = initial_prompt

    def _transcribe(self, pcm: bytes) -> str:
        result = self._backend.transcribe(
            pcm_to_wav(normalise_level(pcm)),
            format="wav",
            language=self._language or None,
            initial_prompt=self._initial_prompt or None,
        )
        return str(getattr(result, "text", "") or "").strip()

    async def verify(self, pcm: bytes) -> Verdict:
        if self._backend is None:
            return Verdict(True, "", "no speech backend")
        if not pcm:
            return Verdict(True, "", "no audio buffered")
        try:
            heard = await asyncio.wait_for(
                asyncio.to_thread(self._transcribe, pcm), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            return Verdict(True, "", f"verifier slower than {self._timeout:g}s")
        except Exception as exc:  # noqa: BLE001 -- fail open, by design
            logger.debug("Wake-word verification failed: %s", exc)
            return Verdict(True, "", f"verifier error: {exc}")
        return Verdict(heard_wake_phrase(heard), heard)


def make_verifier(config: Any, backend: Any) -> Optional[WakeWordVerifier]:
    """The verifier for this server, or ``None`` when switched off."""
    speech = getattr(config, "speech", None)
    mode = str(getattr(speech, "wake_word_verify", "local") or "off").lower()
    if mode == "off":
        return None
    if mode not in VERIFY_MODES:
        logger.warning("Unknown wake_word_verify=%r; treating as 'local'", mode)
    return WakeWordVerifier(
        backend,
        initial_prompt=str(getattr(speech, "initial_prompt", "Hey Sage.") or ""),
    )


__all__ = [
    "AudioRing",
    "RING_FRAMES",
    "VERIFY_MODES",
    "Verdict",
    "WakeWordVerifier",
    "heard_wake_phrase",
    "make_verifier",
    "normalise_level",
    "pcm_to_wav",
]
