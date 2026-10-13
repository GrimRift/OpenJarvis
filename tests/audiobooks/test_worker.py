"""The render worker with a fake book voice: yields to live Sage, waits for
memory, stops the voice when idle, survives failures."""

from __future__ import annotations

import json
import shutil
import time

import numpy as np
import pytest

from openjarvis.audiobooks import BookStore, import_book
from openjarvis.audiobooks import worker as worker_mod
from openjarvis.audiobooks.queue import RenderQueue, chapter_file
from openjarvis.audiobooks.render import SAMPLE_RATE
from openjarvis.audiobooks.worker import RenderWorker, live_busy
from tests.audiobooks.builders import make_epub

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="ffmpeg not on PATH"
)


class FakeBookVoice:
    def __init__(self, fail_start=False):
        self.up = False
        self.starts = 0
        self.said = []
        self.fail_start = fail_start
        self.last_seconds = None

    def running(self):
        return self.up

    def start(self, voice):
        self.starts += 1
        if self.fail_start:
            raise RuntimeError("paging file too small")
        self.up = True

    def stop(self):
        self.up = False

    def synthesize(self, text, voice):
        self.said.append(text)
        self.last_seconds = 0.0  # as BookVoice: set after every piece
        return np.zeros(int(SAMPLE_RATE * len(text) * 0.062), np.float32) + 0.1


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = BookStore(tmp_path / "books")
    para = "<p>" + "The mill wheel turned in the stream all day. " * 4 + "</p>"
    docs = [("c1.xhtml", para), ("c2.xhtml", para)]
    book = import_book(
        make_epub(
            tmp_path / "b.epub",
            docs,
            [("Chapter 1", "c1.xhtml"), ("Chapter 2", "c2.xhtml")],
        ),
        store,
    )
    monkeypatch.setattr(worker_mod, "voice_exists", lambda v: True)
    return book, store


def _worker(store, voice, busy=lambda: None, resources=lambda v: None, memory=None):
    return RenderWorker(
        store,
        voice,
        busy=busy,
        transcriber=None,
        resources=resources,
        memory=memory or (lambda: None),
        poll=0,
    )


def _status(store):
    return json.loads((store.root / "render-status.json").read_text())


def test_idle_queue_starts_nothing(setup):
    _, store = setup
    voice = FakeBookVoice()
    w = _worker(store, voice)
    w.step()
    assert voice.starts == 0 and _status(store)["state"] == "idle"


def test_waits_for_live_sage_before_starting(setup):
    book, store = setup
    RenderQueue(store).add(book["id"], "range", chapters=[1])
    voice = FakeBookVoice()
    w = _worker(store, voice, busy=lambda: "Sage is speaking")
    w.step()
    assert voice.starts == 0
    assert _status(store)["reason"] == "Sage is speaking"


def test_waits_for_memory_and_retries_later(setup):
    book, store = setup
    RenderQueue(store).add(book["id"], "range", chapters=[1])
    voice = FakeBookVoice()
    w = _worker(store, voice, resources=lambda v: "not enough memory: 2.0 GB")
    w.step()
    assert voice.starts == 0 and _status(store)["state"] == "waiting"
    assert "memory" in _status(store)["reason"]


def test_failed_start_backs_off(setup):
    book, store = setup
    RenderQueue(store).add(book["id"], "range", chapters=[1])
    voice = FakeBookVoice(fail_start=True)
    w = _worker(store, voice)
    w.step()
    w.step()
    assert voice.starts == 1
    assert _status(store)["state"] == "error"


@needs_ffmpeg
def test_renders_queue_then_stops_the_voice_when_idle(setup, monkeypatch):
    book, store = setup
    queue = RenderQueue(store)
    job = queue.add(book["id"], "book")
    queue.set_break(0)
    voice = FakeBookVoice()
    w = _worker(store, voice)
    w.step()
    w.step()
    assert chapter_file(store, book["id"], job["voice"], 1).is_file()
    assert chapter_file(store, book["id"], job["voice"], 2).is_file()
    assert voice.said[0] == "Chapter 1."
    assert len(voice.said) > 2
    w.step()  # nothing left: job closed, voice still warm
    assert queue.load()["jobs"][0]["state"] == "done" and voice.up
    monkeypatch.setattr(worker_mod, "STOP_IDLE_SECONDS", -1)
    w.step()
    assert not voice.up


def test_pause_mid_chapter_stops_between_pieces(setup):
    book, store = setup
    queue = RenderQueue(store)
    queue.add(book["id"], "range", chapters=[1])
    voice = FakeBookVoice()
    calls = {"n": 0}

    def busy():
        calls["n"] += 1
        if calls["n"] == 2:  # while the first piece is let through: the user pauses
            queue.set_paused(True)
        return None

    w = _worker(store, voice, busy=busy)
    w.step()
    assert voice.said == ["Chapter 1."]
    assert _status(store)["state"] == "stopped"


def test_live_busy_reads_the_servers_activity(monkeypatch):
    from openjarvis.core import activity

    activity.reset()
    monkeypatch.setattr("openjarvis.speech.player.is_speaking", lambda: False)
    assert live_busy() is None
    activity.tts_begin()
    assert live_busy() == "Sage is talking or listening"
    activity.tts_end()
    assert live_busy() == "Sage just replied"
    activity.reset()
    activity.note_user_turn()
    assert live_busy() == "a reply is on its way"
    activity.reset()
    monkeypatch.setattr("openjarvis.speech.player.is_speaking", lambda: True)
    assert live_busy() == "Sage is speaking"
    activity.reset()


def test_low_memory_mid_chapter_stops_the_book_voice(setup):
    book, store = setup
    RenderQueue(store).add(book["id"], "range", chapters=[1])
    voice = FakeBookVoice()
    left = iter([None, "memory ran low while rendering (0.8 GB left): stopped"])
    w = _worker(store, voice, memory=lambda: next(left, None))
    w.step()
    assert voice.said == ["Chapter 1."]
    assert not voice.up  # stopped at once, not after the idle wait
    status = _status(store)
    assert status["state"] == "waiting" and "memory ran low" in status["reason"]


@pytest.mark.parametrize("speed, rests", [(1.0, 0), (0.5, 1)])
def test_speed_rests_between_pieces_in_proportion(setup, speed, rests):
    book, store = setup
    queue = RenderQueue(store)
    queue.add(book["id"], "range", chapters=[1])
    queue.set_speed(speed)
    voice = FakeBookVoice()
    w = _worker(store, voice)
    slept = []
    w.rest = slept.append
    w.step()
    spoken = len(voice.said)
    assert spoken >= 2
    # one rest before every piece after the first (none at full speed)
    assert len(slept) == rests * (spoken - 1)
    assert all(s >= 0 for s in slept)


@needs_ffmpeg
def test_chapter_break_unloads_the_voice_and_reloads_it_before_the_end(setup):
    book, store = setup
    queue = RenderQueue(store)
    job = queue.add(book["id"], "range", chapters=[1, 2])
    voice = FakeBookVoice()
    w = _worker(store, voice)
    w.step()  # chapter 1
    said = len(voice.said)
    w.step()  # break (default 5 min): nothing said, the voice's memory freed
    assert len(voice.said) == said and not voice.up
    status = _status(store)
    assert status["state"] == "resting" and "between chapters" in status["reason"]
    # 30 s before the break ends: loaded again, but chapter 2 waits for the end
    w._break_until = time.monotonic() + 30
    w.step()
    assert voice.up and voice.starts == 2 and len(voice.said) == said
    assert not chapter_file(store, book["id"], job["voice"], 2).exists()
    w._break_until = 0  # the break is over
    w.step()
    assert chapter_file(store, book["id"], job["voice"], 2).is_file()
    assert voice.starts == 2
