"""The chime before Sage speaks unprompted (M36 phase 4).

A voice out of a silent room startles; a soft two-note tone a quarter
second ahead gives the listener a beat to register "that's Sage" before the
sentence starts. Synthesised here rather than shipped as a file: a few
hundred samples of sine are simpler to keep than package data, and the
sound is described by four numbers anyone can retune.
"""

from __future__ import annotations

import math
import struct
import tempfile
import wave
from pathlib import Path

RATE = 44100
# A rising fourth, E5 to A5: soft, glassy, and over before the voice.
NOTES = ((659.25, 0.16), (880.0, 0.22))
AMPLITUDE = 0.35
# Silence between the chime's end and the first word.
GAP_SECONDS = 0.25


def _tone(freq: float, dur: float, attack: float = 0.01, release: float = 0.18):
    count = int(RATE * dur)
    for i in range(count):
        t = i / RATE
        env = min(1.0, t / attack)
        if t >= dur - release:
            env *= max(0.0, (dur - t) / release)
        # A faint octave over the fundamental reads as glass, not a buzzer.
        value = math.sin(2 * math.pi * freq * t) * 0.85
        value += math.sin(2 * math.pi * freq * 2 * t) * 0.15
        yield value * env * AMPLITUDE


def chime_path() -> Path:
    """The chime as a wav, written once per machine and reused."""
    path = Path(tempfile.gettempdir()) / "sage-chime.wav"
    if path.exists() and path.stat().st_size > 1000:
        return path
    frames = bytearray()
    for freq, dur in NOTES:
        for sample in _tone(freq, dur):
            frames += struct.pack("<h", int(max(-1.0, min(1.0, sample)) * 32767))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(bytes(frames))
    return path


__all__ = ["GAP_SECONDS", "chime_path"]
