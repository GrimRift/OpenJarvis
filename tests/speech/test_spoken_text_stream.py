"""Incremental speech segmentation must never expose partial private values."""

from __future__ import annotations

import pytest

from openjarvis.speech.spoken_text import SpokenTextOverflow, SpokenTextStream


def test_completed_sentence_is_released_before_final_tail() -> None:
    stream = SpokenTextStream()

    assert stream.push("First sentence. The second") == ["First sentence."]
    assert stream.push(" sentence is still generating") == []
    assert stream.finish() == ["The second sentence is still generating"]


def test_segments_are_delivered_in_order_and_final_tail_is_flushed() -> None:
    stream = SpokenTextStream()

    spoken = [
        *stream.push("One. Two! Three"),
        *stream.push("? Tail without punctuation"),
        *stream.finish(),
    ]

    assert spoken == ["One.", "Two!", "Three?", "Tail without punctuation"]


@pytest.mark.parametrize(
    ("parts", "secret", "notice"),
    [
        (
            ("Open https://exa", "mple.com/reset now. "),
            "https://example.com/reset",
            "The link is in chat.",
        ),
        (
            (r"Use `C:\Program Fi", r"les\Sage\user.json` now. "),
            r"C:\Program Files\Sage\user.json",
            "The file path is in chat.",
        ),
        (
            ("Your verification code is 73", "9204. "),
            "739204",
            "The sensitive value is in chat.",
        ),
        (
            ("Use session 123e4567-e89b-12d3-", "a456-426614174000. "),
            "123e4567-e89b-12d3-a456-426614174000",
            "The sensitive value is in chat.",
        ),
    ],
)
def test_private_values_split_across_deltas_never_leak(
    parts: tuple[str, str], secret: str, notice: str
) -> None:
    stream = SpokenTextStream()

    assert stream.push(parts[0]) == []
    spoken = stream.push(parts[1]) + stream.finish()

    joined = " ".join(spoken)
    assert secret not in joined
    assert notice in joined


def test_unfinished_markdown_is_held_across_deltas() -> None:
    stream = SpokenTextStream()

    assert stream.push("See [the doc") == []
    assert stream.push("s](https://example.com/a). Next") == ["See the docs."]
    assert stream.finish() == ["Next"]


def test_unfinished_emphasis_is_held_until_its_closing_delta() -> None:
    stream = SpokenTextStream()

    assert stream.push("This is **bold. ") == []
    assert stream.push("Still bold**. Next") == ["This is bold. Still bold."]
    assert stream.finish() == ["Next"]


def test_source_text_is_never_modified() -> None:
    source = "Open https://example.com. Keep **all** chat formatting."
    stream = SpokenTextStream()

    stream.push(source)
    stream.finish()

    assert source == "Open https://example.com. Keep **all** chat formatting."


def test_pending_raw_text_is_bounded() -> None:
    stream = SpokenTextStream(max_pending_chars=32)

    with pytest.raises(SpokenTextOverflow):
        stream.push("x" * 33)
