"""Memory hygiene agent -- the nightly clean-up, on the scheduler (M38).

Same shape as the episode writer: a registered agent so it rides the
existing scheduler and pins its model, a self-registering cron at a local
hour, and a boot-time catch-up because Sage is rarely on at 23:00.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from openjarvis.agents._model_override import apply_configured_model
from openjarvis.agents._stubs import AgentContext, AgentResult, BaseAgent
from openjarvis.core.registry import AgentRegistry
from openjarvis.memory.settings import load_memory_settings

logger = logging.getLogger(__name__)

HYGIENE_CRON_PROMPT = "Clean up Sage's memory."
_TASK_KEY_FIELD = "managed_by"
_TASK_KEY = "m38-memory-hygiene"
# A run counts as "last night" for this long; older, and boot catches up.
CATCH_UP_AFTER_SECONDS = 20 * 3600


def _store() -> Any:
    from openjarvis.core.config import load_config
    from openjarvis.memory.store import create_fact_store

    mem = load_config().memory
    return create_fact_store(
        getattr(mem, "backend", "local") or "local",
        path=getattr(mem, "facts_path", None),
        max_facts=getattr(mem, "max_facts", 1000) or 1000,
    )


@AgentRegistry.register("memory_hygiene")
class MemoryHygieneAgent(BaseAgent):
    """Merge duplicates, drop contradictions and stale lines, log the run."""

    agent_id = "memory_hygiene"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        settings = load_memory_settings()
        args, kwargs = apply_configured_model(
            args, kwargs, settings.cloud_model, "cloud", label="Memory hygiene"
        )
        super().__init__(*args, **kwargs)
        self._configured_model = settings.cloud_model

    def run(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        from openjarvis.memory.hygiene import run_hygiene

        settings = load_memory_settings()
        if not settings.hygiene_enabled:
            return AgentResult(content="Memory hygiene is switched off.", turns=0)
        self._emit_turn_start(input)
        run = run_hygiene(
            _store(),
            self._engine,
            self._configured_model,
            restore_window_days=settings.restore_window_days,
        )
        removed = sum(len(c.removed) for c in run.changes)
        summary = (
            f"Memory hygiene: {run.facts_before} -> {run.facts_after} facts, "
            f"{len(run.changes)} change(s), {removed} removed"
            + (f"; error: {run.error}" if run.error else "")
        )
        self._emit_turn_end(content_length=len(summary))
        return AgentResult(content=summary, turns=1)


def register_hygiene_cron(scheduler: Any) -> Optional[Any]:
    """Self-register the nightly run, replacing a stale one. Idempotent."""
    from openjarvis.agents.episode_writer import _cancel_others, _utc_hour_for_local
    from openjarvis.core.moments import configured_timezone

    settings = load_memory_settings()
    timezone_name = configured_timezone()
    # Half an hour after the episode writer, so the day's episode is written
    # from the facts as they were, and the clean-up sees the day's new facts.
    utc_hour = _utc_hour_for_local(settings.hygiene_hour_local, timezone_name)
    cron_expr = f"30 {utc_hour} * * *"
    existing = [
        task
        for task in scheduler.list_tasks()
        if task.status in {"active", "paused"}
        and task.agent == "memory_hygiene"
        and task.metadata.get(_TASK_KEY_FIELD) == _TASK_KEY
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
        prompt=HYGIENE_CRON_PROMPT,
        schedule_type="cron",
        schedule_value=cron_expr,
        agent="memory_hygiene",
        context_mode="isolated",
        metadata={
            _TASK_KEY_FIELD: _TASK_KEY,
            "hour_local": settings.hygiene_hour_local,
            "timezone": timezone_name,
        },
    )


def catch_up_hygiene(system: Any) -> bool:
    """Run the clean-up now if last night's was missed. For the boot hook."""
    from openjarvis.memory.hygiene import last_run_at

    settings = load_memory_settings()
    if not settings.hygiene_enabled:
        return False
    last = last_run_at()
    if last is not None and time.time() - last < CATCH_UP_AFTER_SECONDS:
        return False
    try:
        system.ask(HYGIENE_CRON_PROMPT, agent="memory_hygiene", tools=None)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Memory hygiene catch-up failed: %s", exc)
        return False


__all__ = [
    "HYGIENE_CRON_PROMPT",
    "MemoryHygieneAgent",
    "catch_up_hygiene",
    "register_hygiene_cron",
]
