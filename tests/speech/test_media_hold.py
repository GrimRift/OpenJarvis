"""The media hold: pause on the user's voice, turn down for Sage's, give back."""

from __future__ import annotations

import pytest

from openjarvis.speech import media_hold


class _Session:
    def __init__(self, app: str, playing: bool) -> None:
        self.source_app_user_model_id = app
        self.playing = playing

    def get_playback_info(self):
        status = 4 if self.playing else 5

        class _Info:
            playback_status = status

        return _Info()

    async def try_pause_async(self):
        self.playing = False
        return True

    async def try_play_async(self):
        self.playing = True
        return True


class _Manager:
    def __init__(self, sessions) -> None:
        self.sessions = sessions

    def get_sessions(self):
        return self.sessions


@pytest.fixture
def media(monkeypatch):
    spotify = _Session("SpotifyAB.SpotifyMusic!Spotify", playing=True)
    opera = _Session("OperaSoftware.OperaGXWebBrowser.1", playing=True)
    manager = _Manager([spotify, opera])

    async def fake_manager():
        return manager

    monkeypatch.setattr(media_hold, "_manager", fake_manager)
    monkeypatch.setattr(media_hold.sys, "platform", "win32")
    monkeypatch.setattr(media_hold, "_arm_timer", lambda: None)
    media_hold.release()
    yield spotify, opera
    media_hold.release()


def test_pause_then_release_resumes_what_was_playing(media):
    spotify, opera = media
    assert sorted(media_hold.pause()) == sorted(
        [spotify.source_app_user_model_id, opera.source_app_user_model_id]
    )
    assert not spotify.playing and not opera.playing
    out = media_hold.release()
    assert spotify.playing and opera.playing
    assert len(out["resumed"]) == 2


def test_something_paused_before_the_hold_is_not_resumed(media):
    spotify, opera = media
    spotify.playing = False  # the user had paused it themselves
    media_hold.pause()
    media_hold.release()
    assert not spotify.playing
    assert opera.playing


def test_a_media_request_keeps_everything_paused(media):
    spotify, opera = media
    media_hold.pause()
    media_hold.keep()  # e.g. youtube_play opened a new video
    media_hold.release()
    assert not spotify.playing and not opera.playing


def test_pause_the_video_keeps_only_the_video(media):
    spotify, opera = media
    media_hold.pause()  # the wake word paused both
    chosen = media_hold.pause_for_user("video")
    assert chosen == [opera.source_app_user_model_id]
    media_hold.release()
    assert spotify.playing
    assert not opera.playing
    assert media_hold.resume_for_user("video") == [opera.source_app_user_model_id]
    assert opera.playing


@pytest.mark.parametrize(
    "app, kind",
    [
        ("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", "music"),
        ("OperaSoftware.OperaGXWebBrowser.1768011669", "video"),
        ("Microsoft.ZuneVideo!App", "other"),
    ],
)
def test_kind_of(app, kind):
    assert media_hold.kind_of(app) == kind


def test_nothing_happens_off_windows(monkeypatch):
    monkeypatch.setattr(media_hold.sys, "platform", "linux")
    assert media_hold.pause() == []
    assert media_hold.pause_for_user() == []


def test_sage_app_is_never_ducked():
    from openjarvis.speech.ducking import _is_sage_app

    class _Proc:
        def __init__(self, name, parents=()):
            self._name = name
            self._parents = parents

        def name(self):
            return self._name

        def parents(self):
            return list(self._parents)

    app = _Proc("sage-desktop.exe")
    assert _is_sage_app(_Proc("msedgewebview2.exe", [app, _Proc("explorer.exe")]))
    assert not _is_sage_app(_Proc("opera.exe", [_Proc("explorer.exe")]))
