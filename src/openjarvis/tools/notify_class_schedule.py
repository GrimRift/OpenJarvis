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
from openjarvis.tools.notify_windows import deliver


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
                            "How many minutes ahead to look for an upcoming "
                            "class. Default 15."
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
        pending = [c for c in upcoming if self._occurrence_key(c, now) not in already]
        if not pending:
            return ToolResult(
                tool_name="notify_class_schedule",
                content="Nothing upcoming — no notification sent.",
                success=True,
                metadata={"notified": False, "upcoming": []},
            )

        notified: List[str] = []
        for cls in pending:
            message = (
                f"{cls['subject_description']} ({cls['subject_code']}) at "
                f"{cls['start_time']} in {cls['room']} ({cls['mode']})"
            )
            try:
                deliver("Class starting soon", message, duration="long")
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
            already.add(self._occurrence_key(cls, now))
            notified.append(message)

        self._record(state, already, now)
        return ToolResult(
            tool_name="notify_class_schedule",
            content=f"Notified about {len(notified)} class(es): " + "; ".join(notified),
            success=True,
            metadata={"notified": True, "upcoming": pending},
        )

    @staticmethod
    def _occurrence_key(item: Dict[str, Any], now: datetime) -> str:
        return f"{item['subject_code']}|{now.strftime('%A')}|{item['section']}"

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
