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
            _header,
            _metadata_message,
            message_url,
            query_words,
            score_match,
            search_messages,
        )
        from openjarvis.tools.opera_control import (
            _NAV_TIMEOUT,
            ensure_opera,
            opera_session,
        )

        query = str(params.get("query") or "").strip()
        if not query:
            return self._fail("Which message should I open?")
        if not Path(_TOKEN_PATH).exists():
            return self._fail("Gmail is not connected. Run: jarvis connect gmail")
        problem = ensure_opera(minimized=False)
        if problem:
            return self._fail(problem)

        try:
            from openjarvis.connectors.google_auth import call_with_refresh

            ids = call_with_refresh(
                lambda token: search_messages(token, query, 1),
                _TOKEN_PATH,
            )
            meta = (
                call_with_refresh(
                    lambda token: _metadata_message(token, ids[0]), _TOKEN_PATH
                )
                if ids
                else {}
            )
        except Exception as error:
            return self._fail(f"could not search Gmail: {error}")

        headers = (meta.get("payload") or {}).get("headers") or []
        subject = _header(headers, "Subject") or "(no subject)"
        sender = _header(headers, "From") or "unknown sender"
        when = _header(headers, "Date")
        named = f'"{subject}" from {sender}' + (f" ({when})" if when else "")
        # Gmail's fallback search ORs the words, so a lone "reminder" matched
        # an Academia.edu promo for "PMFC examination reminder" -- which lives
        # in Outlook -- and it was opened three times as if it were the one
        # (2 October). Open only what matches at least half of the words.
        words = query_words(query)
        if ids and words:
            haystack = " ".join([sender, subject, meta.get("snippet") or ""])
            if score_match(haystack, words) * 2 < len(words):
                return ToolResult(
                    tool_name=self.tool_id,
                    content=(
                        f"No Gmail message matches {query!r} well, so nothing "
                        f"was opened. The closest was {named}, which is not it. "
                        "It is not in Gmail: call outlook_open with the same "
                        "words now, before answering (the user has two "
                        "mailboxes; school mail is in Outlook)."
                    ),
                    success=True,
                    metadata={"found": False, "closest": ids[0]},
                )

        if not ids:
            # A miss is a miss, never evidence that the message was imagined.
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    f"No Gmail message matches {query!r}, so there is nothing "
                    "to open. It may be worded differently, or be in Outlook "
                    "(call outlook_open with the same words before answering)."
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
            content=(
                f"Opened in Gmail: {named}.{where} Name it when you tell the "
                "user, so they can see it is the right one."
            ),
            success=True,
            metadata={"found": True, "id": ids[0], "url": url},
        )

    def _fail(self, reason: str) -> ToolResult:
        return ToolResult(tool_name=self.tool_id, content=reason, success=False)
