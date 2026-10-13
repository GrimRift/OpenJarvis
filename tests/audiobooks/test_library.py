"""The library layer the Books page and the chat tools share, and its routes."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.audiobooks import BookStore, import_book
from openjarvis.audiobooks.library import BookNotFound, Library
from openjarvis.audiobooks.queue import chapter_file
from openjarvis.server.book_routes import create_book_router
from tests.audiobooks.builders import make_epub

BODY = "<p>" + "The mill wheel turned slowly in the stream all day. " * 8 + "</p>"
LABELS = ["Copyright", "Chapter 1", "Chapter 2", "Chapter 3"]


@pytest.fixture
def lib(tmp_path):
    docs = [(f"d{i}.xhtml", BODY) for i in range(len(LABELS))]
    toc = [(label, f"d{i}.xhtml") for i, label in enumerate(LABELS)]
    store = BookStore(tmp_path / "books")
    import_book(make_epub(tmp_path / "b.epub", docs, toc, cover=b"\xff\xd8jpeg"), store)
    return Library(store)


def _make(lib, index, seconds=60.0, voice="lotm-narrator"):
    book = lib.find("")
    path = chapter_file(lib.store, book["id"], voice, index)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"m4a")
    status_path = path.parent / "status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    status[str(index)] = {"file": path.name, "seconds": seconds, "flags": []}
    status_path.write_text(json.dumps(status))


def test_find_by_title_piece_or_the_only_book(lib):
    assert lib.find("")["title"] == "Synthetic Book"
    assert lib.find("synthetic")["title"] == "Synthetic Book"
    with pytest.raises(BookNotFound):
        lib.find("dune")


def test_detail_states_follow_files_queue_and_worker(lib):
    book = lib.find("")
    _make(lib, 2)
    lib.render(book["id"], chapters="2-3")
    (lib.store.root / "render-status.json").write_text(
        json.dumps(
            {
                "state": "rendering",
                "book": book["id"],
                "voice": "lotm-narrator",
                "chapter": 4,
            }
        )
    )
    d = lib.detail(book["id"])
    assert {c["label"]: c["state"] for c in d["chapters"]} == {
        "Copyright": "skipped",
        "Chapter 1": "ready",  # index 2: its file exists
        "Chapter 2": "queued",  # in the 2-3 job
        "Chapter 3": "rendering",  # index 4: the worker is on it
    }
    assert d["chapters"][1]["seconds"] == 60
    assert d["jobs"][0]["wanted"] == 2 and d["jobs"][0]["done"] == 0


def test_position_moves_rolling_jobs_and_survives_reimport(lib, tmp_path):
    book = lib.find("")
    job = lib.render(book["id"], ahead=1)
    lib.set_position(book["id"], 3, 125.0)
    assert lib.queue.load()["jobs"][0]["position"] == 3
    assert lib.queue.wanted(
        lib.queue.load()["jobs"][0], lib.store.load(book["id"])
    ) == [4]
    assert job["mode"] == "rolling"
    d = lib.detail(book["id"])
    assert d["position"] == 3 and d["position_seconds"] == 125.0
    import_book(tmp_path / "b.epub", lib.store)
    assert lib.store.load(book["id"])["position"] == 3
    with pytest.raises(ValueError):
        lib.set_position(book["id"], 99)


def test_describe_speaks_progress(lib):
    book = lib.find("")
    _make(lib, 2, seconds=1800)
    lib.set_position(book["id"], 2)
    text = lib.describe(book["id"])
    assert "1 of 3 chapters are ready" in text and "You're at Chapter 1." in text


def test_routes(lib):
    app = FastAPI()
    app.include_router(create_book_router(lambda: lib))
    client = TestClient(app)
    book = lib.find("")
    _make(lib, 2)
    listing = client.get("/v1/books").json()
    assert listing["books"][0]["rendered"] == 1
    assert client.get(f"/v1/books/{book['id']}").json()["voice"] == "lotm-narrator"
    assert client.get("/v1/books/nope-1").status_code == 404
    assert client.get(f"/v1/books/{book['id']}/cover").content == b"\xff\xd8jpeg"
    audio = client.get(f"/v1/books/{book['id']}/chapters/2/audio")
    assert audio.status_code == 200 and audio.content == b"m4a"
    assert client.get(f"/v1/books/{book['id']}/chapters/3/audio").status_code == 404
    r = client.post(f"/v1/books/{book['id']}/render", json={"chapters": "2-3"})
    assert r.status_code == 200 and r.json()["job"]["mode"] == "range"
    assert (
        client.post(
            f"/v1/books/{book['id']}/render", json={"chapters": "9"}
        ).status_code
        == 400
    )
    assert (
        client.put("/v1/books/queue", json={"paused": True, "speed": 0.75}).json()[
            "speed"
        ]
        == 0.75
    )
    assert client.put("/v1/books/queue", json={"speed": 3}).status_code == 400
    assert (
        client.put(f"/v1/books/{book['id']}/voice", json={"voice": "jarvis"}).json()[
            "voice"
        ]
        == "jarvis"
    )
    assert (
        client.put(
            f"/v1/books/{book['id']}/position", json={"index": 3, "seconds": 9}
        ).json()["position"]
        == 3
    )


def test_import_upload_route(lib, tmp_path):
    app = FastAPI()
    app.include_router(create_book_router(lambda: lib))
    client = TestClient(app)
    epub = make_epub(
        tmp_path / "other.epub",
        [("a.xhtml", BODY)],
        [("Chapter 1", "a.xhtml")],
        title="Other Book",
    )
    r = client.post(
        "/v1/books/import", files={"file": ("Other Book.epub", epub.read_bytes())}
    )
    assert r.status_code == 200 and r.json()["title"] == "Other Book"
    assert (lib.store.root / "_sources" / "Other Book.epub").is_file()
    bad = client.post("/v1/books/import", files={"file": ("notes.txt", b"hi")})
    assert bad.status_code == 400


def test_chapter_text_for_reading(lib):
    app = FastAPI()
    app.include_router(create_book_router(lambda: lib))
    client = TestClient(app)
    book = lib.find("")
    r = client.get(f"/v1/books/{book['id']}/chapters/2/text")
    assert r.status_code == 200
    body = r.json()
    assert body["label"] == "Chapter 1"
    assert body["paragraphs"][0].startswith("The mill wheel")
    assert client.get(f"/v1/books/{book['id']}/chapters/99/text").status_code == 404
