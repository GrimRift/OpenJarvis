"""Tests for web_read — provenance, bounds, and how it fails."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from openjarvis.security import page_access
from openjarvis.tools.web_read import (
    MAX_READS_PER_TURN,
    WebReadTool,
    page_text,
    youtube_text,
)

PAGE = "https://www.clickthecity.com/movies/theaters/sm-city-calamba"


@pytest.fixture(autouse=True)
def _turn():
    """The allowance is process-level, so it leaks between tests without this."""
    page_access.clear()
    # No test reaches the network: the plain read finds nothing unless a
    # test says otherwise, so the browser path is the one under test.
    with patch.object(WebReadTool, "_fetch_static", return_value=None):
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
        with patch.object(WebReadTool, "_render", return_value=(long_text, 1.0, "")):
            with patch("openjarvis.tools.web_read.port_is_open", return_value=True):
                result = _tool().execute(url=PAGE)
        assert result.success is True
        assert result.metadata["truncated"] is True
        assert "truncated at" in result.content
        assert result.metadata["chars"] == 50_000

    def test_an_empty_render_is_a_failure_not_a_blank_answer(self):
        """Returning "" would read as a page that genuinely said nothing."""
        page_access.allow([PAGE])
        with patch.object(WebReadTool, "_render", return_value=("   ", 1.0, "")):
            with patch("openjarvis.tools.web_read.port_is_open", return_value=True):
                result = _tool().execute(url=PAGE)
        assert result.success is False
        assert "no readable text" in result.content

    def test_a_successful_read_counts_against_the_limit(self):
        page_access.allow([PAGE])
        with patch.object(WebReadTool, "_render", return_value=("hello", 1.0, "")):
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


class TestDirectRead:
    """A plain request first; the browser only for a page that needs it."""

    def test_a_served_page_is_read_without_opening_the_browser(self):
        page_access.allow([PAGE])
        with (
            patch.object(
                WebReadTool, "_fetch_static", return_value=("The article.", "Sun facts")
            ),
            patch.object(WebReadTool, "_render") as render,
            patch("openjarvis.tools.web_read.port_is_open") as port,
        ):
            result = _tool().execute(url=PAGE)
        assert result.success is True
        assert result.metadata["mode"] == "direct"
        assert not render.called and not port.called

    def test_a_page_read_is_listed_as_a_source(self):
        # The chat lists sources from tool metadata; a read page had none, so
        # the page an answer rested on was never shown as a reference.
        page_access.allow([PAGE])
        with (
            patch.object(
                WebReadTool, "_render", return_value=("Showtimes", 1.0, "SM Calamba")
            ),
            patch("openjarvis.tools.web_read.port_is_open", return_value=True),
        ):
            result = _tool().execute(url=PAGE)
        assert result.metadata["mode"] == "browser"
        assert result.metadata["sources"] == [
            {"title": "SM Calamba", "url": PAGE, "summary": "Showtimes"}
        ]

    def test_an_untitled_page_is_listed_by_its_site(self):
        page_access.allow([PAGE])
        with (
            patch.object(WebReadTool, "_render", return_value=("text", 1.0, "")),
            patch("openjarvis.tools.web_read.port_is_open", return_value=True),
        ):
            result = _tool().execute(url=PAGE)
        assert result.metadata["sources"][0]["title"] == "www.clickthecity.com"


class TestPageText:
    def test_the_main_part_is_kept_and_the_chrome_dropped(self):
        body = "Fusion in the core. " * 40
        markup = (
            "<html><head><title>The Sun &amp; you</title>"
            "<script>var x = 'not words';</script></head><body>"
            "<nav>Home About</nav><main><h1>Sun</h1><p>" + body + "</p></main>"
            "<footer>Copyright</footer></body></html>"
        )
        text, title = page_text(markup)
        assert title == "The Sun & you"
        assert text.startswith("Sun\nFusion in the core.")
        assert "not words" not in text and "Home About" not in text
        assert "Copyright" not in text

    def test_a_script_shell_has_almost_no_text(self):
        script = "<script>" + "x" * 20000 + "</script>"
        markup = "<html><body><div id=app></div>" + script + "</body></html>"
        text, _ = page_text(markup)
        assert text == ""


class TestYouTube:
    def test_a_video_is_read_from_its_player_data(self):
        markup = (
            '<script>var ytInitialPlayerResponse = {"videoDetails":{'
            '"title":"Opus 5.5 Is Crazy Good","lengthSeconds":"1097",'
            '"shortDescription":"Two new models in the same day?? \\u2728\\nMore.",'
            '"viewCount":"149292"},"microformat":{"ownerChannelName":"Matt Wolfe",'
            '"publishDate":"2026-09-22T15:30:04-07:00"}};</script>'
        )
        text, title = youtube_text("https://www.youtube.com/watch?v=abc", markup)
        assert title == "Opus 5.5 Is Crazy Good - YouTube"
        assert "Channel: Matt Wolfe" in text
        assert "Published: 2026-09-22" in text
        assert "Length: 18:17" in text
        assert "Views: 149,292" in text
        assert "Two new models in the same day?? \u2728\nMore." in text

    def test_other_sites_are_not_youtube(self):
        assert youtube_text("https://example.com/watch", '"title":"x"') is None
