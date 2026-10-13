"""The render queue: three ways of filling it, chapter specs, closing jobs."""

from __future__ import annotations

import pytest

from openjarvis.audiobooks import BookStore, import_book
from openjarvis.audiobooks.queue import (
    DEFAULT_VOICE,
    RenderQueue,
    chapter_file,
    parse_chapters,
)
from tests.audiobooks.builders import make_epub

BODY = "<p>" + "The mill wheel turned slowly in the stream. " * 8 + "</p>"
LABELS = [
    "Copyright",
    "Chapter 1",
    "Chapter 2",
    "Chapter 3.1",
    "Chapter 3.2",
    "Chapter 4",
]


@pytest.fixture
def book(tmp_path):
    docs = [(f"d{i}.xhtml", BODY) for i in range(len(LABELS))]
    toc = [(label, f"d{i}.xhtml") for i, label in enumerate(LABELS)]
    store = BookStore(tmp_path / "books")
    return import_book(make_epub(tmp_path / "b.epub", docs, toc), store), store


def _render(store, job, index):
    path = chapter_file(store, job["book"], job["voice"], index)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"m4a")


def test_parse_chapters_uses_the_books_numbering(book):
    b, _ = book
    by_label = {c["label"]: c["index"] for c in b["chapters"]}
    assert parse_chapters(b, "2-3") == [
        by_label["Chapter 2"],
        by_label["Chapter 3.1"],
        by_label["Chapter 3.2"],
    ]
    assert parse_chapters(b, "3.2") == [by_label["Chapter 3.2"]]
    assert parse_chapters(b, "4,1") == [by_label["Chapter 4"], by_label["Chapter 1"]]
    for bad in ("9", "3-1", "one"):
        with pytest.raises(ValueError):
            parse_chapters(b, bad)


def test_book_job_skips_front_matter_and_closes_when_done(book):
    b, store = book
    queue = RenderQueue(store)
    job = queue.add(b["id"], "book")
    assert job["voice"] == DEFAULT_VOICE
    assert "Copyright" not in [
        c["label"] for c in b["chapters"] if c["index"] in job["chapters"]
    ]
    seen = []
    while (work := queue.next_work()) is not None:
        seen.append(work[1])
        _render(store, work[0], work[1])
    assert seen == job["chapters"]
    assert queue.load()["jobs"][0]["state"] == "done"


def test_jobs_run_in_order_and_cancel_moves_on(book):
    b, store = book
    queue = RenderQueue(store)
    first = queue.add(b["id"], "range", chapters=[3])
    second = queue.add(b["id"], "range", chapters=[5], voice="jarvis")
    assert queue.next_work()[0]["id"] == first["id"]
    assert queue.cancel(first["id"]) == 1
    job, index = queue.next_work()
    assert (job["id"], index, job["voice"]) == (second["id"], 5, "jarvis")


def test_rolling_job_refills_after_the_position_and_stays_open(book):
    b, store = book
    queue = RenderQueue(store)
    job = queue.add(b["id"], "rolling", ahead=2, position=2)
    assert queue.wanted(job, b) == [3, 4]
    for index in (3, 4):
        _render(store, queue.next_work()[0], index)
    assert queue.next_work() is None
    assert queue.load()["jobs"][0]["state"] == "active"
    queue.set_position(b["id"], 4)
    assert queue.next_work()[1] == 5


def test_rendered_in_another_voice_does_not_count(book):
    b, store = book
    queue = RenderQueue(store)
    _render(store, {"book": b["id"], "voice": "jarvis"}, 2)
    queue.add(b["id"], "range", chapters=[2])
    assert queue.next_work()[1] == 2


def test_pause_and_bad_input(book):
    b, store = book
    queue = RenderQueue(store)
    queue.set_paused(True)
    assert queue.load()["paused"] is True
    with pytest.raises(ValueError):
        queue.add(b["id"], "range")
    with pytest.raises(ValueError):
        queue.add(b["id"], "range", chapters=[99])
    with pytest.raises(ValueError):
        queue.add(b["id"], "book", voice="../x")
    assert not (store.root / "queue.lock").exists()


def test_speed_defaults_to_two_thirds_and_is_bounded(book):
    _, store = book
    queue = RenderQueue(store)
    assert queue.load()["speed"] == pytest.approx(2 / 3)
    assert queue.set_speed(0.75) == 0.75 and queue.load()["speed"] == 0.75
    for bad in (0, 1.5, -1):
        with pytest.raises(ValueError):
            queue.set_speed(bad)


def test_break_between_chapters_defaults_and_bounds(book):
    _, store = book
    queue = RenderQueue(store)
    assert queue.load()["break_minutes"] == 5.0
    assert queue.set_break(0) == 0
    with pytest.raises(ValueError):
        queue.set_break(-1)
