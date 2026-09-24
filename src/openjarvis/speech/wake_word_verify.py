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
#: Letters of transcript allowed ahead of a sound match: one short word ("oh,
#: he's in"). Every recorded mishearing starts the transcript, and a sound
#: match further in is a sentence that happens to end in the shape -- "Look,
#: he's in." from a conversation downstairs, 23 September. Not applied when
#: the name itself ends it ("Peace Sage."): the hey was misheard, not made up.
_PHONETIC_MAX_LEAD = 3
#: Whisper's no-speech probability at or above which only the words confirm,
#: not the sound. Distant, muffled speech sets the detector off and Whisper
#: then guesses a word for it; "Easy." from a conversation downstairs (23
#: September) scored 0.52. The only two recorded takes out of 220 that
#: passed on the sound alone scored 0.06 (from the door) and 0.01.
MUFFLED_NO_SPEECH = 0.45


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z']+", text.lower()) if t]


def heard_wake_phrase(
    text: str, *, strict: bool = False, muffled: bool = False
) -> bool:
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
    ``muffled`` also drops the sound rule, keeping the words: the
    recogniser doubted it heard speech at all (see ``MUFFLED_NO_SPEECH``).
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
    if muffled:
        return False
    full = re.sub(r"[^a-z]", "", text.lower())
    letters = full[-_PHONETIC_LETTERS:]
    match = _PHONETIC_TAIL.search(letters)
    if not match:
        return False
    named = bool(words) and words[-1] in _SAGE
    if not named and len(full) - len(letters) + match.start() > _PHONETIC_MAX_LEAD:
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
    #: Why a firing was confirmed without the words, or "muffled" when only
    #: the words could confirm it -- empty when the transcript itself decided.
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


#: The small model gone unused this long is transcribed once on silence.
#: After two idle hours the first check of the night took 2,937 ms and the
#: second 983 ms, against 340-620 ms warm (25 September) -- Windows pages
#: out an idle GPU context, and 3 s is the verifier's limit.
IDLE_WARM_SECONDS = 240.0
#: A second of digital silence: nothing to hear, nothing kept.
_SILENCE = bytes(16000 * 2)
# Shared by every socket's verifier (two open tabs share the one model):
# when the model last ran, and how many real checks are running now.
_verifier_state = {
    "last_used": time.monotonic(),
    "running": 0,
    "warm_runs": 0,
    "last_warm_ms": None,
    "last_skip": "",
}
_verifier_state_lock = threading.Lock()


def verifier_status() -> dict:
    """For /v1/speech/health: how long the small model has been idle and
    what the idle warm-up has done (its log line is INFO, which the server's
    log does not keep)."""
    with _verifier_state_lock:
        return {
            "idle_s": round(time.monotonic() - _verifier_state["last_used"], 1),
            "checks_running": _verifier_state["running"],
            "warm_runs": _verifier_state["warm_runs"],
            "last_warm_ms": _verifier_state["last_warm_ms"],
            "last_skip": _verifier_state["last_skip"],
        }


def _model_busy_or_recent(now: float) -> bool:
    with _verifier_state_lock:
        return (
            _verifier_state["running"] > 0
            or now - _verifier_state["last_used"] < IDLE_WARM_SECONDS
        )


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

    def _transcribe(self, pcm: bytes) -> tuple[str, bool]:
        kwargs: dict = {"format": "wav", "language": self._language or None}
        # A backend that takes no prompt (a fake in tests) still works.
        if self._initial_prompt and _accepts(
            self._backend.transcribe, "initial_prompt"
        ):
            kwargs["initial_prompt"] = self._initial_prompt
        result = self._backend.transcribe(pcm_to_wav(normalise_level(pcm)), **kwargs)
        doubt = [
            s.no_speech
            for s in getattr(result, "segments", None) or []
            if getattr(s, "no_speech", None) is not None
        ]
        muffled = bool(doubt) and max(doubt) >= MUFFLED_NO_SPEECH
        return str(getattr(result, "text", "") or "").strip(), muffled

    async def warm_if_idle(self) -> Optional[int]:
        """Run the model once on silence if it has sat unused for
        IDLE_WARM_SECONDS, so the next real check is not the slow one.

        Only when nothing else wants the card or the room: never during a
        real check, a live exchange (the user talking, a reply being made or
        heard) or the server's own voice. Nothing is kept or learnt -- the
        transcript of silence is discarded. Returns the milliseconds it took,
        or None when it did not run.
        """
        if self._backend is None:
            return None
        now = time.monotonic()
        if _model_busy_or_recent(now):
            return None
        try:
            from openjarvis.core import activity
            from openjarvis.speech.player import is_speaking

            snap = activity.snapshot()
            why = (
                "exchange live"
                if snap.exchange_live
                else "reply audible"
                if snap.reply_audible
                else "microphone transmitting"
                if snap.flux_transmitting
                else "server speaking"
                if is_speaking()
                else ""
            )
        except Exception as exc:  # noqa: BLE001 -- unsure of the room: stay out
            why = f"activity unknown: {exc}"
        if why:
            with _verifier_state_lock:
                _verifier_state["last_skip"] = why
            return None
        with _verifier_state_lock:
            if _verifier_state["running"] > 0:
                return None
            _verifier_state["running"] += 1
        started = time.perf_counter()
        try:
            await asyncio.to_thread(self._transcribe, _SILENCE)
        except Exception as exc:  # noqa: BLE001 -- warm-up is optional
            logger.debug("Wake-word verifier idle warm-up failed: %s", exc)
            return None
        finally:
            with _verifier_state_lock:
                _verifier_state["running"] -= 1
                _verifier_state["last_used"] = time.monotonic()
        ms = int((time.perf_counter() - started) * 1000)
        with _verifier_state_lock:
            _verifier_state["warm_runs"] += 1
            _verifier_state["last_warm_ms"] = ms
        logger.info("Wake-word verifier kept warm (%d ms on silence)", ms)
        return ms

    async def verify(self, pcm: bytes, *, strict: bool = False) -> Verdict:
        if self._backend is None:
            return Verdict(True, "", "no speech backend")
        if not pcm:
            return Verdict(True, "", "no audio buffered")
        started = time.perf_counter()
        with _verifier_state_lock:
            _verifier_state["running"] += 1
        try:
            heard, muffled = await asyncio.wait_for(
                asyncio.to_thread(self._transcribe, pcm), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            # Not fail-open like the other paths: a firing that cannot be
            # checked in time is more likely Sage's own voice than the user.
            return Verdict(False, "", f"verifier slower than {self._timeout:g}s")
        except Exception as exc:  # noqa: BLE001 -- fail open, by design
            logger.debug("Wake-word verification failed: %s", exc)
            return Verdict(True, "", f"verifier error: {exc}")
        finally:
            with _verifier_state_lock:
                _verifier_state["running"] -= 1
                _verifier_state["last_used"] = time.monotonic()
        ms = int((time.perf_counter() - started) * 1000)
        verdict = Verdict(
            heard_wake_phrase(heard, strict=strict, muffled=muffled),
            heard,
            "muffled" if muffled else "",
            ms,
            strict,
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
        if key[0] == "parakeet":
            # Reuse the streaming model rather than load a second Whisper.
            # Off by default: it has no phrase prompt, so its spellings of
            # the name lean on heard_wake_phrase's phonetic rules. Measure
            # with scripts/wake_word_verify_compare.py before switching.
            from openjarvis.speech import parakeet
            from openjarvis.speech.parakeet.batch import ParakeetBatchTranscriber

            engine = parakeet.load_engine(
                parakeet.weights.model_dir(
                    getattr(speech, "parakeet_model_dir", "") or ""
                ),
                device=str(getattr(speech, "parakeet_device", "cuda") or "cuda"),
                quant=str(getattr(speech, "parakeet_quant", "") or ""),
            )
            _LOCAL_MODEL = ParakeetBatchTranscriber(engine)
            _LOCAL_MODEL_KEY = key
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
