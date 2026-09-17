"""Second opinion on a wake-word detection: did anyone say the words?

The detector (``wake_word.py``) judges acoustic shape in a 1.28 s window,
and a loud transient of the right length can clear it. This transcribes
the last two seconds of audio and confirms only if the phrase is in the
text. Speech-to-text mishears the name in the same few ways every time
("stage", "sayge"), so the match is a written-down list, not a learned
one. Verification can only remove firings: no backend or an exception
confirms, so the wake word never goes deaf because the verifier did; a
*timeout* rejects, since a check that cannot finish is usually the GPU
busy while Sage's own voice tripped the detector (docs/wake-word-verify.md).
"""

from __future__ import annotations

import array
import asyncio
import inspect
import io
import logging
import os
import re
import threading
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
#: The local model answers in ~130 ms warm and ~500 ms cold; past this it
#: is fighting something for the GPU. A timeout used to confirm, and a
#: class reminder's own voice woke Sage through it: now it rejects, and
#: the user says the word again.
VERIFY_TIMEOUT_SECONDS = 3.0
#: After a detection, this many more 80 ms frames are gathered before the
#: transcript is read, up to this many times: the detector fires on "hey
#: sa-" with the phrase still in the air (measured 17 September on 61
#: takes: whole phrase in the ring at the firing for 14, at +320 ms for 38,
#: at +640 ms for 51; the extra audio let no negative through).
VERIFY_STAGE_FRAMES = 4
VERIFY_STAGES = 2

#: The modes ``[speech] wake_word_verify`` and the browser's setting accept.
#: There was a Deepgram option for an evening; the small local model was
#: both faster and more accurate on this voice, so it went.
VERIFY_MODES = ("local", "off")

_HEY = {
    "hey",
    "hi",
    "hay",
    "he",
    "a",
    "eh",
    "ay",
    "ok",
    "okay",
    "hei",
    "hej",
    "yo",
    # Heard live before the name, 17 September: "thanks Sage", "thank you,
    # Sage", "and Sage", "peace in Sage".
    "thanks",
    "thank",
    "and",
    "in",
    "i'm",
}
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
#: which is how Deepgram wrote the live clips of 16 September. The lead
#: must be "hey"-shaped -- h plus a vowel or two, a bare vowel, be, pe --
#: since a lead of any four letters let "see you", "you see?" and "can you
#: see?" through on the first day (a reminder's own echo, then; a video's
#: line, next time).
_PHONETIC_TAIL = re.compile(
    r"(?:h[aeiy]{1,2}|[aeiy]{1,2}|be|pe)[scz][aeiy]+"
    r"(?:[gjdnmzv]+(?:e|s|es)?|ch|s|es|you)?$"
)
#: A bare vowel run after the s ("easy", "hazy"), or "you" after it ("hey,
#: see you" -- three recordings), is the phrase only behind a lead with an
#: h in it or "ea"; "i see" and "soon, see you" are not.
_BARE_END = re.compile(r"(?:h[aeiy]{1,2}|ea)[scz][aeiy]+(?:you)?$")
#: Letters of transcript the phonetic rule may look at, so a long sentence
#: that happens to end in the shape is not mistaken for the phrase.
_PHONETIC_LETTERS = 12


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z']+", text.lower()) if t]


def heard_wake_phrase(text: str, *, strict: bool = False) -> bool:
    """Whether the transcript is "hey sage" as Whisper usually writes it.

    Two rules, either suffices. By words: a hey-word followed by a
    sage-word, at most one token between them ("hey, uh, sage"). By sound:
    the run-together letters end in the phrase's shape (see
    ``_PHONETIC_TAIL``). "sage" alone passes neither: the detector already
    required the whole phrase acoustically, and the decision was to hold
    the transcript to the same standard. ``strict`` drops the sound rule
    and asks only that the *name* be spelt out: used while another app is
    audibly playing, when a lyric can have the shape but rarely the word,
    and the recogniser, hearing the user over music, gets the name right
    and the "hey" wrong ("Thank you, Sage", "And Sage" -- 17 September).
    """
    words = [w.rstrip("'s") if w.endswith("'s") else w for w in _tokens(text)]
    if strict:
        return any(w in _SAGE for w in words)
    for i, word in enumerate(words):
        if word not in _HEY:
            continue
        for j in (i + 1, i + 2):
            if j < len(words) and words[j] in _SAGE:
                return True
    letters = re.sub(r"[^a-z]", "", text.lower())[-_PHONETIC_LETTERS:]
    match = _PHONETIC_TAIL.search(letters)
    if not match:
        return False
    if re.search(r"[scz][aeiy]+(?:you)?$", match.group(0)):
        return bool(_BARE_END.search(letters))
    return True


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
    #: How long the transcriber took, so slowness is measured, not felt.
    ms: int = 0
    #: Whether another app was audibly playing, so only the words counted.
    strict: bool = False


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
        # A backend that takes no prompt (a fake in tests) still works.
        if self._initial_prompt and _accepts(
            self._backend.transcribe, "initial_prompt"
        ):
            kwargs["initial_prompt"] = self._initial_prompt
        result = self._backend.transcribe(pcm_to_wav(normalise_level(pcm)), **kwargs)
        return str(getattr(result, "text", "") or "").strip()

    async def verify(self, pcm: bytes, *, strict: bool = False) -> Verdict:
        if self._backend is None:
            return Verdict(True, "", "no speech backend")
        if not pcm:
            return Verdict(True, "", "no audio buffered")
        started = time.perf_counter()
        try:
            heard = await asyncio.wait_for(
                asyncio.to_thread(self._transcribe, pcm), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            # Not fail-open like the other paths: a firing that cannot be
            # checked in time is more likely Sage's own voice than the user.
            return Verdict(False, "", f"verifier slower than {self._timeout:g}s")
        except Exception as exc:  # noqa: BLE001 -- fail open, by design
            logger.debug("Wake-word verification failed: %s", exc)
            return Verdict(True, "", f"verifier error: {exc}")
        ms = int((time.perf_counter() - started) * 1000)
        verdict = Verdict(
            heard_wake_phrase(heard, strict=strict), heard, "", ms, strict
        )
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


#: The dedicated local verifier model, loaded once per process. Measured
#: 16 September on the recorded and live clips: tiny.en on CUDA answers in
#: ~110 ms warm and, prompted with the phrase, writes "Hey Sage." where
#: distil-large wrote "hazage" -- 117/119 positives, no negative accepted.
#: The big transcription model is the wrong tool for a two-second yes/no.
_LOCAL_MODEL: Any = None
_LOCAL_MODEL_KEY: tuple = ()
_LOCAL_LOCK = threading.Lock()


def local_verifier_backend(config: Any) -> Any:
    """The small Whisper for verification, built on first use and kept."""
    global _LOCAL_MODEL, _LOCAL_MODEL_KEY
    speech = getattr(config, "speech", None)
    key = (
        str(getattr(speech, "wake_word_verify_model", "tiny.en") or "tiny.en"),
        str(getattr(speech, "device", "auto") or "auto"),
        str(getattr(speech, "compute_type", "float16") or "float16"),
    )
    with _LOCAL_LOCK:
        if _LOCAL_MODEL is not None and _LOCAL_MODEL_KEY == key:
            return _LOCAL_MODEL
        from openjarvis.speech.faster_whisper import FasterWhisperBackend

        _LOCAL_MODEL = FasterWhisperBackend(
            model_size=key[0], device=key[1], compute_type=key[2]
        )
        _LOCAL_MODEL_KEY = key
        return _LOCAL_MODEL


def warm_local_verifier(config: Any) -> None:
    """Load the small model now, so the first "Hey Sage" is not the slow one."""
    try:
        local_verifier_backend(config).health()
    except Exception as exc:  # noqa: BLE001 -- warm-up is optional
        logger.debug("Wake-word verifier warm-up failed: %s", exc)


#: Another app peaking above this is "audibly playing"; 0.05 is well
#: under speech but above meter noise on a silent session.
MEDIA_PEAK_THRESHOLD = 0.05


def media_is_playing() -> bool:
    try:
        from openjarvis.speech.ducking import media_peak

        return media_peak() > MEDIA_PEAK_THRESHOLD
    except Exception:  # noqa: BLE001
        return False


def make_verifier(
    config: Any, backend: Any, mode: Optional[str] = None
) -> Optional[WakeWordVerifier]:
    """The verifier for one socket, or ``None`` when switched off.

    ``mode`` is the browser's choice (the Settings page); the config key
    is the default when it sends none.
    """
    speech = getattr(config, "speech", None)
    chosen = str(mode or getattr(speech, "wake_word_verify", "local") or "off").lower()
    if chosen == "off":
        return None
    if chosen not in VERIFY_MODES:
        logger.warning("Unknown wake_word_verify=%r; treating as 'local'", chosen)
        chosen = "local"
    try:
        transcriber = local_verifier_backend(config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Small verifier model unavailable (%s); using main", exc)
        transcriber = backend
    return WakeWordVerifier(
        transcriber,
        initial_prompt=str(getattr(speech, "initial_prompt", "Hey Sage.") or ""),
    )


__all__ = [
    "VERIFY_STAGE_FRAMES",
    "VERIFY_STAGES",
    "AudioRing",
    "RING_FRAMES",
    "VERIFY_MODES",
    "Verdict",
    "WakeWordVerifier",
    "heard_wake_phrase",
    "local_verifier_backend",
    "make_verifier",
    "normalise_level",
    "pcm_to_wav",
    "warm_local_verifier",
    "media_is_playing",
]
