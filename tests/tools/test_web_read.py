"""Tests for web_read — provenance, bounds, and how it fails."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from openjarvis.security import page_access
from openjarvis.tools.web_read import MAX_READS_PER_TURN, WebReadTool

PAGE = "https://www.clickthecity.com/movies/theaters/sm-city-calamba"


@pytest.fixture(autouse=True)
def _turn():
    """The allowance is process-level, so it leaks between tests without this."""
    page_access.clear()
    yield
    page_access.clear()


def _tool():
    return WebReadTool()


class TestProvenance:
    """Only a page the user named, or one a search returned.

    The tool receives a bare string and cannot tell where it came from, so the
    check lives in the turn's allowance rather than in the tool description.
    A link inside an email or inside a page just read is precisely the one
    that must not be followed -- that is how a page gets to choose what Sage
    fetches next.
    """

    def test_a_url_from_nowhere_is_refused(self):
        result = _tool().execute(url="https://evil.example/collect")
        assert result.success is False
        assert "paste it to me" in result.content

    def test_a_url_the_user_typed_is_allowed_through(self):
        page_access.set_turn(f"read {PAGE} for me")
        with patch(
            "openjarvis.tools.web_read.port_is_open", return_value=False
        ) as port:
            result = _tool().execute(url=PAGE)
        # Refused for a reachable-browser reason, not a provenance one.
        assert port.called
        assert "paste it to me" not in result.content

    def test_a_url_a_search_returned_is_allowed_through(self):
        page_access.allow([PAGE])
        with patch("openjarvis.tools.web_read.port_is_open", return_value=False):
            result = _tool().execute(url=PAGE)
        assert "paste it to me" not in result.content

    def test_tracking_parameters_do_not_defeat_the_match(self):
        """A search result and the same link typed rarely match byte for byte."""
        page_access.allow([PAGE])
        with patch("openjarvis.tools.web_read.port_is_open", return_value=False):
            result = _tool().execute(url=f"{PAGE}/?utm_source=news#top")
        assert "paste it to me" not in result.content

    def test_a_missing_url_is_refused_before_anything_opens(self):
        with patch("openjarvis.tools.web_read.opera_session") as session:
            result = _tool().execute(url="")
        assert result.success is False
        assert not session.called


class TestBounds:
    def test_the_per_turn_read_limit_holds(self):
        page_access.allow([PAGE])
        for _ in range(MAX_READS_PER_TURN):
            page_access.note_read()
        result = _tool().execute(url=PAGE)
        assert result.success is False
        assert "limit" in result.content

    def test_an_unreachable_browser_explains_itself(self):
        """A dead CDP port must not read as "the page had nothing"."""
        page_access.allow([PAGE])
        with patch("openjarvis.tools.web_read.port_is_open", return_value=False):
            result = _tool().execute(url=PAGE)
        assert result.success is False
        assert "Opera" in result.content

    def test_long_text_is_truncated_and_says_so(self):
        page_access.allow([PAGE])
        long_text = "x" * 50_000
        with patch.object(WebReadTool, "_render", return_value=(long_text, 1.0)):
            with patch("openjarvis.tools.web_read.port_is_open", return_value=True):
                result = _tool().execute(url=PAGE)
        assert result.success is True
        assert result.metadata["truncated"] is True
        assert "truncated at" in result.content
        assert result.metadata["chars"] == 50_000

    def test_an_empty_render_is_a_failure_not_a_blank_answer(self):
        """Returning "" would read as a page that genuinely said nothing."""
        page_access.allow([PAGE])
        with patch.object(WebReadTool, "_render", return_value=("   ", 1.0)):
            with patch("openjarvis.tools.web_read.port_is_open", return_value=True):
                result = _tool().execute(url=PAGE)
        assert result.success is False
        assert "no readable text" in result.content

    def test_a_successful_read_counts_against_the_limit(self):
        page_access.allow([PAGE])
        with patch.object(WebReadTool, "_render", return_value=("hello", 1.0)):
            with patch("openjarvis.tools.web_read.port_is_open", return_value=True):
                _tool().execute(url=PAGE)
        assert page_access.reads_used() == 1

    def test_a_refused_read_does_not_count(self):
        """A provenance refusal must not spend the turn's budget."""
        _tool().execute(url="https://evil.example/collect")
        assert page_access.reads_used() == 0


class TestNormalisation:
    def test_www_and_trailing_slash_are_the_same_page(self):
        assert page_access.normalise("https://www.example.com/a/") == (
            page_access.normalise("https://example.com/a")
        )

    def test_sentence_punctuation_is_not_part_of_the_address(self):
        found = page_access.urls_in("see https://example.com/page.")
        assert page_access.normalise("https://example.com/page") in found

    def test_a_bare_word_is_not_a_url(self):
        assert page_access.urls_in("read the page") == set()
