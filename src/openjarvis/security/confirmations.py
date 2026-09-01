"""Two-turn confirmation for tools that do something consequential.

``ToolSpec.requires_confirmation`` used to mean "fail". Nothing in the web
chat path supplied a ``confirm_callback`` --- ``server/routes.py`` never set
one, and the agent-manager routes hardcode ``lambda _prompt: True`` --- so
``ToolExecutor`` rejected the call outright with "requires confirmation but no
confirmation callback is available". The flag disabled a tool instead of
guarding it, which is why ``git_commit`` sat in the configured tool list and
could never run from chat.

The gate here is the **user turn**, not the model. A tool asking for
confirmation records a fingerprint of the exact call and the turn that asked.
It can only be redeemed on a *different* turn, and only when that turn's user
message is affirmative. The model cannot manufacture a new user turn, so it
cannot approve its own tool call --- including when it is repeating
instructions it read in a web page, a document, or (later) on screen.

The fingerprint covers the tool name *and* its arguments, so answering "yes" to
one request never authorises a different call that happens to arrive in the
same turn.

Held in a ``ContextVar`` rather than on the executor: the executor is shared
across concurrent requests, and per-request state on a shared agent is exactly
the race that ``_get_agent_model_lock`` had to be introduced to fix.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Optional

#: A pending request expires rather than waiting forever for an answer.
PENDING_TTL_SECONDS = 300.0

# A whole-message vocabulary check rather than a phrase list.
#
# The first version matched a fixed set of phrases and was too strict in real
# use: "yep sure thing" and "ok go ahead please" both read as plain agreement
# and were rejected, costing an extra exchange every time. Listing more phrases
# does not scale — people pad agreement with politeness in endless
# combinations.
#
# So: every word must come from a vocabulary of agreement and politeness, and
# at least one must be actual agreement. Any word carrying new content fails,
# which is what keeps this safe. "yes, but make it 9pm instead" fails on
# "but", "9pm" and "instead"; "yes, and delete the old one" fails on "delete"
# and "old". A near-miss costs one exchange; a false positive runs something
# the user did not sanction.
_AGREEMENT_WORDS = frozenset(
    """affirmative absolutely agreed alright approve approved aye certainly
    confirm confirmed confirming correct definitely do fine go good granted
    indeed ok okay okey proceed right roger sure yea yeah yep yes yup""".split()
)

# Padding that adds no meaning: allowed alongside agreement, never on its own.
_FILLER_WORDS = frozenset(
    """ahead all cheers course for great head is it its lets let me my now of
    please set sir sounds thanks thank that the them these this thing things
    to too us we with you your""".split()
)

_ALLOWED_WORDS = _AGREEMENT_WORDS | _FILLER_WORDS
_WORD_RE = re.compile(r"[a-z']+")


@dataclass(frozen=True)
class TurnContext:
    """Identifies one user turn and whether it said yes."""

    turn_key: str
    affirmative: bool


_current_turn: ContextVar[Optional[TurnContext]] = ContextVar(
    "openjarvis_confirmation_turn", default=None
)

# fingerprint -> (turn that asked, when it asked)
_pending: dict[str, tuple[str, float]] = {}
_pending_lock = threading.Lock()


def fingerprint(tool_name: str, arguments: Any) -> str:
    """Stable id for one exact tool call, arguments included.

    Canonical JSON rather than the human prompt string: the model re-sends the
    same call as fresh JSON, so key order is not stable between turns. Hashing
    the rendered prompt made an identical request look like a different one and
    the user was asked to confirm twice. Sorting keys fixes that while keeping
    a genuinely different argument a genuinely different request.
    """
    try:
        canonical = json.dumps(
            {"tool": tool_name, "args": arguments},
            sort_keys=True,
            default=str,
        )
    except (TypeError, ValueError):
        canonical = f"{tool_name}:{arguments!r}"
    return hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()


def turn_key(messages: Any) -> str:
    """Identify a turn by the whole exchange that produced it.

    Every reply appends to the transcript, so a later request necessarily
    hashes differently. That difference *is* the proof that a real user turn
    happened in between, which is the only thing standing between the model and
    self-approval.
    """
    parts = []
    for message in messages or []:
        role = getattr(message, "role", None)
        content = getattr(message, "content", None)
        if role is None and isinstance(message, dict):
            role = message.get("role")
            content = message.get("content")
        parts.append(f"{role}:{content}")
    return hashlib.sha256("\n".join(parts).encode("utf-8", "replace")).hexdigest()


def is_affirmative(text: str) -> bool:
    """Whether *text* is plain agreement and nothing else.

    Digits fail on sight: a number is never padding, and "yes, 9pm" is a
    correction wearing agreement's clothes.
    """
    lowered = (text or "").strip().lower()
    if not lowered or any(character.isdigit() for character in lowered):
        return False
    words = _WORD_RE.findall(lowered)
    if not words or len(words) > 8:
        return False
    if not all(word in _ALLOWED_WORDS for word in words):
        return False
    return any(word in _AGREEMENT_WORDS for word in words)


def last_user_text(messages: Any) -> str:
    for message in reversed(list(messages or [])):
        role = getattr(message, "role", None)
        content = getattr(message, "content", None)
        if role is None and isinstance(message, dict):
            role = message.get("role")
            content = message.get("content")
        if role == "user":
            return content if isinstance(content, str) else ""
    return ""


def set_turn(messages: Any):
    """Bind the current turn for the duration of a request. Returns a reset token."""
    context = TurnContext(
        turn_key=turn_key(messages),
        affirmative=is_affirmative(last_user_text(messages)),
    )
    return _current_turn.set(context)


def reset_turn(token) -> None:
    try:
        _current_turn.reset(token)
    except (ValueError, LookupError):
        # Reset from a different context than the one that set it; the
        # ContextVar falls out of scope on its own.
        pass


def _purge_expired(now: float) -> None:
    for key, (_, issued_at) in list(_pending.items()):
        if now - issued_at > PENDING_TTL_SECONDS:
            _pending.pop(key, None)


def decide(tool_name: str, arguments: Any) -> bool:
    """Whether this exact call is approved *right now*.

    False is not a denial --- it means "not yet". The caller turns it into a
    request the user can answer, and the answer arrives on the next turn.
    """
    context = _current_turn.get()
    if context is None:
        # No turn bound (a background job, a scheduled task). Fail closed:
        # nobody is present to answer.
        return False

    key = fingerprint(tool_name, arguments)
    now = time.monotonic()
    with _pending_lock:
        _purge_expired(now)
        asked_on = _pending.get(key)
        if (
            context.affirmative
            and asked_on is not None
            and asked_on[0] != context.turn_key
        ):
            del _pending[key]
            return True
        _pending[key] = (context.turn_key, now)
    return False


def clear() -> None:
    """Drop every pending request. For tests."""
    with _pending_lock:
        _pending.clear()


__all__ = [
    "PENDING_TTL_SECONDS",
    "TurnContext",
    "clear",
    "decide",
    "fingerprint",
    "is_affirmative",
    "last_user_text",
    "reset_turn",
    "set_turn",
    "turn_key",
]
