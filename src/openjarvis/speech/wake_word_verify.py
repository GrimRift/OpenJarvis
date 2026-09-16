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
import inspect
import io
import logging
import os
import re
import time
import wave
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Optional

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280
#: Two seconds of 80 ms frames: enough for "Hey Sage" said slowly plus the
#: detector's own lag behind the end of the phrase.
RING_FRAMES = 25
VERIFY_TIMEOUT_SECONDS = 1.5

#: The modes ``[speech] wake_word_verify`` and the browser's setting accept.
VERIFY_MODES = ("deepgram", "local", "off")

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
#: The s may come out voiced ("hazage", "hazy") or as a soft c ("acid"),
#: which is how Deepgram wrote the live clips of 16 September.
_PHONETIC_TAIL = re.compile(
    r"[a-z]{1,4}[scz][aeiy]+(?:[gjdnmzv]+(?:e|s|es)?|ch|you|s|es)?$"
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


class DeepgramTranscriber:
    """Deepgram prerecorded (nova-3) on the v7 SDK, for one two-second clip.

    ``keyterm`` boosts the name so it is spelt as itself when it is heard
    clearly. The key never leaves the server; this is the same key Flux
    uses. About 200-350 ms a clip, measured live.
    """

    backend_id = "deepgram"

    def __init__(self, api_key: str, model: str = "nova-3") -> None:
        from deepgram import DeepgramClient

        self._client = DeepgramClient(api_key=api_key)
        self._model = model

    def transcribe(
        self, audio: bytes, *, format: str = "wav", language: Optional[str] = None
    ) -> Any:
        response = self._client.listen.v1.media.transcribe_file(
            request=audio,
            model=self._model,
            language=language or "en",
            keyterm=["Sage"],
            smart_format=False,
        )
        channels = getattr(getattr(response, "results", None), "channels", None) or []
        alternatives = getattr(channels[0], "alternatives", None) if channels else None
        text = alternatives[0].transcript if alternatives else ""

        class _Result:
            pass

        result = _Result()
        result.text = text  # type: ignore[attr-defined]
        return result


class WakeWordVerifier:
    """Runs a transcriber over the ring and decides."""

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

    @property
    def backend_id(self) -> str:
        return str(getattr(self._backend, "backend_id", "") or "")

    def _transcribe(self, pcm: bytes) -> str:
        kwargs: dict = {"format": "wav", "language": self._language or None}
        # Only Whisper takes a prompt; Deepgram is steered by keyterm instead.
        if self._initial_prompt and _accepts(
            self._backend.transcribe, "initial_prompt"
        ):
            kwargs["initial_prompt"] = self._initial_prompt
        result = self._backend.transcribe(pcm_to_wav(normalise_level(pcm)), **kwargs)
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
        verdict = Verdict(heard_wake_phrase(heard), heard)
        keep_clip(pcm, verdict)
        return verdict


#: Where the last few judged clips go when OPENJARVIS_WAKE_WORD_KEEP_CLIPS
#: names a directory: the only way to see what the verifier saw.
_KEEP_DIR = os.environ.get("OPENJARVIS_WAKE_WORD_KEEP_CLIPS", "")
_KEEP_MAX = 30


def keep_clip(pcm: bytes, verdict: Verdict) -> None:
    if not _KEEP_DIR:
        return
    try:
        folder = Path(_KEEP_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        tag = "ok" if verdict.confirmed else "rejected"
        heard = re.sub(r"[^a-z0-9]+", "_", verdict.heard.lower())[:30] or "nothing"
        (folder / f"{stamp}_{tag}_{heard}.wav").write_bytes(pcm_to_wav(pcm))
        for old in sorted(folder.glob("*.wav"))[:-_KEEP_MAX]:
            old.unlink()
    except OSError as exc:
        logger.debug("Could not keep wake-word clip: %s", exc)


def _accepts(func: Any, name: str) -> bool:
    try:
        return name in inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False


def _deepgram_key() -> str:
    from openjarvis.speech import flux

    key = flux.api_key()
    if key:
        return key
    try:
        from openjarvis.core.credentials import load_credentials

        for _tool, kvs in load_credentials().items():
            if kvs.get("DEEPGRAM_API_KEY"):
                return str(kvs["DEEPGRAM_API_KEY"])
    except Exception:  # noqa: BLE001
        pass
    return ""


def make_verifier(
    config: Any, backend: Any, mode: Optional[str] = None
) -> Optional[WakeWordVerifier]:
    """The verifier for one socket, or ``None`` when switched off.

    ``mode`` is the browser's choice (the Settings page); the config key
    is the default when it sends none. Deepgram without a key falls back
    to the local backend rather than to nothing.
    """
    speech = getattr(config, "speech", None)
    chosen = str(mode or getattr(speech, "wake_word_verify", "local") or "off").lower()
    if chosen == "off":
        return None
    if chosen not in VERIFY_MODES:
        logger.warning("Unknown wake_word_verify=%r; treating as 'local'", chosen)
        chosen = "local"
    transcriber = backend
    if chosen == "deepgram":
        key = _deepgram_key()
        if key:
            try:
                transcriber = DeepgramTranscriber(key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Deepgram verifier unavailable (%s); using local", exc)
        else:
            logger.info("No DEEPGRAM_API_KEY; wake-word verifier falls back to local")
    return WakeWordVerifier(
        transcriber,
        initial_prompt=str(getattr(speech, "initial_prompt", "Hey Sage.") or ""),
    )


__all__ = [
    "AudioRing",
    "DeepgramTranscriber",
    "RING_FRAMES",
    "VERIFY_MODES",
    "Verdict",
    "WakeWordVerifier",
    "heard_wake_phrase",
    "make_verifier",
    "normalise_level",
    "pcm_to_wav",
]
