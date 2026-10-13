"""M42 chat tools: status, render, pause, import, play."""

from __future__ import annotations

import json

import pytest

from openjarvis.audiobooks import BookStore, import_book
from openjarvis.audiobooks.library import Library
from openjarvis.audiobooks.queue import chapter_file
from openjarvis.tools import audiobook_tool as tools
from tests.audiobooks.builders import make_epub

BODY = "<p>" + "The mill wheel turned slowly in the stream all day. " * 8 + "</p>"


@pytest.fixture
def lib(tmp_path):
    docs = [(f"d{i}.xhtml", BODY) for i in range(3)]
    toc = [(f"Chapter {i + 1}", f"d{i}.xhtml") for i in range(3)]
    store = BookStore(tmp_path / "books")
    import_book(make_epub(tmp_path / "b.epub", docs, toc, title="Mill Tales"), store)
    return Library(store)


def _ready(lib, index):
    book = lib.find("")
    path = chapter_file(lib.store, book["id"], "lotm-narrator", index)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"m4a")
    (path.parent / "status.json").write_text(
        json.dumps({str(index): {"file": path.name, "seconds": 600}})
    )


def test_status_and_render(lib):
    status = tools.AudiobookStatusTool(lib).execute()
    assert "0 of 3 chapters are ready" in status.content
    r = tools.AudiobookRenderTool(lib).execute(book="mill", chapters="2-3")
    assert r.success and "Queued 2 chapters of Mill Tales" in r.content
    r = tools.AudiobookRenderTool(lib).execute(ahead=2)
    assert "keeping 2 chapters ready" in r.content
    bad = tools.AudiobookRenderTool(lib).execute(chapters="9")
    assert not bad.success


def test_pause_resume_cancel(lib):
    t = tools.AudiobookPauseTool(lib)
    t.execute(action="pause")
    assert lib.queue.load()["paused"] is True
    t.execute(action="resume")
    assert lib.queue.load()["paused"] is False
    tools.AudiobookRenderTool(lib).execute()
    assert "Cancelled 1" in t.execute(action="cancel").content


def test_play_needs_a_made_chapter_and_hands_the_app_what_to_play(lib):
    play = tools.AudiobookPlayTool(lib)
    r = play.execute(chapter="2")
    assert not r.success and "isn't made yet" in r.content
    _ready(lib, 2)
    r = play.execute(chapter="2")
    assert r.success
    meta = r.metadata["audiobook_play"]
    assert (meta["action"], meta["index"], meta["seconds"], meta["label"]) == (
        "play",
        2,
        0.0,
        "Chapter 2",
    )
    book = lib.find("")
    lib.set_position(book["id"], 2, 90)
    assert play.execute(chapter="continue").metadata["audiobook_play"]["seconds"] == 90
    assert play.execute(action="pause").metadata == {
        "audiobook_play": {"action": "pause"}
    }


def test_import_only_books_from_the_users_folders(lib, tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_import_roots", lambda: [tmp_path / "ok"])
    (tmp_path / "ok").mkdir()
    good = make_epub(
        tmp_path / "ok" / "n.epub",
        [("a.xhtml", BODY)],
        [("Chapter 1", "a.xhtml")],
        title="New One",
    )
    r = tools.AudiobookImportTool(lib).execute(path=str(good))
    assert r.success and "Imported New One" in r.content
    outside = make_epub(
        tmp_path / "x.epub", [("a.xhtml", BODY)], [("Chapter 1", "a.xhtml")]
    )
    assert not tools.AudiobookImportTool(lib).execute(path=str(outside)).success
    assert (
        not tools.AudiobookImportTool(lib)
        .execute(path=str(tmp_path / "ok" / "a.txt"))
        .success
    )


def test_voice_names_resolve_from_labels(tmp_path, monkeypatch):
    voices = tmp_path / "voices"
    (voices / "lotm-narrator").mkdir(parents=True)
    (voices / "lotm-narrator" / "meta.json").write_text(
        json.dumps({"label": "LOTM Narrator"})
    )
    monkeypatch.setenv("OPENJARVIS_HOME", str(tmp_path))
    assert tools._resolve_voice("LOTM Narrator") == "lotm-narrator"
    assert tools._resolve_voice("lotm-narrator") == "lotm-narrator"
    with pytest.raises(ValueError):
        tools._resolve_voice("Morgan")
