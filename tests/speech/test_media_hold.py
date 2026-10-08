"""The media hold turns other apps down while the user talks, then back up;
the user's own "pause the video" pauses only what they named."""

from __future__ import annotations

import pytest

from openjarvis.speech import ducking, media_hold


class _Volume:
    def __init__(self, level: float) -> None:
        self.level = level

    def GetMasterVolume(self):  # noqa: N802 -- the COM name
        return self.level

    def SetMasterVolume(self, level, _ctx):  # noqa: N802
        self.level = level


class _Proc:
    def __init__(self, pid: int) -> None:
        self.pid = pid


class _AudioSession:
    def __init__(self, pid: int, level: float = 1.0) -> None:
        self.Process = _Proc(pid)
        self.SimpleAudioVolume = _Volume(level)


@pytest.fixture
def apps(monkeypatch):
    opera = _AudioSession(1, 0.8)
    spotify = _AudioSession(2, 1.0)
    sessions = [("opera.exe", opera), ("Spotify.exe", spotify)]
    monkeypatch.setattr(ducking, "_sessions", lambda active_only=True: sessions)
    monkeypatch.setattr(ducking, "_remember", lambda _levels: None)
    monkeypatch.setattr(ducking, "_forget", lambda: None)
    monkeypatch.setattr(ducking, "_FADE_STEPS", 1)
    monkeypatch.setattr(media_hold, "_arm_timer", lambda: None)
    media_hold.release()
    yield opera, spotify
    media_hold.release()


def test_duck_turns_every_app_down_and_release_puts_it_back(apps):
    opera, spotify = apps
    assert media_hold.duck() == ["opera.exe", "Spotify.exe"]
    assert opera.SimpleAudioVolume.level == pytest.approx(0.8 * media_hold.LEVEL)
    assert spotify.SimpleAudioVolume.level == pytest.approx(media_hold.LEVEL)
    assert media_hold.release() == ["opera.exe", "Spotify.exe"]
    assert opera.SimpleAudioVolume.level == pytest.approx(0.8)
    assert spotify.SimpleAudioVolume.level == pytest.approx(1.0)


def test_a_second_duck_does_not_go_lower(apps):
    """The wake word ducks, then Sage starts speaking: one step down only."""
    opera, _ = apps
    media_hold.duck()
    assert media_hold.duck() == []
    assert opera.SimpleAudioVolume.level == pytest.approx(0.8 * media_hold.LEVEL)
    media_hold.release()
    assert opera.SimpleAudioVolume.level == pytest.approx(0.8)


def test_release_with_nothing_held_touches_nothing(apps):
    opera, _ = apps
    assert media_hold.release() == []
    assert opera.SimpleAudioVolume.level == pytest.approx(0.8)


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


@pytest.fixture
def players(monkeypatch):
    spotify = _Session("SpotifyAB.SpotifyMusic!Spotify", playing=True)
    opera = _Session("OperaSoftware.OperaGXWebBrowser.1", playing=True)

    class _Manager:
        def get_sessions(self):
            return [spotify, opera]

    async def fake_manager():
        return _Manager()

    monkeypatch.setattr(media_hold, "_manager", fake_manager)
    monkeypatch.setattr(media_hold.sys, "platform", "win32")
    media_hold._user_paused.clear()
    return spotify, opera


def test_pause_the_video_leaves_the_music_playing(players):
    spotify, opera = players
    assert media_hold.pause_for_user("video") == [opera.source_app_user_model_id]
    assert spotify.playing and not opera.playing
    assert media_hold.resume_for_user("video") == [opera.source_app_user_model_id]
    assert opera.playing


def test_resume_brings_back_only_what_the_user_paused(players):
    spotify, opera = players
    spotify.playing = False  # paused in Spotify itself, earlier
    media_hold.pause_for_user("video")
    media_hold.resume_for_user("all")
    assert opera.playing and not spotify.playing


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
    assert media_hold.pause_for_user() == []
    assert media_hold.resume_for_user() == []


def test_sage_app_is_never_ducked():
    from openjarvis.speech.ducking import _is_sage_app

    class _P:
        def __init__(self, name, parents=()):
            self._name = name
            self._parents = parents

        def name(self):
            return self._name

        def parents(self):
            return list(self._parents)

    app = _P("sage-desktop.exe")
    assert _is_sage_app(_P("msedgewebview2.exe", [app, _P("explorer.exe")]))
    assert not _is_sage_app(_P("opera.exe", [_P("explorer.exe")]))


def test_hey_sage_pauses_the_video_and_turns_the_music_down(apps, players):
    # 8 October: at 10% a Kurzgesagt narration still became the request.
    opera_volume, spotify_volume = apps
    spotify, opera = players
    media_hold.duck(pause_video=True)
    assert not opera.playing and spotify.playing
    assert spotify_volume.SimpleAudioVolume.level == pytest.approx(media_hold.LEVEL)
    media_hold.release()
    assert opera.playing
    assert spotify_volume.SimpleAudioVolume.level == pytest.approx(1.0)


def test_a_video_the_user_asks_to_pause_stays_paused(apps, players):
    _opera_volume, _spotify_volume = apps
    _spotify, opera = players
    media_hold.duck(pause_video=True)
    media_hold.pause_for_user("video")  # "pause the video", said to Sage
    media_hold.release()
    assert not opera.playing


def test_an_app_that_starts_during_the_hold_is_turned_down(apps, monkeypatch):
    # An ad at full volume in a video Sage had just started, while it was
    # still answering (8 October).
    import time

    opera, spotify = apps
    monkeypatch.setattr(media_hold, "WATCH_SECONDS", 0.05)
    sessions = [("Spotify.exe", spotify)]
    monkeypatch.setattr(ducking, "_sessions", lambda active_only=True: sessions)
    media_hold.duck()
    sessions.append(("opera.exe", opera))
    time.sleep(0.3)
    assert opera.SimpleAudioVolume.level == pytest.approx(0.8 * media_hold.LEVEL)
    media_hold.release()
    assert opera.SimpleAudioVolume.level == pytest.approx(0.8)
