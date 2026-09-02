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

    def test_it_says_the_subject_and_the_minutes(self, tmp_path):
        tool = NotifyClassScheduleTool(state_path=tmp_path / "s.json")
        tool._checker = _Checker(12)
        tool.execute(now=datetime(2026, 9, 2, 12, 48))
        assert self.spoken == ["Methods of Research in 15 minutes."]

    def test_the_second_reminder_says_five(self, tmp_path):
        tool = NotifyClassScheduleTool(state_path=tmp_path / "s.json")
        tool._checker = _Checker(3)
        tool.execute(now=datetime(2026, 9, 2, 12, 57))
        assert self.spoken == ["Methods of Research in 5 minutes."]


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
        not have been delivered even if the model had called the tool."""
        from openjarvis.agents.class_notifier import POLL_SECONDS

        assert POLL_SECONDS < min(REMINDER_STAGES) * 60
