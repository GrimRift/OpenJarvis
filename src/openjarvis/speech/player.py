"""Play an audio file through the machine's speakers, from the server.

Sage's voice normally reaches the speakers through the browser tab, which
synthesises and plays. Unprompted speech (M36) cannot depend on a tab being
open, so the server needs a player of its own. ``ffplay`` plays with no
window; the fallbacks are the platform players, and on Windows the OS file
association, which always works but opens a visible player.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time

_PLAYERS = ["ffplay -nodisp -autoexit -loglevel quiet", "aplay", "afplay", "paplay"]

#: One voice at a time, server-wide. The moments engine (greeting, welcome
#: back, initiative) and the desktop reminder each had a player of their
#: own, and on 17 September the morning greeting and the class reminder for
#: the same 9:40 class both fired at 9:25 and were heard over each other.
#: Everything that speaks from the server takes this lock for as long as
#: the sound plays; the second voice waits its turn.
SPEAKING = threading.RLock()

#: Echo of the server's own voice reaches the microphone for a moment after
#: the player exits; the listeners stay deaf this long past the end.
ECHO_TAIL_SECONDS = 1.0
_state_lock = threading.Lock()
_speakers = 0
_last_spoke_at = 0.0


#: How long a voice waits for the user's open turn to end before speaking
#: anyway. A reminder is time-sensitive; a turn is at most the listening
#: window plus the sentence being said.
FLOOR_WAIT_SECONDS = 20.0


def _wait_for_the_floor(timeout: float) -> None:
    """Do not talk over the user. While the browser is transmitting a turn
    (after a wake word, in a reply window, for a follow-up) the microphone
    would go deaf for the voice and lose what was being said; wait for the
    turn to close, up to ``timeout``."""
    if timeout <= 0:
        return
    from openjarvis.core import activity

    deadline = time.monotonic() + timeout
    while activity.snapshot().flux_transmitting and time.monotonic() < deadline:
        time.sleep(0.1)


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
        _wait_for_the_floor(self._wait_for_turn)
        SPEAKING.acquire()
        with _state_lock:
            _speakers += 1
        return self

    def __exit__(self, *exc: object) -> None:
        global _speakers, _last_spoke_at
        with _state_lock:
            _speakers -= 1
            _last_spoke_at = time.monotonic()
        SPEAKING.release()


def is_speaking(now: float | None = None) -> bool:
    """Whether the server is speaking, or just stopped (echo tail)."""
    with _state_lock:
        if _speakers > 0:
            return True
        current = time.monotonic() if now is None else now
        return current - _last_spoke_at < ECHO_TAIL_SECONDS


def play_file(audio_path: str, *, duck: bool = True) -> bool:
    """Play *audio_path* to completion. Returns whether a silent player ran.

    Other apps are held at a fraction of their volume for the duration (see
    ``ducking``), so a film does not drown the voice and the voice does not
    have to shout over the film.
    """
    from openjarvis.speech.ducking import ducked

    with speaking():
        if not duck:
            return _play(audio_path)
        with ducked():
            return _play(audio_path)


def _play(audio_path: str) -> bool:
    for player in _PLAYERS:
        cmd_parts = player.split() + [audio_path]
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
    "ECHO_TAIL_SECONDS",
    "FLOOR_WAIT_SECONDS",
    "SPEAKING",
    "is_speaking",
    "play_file",
    "speaking",
]
