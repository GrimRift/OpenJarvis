"""LLM-backed extraction of durable facts from a conversation turn.

The extractor takes a single (user, assistant) exchange and asks a small
local model to distill any long-term, user-specific facts worth remembering.
It is deliberately defensive: extraction runs on a background thread far from
the request path, so *any* failure — a dropped Ollama connection, a timeout, a
``BrokenPipeError`` when the client went away, or simply unparseable output —
must degrade to "no facts" rather than propagate.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Optional

from openjarvis.core.types import Message, Role

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "You extract durable, long-term facts about the user from a single "
    "conversation exchange. A good fact is stable over time and useful in "
    "future conversations: preferences, identity, goals, ongoing projects, "
    "constraints, or relationships. Ignore one-off task details, small talk, "
    "and anything the assistant said about itself.\n\n"
    # These facts are re-injected verbatim into every future prompt, so a
    # sentence that was only true when written keeps asserting itself long
    # afterwards. Observed live: "User has a class starting at 11:00 AM
    # today", captured on a Friday, had the assistant announcing a class on
    # the following Sunday — and outweighing the schedule tool, which was
    # correctly reporting none.
    "Never record anything whose truth depends on when it was said. Drop "
    "facts containing today, tomorrow, tonight, this morning, currently, "
    "right now, upcoming, or next — a recurring commitment is only worth "
    "keeping if you state the actual day and time it recurs, and a one-off "
    "event is not worth keeping at all.\n\n"
    "Respond with ONLY a JSON array of short fact strings (each under 200 "
    "characters). If there is nothing worth remembering, respond with []."
)


class FactExtractor:
    """Extract memory-worthy facts from a conversation turn via an engine."""

    def __init__(
        self,
        engine: Any,
        model: str,
        *,
        temperature: float = 0.0,
        # Room for a reasoning model to think before the JSON: 512 was
        # enough for the local model and for a short exchange in the cloud,
        # but a long one would have come back empty and been read as "no
        # facts" -- the same failure the initiative writer had.
        max_tokens: int = 2000,
        max_facts_per_turn: int = 10,
        max_fact_chars: int = 200,
        system_prompt: Optional[str] = None,
    ) -> None:
        self._engine = engine
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._max_facts_per_turn = max_facts_per_turn
        self._max_fact_chars = max_fact_chars
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT

    def extract(
        self,
        user_text: str,
        assistant_text: str = "",
        answered_by: str = "",
    ) -> List[str]:
        """Return durable facts from the exchange. Never raises.

        *answered_by* is the model that produced the turn. When it is a cloud
        model, extraction follows it there, because the exchange has already
        reached that provider and a local extraction model would otherwise
        load several GB onto the GPU the user is watching video on.
        """
        user_text = (user_text or "").strip()
        if not user_text:
            return []

        exchange = f"User: {user_text}"
        if assistant_text and assistant_text.strip():
            exchange += f"\nAssistant: {assistant_text.strip()}"

        messages = [
            Message(role=Role.SYSTEM, content=self._system_prompt),
            Message(role=Role.USER, content=exchange),
        ]

        model = self._resolve_model(answered_by)

        try:
            result = self._engine.generate(
                messages,
                model=model,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                # Unload the moment this returns. Extraction is a one-shot
                # background call, but the model it loads is charged to the
                # GPU the user is also watching video on: measured 2.9 GB in
                # use before a message and 7.2 GB of 8.1 GB after, held for
                # five minutes. Non-Ollama engines strip the argument rather
                # than ignoring it, so a cloud extraction model is safe too.
                keep_alive=0,
            )
        except BrokenPipeError:
            # The classic failure mode: the model call's transport died.
            # Extraction is best-effort, so swallow it.
            logger.debug("Memory extraction aborted: broken pipe", exc_info=True)
            return []
        except Exception:  # noqa: BLE001 — extraction must never crash the worker
            logger.debug("Memory extraction failed", exc_info=True)
            return []

        if isinstance(result, dict):
            content = result.get("content", "") or ""
        else:
            content = str(result)

        return self._parse(content)

    @property
    def engine(self) -> Any:
        return self._engine

    def tagging_model(self) -> str:
        """The model that tags fact topics: the one the settings pick for
        extraction, as if no turn had answered."""
        return self._resolve_model("")

    def _resolve_model(self, answered_by: str) -> str:
        """Pick the extraction model for a turn answered by *answered_by*.

        The user's memory settings decide first (M38): "cloud" always uses
        the cloud model -- the local 4b one is where the stale and duplicate
        facts came from -- and "local" always uses the configured
        ``extraction_model``. Only if the settings cannot be read does the
        older rule apply: follow the model that answered, so a cloud turn
        never drags the GPU awake for extraction.
        """
        try:
            from openjarvis.memory.settings import load_memory_settings

            settings = load_memory_settings()
            if settings.extraction_mode == "cloud" and settings.cloud_model:
                return settings.cloud_model
            if settings.extraction_mode == "local":
                return self._model
        except Exception:  # noqa: BLE001 — settings must never lose a fact
            logger.debug("Memory settings unavailable", exc_info=True)
        if not answered_by:
            return self._model
        try:
            from openjarvis.engine.cloud import is_cloud_model
        except Exception:  # noqa: BLE001 — never let a missing import lose a fact
            logger.debug("Cloud model check unavailable", exc_info=True)
            return self._model
        return answered_by if is_cloud_model(answered_by) else self._model

    # -- parsing ------------------------------------------------------------

    def _parse(self, content: str) -> List[str]:
        """Parse model output into a clean, deduped, capped list of facts."""
        if not content or not content.strip():
            return []

        raw = self._coerce_to_list(content)

        facts: List[str] = []
        seen: set[str] = set()
        for item in raw:
            fact = self._clean_fact(item)
            if not fact:
                continue
            key = fact.lower()
            if key in seen:
                continue
            seen.add(key)
            facts.append(fact)
            if len(facts) >= self._max_facts_per_turn:
                break
        return facts

    def _coerce_to_list(self, content: str) -> List[str]:
        """Best-effort conversion of model output to a list of strings."""
        # 1. Every JSON array anywhere in the output (models wrap them in
        #    prose or code fences, or answer with more than one). Decoding
        #    each one in place, rather than one greedy "[ ... ]" match, keeps
        #    a stray "[]" after the array from failing the whole parse -- that
        #    sent the JSON line to the line parser verbatim (9 Oct:
        #    '["Prefers open-fit earbuds."]' was saved as a fact).
        # A bulleted answer goes to the line parser even if a bullet holds
        # an array; that parser decodes JSON per line.
        found = None if _BULLET.search(content) else _json_lists(content)
        if found is not None:
            return found

        # 2. Fall back to line-based parsing (markdown bullets / numbered).
        #    Deliberately permissive: small local models routinely answer with
        #    one bare fact per line, and rejecting those would quietly stop
        #    memory capture. Injected text can trivially add list markers, so
        #    filtering here buys no defence — provenance tagging and the
        #    injection scanner in MemoryService are the real gate.
        items: List[str] = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            line = _BULLET.sub("", line)
            if line[:1] in ('[', '"'):
                try:
                    items.extend(_strings_in(json.loads(line)))
                    continue
                except (json.JSONDecodeError, ValueError):
                    pass
            items.append(line)
        return items

    def _clean_fact(self, item: str) -> str:
        fact = str(item).strip().strip("\"'").strip()
        # Drop obvious non-facts the model sometimes emits.
        if not fact or fact.lower() in ("[]", "none", "n/a", "null"):
            return ""
        # JSON that could not be decoded is never a fact.
        if fact[:1] in ("[", "{"):
            return ""
        if len(fact) > self._max_fact_chars:
            fact = fact[: self._max_fact_chars].rstrip()
        return fact


_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*", re.MULTILINE)
_FACT_KEYS = ("fact", "text", "content")


def _strings_in(value: Any) -> List[str]:
    """The fact strings in a decoded JSON value: nested lists flattened,
    ``{"fact": ...}`` objects unwrapped, anything else dropped."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for item in value for s in _strings_in(item)]
    if isinstance(value, dict):
        for key in _FACT_KEYS:
            if isinstance(value.get(key), str):
                return [value[key]]
    return []


def _json_lists(content: str) -> Optional[List[str]]:
    """Strings from every JSON array in *content*, or None if there is none.

    An array of only numbers (e.g. "[1]" in prose) does not count, so a
    bulleted answer that mentions one still reaches the line parser.
    """
    decoder = json.JSONDecoder()
    items: List[str] = []
    found = False
    pos = content.find("[")
    while pos != -1:
        try:
            value, end = decoder.raw_decode(content, pos)
        except (json.JSONDecodeError, ValueError):
            pos = content.find("[", pos + 1)
            continue
        if isinstance(value, list):
            strings = _strings_in(value)
            if strings or not value:
                found = True
                items.extend(strings)
        pos = content.find("[", end)
    return items if found else None


__all__ = ["FactExtractor"]
