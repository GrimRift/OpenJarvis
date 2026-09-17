"""Tests for audio ducking around Sage's server-side voice.

The point is the restore: lowering a film is easy, and leaving it lowered
after Sage has finished is the bug the user would notice every time. Each
session is put back to exactly its own level, and nothing here can stop
the speech that it wraps.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from openjarvis.speech import ducking


@pytest.fixture(autouse=True)
def _nobody_speaking(monkeypatch):
    """The echo tail after a held ``speaking()`` would leave the next test's
    listeners deaf for a second; every test starts and ends with silence."""
    from openjarvis.speech import player

    monkeypatch.setattr(player, "_speakers", 0)
    monkeypatch.setattr(player, "_last_spoke_at", 0.0)


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


class TestOneVoiceAtATime:
    """The morning greeting and the class reminder for the same 9:40 class
    both fired at 9:25 on 17 September and played over each other: two
    players, no lock. Every server-side voice now holds ``player.SPEAKING``
    for the length of the sound, so the second waits for the first.
    """

    def test_play_file_holds_the_lock_while_playing(self, monkeypatch):
        import threading
        import time

        from openjarvis.speech import player

        playing = []

        def _fake_play(path, volume=1.0):
            playing.append(path)
            time.sleep(0.15)
            playing.remove(path)
            return True

        monkeypatch.setattr(player, "_play", _fake_play)
        overlaps = []

        def _worker(name):
            def _observe(path, volume=1.0):
                if playing:
                    overlaps.append((name, list(playing)))
                return _fake_play(path)

            monkeypatch.setattr(player, "_play", _observe)
            player.play_file(name, duck=False)

        threads = [threading.Thread(target=_worker, args=(f"v{i}",)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert overlaps == []

    def test_the_reminder_player_waits_for_the_lock(self, monkeypatch):
        import time

        from openjarvis.speech import player
        from openjarvis.tools import notify_windows

        started = []

        class _Proc:
            def wait(self, timeout=None):
                return 0

        def _popen(*args, **kwargs):
            started.append(time.monotonic())
            return _Proc()

        monkeypatch.setattr(notify_windows.subprocess, "Popen", _popen)
        with player.speaking():
            assert notify_windows._run_hidden("noop") is True
            time.sleep(0.2)
            assert started == [], (
                "the reminder started while another voice held the lock"
            )
        deadline = time.monotonic() + 2
        while not started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(started) == 1


class TestListenersAreDeafWhileTheServerSpeaks:
    def test_is_speaking_covers_the_hold_and_the_echo_tail(self, monkeypatch):
        from openjarvis.speech import player

        monkeypatch.setattr(player, "_speakers", 0)
        monkeypatch.setattr(player, "_last_spoke_at", 0.0)
        assert not player.is_speaking(now=1000.0)
        with player.speaking():
            assert player.is_speaking()
        assert player.is_speaking()  # the tail
        assert not player.is_speaking(now=player._last_spoke_at + 5)

    def test_flux_relay_forwards_silence_while_speaking(self):
        import inspect

        from openjarvis.server import flux_routes

        source = inspect.getsource(flux_routes.flux_stream)
        assert "if is_speaking():" in source and "bytes(len(data))" in source

    def test_wake_word_socket_suppresses_detections_while_speaking(self, monkeypatch):
        pytest.importorskip("fastapi")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from openjarvis.core.config import JarvisConfig
        from openjarvis.server.api_routes import websocket_router
        from openjarvis.speech import player

        class _Detector:
            available = True
            resets = 0

            def clone(self):
                return self

            def score(self, frame):
                return 0.9

            def is_detection(self, score):
                return True

            def reset(self):
                self.resets += 1

        def _unavailable(config):
            raise RuntimeError("no model in tests")

        monkeypatch.setattr(
            "openjarvis.speech.wake_word_verify.local_verifier_backend", _unavailable
        )
        app = FastAPI()
        app.include_router(websocket_router)
        cfg = JarvisConfig()
        cfg.speech.wake_word_verify = "off"
        app.state.config = cfg
        app.state.api_key = ""
        app.state.wake_word_detector = _Detector()
        app.state.speech_backend = None
        client = TestClient(app)
        with player.speaking():
            with client.websocket_connect("/v1/speech/wake-word") as ws:
                ws.send_bytes(bytes(2560))
                msg = ws.receive_json()
        assert msg["type"] == "score" and msg["muted"] is True
        assert app.state.wake_word_detector.resets == 1


class TestAVoiceWaitsForTheUsersTurn:
    def test_speaking_waits_until_the_turn_closes(self):
        import threading
        import time

        from openjarvis.core import activity
        from openjarvis.speech import player

        activity.flux_transmitting(True)
        try:
            threading.Timer(0.3, lambda: activity.flux_transmitting(False)).start()
            t0 = time.monotonic()
            with player.speaking(wait_for_turn=5.0):
                waited = time.monotonic() - t0
            assert 0.25 <= waited < 2.0
        finally:
            activity.flux_transmitting(False)

    def test_but_not_forever(self):
        import time

        from openjarvis.core import activity
        from openjarvis.speech import player

        activity.flux_transmitting(True)
        try:
            t0 = time.monotonic()
            with player.speaking(wait_for_turn=0.2):
                pass
            assert time.monotonic() - t0 < 1.0
        finally:
            activity.flux_transmitting(False)


class TestRepliesAndServerVoicesTakeTurns:
    """An initiative line was heard over a chat answer on 17 September: the
    server's lock covered its own voices, not the browser's reply. Now a
    server voice waits for a reply being read aloud, and a reply's audio
    waits for a server voice."""

    def test_a_server_voice_waits_for_the_browsers_reply(self):
        import threading
        import time

        from openjarvis.core import activity
        from openjarvis.speech import player

        activity.tts_begin()
        try:
            threading.Timer(0.3, activity.tts_end).start()
            t0 = time.monotonic()
            with player.speaking(wait_for_turn=5.0):
                waited = time.monotonic() - t0
            assert 0.25 <= waited < 2.0
        finally:
            activity.tts_end()

    def test_a_reply_waits_for_a_server_voice(self):
        import asyncio
        import threading

        from openjarvis.server.tts_stream_routes import wait_for_server_voice
        from openjarvis.speech import player

        async def scenario():
            release = threading.Event()

            def _hold():
                with player.speaking(wait_for_turn=0):
                    release.wait(2.0)

            holder = threading.Thread(target=_hold)
            holder.start()
            await asyncio.sleep(0.05)
            assert player.is_speaking()
            loop = asyncio.get_running_loop()
            loop.call_later(0.3, release.set)
            t0 = loop.time()
            await wait_for_server_voice(timeout=5.0)
            waited = loop.time() - t0
            holder.join()
            # Released at 0.3 s plus the echo tail.
            from openjarvis.speech.player import ECHO_TAIL_SECONDS

            assert 0.2 + ECHO_TAIL_SECONDS <= waited < 2.0 + ECHO_TAIL_SECONDS

        asyncio.run(scenario())

    def test_but_a_reply_does_not_wait_forever(self):
        import asyncio

        from openjarvis.server.tts_stream_routes import wait_for_server_voice
        from openjarvis.speech import player

        async def scenario():
            with player.speaking(wait_for_turn=0):
                loop = asyncio.get_running_loop()
                t0 = loop.time()
                await wait_for_server_voice(timeout=0.2)
                assert loop.time() - t0 < 1.0

        asyncio.run(scenario())
