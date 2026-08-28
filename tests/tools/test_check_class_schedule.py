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


def test_reading_the_schedule_twice_still_reports_the_class(tmp_path):
    """This tool is read-only. It used to record a "notified" marker just for
    looking, so the 08:00 day-summary task silently consumed every reminder on
    the schedule hours before any of them were due."""
    now = datetime(2026, 3, 10, 9, 50)
    schedule = _write_schedule(tmp_path, now.strftime("%A"))
    tool = _make_tool(tmp_path, schedule)

    first = tool.execute(now=now, lookahead_minutes=15)
    second = tool.execute(now=now + timedelta(minutes=1), lookahead_minutes=15)

    assert len(first.metadata["upcoming"]) == 1
    assert len(second.metadata["upcoming"]) == 1


def test_the_checker_owns_no_notification_state(tmp_path):
    """Suppression belongs to whatever delivers a notification. Holding it here
    is what let the day-summary read cancel the day's reminders."""
    tool = _make_tool(tmp_path, _write_schedule(tmp_path, "Monday"))

    assert not hasattr(tool, "_state_path")


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


# -- Day view -----------------------------------------------------------------


def _write_day(tmp_path, day_name: str):
    """Three classes on one day, mirroring the real note's shape."""
    path = tmp_path / "Class Schedule.md"
    rows = [
        ("EARLY1", "Early Class", "S1", "11:00AM–01:00PM", "Mezz 8"),
        ("MID222", "Midday Class", "S2", "03:00PM–05:00PM", "CEA-E01"),
        ("LATE33", "Late Class", "S3", "05:00PM–07:00PM", "Mezz 6"),
    ]
    path.write_text(
        "# Class Schedule\n\n## Subjects\n\n"
        "| Subject Code | Subject Description | Section | Day | Time "
        "| Room | Mode | Instructor |\n"
        "|---|---|---|---|---|---|---|---|\n"
        + "".join(
            f"| {code} | {desc} | {sec} | {day_name} | {time} "
            f"| {room} | In-person | Prof X |\n"
            for code, desc, sec, time, room in rows
        ),
        encoding="utf-8",
    )
    return path


def test_full_day_lists_every_class_with_a_status(tmp_path):
    """The reported bug: at 17:05 with three classes on the note and one in
    session, Sage answered "no classes scheduled for today"."""
    now = datetime(2026, 3, 13, 17, 5)  # a Friday, 5 minutes into the last class
    tool = _make_tool(tmp_path, _write_day(tmp_path, now.strftime("%A")))

    result = tool.execute(now=now, full_day=True)

    by_code = {c["subject_code"]: c for c in result.metadata["classes"]}
    assert set(by_code) == {"EARLY1", "MID222", "LATE33"}
    assert by_code["EARLY1"]["status"] == "ended"
    assert by_code["MID222"]["status"] == "ended"
    assert by_code["LATE33"]["status"] == "in_progress"
    assert "3 class(es)" in result.content


def test_full_day_marks_classes_that_have_not_started(tmp_path):
    now = datetime(2026, 3, 13, 8, 0)
    tool = _make_tool(tmp_path, _write_day(tmp_path, now.strftime("%A")))

    statuses = {
        c["subject_code"]: c["status"]
        for c in tool.execute(now=now, full_day=True).metadata["classes"]
    }
    assert set(statuses.values()) == {"upcoming"}


def test_full_day_on_a_free_day_says_so(tmp_path):
    class_day = datetime(2026, 3, 13, 12, 0)
    other_day = class_day - timedelta(days=1)
    tool = _make_tool(tmp_path, _write_day(tmp_path, class_day.strftime("%A")))

    result = tool.execute(now=other_day, full_day=True)

    assert result.metadata["classes"] == []
    assert "No classes scheduled for today" in result.content


def test_the_reminder_window_still_excludes_a_started_class(tmp_path):
    """full_day must not change the narrow mode — notifications depend on it."""
    now = datetime(2026, 3, 13, 17, 5)
    tool = _make_tool(tmp_path, _write_day(tmp_path, now.strftime("%A")))

    assert tool.execute(now=now).metadata["upcoming"] == []


def test_a_huge_lookahead_still_cannot_reveal_a_started_class(tmp_path):
    """Documents why full_day had to exist: raising lookahead never helps, and
    the old description told the model to try exactly that."""
    now = datetime(2026, 3, 13, 17, 5)
    tool = _make_tool(tmp_path, _write_day(tmp_path, now.strftime("%A")))

    assert tool.execute(now=now, lookahead_minutes=1440).metadata["upcoming"] == []


def test_the_reminder_window_still_finds_an_imminent_class(tmp_path):
    now = datetime(2026, 3, 13, 16, 50)
    tool = _make_tool(tmp_path, _write_day(tmp_path, now.strftime("%A")))

    upcoming = tool.execute(now=now).metadata["upcoming"]
    assert [c["subject_code"] for c in upcoming] == ["LATE33"]
