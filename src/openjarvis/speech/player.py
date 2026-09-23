"""Play an audio file through the machine's speakers, from the server.

Sage's voice normally reaches the speakers through the browser tab, which
synthesises and plays. Unprompted speech (M36) cannot depend on a tab being
open, so the server needs a player of its own. ``ffplay`` plays with no
window; the fallbacks are the platform players, and on Windows the OS file
association, which always works but opens a visible player.
"""

from __future__ import annotations

import array
import itertools
import math
import shutil
import subprocess
import sys
import threading
import time
import wave
from typing import Any, Dict, List, Optional

_PLAYERS = ["ffplay -nodisp -autoexit -loglevel quiet", "aplay", "afplay", "paplay"]

#: One voice at a time, server-wide. The moments engine (greeting, welcome
#: back, initiative) and the desktop reminder each had a player of their
#: own, and on 17 September the morning greeting and the class reminder for
#: the same 9:40 class both fired at 9:25 and were heard over each other.
#: Everything that speaks from the server takes this lock for as long as
#: the sound plays; the second voice waits its turn.
SPEAKING = threading.RLock()

#: Echo of the server's own voice reaches the microphone for a moment after
#: the player exits, and the wake-word detector scores a 1.28 s rolling
#: window that still holds the voice after that; the listeners stay deaf
#: this long past the end. At 1.0 s a class reminder fired the wake word
#: on its own last words.
ECHO_TAIL_SECONDS = 2.0
_state_lock = threading.Lock()
_speakers = 0
_last_spoke_at = 0.0


#: How long a voice waits for the exchange in progress -- the user's open
#: turn, or a reply being read aloud -- before speaking anyway. A reminder
#: is time-sensitive; a long reply can run half a minute.
FLOOR_WAIT_SECONDS = 45.0
#: Past that, a voice still waits for a reply being heard to end, this much
#: longer. An open microphone can be talked into; a reply cannot be talked
#: over. On 23 September a reminder waited its 45 s through a run of back-
#: and-forth and then spoke over the next answer, and was not heard.
REPLY_WAIT_SECONDS = 90.0


def _wait_for_the_floor(timeout: float) -> None:
    """Do not talk over the exchange. While the browser is transmitting a
    turn (after a wake word, in a reply window, for a follow-up) the
    microphone would go deaf for the voice and lose what was being said;
    while it is reading a reply aloud, two voices would play at once -- an
    initiative line was heard over a chat answer on 17 September. Wait for
    both to end, up to ``timeout``."""
    if timeout <= 0:
        return
    from openjarvis.core import activity

    deadline = time.monotonic() + timeout
    while activity.snapshot().sage_mid_turn and time.monotonic() < deadline:
        time.sleep(0.1)
    deadline = time.monotonic() + REPLY_WAIT_SECONDS
    while activity.snapshot().reply_audible and time.monotonic() < deadline:
        time.sleep(0.1)


#: How many times this thread holds the floor (SPEAKING is re-entrant).
_holding = threading.local()


class speaking:
    """Hold the floor: the lock, and the fact of it for the listeners.

    The server's own voice is the one sound the microphone must never take
    for the user. On 17 September a class reminder played into the reply
    window a greeting had just opened, and "online in ten minutes" came
    back as the user's answer; the wake word fired on it too. While this is
    held -- and for ``ECHO_TAIL_SECONDS`` after -- the wake-word socket
    suppresses detections and the Flux relay forwards silence.
    """

    def __init__(self, wait_for_turn: float = FLOOR_WAIT_SECONDS) -> None:
        self._wait_for_turn = wait_for_turn

    def __enter__(self) -> "speaking":
        global _speakers
        # Already holding the floor on this thread (the chime inside a
        # spoken line's hold): do not wait for it again. Waiting there let
        # a turn the user began after the chime hold the words back.
        if getattr(_holding, "n", 0) == 0:
            _wait_for_the_floor(self._wait_for_turn)
        SPEAKING.acquire()
        _holding.n = getattr(_holding, "n", 0) + 1
        with _state_lock:
            _speakers += 1
        return self

    def __exit__(self, *exc: object) -> None:
        global _speakers, _last_spoke_at
        with _state_lock:
            _speakers -= 1
            _last_spoke_at = time.monotonic()
        _holding.n -= 1
        SPEAKING.release()


def is_speaking(now: float | None = None) -> bool:
    """Whether the server is speaking, or just stopped (echo tail)."""
    with _state_lock:
        if _speakers > 0:
            return True
        current = time.monotonic() if now is None else now
        return current - _last_spoke_at < ECHO_TAIL_SECONDS


# -- What the server is saying, for the orb --------------------------------
#
# Reminders, schedule notices and moments are spoken here, not in the
# browser, so the page had no idea Sage was talking: the orb sat in standing
# by through every one of them. The page follows this instead. It is kept
# apart from the speaking lock on purpose -- the lock is about the floor and
# the microphone; this is only about what the orb shows.

#: Width of one envelope step. The orb draws at 60 a second; 20 ms is finer
#: than a syllable and coarse enough that a minute of speech is 3,000 numbers.
ENVELOPE_STEP_MS = 20

#: Loudness to orb level: level = RMS x this, the same number the page
#: scales its own voice by (SPEECH_LEVEL_SCALE in frontend audio-level.ts),
#: so a reminder moves the orb as a chat reply at the same loudness does. An
#: architecture test holds the two equal. It used to scale each clip against
#: its own loud end instead; measured on real lines, that left reminders at
#: a median level of 0.50 while chat replies sat at 0.83.
SPEECH_LEVEL_SCALE = 3.5

#: Not speech: a chime playing is not Sage talking.
_SILENT_CHANNELS = frozenset({"chime"})

_voice_lock = threading.Lock()
_voice: Optional[Dict[str, Any]] = None
_voice_ids = itertools.count(1)


def voice_envelope(audio_path: str) -> Optional[List[float]]:
    """Loudness of *audio_path* every ``ENVELOPE_STEP_MS``, 0..1, or None,
    on the page's scale (``SPEECH_LEVEL_SCALE``)."""
    decoded = _decode_mono(audio_path)
    if decoded is None:
        return None
    samples, rate = decoded
    step = max(1, int(rate * ENVELOPE_STEP_MS / 1000))
    rms: List[float] = []
    for start in range(0, len(samples), step):
        chunk = samples[start : start + step]
        if not chunk:
            break
        rms.append(math.sqrt(sum(v * v for v in chunk) / len(chunk)))
    return [round(min(1.0, v * SPEECH_LEVEL_SCALE), 3) for v in rms]


def _decode_mono(audio_path: str) -> Optional[tuple]:
    """Mono samples in -1..1 and their rate. PCM wav directly; anything
    else -- float wav, mp3 -- through ffmpeg, which ships beside ffplay."""
    try:
        with wave.open(audio_path, "rb") as handle:
            width = handle.getsampwidth()
            channels = handle.getnchannels()
            rate = handle.getframerate()
            raw = handle.readframes(handle.getnframes())
        if width == 2:
            values = array.array("h")
            values.frombytes(raw)
            if sys.byteorder != "little":
                values.byteswap()
            mono = values[::channels] if channels > 1 else values
            return [v / 32768.0 for v in mono], rate
    except (wave.Error, EOFError, OSError):
        pass
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    rate = 8000
    try:
        out = subprocess.run(
            [ffmpeg, "-v", "quiet", "-i", audio_path, "-f", "s16le"]
            + ["-ac", "1", "-ar", str(rate), "-"],
            capture_output=True,
            timeout=15,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    values = array.array("h")
    values.frombytes(out[: len(out) - len(out) % 2])
    if sys.byteorder != "little":
        values.byteswap()
    return [v / 32768.0 for v in values], rate


class voice:
    """Mark the server as saying something, for as long as it plays."""

    def __init__(self, channel: str, envelope: Optional[List[float]] = None) -> None:
        self._channel = channel
        self._envelope = envelope
        self._id: Optional[int] = None

    def __enter__(self) -> "voice":
        global _voice
        if self._channel in _SILENT_CHANNELS:
            return self
        with _voice_lock:
            self._id = next(_voice_ids)
            _voice = {
                "id": self._id,
                "channel": self._channel,
                "started": time.monotonic(),
                "envelope": self._envelope,
            }
        return self

    def __exit__(self, *exc: object) -> None:
        global _voice
        with _voice_lock:
            if _voice is not None and _voice["id"] == self._id:
                _voice = None


def current_voice(now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """What the server is saying right now, or None.

    ``elapsed_ms`` is how far into it the voice is, so a page that tunes in
    part-way lines its envelope up with the sound rather than restarting it.
    """
    with _voice_lock:
        if _voice is None:
            return None
        current = time.monotonic() if now is None else now
        return {
            "speaking": True,
            "id": _voice["id"],
            "channel": _voice["channel"],
            "elapsed_ms": round((current - _voice["started"]) * 1000),
            "step_ms": ENVELOPE_STEP_MS,
            "envelope": _voice["envelope"],
        }


def play_file(audio_path: str, *, duck: bool = True, channel: str = "moments") -> bool:
    """Play *audio_path* to completion. Returns whether a silent player ran.

    Other apps are held at a fraction of their volume for the duration (see
    ``ducking``), so a film does not drown the voice and the voice does not
    have to shout over the film. ``channel`` picks the user's volume for
    this kind of sound (``speech.volume``).
    """
    from openjarvis.speech.ducking import ducked
    from openjarvis.speech.volume import gain

    volume = gain(channel)
    # Measured before taking the floor, so the voice is not held up by it
    # once it has the floor.
    envelope = None if channel in _SILENT_CHANNELS else voice_envelope(audio_path)
    with speaking():
        with voice(channel, envelope):
            if not duck:
                return _play(audio_path, volume)
            with ducked():
                return _play(audio_path, volume)


def _volume_filter(volume: float) -> str:
    """ffplay's ``-volume`` stops at 100, so the gain goes through the
    volume filter, and a lookahead limiter keeps a boosted loud file from
    clipping."""
    return f"volume={max(0.0, volume):.3f},alimiter=limit=0.97:level=false"


def _play(audio_path: str, volume: float = 1.0) -> bool:
    for player in _PLAYERS:
        cmd_parts = player.split()
        if cmd_parts[0] == "ffplay":
            cmd_parts += ["-af", _volume_filter(volume)]
        cmd_parts.append(audio_path)
        try:
            subprocess.run(
                cmd_parts,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue

    if sys.platform == "win32":
        try:
            import os

            os.startfile(audio_path)  # noqa: S606
        except OSError:
            pass
    return False


__all__ = [
    "ENVELOPE_STEP_MS",
    "ECHO_TAIL_SECONDS",
    "FLOOR_WAIT_SECONDS",
    "SPEAKING",
    "current_voice",
    "is_speaking",
    "play_file",
    "speaking",
    "voice",
    "voice_envelope",
]
