"""2026-10-14 additions: stutters, honest repeats, follow-along timings,
flags the user can see and dismiss, and chapters added after import."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.audiobooks import BookStore, import_book
from openjarvis.audiobooks import pieces as piece_rules
from openjarvis.audiobooks.additions import _chapter_part, split_paragraphs
from openjarvis.audiobooks.check import judge
from openjarvis.audiobooks.library import Library
from openjarvis.audiobooks.queue import chapter_file
from openjarvis.server.book_routes import create_book_router
from tests.audiobooks.builders import make_epub

BODY = "<p>" + "The mill wheel turned slowly in the stream all day. " * 8 + "</p>"


# ------------------------------------------------------------------ stutters


@pytest.mark.parametrize(
    "written, spoken",
    [
        ('"J-just now..."', '"Ja-just now..."'),
        ("W-what?", "Wha-what?"),
        ("B-but", "Ba-but"),
        ("s-s-sorry", "so-so-sorry"),
        ("G-Gio", "Gio-Gio"),
        ("N-NO!", "NO-NO!"),
    ],
)
def test_a_written_stutter_is_spoken_as_one(written, spoken):
    assert piece_rules.spoken_stutters(written) == spoken


@pytest.mark.parametrize(
    "text",
    [
        "a C-class hunter",
        "win-win",
        "one-on-one",
        "Kuk-kuk-kuk",
        "re-read",
        "x-ray, T-shirt, e-mail, A-list",
        "Uh-huh",
    ],
)
def test_hyphenated_words_that_are_not_stutters_are_left_alone(text):
    assert piece_rules.spoken_stutters(text) == text


# ------------------------------------------------------------------ the check


def test_a_line_that_repeats_on_purpose_is_not_flagged():
    # 10-13 chapters 6, 9, 11: "Rustle, rustle" heard as "Russell, Russell".
    assert (
        judge("Rustle, rustle. Swish...", "Russell, Russell. Swish.", 1.5, 0.062) == []
    )
    assert judge("Swish, swiiiish.", "Swish, swish.", 1.0, 0.062) == []


def test_a_line_said_twice_is_still_flagged():
    assert judge("It's nice.", "It's nice. It's nice.", 0.6, 0.062) == [
        "repeated 'its nice'"
    ]


# ------------------------------------------------------------------ timings


def test_every_piece_knows_its_paragraph():
    text = (
        "First paragraph here, long enough to stand alone.\n\n…\n\n* * *\n\n"
        "Yes.\n\nThe last paragraph of the chapter, also long."
    )
    items = piece_rules.build(text, "Chapter 1.")
    assert items[0].para == -1
    spoken = [(it.para, it.text) for it in items if it.text]
    assert spoken[1][0] == 0
    # "Yes." (paragraph 3) is merged into the next one and starts it.
    assert spoken[-1] == (3, "Yes. The last paragraph of the chapter, also long.")


# ------------------------------------------------------------------ library


@pytest.fixture
def lib(tmp_path):
    labels = ["Chapter 1", "Chapter 2", "Chapter 4"]
    docs = [(f"d{i}.xhtml", BODY) for i in range(len(labels))]
    toc = [(label, f"d{i}.xhtml") for i, label in enumerate(labels)]
    store = BookStore(tmp_path / "books")
    import_book(make_epub(tmp_path / "b.epub", docs, toc), store)
    return Library(store)


def _rendered(lib, index, seconds=60.0, **extra):
    book = lib.find("")
    path = chapter_file(lib.store, book["id"], "lotm-narrator", index)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"m4a")
    status_path = path.parent / "status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    status[str(index)] = {"file": path.name, "seconds": seconds, "flags": [], **extra}
    status_path.write_text(json.dumps(status))
    return book


def test_reader_gets_paragraph_starts_estimated_for_old_chapters(lib):
    book = _rendered(lib, 1, seconds=40.0)
    text = lib.chapter_text(book["id"], 1)
    assert text["starts_exact"] is False
    assert text["starts"][0][0] == 0 and 0 < text["starts"][0][1] < 3
    _rendered(lib, 2, paragraphs=[[0, 1.5]])
    assert lib.chapter_text(book["id"], 2)["starts"] == [[0, 1.5]]
    assert lib.chapter_text(book["id"], 3)["starts"] is None  # not rendered


def test_flags_are_listed_with_a_time_and_can_be_dismissed(lib):
    flag = {"piece": 1, "text": "Rustle, rustle.", "why": ["repeated 'russell'"]}
    book = _rendered(lib, 1, flags=[flag])
    flags = lib.chapter_flags(book["id"], 1)
    assert flags[0]["text"] == "Rustle, rustle." and flags[0]["at"] > 0
    assert lib.dismiss_flag(book["id"], 1, 1) == 0
    assert lib.detail(book["id"])["chapters"][0]["flags"] == 0


def test_redo_keeps_the_old_file_and_queues_the_chapter(lib):
    book = _rendered(lib, 2)
    job = lib.redo_chapter(book["id"], 2)
    assert job["chapters"] == [2]
    folder = chapter_file(lib.store, book["id"], "lotm-narrator", 2).parent
    assert not (folder / "0002.m4a").exists()
    assert len(list((folder / "replaced").glob("0002-*.m4a"))) == 1
    assert lib.detail(book["id"])["chapters"][1]["state"] == "queued"


def test_a_pasted_chapter_goes_in_its_place_without_renumbering(lib):
    book = lib.find("")
    assert book["missing_numbers"] == [3]
    entry = lib.add_chapter_text(
        book["id"], "", "Chapter 3: The Well\n\n" + "The bucket came up empty. " * 10
    )
    assert (
        entry["index"] == 4 and entry["number"] == "3" and entry["title"] == "The Well"
    )
    after = lib.store.load(book["id"])
    assert [c["number"] for c in after["chapters"]] == ["1", "2", "3", "4"]
    assert [c["index"] for c in after["chapters"]] == [1, 2, 4, 3]
    assert after["missing_numbers"] == []
    assert "The Well" not in lib.store.chapter_text(
        book["id"], 4
    )  # announced, not read twice
    with pytest.raises(ValueError, match="already has chapter 3"):
        lib.add_chapter_text(book["id"], "3", "More text here. " * 10)


def test_rolling_render_follows_reading_order_not_index(lib):
    book = lib.find("")
    lib.add_chapter_text(book["id"], "3", "The bucket came up empty. " * 10)
    lib.set_position(book["id"], 2)
    lib.render(book["id"], ahead=2)
    job = lib.queue.load()["jobs"][-1]
    assert lib.queue.wanted(job, lib.store.load(book["id"])) == [4, 3]


def test_a_newer_file_adds_only_the_chapters_the_book_lacks(lib, tmp_path):
    book = lib.find("")
    labels = ["Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4", "Chapter 5"]
    docs = [(f"d{i}.xhtml", BODY) for i in range(len(labels))]
    toc = [(label, f"d{i}.xhtml") for i, label in enumerate(labels)]
    newer = make_epub(tmp_path / "newer.epub", docs, toc)
    result = lib.add_chapters_from_file(book["id"], newer)
    assert result["added"] == ["Chapter 3", "Chapter 5"] and result["already"] == 3
    numbers = [c["number"] for c in lib.store.load(book["id"])["chapters"]]
    assert numbers == ["1", "2", "3", "4", "5"]


def test_pasted_web_text_splits_by_line_and_drops_navigation():
    assert split_paragraphs("One.\nTwo.\n") == ["One.", "Two."]
    assert split_paragraphs("One\nstill one.\n\nTwo.") == ["One still one.", "Two."]
    lines = [
        "Home",
        "Novel name",
        "Chapter 88 - The Well",
        "A.",
        "B.",
        "C.",
        "D.",
        "E.",
        "F.",
        "Next Chapter",
        "Comments",
    ]
    body, heading = _chapter_part(lines)
    assert heading == "Chapter 88 - The Well" and body == [
        "A.",
        "B.",
        "C.",
        "D.",
        "E.",
        "F.",
    ]


def test_routes_add_and_dismiss(lib):
    app = FastAPI()
    app.include_router(create_book_router(lambda: lib))
    client = TestClient(app)
    book = _rendered(lib, 1, flags=[{"piece": 1, "text": "x", "why": ["garbled"]}])
    r = client.post(
        f"/v1/books/{book['id']}/chapters",
        json={"number": "3", "text": "The bucket came up empty. " * 10},
    )
    assert r.status_code == 200 and r.json()["added"]["label"] == "Chapter 3"
    assert (
        client.get(f"/v1/books/{book['id']}/chapters/1/flags").json()["flags"][0][
            "piece"
        ]
        == 1
    )
    assert client.delete(f"/v1/books/{book['id']}/chapters/1/flags/1").json() == {
        "flags": 0
    }
    assert (
        client.post(
            f"/v1/books/{book['id']}/chapters", json={"number": "x1", "text": "a"}
        ).status_code
        == 400
    )


# ------------------------------------------------------------------ sound effects


@pytest.mark.parametrize(
    "paragraph, kinds",
    [
        ("Rustle...", ["rustle"]),
        ("Rustle, rustle. Swish...", ["rustle", "rustle", "whoosh"]),
        ("Swoooosh, Rustle, rustle!", ["whoosh", "rustle", "rustle"]),
        ("SPLASH!!", ["splash"]),
        ("Creeeak...", ["creak"]),
    ],
)
def test_sound_only_paragraphs_get_their_sounds(paragraph, kinds):
    from openjarvis.audiobooks.sfx import sound_words

    assert [k for k, _ in sound_words(paragraph)] == kinds


@pytest.mark.parametrize(
    "paragraph", ["The leaves rustle.", "Rustle, he said.", "Yes."]
)
def test_sound_words_inside_narration_get_none(paragraph):
    from openjarvis.audiobooks.sfx import sound_words

    assert sound_words(paragraph) is None


def test_sound_cues_follow_their_words_through_a_merge():
    items = piece_rules.build(
        "Rustle...\n\nA long and pale hand disturbs the petals.", "Chapter 4."
    )
    merged = items[1]
    assert merged.text.startswith("Rustle... A long")
    assert merged.sounds == [("rustle", 0.0)]


def test_sounds_are_mixed_under_the_voice(monkeypatch):
    import numpy as np

    from openjarvis.audiobooks import sfx

    monkeypatch.setattr(sfx, "sound", lambda kind, pick: np.ones(100, np.float32))
    audio = np.zeros(48000, np.float32)
    assert sfx.mix_under(audio, [("rustle", 1.0), ("thud", 5.0)], voice_rms=0.2) == 1
    assert audio[24000] == pytest.approx(0.2 * sfx.UNDER_VOICE)
    assert audio[:24000].max() == 0
