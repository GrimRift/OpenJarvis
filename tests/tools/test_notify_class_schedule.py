"""Tests for notify_class_schedule — in particular, who owns the once-per-day
suppression state.

It used to live in check_class_schedule, which meant merely *reading* the
schedule consumed the day's reminders: the 08:00 "what's coming up today"
task summarised the day with a wide lookahead and the 11:00 class was never
announced, because the summary had already marked it sent.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

_ROWS = [
    ("CS101", "Intro to Testing", "SEC1", "10:00AM–11:00AM"),
    ("CS202", "Advanced Testing", "SEC2", "03:00PM–05:00PM"),
]


def _write_schedule(tmp_path, day_name: str):
    path = tmp_path / "Class Schedule.md"
    header = (
        "# Class Schedule\n\n## Subjects\n\n"
        "| Subject Code | Subject Description | Section | Day | Time "
        "| Room | Mode | Instructor |\n"
        "|---|---|---|---|---|---|---|---|\n"
    )
    body = "".join(
        f"| {code} | {desc} | {sec} | {day_name} | {time} "
        "| Room 1 | In-person | Prof X |\n"
        for code, desc, sec, time in _ROWS
    )
    path.write_text(header + body, encoding="utf-8")
    return path


def _make_tool(tmp_path, day_name):
    from openjarvis.tools.notify_class_schedule import NotifyClassScheduleTool

    return NotifyClassScheduleTool(
        schedule_path=str(_write_schedule(tmp_path, day_name)),
        state_path=tmp_path / "state.json",
    )


def test_a_prior_wide_lookahead_check_does_not_suppress_the_reminder(tmp_path):
    """The regression: the day-summary read must leave the reminder intact.

    Goes through the notifier's own checker, because in production both paths
    share one checker configuration -- that shared state file is precisely
    what let a read cancel a reminder.
    """
    now = datetime(2026, 3, 10, 9, 50)
    tool = _make_tool(tmp_path, now.strftime("%A"))

    tool._checker.execute(now=datetime(2026, 3, 10, 8, 0), lookahead_minutes=1440)

    with patch("openjarvis.tools.notify_class_schedule.deliver") as deliver:
        result = tool.execute(now=now, lookahead_minutes=15)

    assert deliver.call_count == 1
    assert result.metadata["notified"] is True


def test_the_same_class_is_only_announced_once(tmp_path):
    now = datetime(2026, 3, 10, 9, 50)
    tool = _make_tool(tmp_path, now.strftime("%A"))

    with patch("openjarvis.tools.notify_class_schedule.deliver") as deliver:
        tool.execute(now=now, lookahead_minutes=15)
        second = tool.execute(now=now + timedelta(minutes=1), lookahead_minutes=15)

    assert deliver.call_count == 1
    assert second.metadata["notified"] is False


def test_suppression_resets_on_a_new_date(tmp_path):
    now = datetime(2026, 3, 10, 9, 50)
    tool = _make_tool(tmp_path, now.strftime("%A"))

    with patch("openjarvis.tools.notify_class_schedule.deliver") as deliver:
        tool.execute(now=now, lookahead_minutes=15)
        tool.execute(now=now + timedelta(days=7), lookahead_minutes=15)

    assert deliver.call_count == 2


def test_a_failed_toast_is_retried_rather_than_suppressed(tmp_path):
    """Recording on failure would silently cost the user the reminder."""
    now = datetime(2026, 3, 10, 9, 50)
    tool = _make_tool(tmp_path, now.strftime("%A"))

    with patch(
        "openjarvis.tools.notify_class_schedule.deliver",
        side_effect=RuntimeError("toast backend down"),
    ):
        failed = tool.execute(now=now, lookahead_minutes=15)
    assert failed.success is False

    with patch("openjarvis.tools.notify_class_schedule.deliver") as deliver:
        retried = tool.execute(now=now + timedelta(minutes=1), lookahead_minutes=15)

    assert deliver.call_count == 1
    assert retried.metadata["notified"] is True


def test_a_partial_failure_keeps_the_delivered_one_suppressed(tmp_path):
    """Two classes in one window: the one that went out must not repeat, the
    one that did not must be retried."""
    now = datetime(2026, 3, 10, 9, 50)
    tool = _make_tool(tmp_path, now.strftime("%A"))
    # Both classes land in the window with a lookahead wide enough to span them.
    lookahead = 400

    with patch(
        "openjarvis.tools.notify_class_schedule.deliver",
        side_effect=[None, RuntimeError("backend down")],
    ) as deliver:
        tool.execute(now=now, lookahead_minutes=lookahead)
    assert deliver.call_count == 2

    with patch("openjarvis.tools.notify_class_schedule.deliver") as deliver:
        tool.execute(now=now + timedelta(minutes=1), lookahead_minutes=lookahead)

    assert deliver.call_count == 1
    assert "CS202" in deliver.call_args[0][1]


def test_nothing_upcoming_writes_no_state(tmp_path):
    now = datetime(2026, 3, 10, 6, 0)
    tool = _make_tool(tmp_path, now.strftime("%A"))

    with patch("openjarvis.tools.notify_class_schedule.deliver") as deliver:
        result = tool.execute(now=now, lookahead_minutes=15)

    assert deliver.call_count == 0
    assert result.metadata["notified"] is False
    assert not (tmp_path / "state.json").exists()


def test_an_unreadable_schedule_fails_without_claiming_a_notification(tmp_path):
    from openjarvis.tools.notify_class_schedule import NotifyClassScheduleTool

    tool = NotifyClassScheduleTool(
        schedule_path=str(tmp_path / "missing.md"),
        state_path=tmp_path / "state.json",
    )

    with patch("openjarvis.tools.notify_class_schedule.deliver") as deliver:
        result = tool.execute(now=datetime(2026, 3, 10, 9, 50))

    assert result.success is False
    assert deliver.call_count == 0
