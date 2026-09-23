"""What the server is saying aloud, for the orb.

Reminders, schedule notices and moments are spoken by the server, so the
page had no way to know Sage was talking and the orb sat in standing by
through every one of them. ``current_voice`` is what the page follows; the
envelope is what lets the orb's syllables land on the real ones.
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from openjarvis.speech import player


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(player, "_speakers", 0)
    monkeypatch.setattr(player, "_last_spoke_at", 0.0)
    monkeypatch.setattr(player, "_voice", None)
    monkeypatch.setattr(player, "_wait_for_the_floor", lambda timeout: None)


def _wav(path: Path, segments: list[tuple[float, float]], rate: int = 16000) -> Path:
    """A tone in segments of (seconds, amplitude)."""
    frames = bytearray()
    t = 0
    for seconds, amp in segments:
        for _ in range(int(seconds * rate)):
            value = amp * math.sin(2 * math.pi * 220 * t / rate)
            frames += struct.pack("<h", int(value * 32767))
            t += 1
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return path


class TestEnvelope:
    def test_follows_the_loudness_of_the_clip(self, tmp_path) -> None:
        clip = _wav(tmp_path / "v.wav", [(0.2, 0.0), (0.2, 0.5), (0.2, 0.0)])
        env = player.voice_envelope(str(clip))
        assert env is not None
        # 0.6 s at 20 ms a step.
        assert len(env) == 30
        quiet_before, loud, quiet_after = env[:9], env[11:19], env[21:]
        assert max(quiet_before) < 0.05
        assert max(quiet_after) < 0.05
        assert min(loud) > 0.8

    def test_scales_to_the_clip_not_to_full_scale(self, tmp_path) -> None:
        # A quietly synthesised voice still reaches the orb's full range:
        # syllables land near 0.9 whatever the synthesis volume was.
        soft = player.voice_envelope(str(_wav(tmp_path / "s.wav", [(0.3, 0.05)])))
        loud = player.voice_envelope(str(_wav(tmp_path / "l.wav", [(0.3, 0.8)])))
        assert soft is not None and loud is not None
        assert abs(max(soft) - max(loud)) < 0.05

    def test_unreadable_audio_gives_none_not_an_error(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(player.shutil, "which", lambda name: None)
        junk = tmp_path / "x.mp3"
        junk.write_bytes(b"not audio")
        assert player.voice_envelope(str(junk)) is None


class TestCurrentVoice:
    def test_reported_while_playing_and_gone_after(self, tmp_path, monkeypatch) -> None:
        clip = _wav(tmp_path / "v.wav", [(0.1, 0.5)])
        seen = {}

        def fake_play(path, volume=1.0):
            seen["during"] = player.current_voice()
            return True

        monkeypatch.setattr(player, "_play", fake_play)
        assert player.play_file(str(clip), duck=False, channel="reminders")
        during = seen["during"]
        assert during is not None and during["speaking"] is True
        assert during["channel"] == "reminders"
        assert during["step_ms"] == player.ENVELOPE_STEP_MS
        assert during["envelope"] and len(during["envelope"]) == 5
        assert player.current_voice() is None

    def test_a_chime_is_not_speech(self, tmp_path, monkeypatch) -> None:
        clip = _wav(tmp_path / "c.wav", [(0.1, 0.5)])
        seen = {}

        def fake_play(path, volume=1.0):
            seen["during"] = player.current_voice()
            return True

        monkeypatch.setattr(player, "_play", fake_play)
        player.play_file(str(clip), duck=False, channel="chime")
        assert seen["during"] is None

    def test_cleared_even_when_the_player_fails(self, tmp_path, monkeypatch) -> None:
        clip = _wav(tmp_path / "v.wav", [(0.1, 0.5)])

        def broken(path, volume=1.0):
            raise RuntimeError("player crashed")

        monkeypatch.setattr(player, "_play", broken)
        with pytest.raises(RuntimeError):
            player.play_file(str(clip), duck=False, channel="moments")
        # A voice left behind would hold the orb in speaking for good.
        assert player.current_voice() is None

    def test_a_voice_without_a_file_still_speaks(self) -> None:
        # The built-in Windows voice has no file to measure; the orb must
        # still switch to speaking and move on its own rhythm.
        with player.voice("reminders"):
            now = player.current_voice()
            assert now is not None and now["envelope"] is None
        assert player.current_voice() is None

    def test_elapsed_lets_a_late_page_line_up(self) -> None:
        with player.voice("moments", [0.5] * 100):
            started = player._voice["started"]
            later = player.current_voice(now=started + 0.4)
            assert later is not None and later["elapsed_ms"] == 400


class TestOneHoldForChimeAndWords:
    """The chime plays inside the spoken line's hold of the floor and its
    duck. Neither may run again inside: the duck would restore the outer
    duck's "leftover" volumes, bringing a film back up under the voice, and
    the floor would make the words wait for a turn begun after the chime."""

    def test_nested_duck_does_nothing(self, monkeypatch) -> None:
        from openjarvis.speech import ducking

        opened = []

        class _Once:
            def __init__(self, *a):
                opened.append(1)

            def __enter__(self):
                return ["film"]

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(ducking, "_duck", _Once)
        with ducking.ducked() as outer:
            with ducking.ducked() as inner:
                assert inner == []
            assert outer == ["film"]
        assert opened == [1]
        # And a later, separate duck is real again.
        with ducking.ducked():
            pass
        assert opened == [1, 1]

    def test_nested_floor_does_not_wait_again(self, monkeypatch) -> None:
        waits = []
        monkeypatch.setattr(
            player, "_wait_for_the_floor", lambda timeout: waits.append(1)
        )
        with player.speaking():
            with player.speaking():
                pass
        assert waits == [1]
        with player.speaking():
            pass
        assert waits == [1, 1]
