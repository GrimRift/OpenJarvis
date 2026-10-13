"""Rendering a chapter with a fake voice: resume, retakes, stop, encode."""

from __future__ import annotations

import json
import shutil

import numpy as np
import pytest

from openjarvis.audiobooks import BookStore, import_book
from openjarvis.audiobooks.queue import audio_dir
from openjarvis.audiobooks.render import SAMPLE_RATE, ChapterRenderer, Stopped
from tests.audiobooks.builders import make_epub

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="ffmpeg not on PATH"
)

TEXT = (
    "<p>The painter opened the shutters and looked out over the valley.</p>"
    "<p>\u201c\u2026\u201d</p><hr/>"
    "<p>Morning came slowly, and the mill wheel began to turn again.</p>"
    "<p>It's nice.</p>"
)


class FakeVoice:
    """A tone per piece, 0.062 s per character; dies after N pieces if asked."""

    def __init__(self, fail_after: int | None = None):
        self.calls = []
        self.fail_after = fail_after

    def __call__(self, text: str, voice: str) -> np.ndarray:
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("voice died")
        self.calls.append(text)
        t = np.arange(int(SAMPLE_RATE * len(text) * 0.062)) / SAMPLE_RATE
        return (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


@pytest.fixture
def chapter(tmp_path):
    store = BookStore(tmp_path / "books")
    path = make_epub(
        tmp_path / "b.epub", [("c.xhtml", TEXT)], [("Chapter 1", "c.xhtml")]
    )
    return import_book(path, store), store


def _renderer(store, voice, may_go=lambda: True, ears=None):
    return ChapterRenderer(store, voice, may_go, transcriber=ears)


def test_stop_and_resume_render_each_piece_once(chapter):
    book, store = chapter
    voice = FakeVoice()
    allowed = {"n": 2}

    def may_go():
        allowed["n"] -= 1
        return allowed["n"] >= 0

    with pytest.raises(Stopped):
        _renderer(store, voice, may_go=may_go).render(book["id"], 1, "narrator")
    assert voice.calls == [
        "Chapter 1.",
        "The painter opened the shutters and looked out over the valley.",
    ]
    work = audio_dir(store, book["id"], "narrator") / "work" / "0001"
    assert len(json.loads((work / "manifest.json").read_text())["pieces"]) == 2
    if not shutil.which("ffmpeg"):
        return
    result = _renderer(store, voice).render(book["id"], 1, "narrator")
    assert voice.calls[2:] == [
        "Morning came slowly, and the mill wheel began to turn again. It's nice."
    ]
    assert result.pieces == 3 and result.path.is_file() and not work.exists()


@needs_ffmpeg
def test_chapter_is_encoded_tagged_and_recorded(chapter):
    import subprocess

    book, store = chapter
    result = _renderer(store, FakeVoice()).render(book["id"], 1, "narrator")
    assert result.path.name == "0001.m4a"
    # pieces + title gap + beat + scene + paragraph gaps
    assert 9 < result.seconds < 14
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format_tags=title,album:stream=codec_name,sample_rate",
            "-of",
            "json",
            str(result.path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(probe.stdout)
    assert info["streams"][0]["codec_name"] == "aac"
    assert info["format"]["tags"]["title"] == "Chapter 1"
    assert info["format"]["tags"]["album"] == "Synthetic Book"
    status = json.loads((result.path.parent / "status.json").read_text())
    assert status["1"]["pieces"] == 3 and status["1"]["retakes"] == 0


@needs_ffmpeg
def test_a_repeated_piece_gets_another_take(chapter):
    book, store = chapter
    voice = FakeVoice()

    class Ears:
        def __init__(self):
            self.n = 0

        def transcribe(self, audio, rate):
            # the 3rd piece is heard twice on its first listen only
            self.n += 1
            text = voice.calls[min(self.n, len(voice.calls)) - 1]
            return f"{text} {text}" if self.n == 3 else text

    result = _renderer(store, voice, ears=Ears()).render(book["id"], 1, "narrator")
    assert result.retakes == 1 and result.flags == []
    assert len(voice.calls) == 4


def test_a_dead_voice_raises_and_keeps_finished_pieces(chapter):
    book, store = chapter
    with pytest.raises(RuntimeError):
        _renderer(store, FakeVoice(fail_after=1)).render(book["id"], 1, "narrator")
    work = audio_dir(store, book["id"], "narrator") / "work" / "0001"
    assert (work / "p0000.wav").is_file()
