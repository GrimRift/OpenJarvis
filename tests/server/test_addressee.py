"""Words heard while Sage answered. "blah blah blah" restarted the answer
(24 September); an addition threw away an answer the user had half heard
and wrote it again from the top (15 October)."""

from __future__ import annotations

from openjarvis.server.addressee import decide_interjection, parse_interjection


class _Engine:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.seen = reply, error, []

    def generate(self, messages, **kwargs):
        self.seen.append((messages, kwargs))
        if self.error:
            raise self.error
        return {"content": self.reply}


def test_each_marker_is_read_from_the_reply():
    assert parse_interjection("[[noise]]") == ("noise", "")
    assert parse_interjection("[[now]]") == ("now", "")
    assert parse_interjection(" [[NEW]]\n") == ("new", "")


def test_after_carries_its_line():
    reply = "[[after]] Glad you're feeling better, Sir. Keep resting."
    assert parse_interjection(reply) == (
        "after",
        "Glad you're feeling better, Sir. Keep resting.",
    )


def test_an_after_with_nothing_to_say_is_let_pass():
    assert parse_interjection("[[after]]   ") == ("noise", "")


def test_only_the_marker_kinds_write_nothing_after_them():
    # A line after [[now]] is never spoken: the turn that follows writes it.
    assert parse_interjection("[[now]] Rome, Georgia, is a city in...") == ("now", "")


def test_an_unclear_or_failed_decision_sends_the_words_on():
    # The model is then told to finish its point if they are noise
    # (CONTINUE_NOTE).
    assert decide_interjection(_Engine("hmm"), "m", "q", "s", "w") == ("now", "")
    assert decide_interjection(
        _Engine(error=RuntimeError("down")), "m", "q", "s", "w"
    ) == ("now", "")


def test_the_decision_sees_what_was_already_said_and_has_room_to_think():
    engine = _Engine("[[noise]]")
    decide_interjection(
        engine, "gpt-6-luna", "my fever", "Glad it is easing.", "blah blah"
    )
    messages, kwargs = engine.seen[0]
    assert kwargs["max_tokens"] >= 600
    assert "blah blah" in messages[1].content
    assert "Glad it is easing." in messages[1].content


def test_nothing_said_yet_is_named_as_such():
    engine = _Engine("[[now]]")
    decide_interjection(engine, "m", "q", "  ", "w")
    assert "(nothing yet)" in engine.seen[0][0][1].content
