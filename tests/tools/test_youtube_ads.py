"""Every skippable YouTube ad is skipped, in any YouTube tab (youtube_ads)."""

from __future__ import annotations

from openjarvis.tools.youtube_ads import check_once


class _Page:
    def __init__(self, ad: bool, skip: bool) -> None:
        self.ad, self.skip = ad, skip
        self.clicks = 0
        self.closed = False

    def evaluate(self, _js):
        return {"ad": self.ad, "skip": self.skip}

    def click(self, _selector):
        self.clicks += 1
        return True

    def close(self):
        self.closed = True


class _Browser:
    def __init__(self, tabs) -> None:
        self.tabs = tabs  # id -> (url, page)

    def page_targets(self):
        return [
            {"id": tid, "url": url, "title": tid}
            for tid, (url, _) in self.tabs.items()
        ]

    def attach(self, target):
        return self.tabs[target["id"]][1]


def test_skips_a_mid_roll_once_the_button_shows():
    page = _Page(ad=True, skip=False)
    browser = _Browser({"a": ("https://www.youtube.com/watch?v=x", page)})
    pages, skipping = {}, set()
    assert check_once(browser, pages, skipping) == 0  # no Skip yet: wait
    page.skip = True
    assert check_once(browser, pages, skipping) == 1
    assert page.clicks == 1


def test_an_unskippable_ad_is_left_alone():
    page = _Page(ad=True, skip=False)
    browser = _Browser({"a": ("https://www.youtube.com/watch?v=x", page)})
    for _ in range(5):
        check_once(browser, {}, set())
    assert page.clicks == 0


def test_only_youtube_watch_tabs_and_closed_tabs_are_dropped():
    yt = _Page(ad=True, skip=True)
    other = _Page(ad=True, skip=True)
    browser = _Browser(
        {
            "yt": ("https://www.youtube.com/watch?v=x", yt),
            "news": ("https://example.com/", other),
        }
    )
    pages, skipping = {}, set()
    check_once(browser, pages, skipping)
    assert yt.clicks == 1 and other.clicks == 0
    browser.tabs.pop("yt")
    check_once(browser, pages, skipping)
    assert yt.closed and "yt" not in pages


def test_a_stale_tab_is_reattached():
    class _Stale(_Page):
        def evaluate(self, _js):
            raise RuntimeError("socket closed")

    stale = _Stale(ad=True, skip=True)
    browser = _Browser({"a": ("https://www.youtube.com/watch?v=x", stale)})
    pages = {}
    check_once(browser, pages, set())
    assert "a" not in pages and stale.closed
