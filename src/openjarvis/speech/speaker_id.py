"""Who is talking: the user, or Sage's own voice coming back.

While Sage speaks, the microphone hears both. The browser's echo
cancellation removes most of Sage, and the words decide the rest
(frontend/src/lib/barge-in.ts) -- but words cannot tell "the user said what
Sage just said" from "Sage heard itself". A voice fingerprint can.

The fingerprint is the voice encoder already inside the Chatterbox sidecar
(a 256-number summary of how a voice sounds), asked over HTTP. The user's is
the average of their confirmed "Hey Sage" clips, learnt as they happen;
Sage's is the reference recording of whichever voice is selected.

Measured 24 September on 21 of the user's wake clips and 19 two-second
pieces of Sage's voice:

    user clips   vs user 0.63-0.90   vs Sage 0.49-0.54
    Sage pieces  vs user 0.50-0.58   vs Sage 0.83-0.93
    0.8 s pieces vs user 0.56-0.66   vs Sage 0.49-0.57

Two seconds separate cleanly; 0.8 s alone does not. So the encoder always
gets at least the loudest 1.5 s of a turn (`voiced_with_speech`), and a
turn with under half a second of actual speech -- a lone "stop" -- is not
judged by voice; the words still decide those. Padded with 9 s of the
room's own hum, held-out clips still scored 0.87-0.93 against the user and
~0.48 against Sage with 0.36-1.46 s of speech in them.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

PROFILE_FILE = "speaker_profile.json"
SAMPLE_RATE = 16000

#: Below this much speech a fingerprint is a guess (see above).
MIN_SECONDS = 0.5
#: Quieter than this (16-bit RMS) is silence or the zeros sent while the
#: server itself speaks, which has no voice to fingerprint.
MIN_RMS = 40.0
#: How far the two similarities must differ before either is believed. The
#: user's clips beat Sage's voice by 0.10 at worst, Sage beats the user by
#: 0.25 at worst; mixed audio lands in between, and is "unsure".
USER_MARGIN = 0.06
SAGE_MARGIN = 0.12
#: Without Sage's fingerprint (a cloud voice) only a clear match with the
#: user counts; a poor one is not proof of Sage.
USER_ALONE = 0.72
#: The user's fingerprint is the mean of this many newest clips, so it
#: follows a new microphone within a day of use.
MAX_USER_CLIPS = 60
#: Enough clips to trust the average.
MIN_USER_CLIPS = 5

EMBED_TIMEOUT = 1.0

Embedder = Callable[[bytes], Optional[List[float]]]


def verdict(user: float, sage: Optional[float], seconds: float) -> str:
    """One turn's verdict from its similarities: "user", "sage" or "unsure"."""
    if seconds < MIN_SECONDS:
        return "unsure"
    if sage is None:
        return "user" if user >= USER_ALONE else "unsure"
    if user - sage >= USER_MARGIN:
        return "user"
    if sage - user >= SAGE_MARGIN:
        return "sage"
    return "unsure"


def pcm_rms(pcm: bytes) -> float:
    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


#: A wake clip with less speech than this in it teaches nothing.
LEARN_SECONDS = 0.5
FRAME = 320  # 20 ms
#: A frame is speech when this many times louder than the turn's quiet tenth.
NOISE_TIMES = 2.0
#: The encoder always gets at least this much of the turn, loudest first.
KEEP_SECONDS = 1.5


def voiced(pcm: bytes) -> bytes:
    """Only the frames with someone speaking in them.

    A turn's window runs from when it opened to when it closed: "please"
    arrived as 11 s of mostly fan hum, and fingerprinted at 0.47 against
    both voices (24 September). The hum is steady and quiet, speech is not,
    so frames well below the turn's loud ones are dropped.
    """
    return voiced_with_speech(pcm)[0]


def voiced_with_speech(pcm: bytes) -> tuple:
    """`voiced`, and how many seconds of it cleared the room's noise.

    The noise is measured, not assumed: with the browser's noise filter off
    the fan held 1500-2000 RMS against speech peaks of 5000-10000 (24
    September), and a floor taken as a share of the loudest frames kept
    0.4 s of a two-second "Hey Sage". Frames above twice the quiet tenth
    are speech; the loudest 1.5 s are always kept, so the encoder has
    enough to read, while `seconds` counts only what was speech.
    """
    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")
    n = samples.size // FRAME
    if n == 0:
        return b"", 0.0
    frames = samples[: n * FRAME].reshape(n, FRAME)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    floor = max(MIN_RMS, NOISE_TIMES * float(np.percentile(rms, 10)))
    speech = rms >= floor
    keep = speech.copy()
    least = min(n, int(KEEP_SECONDS * SAMPLE_RATE / FRAME))
    if keep.sum() < least:
        keep[np.argsort(rms)[-least:]] = True
    seconds = float(speech.sum()) * FRAME / SAMPLE_RATE
    return frames[keep].tobytes(), seconds


def _unit(vector: Any) -> Optional[np.ndarray]:
    arr = np.asarray(vector, dtype=np.float64).reshape(-1)
    norm = float(np.linalg.norm(arr))
    return arr / norm if arr.size and norm > 0 else None


class SpeakerId:
    """The two fingerprints, and the check of one turn against them."""

    def __init__(
        self,
        data_dir: Path,
        embed: Embedder,
        sage_embedding: Callable[[], Optional[List[float]]],
    ) -> None:
        self._path = Path(data_dir) / PROFILE_FILE
        self._embed = embed
        self._sage_source = sage_embedding
        self._lock = threading.Lock()
        self._clips: Dict[str, List[float]] = {}
        self._load()

    # --------------------------------------------------------- the profile

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        clips = data.get("user_clips") if isinstance(data, dict) else None
        if isinstance(clips, dict):
            self._clips = {
                str(k): [float(x) for x in v]
                for k, v in clips.items()
                if isinstance(v, list) and v
            }

    def _save(self) -> None:
        try:
            self._path.write_text(
                json.dumps({"user_clips": self._clips, "updated": time.time()}),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.debug("Could not save the speaker profile: %s", exc)

    @property
    def user_clips(self) -> int:
        return len(self._clips)

    def _user(self) -> Optional[np.ndarray]:
        if len(self._clips) < MIN_USER_CLIPS:
            return None
        return _unit(np.mean(list(self._clips.values()), axis=0))

    def _sage_vector(self) -> Optional[np.ndarray]:
        # Asked every turn: the source answers from its own cache, keyed by
        # the voice selected now, so a change in Settings counts at once.
        try:
            found = self._sage_source()
        except Exception as exc:  # noqa: BLE001 -- a missing voice is no verdict
            logger.debug("Sage's voice fingerprint unavailable: %s", exc)
            found = None
        return _unit(found) if found else None

    def learn_user(self, pcm: bytes, key: str) -> bool:
        """Add one clip of the user's voice. False when it could not be read."""
        if key in self._clips:
            return False
        pcm, seconds = voiced_with_speech(pcm)
        if seconds < LEARN_SECONDS:
            return False
        vector = self._embed(pcm)
        if not vector:
            return False
        with self._lock:
            self._clips[key] = [round(float(x), 5) for x in vector]
            for old in sorted(self._clips)[:-MAX_USER_CLIPS]:
                del self._clips[old]
            self._save()
        return True

    def seed_from_clips(self, folder: Path) -> int:
        """Learn from wake clips already on disk (the confirmed ones only)."""
        added = 0
        try:
            paths = sorted(Path(folder).glob("*_ok_*.wav"))
        except OSError:
            return 0
        for path in paths[-MAX_USER_CLIPS:]:
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if self.learn_user(data[44:], path.stem):
                added += 1
        return added

    # ----------------------------------------------------------- one turn

    def score(self, pcm: bytes) -> Optional[Dict[str, Any]]:
        """Similarities and verdict for one turn's audio, or None when there
        is nothing to judge with (too short, silent, no profile yet)."""
        pcm, seconds = voiced_with_speech(pcm)
        if seconds < MIN_SECONDS:
            return None
        user = self._user()
        if user is None:
            return None
        vector = _unit(self._embed(pcm) or [])
        if vector is None:
            return None
        user_sim = float(vector @ user)
        sage = self._sage_vector()
        sage_sim = float(vector @ sage) if sage is not None else None
        return {
            "user": round(user_sim, 3),
            "sage": round(sage_sim, 3) if sage_sim is not None else None,
            "seconds": round(seconds, 2),
            "verdict": verdict(user_sim, sage_sim, seconds),
        }


# ------------------------------------------------------------ the sidecar


def _sidecar_embed(base_url: str) -> Embedder:
    def embed(pcm: bytes) -> Optional[List[float]]:
        request = urllib.request.Request(
            f"{base_url}/speaker/embed",
            data=pcm,
            method="POST",
            headers={"Content-Type": "application/octet-stream"},
        )
        try:
            with urllib.request.urlopen(request, timeout=EMBED_TIMEOUT) as response:
                return list(json.loads(response.read())["embedding"])
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            logger.debug("Speaker fingerprint unavailable: %s", exc)
            return None

    return embed


#: After a failed fetch of Sage's fingerprint, wait this long before asking
#: the sidecar again, rather than on every turn while it is down.
VOICE_RETRY = 60.0


def _sidecar_voice(
    base_url: str, speech_cfg: Any
) -> Callable[[], Optional[List[float]]]:
    """Sage's fingerprint for the voice selected now (voice_choice.json),
    fetched once per voice."""
    cache: Dict[str, List[float]] = {}
    failed: Dict[str, float] = {}

    def fetch() -> Optional[List[float]]:
        from openjarvis.speech.voice_choice import chosen_voice_id

        voice = chosen_voice_id(speech_cfg)
        if not voice.startswith("chatterbox:"):
            return None
        name = voice.split(":", 1)[1]
        if name in cache:
            return cache[name]
        if time.monotonic() - failed.get(name, -VOICE_RETRY) < VOICE_RETRY:
            return None
        url = f"{base_url}/speaker/voice/{urllib.request.quote(name)}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                cache[name] = list(json.loads(response.read())["embedding"])
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            failed[name] = time.monotonic()
            logger.debug("No fingerprint for voice %s: %s", name, exc)
            return None
        return cache[name]

    return fetch


_INSTANCE: Optional[SpeakerId] = None
_INSTANCE_LOCK = threading.Lock()


def get(config: Any) -> Optional[SpeakerId]:
    """The process's one SpeakerId, seeded from kept wake clips on first use;
    None when voice recognition is switched off."""
    global _INSTANCE
    speech_cfg = getattr(config, "speech", None)
    if not getattr(speech_cfg, "speaker_id_enabled", True):
        return None
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                from openjarvis.core.paths import get_data_dir
                from openjarvis.speech.chatterbox_sidecar import base_url

                url = base_url(speech_cfg)
                instance = SpeakerId(
                    get_data_dir(), _sidecar_embed(url), _sidecar_voice(url, speech_cfg)
                )
                _INSTANCE = instance
    _maybe_seed(_INSTANCE)
    return _INSTANCE


#: Seconds between attempts to learn from the kept clips. Tried again, not
#: once: the first use comes as the page reconnects after a restart, before
#: the sidecar has loaded, and a one-shot seed learnt nothing (24 September).
SEED_RETRY = 60.0
_last_seed = 0.0


def _maybe_seed(instance: SpeakerId) -> None:
    global _last_seed
    keep = os.environ.get("OPENJARVIS_WAKE_WORD_KEEP_CLIPS", "")
    now = time.monotonic()
    if not keep or instance.user_clips >= MIN_USER_CLIPS:
        return
    if _last_seed and now - _last_seed < SEED_RETRY:
        return
    _last_seed = now

    def seed() -> None:
        added = instance.seed_from_clips(Path(keep))
        logger.info(
            "Voice fingerprint: learnt %d kept wake clips (%d in profile)",
            added,
            instance.user_clips,
        )

    threading.Thread(target=seed, name="speaker-id-seed", daemon=True).start()


def learn_in_background(
    config: Any, pcm: bytes, *, unless: Optional[Callable[[], bool]] = None
) -> None:
    """A confirmed "Hey Sage" is the user's voice: learn it without making
    the wake word wait for the fingerprint. ``unless`` is asked first, on
    the learning thread: another app playing mixes into the clip."""
    instance = get(config)
    if instance is None:
        return
    key = time.strftime("%Y%m%d_%H%M%S") + f"_live_{int(time.time() * 1000) % 1000:03d}"

    def learn() -> None:
        if unless is not None:
            try:
                if unless():
                    return
            except Exception:  # noqa: BLE001 -- a failed lookup is not a veto
                pass
        instance.learn_user(pcm, key)

    threading.Thread(
        target=learn,
        name="speaker-id-learn",
        daemon=True,
    ).start()


class AudioTimeline:
    """The audio a transcription session has been sent, addressable by the
    session's own clock: Deepgram's turn windows are seconds of audio
    received, and idle gaps between turns are never sent, so a byte count
    is that clock."""

    def __init__(self, keep_seconds: float = 30.0) -> None:
        self._buffer = bytearray()
        self._start = 0  # byte offset of _buffer[0] on the session clock
        self._keep = int(keep_seconds * SAMPLE_RATE * 2)

    def append(self, data: bytes) -> None:
        self._buffer.extend(data)
        excess = len(self._buffer) - self._keep
        if excess > 0:
            excess -= excess % 2
            del self._buffer[:excess]
            self._start += excess

    def slice(self, start_s: float, end_s: float) -> bytes:
        if end_s <= start_s:
            return b""
        lo = int(start_s * SAMPLE_RATE) * 2 - self._start
        hi = int(end_s * SAMPLE_RATE) * 2 - self._start
        lo = max(0, lo)
        hi = min(len(self._buffer), hi)
        return bytes(self._buffer[lo:hi]) if hi > lo else b""
