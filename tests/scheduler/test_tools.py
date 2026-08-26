"""Tests for scheduler MCP tools."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from unittest.mock import MagicMock

from openjarvis.scheduler.scheduler import ScheduledTask
from openjarvis.scheduler.tools import (
    CancelScheduledTaskTool,
    ListScheduledTasksTool,
    PauseScheduledTaskTool,
    ResumeScheduledTaskTool,
    ScheduleTaskTool,
)

# -- Spec correctness --------------------------------------------------------


class TestToolSpecs:
    def test_schedule_task_spec(self):
        tool = ScheduleTaskTool()
        assert tool.spec.name == "schedule_task"
        assert "prompt" in tool.spec.parameters["properties"]
        assert "schedule_type" in tool.spec.parameters["properties"]

    def test_list_spec(self):
        tool = ListScheduledTasksTool()
        assert tool.spec.name == "list_scheduled_tasks"

    def test_pause_spec(self):
        tool = PauseScheduledTaskTool()
        assert tool.spec.name == "pause_scheduled_task"

    def test_resume_spec(self):
        tool = ResumeScheduledTaskTool()
        assert tool.spec.name == "resume_scheduled_task"

    def test_cancel_spec(self):
        tool = CancelScheduledTaskTool()
        assert tool.spec.name == "cancel_scheduled_task"

    def test_all_tools_have_scheduler_category(self):
        for cls in [
            ScheduleTaskTool,
            ListScheduledTasksTool,
            PauseScheduledTaskTool,
            ResumeScheduledTaskTool,
            CancelScheduledTaskTool,
        ]:
            assert cls().spec.category == "scheduler"


# -- Scheduler not available --------------------------------------------------


class TestNoScheduler:
    def test_schedule_task_no_scheduler(self):
        tool = ScheduleTaskTool()
        tool._scheduler = None
        result = tool.execute(
            prompt="hello", schedule_type="once", schedule_value="2026-01-01"
        )
        assert not result.success
        assert "not available" in result.content

    def test_list_no_scheduler(self):
        tool = ListScheduledTasksTool()
        tool._scheduler = None
        result = tool.execute()
        assert not result.success

    def test_pause_no_scheduler(self):
        tool = PauseScheduledTaskTool()
        tool._scheduler = None
        result = tool.execute(task_id="abc")
        assert not result.success

    def test_resume_no_scheduler(self):
        tool = ResumeScheduledTaskTool()
        tool._scheduler = None
        result = tool.execute(task_id="abc")
        assert not result.success

    def test_cancel_no_scheduler(self):
        tool = CancelScheduledTaskTool()
        tool._scheduler = None
        result = tool.execute(task_id="abc")
        assert not result.success


# -- With injected scheduler --------------------------------------------------


class TestWithScheduler:
    def test_schedule_task(self):
        mock_sched = MagicMock()
        mock_sched.create_task.return_value = ScheduledTask(
            id="t123",
            prompt="hello",
            schedule_type="once",
            schedule_value="2026-01-01T00:00:00",
            next_run="2026-01-01T00:00:00",
        )
        tool = ScheduleTaskTool()
        tool._scheduler = mock_sched
        result = tool.execute(
            prompt="hello", schedule_type="once", schedule_value="2026-01-01T00:00:00"
        )
        assert result.success
        assert "t123" in result.content
        mock_sched.create_task.assert_called_once()

    def test_list_scheduled_tasks(self):
        mock_sched = MagicMock()
        mock_sched.list_tasks.return_value = [
            ScheduledTask(
                id="t1", prompt="a", schedule_type="interval", schedule_value="60"
            ),
        ]
        tool = ListScheduledTasksTool()
        tool._scheduler = mock_sched
        result = tool.execute()
        assert result.success
        assert "t1" in result.content

    def test_schedule_task_missing_params(self):
        tool = ScheduleTaskTool()
        tool._scheduler = MagicMock()
        result = tool.execute(prompt="hello")  # missing schedule_type, schedule_value
        assert not result.success
        assert "Missing" in result.content

    def test_pause_missing_task_id(self):
        tool = PauseScheduledTaskTool()
        tool._scheduler = MagicMock()
        result = tool.execute()  # missing task_id
        assert not result.success
        assert "Missing" in result.content


# -- Registration ------------------------------------------------------------


class TestRegistration:
    def test_importing_the_tools_package_registers_them(self):
        """These live under openjarvis.scheduler, so tools/__init__ imports them.

        Run in a subprocess: conftest clears ToolRegistry before each test and
        the modules are already cached, so an in-process import would neither
        re-run the decorators nor prove the wiring. Reloading instead would
        rebind the tool classes and break other tests in this file.
        """
        code = (
            "import openjarvis.tools;"
            "from openjarvis.core.registry import ToolRegistry;"
            "missing=[n for n in ('schedule_task','list_scheduled_tasks',"
            "'pause_scheduled_task','resume_scheduled_task',"
            "'cancel_scheduled_task') if n not in ToolRegistry.keys()];"
            "raise SystemExit('unregistered: %s' % missing if missing else 0)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr


# -- Deferred scheduler injection -------------------------------------------


class TestSetScheduler:
    def test_injection_reaches_instances_built_beforehand(self):
        """Tools are constructed at startup before the scheduler exists."""
        from openjarvis.scheduler.tools import set_scheduler

        tool = ScheduleTaskTool()
        assert not tool.execute(
            prompt="p", schedule_type="interval", schedule_value="60"
        ).success

        mock_sched = MagicMock()
        mock_sched.create_task.return_value = ScheduledTask(
            id="t1", prompt="p", schedule_type="interval", schedule_value="60"
        )
        set_scheduler(mock_sched)
        try:
            assert tool.execute(
                prompt="p", schedule_type="interval", schedule_value="60"
            ).success
        finally:
            set_scheduler(None)


# -- Cron timezone conversion ------------------------------------------------


class TestCronTimezone:
    """Cron is evaluated against UTC, so a local time must be converted."""

    @staticmethod
    def _at_plus_eight():
        from unittest.mock import patch

        return patch("openjarvis.scheduler.tools._utc_offset_hours", return_value=8)

    def test_converts_local_hour_to_utc(self):
        from openjarvis.scheduler.tools import _local_cron_to_utc

        with self._at_plus_eight():
            assert _local_cron_to_utc("0 8 * * *")[0] == "0 0 * * *"
            assert _local_cron_to_utc("30 14 * * *")[0] == "30 6 * * *"

    def test_shifts_day_of_week_when_crossing_a_utc_day_boundary(self):
        from openjarvis.scheduler.tools import _local_cron_to_utc

        with self._at_plus_eight():
            # Monday 05:00 local is Sunday 21:00 UTC.
            assert _local_cron_to_utc("0 5 * * 1")[0] == "0 21 * * 0"

    def test_no_conversion_when_local_is_utc(self):
        from unittest.mock import patch

        from openjarvis.scheduler.tools import _local_cron_to_utc

        with patch("openjarvis.scheduler.tools._utc_offset_hours", return_value=0):
            assert _local_cron_to_utc("0 5 * * *") == ("0 5 * * *", "")

    def test_ambiguous_expressions_are_left_alone_with_an_explanation(self):
        """Storing as-is and saying so beats shifting it wrongly."""
        from openjarvis.scheduler.tools import _local_cron_to_utc

        with self._at_plus_eight():
            for expr in ("0 */2 * * *", "0 2 15 * *"):
                converted, note = _local_cron_to_utc(expr)
                assert converted == expr
                assert note

    def test_interval_schedules_are_never_converted(self):
        from openjarvis.scheduler.tools import set_scheduler

        mock_sched = MagicMock()
        mock_sched.create_task.return_value = ScheduledTask(
            id="t1", prompt="p", schedule_type="interval", schedule_value="300"
        )
        set_scheduler(mock_sched)
        try:
            with self._at_plus_eight():
                ScheduleTaskTool().execute(
                    prompt="p", schedule_type="interval", schedule_value="300"
                )
            assert mock_sched.create_task.call_args.kwargs["schedule_value"] == "300"
        finally:
            set_scheduler(None)


class TestDefaultAgent:
    def test_defaults_to_a_tool_calling_agent(self):
        """SimpleAgent is single-turn: a scheduled 'check X and notify me'
        would generate text and do nothing."""
        from openjarvis.scheduler.tools import set_scheduler

        mock_sched = MagicMock()
        mock_sched.create_task.return_value = ScheduledTask(
            id="t1", prompt="p", schedule_type="interval", schedule_value="60"
        )
        set_scheduler(mock_sched)
        try:
            ScheduleTaskTool().execute(
                prompt="check my calendar",
                schedule_type="interval",
                schedule_value="60",
            )
            assert mock_sched.create_task.call_args.kwargs["agent"] == "orchestrator"
        finally:
            set_scheduler(None)

    def test_explicit_agent_is_respected(self):
        from openjarvis.scheduler.tools import set_scheduler

        mock_sched = MagicMock()
        mock_sched.create_task.return_value = ScheduledTask(
            id="t1", prompt="p", schedule_type="interval", schedule_value="60"
        )
        set_scheduler(mock_sched)
        try:
            ScheduleTaskTool().execute(
                prompt="p",
                schedule_type="interval",
                schedule_value="60",
                agent="simple",
            )
            assert mock_sched.create_task.call_args.kwargs["agent"] == "simple"
        finally:
            set_scheduler(None)


class TestOnceTimezone:
    """get_due_tasks compares next_run to a UTC-aware ISO string as text."""

    def test_naive_datetime_is_converted_to_an_explicit_utc_instant(self):
        from openjarvis.scheduler.tools import _local_once_to_utc

        converted, note = _local_once_to_utc("2026-08-27T13:00:00")
        parsed = datetime.fromisoformat(converted)
        assert parsed.tzinfo is not None, "a naive value is silently read as UTC"
        assert parsed == datetime(2026, 8, 27, 13, 0).astimezone()
        assert note

    def test_explicit_offset_is_left_alone(self):
        from openjarvis.scheduler.tools import _local_once_to_utc

        assert _local_once_to_utc("2026-08-27T13:00:00+05:00") == (
            "2026-08-27T13:00:00+05:00",
            "",
        )

    def test_unparseable_value_is_passed_through(self):
        from openjarvis.scheduler.tools import _local_once_to_utc

        assert _local_once_to_utc("not-a-date") == ("not-a-date", "")

    def test_once_schedules_are_stored_timezone_aware(self):
        from openjarvis.scheduler.tools import set_scheduler

        mock_sched = MagicMock()
        mock_sched.create_task.return_value = ScheduledTask(
            id="t1", prompt="p", schedule_type="once", schedule_value="x"
        )
        set_scheduler(mock_sched)
        try:
            ScheduleTaskTool().execute(
                prompt="remind me",
                schedule_type="once",
                schedule_value="2026-08-27T08:00:00",
            )
            stored = mock_sched.create_task.call_args.kwargs["schedule_value"]
            assert datetime.fromisoformat(stored).tzinfo is not None
        finally:
            set_scheduler(None)
