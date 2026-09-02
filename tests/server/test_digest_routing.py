"""Which briefing a question gets: the stored one, or a fresh build.

Rebuilding waits on Teams and two mailboxes, so the default is the text the
05:00 cron already wrote. Asking for the *latest* is what pays that cost.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from openjarvis.server import routes
from openjarvis.server.routes import (
    _DIGEST_FRESH_RE,
    _DIGEST_INTENT_RE,
    _digest_age,
    _stored_digest,
)


def _route(query: str) -> str:
    intent = bool(_DIGEST_INTENT_RE.search(query))
    fresh = bool(_DIGEST_FRESH_RE.search(query))
    if not intent:
        return "chat"
    return "rebuild" if fresh else "cached"


class TestWhichBriefingIsServed:
    @pytest.mark.parametrize(
        "query",
        ["morning digest", "good morning", "my briefing", "daily briefing"],
    )
    def test_asking_plainly_serves_the_stored_one(self, query):
        assert _route(query) == "cached"

    @pytest.mark.parametrize(
        "query",
        [
            "give me the latest morning digest",
            "give me the latest briefing",
            "new morning digest",
            "regenerate my daily briefing",
            "morning digest right now",
            "rebuild the briefing",
        ],
    )
    def test_asking_for_the_latest_rebuilds(self, query):
        assert _route(query) == "rebuild"

    @pytest.mark.parametrize(
        "query", ["what is the weather", "play a song", "brief me on the news"]
    )
    def test_everything_else_is_ordinary_chat(self, query):
        assert _route(query) == "chat"

    def test_a_bare_briefing_still_counts(self):
        """"give me the latest briefing" is how the user actually asks, and
        requiring "daily" or "morning" in front of it sent that phrasing to
        the ordinary chat agent."""
        assert _DIGEST_INTENT_RE.search("the latest briefing") is not None


class TestSayingHowOldItIs:
    """A briefing served silently reads as current."""

    def test_today(self):
        assert "today" in _digest_age(datetime.now())

    def test_yesterday(self):
        assert "yesterday" in _digest_age(datetime.now() - timedelta(days=1))

    def test_older_says_how_many_days(self):
        assert "3 days ago" in _digest_age(datetime.now() - timedelta(days=3))

    def test_a_missing_timestamp_is_not_guessed_at(self):
        assert _digest_age(None) == "Age unknown"


class TestReadingTheStoredBriefing:
    class _Artifact:
        def __init__(self, text, when=None):
            self.text = text
            self.generated_at = when or datetime.now()

    def _store(self, monkeypatch, artifact):
        class _Store:
            def __init__(self, *a, **k):
                pass

            def get_latest(self):
                return artifact

            def close(self):
                self.closed = True

        monkeypatch.setattr("openjarvis.agents.digest_store.DigestStore", _Store)

    def test_it_returns_the_text_with_an_age_note(self, monkeypatch):
        self._store(monkeypatch, self._Artifact("Sir, your briefing."))
        stored = _stored_digest()
        assert "Sir, your briefing." in stored
        assert "Briefing from" in stored
        assert "latest briefing" in stored

    def test_nothing_stored_yields_nothing(self, monkeypatch):
        self._store(monkeypatch, None)
        assert _stored_digest() == ""

    def test_an_empty_briefing_is_not_served(self, monkeypatch):
        """Serving a blank one would read as "you have nothing waiting"."""
        self._store(monkeypatch, self._Artifact("   "))
        assert _stored_digest() == ""

    def test_a_broken_store_falls_through_to_a_rebuild(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("database is locked")

        monkeypatch.setattr("openjarvis.agents.digest_store.DigestStore", _boom)
        assert _stored_digest() == ""


class TestTheCannedReplyShape:
    def test_non_streaming_carries_the_content(self):
        reply = routes._canned_reply("hello", "gpt-test", stream=False)
        assert reply.choices[0].message.content == "hello"

    def test_streaming_returns_an_event_stream(self):
        reply = routes._canned_reply("hello", "gpt-test", stream=True)
        assert reply.media_type == "text/event-stream"
