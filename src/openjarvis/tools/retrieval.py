"""Retrieval tool — search memory backends for relevant context."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec
from openjarvis.tools.storage._stubs import MemoryBackend, RetrievalResult

logger = logging.getLogger(__name__)


def _timestamp_with_age(value: str) -> str:
    """Pair an exact timestamp with deterministic relative age."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
        seconds = (now - parsed).total_seconds()
    except (TypeError, ValueError):
        return value

    future = seconds < 0
    seconds = abs(seconds)
    if seconds < 86400:
        hours = max(1, int(seconds // 3600))
        age = f"{hours} hour{'s' if hours != 1 else ''}"
    else:
        days = int(seconds // 86400)
        age = f"{days} day{'s' if days != 1 else ''}"
    relation = f"in {age}" if future else f"{age} ago"
    return f"{value} ({relation})"


def _format_results(results: list[RetrievalResult]) -> str:
    """Expose provenance the model needs to judge recency accurately."""
    lines: list[str] = []
    for result in results:
        details: list[str] = []
        if result.source:
            details.append(f"Source: {result.source}")

        timestamp = str(result.metadata.get("timestamp", "")).strip()
        if timestamp:
            details.append(f"Date: {_timestamp_with_age(timestamp)}")

        title = str(result.metadata.get("title", "")).strip()
        if title:
            label = (
                "Subject"
                if result.source in {"gmail", "gmail_imap", "outlook"}
                else "Title"
            )
            details.append(f"{label}: {title}")

        author = str(result.metadata.get("author", "")).strip()
        if author:
            details.append(f"From: {author}")

        prefix = f"[{' | '.join(details)}] " if details else ""
        lines.append(f"{prefix}{result.content}")
    return "\n\n".join(lines)


def resolve_retrieval_backend(
    fallback: Optional[MemoryBackend] = None,
) -> Optional[MemoryBackend]:
    """Pick the backend the ``retrieval`` tool should search.

    ``memory.db`` (the generic ``memory_*`` scratchpad) and ``knowledge.db``
    (everything ``jarvis connect --sync`` ingests: Obsidian notes, mail, etc.)
    are separate stores. This tool advertises itself as "search the knowledge
    base" but was wired to the former, which on a real machine held a single
    stale test document — so every retrieval returned that same irrelevant
    chunk and the agent burned its remaining turns hunting for a note it could
    never reach. The ``memory_*`` tools keep using ``memory.db``; only
    ``retrieval`` moves.

    Shared by every construction site (``SystemBuilder`` for ``jarvis serve``,
    ``cli/ask.py`` for one-shot CLI runs) so they cannot drift apart.
    """
    try:
        from openjarvis.connectors.store import KnowledgeStore

        return KnowledgeStore()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falling back to memory backend for retrieval: %s", exc)
        return fallback


@ToolRegistry.register("retrieval")
class RetrievalTool(BaseTool):
    """Search the memory backend and return formatted context."""

    tool_id = "retrieval"

    def __init__(
        self,
        backend: Optional[MemoryBackend] = None,
        *,
        top_k: int = 5,
    ) -> None:
        self._backend = backend
        self._top_k = top_k

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="retrieval",
            description=(
                "Search everything the user has connected and ingested: "
                "their email/Gmail inbox, Obsidian notes, calendar entries, "
                "documents and listening history. Use this for any question "
                "about the user's own mail, messages, notes or files — "
                "including 'check my inbox', 'what emails do I have', or "
                "'find that note'. This is the only way to reach that "
                "content; there is no separate email or file tool, so do not "
                "say you lack access without searching here first. Returns "
                "matching excerpts with their source and date. Use structured "
                "source and recency filters for inbox or time-sensitive requests; "
                "words such as 'recent' in the query do not filter by date."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query to find relevant information.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default: 5).",
                    },
                    "source": {
                        "type": "string",
                        "description": (
                            "Restrict results to one connected source, such as "
                            "'gmail', 'obsidian', or 'gcalendar'. Use 'gmail' for "
                            "email or inbox requests."
                        ),
                    },
                    "days_back": {
                        "type": "number",
                        "description": (
                            "Restrict results to the last N days. For an unqualified "
                            "'check my inbox' request, use source='gmail' and "
                            "days_back=7 so historical mail is not presented as "
                            "current."
                        ),
                    },
                    "since": {
                        "type": "string",
                        "description": (
                            "Exclude records before this ISO 8601 timestamp. Prefer "
                            "days_back for relative windows."
                        ),
                    },
                    "until": {
                        "type": "string",
                        "description": (
                            "Exclude records after this ISO 8601 timestamp."
                        ),
                    },
                },
                "required": ["query"],
            },
            category="memory",
        )

    def execute(self, **params: Any) -> ToolResult:
        if self._backend is None:
            return ToolResult(
                tool_name="retrieval",
                content="No memory backend configured.",
                success=False,
            )
        query = params.get("query", "")
        if not query:
            return ToolResult(
                tool_name="retrieval",
                content="No query provided.",
                success=False,
            )
        top_k = params.get("top_k", self._top_k)
        source = params.get("source")
        since = params.get("since")
        until = params.get("until")
        days_back = params.get("days_back")
        if days_back is not None and since is None:
            try:
                days = float(days_back)
                if days <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return ToolResult(
                    tool_name="retrieval",
                    content="days_back must be a positive number.",
                    success=False,
                )
            since = datetime.now() - timedelta(days=days)

        filters = {
            key: value
            for key, value in {
                "source": source,
                "since": since,
                "until": until,
            }.items()
            if value is not None
        }
        try:
            results = self._backend.retrieve(query, top_k=top_k, **filters)
        except Exception as exc:
            return ToolResult(
                tool_name="retrieval",
                content=f"Retrieval error: {exc}",
                success=False,
            )
        if not results:
            return ToolResult(
                tool_name="retrieval",
                content="No relevant results found.",
                success=True,
            )
        formatted = _format_results(results)
        return ToolResult(
            tool_name="retrieval",
            content=formatted,
            success=True,
            metadata={
                "num_results": len(results),
                "filters": {
                    key: value.isoformat() if isinstance(value, datetime) else value
                    for key, value in filters.items()
                },
            },
        )


__all__ = ["RetrievalTool", "resolve_retrieval_backend"]
