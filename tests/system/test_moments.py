"""Tests for the moments engine (M36 phase 3).

Every moment is guarded by presence, a daily cap, quiet hours and "not now".
A wrong guard is worse than no moment at all -- a greeting to an empty room,
a second good morning, a voice at midnight -- so each guard is exercised
with an injected clock, a scripted presence monitor and a recording speaker
rather than a desk and a pair of speakers.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from openjarvis.core.activity import Activity
from openjarvis.core.moments import (
    DAILY_CAPS,
    FALLBACK_LINES,
    FOLLOW_UP_AFTER_SECONDS,
    FOLLOW_UP_LINES,
    MOMENT_GREETING,
    MOMENT_INITIATIVE,
    MOMENT_TOLD,
    MOMENT_WELCOME_BACK,
    MomentEngine,
    fallback_text,
    in_quiet_hours,
    last_fallback,
    load_state,
    local_to_timestamp,
    recent_openings,
    time_of_day,
)
from openjarvis.core.presence import (
    STATE_AWAY,
    STATE_PRESENT,
    PresenceMonitor,
    PresenceSettings,
    save_settings,
)

TZ = "Asia/Singapore"


def _at(hour: int, minute: int = 0, day: int = 15) -> float:
    return datetime(2026, 9, day, hour, minute, tzinfo=ZoneInfo(TZ)).timestamp()


class _Desk:
    """A scripted desk: the idle sensor reads whatever the test sets."""

    def __init__(self) -> None:
        self.idle = 1.0

    def __call__(self) -> Optional[float]:
        return self.idle


class _Rig:
    def __init__(self, tmp_path: Path, settings: Optional[PresenceSettings] = None):
        self.now = _at(9)
        self.desk = _Desk()
        self.spoken: List[Tuple[str, str]] = []
        self.compose_calls: List[str] = []
        self.last_context: dict = {}
        # Initiative: what the fake writer will say ("" declines), what the
        # desk looks like, and what the server has seen the user do.
        self.initiative_line = ""
        self.initiative_contexts: List[dict] = []
        self.busy: List[str] = []
        # These tests simulate a live session; the page has been opened.
        self.activity = Activity(ui_seen=True)
        save_settings(settings or PresenceSettings(enabled=True), tmp_path)
        self.monitor = PresenceMonitor(
            config_dir=tmp_path,
            idle_sensor=self.desk,
            foreground_sensor=lambda: None,
            clock=lambda: self.now,
        )
        self.engine = MomentEngine(
            self.monitor,
            config_dir=tmp_path,
            clock=lambda: self.now,
            composer=self._compose,
            speaker=self._speak,
            chimer=lambda: True,
            initiative_composer=self._compose_initiative,
            busy_sensor=lambda activity, settings: list(self.busy),
            activity_source=lambda: self.activity,
            scheduler_lookup=lambda: None,
            timezone_name=TZ,
            reminder=self._remind,
        )
        self.reminders: list[str] = []

    def _remind(self, what: str) -> bool:
        self.reminders.append(what)
        return True

    def _compose(self, kind: str, context: dict) -> str:
        self.compose_calls.append(kind)
        self.last_context = context
        return f"[{kind}] {context.get('away_for', '')}".strip()

    def _compose_initiative(self, context: dict) -> str:
        self.initiative_contexts.append(context)
        return self.initiative_line

    def _speak(self, text: str) -> bool:
        self.spoken.append(text)
        return True

    def tick(self, at: Optional[float] = None, idle: Optional[float] = None):
        if at is not None:
            self.now = at
        if idle is not None:
            self.desk.idle = idle
        self.monitor.poll()
        return self.engine.tick()

    def leave_and_return(self, leave_at: float, return_at: float) -> None:
        """Walk away (idle past the threshold) and come back."""
        self.tick(leave_at, idle=1.0)
        self.tick(leave_at + 600, idle=600.0)
        self.tick(return_at, idle=1.0)


class TestPureHelpers:
    def test_quiet_hours_wrap_midnight(self) -> None:
        assert in_quiet_hours(23, 23, 7)
        assert in_quiet_hours(2, 23, 7)
        assert not in_quiet_hours(7, 23, 7)
        assert not in_quiet_hours(12, 23, 7)

    def test_quiet_hours_within_a_day(self) -> None:
        assert in_quiet_hours(14, 13, 15)
        assert not in_quiet_hours(15, 13, 15)

    def test_equal_bounds_mean_no_quiet_hours(self) -> None:
        assert not in_quiet_hours(3, 7, 7)

    def test_time_of_day_names(self) -> None:
        assert time_of_day(7) == "morning"
        assert time_of_day(13) == "afternoon"
        assert time_of_day(19) == "evening"

    def test_local_datetime_is_read_in_the_users_zone(self) -> None:
        assert local_to_timestamp("2026-09-15T15:00", TZ) == _at(15)
        assert local_to_timestamp("not a time", TZ) is None

    def test_fallback_lines_exist_for_every_kind(self) -> None:
        assert (
            "evening"
            in fallback_text(MOMENT_GREETING, {"time_of_day": "evening"}).lower()
        )
        assert "day" in fallback_text(MOMENT_GREETING, {}).lower()
        assert "back" in fallback_text(MOMENT_WELCOME_BACK, {"away_for": "2 hours"})
        told = fallback_text(
            MOMENT_TOLD,
            {"told": "The user asked to be told when: class starts. It is time."},
        )
        assert "class starts" in told


class TestGreeting:
    def test_first_appearance_in_the_morning_is_greeted_once(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        said = rig.tick(_at(8), idle=1.0)
        assert [r.kind for r in said] == [MOMENT_GREETING]
        assert rig.spoken == ["[greeting]"]
        # However many polls follow, and however the state flickers.
        for minute in range(1, 30):
            assert rig.tick(_at(8, minute)) == []
        rig.leave_and_return(_at(9), _at(11))
        assert [r.kind for r in rig.engine.history()].count(MOMENT_GREETING) == 1

    def test_an_afternoon_first_boot_is_greeted_for_the_afternoon(
        self, tmp_path
    ) -> None:
        # Sage is not on all day: switched on at 14:00, the first thing it
        # sees is the user, and that is the day's greeting.
        rig = _Rig(tmp_path)
        rig.contexts = []
        said = rig.tick(_at(14), idle=1.0)
        assert [r.kind for r in said] == [MOMENT_GREETING]
        assert rig.last_context["time_of_day"] == "afternoon"

    def test_a_midnight_coder_still_gets_a_good_morning_after_sleeping(
        self, tmp_path
    ) -> None:
        rig = _Rig(tmp_path)
        rig.tick(_at(22, 30, day=14), idle=1.0)  # yesterday's greeting
        rig.tick(_at(0, 30), idle=1.0)  # past midnight: quiet hours
        assert rig.spoken == ["[greeting]"]
        rig.leave_and_return(_at(1), _at(9))
        # A new calendar day, not 24 hours since the last one.
        assert rig.spoken == ["[greeting]", "[greeting] 7 hours"]

    def test_survives_a_restart(self, tmp_path) -> None:
        # A server restart in the morning must not repeat the greeting: the
        # daily cap is on disk, not in memory.
        rig = _Rig(tmp_path)
        rig.tick(_at(8), idle=1.0)
        assert len(rig.spoken) == 1
        again = _Rig(tmp_path)
        again.now = _at(8, 5)
        assert again.tick(_at(8, 5), idle=1.0) == []

    def test_switch_off_just_this_moment(self, tmp_path) -> None:
        rig = _Rig(tmp_path, PresenceSettings(enabled=True, greeting_enabled=False))
        assert rig.tick(_at(8), idle=1.0) == []

    def test_shut_down_at_night_booted_in_the_morning(self, tmp_path) -> None:
        # The case the user actually lives: Stop Sage at 23:30, laptop on at
        # 08:15. The gap in readings is the night.
        rig = _Rig(tmp_path)
        rig.tick(_at(22, 40, day=14), idle=1.0)
        again = _Rig(tmp_path)
        said = again.tick(_at(8, 15), idle=1.0)
        assert [r.kind for r in said] == [MOMENT_GREETING]
        assert again.spoken == ["[greeting] 9 hours"]
        assert again.last_context["time_of_day"] == "morning"


class TestWelcomeBack:
    def test_returning_after_an_hour_is_greeted(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)  # the day's greeting
        rig.leave_and_return(_at(13, 5), _at(15))
        # Away from 13:05 (last input), noticed at 13:15, back at 15:00.
        assert rig.spoken == ["[greeting]", "[welcome_back] 1 hour 50 min"]

    def test_a_coffee_break_is_not(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)
        rig.leave_and_return(_at(13, 5), _at(13, 40))
        assert rig.spoken == ["[greeting]"]
        # And that short absence is not greeted later either, when a longer
        # one would have been.
        assert rig.tick(_at(13, 41)) == []

    def test_one_return_earns_one_greeting(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)
        rig.leave_and_return(_at(13, 5), _at(15))
        for minute in range(1, 10):
            rig.tick(_at(15, minute))
        assert len(rig.spoken) == 2

    def test_capped_per_day(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.tick(_at(12), idle=1.0)
        hour = 12
        for _ in range(DAILY_CAPS[MOMENT_WELCOME_BACK] + 2):
            rig.leave_and_return(_at(hour, 5), _at(hour + 1, 30))
            hour += 2
        assert len(rig.spoken) == 1 + DAILY_CAPS[MOMENT_WELCOME_BACK]

    def test_sage_off_in_between_counts_as_away(self, tmp_path) -> None:
        # Shut down at 13:00, back on at 16:00: the user's choice is that
        # the gap counts, since Sage is off whenever they go out.
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)  # today's greeting
        assert len(rig.spoken) == 1
        again = _Rig(tmp_path)
        said = again.tick(_at(16), idle=1.0)
        assert [r.kind for r in said] == [MOMENT_WELCOME_BACK]
        assert again.spoken == ["[welcome_back] 3 hours"]

    def test_the_desk_clock_restarts_on_a_return_sage_only_inferred(
        self, tmp_path
    ) -> None:
        """17 September: the monitor last watched an absence end at 13:12;
        at 21:22 a return was recognised from a gap in Sage's own readings
        ("welcome back"), and at 22:24 the initiative said "after nine hours
        at the desk" -- the clock had never restarted."""
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)
        rig.leave_and_return(_at(13, 2), _at(13, 12))  # watched, too short to greet
        rig.tick(_at(13, 13), idle=1.0)
        again = _Rig(tmp_path)  # Sage restarted while the user was away
        said = again.tick(_at(21, 22), idle=1.0)
        assert [r.kind for r in said] == [MOMENT_WELCOME_BACK]
        again.initiative_line = "[useful] Water, sir."
        again.tick(_at(21, 30), idle=1.0)
        assert again.initiative_contexts[-1]["at_desk_for"] == "8 minutes"

    def test_a_short_restart_is_not(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)
        again = _Rig(tmp_path)
        assert again.tick(_at(13, 20), idle=1.0) == []

    def test_never_while_away(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)
        rig.tick(_at(13, 5), idle=1.0)
        rig.tick(_at(15), idle=7000.0)
        assert rig.monitor.snapshot().state == STATE_AWAY
        assert rig.spoken == ["[greeting]"]


class TestGuards:
    def test_quiet_hours_silence_everything(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.engine.add_watch("the download finished", due_at=_at(23, 30))
        rig.tick(_at(22), idle=1.0)
        assert rig.spoken == ["[greeting]"]
        rig.leave_and_return(_at(22, 5), _at(23, 30))
        assert rig.spoken == ["[greeting]"]
        assert rig.engine.snapshot()["last_reason"] == "quiet hours"

    def test_not_now_silences_the_rest_of_the_day_only(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.engine.snooze_today()
        rig.tick(_at(13), idle=1.0)
        rig.leave_and_return(_at(13, 5), _at(15))
        assert rig.spoken == []
        assert rig.engine.snapshot()["snoozed_today"] is True
        # Tomorrow it is over on its own.
        rig.tick(_at(8, day=16), idle=1.0)
        assert rig.spoken == ["[greeting] 17 hours"]

    def test_master_switch_off_means_silence(self, tmp_path) -> None:
        rig = _Rig(tmp_path, PresenceSettings(enabled=False))
        assert rig.tick(_at(8), idle=1.0) == []
        assert rig.compose_calls == []

    def test_a_model_failure_speaks_the_fallback(self, tmp_path) -> None:
        rig = _Rig(tmp_path)

        def broken(kind: str, context: dict) -> str:
            raise RuntimeError("cloud down")

        rig.engine._composer = broken
        said = rig.tick(_at(8), idle=1.0)
        assert "morning" in said[0].text.lower() and "sir" in said[0].text
        assert "cloud down" in said[0].detail
        assert rig.spoken == [said[0].text]
        rig.engine._composer = broken
        rig.leave_and_return(_at(9), _at(11))
        assert "1 hour 55 min" in rig.spoken[-1]

    def test_a_voice_failure_still_counts(self, tmp_path) -> None:
        # Otherwise a dead speaker would retry every fifteen seconds.
        rig = _Rig(tmp_path)
        rig.engine._speaker = lambda text: (_ for _ in ()).throw(
            RuntimeError("no audio")
        )
        said = rig.tick(_at(8), idle=1.0)
        assert said[0].spoken is False
        assert rig.tick(_at(8, 1)) == []


class TestTellMeWhen:
    def test_a_time_watch_fires_when_due_and_present(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)
        watch = rig.engine.add_watch("your class starts", due_at=_at(13, 30))
        assert rig.tick(_at(13, 29)) == []
        said = rig.tick(_at(13, 30))
        assert [r.kind for r in said] == [MOMENT_TOLD]
        assert rig.compose_calls[-1] == MOMENT_TOLD
        assert rig.engine.list_watches() == []
        assert rig.tick(_at(13, 31)) == []
        assert watch.id not in {w.id for w in rig.engine.list_watches()}

    def test_held_while_away_and_said_on_return(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)
        rig.engine.add_watch("the render finished", due_at=_at(13, 30))
        rig.tick(_at(13, 5), idle=1.0)
        rig.tick(_at(13, 30), idle=1500.0)
        assert rig.spoken == ["[greeting]"]
        said = rig.tick(_at(13, 45), idle=1.0)
        assert [r.kind for r in said] == [MOMENT_TOLD]

    def test_stale_watches_are_dropped_not_said(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)
        rig.engine.add_watch("your class starts", due_at=_at(13, 10))
        rig.tick(_at(13, 5), idle=1.0)
        rig.tick(_at(13, 10), idle=300.0)
        # A day later: a good morning, but not "your class started yesterday".
        said = rig.tick(_at(9, day=16), idle=1.0)
        assert [r.kind for r in said] == [MOMENT_GREETING]
        records = [r for r in rig.engine.history() if r.kind == MOMENT_TOLD]
        assert records and records[0].spoken is False
        assert "too late" in records[0].detail
        assert rig.engine.list_watches() == []

    def test_a_reminder_is_delivered_when_due_even_while_away(self, tmp_path) -> None:
        """ "Tell me in five minutes that I have to eat": toast and voice,
        wherever the user is, no composer, no presence gate."""
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)
        rig.engine.add_watch("you have to eat", due_at=_at(13, 5), anywhere=True)
        assert rig.tick(_at(13, 4), idle=1500.0) == []
        assert rig.reminders == []
        rig.tick(_at(13, 5), idle=1500.0)  # away from the desk
        assert rig.reminders == ["you have to eat"]
        assert rig.engine.list_watches() == []
        assert MOMENT_TOLD not in rig.compose_calls
        record = [r for r in rig.engine.history() if r.kind == MOMENT_TOLD][-1]
        assert record.spoken is False and record.detail == "reminder"
        assert "(reminder)" in record.text

    def test_a_reminder_found_hours_late_is_dropped(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)
        rig.engine.add_watch("you have to eat", due_at=_at(13, 5), anywhere=True)
        rig.tick(_at(17), idle=1.0)  # Sage was off through the afternoon
        assert rig.reminders == []
        record = [r for r in rig.engine.history() if r.kind == MOMENT_TOLD][-1]
        assert "too late" in record.detail and rig.engine.list_watches() == []

    def test_a_reminder_needs_a_time(self, tmp_path) -> None:
        import pytest

        rig = _Rig(tmp_path)
        with pytest.raises(ValueError):
            rig.engine.add_watch("x", task_id="job", anywhere=True)

    def test_a_job_watch_fires_when_the_task_has_run(self, tmp_path) -> None:
        class _Store:
            runs: list = []

            def get_run_logs(self, task_id, limit=10):
                return list(self.runs)

        class _Scheduler:
            _store = _Store()

            def list_tasks(self):
                return []

        scheduler = _Scheduler()
        rig = _Rig(tmp_path)
        rig.engine._scheduler_lookup = lambda: scheduler
        rig.tick(_at(13), idle=1.0)
        rig.engine.add_watch("the research job is done", task_id="abc")
        assert rig.tick(_at(13, 1)) == []
        _Store.runs = [
            {
                "finished_at": "2026-09-15T05:02:00+00:00",
                "success": True,
                "result": "Found 3",
            }
        ]
        said = rig.tick(_at(13, 3))
        assert [r.kind for r in said] == [MOMENT_TOLD]

    def test_watches_and_snooze_persist(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.engine.add_watch("it is three", due_at=_at(15))
        rig.engine.snooze_today()
        state = load_state(tmp_path)
        assert [w.what for w in state.watches] == ["it is three"]
        assert state.snoozed_day == "2026-09-15"
        assert rig.engine.cancel_watch(state.watches[0].id) is True
        assert load_state(tmp_path).watches == []


class TestRecord:
    def test_every_moment_is_on_the_record(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.tick(_at(8), idle=1.0)
        history = load_state(tmp_path).history
        assert len(history) == 1
        assert history[0].kind == MOMENT_GREETING
        assert history[0].spoken is True
        assert rig.monitor.snapshot().state == STATE_PRESENT


class TestVariedLines:
    def test_the_fallback_never_repeats_the_last_line(self) -> None:
        context = {"time_of_day": "morning"}
        first = fallback_text(MOMENT_GREETING, context)
        for _ in range(20):
            assert fallback_text(MOMENT_GREETING, context, avoid=first) != first

    def test_every_fallback_line_fills_its_slots(self) -> None:
        for kind, lines in FALLBACK_LINES.items():
            for _ in range(10):
                text = fallback_text(
                    kind, {"time_of_day": "evening", "away_for": "2 hours"}
                )
                assert "{" not in text and "}" not in text
            assert len(lines) >= 3

    def test_last_fallback_is_only_a_fallback(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.engine._record(
            MOMENT_GREETING,
            "Good morning, sir.",
            spoken=True,
            detail="model unavailable: down",
            now=1.0,
        )
        assert (
            last_fallback(rig.engine.history(), MOMENT_GREETING) == "Good morning, sir."
        )
        rig.engine._record(
            MOMENT_GREETING,
            "Morning. Two things today.",
            spoken=True,
            detail="",
            now=2.0,
        )
        assert last_fallback(rig.engine.history(), MOMENT_GREETING) is None

    def test_recent_openings_reach_the_model(self, tmp_path) -> None:
        # The model has no memory of yesterday's greeting; the record is it.
        rig = _Rig(tmp_path)
        rig.engine._record(
            MOMENT_GREETING,
            "Good morning, sir. Yesterday you fixed the orb.",
            spoken=True,
            detail="",
            now=1.0,
        )
        rig.tick(_at(8), idle=1.0)
        assert rig.last_context["recent_openings"].startswith(
            "- Good morning, sir. Yesterday you fixed the"
        )
        assert recent_openings([], MOMENT_GREETING) == ""


class TestReturnLatency:
    def test_the_monitor_hands_a_return_to_the_engine_at_once(self, tmp_path) -> None:
        # No second poll on the engine's own clock: the monitor's transition
        # is the moment, and the greeting is spoken from it.
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)
        rig.spoken.clear()
        rig.monitor.add_listener(rig.engine._on_presence_change)
        rig.now = _at(13, 5)
        rig.monitor.poll()
        rig.now = _at(13, 15)
        rig.desk.idle = 600.0
        rig.monitor.poll()
        rig.now = _at(15)
        rig.desk.idle = 1.0
        rig.monitor.poll()  # engine.tick() never called by the test
        assert [t for t in rig.spoken] == ["[welcome_back] 1 hour 50 min"]

    def test_the_chime_sounds_before_the_words_are_written(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        order: List[str] = []
        rig.engine._chimer = lambda: order.append("chime") or True

        def compose(kind: str, context: dict) -> str:
            order.append("compose")
            return "words"

        rig.engine._composer = compose
        rig.tick(_at(8), idle=1.0)
        assert order == ["chime", "compose"]

    def test_nothing_chimes_when_nothing_is_said(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        chimes: List[int] = []
        rig.engine._chimer = lambda: chimes.append(1) or True
        rig.tick(_at(8), idle=1.0)
        rig.tick(_at(8, 1))
        rig.tick(_at(8, 2))
        assert chimes == [1]


class TestAwayNews:
    class _Store:
        def __init__(self, runs):
            self.runs = runs

        def get_run_logs(self, task_id, limit=10):
            return list(self.runs.get(task_id, []))

    class _Task:
        def __init__(self, id, agent, prompt, metadata=None):
            self.id, self.agent, self.prompt = id, agent, prompt
            self.metadata = metadata or {}

    def _scheduler(self, tasks, runs):
        store = self._Store(runs)

        class _S:
            _store = store

            def list_tasks(self_inner):
                return tasks

        return _S()

    def test_housekeeping_counts_only_when_it_notified(self) -> None:
        from openjarvis.core.moments import _finished_jobs

        quiet = {
            "content": "Nothing upcoming — no notification sent.",
            "tool_results": [{"tool_name": "check_class_schedule", "success": True}],
        }
        alert = {
            "content": "Structural Analysis in 10 minutes.",
            "tool_results": [{"tool_name": "notify_windows", "success": True}],
        }
        tasks = [
            self._Task(
                "cls",
                "class_notifier",
                "Check the class schedule",
                {"openjarvis_task_key": "class-schedule-notify"},
            ),
            self._Task("mine", "orchestrator", "Remind me to stretch"),
        ]
        runs = {
            "cls": [
                {
                    "finished_at": "2026-09-15T13:00:00+00:00",
                    "success": 1,
                    "result": json.dumps(quiet),
                },
                {
                    "finished_at": "2026-09-15T13:05:00+00:00",
                    "success": 1,
                    "result": json.dumps(alert),
                },
            ],
            "mine": [
                {
                    "finished_at": "2026-09-15T13:02:00+00:00",
                    "success": 1,
                    "result": json.dumps({"content": "Reminder sent."}),
                },
            ],
        }
        lines = _finished_jobs(self._scheduler(tasks, runs), since=_at(12))
        assert any("Structural Analysis" in line for line in lines)
        assert any("Reminder sent" in line for line in lines)
        assert not any("Nothing upcoming" in line for line in lines)


class TestInitiative:
    """Sage starting a conversation (M37 phase 1, Gentle)."""

    def _settled(self, tmp_path, **settings):
        rig = _Rig(tmp_path, PresenceSettings(enabled=True, **settings))
        rig.tick(_at(13), idle=1.0)  # the day's greeting
        rig.spoken.clear()
        return rig

    def test_speaks_after_a_lull_and_then_cools_down(self, tmp_path) -> None:
        rig = self._settled(tmp_path)
        rig.initiative_line = "How is the paper going, sir?"
        assert rig.tick(_at(13, 4)) == []  # greeting was 4 min ago: not a lull
        said = rig.tick(_at(13, 6))
        assert [r.kind for r in said] == [MOMENT_INITIATIVE]
        assert rig.spoken == ["How is the paper going, sir?"]
        for minute in range(7, 16):
            assert rig.tick(_at(13, minute)) == []  # cooling down
        assert [r.kind for r in rig.tick(_at(13, 17))] == [MOMENT_INITIATIVE]

    def test_the_writer_may_decline_and_that_costs_half_a_cooldown(
        self, tmp_path
    ) -> None:
        rig = self._settled(tmp_path)
        rig.initiative_line = ""
        assert rig.tick(_at(13, 6)) == []
        assert len(rig.initiative_contexts) == 1
        assert rig.engine.snapshot()["last_reason"] == "initiative declined"
        assert rig.tick(_at(13, 10)) == []
        assert len(rig.initiative_contexts) == 1
        rig.initiative_line = "Water break, sir?"
        assert [r.kind for r in rig.tick(_at(13, 12))] == [MOMENT_INITIATIVE]

    def test_a_recent_chat_turn_is_not_a_lull(self, tmp_path) -> None:
        rig = self._settled(tmp_path)
        rig.initiative_line = "Anything I can do, sir?"
        rig.activity = Activity(last_user_turn_at=_at(13, 5), ui_seen=True)
        assert rig.tick(_at(13, 8)) == []
        assert [r.kind for r in rig.tick(_at(13, 11))] == [MOMENT_INITIATIVE]

    def test_busy_means_silence(self, tmp_path) -> None:
        rig = self._settled(tmp_path)
        rig.initiative_line = "A thought, sir."
        rig.busy = ["full-screen: Netflix"]
        assert rig.tick(_at(13, 10)) == []
        assert rig.engine.snapshot()["last_reason"].startswith("busy")
        rig.busy = []
        assert [r.kind for r in rig.tick(_at(13, 11))] == [MOMENT_INITIATIVE]

    def test_hourly_cap(self, tmp_path) -> None:
        rig = self._settled(
            tmp_path, initiative_cooldown_seconds=60, initiative_idle_seconds=60
        )
        rig.initiative_line = "Sir?"
        minute = 6
        for _ in range(6):
            rig.tick(_at(13, minute))
            minute += 2
        assert rig.spoken.count("Sir?") == 3

    def test_off_mode_never_asks_the_writer(self, tmp_path) -> None:
        rig = self._settled(tmp_path, initiative_mode="off")
        rig.initiative_line = "Sir?"
        rig.tick(_at(13, 20))
        assert rig.spoken == [] and rig.initiative_contexts == []

    def test_timed_quiet_silences_every_moment_until_it_lapses(self, tmp_path) -> None:
        rig = self._settled(tmp_path)
        rig.initiative_line = "Sir?"
        rig.engine.snooze_for(30 * 60)  # until 13:30
        for minute in (10, 20, 29):
            assert rig.tick(_at(13, minute)) == []
        assert "quiet for" in rig.engine.snapshot()["last_reason"]
        assert rig.engine.snapshot()["snoozed_until"] is not None
        # It lapses on its own...
        assert [r.kind for r in rig.tick(_at(13, 31))] == [MOMENT_INITIATIVE]
        # ...and "continue" lifts it early.
        rig.engine.snooze_for(30 * 60)
        assert rig.tick(_at(13, 45)) == []
        rig.engine.resume()
        assert [r.kind for r in rig.tick(_at(13, 46))] == [MOMENT_INITIATIVE]

    def test_the_writer_sees_the_mode_and_its_categories(self, tmp_path) -> None:
        rig = self._settled(tmp_path)
        rig.initiative_line = "Sir?"
        rig.tick(_at(13, 6))
        context = rig.initiative_contexts[0]
        assert context["mode"] == "gentle"
        assert context["allowed_categories"] == "contextual, useful"


class TestWaitingForTheInterface:
    """Sage autostarts with Windows. Before the page has been opened once,
    the user may be nowhere near it -- on 18 September it made a remark and
    then followed it up while the window had never been opened."""

    def test_nothing_unprompted_until_the_page_has_been_opened(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.activity = Activity(ui_seen=False)
        rig.tick(_at(13), idle=1.0)
        rig.spoken.clear()
        rig.initiative_line = "Sir, some water perhaps?"
        assert rig.tick(_at(13, 6)) == []
        assert rig.spoken == []
        assert rig.engine.snapshot()["last_reason"] == (
            "the interface has not been opened yet"
        )

    def test_once_opened_it_speaks_even_with_the_window_closed(self, tmp_path) -> None:
        # The gate is about startup, not about the window being open now.
        rig = _Rig(tmp_path)
        rig.activity = Activity(ui_seen=False)
        rig.tick(_at(13), idle=1.0)
        rig.spoken.clear()
        rig.initiative_line = "Sir, some water perhaps?"
        assert rig.tick(_at(13, 6)) == []
        rig.activity = Activity(ui_seen=True)
        assert [r.kind for r in rig.tick(_at(13, 12))] == [MOMENT_INITIATIVE]


class TestFollowUp:
    def _prompted(self, tmp_path):
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)
        rig.spoken.clear()
        rig.initiative_line = "How is the paper going, sir?"
        assert [r.kind for r in rig.tick(_at(13, 6))] == [MOMENT_INITIATIVE]
        return rig

    def test_an_answer_within_the_window_closes_the_prompt(self, tmp_path) -> None:
        rig = self._prompted(tmp_path)
        rig.activity = Activity(last_user_turn_at=_at(13, 6) + 30, ui_seen=True)
        rig.tick(_at(13, 7))
        assert rig.spoken == ["How is the paper going, sir?"]  # no follow-up
        assert rig.engine.history()[-1].detail == "answered"
        assert load_state(tmp_path).unanswered_streak == 0

    def test_one_follow_up_then_let_go(self, tmp_path) -> None:
        rig = self._prompted(tmp_path)
        # A minute of silence is no longer a cue to speak again.
        assert rig.tick(_at(13, 7)) == []
        assert len(rig.spoken) == 1
        rig.tick(_at(13, 6) + FOLLOW_UP_AFTER_SECONDS)
        assert len(rig.spoken) == 2 and rig.spoken[1] in FOLLOW_UP_LINES
        rig.tick(_at(13, 6) + FOLLOW_UP_AFTER_SECONDS + 30)
        assert len(rig.spoken) == 2
        rig.tick(_at(13, 6) + 2 * FOLLOW_UP_AFTER_SECONDS)  # given up
        state = load_state(tmp_path)
        assert state.pending_prompt_at is None and state.unanswered_streak == 1
        assert rig.engine.snapshot()["last_reason"] != "waiting on an answer"

    def test_a_second_unanswered_prompt_is_let_go_in_silence(self, tmp_path) -> None:
        """The user's complaint: having said nothing once, they were asked
        again -- "Only if you feel like it, sir." Follow-ups are kept for the
        rare case, so the second one inside the day says nothing at all."""
        rig = self._prompted(tmp_path)
        rig.tick(_at(13, 6) + FOLLOW_UP_AFTER_SECONDS)
        assert len(rig.spoken) == 2  # the one follow-up of the day
        rig.tick(_at(13, 6) + 2 * FOLLOW_UP_AFTER_SECONDS)

        # Later the same session -- near enough that nothing counts as a
        # return, far enough that initiative may speak again.
        rig.initiative_line = "Sir, some water perhaps?"
        assert [r.kind for r in rig.tick(_at(13, 18))] == [MOMENT_INITIATIVE]
        spoken_before = len(rig.spoken)
        rig.tick(_at(13, 18) + FOLLOW_UP_AFTER_SECONDS + 5)
        # Let go without a word, rather than nudged a second time.
        assert len(rig.spoken) == spoken_before
        assert load_state(tmp_path).pending_prompt_at is None

    def test_ignored_twice_doubles_the_cooldown(self, tmp_path) -> None:
        rig = self._prompted(tmp_path)
        rig.tick(_at(13, 6) + FOLLOW_UP_AFTER_SECONDS)
        rig.tick(_at(13, 6) + 2 * FOLLOW_UP_AFTER_SECONDS)
        assert [r.kind for r in rig.tick(_at(13, 18))] == [MOMENT_INITIATIVE]
        # The second prompt of the day is let go in silence, at the same
        # point the follow-up would have been due.
        rig.tick(_at(13, 18) + FOLLOW_UP_AFTER_SECONDS + 5)
        assert load_state(tmp_path).unanswered_streak == 2
        assert rig.tick(_at(13, 30)) == []  # would have been due; cooldown doubled
        assert rig.engine.snapshot()["last_reason"] == "backing off"
        assert [r.kind for r in rig.tick(_at(13, 39))] == [MOMENT_INITIATIVE]
        # An answer resets it.
        rig.activity = Activity(last_user_turn_at=_at(13, 39) + 10, ui_seen=True)
        rig.tick(_at(13, 40))
        assert load_state(tmp_path).unanswered_streak == 0

    def test_leaving_the_desk_drops_the_prompt_without_blame(self, tmp_path) -> None:
        rig = self._prompted(tmp_path)
        rig.tick(_at(13, 6) + 20, idle=1.0)
        rig.tick(_at(13, 12), idle=400.0)  # away
        assert load_state(tmp_path).pending_prompt_at is None
        assert load_state(tmp_path).unanswered_streak == 0
        assert len(rig.spoken) == 1

    def test_quiet_means_no_follow_up_either(self, tmp_path) -> None:
        rig = self._prompted(tmp_path)
        rig.engine.snooze_for(1800)
        rig.tick(_at(13, 7))
        rig.tick(_at(13, 8))
        assert len(rig.spoken) == 1
        assert load_state(tmp_path).unanswered_streak == 0

    def test_the_record_carries_when_the_audio_ended(self, tmp_path) -> None:
        rig = self._prompted(tmp_path)
        assert rig.engine.history()[-1].ended_at is not None


class TestHeldLine:
    """The user starts talking while the line is being written: it is said
    after the exchange, not over it, and not dropped."""

    def _rig(self, tmp_path):
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)
        rig.spoken.clear()
        return rig

    def test_a_line_written_over_the_users_turn_is_held(self, tmp_path) -> None:
        rig = self._rig(tmp_path)

        def compose(context: dict) -> str:
            # While the writer works, the user starts talking.
            rig.activity = Activity(last_user_turn_at=rig.now + 3, ui_seen=True)
            return "How is the paper, sir?"

        rig.engine._initiative_composer = compose
        assert rig.tick(_at(13, 6)) == []
        assert rig.spoken == []
        assert load_state(tmp_path).held_line == "How is the paper, sir?"
        assert rig.engine.snapshot()["last_reason"] == "held: the user started talking"

    def test_said_once_both_sides_have_been_quiet(self, tmp_path) -> None:
        rig = self._rig(tmp_path)
        rig.engine._state.held_line = "How is the paper, sir?"
        rig.engine._state.held_at = _at(13, 6)
        rig.activity = Activity(
            last_user_turn_at=_at(13, 6) + 5,
            last_reply_end_at=_at(13, 6) + 12,
            ui_seen=True,
        )
        assert rig.tick(_at(13, 6) + 20) == []  # reply ended 8 s ago
        said = rig.tick(_at(13, 6) + 35)
        assert [r.text for r in said] == ["How is the paper, sir?"]
        assert rig.spoken == ["How is the paper, sir?"]
        assert load_state(tmp_path).held_line == ""
        assert load_state(tmp_path).pending_prompt_at is not None  # awaits an answer

    def test_not_while_busy_and_dropped_when_stale(self, tmp_path) -> None:
        rig = self._rig(tmp_path)
        rig.engine._state.held_line = "Sir?"
        rig.engine._state.held_at = _at(13, 6)
        rig.busy = ["full-screen: Netflix"]
        assert rig.tick(_at(13, 8)) == []
        rig.busy = []
        assert rig.tick(_at(13, 17)) == []  # 11 min: stale
        assert rig.spoken == []
        assert load_state(tmp_path).held_line == ""
        assert any(h.detail == "held too long; dropped" for h in rig.engine.history())


class TestPhase3:
    def test_the_reply_is_parsed_into_category_and_line(self) -> None:
        from openjarvis.core.moments import parse_initiative_reply

        assert parse_initiative_reply("[contextual] Did the design settle, sir?") == (
            "contextual",
            "Did the design settle, sir?",
        )
        assert parse_initiative_reply("Useful: a stretch, sir.") == (
            "useful",
            "a stretch, sir.",
        )
        assert parse_initiative_reply("Plain line, sir.") == ("", "Plain line, sir.")
        assert parse_initiative_reply("SKIP") == ("", "")
        assert parse_initiative_reply("") == ("", "")

    def test_the_category_is_recorded_and_never_spoken(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)
        rig.spoken.clear()
        rig.initiative_line = (
            "[curious] Ever wondered why concrete cures faster warm, sir?"
        )
        said = rig.tick(_at(13, 6))
        assert rig.spoken == ["Ever wondered why concrete cures faster warm, sir?"]
        assert said[0].detail == "category=curious"

    def test_the_writer_sees_recent_categories_and_the_fields(self, tmp_path) -> None:
        rig = _Rig(tmp_path)
        rig.tick(_at(13), idle=1.0)
        rig.initiative_line = "[useful] Water, sir."
        rig.tick(_at(13, 6))
        rig.activity = Activity(last_user_turn_at=_at(13, 7), ui_seen=True)
        rig.tick(_at(13, 8))
        rig.initiative_line = "[contextual] And the paper, sir?"
        rig.tick(_at(13, 20))
        context = rig.initiative_contexts[-1]
        assert "[useful] Water, sir." in context["recent_initiatives"]
        assert "civil engineering" in context["fields"]
        assert context["memory_rule"].startswith("Never open a personal subject")

    def test_social_loosens_the_memory_rule(self, tmp_path) -> None:
        rig = _Rig(tmp_path, PresenceSettings(enabled=True, initiative_mode="social"))
        rig.tick(_at(13), idle=1.0)
        rig.initiative_line = "[reflective] You work best after lunch, sir."
        rig.tick(_at(13, 6))
        context = rig.initiative_contexts[-1]
        assert context["memory_rule"].startswith("Social")
        assert context["allowed_categories"].endswith("reflective")

    def test_choosing_a_mode_sets_its_cadence(self) -> None:
        from openjarvis.core.presence import apply_initiative_mode

        settings = PresenceSettings(
            initiative_idle_seconds=45, initiative_cooldown_seconds=60
        )
        apply_initiative_mode(settings, "curious")
        assert (
            settings.initiative_idle_seconds,
            settings.initiative_cooldown_seconds,
            settings.initiative_per_hour,
        ) == (240, 420, 5)
        apply_initiative_mode(settings, "off")
        assert (
            settings.initiative_mode == "off"
            and settings.initiative_idle_seconds == 240
        )
