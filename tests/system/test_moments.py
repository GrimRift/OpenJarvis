"""Tests for the moments engine (M36 phase 3).

Every moment is guarded by presence, a daily cap, quiet hours and "not now".
A wrong guard is worse than no moment at all -- a greeting to an empty room,
a second good morning, a voice at midnight -- so each guard is exercised
with an injected clock, a scripted presence monitor and a recording speaker
rather than a desk and a pair of speakers.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from openjarvis.core.moments import (
    DAILY_CAPS,
    MOMENT_GREETING,
    MOMENT_TOLD,
    MOMENT_WELCOME_BACK,
    MomentEngine,
    fallback_text,
    in_quiet_hours,
    load_state,
    local_to_timestamp,
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
            scheduler_lookup=lambda: None,
            timezone_name=TZ,
        )

    def _compose(self, kind: str, context: dict) -> str:
        self.compose_calls.append(kind)
        self.last_context = context
        return f"[{kind}] {context.get('away_for', '')}".strip()

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
            fallback_text(MOMENT_GREETING, {"time_of_day": "evening"})
            == "Good evening, sir."
        )
        assert "day" in fallback_text(MOMENT_GREETING, {})
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
        assert said[0].text == "Good morning, sir."
        assert "cloud down" in said[0].detail
        assert rig.spoken == ["Good morning, sir."]
        rig.engine._composer = broken
        rig.leave_and_return(_at(9), _at(11))
        assert rig.spoken[-1] == "Welcome back, sir. You were away 1 hour 55 min."

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
