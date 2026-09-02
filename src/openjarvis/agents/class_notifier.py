"""Run the class-schedule check on a timer, without asking a model to.

The scheduled task used ``agent="orchestrator"`` with the prompt "Call
notify_class_schedule with lookahead_minutes=15." Every ten minutes for
months the model answered *about* the tool instead of calling it::

    "I will call `notify_class_schedule` with a 15-minute lookahead..."
    "I have initiated the `notify_class_schedule` tool..."
    "I cannot execute tool calls directly as my current interface does not..."

Each run was recorded as a success. Nothing was ever checked, and no reminder
ever fired.

``notify_class_schedule`` already removed the *decision* from the model, for
the same class of reason. This removes the *invocation* too: the scheduler can
only run an agent, so the agent is a two-line one that calls the tool. There is
no judgement left in the loop to get wrong.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from openjarvis.agents._stubs import AgentContext, AgentResult, BaseAgent
from openjarvis.core.registry import AgentRegistry

logger = logging.getLogger(__name__)

_TASK_PROMPT = "Check the class schedule and notify about anything imminent."
_TASK_KEY = "class-schedule-notify"
_TASK_KEY_FIELD = "openjarvis_task_key"

#: At most the smallest reminder stage, or the 5-minute alert falls between
#: two checks entirely. The old task polled every 10 minutes, which could not
#: have delivered a 5-minute reminder even had it worked. At exactly the stage
#: length the alert still lands, but late by up to one interval, which is why
#: the spoken reminder states the real remaining time rather than the stage.
POLL_SECONDS = 300


@AgentRegistry.register("class_notifier")
class ClassNotifierAgent(BaseAgent):
    """Call ``notify_class_schedule`` and report what it did."""

    agent_id = "class_notifier"

    def run(
        self,
        input: str = "",
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        from openjarvis.tools.notify_class_schedule import NotifyClassScheduleTool

        try:
            result = NotifyClassScheduleTool().execute()
        except Exception as error:
            logger.warning("Class schedule check failed", exc_info=True)
            return AgentResult(
                content=f"Class schedule check failed: {error}",
                turns=1,
                metadata={"error": True},
            )
        return AgentResult(
            content=result.content,
            tool_results=[result],
            turns=1,
            metadata=result.metadata or {},
        )


def register_class_notifier_cron(scheduler: Any) -> Any:
    """Register the class-schedule poll, replacing the model-driven task.

    Idempotent like the proactive and digest crons, and it also retires the
    old ``orchestrator`` task that only ever narrated the tool call — leaving
    that one active would mean two checks, one of which never works.
    """
    metadata = {_TASK_KEY_FIELD: _TASK_KEY}

    stale = [
        task
        for task in scheduler.list_tasks()
        if task.status in {"active", "paused"}
        and "notify_class_schedule" in (task.prompt or "")
        and task.agent != "class_notifier"
    ]
    for task in stale:
        try:
            scheduler.cancel_task(task.id)
            logger.info("Retired the model-driven class schedule task %s", task.id)
        except Exception:
            logger.warning("Could not retire task %s", task.id, exc_info=True)

    existing = [
        task
        for task in scheduler.list_tasks()
        if task.status in {"active", "paused"}
        and task.agent == "class_notifier"
        and task.metadata.get(_TASK_KEY_FIELD) == _TASK_KEY
    ]

    paused = [task for task in existing if task.status == "paused"]
    if paused:
        keep = min(paused, key=lambda task: task.id)
        _cancel_others(scheduler, existing, keep=keep)
        return keep

    matching = [
        task
        for task in existing
        if task.schedule_type == "interval"
        and str(task.schedule_value) == str(POLL_SECONDS)
    ]
    if matching:
        keep = min(matching, key=lambda task: task.id)
        _cancel_others(scheduler, existing, keep=keep)
        return keep

    _cancel_others(scheduler, existing)
    return scheduler.create_task(
        prompt=_TASK_PROMPT,
        schedule_type="interval",
        schedule_value=str(POLL_SECONDS),
        agent="class_notifier",
        context_mode="isolated",
        metadata=metadata,
    )


def _cancel_others(scheduler: Any, tasks: List[Any], *, keep: Any = None) -> None:
    for task in tasks:
        if keep is not None and task.id == keep.id:
            continue
        try:
            scheduler.cancel_task(task.id)
        except Exception:
            logger.warning("Could not cancel task %s", task.id, exc_info=True)


__all__ = ["ClassNotifierAgent", "POLL_SECONDS", "register_class_notifier_cron"]
