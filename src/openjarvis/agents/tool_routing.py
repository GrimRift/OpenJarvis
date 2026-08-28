"""Send only the tool schemas a request could plausibly need.

Every turn carries the full tool payload, and it is the dominant part of the
input context: measured on the live 23-tool configuration, schemas are 3,791
tokens against 1,540 for the entire system prompt. A multi-turn tool call
re-sends that on each turn, which is why an inbox question cost 18,092 input
tokens and even a tool-free "who are you" cost 8,660.

The rules here are deliberately biased toward *including* a tool. Hiding one
the model needed is a capability regression -- it produces "I can't do that"
for something Sage can plainly do -- while including a spare one costs only
tokens. Three properties enforce that bias:

1. Core tools are always sent. Anything broadly useful or hard to predict from
   wording lives here, not in a gated group.
2. A tool in no group is always sent. Adding a tool therefore cannot silently
   hide it; it has to be put in a group deliberately.
3. Groups match against the recent conversation, not just the newest message,
   so "open spotify" followed by "next one" keeps the media group.

Order is preserved so the serialized payload stays byte-stable across the
turns of one request, which is what a provider's prompt cache keys on.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Sequence, Set

# Always sent: broadly useful, or needed for requests whose wording gives no
# reliable signal. Keeping retrieval here is deliberate -- "check my inbox",
# "what did I say about X" and "when is my flight" all resolve through it.
_CORE: Set[str] = {
    "retrieval",
    "web_search",
    "world_time",
    "calculator",
    "notify_windows",
}

# Gated groups. Patterns are generous on purpose; a false positive costs
# tokens, a false negative costs a capability.
_GROUPS: Dict[str, Dict[str, Any]] = {
    "coding": {
        "tools": {
            "file_read",
            "file_write",
            "directory_list",
            "apply_patch",
            "coding_command",
            "git_status",
            "git_diff",
            "git_log",
            "git_commit",
        },
        "pattern": re.compile(
            r"\b(file|files|folder|directory|dir|path|code|coding|repo|"
            r"repository|git|commit|commits|diff|branch|patch|script|"
            r"scripts|test|tests|pytest|python|npm|node|npx|function|"
            r"refactor|bug|lint|build|read|write|edit|create|delete|"
            r"rename|move|copy|search|grep|log|logs|source|project)\b"
            r"|[\\/]|\.\w{1,4}\b",
            re.IGNORECASE,
        ),
    },
    "scheduling": {
        "tools": {
            "schedule_task",
            "list_scheduled_tasks",
            "pause_scheduled_task",
            "resume_scheduled_task",
            "cancel_scheduled_task",
        },
        "pattern": re.compile(
            r"\b(schedule|scheduled|scheduling|remind|reminder|reminders|"
            r"task|tasks|cron|recurring|repeat|repeating|every|daily|"
            r"weekly|hourly|nightly|morning|evening|tomorrow|later|"
            r"pause|resume|cancel|upcoming|routine|automation|automate)\b"
            r"|\b\d{1,2}\s*(am|pm)\b|\b\d{1,2}:\d{2}\b",
            re.IGNORECASE,
        ),
    },
    "class_schedule": {
        "tools": {"check_class_schedule", "notify_class_schedule"},
        "pattern": re.compile(
            r"\b(class|classes|schedule|subject|subjects|lecture|lectures|"
            r"course|courses|room|professor|instructor|semester|campus|"
            r"school|university|today|tomorrow|next)\b",
            re.IGNORECASE,
        ),
    },
    "media": {
        "tools": {"spotify_control", "open_app"},
        "pattern": re.compile(
            r"\b(spotify|music|song|songs|track|tracks|album|artist|"
            r"playlist|play|playing|pause|resume|skip|next|previous|"
            r"volume|listen|listening|open|launch|start|run|app|apps|"
            r"application|notepad|obsidian|browser|window)\b",
            re.IGNORECASE,
        ),
    },
}

# How many recent messages of context feed the match. Enough to survive a
# short follow-up ("do that", "next one") without dragging in stale topics.
_CONTEXT_MESSAGES = 6


def _grouped_tools() -> Set[str]:
    names: Set[str] = set()
    for group in _GROUPS.values():
        names |= group["tools"]
    return names


def selected_tool_names(text: str) -> Set[str]:
    """Tool names to send for *text*, excluding ungrouped and core tools."""
    chosen: Set[str] = set(_CORE)
    for group in _GROUPS.values():
        if group["pattern"].search(text):
            chosen |= group["tools"]
    return chosen


def route_tools(
    openai_tools: Sequence[Dict[str, Any]],
    text: str,
) -> List[Dict[str, Any]]:
    """Filter OpenAI-format tool schemas down to those *text* may need.

    Unrecognised tools are kept, so this can never hide a capability that was
    not explicitly assigned to a group. Input order is preserved.
    """
    if not openai_tools:
        return list(openai_tools)

    grouped = _grouped_tools()
    allowed = selected_tool_names(text)

    kept: List[Dict[str, Any]] = []
    for schema in openai_tools:
        name = (schema.get("function") or {}).get("name", "")
        if name not in grouped or name in allowed:
            kept.append(schema)
    return kept


def _role_of(item: Any) -> str:
    role = getattr(item, "role", None)
    if role is None and isinstance(item, dict):
        role = item.get("role")
    return str(getattr(role, "value", role) or "").lower()


def _content_of(item: Any) -> str:
    content = getattr(item, "content", None)
    if content is None and isinstance(item, dict):
        content = item.get("content")
    return content if isinstance(content, str) else ""


def routing_text(message: str, prior: Iterable[Any] = ()) -> str:
    """Join the newest message with recent *user* turns for matching.

    A follow-up like "next one" carries no signal by itself; the turn that set
    the topic does.

    Only user turns count. The server injects retrieved memory as a system
    message before dispatch, and a blob pulled from a 64k-chunk corpus matches
    essentially every group -- which silently selected the whole toolset on
    every request and made routing a no-op. Assistant turns are excluded for
    the same reason: what Sage said is not what the user asked for.
    """
    user_turns = [
        _content_of(item)
        for item in prior
        if _role_of(item) == "user" and _content_of(item)
    ]
    parts = user_turns[-_CONTEXT_MESSAGES:]
    parts.append(message or "")
    return "\n".join(parts)


__all__ = ["route_tools", "routing_text", "selected_tool_names"]
