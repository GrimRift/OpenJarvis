"""close_media: YouTube/Netflix tabs by site, Spotify by process."""

from __future__ import annotations

from openjarvis.tools.close_media import CloseMediaTool, host_matches, tabs_to_close


def _page(id_, url, title, type_="page"):
    return {"type": type_, "id": id_, "url": url, "title": title}


TARGETS = [
    _page("1", "https://www.youtube.com/watch?v=x", "A video"),
    _page("2", "https://music.youtube.com/", "YT Music"),
    _page("3", "https://www.netflix.com/watch/1", "A film"),
    _page("4", "https://open.spotify.com/", "Spotify web"),
    _page("5", "https://mail.google.com/", "Gmail"),
    _page("6", "https://notyoutube.com/", "Lookalike"),
    _page("7", "https://www.youtube.com/", "ext", type_="background_page"),
]


def test_host_matching_is_by_domain_not_substring():
    assert host_matches("https://www.youtube.com/x", ("youtube.com",))
    assert host_matches("https://youtu.be/x", ("youtu.be",))
    assert not host_matches("https://notyoutube.com/", ("youtube.com",))
    assert not host_matches("https://youtube.com.evil.example/", ("youtube.com",))


def test_each_target_picks_its_own_tabs_and_only_pages():
    assert [t["id"] for t in tabs_to_close(TARGETS, "youtube")] == ["1", "2"]
    assert [t["id"] for t in tabs_to_close(TARGETS, "netflix")] == ["3"]
    assert [t["id"] for t in tabs_to_close(TARGETS, "spotify")] == ["4"]
    assert [t["id"] for t in tabs_to_close(TARGETS, "all")] == ["1", "2", "3", "4"]


def test_execute_closes_the_tabs_and_quits_spotify(monkeypatch):
    closed = []

    class _Browser:
        def __init__(self, port):
            pass

        def targets(self):
            return TARGETS

        def close_target(self, target_id):
            closed.append(target_id)

    monkeypatch.setattr("openjarvis.tools.cdp.Browser", _Browser)
    monkeypatch.setattr("openjarvis.tools.opera_control.port_is_open", lambda: True)
    monkeypatch.setattr("openjarvis.tools.close_media.quit_spotify", lambda: 1)

    result = CloseMediaTool().execute(what="all")
    assert result.success
    assert closed == ["1", "2", "3", "4"]
    assert "Closed 4 tab(s)" in result.content and "Quit Spotify." in result.content

    only_video = CloseMediaTool().execute(what="youtube")
    assert closed[-2:] == ["1", "2"] and "Spotify" not in only_video.content


def test_no_port_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr("openjarvis.tools.opera_control.port_is_open", lambda: False)
    monkeypatch.setattr("openjarvis.tools.close_media.quit_spotify", lambda: 0)
    result = CloseMediaTool().execute(what="netflix")
    assert result.success and "control port is not open" in result.content
    spotify = CloseMediaTool().execute(what="spotify")
    assert spotify.success and "not running" in spotify.content
    assert "control port" not in spotify.content


def test_rejects_an_unknown_target():
    assert not CloseMediaTool().execute(what="tv").success
