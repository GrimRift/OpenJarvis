"""Episode writer -- Sage's diary entry for the day (M36 phase 2).

Runs once a night on a schedule and writes one episode: a few sentences on
what the user and Sage did that day, drawn from the trace store. The prompt
it is given is ignored; the day is the input.

A registered agent rather than a bare function so it rides the existing
scheduler exactly as the proactive run and the digest do, and pins its model
the same way: a scheduled run has no other way to pick one, and the local
default has invented actions it never took.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import Any, Optional

from openjarvis.agents._model_override import apply_configured_model
from openjarvis.agents._stubs import AgentContext, AgentResult, BaseAgent
from openjarvis.core.presence import load_settings
from openjarvis.core.registry import AgentRegistry
from openjarvis.core.types import Message, Role
from openjarvis.memory.episodes import (
    SUMMARY_SYSTEM_PROMPT,
    Episode,
    collect_turns,
    save_episode,
    transcript_for_prompt,
)

logger = logging.getLogger(__name__)

EPISODE_CRON_PROMPT = "Write Sage's diary entry for today."

# A prompt may name a day, so past days can be filled in through the same
# agent rather than a second writer: "Write Sage's diary entry for 2026-09-08".
_DAY_IN_PROMPT = re.compile(r"(\d{4}-\d{2}-\d{2})")


def day_from_prompt(prompt: str) -> date:
    match = _DAY_IN_PROMPT.search(prompt or "")
    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            pass
    return date.today()


@AgentRegistry.register("episode_writer")
class EpisodeWriterAgent(BaseAgent):
    """Summarise today's conversations into one stored episode."""

    agent_id = "episode_writer"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        settings = load_settings()
        args, kwargs = apply_configured_model(
            args,
            kwargs,
            settings.episodes_model,
            settings.episodes_engine,
            label="Episodes",
        )
        super().__init__(*args, **kwargs)
        self._configured_model = settings.episodes_model

    def run(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        settings = load_settings()
        if not settings.enabled or not settings.episodes_enabled:
            # Nothing is called and nothing is written: with the master
            # switch off, Sage behaves exactly as it did before M36.
            return AgentResult(content="Episodes are switched off.", turns=0)

        day = day_from_prompt(input)
        turns = collect_turns(day)
        if not turns:
            # A quiet day is not an episode. Storing "no conversations" would
            # put that line in every prompt for three days.
            return AgentResult(content=f"No conversations on {day}.", turns=0)

        self._emit_turn_start(input)
        messages = [
            Message(role=Role.SYSTEM, content=SUMMARY_SYSTEM_PROMPT),
            Message(
                role=Role.USER,
                content=(
                    f"Date: {day.strftime('%A %d %B %Y')}\n"
                    f"Turns: {len(turns)}\n\n{transcript_for_prompt(turns)}"
                ),
            ),
        ]
        try:
            result = self._generate(messages)
        except Exception as exc:
            logger.warning("Episode summary failed: %s", exc)
            self._emit_turn_end(content_length=0)
            return AgentResult(content=f"Episode not written: {exc}", turns=1)

        summary = str(result.get("content") or "").strip()
        self._emit_turn_end(content_length=len(summary))
        if not summary:
            return AgentResult(content="Episode not written: empty summary.", turns=1)

        save_episode(
            Episode(
                day=day.isoformat(),
                summary=summary,
                turns=len(turns),
                written_at=time.time(),
                model=self._configured_model,
            )
        )
        return AgentResult(content=summary, turns=1)




# -- Scheduling ----------------------------------------------------------------

_EPISODE_TASK_KEY_FIELD = "managed_by"
_EPISODE_TASK_KEY = "m36-episodes"


def _utc_hour_for_local(hour_local: int, timezone_name: str) -> int:
    """The UTC hour at which *hour_local* falls today in *timezone_name*.

    The scheduler evaluates cron in UTC while this machine is UTC+8, so
    "23:00" written naively would run at 07:00 the next morning. That trap is
    documented; this is the conversion.
    """
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(timezone_name)
        local = datetime.now(tz).replace(hour=hour_local, minute=0, second=0)
        return local.astimezone(ZoneInfo("UTC")).hour
    except Exception:
        return hour_local


def register_episode_cron(scheduler: Any) -> Optional[Any]:
    """Self-register the nightly episode task, replacing a stale one.

    Idempotent, like the proactive agent's registration: an existing task with
    the same schedule is kept, a paused one is respected, and one whose
    schedule no longer matches the settings is replaced.
    """
    settings = load_settings()
    # [scheduler] has no timezone field -- the key in config.toml is not read
    # by anything -- so the proactive section's timezone is the one that is
    # actually maintained. Reading scheduler.timezone raised, fell back to
    # UTC, and put the first registration at 07:00 local.
    timezone_name = "UTC"
    try:
        from openjarvis.core.config import load_config

        cfg = load_config()
        timezone_name = (
            getattr(cfg.proactive, "timezone", "")
            or getattr(cfg.digest, "timezone", "")
            or "UTC"
        )
    except Exception:
        pass
    utc_hour = _utc_hour_for_local(settings.episodes_hour_local, timezone_name)
    cron_expr = f"0 {utc_hour} * * *"
    metadata = {
        _EPISODE_TASK_KEY_FIELD: _EPISODE_TASK_KEY,
        "hour_local": settings.episodes_hour_local,
        "timezone": timezone_name,
    }

    existing = [
        task
        for task in scheduler.list_tasks()
        if task.status in {"active", "paused"}
        and task.agent == "episode_writer"
        and task.metadata.get(_EPISODE_TASK_KEY_FIELD) == _EPISODE_TASK_KEY
    ]
    paused = [task for task in existing if task.status == "paused"]
    if paused:
        keep = min(paused, key=lambda task: task.id)
        _cancel_others(scheduler, existing, keep)
        return keep
    matching = [
        task
        for task in existing
        if task.schedule_type == "cron" and task.schedule_value == cron_expr
    ]
    if matching:
        keep = min(matching, key=lambda task: task.id)
        _cancel_others(scheduler, existing, keep)
        return keep
    _cancel_others(scheduler, existing, None)
    return scheduler.create_task(
        prompt=EPISODE_CRON_PROMPT,
        schedule_type="cron",
        schedule_value=cron_expr,
        agent="episode_writer",
        context_mode="isolated",
        metadata=metadata,
    )


def _cancel_others(scheduler: Any, tasks: Any, keep: Optional[Any]) -> None:
    for task in tasks:
        if keep is not None and task.id == keep.id:
            continue
        try:
            scheduler.cancel_task(task.id)
        except Exception as exc:
            logger.debug("Could not cancel stale episode task %s: %s", task.id, exc)


__all__ = [
    "EPISODE_CRON_PROMPT",
    "EpisodeWriterAgent",
    "day_from_prompt",
    "register_episode_cron",
]
