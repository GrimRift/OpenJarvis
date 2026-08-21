"""Tests for the check_class_schedule tool."""

from __future__ import annotations

from datetime import datetime, timedelta

from openjarvis.core.registry import ToolRegistry


def _write_schedule(tmp_path, day_name: str, time_range: str = "10:00AM–11:00AM"):
    path = tmp_path / "Class Schedule.md"
    path.write_text(
        "# Class Schedule\n"
        "\n"
        "## Subjects\n"
        "\n"
        "| Subject Code | Subject Description | Section | Day | Time "
        "| Room | Mode | Instructor |\n"
        "|---|---|---|---|---|---|---|---|\n"
        f"| CS101 | Intro to Testing | SEC1 | {day_name} | {time_range} "
        "| Room 1 | In-person | Prof X |\n",
        encoding="utf-8",
    )
    return path


def _make_tool(tmp_path, schedule_path=None):
    from openjarvis.tools.check_class_schedule import CheckClassScheduleTool

    return CheckClassScheduleTool(
        schedule_path=str(schedule_path) if schedule_path else None,
        state_path=tmp_path / "state.json",
    )


def test_check_class_schedule_registered():
    from openjarvis.tools.check_class_schedule import CheckClassScheduleTool

    ToolRegistry.register_value("check_class_schedule", CheckClassScheduleTool)
    assert ToolRegistry.contains("check_class_schedule")


def test_upcoming_class_detected(tmp_path):
    now = datetime(2026, 3, 10, 9, 50)  # 10 minutes before a 10:00AM class
    day_name = now.strftime("%A")
    schedule = _write_schedule(tmp_path, day_name)
    tool = _make_tool(tmp_path, schedule)

    result = tool.execute(now=now, lookahead_minutes=15)

    assert result.success is True
    assert len(result.metadata["upcoming"]) == 1
    assert result.metadata["upcoming"][0]["subject_code"] == "CS101"


def test_class_not_yet_in_window(tmp_path):
    now = datetime(2026, 3, 10, 9, 30)  # 30 minutes before, default window is 15
    day_name = now.strftime("%A")
    schedule = _write_schedule(tmp_path, day_name)
    tool = _make_tool(tmp_path, schedule)

    result = tool.execute(now=now)

    assert result.success is True
    assert result.metadata["upcoming"] == []


def test_class_already_started_excluded(tmp_path):
    now = datetime(2026, 3, 10, 10, 5)  # 5 minutes after start
    day_name = now.strftime("%A")
    schedule = _write_schedule(tmp_path, day_name)
    tool = _make_tool(tmp_path, schedule)

    result = tool.execute(now=now, lookahead_minutes=15)

    assert result.metadata["upcoming"] == []


def test_day_of_week_boundary(tmp_path):
    class_day = datetime(2026, 3, 10, 9, 50)
    wrong_day = class_day - timedelta(days=1)  # same clock time, previous day
    day_name = class_day.strftime("%A")
    schedule = _write_schedule(tmp_path, day_name)
    tool = _make_tool(tmp_path, schedule)

    result = tool.execute(now=wrong_day, lookahead_minutes=15)

    assert result.metadata["upcoming"] == []


def test_dedup_same_occurrence_not_renotified(tmp_path):
    now = datetime(2026, 3, 10, 9, 50)
    day_name = now.strftime("%A")
    schedule = _write_schedule(tmp_path, day_name)
    tool = _make_tool(tmp_path, schedule)

    first = tool.execute(now=now, lookahead_minutes=15)
    second = tool.execute(now=now + timedelta(minutes=1), lookahead_minutes=15)

    assert len(first.metadata["upcoming"]) == 1
    assert second.metadata["upcoming"] == []


def test_dedup_resets_next_day(tmp_path):
    now = datetime(2026, 3, 10, 9, 50)
    day_name = now.strftime("%A")
    schedule = _write_schedule(tmp_path, day_name)
    tool = _make_tool(tmp_path, schedule)

    tool.execute(now=now, lookahead_minutes=15)
    next_week_same_day = now + timedelta(days=7)  # same weekday, new date
    second = tool.execute(now=next_week_same_day, lookahead_minutes=15)

    assert len(second.metadata["upcoming"]) == 1


def test_missing_file_returns_failure(tmp_path):
    tool = _make_tool(tmp_path, tmp_path / "does-not-exist.md")

    result = tool.execute(now=datetime(2026, 3, 10, 9, 50))

    assert result.success is False


def test_malformed_table_returns_failure(tmp_path):
    path = tmp_path / "Class Schedule.md"
    path.write_text("# Class Schedule\n\nNo subjects section here.\n", encoding="utf-8")
    tool = _make_tool(tmp_path, path)

    result = tool.execute(now=datetime(2026, 3, 10, 9, 50))

    assert result.success is False
