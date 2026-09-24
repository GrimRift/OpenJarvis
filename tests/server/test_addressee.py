"""Words heard while Sage prepared an answer (24 September): "blah blah
blah" restarted the answer; it should have been let pass."""

from __future__ import annotations

from openjarvis.server.addressee import classify_addition


class _Engine:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.seen = reply, error, []

    def generate(self, messages, **kwargs):
        self.seen.append((messages, kwargs))
        if self.error:
            raise self.error
        return {"content": self.reply}


def test_each_kind_is_read_from_the_reply():
    for reply, kind in (("noise", "noise"), ("Add.", "add"), ("new\n", "new")):
        assert classify_addition(_Engine(reply), "m", "q", "w") == kind


def test_an_unclear_or_failed_check_sends_the_words_on():
    # The model is then told to ignore them if they are noise (AMEND_NOTE).
    assert classify_addition(_Engine("hmm"), "m", "q", "w") == "add"
    assert (
        classify_addition(_Engine(error=RuntimeError("down")), "m", "q", "w") == "add"
    )


def test_the_check_has_room_to_think():
    engine = _Engine("noise")
    classify_addition(engine, "gpt-5.6-luna", "play F1", "blah blah")
    assert engine.seen[0][1]["max_tokens"] >= 600
    assert "blah blah" in engine.seen[0][0][1].content
