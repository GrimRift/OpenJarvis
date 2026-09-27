"""Runs missed while Sage was off (27 September: a start at 19:57 ran the
23:10 sleep reminder, the 05:00 morning briefing, and the diary for the
wrong day). Each task's late run follows the user's choice for it."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from openjarvis.scheduler.scheduler import ScheduledTask, TaskScheduler, plan_run
from openjarvis.scheduler.store import SchedulerStore

SGT = {"timezone": "Asia/Singapore"}
# 2026-09-27 19:57 in Singapore.
NOW = datetime(2026, 9, 27, 11, 57, tzinfo=timezone.utc)


def _task(kind="cron", due="2026-09-26T15:10:00+00:00", meta=None, prompt="p"):
    return ScheduledTask(
        id="t",
        prompt=prompt,
        schedule_type=kind,
        schedule_value="10 15 * * *" if kind == "cron" else "x",
        next_run=due,
        metadata={**SGT, **(meta or {})},
    )


def test_on_time_runs_as_it_is():
    run, prompt, _ = plan_run(_task(due="2026-09-27T11:55:00+00:00"), NOW)
    assert run and prompt == "p"


def test_a_reminder_hours_late_waits_for_its_next_time():
    # The 23:10 sleep reminder, found at 19:57 the next day.
    run, _, reason = plan_run(_task(), NOW)
    assert not run and "late" in reason


def test_a_reminder_within_the_hour_still_runs():
    run, _, _ = plan_run(_task(due="2026-09-27T11:10:00+00:00"), NOW)
    assert run


@pytest.mark.parametrize("key", ["digest-daily", "proactive-daily"])
def test_the_morning_jobs_catch_up_the_same_day_only(key):
    meta = {"openjarvis_task_key": key}
    # Due 05:00 today (21:00 UTC yesterday): still today at 19:57.
    today = _task(due="2026-09-26T21:00:00+00:00", meta=meta)
    assert plan_run(today, NOW)[0]
    # Due 05:00 yesterday: another day.
    yesterday = _task(due="2026-09-25T21:00:00+00:00", meta=meta)
    run, _, reason = plan_run(yesterday, NOW)
    assert not run and "same day" in reason


def test_the_diary_catches_up_for_the_day_it_missed():
    task = _task(
        due="2026-09-26T15:00:00+00:00",
        meta={"managed_by": "m36-episodes"},
        prompt="Write Sage's diary entry for today.",
    )
    run, prompt, _ = plan_run(task, NOW)
    assert run and prompt == "Write Sage's diary entry for 2026-09-26"


def test_memory_clean_up_catches_up():
    meta = {"managed_by": "m38-memory-hygiene"}
    task = _task(due="2026-09-26T15:30:00+00:00", meta=meta)
    assert plan_run(task, NOW)[0]


def test_a_missed_one_off_is_delivered_marked_missed():
    due = "2026-09-27T07:00:00+00:00"
    task = _task(kind="once", due=due, prompt="Remind Sir to call.")
    run, prompt, _ = plan_run(task, NOW)
    assert run
    assert prompt.startswith("(Missed while Sage was off: this was due at 3:00 PM")
    assert prompt.endswith("Remind Sir to call.")


def test_a_task_can_set_its_own_policy():
    task = _task(meta={"late_policy": "catch_up"})
    assert plan_run(task, NOW)[0]


def test_a_skipped_run_moves_to_the_next_time_and_is_logged(tmp_path):
    store = SchedulerStore(tmp_path / "s.db")
    scheduler = TaskScheduler(store, poll_interval=1)
    try:
        created = scheduler.create_task("Sleep early", "cron", "10 15 * * *")
        d = store.get_task(created.id)
        d["next_run"] = "2026-09-26T15:10:00+00:00"
        store.update_task(d)
        scheduler._skip_task(ScheduledTask.from_dict(d), "due 11:10 PM, 1247 min late")
        after = store.get_task(created.id)
        assert after["next_run"] > datetime.now(timezone.utc).isoformat()
        assert after["last_run"] is None
        logs = store.get_run_logs(created.id)
        assert logs and logs[0]["result"].startswith("Skipped:")
    finally:
        scheduler.stop()
        store.close()
