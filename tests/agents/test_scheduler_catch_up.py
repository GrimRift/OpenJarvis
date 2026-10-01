"""A scheduled Operator run missed while the PC slept runs once, late (1 Oct).

The "Morning brief" (cron 06:30) only ran if Sage was up at 06:30: next_fire
lives in memory and was computed from *now* at start-up, so a slot that passed
while Sage was off was dropped. On Thu 1 Oct the laptop slept 23:34 -> 14:17
and the brief never came. Now a missed slot runs once when Sage is back, the
same day and under 10 h late; older misses are skipped, never stacked.
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

CRON = "30 6 * * *"


def _at(day: int, hour: int, minute: int = 0) -> float:
    return datetime.datetime(2026, 10, day, hour, minute).timestamp()


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def manager(tmp_path):
    from openjarvis.agents.manager import AgentManager

    mgr = AgentManager(db_path=str(tmp_path / "agents.db"))
    yield mgr
    mgr.close()


def _agent(manager, *, created_at: float, last_run_at: float | None = None) -> str:
    agent = manager.create_agent(
        name="Morning brief",
        agent_type="orchestrator",
        config={"schedule_type": "cron", "schedule_value": CRON},
    )
    manager._conn.execute(
        "UPDATE managed_agents SET created_at = ?, last_run_at = ? WHERE id = ?",
        (created_at, last_run_at, agent["id"]),
    )
    manager._conn.commit()
    return agent["id"]


def _scheduler(manager, clock):
    from openjarvis.agents.scheduler import AgentScheduler

    executor = MagicMock()
    return AgentScheduler(manager=manager, executor=executor, clock=clock), executor


def test_a_slot_missed_while_sage_was_off_runs_once_when_it_starts(manager):
    aid = _agent(manager, created_at=_at(1, 6), last_run_at=_at(1, 6, 31))
    clock = Clock(_at(2, 14, 17))  # slept 23:34 -> 14:17, Fri's 06:30 missed
    scheduler, executor = _scheduler(manager, clock)
    scheduler.register_agent(aid)

    scheduler._check_due_agents()
    executor.execute_tick.assert_called_once_with(aid, late_for=_at(2, 6, 30))

    clock.now += 5
    scheduler._check_due_agents()
    assert executor.execute_tick.call_count == 1
    assert scheduler._agents[aid]["next_fire"] == _at(3, 6, 30)


def test_no_catch_up_when_it_already_ran_after_the_slot(manager):
    # e.g. the user pressed Run now at 08:00
    aid = _agent(manager, created_at=_at(1, 6), last_run_at=_at(2, 8))
    scheduler, executor = _scheduler(manager, Clock(_at(2, 14, 17)))
    scheduler.register_agent(aid)
    scheduler._check_due_agents()
    executor.execute_tick.assert_not_called()


def test_no_catch_up_ten_hours_or_more_late(manager):
    aid = _agent(manager, created_at=_at(1, 6), last_run_at=_at(1, 6, 31))
    scheduler, executor = _scheduler(manager, Clock(_at(2, 16, 30)))
    scheduler.register_agent(aid)
    scheduler._check_due_agents()
    executor.execute_tick.assert_not_called()


def test_no_catch_up_of_yesterdays_slot(manager):
    aid = _agent(manager, created_at=_at(1, 6), last_run_at=_at(1, 6, 31))
    # 01:00 on the 3rd: the last slot (2nd, 06:30) is from another day
    scheduler, executor = _scheduler(manager, Clock(_at(3, 1)))
    scheduler.register_agent(aid)
    scheduler._check_due_agents()
    executor.execute_tick.assert_not_called()


def test_no_catch_up_for_an_agent_created_after_the_slot(manager):
    aid = _agent(manager, created_at=_at(2, 9))
    scheduler, executor = _scheduler(manager, Clock(_at(2, 14)))
    scheduler.register_agent(aid)
    scheduler._check_due_agents()
    executor.execute_tick.assert_not_called()


def test_an_on_time_fire_is_not_marked_late(manager):
    aid = _agent(manager, created_at=_at(1, 6), last_run_at=_at(1, 6, 31))
    clock = Clock(_at(2, 6))
    scheduler, executor = _scheduler(manager, clock)
    scheduler.register_agent(aid)
    clock.now = _at(2, 6, 30) + 1
    scheduler._check_due_agents()
    executor.execute_tick.assert_called_once_with(aid)


def test_server_up_through_sleep_fires_the_slot_late(manager):
    aid = _agent(manager, created_at=_at(1, 6), last_run_at=_at(1, 6, 31))
    clock = Clock(_at(1, 23, 34))
    scheduler, executor = _scheduler(manager, clock)
    scheduler.register_agent(aid)
    clock.now = _at(2, 14, 17)  # the laptop wakes
    scheduler._check_due_agents()
    executor.execute_tick.assert_called_once_with(aid, late_for=_at(2, 6, 30))


def test_server_up_through_a_long_sleep_skips_the_stale_slot(manager):
    aid = _agent(manager, created_at=_at(1, 6), last_run_at=_at(1, 6, 31))
    clock = Clock(_at(1, 23, 34))
    scheduler, executor = _scheduler(manager, clock)
    scheduler.register_agent(aid)
    clock.now = _at(2, 17)  # 10.5 h after the slot
    scheduler._check_due_agents()
    executor.execute_tick.assert_not_called()
    assert scheduler._agents[aid]["next_fire"] == _at(3, 6, 30)


def test_a_late_run_tells_the_model_and_counts(scenario_harness, monkeypatch):
    from openjarvis.agents import executor as executor_module

    sent = []
    monkeypatch.setattr(
        executor_module,
        "_send_to_phone",
        lambda title, text: sent.append(text) or True,
    )
    h = scenario_harness
    h.engine._responses = [{"content": "Inbox: 2 need replies."}]
    agent = h.manager.create_agent(
        name="Morning brief",
        config={
            "schedule_type": "cron",
            "schedule_value": CRON,
            "instruction": "Brief me.",
            "deliver_to": "telegram",
        },
    )
    slot = datetime.datetime.now().replace(hour=6, minute=30).timestamp()
    h.executor.execute_tick(agent["id"], late_for=slot)

    prompt = str(h.engine.last_messages)
    assert "This run is late: it was due at 06:30" in prompt
    assert sent == [
        "Late brief: missed 06:30, the PC was asleep.\n\nInbox: 2 need replies."
    ]
    assert h.manager.get_agent(agent["id"])["total_runs"] == 1
