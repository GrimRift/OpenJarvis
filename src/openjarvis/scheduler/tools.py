"""MCP tools for scheduler operations — schedule, list, pause, resume, cancel."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, FrozenSet, List, Optional, Tuple

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

# A scheduled task normally exists to *do* something, so it needs an agent
# that can call tools. See ScheduleTaskTool.execute for why not "simple".
_DEFAULT_TASK_AGENT = "orchestrator"


class ModelNotResolvedError(ValueError):
    """Raised when a requested model name cannot be matched safely."""


def _available_cloud_models() -> List[str]:
    """Cloud models whose provider is actually configured right now.

    Not the full catalogue in ``engine/cloud.py`` — only entries whose API
    key is present, matching what a call would really be able to reach.
    """
    try:
        from openjarvis.engine.cloud import CloudEngine

        return CloudEngine().list_models()
    except Exception:
        return []


def _family_tokens(name: str) -> FrozenSet[str]:
    """Letters-only words in a model name, e.g. 'gpt-5.6-luna' -> {gpt, luna}.

    Version numbers and single letters ('4o' -> 'o') are dropped since they
    are what a paraphrase tends to lose ('gpt luna' for 'gpt-5.6-luna').
    Matching on the remaining words is deliberately strict (exact set
    equality, not a subset) — see ``_resolve_model``.
    """
    return frozenset(t for t in re.split(r"[^a-zA-Z]+", name.lower()) if len(t) >= 2)


def _resolve_model(requested: str) -> Tuple[str, str]:
    """Resolve a possibly-approximate model name to one that really exists.

    Live case this exists for: asked to schedule a task "using gpt luna",
    the model wrote ``"gpt-luna"`` into the tool call — plausible-looking,
    not a real model id. Stored verbatim, it would 404 the day the task
    finally runs, unattended, with the confirmation message having already
    claimed success. Validate here instead of trusting the string.

    Returns ``(resolved_name, note)``, unchanged with no note on an exact
    match. Raises :class:`ModelNotResolvedError` — naming the available
    options — when nothing matches or more than one candidate does, so the
    tool call fails loudly rather than storing a name that only breaks
    later.
    """
    candidates = _available_cloud_models()
    if requested in candidates:
        return requested, ""

    requested_tokens = _family_tokens(requested)
    matches = [c for c in candidates if _family_tokens(c) == requested_tokens]

    if len(matches) == 1:
        return matches[0], f"Resolved {requested!r} to the real model {matches[0]!r}."

    available = (
        ", ".join(candidates) if candidates else "(no cloud provider configured)"
    )
    if len(matches) > 1:
        raise ModelNotResolvedError(
            f"{requested!r} matches more than one available model "
            f"({', '.join(matches)}) — use the exact id. Available: {available}."
        )
    raise ModelNotResolvedError(
        f"{requested!r} is not a recognized, available model. Available: {available}."
    )


def _utc_offset_hours() -> int:
    """Whole-hour offset of local time from UTC (e.g. 8 for UTC+8)."""
    local_now = datetime.now()
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    return round((local_now - utc_now).total_seconds() / 3600)


def _local_cron_to_utc(expr: str) -> Tuple[str, str]:
    """Convert a local-time cron expression to the UTC one the scheduler runs.

    ``TaskScheduler._compute_next_run`` evaluates cron against
    ``datetime.now(timezone.utc)``, so a caller asking for "8am" must store
    the UTC equivalent. Doing that arithmetic here rather than in the prompt
    follows the same reasoning as ``check_class_schedule``: small models are
    unreliable at date/time math.

    Returns ``(expression, note)``. The expression is unchanged when no
    conversion is needed or when it cannot be done safely, and *note*
    explains which happened.
    """
    offset = _utc_offset_hours()
    fields = expr.split()
    if offset == 0 or len(fields) != 5:
        return expr, ""

    minute, hour, dom, month, dow = fields
    if not hour.isdigit():
        # Ranges/steps/lists shift ambiguously; leave them alone and say so.
        return expr, (
            f"Hour field {hour!r} is not a plain number, so it was stored as-is "
            "and will be interpreted as UTC."
        )

    shifted = int(hour) - offset
    utc_hour = shifted % 24
    day_delta = -1 if shifted < 0 else (1 if shifted > 23 else 0)

    if day_delta and dom != "*":
        return expr, (
            "This time crosses a UTC day boundary and the expression pins a "
            "day of the month, which cannot be shifted safely. Stored as-is "
            "and interpreted as UTC."
        )
    if day_delta and dow != "*":
        if not dow.isdigit():
            return expr, (
                "This time crosses a UTC day boundary and the day-of-week "
                "field is not a plain number. Stored as-is and interpreted "
                "as UTC."
            )
        dow = str((int(dow) + day_delta) % 7)

    return f"{minute} {utc_hour} {dom} {month} {dow}", (
        f"Interpreted as {hour}:{minute.zfill(2)} local time (UTC{offset:+d})."
    )


def _local_once_to_utc(value: str) -> Tuple[str, str]:
    """Normalise a one-off ISO datetime to an explicit UTC instant.

    ``SchedulerStore.get_due_tasks`` compares ``next_run`` to a UTC-aware ISO
    string *as text*, so a naive local timestamp is silently read as UTC and
    fires at the wrong moment (8 hours late at UTC+8). Times written by a
    caller are local, so attach the local zone before converting.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value, ""
    if parsed.tzinfo is not None:
        return value, ""
    local = parsed.astimezone()
    return local.astimezone(timezone.utc).isoformat(), (
        f"Interpreted as {local.strftime('%Y-%m-%d %H:%M')} local time."
    )


def _describe_schedule(schedule_type: str, schedule_value: str) -> str:
    """Render a schedule in plain local-time English.

    Stored values are machine-facing — a UTC cron expression, a raw second
    count, a UTC instant — and a small model asked to interpret them gets
    them wrong (``0 0 * * *`` read as "midnight" when it is 08:00 at UTC+8,
    ``600`` read as "every hour"). Compute the description here so nothing
    is left to infer.
    """
    if schedule_type == "interval":
        try:
            seconds = int(float(schedule_value))
        except ValueError:
            return f"every {schedule_value} seconds"
        if seconds % 3600 == 0 and seconds >= 3600:
            hours = seconds // 3600
            return f"every {hours} hour{'s' if hours != 1 else ''}"
        if seconds % 60 == 0 and seconds >= 60:
            minutes = seconds // 60
            return f"every {minutes} minute{'s' if minutes != 1 else ''}"
        return f"every {seconds} second{'s' if seconds != 1 else ''}"

    if schedule_type == "once":
        local = _to_local(schedule_value)
        return f"once at {local}" if local else f"once at {schedule_value}"

    if schedule_type == "cron":
        fields = schedule_value.split()
        if len(fields) == 5:
            minute, hour, dom, month, dow = fields
            if minute.isdigit() and hour.isdigit():
                offset = _utc_offset_hours()
                local_hour = (int(hour) + offset) % 24
                when = f"{local_hour:02d}:{int(minute):02d} local time"
                if dom == "*" and month == "*" and dow == "*":
                    return f"daily at {when}"
                return f"at {when} (cron {schedule_value} UTC)"
        return f"cron {schedule_value} (UTC)"

    return f"{schedule_type} {schedule_value}"


def _to_local(iso_utc: Optional[str]) -> str:
    """Render a stored UTC ISO timestamp in local time, for confirmations."""
    if not iso_utc:
        return ""
    try:
        return (
            datetime.fromisoformat(iso_utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
        )
    except ValueError:
        return ""


def set_scheduler(scheduler: Any) -> None:
    """Give every scheduler tool the running ``TaskScheduler``.

    The tools are constructed with no arguments by the registry, and read
    ``self._scheduler`` at call time, so injecting on the classes here works
    for instances built before the scheduler exists.
    """
    for cls in (
        ScheduleTaskTool,
        ListScheduledTasksTool,
        PauseScheduledTaskTool,
        ResumeScheduledTaskTool,
        CancelScheduledTaskTool,
    ):
        cls._scheduler = scheduler


@ToolRegistry.register("schedule_task")
class ScheduleTaskTool(BaseTool):
    """Schedule a new task for future or recurring execution."""

    tool_id = "schedule_task"
    _scheduler: Optional[Any] = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="schedule_task",
            description=(
                "Schedule a prompt to run automatically, later or on a "
                "repeating schedule. Use for requests like 'every morning at "
                "8, check X' or 'remind me in 30 minutes'. Write cron times in "
                "the user's LOCAL time — the conversion to UTC is handled for "
                "you, so do not adjust the hour yourself."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The prompt/query to execute on schedule.",
                    },
                    "schedule_type": {
                        "type": "string",
                        "description": (
                            "Schedule type: 'cron', 'interval', or 'once'."
                        ),
                        "enum": ["cron", "interval", "once"],
                    },
                    "schedule_value": {
                        "type": "string",
                        "description": (
                            "Schedule value: cron expression, seconds for "
                            "interval, or ISO datetime for once."
                        ),
                    },
                    "agent": {
                        "type": "string",
                        "description": (
                            "Agent to run the prompt (default: orchestrator). "
                            "Leave unset unless the task needs no tools at "
                            "all — 'simple' is single-turn and cannot call any."
                        ),
                    },
                    "tools": {
                        "type": "string",
                        "description": (
                            "Comma-separated tool names for agent "
                            "(e.g. 'calculator,think')."
                        ),
                    },
                    "model": {
                        "type": "string",
                        "description": (
                            "Model to run this task on. Copy the id exactly "
                            "as listed, e.g. 'gpt-5.6-luna' — an approximate "
                            "name is rejected rather than guessed at. Leave "
                            "unset to use the server's default; set it when "
                            "the task needs stronger reasoning than the local "
                            "model gives."
                        ),
                    },
                },
                "required": ["prompt", "schedule_type", "schedule_value"],
            },
            category="scheduler",
        )

    def execute(self, **params: Any) -> ToolResult:
        if self._scheduler is None:
            return ToolResult(
                tool_name="schedule_task",
                content="Scheduler not available. Cannot schedule tasks.",
                success=False,
            )
        prompt = params.get("prompt", "")
        schedule_type = params.get("schedule_type", "")
        schedule_value = params.get("schedule_value", "")
        if not prompt or not schedule_type or not schedule_value:
            return ToolResult(
                tool_name="schedule_task",
                content=(
                    "Missing required parameters:"
                    " prompt, schedule_type, schedule_value."
                ),
                success=False,
            )
        note = ""
        if schedule_type == "cron":
            schedule_value, note = _local_cron_to_utc(schedule_value)
        elif schedule_type == "once":
            schedule_value, note = _local_once_to_utc(schedule_value)

        model_note = ""
        requested_model = params.get("model") or ""
        if requested_model and ":" not in requested_model:
            # A ':' marks an Ollama-style local tag (e.g. "qwen3.5:4b"), which
            # is normally copy-pasted verbatim and has no discovery source to
            # validate against here. Everything else is a free-text cloud
            # name a small model can paraphrase — resolve it against what is
            # actually reachable rather than storing it unchecked. Live case:
            # "using gpt luna" was written into the tool call as "gpt-luna",
            # which is not a real model and would 404 whenever the task ran.
            try:
                requested_model, resolve_note = _resolve_model(requested_model)
                model_note = resolve_note
            except ModelNotResolvedError as exc:
                return ToolResult(
                    tool_name="schedule_task",
                    content=str(exc),
                    success=False,
                )

        try:
            task = self._scheduler.create_task(
                prompt=prompt,
                schedule_type=schedule_type,
                schedule_value=schedule_value,
                # Not "simple": SimpleAgent is single-turn and cannot call
                # tools, so a scheduled "check X and notify me" would produce
                # text and do nothing. Tasks created from chat almost always
                # need tools, so default to the tool-calling agent.
                agent=params.get("agent") or _DEFAULT_TASK_AGENT,
                tools=params.get("tools", ""),
                # TaskScheduler._execute_task reads this back out; it rides in
                # metadata because scheduled_tasks has no migration path.
                metadata=({"model": requested_model} if requested_model else {}),
            )
            payload = {
                "task_id": task.id,
                "next_run": task.next_run,
                "next_run_local": _to_local(task.next_run),
                "status": task.status,
            }
            if note:
                payload["note"] = note
            if model_note:
                payload["model_note"] = model_note
            return ToolResult(
                tool_name="schedule_task",
                content=json.dumps(payload),
                success=True,
            )
        except Exception as exc:
            return ToolResult(
                tool_name="schedule_task",
                content=f"Failed to schedule task: {exc}",
                success=False,
            )


@ToolRegistry.register("list_scheduled_tasks")
class ListScheduledTasksTool(BaseTool):
    """List all scheduled tasks."""

    tool_id = "list_scheduled_tasks"
    _scheduler: Optional[Any] = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="list_scheduled_tasks",
            description=(
                "List scheduled tasks, optionally filtered by status. Each "
                "task includes 'schedule_human' and 'next_run_local' — quote "
                "those when describing a task's timing, and do not convert "
                "the raw cron, interval seconds, or UTC timestamps yourself."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": (
                            "Filter by status: 'active', 'paused', "
                            "'completed', 'cancelled'."
                        ),
                    },
                },
            },
            category="scheduler",
        )

    def execute(self, **params: Any) -> ToolResult:
        if self._scheduler is None:
            return ToolResult(
                tool_name="list_scheduled_tasks",
                content="Scheduler not available.",
                success=False,
            )
        try:
            status = params.get("status")
            tasks = self._scheduler.list_tasks(status=status)
            items = []
            for t in tasks:
                d = t.to_dict()
                # Stored fields are UTC/machine-facing; state the local
                # equivalents outright so they are not re-derived downstream.
                d["schedule_human"] = _describe_schedule(
                    t.schedule_type, t.schedule_value
                )
                d["next_run_local"] = _to_local(t.next_run)
                d["last_run_local"] = _to_local(t.last_run)
                items.append(d)
            return ToolResult(
                tool_name="list_scheduled_tasks",
                content=json.dumps(items, default=str),
                success=True,
            )
        except Exception as exc:
            return ToolResult(
                tool_name="list_scheduled_tasks",
                content=f"Failed to list tasks: {exc}",
                success=False,
            )


@ToolRegistry.register("pause_scheduled_task")
class PauseScheduledTaskTool(BaseTool):
    """Pause a scheduled task."""

    tool_id = "pause_scheduled_task"
    _scheduler: Optional[Any] = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="pause_scheduled_task",
            description="Pause an active scheduled task.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "ID of the task to pause.",
                    },
                },
                "required": ["task_id"],
            },
            category="scheduler",
        )

    def execute(self, **params: Any) -> ToolResult:
        if self._scheduler is None:
            return ToolResult(
                tool_name="pause_scheduled_task",
                content="Scheduler not available.",
                success=False,
            )
        task_id = params.get("task_id", "")
        if not task_id:
            return ToolResult(
                tool_name="pause_scheduled_task",
                content="Missing required parameter: task_id.",
                success=False,
            )
        try:
            self._scheduler.pause_task(task_id)
            return ToolResult(
                tool_name="pause_scheduled_task",
                content=f"Task {task_id} paused.",
                success=True,
            )
        except KeyError:
            return ToolResult(
                tool_name="pause_scheduled_task",
                content=f"Task not found: {task_id}",
                success=False,
            )
        except Exception as exc:
            return ToolResult(
                tool_name="pause_scheduled_task",
                content=f"Failed to pause task: {exc}",
                success=False,
            )


@ToolRegistry.register("resume_scheduled_task")
class ResumeScheduledTaskTool(BaseTool):
    """Resume a paused scheduled task."""

    tool_id = "resume_scheduled_task"
    _scheduler: Optional[Any] = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="resume_scheduled_task",
            description="Resume a paused scheduled task.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "ID of the task to resume.",
                    },
                },
                "required": ["task_id"],
            },
            category="scheduler",
        )

    def execute(self, **params: Any) -> ToolResult:
        if self._scheduler is None:
            return ToolResult(
                tool_name="resume_scheduled_task",
                content="Scheduler not available.",
                success=False,
            )
        task_id = params.get("task_id", "")
        if not task_id:
            return ToolResult(
                tool_name="resume_scheduled_task",
                content="Missing required parameter: task_id.",
                success=False,
            )
        try:
            self._scheduler.resume_task(task_id)
            return ToolResult(
                tool_name="resume_scheduled_task",
                content=f"Task {task_id} resumed.",
                success=True,
            )
        except KeyError:
            return ToolResult(
                tool_name="resume_scheduled_task",
                content=f"Task not found: {task_id}",
                success=False,
            )
        except Exception as exc:
            return ToolResult(
                tool_name="resume_scheduled_task",
                content=f"Failed to resume task: {exc}",
                success=False,
            )


@ToolRegistry.register("cancel_scheduled_task")
class CancelScheduledTaskTool(BaseTool):
    """Cancel a scheduled task."""

    tool_id = "cancel_scheduled_task"
    _scheduler: Optional[Any] = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="cancel_scheduled_task",
            description="Cancel a scheduled task.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "ID of the task to cancel.",
                    },
                },
                "required": ["task_id"],
            },
            category="scheduler",
        )

    def execute(self, **params: Any) -> ToolResult:
        if self._scheduler is None:
            return ToolResult(
                tool_name="cancel_scheduled_task",
                content="Scheduler not available.",
                success=False,
            )
        task_id = params.get("task_id", "")
        if not task_id:
            return ToolResult(
                tool_name="cancel_scheduled_task",
                content="Missing required parameter: task_id.",
                success=False,
            )
        try:
            self._scheduler.cancel_task(task_id)
            return ToolResult(
                tool_name="cancel_scheduled_task",
                content=f"Task {task_id} cancelled.",
                success=True,
            )
        except KeyError:
            return ToolResult(
                tool_name="cancel_scheduled_task",
                content=f"Task not found: {task_id}",
                success=False,
            )
        except Exception as exc:
            return ToolResult(
                tool_name="cancel_scheduled_task",
                content=f"Failed to cancel task: {exc}",
                success=False,
            )


__all__ = [
    "CancelScheduledTaskTool",
    "ListScheduledTasksTool",
    "PauseScheduledTaskTool",
    "ResumeScheduledTaskTool",
    "ScheduleTaskTool",
]
