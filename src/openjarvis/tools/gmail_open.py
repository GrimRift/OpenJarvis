"""Put one Gmail message on screen, for the user to read themselves.

The companion to ``gmail_read``'s search: that one reads a message *to* the
user, this one opens it *for* them. "Tell me more about the OpenAI email" is a
question; "open that email" is a request to look at it, and answering the
second by reciting the body is not what was asked.

The message is found through the Gmail API and opened by its own URL, so the
browser lands on that message rather than on an inbox the user then has to
search. Read-only in both halves: nothing is replied to, sent or deleted, and
no link inside the message is followed.
"""

from __future__ import annotations

from typing import Any, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


@ToolRegistry.register("gmail_open")
class GmailOpenTool(BaseTool):
    """Open one Gmail message in the user's browser."""

    tool_id = "gmail_open"
    is_local = False

    def __init__(self, allowed_dirs: Optional[List[str]] = None) -> None:
        super().__init__()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="gmail_open",
            description=(
                "Open a specific Gmail message in the user's browser so they "
                "can read it themselves. Use for 'open that email', 'show me "
                "that Gmail', 'pull up the OpenAI email'. To ANSWER a question "
                "about a message instead, use gmail_read with a query — that "
                "reads it aloud rather than opening a window."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Words identifying the message — sender, subject "
                            "words, or Gmail search syntax."
                        ),
                    },
                    "monitor": {
                        "type": "integer",
                        "description": (
                            "Optional monitor number. Omit unless the user "
                            "named a screen."
                        ),
                    },
                },
                "required": ["query"],
            },
            category="productivity",
            timeout_seconds=45.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        from pathlib import Path

        from openjarvis.tools.gmail_read import (
            _TOKEN_PATH,
            message_url,
            search_messages,
        )
        from openjarvis.tools.opera_control import (
            _NAV_TIMEOUT,
            opera_session,
            port_is_open,
            setup_hint,
        )

        query = str(params.get("query") or "").strip()
        if not query:
            return self._fail("Which message should I open?")
        if not Path(_TOKEN_PATH).exists():
            return self._fail("Gmail is not connected. Run: jarvis connect gmail")
        if not port_is_open():
            return self._fail(setup_hint())

        try:
            from openjarvis.connectors.google_auth import call_with_refresh

            ids = call_with_refresh(
                lambda token: search_messages(token, query, 1),
                _TOKEN_PATH,
            )
        except Exception as error:
            return self._fail(f"could not search Gmail: {error}")

        if not ids:
            # A miss is a miss, never evidence that the message was imagined.
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    f"No Gmail message matches {query!r}, so there is nothing "
                    "to open. It may be worded differently."
                ),
                success=True,
                metadata={"found": False},
            )

        url = message_url(ids[0])
        monitor = params.get("monitor")
        try:
            with opera_session(own_window=monitor is not None) as session:
                session.page.navigate(url, timeout=_NAV_TIMEOUT)
                where = session.move_to_monitor(monitor)
        except Exception as error:
            return self._fail(f"could not open that message: {error}")

        return ToolResult(
            tool_name=self.tool_id,
            content=f"Opened that message in Gmail.{where}",
            success=True,
            metadata={"found": True, "id": ids[0], "url": url},
        )

    def _fail(self, reason: str) -> ToolResult:
        return ToolResult(tool_name=self.tool_id, content=reason, success=False)
