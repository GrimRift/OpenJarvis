"""Tests for episodes: memory that spans days.

Two things here shipped as findings rather than as design. Scheduled jobs on
the orchestrator agent leave traces indistinguishable from typed messages, so
"Check my calendar before 9 AM" would have read as something the user said
every single day. And the whole feature sits under the M36 master switch,
which must mean the model is never called when it is off.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from openjarvis.memory.episodes import (
    Episode,
    collect_turns,
    format_recent_days,
    load_episodes,
    recent_episodes,
    save_episode,
    transcript_for_prompt,
)


def _traces(tmp_path: Path, rows) -> Path:
    path = tmp_path / "traces.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE traces (agent TEXT, query TEXT, result TEXT, started_at TEXT)"
    )
    conn.executemany("INSERT INTO traces VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return path


def _scheduler(tmp_path: Path, prompts) -> Path:
    path = tmp_path / "scheduler.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE scheduled_tasks (prompt TEXT)")
    conn.executemany("INSERT INTO scheduled_tasks VALUES (?)", [(p,) for p in prompts])
    conn.commit()
    conn.close()
    return path


def _at(day: date, hour: int) -> str:
    return str(datetime(day.year, day.month, day.day, hour).timestamp())


class TestCollectTurns:
    def test_only_the_users_agents_count(self, tmp_path: Path) -> None:
        today = date.today()
        traces = _traces(tmp_path, [
            ("orchestrator", "fix the waze pack", "done", _at(today, 10)),
            ("class_notifier", "Check the class schedule", "nothing", _at(today, 11)),
            ("morning_digest", "Generate my morning digest.", "...", _at(today, 5)),
        ])
        turns = collect_turns(today, traces_path=traces, scheduler_path=tmp_path / "x")
        assert [t.query for t in turns] == ["fix the waze pack"]

    def test_scheduled_prompts_are_not_the_user_talking(self, tmp_path: Path) -> None:
        # The calendar check runs on the orchestrator agent every morning.
        # Without this, every episode would open with the user "asking" it.
        today = date.today()
        prompt = "Check my calendar for any events before 9:00 AM today."
        traces = _traces(tmp_path, [
            ("orchestrator", prompt, "clear", _at(today, 8)),
            ("orchestrator", "what did we do yesterday", "...", _at(today, 9)),
        ])
        scheduler = _scheduler(tmp_path, [prompt])
        turns = collect_turns(today, traces_path=traces, scheduler_path=scheduler)
        assert [t.query for t in turns] == ["what did we do yesterday"]

    def test_only_that_day(self, tmp_path: Path) -> None:
        today = date.today()
        yesterday = today - timedelta(days=1)
        traces = _traces(tmp_path, [
            ("orchestrator", "old", "x", _at(yesterday, 12)),
            ("orchestrator", "new", "y", _at(today, 12)),
        ])
        turns = collect_turns(today, traces_path=traces, scheduler_path=tmp_path / "x")
        assert [t.query for t in turns] == ["new"]

    def test_a_missing_store_is_no_turns(self, tmp_path: Path) -> None:
        assert collect_turns(date.today(), traces_path=tmp_path / "nope.db") == []


class TestStore:
    def test_round_trip_and_recent(self, tmp_path: Path) -> None:
        today = date.today()
        for offset, text in ((1, "yesterday's work"), (2, "two days ago"), (9, "old")):
            day = (today - timedelta(days=offset)).isoformat()
            save_episode(Episode(day, text, 5, time.time()), tmp_path)
        recent = recent_episodes(count=3, today=today, config_dir=tmp_path)
        # The last three on file, however old, oldest first.
        got = [e.summary for e in recent]
        assert got == ["old", "two days ago", "yesterday's work"]
        two = recent_episodes(count=2, today=today, config_dir=tmp_path)
        assert [e.summary for e in two] == ["two days ago", "yesterday's work"]

    def test_recent_means_last_conversations_not_last_calendar_days(
        self, tmp_path: Path
    ) -> None:
        # Four days without a conversation, then "what were we working on
        # yesterday": a calendar window found nothing. The last conversations
        # are what is wanted, however long ago, and the gap is stated.
        today = date(2026, 9, 13)
        save_episode(Episode("2026-09-08", "tuesday work", 5, time.time()), tmp_path)
        recent = recent_episodes(count=3, today=today, config_dir=tmp_path)
        assert [e.day for e in recent] == ["2026-09-08"]
        text = format_recent_days(recent, today=today)
        assert text.startswith("Tuesday: tuesday work")
        assert "no conversations between Tuesday and today (5 days)" in text

    def test_today_is_never_recent(self, tmp_path: Path) -> None:
        today = date.today()
        save_episode(Episode(today.isoformat(), "today", 3, time.time()), tmp_path)
        assert recent_episodes(count=3, today=today, config_dir=tmp_path) == []

    def test_old_episodes_are_pruned_on_save(self, tmp_path: Path) -> None:
        today = date.today()
        old = (today - timedelta(days=60)).isoformat()
        (tmp_path / "episodes.json").write_text(
            json.dumps({old: {"summary": "ancient", "turns": 1, "written_at": 1}}),
            encoding="utf-8",
        )
        save_episode(Episode(today.isoformat(), "now", 1, time.time()), tmp_path)
        assert old not in load_episodes(tmp_path)

    def test_a_damaged_file_is_no_history(self, tmp_path: Path) -> None:
        (tmp_path / "episodes.json").write_text("{nope", encoding="utf-8")
        assert load_episodes(tmp_path) == {}


class TestFormatting:
    def test_days_are_named_relative_to_today(self) -> None:
        today = date(2026, 9, 13)  # a Sunday
        episodes = [
            Episode("2026-09-11", "friday things", 1, 0),
            Episode("2026-09-12", "saturday things", 1, 0),
        ]
        text = format_recent_days(episodes, today=today)
        assert text.startswith("Friday: friday things")
        assert "Yesterday: saturday things" in text

    def test_transcript_is_bounded(self) -> None:
        from openjarvis.memory.episodes import Turn

        turns = [Turn(at=0, query="q" * 1000, result="r" * 5000) for _ in range(200)]
        text = transcript_for_prompt(turns)
        assert text.count("User:") <= 80
        assert "q" * 301 not in text
        assert "r" * 401 not in text


class TestAgentGate:
    def test_off_means_no_model_call_and_nothing_written(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        from openjarvis.agents.episode_writer import EpisodeWriterAgent

        agent = EpisodeWriterAgent.__new__(EpisodeWriterAgent)
        generated = []
        agent._generate = lambda messages: generated.append(1) or {"content": "x"}
        off = SimpleNamespace(enabled=False, episodes_enabled=True)
        with patch("openjarvis.agents.episode_writer.load_settings", return_value=off):
            result = agent.run("ignored")
        assert generated == []
        assert "switched off" in result.content

    def test_a_quiet_day_writes_nothing(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        from openjarvis.agents.episode_writer import EpisodeWriterAgent

        agent = EpisodeWriterAgent.__new__(EpisodeWriterAgent)
        generated = []
        agent._generate = lambda messages: generated.append(1) or {"content": "x"}
        on = SimpleNamespace(enabled=True, episodes_enabled=True)
        with (
            patch("openjarvis.agents.episode_writer.load_settings", return_value=on),
            patch("openjarvis.agents.episode_writer.collect_turns", return_value=[]),
        ):
            result = agent.run("ignored")
        assert generated == []
        assert "No conversations" in result.content
