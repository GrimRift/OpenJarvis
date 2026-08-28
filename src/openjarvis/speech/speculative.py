"""Speculative generation for Flux EagerEndOfTurn — buffered, never released early.

``EagerEndOfTurn`` means Deepgram is *fairly* sure the user stopped talking.
It is a guess, and ``TurnResumed`` retracts it. Anything done on that guess
must therefore be invisible and reversible: no tools, no TTS, no messages, no
text on screen, nothing the user could mistake for an answer.

The tool boundary here is structural, not prompted. Speculative work runs
through :func:`generate_speculative`, which calls the engine directly with no
executor, no tool specs and no agent in the call path — so there is nothing
to invoke even if the model asks. A prompt instruction or a
``requires_confirmation`` flag would be a request; this is an absence.

Release is gated three ways: the turn index must match, the generation must
not have been cancelled, and the confirmed transcript must be what was
actually spoken to. Anything that smells tool-shaped is discarded outright
and re-run through the real agent after confirmation.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Speculation is only ever worth it for a plain question the model can answer
# from context. Anything that might *do* something is discarded and re-run
# properly, so this list is deliberately broad and biased toward discarding:
# a wasted speculation costs latency, a wrongly-released one costs an action.
_ACTION_HINTS = (
    "play", "pause", "skip", "resume", "stop", "open", "launch", "start",
    "send", "email", "message", "text", "reply", "schedule", "remind",
    "cancel", "delete", "remove", "archive", "create", "add", "write",
    "commit", "push", "run", "execute", "install", "update", "change",
    "set ", "turn on", "turn off", "notify", "search", "look up", "find",
    "book", "buy", "order", "move", "rename", "download", "upload",
)

# Inflections count: "scheduled"/"sending"/"plays" are the same intent as the
# stem, and missing one would let an action request through as speculatable.
_ACTION_STEMS = "|".join(re.escape(h.strip()) for h in _ACTION_HINTS)
_ACTION_RE = re.compile(
    rf"\b({_ACTION_STEMS})(s|d|es|ed|ing)?\b",
    re.IGNORECASE,
)


def looks_tool_capable(transcript: str) -> bool:
    """Whether *transcript* might want an action rather than an answer.

    Errs toward ``True``. A false positive only forfeits the latency win; a
    false negative would release an answer about work that never happened.
    """
    text = (transcript or "").strip()
    if not text:
        return True
    return bool(_ACTION_RE.search(text))


def _normalise(text: str) -> str:
    """Compare transcripts ignoring case, padding and trailing punctuation.

    Flux commonly finalises an eager transcript by adding a full stop or
    fixing capitalisation; that is the same utterance, not a different one.
    """
    return re.sub(r"[^a-z0-9 ]+", "", (text or "").lower()).strip()


@dataclass
class Speculation:
    """One speculative generation, tied to exactly one turn."""

    turn_index: int
    generation_id: str
    transcript: str
    text: str = ""
    cancelled: bool = False
    released: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def matches(self, turn_index: int, final_transcript: str) -> bool:
        return (
            not self.cancelled
            and not self.released
            and self.turn_index == turn_index
            and _normalise(self.transcript) == _normalise(final_transcript)
        )


class SpeculativeManager:
    """Tracks at most one live speculation and gates its release.

    Holding a single slot is the point: a second ``EagerEndOfTurn`` for the
    same turn supersedes the first, and exactly one answer may ever be
    released per confirmed turn.
    """

    def __init__(self) -> None:
        self._current: Optional[Speculation] = None
        self._released_turns: set[int] = set()

    @property
    def current(self) -> Optional[Speculation]:
        return self._current

    def begin(self, turn_index: int, transcript: str) -> Optional[Speculation]:
        """Open a speculation, unless the utterance looks tool-shaped."""
        if looks_tool_capable(transcript):
            logger.debug(
                "Not speculating on a possibly tool-shaped turn %s", turn_index
            )
            self._current = None
            return None
        if turn_index in self._released_turns:
            return None
        self._current = Speculation(
            turn_index=turn_index,
            generation_id=uuid.uuid4().hex,
            transcript=transcript,
        )
        return self._current

    def is_current(self, generation_id: str) -> bool:
        """Whether *generation_id* is still the live speculation.

        Callbacks from a cancelled or superseded generation must be dropped,
        so every write checks this first.
        """
        cur = self._current
        return cur is not None and not cur.cancelled and (
            cur.generation_id == generation_id
        )

    def append(self, generation_id: str, chunk: str) -> None:
        """Buffer output. Stale generations are silently ignored."""
        if not self.is_current(generation_id):
            return
        assert self._current is not None
        self._current.text += chunk

    def cancel(self, reason: str = "") -> Optional[str]:
        """Discard the live speculation. Called on ``TurnResumed``.

        Returns the cancelled generation id so an in-flight request can be
        aborted by the caller.
        """
        cur = self._current
        if cur is None:
            return None
        cur.cancelled = True
        self._current = None
        logger.debug(
            "Cancelled speculation %s for turn %s (%s)",
            cur.generation_id,
            cur.turn_index,
            reason or "TurnResumed",
        )
        return cur.generation_id

    def release(self, turn_index: int, final_transcript: str) -> Optional[str]:
        """Return buffered text only if it belongs to this confirmed turn.

        Returns ``None`` whenever the caller must fall back to the normal
        agent path: no speculation, a cancelled one, a different turn, a
        changed transcript, or a turn already answered.
        """
        if turn_index in self._released_turns:
            logger.warning("Turn %s already released; refusing a second", turn_index)
            return None
        cur = self._current
        if cur is None or not cur.matches(turn_index, final_transcript):
            self._current = None
            return None
        if looks_tool_capable(final_transcript):
            # The confirmed transcript can differ from what was speculated on.
            self._current = None
            return None
        cur.released = True
        self._released_turns.add(turn_index)
        self._current = None
        return cur.text or None

    def reset(self) -> None:
        """Drop all state — used when a session ends or the socket drops."""
        self.cancel("session reset")
        self._released_turns.clear()


def generate_speculative(
    engine: Any,
    *,
    model: str,
    transcript: str,
    prior_messages: Optional[Sequence[Any]] = None,
    temperature: float = 0.3,
    max_tokens: int = 512,
) -> Dict[str, Any]:
    """Answer *transcript* with no tool machinery anywhere in the call path.

    Calls the engine directly: no ``ToolExecutor``, no agent, and no ``tools``
    argument, so there is no mechanism to invoke one. That absence is the
    safety boundary — not an instruction the model could ignore.
    """
    from openjarvis.core.types import Message, Role

    messages: List[Any] = []
    if prior_messages:
        messages.extend(prior_messages)
    messages.append(Message(role=Role.USER, content=transcript))

    return engine.generate(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )


__all__ = [
    "Speculation",
    "SpeculativeManager",
    "generate_speculative",
    "looks_tool_capable",
]
