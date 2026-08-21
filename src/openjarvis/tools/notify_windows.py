"""Windows notification tool — pop a native Windows toast."""

from __future__ import annotations

from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


def _send_toast(title: str, message: str, *, duration: str = "short") -> None:
    # Use notify(), not toast() — toast() blocks until the notification is
    # clicked or dismissed (it awaits activation/dismissal futures), which
    # would hang this tool indefinitely waiting on the user. notify() builds
    # and shows the toast synchronously and returns immediately.
    from win11toast import notify

    notify(title, message, duration=duration)


@ToolRegistry.register("notify_windows")
class NotifyWindowsTool(BaseTool):
    """Send a native Windows toast notification."""

    tool_id = "notify_windows"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="notify_windows",
            description=(
                "Pop a native Windows toast notification with a title and "
                "message. Use for proactively alerting the user about "
                "something time-sensitive."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Notification title.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Notification body text.",
                    },
                    "duration": {
                        "type": "string",
                        "description": "How long the toast stays visible.",
                        "enum": ["short", "long"],
                    },
                },
                "required": ["title", "message"],
            },
            category="notification",
            timeout_seconds=10.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        title = params.get("title", "")
        message = params.get("message", "")
        duration = params.get("duration", "short")

        if not title or not message:
            return ToolResult(
                tool_name="notify_windows",
                content="Both title and message are required.",
                success=False,
            )

        try:
            _send_toast(title, message, duration=duration)
        except Exception as exc:
            return ToolResult(
                tool_name="notify_windows",
                content=f"Failed to send notification: {exc}",
                success=False,
            )

        return ToolResult(
            tool_name="notify_windows",
            content="Notification sent.",
            success=True,
            metadata={"title": title, "message": message},
        )
