"""Tests for audio ducking around Sage's server-side voice.

The point is the restore: lowering a film is easy, and leaving it lowered
after Sage has finished is the bug the user would notice every time. Each
session is put back to exactly its own level, and nothing here can stop
the speech that it wraps.
"""

from __future__ import annotations

from unittest.mock import patch

from openjarvis.speech import ducking


class _Volume:
    def __init__(self, level: float) -> None:
        self.level = level
        self.history = [level]

    def GetMasterVolume(self) -> float:  # noqa: N802 -- COM naming
        return self.level

    def SetMasterVolume(self, level: float, _ctx) -> None:  # noqa: N802
        self.level = level
        self.history.append(level)


class _Session:
    def __init__(self, level: float) -> None:
        self.SimpleAudioVolume = _Volume(level)


def _no_sleep():
    return patch("openjarvis.speech.ducking.time.sleep")


class TestDucked:
    def test_lowers_to_the_fraction_and_restores_exactly(self) -> None:
        film = _Session(0.8)
        music = _Session(0.5)
        with (
            patch.object(
                ducking,
                "_sessions",
                return_value=[("vlc.exe", film), ("Spotify.exe", music)],
            ),
            _no_sleep(),
        ):
            with ducking.ducked(level=0.35, fade_ms=0) as names:
                assert names == ["vlc.exe", "Spotify.exe"]
                assert abs(film.SimpleAudioVolume.level - 0.28) < 1e-9
                assert abs(music.SimpleAudioVolume.level - 0.175) < 1e-9
        assert film.SimpleAudioVolume.level == 0.8
        assert music.SimpleAudioVolume.level == 0.5

    def test_fades_in_steps_rather_than_jumping(self) -> None:
        film = _Session(1.0)
        with (
            patch.object(ducking, "_sessions", return_value=[("vlc.exe", film)]),
            _no_sleep(),
        ):
            with ducking.ducked(level=0.35, fade_ms=300):
                pass
        down = film.SimpleAudioVolume.history[1 : ducking._FADE_STEPS + 1]
        assert down == sorted(down, reverse=True) and len(set(down)) == len(down)
        assert film.SimpleAudioVolume.level == 1.0

    def test_a_silent_app_is_left_alone(self) -> None:
        muted = _Session(0.0)
        with (
            patch.object(ducking, "_sessions", return_value=[("x.exe", muted)]),
            _no_sleep(),
        ):
            with ducking.ducked() as names:
                assert names == []
        assert muted.SimpleAudioVolume.history == [0.0]

    def test_the_block_runs_even_when_audio_is_unavailable(self) -> None:
        with patch.object(ducking, "_sessions", side_effect=RuntimeError("no COM")):
            ran = False
            with ducking.ducked() as names:
                ran = True
            assert ran and names == []

    def test_restores_even_if_the_block_raises(self) -> None:
        film = _Session(0.6)
        with (
            patch.object(ducking, "_sessions", return_value=[("vlc.exe", film)]),
            _no_sleep(),
        ):
            try:
                with ducking.ducked(fade_ms=0):
                    raise RuntimeError("player died")
            except RuntimeError:
                pass
        assert film.SimpleAudioVolume.level == 0.6

    def test_off_windows_there_is_nothing_to_duck(self) -> None:
        with patch.object(ducking.sys, "platform", "linux"):
            assert ducking._sessions() == []
