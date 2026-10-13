"""Listening back to a rendered piece: is it what the text says?

A Whisper round-trip per piece, judged so that the voice's real failures
show and the recogniser's do not. Measured on the 2026-10-13 benchmarks:

* Real failures: a line said twice (Turbo: "It's nice. It's nice."), a line
  that came out near-silent (Nano Frieren dropped two), a garble.
* Not failures: every name ("Seo Gio" -> "C.O.G.O", "Jio"), quote marks,
  "1cm" -> "1 cm", "fibre" -> "fiber". Word-exact scoring flagged 23 of 321
  lotm-narrator pieces for these; character similarity over the whole
  piece, plus a check for a phrase that repeats, does not.
* Also a real failure: a name spelled out letter by letter. Turbo said
  "Seo Gio" as "S-E-O Gio" in 2 of 3 takes (2026-10-14, Kurzgesagt
  narrator); character similarity stays high, so it needs its own check.
"""

from __future__ import annotations

import difflib
import re
import statistics
from typing import List, Optional, Protocol

import numpy as np

#: Seconds of audio per character of text, used until a chapter has its own
#: median (lotm-narrator measured 0.062).
DEFAULT_RATE = 0.062
#: Below this share of the chapter's median rate, a piece came out too short.
TOO_SHORT = 0.45
#: Above this multiple of it, the voice ran on (a repeat the STT missed).
TOO_LONG = 2.6
#: Character similarity of heard vs. said text below which a piece is garbled.
GARBLED = 0.55


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str: ...


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("'", ""))


def _repeated_phrase(said: List[str], heard: List[str]) -> Optional[str]:
    """A run of 1-4 words heard twice in a row that the text does not repeat.

    A voice that repeats itself says more words than the line has; a line
    that repeats on purpose ("Rustle, rustle.") heard as a name ("Russell,
    Russell") does not, and was flagged on 10-13 (chapters 6, 9 and 11).
    """
    said_text = " ".join(said)
    extra = len(heard) - len(said)
    for n in range(4, 0, -1):
        if extra < n:
            continue
        for i in range(len(heard) - 2 * n + 1):
            phrase = heard[i : i + n]
            if phrase == heard[i + n : i + 2 * n]:
                doubled = " ".join(phrase + phrase)
                if doubled not in said_text and (n > 1 or len(phrase[0]) > 3):
                    return " ".join(phrase)
    return None


def _spelled_out(said: List[str], heard: List[str]) -> Optional[str]:
    """A word of the text heard as single letters ("seo" -> "s e o").

    Only letters that join up into (part of) a word the text has: the
    recogniser's own slips on names ("Seo Gio" -> "C.O.G.O") spell nothing
    the text says, and letters the text itself has ("Plan B") are skipped.
    """
    said_text = " ".join(said)
    run: List[str] = []
    for word in heard + [""]:
        if len(word) == 1 and word.isalpha():
            run.append(word)
            continue
        if len(run) >= 2 and " ".join(run) not in said_text:
            joined = "".join(run)

            # "s e o" is part of "seo"; "s e o g o" holds all of it. Two
            # letters count only as a whole word or a word's start: "a i"
            # is not "said" spelled out (review, 10-14).
            def spells(w: str, joined: str = joined) -> bool:
                if len(joined) == 2:
                    return w.startswith(joined)
                return joined in w or w in joined

            if any(spells(w) for w in said if len(w) > 1):
                return joined
        run = []
    return None


def similarity(said: str, heard: str) -> float:
    a, b = " ".join(_words(said)), " ".join(_words(heard))
    if not a:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def judge(said: str, heard: Optional[str], seconds: float, rate: float) -> List[str]:
    """Why a piece needs another take; empty when it is fine. ``heard`` None
    means no transcriber ran: only the length checks apply."""
    reasons: List[str] = []
    chars = max(len(said), 1)
    if seconds / chars < TOO_SHORT * rate:
        reasons.append("too short")
    elif seconds / chars > TOO_LONG * rate and len(said) >= 20:
        reasons.append("too long")
    if heard is None:
        return reasons
    said_words, heard_words = _words(said), _words(heard)
    repeat = _repeated_phrase(said_words, heard_words)
    if repeat:
        reasons.append(f"repeated '{repeat}'")
    elif len(said_words) >= 4 and len(heard_words) > 1.6 * len(said_words) + 2:
        # a longer line said twice has no short phrase repeated back to back
        reasons.append("repeated (too many words)")
    spelled = _spelled_out(said_words, heard_words)
    if spelled:
        reasons.append(f"spelled out '{spelled}'")
    if len(said_words) >= 4 and similarity(said, heard) < GARBLED:
        reasons.append("garbled")
    return reasons


def chapter_rate(pieces: List[tuple]) -> float:
    """Median seconds per character over (text, seconds) pairs."""
    rates = [s / len(t) for t, s in pieces if len(t) >= 20 and s > 0]
    return statistics.median(rates) if len(rates) >= 10 else DEFAULT_RATE


class WhisperTranscriber:
    """faster-whisper on the CPU, a few threads: the GPU is the voice's."""

    def __init__(self, model: str = "base.en", threads: int = 2) -> None:
        self._name = model
        self._threads = threads
        self._model = None

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self._name, device="cpu", compute_type="int8", cpu_threads=self._threads
            )
        if sample_rate != 16000 and len(audio):
            idx = np.linspace(0, len(audio) - 1, int(len(audio) * 16000 / sample_rate))
            audio = audio[idx.astype(int)]
        segments, _ = self._model.transcribe(
            audio.astype(np.float32), language="en", beam_size=1, vad_filter=False
        )
        return " ".join(s.text for s in segments).strip()
