"""The two things the user can say about Sage's unprompted moments (M36).

``not_now`` silences every moment for the rest of the local day. It is a
tool rather than a phrase the model judges: "not now" has to work every time
it is said, typed or spoken, with no room for the model to decide the user
meant something else.

``tell_me_when`` registers a watch -- a time, or a scheduled job -- that
Sage will speak about when it arrives, if the user is at the desk. The model
resolves the time itself (it has the schedule and the clock); the tool only
records it.

Both go through the running engine when there is one, so the server's
in-memory state is the single writer; without a server they edit the state
file directly.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from openjarvis.core.moments import (
    MomentEngine,
    Watch,
    configured_timezone,
    current_engine,
    load_state,
    local_to_timestamp,
    save_state,
    to_local,
)
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


def _engine() -> Optional[MomentEngine]:
    return current_engine()


@ToolRegistry.register("not_now")
class NotNowTool(BaseTool):
    """Silence Sage's unprompted moments for the rest of today."""

    tool_id = "not_now"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="not_now",
            description=(
                "Call this whenever the user says 'not now', 'quiet today', "
                "'stop greeting me', or otherwise asks you to stop speaking "
                "unprompted for the day. It silences every unprompted moment "
                "(good morning, welcome back, tell-me-when) until tomorrow, "
                "and only those: normal replies are unaffected. Call it with "
                "resume=true when the user says to resume today."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "resume": {
                        "type": "boolean",
                        "description": "True to lift today's silence instead.",
                    }
                },
                "required": [],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        resume = bool(params.get("resume", False))
        engine = _engine()
        if engine is not None:
            engine.snooze_today(not resume)
        else:
            state = load_state()
            today = to_local(time.time(), configured_timezone()).date().isoformat()
            state.snoozed_day = "" if resume else today
            save_state(state)
        return ToolResult(
            tool_name=self.tool_id,
            content=(
                "Unprompted moments are back on for today."
                if resume
                else "Understood. No unprompted moments for the rest of today."
            ),
            success=True,
            metadata={"snoozed_today": not resume},
        )


@ToolRegistry.register("tell_me_when")
class TellMeWhenTool(BaseTool):
    """Register, list or cancel a 'tell me when' watch."""

    tool_id = "tell_me_when"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="tell_me_when",
            description=(
                "The user asked to be TOLD, out loud, when something happens: "
                "'tell me when my class starts', 'let me know when it's 3', "
                "'tell me when that job finishes'. Register a watch with "
                "either `at` (a local ISO datetime you have resolved, e.g. "
                "'2026-09-15T15:00' -- work it out from the schedule or the "
                "clock first) or `task_id` (a scheduled task id from "
                "list_scheduled_tasks). Sage speaks when it is due and the "
                "user is at the desk. Not for reminders that should reach the "
                "phone -- those are schedule_task with a notification. "
                "action='list' shows pending watches; action='cancel' with "
                "watch_id removes one."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "list", "cancel"],
                        "description": "Defaults to add.",
                    },
                    "what": {
                        "type": "string",
                        "description": (
                            "What the user wants to be told, in their words: "
                            "'your Structural Analysis class starts'."
                        ),
                    },
                    "at": {
                        "type": "string",
                        "description": "Local ISO datetime for a time watch.",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Scheduled task id for a job watch.",
                    },
                    "watch_id": {
                        "type": "string",
                        "description": "Watch id to cancel.",
                    },
                },
                "required": [],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        action = str(params.get("action") or "add").strip().lower()
        engine = _engine()
        if action == "list":
            watches = engine.list_watches() if engine else load_state().watches
            return ToolResult(
                tool_name=self.tool_id,
                content=_describe(watches) or "No pending watches.",
                success=True,
                metadata={"watches": [w.to_dict() for w in watches]},
            )
        if action == "cancel":
            watch_id = str(params.get("watch_id") or "").strip()
            if not watch_id:
                return ToolResult(
                    tool_name=self.tool_id,
                    content="A watch_id is needed to cancel; call with action='list'.",
                    success=False,
                )
            if engine is not None:
                removed = engine.cancel_watch(watch_id)
            else:
                state = load_state()
                before = len(state.watches)
                state.watches = [w for w in state.watches if w.id != watch_id]
                removed = len(state.watches) != before
                if removed:
                    save_state(state)
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    f"Cancelled watch {watch_id}."
                    if removed
                    else f"No watch called {watch_id}."
                ),
                success=removed,
            )

        what = str(params.get("what") or "").strip()
        if not what:
            return ToolResult(
                tool_name=self.tool_id,
                content="Say what the user wants to be told about (`what`).",
                success=False,
            )
        at = str(params.get("at") or "").strip()
        task_id = str(params.get("task_id") or "").strip()
        if bool(at) == bool(task_id):
            return ToolResult(
                tool_name=self.tool_id,
                content="Give exactly one of `at` (a local datetime) or `task_id`.",
                success=False,
            )
        due_at = None
        if at:
            due_at = local_to_timestamp(at, configured_timezone())
            if due_at is None:
                return ToolResult(
                    tool_name=self.tool_id,
                    content=f"Could not read {at!r} as a local ISO datetime.",
                    success=False,
                )
        try:
            if engine is not None:
                watch = engine.add_watch(what, due_at=due_at, task_id=task_id or None)
            else:
                state = load_state()
                watch = Watch(
                    id=uuid.uuid4().hex[:8],
                    what=what,
                    created_at=time.time(),
                    due_at=due_at,
                    task_id=task_id or None,
                )
                state.watches.append(watch)
                save_state(state)
        except ValueError as exc:
            return ToolResult(tool_name=self.tool_id, content=str(exc), success=False)
        when = (
            f"at {to_local(due_at, configured_timezone()).strftime('%H:%M on %A')}"
            if due_at is not None
            else f"when task {task_id} finishes"
        )
        return ToolResult(
            tool_name=self.tool_id,
            content=(
                f"I'll tell you {when}: {what}. (Only if you're at the desk; "
                f"watch id {watch.id}.)"
            ),
            success=True,
            metadata={"watch": watch.to_dict()},
        )


def _describe(watches: Any) -> str:
    tz = configured_timezone()
    lines = []
    for w in watches:
        when = (
            to_local(w.due_at, tz).strftime("%a %H:%M")
            if w.due_at is not None
            else f"task {w.task_id}"
        )
        lines.append(f"{w.id}: {w.what} ({when})")
    return "\n".join(lines)


__all__ = ["NotNowTool", "TellMeWhenTool"]
