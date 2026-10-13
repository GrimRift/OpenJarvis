"""M42 step 4: M4B parts by stream copy, OneDrive copy, Telegram send."""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from openjarvis.audiobooks import export
from openjarvis.audiobooks.export import Destinations
from openjarvis.audiobooks.queue import chapter_file
from openjarvis.audiobooks.store import BookStore

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not on PATH",
)
VOICE = "lotm-narrator"


def _book(store, n=5, skip=()):
    chapters = [
        {
            "index": i,
            "label": f"Chapter {i}",
            "number": str(i),
            "title": f"Chapter {i}" if i != 2 else "The Well",
            "skip": i in skip,
            "words": 100,
            "skip_reason": None,
            "file": f"chapters/{i:04d}.txt",
        }
        for i in range(1, n + 1)
    ]
    book = {
        "id": "test-book-0000abcd",
        "title": "Test: Book?",
        "author": "취미글주의, Hobby",
        "chapters": chapters,
    }
    return store.save(book, ["Text."] * n)


def _ffmpeg(*args):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True
    )


def _render(store, book, index, seconds=1.0, voice=VOICE):
    out = chapter_file(store, book["id"], voice, index)
    out.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg(
        "-f", "lavfi", "-i", f"sine=frequency={200 + index * 50}:duration={seconds}",
        "-ar", "24000", "-ac", "1", "-c:a", "aac", "-b:a", "64k",
        "-metadata", f"title=Chapter {index}", str(out),
    )  # fmt: skip
    return out


def _cover(store, book):
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1",
        str(store.root / book["id"] / "cover.jpg"),
    )  # fmt: skip
    return store.update(book["id"], cover="cover.jpg")


def _probe(path):
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_format", "-show_streams",
            "-show_chapters", "-of", "json", str(path),
        ],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )  # fmt: skip
    return json.loads(out.stdout)


@pytest.fixture
def env(tmp_path):
    store = BookStore(tmp_path / "books")
    dest = Destinations(tmp_path / "Audiobooks", tmp_path / "OneDrive" / "Sage")
    return store, dest


def test_plan_parts_runs_of_narrated_chapters_named_by_book_numbers(env):
    store, _ = env
    book = _book(store, n=7, skip=(3,))
    parts = export.plan_parts(book, part_size=3)
    assert [p.indexes for p in parts] == [[1, 2, 4], [5, 6, 7]]
    assert parts[0].name() == "Part 01 (Ch 1-4)"
    assert parts[1].name(last=book["chapters"][4]) == "Part 02 (Ch 5)"
    assert export.part_of(book, 6, part_size=3).number == 2
    assert export.part_of(book, 3, part_size=3) is None  # skipped chapter


def test_folder_name_is_windows_safe_and_other_voices_get_their_own(env):
    store, _ = env
    book = _book(store)
    assert export.book_folder_name(book, VOICE) == "Test Book"
    assert export.book_folder_name(book, "jarvis") == "Test Book (jarvis)"


def test_destinations_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SAGE_AUDIOBOOKS_DIR", str(tmp_path / "A"))
    monkeypatch.setenv("SAGE_AUDIOBOOKS_ONEDRIVE", "off")
    assert export.destinations() == Destinations(tmp_path / "A", None)
    monkeypatch.delenv("SAGE_AUDIOBOOKS_DIR")
    monkeypatch.delenv("SAGE_AUDIOBOOKS_ONEDRIVE")
    monkeypatch.setenv("OneDrive", str(tmp_path))
    d = export.destinations()
    assert d.root == export.DEFAULT_ROOT
    assert d.onedrive == tmp_path / "Sage" / "Audiobooks"


@needs_ffmpeg
def test_part_is_a_stream_copy_with_chapters_tags_and_cover(env):
    store, dest = env
    book = _cover(store, _book(store, n=3))
    lengths = [1.0, 1.5, 2.0]
    for i, s in enumerate(lengths, 1):
        _render(store, book, i, s)
    [out] = export.export_parts(book["id"], store=store, dest=dest, part_size=25)
    assert out == dest.root / "Test Book" / "Part 01 (Ch 1-3).m4b"
    info = _probe(out)
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    video = [s for s in info["streams"] if s["codec_type"] == "video"]
    assert audio[0]["codec_name"] == "aac" and audio[0]["sample_rate"] == "24000"
    assert video and video[0]["disposition"]["attached_pic"] == 1
    titles = [c["tags"]["title"] for c in info["chapters"]]
    assert titles == ["Chapter 1", "Chapter 2: The Well", "Chapter 3"]
    total = float(info["format"]["duration"])
    probed = sum(export.probe_duration(chapter_file(store, book["id"], VOICE, i))
                 for i in (1, 2, 3))  # fmt: skip
    assert abs(total - probed) < export.DURATION_TOLERANCE
    assert float(info["chapters"][-1]["end_time"]) == pytest.approx(probed, abs=0.01)
    tags = {k.lower(): v for k, v in info["format"]["tags"].items()}
    assert tags["album"] == "Test: Book?"
    assert tags["artist"] == "취미글주의, Hobby"
    assert tags["title"] == "Test: Book? - Part 01 (Ch 1-3)"
    assert tags["genre"] == "Audiobook"
    # OneDrive copy is the same file; nothing temp left behind.
    copy = dest.onedrive / "Test Book" / out.name
    assert copy.read_bytes() == out.read_bytes()
    assert not list(dest.root.rglob("~$*")) and not list(dest.onedrive.rglob("~$*"))
    status = export.part_status(book["id"], store=store)
    assert status[0]["exported"]["onedrive"] == str(copy)


@needs_ffmpeg
def test_incomplete_part_waits_unless_partial_then_full_part_replaces_it(env):
    store, dest = env
    book = _book(store, n=4)
    for i in (1, 2):
        _render(store, book, i)
    assert export.export_parts(book["id"], store=store, dest=dest, part_size=3) == []
    written = export.export_parts(
        book["id"], store=store, dest=dest, part_size=3, partial=True
    )
    assert [p.name for p in written] == ["Part 01 (Ch 1-2).m4b"]
    assert export.part_status(book["id"], store=store, part_size=3)[0]["exported"]
    _render(store, book, 3)
    (path,) = export.export_parts(book["id"], store=store, dest=dest, part_size=3)
    assert path.name == "Part 01 (Ch 1-3).m4b"
    names = sorted(p.name for p in (dest.root / "Test Book").iterdir())
    assert names == ["Part 01 (Ch 1-3).m4b"]
    assert sorted(p.name for p in (dest.onedrive / "Test Book").iterdir()) == names
    # Up to date: nothing rewritten until a chapter changes or force.
    assert export.export_parts(book["id"], store=store, dest=dest, part_size=3) == []
    again = export.export_parts(
        book["id"], store=store, dest=dest, part_size=3, force=True
    )
    assert len(again) == 1


@needs_ffmpeg
def test_auto_export_writes_a_part_once_its_last_chapter_is_made(env, monkeypatch):
    store, dest = env
    monkeypatch.setattr(export, "PART_SIZE", 2)
    book = _book(store, n=3)
    _render(store, book, 1)
    assert export.auto_export(book["id"], VOICE, 1, store=store, dest=dest) is None
    _render(store, book, 2)
    out = export.auto_export(book["id"], VOICE, 2, store=store, dest=dest)
    assert out.name == "Part 01 (Ch 1-2).m4b"
    _render(store, book, 3)  # the short last part is complete on its own
    out = export.auto_export(book["id"], VOICE, 3, store=store, dest=dest)
    assert out.name == "Part 02 (Ch 3).m4b"
    monkeypatch.setenv("SAGE_AUDIOBOOKS_AUTO_EXPORT", "0")
    assert export.auto_export(book["id"], VOICE, 3, store=store, dest=dest) is None


@needs_ffmpeg
def test_render_worker_exports_the_part_it_completes(tmp_path, monkeypatch):
    from openjarvis.audiobooks import worker as worker_mod
    from openjarvis.audiobooks.importer import import_book
    from openjarvis.audiobooks.queue import RenderQueue
    from tests.audiobooks.builders import make_epub
    from tests.audiobooks.test_worker import FakeBookVoice, _worker

    store = BookStore(tmp_path / "books")
    para = "<p>" + "The mill wheel turned in the stream all day. " * 4 + "</p>"
    book = import_book(
        make_epub(
            tmp_path / "b.epub",
            [("c1.xhtml", para), ("c2.xhtml", para)],
            [("Chapter 1", "c1.xhtml"), ("Chapter 2", "c2.xhtml")],
        ),
        store,
    )
    monkeypatch.setattr(worker_mod, "voice_exists", lambda v: True)
    queue = RenderQueue(store)
    queue.add(book["id"], "book")
    queue.set_break(0)
    w = _worker(store, FakeBookVoice())
    root = tmp_path / "Audiobooks"  # conftest points SAGE_AUDIOBOOKS_DIR here
    w.step()
    assert not root.exists()  # part 1 = chapters 1-2, only half made
    w.step()
    assert [p.name for p in root.rglob("*.m4b")] == ["Part 01 (Ch 1-2).m4b"]


class FakePhone:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def send_audio(self, chat, path, *, caption="", title="", performer=""):
        self.sent.append((chat, Path(path).name, caption, title, performer))
        return self.ok


def test_send_chapters_reports_what_was_not_made_or_too_big(env, monkeypatch):
    store, _ = env
    book = _book(store, n=4)
    for i in (1, 2, 3):
        f = chapter_file(store, book["id"], VOICE, i)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * 10)
    monkeypatch.setattr(export, "TELEGRAM_MAX_BYTES", 5)
    phone = FakePhone()
    result = export.send_chapters(
        book["id"], None, "2-4", store=store, channel=phone, chat_id="42"
    )
    assert result.too_big == [2, 3] and result.not_made == [4] and not result.sent
    monkeypatch.setattr(export, "TELEGRAM_MAX_BYTES", 50)
    result = export.send_chapters(
        book["id"], None, "1-2", store=store, channel=phone, chat_id="42"
    )
    assert result.ok and result.sent == [1, 2]
    assert phone.sent[1] == (
        "42", "0002.m4a", "Test: Book? - Chapter 2: The Well",
        "Chapter 2: The Well", "Test: Book?",
    )  # fmt: skip
    assert result.describe(book) == "Sent chapter(s) 1, 2 to your phone."
    failed = export.send_chapters(
        book["id"], None, [1], store=store, channel=FakePhone(ok=False), chat_id="42"
    )
    assert not failed.ok and "refused" in failed.describe(book)


def test_send_chapters_without_a_phone_channel(env, monkeypatch):
    store, _ = env
    book = _book(store, n=1)

    def no_channel():
        raise RuntimeError("no phone channel is configured")

    monkeypatch.setattr(export, "_phone_channel", no_channel)
    result = export.send_chapters(book["id"], None, "1", store=store)
    assert not result.ok and "no phone channel" in result.describe(book)


def test_telegram_send_audio_posts_multipart(tmp_path, monkeypatch):
    import httpx

    from openjarvis.channels.telegram import TelegramChannel

    posted = {}

    def fake_post(url, data=None, files=None, timeout=None):
        name, handle, mime = files["audio"]
        posted.update(url=url, data=data, name=name, mime=mime, body=handle.read())
        return SimpleNamespace(status_code=200, text="ok")

    monkeypatch.setattr(httpx, "post", fake_post)
    audio = tmp_path / "0005.m4a"
    audio.write_bytes(b"aac")
    channel = TelegramChannel(bot_token="t0k")
    ok = channel.send_audio(
        "123",
        str(audio),
        caption="Book - Chapter 5",
        title="Chapter 5",
        performer="Book",
    )
    assert ok and posted["url"].endswith("/sendAudio")
    assert posted["data"] == {
        "chat_id": "123",
        "caption": "Book - Chapter 5",
        "title": "Chapter 5",
        "performer": "Book",
    }
    assert posted["mime"] == "audio/mp4" and posted["body"] == b"aac"
    assert TelegramChannel(bot_token="").send_audio("1", str(audio)) is False


# ------------------------------------------ background send, routes, tools


def _fake_files(store, book, indexes, voice=VOICE):
    for i in indexes:
        f = chapter_file(store, book["id"], voice, i)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * 10)


def _wait_send(store):
    import time

    for _ in range(200):
        if not export.sending():
            return export.send_status(store)
        time.sleep(0.02)
    raise AssertionError("send did not finish")


def test_start_send_runs_in_the_background_and_records_progress(env):
    store, _ = env
    book = _book(store, n=4)
    _fake_files(store, book, [1, 2])
    phone = FakePhone()
    status = export.start_send(
        book["id"], None, "1-3", store=store, channel=phone, chat_id="42"
    )
    assert status["state"] == "sending" and status["made"] == [1, 2]
    assert status["not_made"] == [3]
    done = _wait_send(store)
    assert done["state"] == "done" and done["sent"] == [1, 2]
    assert "Sent chapter(s) 1, 2" in done["message"]
    assert "Not narrated yet: 3" in done["message"]
    with pytest.raises(ValueError):
        export.start_send(book["id"], None, "4", store=store, channel=phone)


def test_one_send_at_a_time(env):
    import threading

    store, _ = env
    book = _book(store, n=2)
    _fake_files(store, book, [1, 2])
    gate = threading.Event()

    class SlowPhone(FakePhone):
        def send_audio(self, *a, **k):
            gate.wait(5)
            return super().send_audio(*a, **k)

    export.start_send(book["id"], None, "1", store=store, channel=SlowPhone())
    with pytest.raises(RuntimeError, match="Still sending"):
        export.start_send(book["id"], None, "2", store=store, channel=FakePhone())
    gate.set()
    assert _wait_send(store)["sent"] == [1]


def test_a_send_cut_by_a_restart_reads_as_stopped(env):
    store, _ = env
    export._write_send_status(store, {"state": "sending", "book": "b", "sent": []})
    assert export.send_status(store)["state"] == "stopped"


def _client(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.audiobooks.library import Library
    from openjarvis.server.book_routes import create_book_router

    app = FastAPI()
    app.include_router(create_book_router(lambda: Library(store)))
    return TestClient(app)


@needs_ffmpeg
def test_routes_export_send_and_detail_delivery(env, monkeypatch):
    store, dest = env
    monkeypatch.setattr(export, "destinations", lambda: dest)
    book = _book(store, n=3)
    _render(store, book, 1)
    _render(store, book, 3)
    client = _client(store)
    r = client.post(f"/v1/books/{book['id']}/export", json={})
    assert r.status_code == 200
    # gaps are skipped in a partial part; the name says the ends
    assert [Path(p).name for p in r.json()["written"]] == ["Part 01 (Ch 1-3).m4b"]
    delivery = client.get(f"/v1/books/{book['id']}").json()["delivery"]
    assert delivery["parts"][0]["exported"]["chapters"] == [1, 3]
    assert delivery["folders"]["pc"] == str(dest.root)
    phone = FakePhone()
    monkeypatch.setattr(export, "_phone_channel", lambda: (phone, "42"))
    r = client.post(f"/v1/books/{book['id']}/send", json={"chapters": "1"})
    assert r.status_code == 200 and r.json()["sending"]["made"] == [1]
    _wait_send(store)
    sent = client.get(f"/v1/books/{book['id']}").json()["delivery"]["sending"]
    assert sent["state"] == "done" and phone.sent[0][1] == "0001.m4a"
    r = client.post(f"/v1/books/{book['id']}/send", json={"chapters": "2"})
    assert r.status_code == 400 and "narrated" in r.json()["detail"]


def test_chat_tools_export_and_send(env, monkeypatch):
    from openjarvis.audiobooks.library import Library
    from openjarvis.tools import audiobook_tool as tools

    store, dest = env
    monkeypatch.setattr(export, "destinations", lambda: dest)
    book = _book(store, n=30)
    lib = Library(store)
    r = tools.AudiobookExportTool(lib).execute()
    assert r.success and "No chapters" in r.content
    phone = FakePhone()
    monkeypatch.setattr(export, "_phone_channel", lambda: (phone, "42"))
    _fake_files(store, book, [26, 27])
    r = tools.AudiobookSendTool(lib).execute(part=2)
    assert r.success and "Sending 2 chapter(s)" in r.content
    assert "chapters 26 and 27" in r.content
    assert "Not narrated yet, so not sent: chapters 28-30" in r.content
    _wait_send(store)
    assert [s[1] for s in phone.sent] == ["0026.m4a", "0027.m4a"]
    assert not tools.AudiobookSendTool(lib).execute(part=3).success
    assert not tools.AudiobookSendTool(lib).execute().success
