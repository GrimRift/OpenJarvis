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

_PLAYERS = ["ffplay -nodisp -autoexit -loglevel quiet", "aplay", "afplay", "paplay"]


def play_file(audio_path: str, *, duck: bool = True) -> bool:
    """Play *audio_path* to completion. Returns whether a silent player ran.

    Other apps are held at a fraction of their volume for the duration (see
    ``ducking``), so a film does not drown the voice and the voice does not
    have to shout over the film.
    """
    from openjarvis.speech.ducking import ducked

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


__all__ = ["play_file"]
