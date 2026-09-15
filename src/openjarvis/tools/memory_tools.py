"""Memory the user can talk to (M38 phase 3).

``remember`` stores what the user said, verbatim, as a pinned curated fact:
the user decided it was worth keeping, so the model does not judge that.
``forget`` removes every fact matching a phrase at once -- the user's
choice -- and says what went, since each removal is restorable for a week
by ``restore_memory``. ``recall`` searches facts, episodes and documents on
demand, with provenance, for the questions the turn's own injection did
not cover.
"""

from __future__ import annotations

import time
from typing import Any, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


def _store() -> Any:
    from openjarvis.core.config import load_config
    from openjarvis.memory.store import create_fact_store

    mem = load_config().memory
    return create_fact_store(
        getattr(mem, "backend", "local") or "local",
        path=getattr(mem, "facts_path", None),
        max_facts=getattr(mem, "max_facts", 1000) or 1000,
    )


def _when(fact: Any) -> str:
    if fact.day:
        return fact.day
    if fact.created_at:
        return time.strftime("%Y-%m-%d", time.localtime(fact.created_at))
    return "unknown date"


@ToolRegistry.register("remember")
class RememberTool(BaseTool):
    """Keep something the user asked to be remembered."""

    tool_id = "remember"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="remember",
            description=(
                "The user asked you to remember something: 'remember that my "
                "adviser is Dr. Cruz', 'note that I prefer metric'. Store it "
                "as they said it, as a durable fact that is always in your "
                "context. Only for things the user explicitly asked you to "
                "keep; ordinary conversation is remembered automatically."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "The fact, one sentence, in the user's terms.",
                    },
                    "private": {
                        "type": "boolean",
                        "description": (
                            "True if the user said it is private: never used when "
                            "Sage speaks first."
                        ),
                    },
                },
                "required": ["fact"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        text = str(params.get("fact") or "").strip()
        if not text:
            return ToolResult(
                tool_name=self.tool_id, content="Nothing to remember.", success=False
            )
        from openjarvis.memory.store import TRUST_TRUSTED

        added = _store().add(
            text,
            source="you",
            trust=TRUST_TRUSTED,
            pinned=True,
            private=bool(params.get("private", False)),
        )
        return ToolResult(
            tool_name=self.tool_id,
            content=(
                f"Remembered: {text}" if added else f"I already remember that: {text}"
            ),
            success=True,
            metadata={"added": added},
        )


@ToolRegistry.register("forget")
class ForgetTool(BaseTool):
    """Remove what matches a phrase, at once, restorably."""

    tool_id = "forget"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="forget",
            description=(
                "The user asked you to forget something: 'forget what I said "
                "about the k-drama', 'forget my old address'. Removes every "
                "remembered fact matching the phrase and reports each one so "
                "the user can ask to restore any of them (restore_memory). "
                "Removal is immediate; do not ask for confirmation first."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "about": {
                        "type": "string",
                        "description": "The subject to forget, in a few words.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "At most this many facts (default 5).",
                    },
                },
                "required": ["about"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        about = str(params.get("about") or "").strip()
        if not about:
            return ToolResult(
                tool_name=self.tool_id, content="Forget what?", success=False
            )
        from openjarvis.memory.recall import score_facts

        store = _store()
        facts = store.list()
        scores = score_facts(facts, about)
        limit = max(1, min(int(params.get("limit") or 5), 20))
        matched = [
            f
            for f, s in sorted(zip(facts, scores), key=lambda p: p[1], reverse=True)
            if s > 0
        ][:limit]
        if not matched:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"I have nothing remembered about {about!r}.",
                success=True,
                metadata={"removed": []},
            )
        removed = []
        for fact in matched:
            if store.remove(fact.id, f"forgotten on request: {about}"):
                removed.append({"id": fact.id, "text": fact.text})
        lines = "\n".join(f"- {r['text']} (id {r['id']})" for r in removed)
        return ToolResult(
            tool_name=self.tool_id,
            content=(
                f"Forgotten {len(removed)} fact(s) about {about!r}:\n{lines}\n"
                "Any of these can be restored within seven days."
            ),
            success=True,
            metadata={"removed": removed},
        )


@ToolRegistry.register("restore_memory")
class RestoreMemoryTool(BaseTool):
    """Bring back something forgotten or cleaned up."""

    tool_id = "restore_memory"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="restore_memory",
            description=(
                "Restore a remembered fact that was forgotten or removed by "
                "the nightly clean-up, by its id from `forget`, or the most "
                "recently removed ones when no id is given ('restore what you "
                "just forgot')."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "fact_id": {
                        "type": "string",
                        "description": "A fact id, if known.",
                    },
                    "last": {
                        "type": "integer",
                        "description": (
                            "Restore this many most recently removed (default 1)."
                        ),
                    },
                },
                "required": [],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        store = _store()
        fact_id = str(params.get("fact_id") or "").strip()
        if fact_id:
            ok = store.restore(fact_id)
            return ToolResult(
                tool_name=self.tool_id,
                content="Restored." if ok else f"Nothing removed has id {fact_id}.",
                success=ok,
            )
        last = max(1, min(int(params.get("last") or 1), 20))
        removed = sorted(
            store.list_removed(), key=lambda f: f.removed_at or 0, reverse=True
        )
        restored: List[str] = []
        for fact in removed[:last]:
            if store.restore(fact.id):
                restored.append(fact.text)
        if not restored:
            return ToolResult(
                tool_name=self.tool_id, content="Nothing to restore.", success=True
            )
        return ToolResult(
            tool_name=self.tool_id,
            content="Restored:\n" + "\n".join(f"- {t}" for t in restored),
            success=True,
            metadata={"restored": restored},
        )


@ToolRegistry.register("recall")
class RecallTool(BaseTool):
    """Search everything Sage remembers, with provenance."""

    tool_id = "recall"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="recall",
            description=(
                "Search your long-term memory on demand: remembered facts, the "
                "daily diary (episodes) and indexed documents. Use it when the "
                "user asks what you know or remember about something, when "
                "they ask why you think something, or when a question needs "
                "more than what is already in context. Each hit says where it "
                "came from and when."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look for."},
                    "limit": {
                        "type": "integer",
                        "description": "Facts to return at most (default 8).",
                    },
                },
                "required": ["query"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        query = str(params.get("query") or "").strip()
        if not query:
            return ToolResult(
                tool_name=self.tool_id, content="Recall what?", success=False
            )
        limit = max(1, min(int(params.get("limit") or 8), 30))
        from openjarvis.memory.recall import score_facts, tokens

        store = _store()
        facts = store.list()
        scores = score_facts(facts, query)
        hits = [
            f
            for f, s in sorted(
                zip(facts, scores), key=lambda p: (p[1], p[0].created_at), reverse=True
            )
            if s > 0
        ][:limit]
        sections: List[str] = []
        if hits:
            sections.append(
                "Remembered facts:\n"
                + "\n".join(
                    f"- {f.text} (from "
                    f"{'you' if f.source == 'you' else 'a conversation'}"
                    f" on {_when(f)}{', pinned' if f.pinned else ''})"
                    for f in hits
                )
            )
        try:
            from openjarvis.memory.episodes import load_episodes

            terms = set(tokens(query))
            days = [
                e
                for e in sorted(
                    load_episodes().values(), key=lambda e: e.day, reverse=True
                )
                if terms & set(tokens(e.summary))
            ][:3]
            if days:
                sections.append(
                    "Diary:\n"
                    + "\n".join(f"- {e.day}: {e.summary[:300]}" for e in days)
                )
        except Exception:
            pass
        try:
            backend: Optional[Any] = None
            from openjarvis.tools.storage.sqlite import SQLiteMemory

            backend = SQLiteMemory()
            docs = backend.retrieve(query, top_k=3)
            docs = [d for d in docs if getattr(d, "score", 0) > 0]
            if docs:
                sections.append(
                    "Documents:\n"
                    + "\n".join(
                        f"- {getattr(d, 'source', '') or 'pasted text'}: "
                        f"{d.content[:200].replace(chr(10), ' ')}"
                        for d in docs
                    )
                )
        except Exception:
            pass
        if not sections:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Nothing remembered about {query!r}.",
                success=True,
            )
        return ToolResult(
            tool_name=self.tool_id, content="\n\n".join(sections), success=True
        )


__all__ = ["ForgetTool", "RecallTool", "RememberTool", "RestoreMemoryTool"]
