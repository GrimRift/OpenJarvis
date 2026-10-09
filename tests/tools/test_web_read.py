"""Tests for web_read — provenance, bounds, and how it fails."""

from __future__ import annotations

import importlib
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
            "openjarvis.tools.opera_control.port_is_open", return_value=False
        ) as port:
            result = _tool().execute(url=PAGE)
        # Refused for a reachable-browser reason, not a provenance one.
        assert port.called
        assert "paste it to me" not in result.content

    def test_a_url_a_search_returned_is_allowed_through(self):
        page_access.allow([PAGE])
        with patch("openjarvis.tools.opera_control.port_is_open", return_value=False):
            result = _tool().execute(url=PAGE)
        assert "paste it to me" not in result.content

    def test_tracking_parameters_do_not_defeat_the_match(self):
        """A search result and the same link typed rarely match byte for byte."""
        page_access.allow([PAGE])
        with patch("openjarvis.tools.opera_control.port_is_open", return_value=False):
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

    def test_a_new_message_gets_a_fresh_read_budget(self):
        """The first answer spent the reads, and "read the reddit post" a
        minute later was refused: the reading limit (29 September)."""
        page_access.allow([PAGE])
        for _ in range(MAX_READS_PER_TURN):
            page_access.note_read()
        page_access.set_turn("read the reddit post more thoroughly")
        assert page_access.reads_used() == 0
        assert page_access.is_allowed(PAGE)

    def test_the_same_page_twice_is_not_read_again(self):
        """One answer read one page three times, a model round each."""
        page_access.allow([PAGE])
        with patch.object(WebReadTool, "_render", return_value=("hello", 1.0, "")):
            with patch("openjarvis.tools.web_read.ensure_opera", return_value=None):
                _tool().execute(url=PAGE)
                with patch.object(WebReadTool, "_render") as render_again:
                    again = _tool().execute(url=PAGE + "/")
        assert again.success is True
        assert "Already read" in again.content
        assert not render_again.called
        assert page_access.reads_used() == 1

    def test_a_refused_url_lists_the_pages_that_can_be_read(self):
        page_access.allow([PAGE])
        result = _tool().execute(url="https://elsewhere.example/linked-from-page")
        assert result.success is False
        assert "Pages you can read now" in result.content
        assert "clickthecity.com/movies/theaters/sm-city-calamba" in result.content

    def test_reads_requested_together_cannot_exceed_the_limit(self):
        """Reads asked for in one response run at the same time; each must
        take its slot before any finishes, or all pass the count."""
        from concurrent.futures import ThreadPoolExecutor
        from contextvars import copy_context

        pages = [f"https://news.example/{n}" for n in range(MAX_READS_PER_TURN + 2)]
        page_access.allow(pages)

        def slow_render(*_args, **_kwargs):
            import time

            time.sleep(0.2)
            return ("text", 0.2, "")

        with patch.object(WebReadTool, "_render", side_effect=slow_render):
            with patch("openjarvis.tools.web_read.ensure_opera", return_value=None):
                with ThreadPoolExecutor(len(pages)) as pool:
                    # Match the synchronous agent's worker boundary: each
                    # worker receives a copy pointing to the same turn state.
                    futures = [
                        pool.submit(copy_context().run, _tool().execute, url=url)
                        for url in pages
                    ]
                    results = [future.result() for future in futures]
        assert sum(result.success for result in results) == MAX_READS_PER_TURN

    def test_an_unreachable_browser_explains_itself(self):
        """A dead CDP port must not read as "the page had nothing"."""
        page_access.allow([PAGE])
        with patch("openjarvis.tools.opera_control.port_is_open", return_value=False):
            result = _tool().execute(url=PAGE)
        assert result.success is False
        assert "Opera" in result.content

    def test_long_text_is_truncated_and_says_so(self):
        page_access.allow([PAGE])
        long_text = "x" * 50_000
        with patch.object(WebReadTool, "_render", return_value=(long_text, 1.0, "")):
            with patch("openjarvis.tools.web_read.ensure_opera", return_value=None):
                result = _tool().execute(url=PAGE)
        assert result.success is True
        assert result.metadata["truncated"] is True
        assert "truncated at" in result.content
        assert result.metadata["chars"] == 50_000

    def test_an_empty_render_is_a_failure_not_a_blank_answer(self):
        """Returning "" would read as a page that genuinely said nothing."""
        page_access.allow([PAGE])
        with patch.object(WebReadTool, "_render", return_value=("   ", 1.0, "")):
            with patch("openjarvis.tools.web_read.ensure_opera", return_value=None):
                result = _tool().execute(url=PAGE)
        assert result.success is False
        assert "no readable text" in result.content

    def test_a_successful_read_counts_against_the_limit(self):
        page_access.allow([PAGE])
        with patch.object(WebReadTool, "_render", return_value=("hello", 1.0, "")):
            with patch("openjarvis.tools.web_read.ensure_opera", return_value=None):
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
            patch("openjarvis.tools.web_read.ensure_opera") as port,
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
            patch("openjarvis.tools.web_read.ensure_opera", return_value=None),
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
            patch("openjarvis.tools.web_read.ensure_opera", return_value=None),
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


class TestSourceSummary:
    """24 September: a source card read "Skip to content (opens in a new
    tab)(opens in a new tab)... Sign In Subscribe All videos Share"."""

    def test_the_pages_own_description_is_used(self):
        from openjarvis.tools.web_read import page_description, source_summary

        markup = (
            '<head><meta property="og:description" '
            'content="Hamilton retired on Lap 7 with a brake problem."></head>'
        )
        description = page_description(markup)
        assert description == "Hamilton retired on Lap 7 with a brake problem."
        assert source_summary(description, "Skip to content ...") == description

    def test_without_one_the_chrome_is_taken_out(self):
        from openjarvis.tools.web_read import source_summary

        body = (
            "Skip to content (opens in a new tab)(opens in a new tab) Sign In "
            "Subscribe All videos Share 2026 Spanish Grand Prix: Hamilton forced "
            "to retire"
        )
        summary = source_summary("", body)
        assert summary.startswith("2026 Spanish Grand Prix")


def _fake_extract(text):
    """A tavily module whose extract returns *text* for any URL."""
    import sys
    from types import ModuleType
    from unittest.mock import MagicMock

    module = ModuleType("tavily")
    client = MagicMock()
    client.extract.side_effect = lambda urls, **kw: {
        "results": [{"url": urls[0], "raw_content": text, "title": "Article"}]
    }
    module.TavilyClient = MagicMock(return_value=client)
    return patch.dict(sys.modules, {"tavily": module}), client


class TestProviderRead:
    """6 October: the search provider's reader goes before the browser, for
    pages a search returned only (the user's rule)."""

    ARTICLE = "The LTO revoked the driver's license for life. " * 60

    @pytest.fixture(autouse=True)
    def _key(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "key")

    def test_a_search_result_is_read_by_the_provider(self):
        page_access.set_turn("pasig suv news")
        page_access.allow_search_results([PAGE])
        fake, client = _fake_extract(self.ARTICLE)
        with fake, patch.object(WebReadTool, "_render") as render:
            result = _tool().execute(url=PAGE)
        assert result.success is True
        assert result.metadata["mode"] == "extract"
        assert "revoked" in result.content
        render.assert_not_called()
        assert client.extract.call_args.kwargs["urls"] == [PAGE]

    def test_a_url_the_user_typed_is_never_sent(self):
        page_access.set_turn(f"read {PAGE}")
        # Typed AND returned by a search: still the user's, still not sent.
        page_access.allow_search_results([PAGE])
        fake, client = _fake_extract(self.ARTICLE)
        with (
            fake,
            patch.object(WebReadTool, "_render", return_value=("Page", 1.0, "")),
            patch("openjarvis.tools.web_read.ensure_opera", return_value=None),
        ):
            result = _tool().execute(url=PAGE)
        client.extract.assert_not_called()
        assert result.metadata["mode"] == "browser"

    def test_a_shell_from_the_provider_goes_on_to_the_browser(self):
        page_access.set_turn("showtimes at sm calamba")
        page_access.allow_search_results([PAGE])
        fake, _ = _fake_extract("Loading... Enable JavaScript.")
        with (
            fake,
            patch.object(WebReadTool, "_render", return_value=("Showtimes", 1.0, "")),
            patch("openjarvis.tools.web_read.ensure_opera", return_value=None),
        ):
            result = _tool().execute(url=PAGE)
        assert result.metadata["mode"] == "browser"

    def test_a_stuck_browser_is_given_up_on(self, monkeypatch):
        """One NYT read held its turn 51 s when Opera stopped answering."""
        import time

        import openjarvis.tools.web_read as web_read

        monkeypatch.setattr(web_read, "RENDER_CAP_SECONDS", 0.2)
        page_access.allow([PAGE])

        def stuck(*_args, **_kwargs):
            time.sleep(1.0)
            return ("late", 1.0, "")

        started = time.monotonic()
        with (
            patch.object(WebReadTool, "_render", side_effect=stuck),
            patch("openjarvis.tools.web_read.ensure_opera", return_value=None),
        ):
            result = _tool().execute(url=PAGE)
        assert time.monotonic() - started < 0.8
        assert result.success is False
        assert "skipped" in result.content


def test_a_bot_check_page_is_reported_not_read():
    """mb.com.ph rendered Cloudflare's 263-character check (6 October)."""
    page_access.allow([PAGE])
    check = (
        "mb.com.ph Performing security verification This website uses a "
        "security service to protect against malicious bots."
    )
    with (
        patch.object(WebReadTool, "_render", return_value=(check, 1.0, "")),
        patch("openjarvis.tools.web_read.ensure_opera", return_value=None),
    ):
        result = _tool().execute(url=PAGE)
    assert result.success is False
    assert "bot check" in result.content


def test_a_long_article_mentioning_a_captcha_is_still_read():
    page_access.allow([PAGE])
    article = "Are you a robot? the quiz asked. " + "Real reporting. " * 200
    with (
        patch.object(WebReadTool, "_render", return_value=(article, 1.0, "")),
        patch("openjarvis.tools.web_read.ensure_opera", return_value=None),
    ):
        assert _tool().execute(url=PAGE).success is True


class TestReadAhead:
    """9 October: the top search results are read while the model decides,
    so the read it asks for is already there.

    The class is taken from the module each time: test_tool_registration
    reloads every tool module, and the read-ahead builds its reader from
    the module's current class, not the one imported at the top here."""

    ARTICLE = ("AirPods 5 last up to 5 hours with ANC. " * 60, "AirPods 5")

    def test_a_read_ahead_page_is_not_fetched_again(self, monkeypatch):
        web_read = importlib.import_module("openjarvis.tools.web_read")

        monkeypatch.setattr(web_read, "PREFETCH_TOP", 3)
        page_access.set_turn("airpods 5 vs airpods 4")
        page_access.allow_search_results([PAGE])
        with patch.object(
            web_read.WebReadTool, "_fetch_static", return_value=self.ARTICLE
        ) as fetch:
            web_read.prefetch([PAGE])
            result = web_read.WebReadTool().execute(url=PAGE)
        assert result.success is True
        assert result.metadata["mode"] == "direct"
        assert "5 hours" in result.content
        assert fetch.call_count == 1

    def test_read_ahead_never_skips_the_checks(self, monkeypatch):
        web_read = importlib.import_module("openjarvis.tools.web_read")

        monkeypatch.setattr(web_read, "PREFETCH_TOP", 3)
        page_access.set_turn("airpods")
        with patch.object(
            web_read.WebReadTool, "_fetch_static", return_value=self.ARTICLE
        ):
            web_read.prefetch([PAGE])
            result = web_read.WebReadTool().execute(url=PAGE)
        # Read ahead, but never returned by a search: still refused.
        assert result.success is False
        assert "only open a page you named yourself" in result.content

    def test_only_the_top_results_are_read_ahead(self, monkeypatch):
        web_read = importlib.import_module("openjarvis.tools.web_read")

        monkeypatch.setattr(web_read, "PREFETCH_TOP", 2)
        urls = [f"https://example.org/{i}" for i in range(5)]
        with patch.object(
            web_read.WebReadTool, "_fetch_static", return_value=None
        ) as fetch:
            web_read.prefetch(urls)
            for url in urls:
                web_read._take_prefetched(url)
        assert fetch.call_count == 2


class TestSeveralAtOnce:
    """9 October: read one per call, two pages took two model rounds."""

    ARTICLE = ("Up to 5 hours with ANC on a single charge. " * 60, "AirPods")
    OTHER = "https://www.apple.com/airpods-5/"

    def test_several_pages_are_read_in_one_call(self):
        page_access.set_turn("airpods 5 vs airpods 4")
        page_access.allow_search_results([PAGE, self.OTHER])
        with patch.object(WebReadTool, "_fetch_static", return_value=self.ARTICLE):
            result = _tool().execute(urls=[PAGE, self.OTHER])
        assert result.success is True
        assert result.metadata["pages_read"] == 2
        assert f"## {PAGE}" in result.content and f"## {self.OTHER}" in result.content
        assert [s["url"] for s in result.metadata["sources"]] == [PAGE, self.OTHER]

    def test_each_page_still_needs_its_own_permission(self):
        page_access.set_turn("airpods")
        page_access.allow_search_results([PAGE])
        with patch.object(WebReadTool, "_fetch_static", return_value=self.ARTICLE):
            result = _tool().execute(urls=[PAGE, "https://elsewhere.example/x"])
        assert result.success is True
        assert result.metadata["pages_read"] == 1
        assert "Not read: I can only open a page you named yourself" in result.content

    def test_the_budget_counts_every_page(self):
        page_access.set_turn("airpods")
        pages = [f"https://example.org/{i}" for i in range(MAX_READS_PER_TURN + 1)]
        page_access.allow_search_results(pages)
        with patch.object(WebReadTool, "_fetch_static", return_value=self.ARTICLE):
            result = _tool().execute(urls=pages)
        assert result.metadata["pages_read"] == MAX_READS_PER_TURN
        assert result.content.count("Not read:") == 1

    def test_a_single_url_reads_as_before(self):
        page_access.set_turn("airpods")
        page_access.allow_search_results([PAGE])
        with patch.object(WebReadTool, "_fetch_static", return_value=self.ARTICLE):
            result = _tool().execute(url=PAGE)
        assert result.metadata["mode"] == "direct"
