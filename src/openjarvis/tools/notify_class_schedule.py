"""Deterministic class-schedule notification.

Checks the schedule and sends a Windows toast ONLY if something is actually
upcoming — the notify decision is made here in code, not left to the model.

Exists because the two-tool pattern (model calls check_class_schedule, then
decides whether to call notify_windows) proved unreliable in practice: even
with an explicit "if nothing is upcoming, do not call notify_windows"
instruction, the local model (qwen3.5:4b) called notify_windows anyway on
multiple scheduled runs, fabricating a notification from classes later in
the day framed as "starting soon." That's a small-model instruction-following
gap no amount of prompt wording reliably closes — so for the automated
schedule-check task specifically, the decision is removed from the model.

Once-per-day suppression also lives here rather than in the checker, because
it is a property of having *notified*, not of having *looked*. While the
checker owned it, the daily "what's coming up today" summary consumed every
reminder on the schedule hours before any of them were due.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.core.paths import get_data_dir
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec
from openjarvis.tools.check_class_schedule import CheckClassScheduleTool
from openjarvis.tools.notify_windows import deliver, speak

#: Minutes before a class to remind, largest first. Two alerts: one with
#: enough time to move, and one that catches a first alert nobody noticed.
#: The poll has to run more often than the smallest gap or the 5-minute
#: reminder falls between checks.
REMINDER_STAGES = (15, 5)


def _stage_for(minutes_until: float) -> Optional[int]:
    """Which reminder a class this close belongs to, or None if too far off."""
    applicable = [stage for stage in REMINDER_STAGES if minutes_until <= stage]
    return min(applicable) if applicable else None


def _default_state_path() -> Path:
    return get_data_dir() / "class_schedule_notify_state.json"


@ToolRegistry.register("notify_class_schedule")
class NotifyClassScheduleTool(BaseTool):
    """Check for an imminent class and notify only if one is actually upcoming."""

    tool_id = "notify_class_schedule"
    is_local = False

    def __init__(
        self,
        schedule_path: Optional[str] = None,
        state_path: Optional[Path] = None,
    ) -> None:
        self._checker = CheckClassScheduleTool(schedule_path=schedule_path)
        self._state_path = Path(state_path) if state_path else _default_state_path()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="notify_class_schedule",
            description=(
                "Check for a class starting imminently and send a Windows "
                "toast notification, but ONLY if one is actually upcoming — "
                "the check and the notify decision both happen inside this "
                "single tool call, with no further judgment call needed. "
                "Use this alone for any 'notify me if a class is starting "
                "soon' task; do not call check_class_schedule and "
                "notify_windows separately for that purpose."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "lookahead_minutes": {
                        "type": "integer",
                        "description": (
                            "How far ahead to search. Reminders still only "
                            "fire at the fixed stages (15 and 5 minutes "
                            "before), so widening this finds more classes "
                            "but does not notify about them sooner."
                        ),
                        "default": 15,
                    },
                },
                "required": [],
            },
            category="scheduling",
            timeout_seconds=10.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        now: datetime = params.get("now") or datetime.now()
        # Only the reminder window is forwarded, never the caller's whole
        # param dict: a hallucinated full_day=true would turn this from "a
        # class starts in 15 minutes" into a toast for every remaining class
        # of the day, each one burning its once-per-day suppression.
        checker_params: Dict[str, Any] = {"now": now, "full_day": False}
        if params.get("lookahead_minutes") is not None:
            checker_params["lookahead_minutes"] = params["lookahead_minutes"]
        check_result = self._checker.execute(**checker_params)
        if not check_result.success:
            return ToolResult(
                tool_name="notify_class_schedule",
                content=check_result.content,
                success=False,
            )

        upcoming = (check_result.metadata or {}).get("upcoming") or []
        state = self._load_state(now)
        already = set(state.get("notified", []))
        # Each class earns two reminders, so the suppression key carries the
        # stage. Keyed on the class alone, the 15-minute alert consumed the
        # 5-minute one.
        staged = []
        for item in upcoming:
            stage = _stage_for(float(item.get("minutes_until") or 0))
            if stage is None:
                continue
            if self._occurrence_key(item, now, stage) in already:
                continue
            staged.append((item, stage))
        pending = [item for item, _ in staged]
        if not pending:
            return ToolResult(
                tool_name="notify_class_schedule",
                content="Nothing upcoming — no notification sent.",
                success=True,
                metadata={"notified": False, "upcoming": []},
            )

        notified: List[str] = []
        for cls, stage in staged:
            message = (
                f"{cls['subject_description']} ({cls['subject_code']}) at "
                f"{cls['start_time']} in {cls['room']} ({cls['mode']})"
            )
            try:
                deliver("Class starting soon", message, duration="long")
                # Spoken after the toast, and only ever as an addition to it:
                # a reminder the user has to be looking at the screen to
                # catch is the one they miss. Silent under Do Not Disturb.
                # The real remaining time, not the stage that triggered it.
                # A poll interval as long as the stage means a 5-minute alert
                # can fire with one minute left, and "in 5 minutes" would then
                # be the one thing the user acts on and the one thing wrong.
                left = max(1, round(float(cls.get("minutes_until") or stage)))
                unit = "minute" if left == 1 else "minutes"
                speak(f"{cls['subject_description']} in {left} {unit}.")
            except Exception as exc:
                # Record only what was delivered, so the classes that did not
                # go out are retried on the next run instead of being
                # suppressed for the rest of the day.
                self._record(state, already, now)
                return ToolResult(
                    tool_name="notify_class_schedule",
                    content=f"Failed to send notification: {exc}",
                    success=False,
                    metadata={"notified": bool(notified), "upcoming": pending},
                )
            already.add(self._occurrence_key(cls, now, stage))
            notified.append(f"{message} [{stage}-minute reminder]")

        self._record(state, already, now)
        return ToolResult(
            tool_name="notify_class_schedule",
            content=f"Notified about {len(notified)} class(es): " + "; ".join(notified),
            success=True,
            metadata={"notified": True, "upcoming": pending},
        )

    @staticmethod
    def _occurrence_key(item: Dict[str, Any], now: datetime, stage: int) -> str:
        return (
            f"{item['subject_code']}|{now.strftime('%A')}|"
            f"{item['section']}|{stage}"
        )

    def _load_state(self, now: datetime) -> Dict[str, Any]:
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        if state.get("date") != now.date().isoformat():
            return {"date": now.date().isoformat(), "notified": []}
        return state

    def _record(self, state: Dict[str, Any], keys: set, now: datetime) -> None:
        if set(state.get("notified", [])) == keys:
            return
        state["date"] = now.date().isoformat()
        state["notified"] = sorted(keys)
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(state), encoding="utf-8")
        except OSError:
            pass
