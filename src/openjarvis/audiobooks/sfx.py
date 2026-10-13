"""Sound effects under sound words ("Rustle...", "Clink.", "SPLASH!!").

The user's choice (2026-10-14): the narrator still says the word, and a real
sound plays softly under it. Only a paragraph made of nothing but sound
words gets one -- that is how the reference book writes them -- so "the
leaves rustled" in a sentence stays plain narration.

Sounds: Kenney's CC0 "RPG Audio" and "Impact Sounds" packs (downloaded with
the user's OK to ``<data>/sfx/kenney/{rpg,impact}``); swish/whoosh and water
sounds are not in them and are made here from filtered noise. A missing
pack only means fewer kinds of sound.
"""

from __future__ import annotations

import functools
import logging
import random
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from openjarvis.core.paths import get_data_dir

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
#: How loud an effect is next to the narration it plays under (RMS ratio):
#: about -9 dB, so it sits under the voice instead of over it.
UNDER_VOICE = 0.35
#: Longest effect kept; longer ones fade out.
MAX_SECONDS = 1.4

#: Sound word -> kind of sound. Elongated spellings ("swiiiish") are folded
#: before lookup.
WORDS: Dict[str, str] = {
    **dict.fromkeys(
        ["rustle", "rustling", "rustles", "shuffle", "flutter", "flap"], "rustle"
    ),
    **dict.fromkeys(
        ["swish", "swoosh", "whoosh", "woosh", "fwoosh", "whiz", "whizz", "swoop"],
        "whoosh",
    ),
    **dict.fromkeys(["clink", "ting", "tink", "chink"], "clink"),
    **dict.fromkeys(["clang", "clank", "gong", "dong", "ding"], "clang"),
    **dict.fromkeys(["clatter", "rattle"], "clatter"),
    **dict.fromkeys(["crunch", "chomp", "munch", "crumble"], "crunch"),
    **dict.fromkeys(["crack", "snap", "crackle"], "crack"),
    **dict.fromkeys(["thud", "thump", "bump", "flop", "plop"], "thud"),
    **dict.fromkeys(["bang", "boom", "bam", "wham", "slam", "smash", "crash"], "bang"),
    **dict.fromkeys(["knock", "tap", "tok", "tuk", "tock", "tick"], "knock"),
    **dict.fromkeys(["click", "clack", "clic"], "click"),
    **dict.fromkeys(["creak", "squeak", "creeak"], "creak"),
    **dict.fromkeys(["splash", "splish", "slosh"], "splash"),
    **dict.fromkeys(["drip", "drop", "plink", "trickle"], "drip"),
    **dict.fromkeys(["jingle", "jangle"], "coins"),
    **dict.fromkeys(["step", "stomp", "thudthud"], "step"),
}

#: Kind -> pack files (glob under <data>/sfx/kenney) or "gen:<name>".
SOURCES: Dict[str, List[str]] = {
    "rustle": ["rpg/cloth*.ogg", "rpg/bookFlip*.ogg"],
    "whoosh": ["gen:whoosh"],
    "clink": ["impact/impactGlass_light_*.ogg"],
    "clang": ["impact/impactMetal_medium_*.ogg", "impact/impactBell_heavy_*.ogg"],
    "clatter": ["impact/impactPlate_light_*.ogg", "impact/impactPlate_medium_*.ogg"],
    "crunch": ["impact/impactWood_light_*.ogg", "rpg/chop.ogg"],
    "crack": ["impact/impactWood_heavy_*.ogg"],
    "thud": ["impact/impactSoft_heavy_*.ogg", "impact/impactSoft_medium_*.ogg"],
    "bang": ["impact/impactPunch_heavy_*.ogg"],
    "knock": ["impact/impactWood_medium_*.ogg", "impact/impactPlank_medium_*.ogg"],
    "click": ["rpg/metalClick.ogg", "rpg/metalLatch.ogg"],
    "creak": ["rpg/creak*.ogg"],
    "splash": ["gen:splash"],
    "drip": ["gen:drip"],
    "coins": ["rpg/handleCoins*.ogg"],
    "step": ["rpg/footstep0*.ogg"],
}

_WORD = re.compile(r"[A-Za-z]+")


def fold(word: str) -> str:
    """'Swiiiish' -> 'swish', 'SPLASH' -> 'splash', 'Creeeak' -> 'creak'."""
    w = word.lower()
    if w in WORDS:
        return w
    for stretched in (
        r"(.)\1{2,}",
        r"(.)\1+",
    ):  # "swoooosh" -> "swoosh", "swiiish" -> "swish"
        for keep in (r"\1\1", r"\1"):
            folded = re.sub(stretched, keep, w)
            if folded in WORDS:
                return folded
    return w


def sound_words(paragraph: str) -> Optional[List[Tuple[str, int]]]:
    """[(kind, character offset)] when the paragraph is only sound words
    (with punctuation), else None."""
    words = list(_WORD.finditer(paragraph))
    if not words or len(words) > 8:
        return None
    found = []
    for m in words:
        kind = WORDS.get(fold(m.group(0)))
        if kind is None:
            return None
        found.append((kind, m.start()))
    return found


# ------------------------------------------------------------------ sounds


def library_dir() -> Path:
    return Path(get_data_dir()) / "sfx" / "kenney"


def _decode(path: Path) -> Optional[np.ndarray]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    out = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if out.returncode != 0 or not out.stdout:
        return None
    return np.frombuffer(out.stdout, dtype="<f4").copy()


def _noise(seconds: float, rng: np.random.Generator) -> np.ndarray:
    return rng.standard_normal(int(SAMPLE_RATE * seconds)).astype(np.float32)


def _one_pole(x: np.ndarray, cutoff: np.ndarray | float) -> np.ndarray:
    """Low-pass with a (possibly moving) cutoff in Hz."""
    a = np.exp(-2 * np.pi * np.broadcast_to(cutoff, x.shape) / SAMPLE_RATE)
    y = np.empty_like(x)
    prev = 0.0
    for i in range(len(x)):
        prev = (1 - a[i]) * x[i] + a[i] * prev
        y[i] = prev
    return y


def _generated(name: str, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if name == "whoosh":
        n = _noise(0.55, rng)
        t = np.linspace(0, 1, len(n))
        cutoff = 300 + 2600 * np.sin(np.pi * t) ** 2  # opens and closes: a pass-by
        band = _one_pole(n, cutoff) - _one_pole(n, cutoff * 0.25)
        return (band * np.sin(np.pi * t) ** 1.5).astype(np.float32)
    if name == "splash":
        n = _noise(0.9, rng)
        t = np.linspace(0, 1, len(n))
        body = _one_pole(n, 1800) * np.exp(-5 * t) * (1 - np.exp(-60 * t))
        spray = (n - _one_pole(n, 3000)) * 0.4 * np.exp(-7 * t)
        return (body + spray).astype(np.float32)
    if name == "drip":
        t = np.arange(int(SAMPLE_RATE * 0.18)) / SAMPLE_RATE
        freq = 900 * np.exp(-t * 9) + 500  # falling "plip"
        phase = 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE
        return (np.sin(phase) * np.exp(-t * 28)).astype(np.float32)
    raise KeyError(name)


@functools.lru_cache(maxsize=None)
def _variants(kind: str) -> Tuple[np.ndarray, ...]:
    out: List[np.ndarray] = []
    root = library_dir()
    for source in SOURCES.get(kind, []):
        if source.startswith("gen:"):
            out += [_generated(source[4:], seed) for seed in range(3)]
            continue
        for path in sorted(root.glob(source)):
            audio = _decode(path)
            if audio is not None and len(audio):
                out.append(audio)
    return tuple(_shape(a) for a in out)


def _shape(audio: np.ndarray) -> np.ndarray:
    """Trim silence at the start, cap the length with a fade, unit RMS."""
    loud = np.flatnonzero(np.abs(audio) > 0.02 * (np.abs(audio).max() or 1))
    if len(loud):
        audio = audio[loud[0] :]
    cap = int(SAMPLE_RATE * MAX_SECONDS)
    if len(audio) > cap:
        audio = audio[:cap].copy()
        fade = int(SAMPLE_RATE * 0.25)
        audio[-fade:] *= np.linspace(1, 0, fade)
    rms = float(np.sqrt(np.mean(audio**2))) or 1.0
    return (audio / rms).astype(np.float32)


def sound(kind: str, pick: int) -> Optional[np.ndarray]:
    """One take of *kind* (unit RMS); ``pick`` varies it between uses."""
    takes = _variants(kind)
    return takes[pick % len(takes)] if takes else None


def mix_under(
    audio: np.ndarray,
    cues: List[Tuple[str, float]],
    voice_rms: float,
    chooser: Callable[[str], int] | None = None,
) -> int:
    """Add each (kind, second) cue into *audio* in place, scaled to sit under
    a voice of RMS ``voice_rms``. Returns how many were placed."""
    rng = random.Random(len(audio))
    chooser = chooser or (lambda kind: rng.randrange(1 << 16))
    placed = 0
    for kind, at in cues:
        clip = sound(kind, chooser(kind))
        if clip is None:
            continue
        start = max(0, int(at * SAMPLE_RATE))
        end = min(len(audio), start + len(clip))
        if end <= start:
            continue
        audio[start:end] += clip[: end - start] * (voice_rms * UNDER_VOICE)
        placed += 1
    return placed
