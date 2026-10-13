"""A note on "Redo chapter" (user, 2026-10-14) -> settings the render uses."""

from __future__ import annotations

import json
import shutil

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.audiobooks import BookStore, import_book
from openjarvis.audiobooks.library import Library
from openjarvis.audiobooks.queue import chapter_file
from openjarvis.audiobooks.redo_notes import clean, interpret, respell
from openjarvis.server.book_routes import create_book_router
from tests.audiobooks.builders import make_epub

BODY = (
    "<p>" + "Seo Gio turned the mill wheel slowly in the stream all day. " * 8 + "</p>"
)

REPLY = json.dumps(
    {
        "pronunciations": [{"word": "Gio", "say": "Jee-oh", "book_wide": True}],
        "pause_scale": 1.3,
        "tempo": 0.5,  # out of range: clamped
        "sfx": "off",
        "sfx_level": None,
        "understood": ["Gio said as Jee-oh.", "Longer pauses.", "Slower."],
        "not_possible": ["More emotion: the voice has no control for that."],
        "evil": "ignored",
    }
)


def test_the_model_reply_becomes_checked_settings():
    got = interpret(
        "say Gio as Jee-oh, slower, longer pauses, no sounds, more emotion",
        "Chapter 1",
        [],
        lambda msgs: f"Sure!\n```json\n{REPLY}\n```",
    )
    assert got["pronunciations"] == [
        {"word": "Gio", "say": "Jee-oh", "book_wide": True}
    ]
    assert got["pause_scale"] == 1.3 and got["tempo"] == 0.85 and got["sfx"] == "off"
    assert "sfx_level" not in got and "evil" not in got
    assert got["not_possible"] and got["note"].startswith("say Gio")


def test_a_model_that_fails_keeps_the_note_and_still_redoes():
    def down(msgs):
        raise RuntimeError("no model is loaded")

    got = interpret("slower please", "Chapter 1", [], down)
    assert got["note"] == "slower please" and "tempo" not in got
    assert "couldn't read the note" in got["not_possible"][0]


def test_numbers_at_one_mean_no_change():
    assert "pause_scale" not in clean({"pause_scale": 1.0})


def test_respelling_keeps_whole_words_and_capitals():
    sayings = [{"word": "Gio", "say": "jee-oh"}]
    assert respell("Gio saw Giovanni. gio!", sayings) == "Jee-oh saw Giovanni. jee-oh!"


@pytest.fixture
def lib(tmp_path):
    store = BookStore(tmp_path / "books")
    docs = [("d0.xhtml", BODY), ("d1.xhtml", BODY)]
    import_book(
        make_epub(
            tmp_path / "b.epub",
            docs,
            [("Chapter 1", "d0.xhtml"), ("Chapter 2", "d1.xhtml")],
        ),
        store,
    )
    return Library(store)


def _rendered(lib, index):
    book = lib.find("")
    path = chapter_file(lib.store, book["id"], "lotm-narrator", index)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"m4a")
    (path.parent / "status.json").write_text(
        json.dumps({str(index): {"file": path.name, "seconds": 60.0}})
    )
    return book


def test_redo_with_a_note_keeps_settings_on_the_chapter_and_the_book(lib):
    book = _rendered(lib, 1)
    settings = interpret("note", "Chapter 1", [], lambda m: REPLY)
    settings["pronunciations"].append(
        {"word": "mill", "say": "mil", "book_wide": False}
    )
    lib.redo_chapter(book["id"], 1, settings)
    after = lib.store.load(book["id"])
    assert after["pronunciations"] == [{"word": "Gio", "say": "Jee-oh"}]
    redo = after["chapters"][0]["redo"]
    assert redo["pause_scale"] == 1.3 and redo["pronunciations"] == [
        {"word": "mill", "say": "mil"}
    ]
    detail = lib.detail(book["id"])["chapters"][0]
    assert detail["redo"]["note"] == "note" and detail["state"] == "queued"
    # a redo without a note keeps the earlier settings
    _rendered(lib, 1)
    lib.redo_chapter(book["id"], 1)
    assert lib.store.load(book["id"])["chapters"][0]["redo"]["pause_scale"] == 1.3


def test_the_route_reads_the_note_with_the_servers_model(lib):
    class Engine:
        def generate(self, messages, **kw):
            assert "slower" in messages[-1].content and kw["max_tokens"] >= 600
            return {"content": REPLY}

    app = FastAPI()
    app.state.engine, app.state.model = Engine(), "m"
    app.include_router(create_book_router(lambda: lib))
    book = _rendered(lib, 2)
    r = TestClient(app).post(
        f"/v1/books/{book['id']}/chapters/2/redo", json={"note": "slower"}
    )
    assert r.status_code == 200
    assert r.json()["understood"][0] == "Gio said as Jee-oh."
    assert r.json()["not_possible"]
    # no body at all still works (the old page's call)
    _rendered(lib, 2)
    assert (
        TestClient(app).post(f"/v1/books/{book['id']}/chapters/2/redo").status_code
        == 200
    )


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_a_render_uses_the_notes_settings(lib):
    from openjarvis.audiobooks.render import SAMPLE_RATE, ChapterRenderer

    said = []

    def voice(text, name):
        said.append(text)
        return np.zeros(int(SAMPLE_RATE * 0.05 * len(text)), np.float32) + 0.1

    book = lib.find("")
    plain = ChapterRenderer(lib.store, voice, lambda: True).render(book["id"], 2, "v")
    lib.redo_chapter(book["id"], 1, interpret("n", "Chapter 1", [], lambda m: REPLY))
    said.clear()
    slow = ChapterRenderer(lib.store, voice, lambda: True).render(book["id"], 1, "v")
    assert any("Jee-oh" in t for t in said) and not any("Gio" in t for t in said)
    # same text: longer pauses and a slower tempo make it longer
    assert slow.seconds > plain.seconds * 1.1


def test_a_preview_reads_the_note_without_queueing(lib):
    class Engine:
        def generate(self, messages, **kw):
            return {"content": REPLY}

    app = FastAPI()
    app.state.engine, app.state.model = Engine(), "m"
    app.include_router(create_book_router(lambda: lib))
    book = _rendered(lib, 2)
    r = TestClient(app).post(
        f"/v1/books/{book['id']}/chapters/2/redo", json={"note": "x", "preview": True}
    )
    assert r.json()["preview"] and r.json()["settings"]["pause_scale"] == 1.3
    assert lib.detail(book["id"])["chapters"][1]["state"] == "ready"
