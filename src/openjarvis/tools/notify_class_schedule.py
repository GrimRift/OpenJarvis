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
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec
from openjarvis.tools.check_class_schedule import CheckClassScheduleTool
from openjarvis.tools.notify_windows import _send_toast


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
        self._checker = CheckClassScheduleTool(
            schedule_path=schedule_path, state_path=state_path
        )

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
        check_result = self._checker.execute(**params)
        if not check_result.success:
            return ToolResult(
                tool_name="notify_class_schedule",
                content=check_result.content,
                success=False,
            )

        upcoming = (check_result.metadata or {}).get("upcoming") or []
        if not upcoming:
            return ToolResult(
                tool_name="notify_class_schedule",
                content="Nothing upcoming — no notification sent.",
                success=True,
                metadata={"notified": False, "upcoming": []},
            )

        notified = []
        for cls in upcoming:
            message = (
                f"{cls['subject_description']} ({cls['subject_code']}) at "
                f"{cls['start_time']} in {cls['room']} ({cls['mode']})"
            )
            try:
                _send_toast("Class starting soon", message, duration="long")
            except Exception as exc:
                return ToolResult(
                    tool_name="notify_class_schedule",
                    content=f"Failed to send notification: {exc}",
                    success=False,
                    metadata={"notified": False, "upcoming": upcoming},
                )
            notified.append(message)

        return ToolResult(
            tool_name="notify_class_schedule",
            content=f"Notified about {len(notified)} class(es): " + "; ".join(notified),
            success=True,
            metadata={"notified": True, "upcoming": upcoming},
        )
