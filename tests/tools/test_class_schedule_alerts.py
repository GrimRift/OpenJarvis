"""Two reminders per class, spoken aloud, silent under Do Not Disturb.

The reminder never fired at all for months: the scheduled task asked a model
to "Call notify_class_schedule with lookahead_minutes=15", and the model
answered *about* the tool instead of calling it, every ten minutes, each run
recorded as a success. These cover what replaced that.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from openjarvis.tools import notify_class_schedule, notify_windows
from openjarvis.tools.notify_class_schedule import (
    REMINDER_STAGES,
    NotifyClassScheduleTool,
    _stage_for,
)

CLASS = {
    "subject_code": "CETHS120",
    "subject_description": "Methods of Research",
    "section": "BSCE231P1",
    "room": "Mezz 6",
    "mode": "In-person",
    "start_time": "1:00PM",
    "end_time": "3:40PM",
}


class TestWhichReminderAClassIsDue:
    @pytest.mark.parametrize("minutes,expected", [(15, 15), (14, 15), (6, 15)])
    def test_the_first_window(self, minutes, expected):
        assert _stage_for(minutes) == expected

    @pytest.mark.parametrize("minutes,expected", [(5, 5), (4.2, 5), (0, 5)])
    def test_the_second_window(self, minutes, expected):
        assert _stage_for(minutes) == expected

    def test_too_far_off_is_no_reminder(self):
        assert _stage_for(85.9) is None

    def test_the_stages_are_ordered_widest_first(self):
        assert list(REMINDER_STAGES) == sorted(REMINDER_STAGES, reverse=True)


class _Checker:
    """Stands in for CheckClassScheduleTool with a fixed answer."""

    def __init__(self, minutes_until):
        self._minutes = minutes_until

    def execute(self, **params):
        from openjarvis.core.types import ToolResult

        item = dict(CLASS, minutes_until=self._minutes, status="upcoming")
        return ToolResult(
            tool_name="check_class_schedule",
            content="ok",
            success=True,
            metadata={"upcoming": [item] if self._minutes is not None else []},
        )


class TestBothRemindersFire:
    """Keyed on the class alone, the 15-minute alert consumed the 5-minute
    one — the second reminder could never have been delivered."""

    @pytest.fixture(autouse=True)
    def _quiet(self, monkeypatch):
        self.spoken = []
        self.toasts = []
        monkeypatch.setattr(
            notify_class_schedule,
            "deliver",
            lambda title, message, **kw: self.toasts.append(message),
        )
        monkeypatch.setattr(
            notify_class_schedule, "speak", lambda text: self.spoken.append(text)
        )

    def _tool(self, tmp_path: Path, minutes):
        tool = NotifyClassScheduleTool(state_path=tmp_path / "state.json")
        tool._checker = _Checker(minutes)
        return tool

    def test_the_fifteen_minute_reminder_fires(self, tmp_path):
        result = self._tool(tmp_path, 12).execute(now=datetime(2026, 9, 2, 12, 48))
        assert result.metadata["notified"] is True
        assert len(self.toasts) == 1

    def test_the_same_reminder_does_not_repeat(self, tmp_path):
        tool = self._tool(tmp_path, 12)
        tool.execute(now=datetime(2026, 9, 2, 12, 48))
        again = tool.execute(now=datetime(2026, 9, 2, 12, 49))
        assert again.metadata["notified"] is False
        assert len(self.toasts) == 1

    def test_the_five_minute_reminder_still_fires_after_it(self, tmp_path):
        tool = self._tool(tmp_path, 12)
        tool.execute(now=datetime(2026, 9, 2, 12, 48))
        tool._checker = _Checker(4)
        second = tool.execute(now=datetime(2026, 9, 2, 12, 56))
        assert second.metadata["notified"] is True
        assert len(self.toasts) == 2

    def test_nothing_close_enough_notifies_nothing(self, tmp_path):
        result = self._tool(tmp_path, 85.9).execute(now=datetime(2026, 9, 2, 11, 30))
        assert result.metadata["notified"] is False
        assert self.toasts == []


class TestItSpeaksAsWellAsShows:
    """A reminder you have to be looking at the screen to catch is the one
    that gets missed."""

    @pytest.fixture(autouse=True)
    def _capture(self, monkeypatch):
        self.spoken = []
        monkeypatch.setattr(
            notify_class_schedule, "deliver", lambda *a, **k: None
        )
        monkeypatch.setattr(
            notify_class_schedule, "speak", lambda text: self.spoken.append(text)
        )

    def test_it_says_the_subject_and_the_real_minutes(self, tmp_path):
        """The remaining time, not the stage that triggered the reminder.

        A stage fires for any class at or under it, so the two drift apart by
        as much as one poll interval -- and the number is the whole point of
        the alert. This class is twelve minutes out, not fifteen.
        """
        tool = NotifyClassScheduleTool(state_path=tmp_path / "s.json")
        tool._checker = _Checker(12)
        tool.execute(now=datetime(2026, 9, 2, 12, 48))
        assert self.spoken == ["Methods of Research in 12 minutes."]

    def test_the_second_reminder_says_what_is_left(self, tmp_path):
        tool = NotifyClassScheduleTool(state_path=tmp_path / "s.json")
        tool._checker = _Checker(3)
        tool.execute(now=datetime(2026, 9, 2, 12, 57))
        assert self.spoken == ["Methods of Research in 3 minutes."]

    def test_one_minute_is_singular(self, tmp_path):
        """A five-minute poll can land with a single minute to spare."""
        tool = NotifyClassScheduleTool(state_path=tmp_path / "s.json")
        tool._checker = _Checker(1)
        tool.execute(now=datetime(2026, 9, 2, 12, 59))
        assert self.spoken == ["Methods of Research in 1 minute."]


class TestDoNotDisturbSilencesTheVoiceOnly:
    def test_it_does_not_speak_under_dnd(self, monkeypatch):
        monkeypatch.setattr(notify_windows, "do_not_disturb", lambda: True)
        synthesised = []
        monkeypatch.setattr(
            notify_windows,
            "subprocess",
            type("S", (), {"Popen": lambda *a, **k: synthesised.append(1)})(),
        )
        assert notify_windows.speak("Class in 5 minutes.") is False
        assert synthesised == []

    def test_the_toast_is_not_suppressed(self, tmp_path, monkeypatch):
        """Do Not Disturb removes the noise, not the reminder."""
        toasts = []
        monkeypatch.setattr(
            notify_class_schedule, "deliver", lambda t, m, **k: toasts.append(m)
        )
        monkeypatch.setattr(notify_windows, "do_not_disturb", lambda: True)
        tool = NotifyClassScheduleTool(state_path=tmp_path / "s.json")
        tool._checker = _Checker(12)
        tool.execute(now=datetime(2026, 9, 2, 12, 48))
        assert len(toasts) == 1

    def test_empty_text_is_never_spoken(self):
        assert notify_windows.speak("   ") is False

    def test_an_unreadable_registry_does_not_silence_the_reminder(
        self, monkeypatch
    ):
        """Fails open: a silent reminder is the failure the user notices."""
        monkeypatch.setattr(notify_windows.sys, "platform", "linux")
        assert notify_windows.do_not_disturb() is False


class TestThePollIsFasterThanTheShortestReminder:
    def test_the_interval_can_catch_the_five_minute_alert(self):
        """The old task polled every 10 minutes, so a 5-minute reminder could
        not have been delivered even if the model had called the tool.

        The bound is inclusive: at exactly the stage length every occurrence
        is still caught, because the stage matches any class *at or under* it
        rather than one at that precise minute. What a longer interval costs
        is punctuality, not delivery -- hence the spoken-time test below.
        """
        from openjarvis.agents.class_notifier import POLL_SECONDS

        assert POLL_SECONDS <= min(REMINDER_STAGES) * 60


class TestTheVoiceIsActuallyAudible:
    """The toast always worked and the voice never did.

    Windows Media Player's COM object sat at playState 9 (Transitioning)
    forever in a non-interactive session while reporting success, and the wait
    loop checked ``playState -eq 3`` 400ms in — still transitioning — so it
    exited at once and released the object mid-load. Silent, twice over.
    """

    def _wav(self, rate=24000, samples=(0.0, 0.5, -0.5)):
        import struct

        body = struct.pack(f"<{len(samples)}f", *samples)
        # A streaming header: the RIFF length field is left as ffffffff, which
        # is what SoundPlayer rejects outright as "not a valid wave file".
        fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
        return (
            b"RIFF\xff\xff\xff\xffWAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(body)) + body
        )

    def test_the_sample_rate_comes_from_the_header(self):
        """Assuming 44.1kHz plays a 24kHz clip half again too fast."""
        assert notify_windows._wav_sample_rate(self._wav(rate=24000)) == 24000
        assert notify_windows._wav_sample_rate(self._wav(rate=44100)) == 44100

    def test_a_headerless_blob_yields_no_rate(self):
        assert notify_windows._wav_sample_rate(b"not audio at all") is None

    def test_it_writes_a_16_bit_wav_soundplayer_accepts(self, tmp_path, monkeypatch):
        import wave

        raw = self._wav()

        class _Backend:
            def synthesize(self, text, output_format="mp3"):
                return type("R", (), {"audio": raw, "format": output_format})()

        monkeypatch.setattr(
            "openjarvis.speech.cartesia_tts.CartesiaTTSBackend", _Backend
        )
        monkeypatch.setattr(
            "openjarvis.core.paths.get_config_dir", lambda: tmp_path
        )
        path = notify_windows._voice_wav("hello")
        assert path is not None
        with wave.open(path) as handle:
            assert handle.getsampwidth() == 2
            assert handle.getframerate() == 24000
            assert handle.getnchannels() == 1

    def test_a_failed_voice_falls_back_rather_than_going_silent(self, monkeypatch):
        """A robotic reminder beats no reminder."""
        monkeypatch.setattr(notify_windows, "do_not_disturb", lambda: False)
        monkeypatch.setattr(notify_windows, "_voice_wav", lambda text: None)
        spoken = []
        monkeypatch.setattr(
            notify_windows, "_speak_builtin", lambda text: spoken.append(text) or True
        )
        assert notify_windows.speak("Class in 5 minutes.") is True
        assert spoken == ["Class in 5 minutes."]

    def test_playback_failure_also_falls_back(self, monkeypatch):
        monkeypatch.setattr(notify_windows, "do_not_disturb", lambda: False)
        monkeypatch.setattr(notify_windows, "_voice_wav", lambda text: "x.wav")
        monkeypatch.setattr(notify_windows, "_play_wav", lambda path: False)
        fell_back = []
        monkeypatch.setattr(
            notify_windows, "_speak_builtin", lambda text: fell_back.append(1) or True
        )
        assert notify_windows.speak("Class in 5 minutes.") is True
        assert fell_back == [1]

    def test_the_good_voice_is_preferred_when_it_works(self, monkeypatch):
        monkeypatch.setattr(notify_windows, "do_not_disturb", lambda: False)
        monkeypatch.setattr(notify_windows, "_voice_wav", lambda text: "x.wav")
        monkeypatch.setattr(notify_windows, "_play_wav", lambda path: True)
        monkeypatch.setattr(
            notify_windows,
            "_speak_builtin",
            lambda text: pytest.fail("fell back despite the good voice working"),
        )
        assert notify_windows.speak("Class in 5 minutes.") is True


class TestTheVoiceIsLoudEnoughToNotice:
    """Firing is not the same as being heard.

    The alert fired correctly and was still missed: Cartesia masters its
    output well below full scale -- the reminder measured 0.228 of it, some
    13 dB down -- which beside a playing video is quiet enough to ignore.
    """

    def test_a_quiet_clip_is_brought_up(self):
        from openjarvis.tools.notify_windows import _TARGET_PEAK, _normalised

        out = _normalised([0.2, -0.1, 0.05])

        assert max(abs(v) for v in out) == pytest.approx(_TARGET_PEAK)

    def test_a_loud_clip_is_left_alone(self):
        """Normalising is a floor, not a leveller: never turn a clip down."""
        from openjarvis.tools.notify_windows import _normalised

        loud = [0.99, -0.98]

        assert _normalised(loud) == loud

    def test_silence_is_not_amplified(self):
        """Guards the divide, and stops a dead clip becoming a hiss."""
        from openjarvis.tools.notify_windows import _normalised

        assert _normalised([0.0, 0.0]) == [0.0, 0.0]

    def test_gain_is_capped(self):
        from openjarvis.tools.notify_windows import _MAX_GAIN, _normalised

        out = _normalised([1e-6])

        assert out[0] == pytest.approx(1e-6 * _MAX_GAIN)
