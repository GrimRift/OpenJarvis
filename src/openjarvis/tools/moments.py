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
                "The user's control over Sage speaking unprompted (greetings, "
                "welcome back, tell-me-when, and Sage starting conversations). "
                "Normal replies and reminders the user scheduled are never "
                "affected. Call it when the user says any of: 'not now' / "
                "'quiet today' (silent until tomorrow); 'be quiet for 30 "
                "minutes' / 'stop talking for now' (pass minutes; default 30 "
                "when unstated); 'continue' / 'you can talk again' "
                "(resume=true); 'only speak when I call you' / 'stop starting "
                "conversations' (initiative='off'); 'you can start "
                "conversations again' (initiative='gentle')."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "minutes": {
                        "type": "number",
                        "description": (
                            "Quiet for this many minutes, not the rest of the day."
                        ),
                    },
                    "resume": {
                        "type": "boolean",
                        "description": "True to lift any quiet now.",
                    },
                    "initiative": {
                        "type": "string",
                        "enum": ["off", "gentle", "curious", "social"],
                        "description": (
                            "Set whether Sage may start conversations: 'off' for "
                            "'only speak when I call you'; a mode to allow it again."
                        ),
                    },
                },
                "required": [],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        resume = bool(params.get("resume", False))
        minutes = params.get("minutes")
        initiative = str(params.get("initiative") or "").strip().lower()
        engine = _engine()
        notes = []

        if initiative:
            from openjarvis.core.presence import (
                INITIATIVE_MODES,
                apply_initiative_mode,
                load_settings,
                save_settings,
            )

            if initiative not in INITIATIVE_MODES:
                return ToolResult(
                    tool_name=self.tool_id,
                    content=f"initiative must be one of {', '.join(INITIATIVE_MODES)}.",
                    success=False,
                )
            settings = load_settings()
            apply_initiative_mode(settings, initiative)
            save_settings(settings)
            notes.append(
                "I won't start conversations; I'll speak when you call me."
                if initiative == "off"
                else f"I may start conversations again ({initiative})."
            )

        if resume:
            if engine is not None:
                engine.resume()
            else:
                state = load_state()
                state.snoozed_day = ""
                state.snoozed_until = None
                save_state(state)
            notes.append("Unprompted moments are back on.")
        elif minutes is not None:
            try:
                span = float(minutes)
            except (TypeError, ValueError):
                span = 0.0
            if span <= 0:
                return ToolResult(
                    tool_name=self.tool_id,
                    content="minutes must be a positive number.",
                    success=False,
                )
            if engine is not None:
                engine.snooze_for(span * 60)
            else:
                state = load_state()
                state.snoozed_until = time.time() + span * 60
                save_state(state)
            notes.append(f"Understood. Nothing unprompted for {int(span)} minutes.")
        elif not initiative:
            if engine is not None:
                engine.snooze_today(True)
            else:
                state = load_state()
                today = to_local(time.time(), configured_timezone()).date().isoformat()
                state.snoozed_day = today
                save_state(state)
            notes.append("Understood. No unprompted moments for the rest of today.")

        return ToolResult(
            tool_name=self.tool_id,
            content=" ".join(notes),
            success=True,
            metadata={
                "resume": resume,
                "minutes": minutes,
                "initiative": initiative or None,
            },
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
                "'tell me in five minutes that I have to eat', 'tell me when "
                "that job finishes'. Give exactly one of: `in_minutes` (a "
                "relative time -- 'in five minutes' is 5, 'in an hour' is 60, "
                "'in two hours' is 120; never refuse a relative time), `at` "
                "(a local ISO datetime you have resolved, e.g. "
                "'2026-09-15T15:00'), or `task_id` (a scheduled task id from "
                "list_scheduled_tasks). A relative time is a reminder: said "
                "aloud and shown as a Windows toast when due, wherever the "
                "user is. An absolute time or a job is a watch: said when due "
                "and the user is at the desk (pass anywhere=true to make it a "
                "reminder too). action='list' shows pending ones; "
                "action='cancel' with watch_id removes one."
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
                    "in_minutes": {
                        "type": "number",
                        "description": (
                            "Minutes from now. Use for any relative time the "
                            "user gives."
                        ),
                    },
                    "at": {
                        "type": "string",
                        "description": "Local ISO datetime for a time watch.",
                    },
                    "anywhere": {
                        "type": "boolean",
                        "description": (
                            "Deliver as a reminder (toast + voice, wherever the "
                            "user is) rather than only at the desk. Implied by "
                            "in_minutes."
                        ),
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
        raw_minutes = params.get("in_minutes")
        in_minutes: Optional[float] = None
        if raw_minutes not in (None, ""):
            try:
                in_minutes = float(raw_minutes)
            except (TypeError, ValueError):
                return ToolResult(
                    tool_name=self.tool_id,
                    content=f"Could not read {raw_minutes!r} as minutes.",
                    success=False,
                )
            if in_minutes <= 0:
                return ToolResult(
                    tool_name=self.tool_id,
                    content="in_minutes must be positive.",
                    success=False,
                )
        given = sum(1 for v in (at, task_id, in_minutes) if v not in ("", None))
        if given != 1:
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    "Give exactly one of `in_minutes`, `at` (a local datetime) "
                    "or `task_id`."
                ),
                success=False,
            )
        due_at = None
        now = time.time()
        if in_minutes is not None:
            due_at = now + in_minutes * 60
        elif at:
            due_at = local_to_timestamp(at, configured_timezone())
            if due_at is None:
                return ToolResult(
                    tool_name=self.tool_id,
                    content=f"Could not read {at!r} as a local ISO datetime.",
                    success=False,
                )
        anywhere = in_minutes is not None or bool(params.get("anywhere", False))
        if anywhere and due_at is None:
            return ToolResult(
                tool_name=self.tool_id,
                content="A reminder needs a time; a job watch is said at the desk.",
                success=False,
            )
        try:
            if engine is not None:
                watch = engine.add_watch(
                    what, due_at=due_at, task_id=task_id or None, anywhere=anywhere
                )
            else:
                state = load_state()
                watch = Watch(
                    id=uuid.uuid4().hex[:8],
                    what=what,
                    created_at=now,
                    due_at=due_at,
                    task_id=task_id or None,
                    anywhere=anywhere,
                )
                state.watches.append(watch)
                save_state(state)
        except ValueError as exc:
            return ToolResult(tool_name=self.tool_id, content=str(exc), success=False)
        if due_at is not None:
            local_due = to_local(due_at, configured_timezone())
            when = (
                f"in {_minutes_phrase(in_minutes)} ({local_due.strftime('%H:%M')})"
                if in_minutes is not None
                else f"at {local_due.strftime('%H:%M on %A')}"
            )
        else:
            when = f"when task {task_id} finishes"
        how = (
            "Said aloud and shown as a toast, wherever you are"
            if anywhere
            else "Only if you're at the desk"
        )
        return ToolResult(
            tool_name=self.tool_id,
            content=f"I'll tell you {when}: {what}. ({how}; watch id {watch.id}.)",
            success=True,
            metadata={"watch": watch.to_dict()},
        )


def _minutes_phrase(minutes: Optional[float]) -> str:
    total = int(round(minutes or 0))
    hours, mins = divmod(total, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins or not parts:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    return " ".join(parts)


def _describe(watches: Any) -> str:
    tz = configured_timezone()
    lines = []
    for w in watches:
        when = (
            to_local(w.due_at, tz).strftime("%a %H:%M")
            if w.due_at is not None
            else f"task {w.task_id}"
        )
        lines.append(f"{w.id}: {w.what} ({when}{', anywhere' if w.anywhere else ''})")
    return "\n".join(lines)


__all__ = ["NotNowTool", "TellMeWhenTool"]
