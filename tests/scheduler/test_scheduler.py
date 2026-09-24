"""Tests for TaskScheduler — scheduling logic, lifecycle, and execution."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from openjarvis.scheduler.scheduler import ScheduledTask, TaskScheduler
from openjarvis.scheduler.store import SchedulerStore


@pytest.fixture()
def store(tmp_path):
    s = SchedulerStore(tmp_path / "scheduler_test.db")
    yield s
    s.close()


@pytest.fixture()
def scheduler(store):
    sched = TaskScheduler(store, poll_interval=1)
    yield sched
    sched.stop()


# -- ScheduledTask dataclass -------------------------------------------------


class TestScheduledTask:
    def test_round_trip(self):
        task = ScheduledTask(
            id="abc123",
            prompt="hello",
            schedule_type="interval",
            schedule_value="60",
            agent="orchestrator",
            tools="calculator,think",
            metadata={"key": "value"},
        )
        d = task.to_dict()
        restored = ScheduledTask.from_dict(d)
        assert restored.id == "abc123"
        assert restored.prompt == "hello"
        assert restored.schedule_type == "interval"
        assert restored.agent == "orchestrator"
        assert restored.tools == "calculator,think"
        assert restored.metadata == {"key": "value"}

    def test_defaults(self):
        task = ScheduledTask(
            id="x",
            prompt="p",
            schedule_type="once",
            schedule_value="2026-01-01T00:00:00",
        )
        assert task.context_mode == "isolated"
        assert task.status == "active"
        assert task.agent == "simple"
        assert task.tools == ""
        assert task.metadata == {}


# -- TaskScheduler create/list -----------------------------------------------


class TestCreateAndList:
    def test_create_task(self, scheduler):
        task = scheduler.create_task(
            prompt="hello world",
            schedule_type="interval",
            schedule_value="3600",
        )
        assert task.id
        assert task.prompt == "hello world"
        assert task.schedule_type == "interval"
        assert task.next_run is not None
        assert task.status == "active"

    def test_create_task_with_agent_and_tools(self, scheduler):
        task = scheduler.create_task(
            prompt="hello",
            schedule_type="once",
            schedule_value="2099-01-01T00:00:00+00:00",
            agent="orchestrator",
            tools="calculator,think",
        )
        assert task.agent == "orchestrator"
        assert task.tools == "calculator,think"

    def test_list_tasks_empty(self, scheduler):
        assert scheduler.list_tasks() == []

    def test_list_tasks(self, scheduler):
        scheduler.create_task("a", "interval", "60")
        scheduler.create_task("b", "interval", "120")
        assert len(scheduler.list_tasks()) == 2

    def test_list_tasks_filter_status(self, scheduler):
        t1 = scheduler.create_task("a", "interval", "60")
        scheduler.create_task("b", "interval", "120")
        scheduler.pause_task(t1.id)
        active = scheduler.list_tasks(status="active")
        paused = scheduler.list_tasks(status="paused")
        assert len(active) == 1
        assert len(paused) == 1

    def test_update_task_schedule_in_place(self, scheduler):
        task = scheduler.create_task("sleep", "cron", "0 14 * * *")

        updated = scheduler.update_task_schedule(task.id, "cron", "0 15 * * *")

        assert updated.id == task.id
        assert updated.schedule_value == "0 15 * * *"
        assert updated.next_run is not None
        stored = scheduler.list_tasks()[0]
        assert stored.schedule_value == "0 15 * * *"

    def test_update_refuses_cancelled_task(self, scheduler):
        task = scheduler.create_task("sleep", "cron", "0 14 * * *")
        scheduler.cancel_task(task.id)

        with pytest.raises(ValueError, match="cancelled"):
            scheduler.update_task_schedule(task.id, "cron", "0 15 * * *")


# -- Pause / resume / cancel -------------------------------------------------


class TestPauseResumeCancel:
    def test_pause_task(self, scheduler):
        task = scheduler.create_task("test", "interval", "60")
        scheduler.pause_task(task.id)
        tasks = scheduler.list_tasks(status="paused")
        assert len(tasks) == 1
        assert tasks[0].status == "paused"

    def test_resume_task(self, scheduler):
        task = scheduler.create_task("test", "interval", "60")
        scheduler.pause_task(task.id)
        scheduler.resume_task(task.id)
        tasks = scheduler.list_tasks(status="active")
        assert len(tasks) == 1

    def test_cancel_task(self, scheduler):
        task = scheduler.create_task("test", "interval", "60")
        scheduler.cancel_task(task.id)
        tasks = scheduler.list_tasks(status="cancelled")
        assert len(tasks) == 1
        assert tasks[0].next_run is None

    def test_pause_nonexistent(self, scheduler):
        with pytest.raises(KeyError):
            scheduler.pause_task("nonexistent")

    def test_resume_nonexistent(self, scheduler):
        with pytest.raises(KeyError):
            scheduler.resume_task("nonexistent")

    def test_cancel_nonexistent(self, scheduler):
        with pytest.raises(KeyError):
            scheduler.cancel_task("nonexistent")


# -- _compute_next_run -------------------------------------------------------


class TestComputeNextRun:
    def test_interval(self, scheduler):
        task = ScheduledTask(
            id="t", prompt="p", schedule_type="interval", schedule_value="300"
        )
        next_run = scheduler._compute_next_run(task)
        assert next_run is not None
        # Should be roughly 300 seconds from now
        parsed = datetime.fromisoformat(next_run)
        diff = (parsed - datetime.now(timezone.utc)).total_seconds()
        assert 295 <= diff <= 310

    def test_once_not_yet_run(self, scheduler):
        target = "2099-06-15T12:00:00+00:00"
        task = ScheduledTask(
            id="t",
            prompt="p",
            schedule_type="once",
            schedule_value=target,
            last_run=None,
        )
        next_run = scheduler._compute_next_run(task)
        assert next_run == target

    def test_once_already_run(self, scheduler):
        task = ScheduledTask(
            id="t",
            prompt="p",
            schedule_type="once",
            schedule_value="2099-06-15T12:00:00+00:00",
            last_run="2099-06-15T12:01:00+00:00",
        )
        next_run = scheduler._compute_next_run(task)
        assert next_run is None

    def test_cron_fallback(self, scheduler):
        task = ScheduledTask(
            id="t",
            prompt="p",
            schedule_type="cron",
            schedule_value="30 2 * * *",
        )
        next_run = scheduler._compute_next_run(task)
        assert next_run is not None

    def test_unknown_type(self, scheduler):
        task = ScheduledTask(
            id="t", prompt="p", schedule_type="unknown", schedule_value="x"
        )
        assert scheduler._compute_next_run(task) is None


# -- _execute_task -----------------------------------------------------------


class TestExecuteTask:
    def test_execute_with_system(self, store):
        mock_system = MagicMock()
        mock_system.ask.return_value = "result text"
        sched = TaskScheduler(store, system=mock_system, poll_interval=1)

        task = sched.create_task("what is 2+2?", "once", "2026-01-01T00:00:00+00:00")
        sched._execute_task(task)

        mock_system.ask.assert_called_once()
        call_args = mock_system.ask.call_args
        assert call_args[0][0] == "what is 2+2?"

        # Check that a run log was recorded
        logs = store.get_run_logs(task.id)
        assert len(logs) == 1
        assert logs[0]["success"] == 1
        assert logs[0]["result"] == "result text"

    def test_execute_without_system(self, store):
        sched = TaskScheduler(store, poll_interval=1)
        task = sched.create_task("dry run", "once", "2026-01-01T00:00:00+00:00")
        sched._execute_task(task)

        logs = store.get_run_logs(task.id)
        assert len(logs) == 1
        assert logs[0]["success"] == 1
        assert "dry-run" in logs[0]["result"]

    def test_set_system_promotes_dry_run_to_real_execution(self, store):
        """A scheduler built without a system must run for real once injected.

        The scheduler is constructed before JarvisSystem exists, so without
        deferred injection every due task silently logged a dry-run instead
        of executing.
        """
        sched = TaskScheduler(store, poll_interval=1)
        task = sched.create_task("first", "once", "2026-01-01T00:00:00+00:00")
        sched._execute_task(task)
        assert "dry-run" in store.get_run_logs(task.id)[0]["result"]

        mock_system = MagicMock()
        mock_system.ask.return_value = "real result"
        sched.set_system(mock_system)

        task2 = sched.create_task("second", "once", "2026-01-01T00:00:00+00:00")
        sched._execute_task(task2)

        mock_system.ask.assert_called_once()
        assert store.get_run_logs(task2.id)[0]["result"] == "real result"

    def test_execute_with_error(self, store):
        mock_system = MagicMock()
        mock_system.ask.side_effect = RuntimeError("engine down")
        sched = TaskScheduler(store, system=mock_system, poll_interval=1)

        task = sched.create_task("fail", "once", "2026-01-01T00:00:00+00:00")
        sched._execute_task(task)

        logs = store.get_run_logs(task.id)
        assert len(logs) == 1
        assert logs[0]["success"] == 0
        assert "engine down" in logs[0]["error"]

    def test_execute_publishes_events(self, store):
        mock_bus = MagicMock()
        sched = TaskScheduler(store, bus=mock_bus, poll_interval=1)
        task = sched.create_task("test", "once", "2026-01-01T00:00:00+00:00")
        sched._execute_task(task)

        assert mock_bus.publish.call_count == 2
        start_call = mock_bus.publish.call_args_list[0]
        end_call = mock_bus.publish.call_args_list[1]
        assert start_call[0][0] == "scheduler_task_start"
        assert end_call[0][0] == "scheduler_task_end"

    def test_execute_once_task_completed_after_run(self, store):
        sched = TaskScheduler(store, poll_interval=1)
        task = sched.create_task("one-shot", "once", "2026-01-01T00:00:00+00:00")
        sched._execute_task(task)

        updated = store.get_task(task.id)
        assert updated["status"] == "completed"
        assert updated["next_run"] is None

    def test_execute_with_tools(self, store):
        mock_system = MagicMock()
        mock_system.ask.return_value = "4"
        sched = TaskScheduler(store, system=mock_system, poll_interval=1)

        task = sched.create_task(
            "what is 2+2?",
            "once",
            "2026-01-01T00:00:00+00:00",
            tools="calculator,think",
        )
        sched._execute_task(task)

        call_kwargs = mock_system.ask.call_args[1]
        assert call_kwargs["tools"] == ["calculator", "think"]


# -- Start / stop lifecycle ---------------------------------------------------


class TestLifecycle:
    def test_start_stop(self, scheduler):
        scheduler.start()
        assert scheduler._thread is not None
        assert scheduler._thread.is_alive()
        scheduler.stop()
        assert not scheduler._thread

    def test_double_start(self, scheduler):
        scheduler.start()
        t1 = scheduler._thread
        scheduler.start()  # Should not create a second thread
        assert scheduler._thread is t1
        scheduler.stop()

    def test_poll_loop_finds_due_tasks(self, store):
        sched = TaskScheduler(store, poll_interval=1)
        # Create a task that is already due
        task = sched.create_task("immediate", "once", "2020-01-01T00:00:00+00:00")
        # Manually set next_run to the past
        d = store.get_task(task.id)
        d["next_run"] = "2020-01-01T00:00:00+00:00"
        store.update_task(d)

        sched.start()
        # Give the poll loop time to execute
        time.sleep(2.5)
        sched.stop()

        logs = store.get_run_logs(task.id)
        assert len(logs) >= 1


# -- SystemBuilder wiring ----------------------------------------------------


class TestSystemBuilderWiring:
    """``_setup_scheduler`` is the only place the in-process scheduler is built."""

    def test_returns_nothing_when_disabled(self):
        from openjarvis.core.config import JarvisConfig
        from openjarvis.system import SystemBuilder

        config = JarvisConfig()
        assert config.scheduler.enabled is False
        store, sched = SystemBuilder(config)._setup_scheduler(config, None)
        assert store is None
        assert sched is None

    def test_builds_scheduler_when_enabled(self, tmp_path):
        from openjarvis.core.config import JarvisConfig
        from openjarvis.system import SystemBuilder

        config = JarvisConfig()
        config.scheduler.enabled = True
        config.scheduler.db_path = str(tmp_path / "sched.db")

        store, sched = SystemBuilder(config)._setup_scheduler(config, None)
        assert store is not None
        assert isinstance(sched, TaskScheduler)
        try:
            # Built before JarvisSystem exists, so it starts systemless and
            # only becomes able to execute once build() injects the system.
            assert sched._system is None
            assert sched._thread is None
        finally:
            sched.stop()
            store.close()


class TestResultCoercion:
    """``system.ask`` returns a dict for tool-calling agents; SQLite cannot bind one."""

    def test_dict_result_is_serialised_not_bound_raw(self, store):
        mock_system = MagicMock()
        mock_system.ask.return_value = {"content": "done", "usage": {}}
        sched = TaskScheduler(store, system=mock_system, poll_interval=1)

        task = sched.create_task("go", "once", "2026-01-01T00:00:00+00:00")
        sched._execute_task(task)

        logs = store.get_run_logs(task.id)
        assert logs[0]["success"] == 1
        assert json.loads(logs[0]["result"])["content"] == "done"

    def test_plain_string_result_is_unchanged(self, store):
        mock_system = MagicMock()
        mock_system.ask.return_value = "plain text"
        sched = TaskScheduler(store, system=mock_system, poll_interval=1)

        task = sched.create_task("go", "once", "2026-01-01T00:00:00+00:00")
        sched._execute_task(task)

        assert store.get_run_logs(task.id)[0]["result"] == "plain text"


# -- Local-offset "once" tasks ------------------------------------------------


class TestOnceTasksWithLocalOffsets:
    """A 10 PM reminder created in local time must be due at 10 PM local.

    Due tasks are selected with ``next_run <= now`` as text, and "now" is
    UTC. A task stored as "...T22:00:00+08:00" sorts after a UTC now of
    "...T16:07:00+00:00" because "22" > "16", so the user's reminder to
    watch a film at 10 PM had no run log at midnight and would have fired at
    six the next morning. Cron tasks never hit this because their next_run
    is computed in UTC.
    """

    def test_a_local_offset_once_task_is_due_when_its_utc_moment_passes(
        self, scheduler, store
    ):
        # 22:00 at +08:00 is 14:00 UTC. Pretend it is 16:07 UTC, two hours
        # later: the reminder is overdue and must be selected.
        task = scheduler.create_task(
            prompt="Remind Sir to watch the film.",
            schedule_type="once",
            schedule_value="2026-09-13T22:00:00+08:00",
        )
        due = store.get_due_tasks("2026-09-13T16:07:00+00:00")
        assert [t["id"] for t in due] == [task.id]

    def test_next_run_is_stored_in_utc(self, scheduler):
        task = scheduler.create_task(
            prompt="x", schedule_type="once", schedule_value="2026-09-13T22:00:00+08:00"
        )
        assert task.next_run == "2026-09-13T14:00:00+00:00"

    def test_a_naive_value_keeps_meaning_utc(self, scheduler):
        task = scheduler.create_task(
            prompt="x", schedule_type="once", schedule_value="2026-09-13T22:00:00"
        )
        assert task.next_run == "2026-09-13T22:00:00+00:00"

    def test_an_unparseable_value_is_left_alone(self, scheduler):
        task = scheduler.create_task(
            prompt="x", schedule_type="once", schedule_value="tomorrow-ish"
        )
        assert task.next_run == "tomorrow-ish"

    def test_rows_stored_before_the_fix_are_healed_on_start(self, store):
        # The user's row shape, saved with the local offset and never run.
        # Dated in the future so the poll loop does not run it the moment
        # the scheduler starts, which is what it correctly does to an
        # overdue task.
        store.save_task(
            {
                "id": "stale1",
                "prompt": "Remind Sir to watch the film.",
                "schedule_type": "once",
                "schedule_value": "2099-09-13T22:00:00+08:00",
                "context_mode": "isolated",
                "status": "active",
                "next_run": "2099-09-13T22:00:00+08:00",
                "last_run": None,
                "agent": "orchestrator",
                "tools": "",
                "metadata": {},
            }
        )
        sched = TaskScheduler(store, poll_interval=60)
        try:
            sched.start()
        finally:
            sched.stop()
        assert store.get_task("stale1")["next_run"] == "2099-09-13T14:00:00+00:00"
        assert store.get_due_tasks("2099-09-13T16:07:00+00:00")
        assert not store.get_due_tasks("2099-09-13T13:00:00+00:00")


class TestATaskFromChatReportsBack:
    """23 September: "narration test at 3:22 PM" ran at 3:22:16 and its
    answer went only into the run log. The user saw nothing and took the
    scheduler for broken."""

    def _run(self, store, monkeypatch, answer, metadata):
        import openjarvis.tools.notify_windows as notify

        sent = []
        monkeypatch.setattr(
            notify, "deliver", lambda title, text, **kw: sent.append((title, text))
        )
        system = MagicMock()
        system.ask.return_value = answer
        sched = TaskScheduler(store, system=system, poll_interval=1)
        task = sched.create_task(
            "x", "once", "2026-01-01T00:00:00+00:00", metadata=metadata
        )
        sched._execute_task(task)
        return sent

    def test_its_answer_is_delivered(self, store, monkeypatch):
        answer = {"content": "The test passed.", "tool_results": []}
        sent = self._run(store, monkeypatch, answer, {"deliver": True})
        assert sent == [("Scheduled task", "The test passed.")]

    def test_not_twice_when_it_notified_itself(self, store, monkeypatch):
        answer = {"content": "Sent.", "tool_results": [{"tool_name": "notify_windows"}]}
        assert self._run(store, monkeypatch, answer, {"deliver": True}) == []

    def test_system_tasks_report_their_own_way(self, store, monkeypatch):
        assert self._run(store, monkeypatch, "Nothing upcoming.", {}) == []

    def test_a_long_answer_is_cut_at_a_sentence(self, store, monkeypatch):
        long = "First sentence here. " * 40
        [(_, message)] = self._run(store, monkeypatch, long, {"deliver": True})
        assert len(message) < 340
        assert message.endswith(". ...")


def test_a_task_from_chat_is_marked_to_deliver(store):
    from openjarvis.scheduler.tools import ScheduleTaskTool

    sched = TaskScheduler(store, poll_interval=1)
    tool = ScheduleTaskTool()
    tool._scheduler = sched
    result = tool.execute(
        prompt="Check the weather in Calamba and summarise it.",
        schedule_type="once",
        schedule_value="2030-01-01T09:00:00",
    )
    assert result.success, result.content
    [task] = sched.list_tasks()
    assert task.metadata.get("deliver") is True


class TestTheModelABackgroundTaskRunsOn:
    """24 September: a reminder with no model of its own ran on the local
    qwen3.5:4b default and put 3.6 GB on an 8 GB card already holding the
    voice engine; the laptop lagged."""

    def test_the_preferred_cloud_model_is_used(self, store, monkeypatch):
        from openjarvis.core import model_preference

        monkeypatch.setattr(
            model_preference, "background_model", lambda: "gpt-5.6-luna"
        )
        system = MagicMock()
        system.ask.return_value = "ok"
        sched = TaskScheduler(store, system=system, poll_interval=1)
        task = sched.create_task(
            "check my calendar", "once", "2026-01-01T00:00:00+00:00"
        )
        sched._execute_task(task)
        assert system.ask.call_args.kwargs["model"] == "gpt-5.6-luna"

    def test_a_local_run_frees_the_gpu_at_once(self, store, _no_model_preference):
        system = MagicMock()
        system.model = "qwen3.5:4b"
        system.ask.return_value = "ok"
        sched = TaskScheduler(store, system=system, poll_interval=1)
        task = sched.create_task(
            "check my calendar", "once", "2026-01-01T00:00:00+00:00"
        )
        sched._execute_task(task)
        assert "model" not in system.ask.call_args.kwargs
        assert _no_model_preference == ["qwen3.5:4b"]

    def test_a_task_that_names_its_model_keeps_it(self, store, _no_model_preference):
        system = MagicMock()
        system.ask.return_value = "ok"
        sched = TaskScheduler(store, system=system, poll_interval=1)
        task = sched.create_task(
            "x", "once", "2026-01-01T00:00:00+00:00", metadata={"model": "gpt-5.6-sol"}
        )
        sched._execute_task(task)
        assert system.ask.call_args.kwargs["model"] == "gpt-5.6-sol"
        assert _no_model_preference == []


class TestThePreferenceIsStored:
    def test_it_round_trips_and_defaults_to_cloud(self, tmp_path):
        from openjarvis.core.model_preference import (
            ModelPreference,
            load_preference,
            save_preference,
        )

        assert load_preference(tmp_path).prefer_cloud is True
        save_preference(ModelPreference(prefer_cloud=False, cloud_model="x"), tmp_path)
        assert load_preference(tmp_path) == ModelPreference(False, "x")
