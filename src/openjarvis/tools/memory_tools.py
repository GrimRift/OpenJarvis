"""Memory the user can talk to (M38 phase 3).

``remember`` stores what the user said, verbatim, as a pinned curated fact:
the user decided it was worth keeping, so the model does not judge that.
``forget`` removes the facts that clearly match a phrase -- asking first
when that is more than two, or everything -- and says what went, since
each removal is restorable for a week by ``restore_memory``. ``recall``
searches facts, episodes and documents on demand, with provenance, for
the questions the turn's own injection did not cover.
"""

from __future__ import annotations

import time
from typing import Any, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec, confirmation_required


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
        from openjarvis.memory import turns
        from openjarvis.memory.store import TRUST_TRUSTED

        added = _store().add(
            text,
            source="you",
            trust=TRUST_TRUSTED,
            pinned=True,
            private=bool(params.get("private", False)),
            # A rewind past this message forgets it (memory/turns.py).
            turn=turns.current(),
        )
        return ToolResult(
            tool_name=self.tool_id,
            content=(
                f"Remembered: {text}" if added else f"I already remember that: {text}"
            ),
            success=True,
            metadata={"added": added},
        )


#: More clear matches than this and the user is shown them before they go.
_FORGET_WITHOUT_ASKING = 2

#: Words that say how much to forget, not what: "forget everything you know
#: about me" names no subject at all.
_SWEEP_WORDS = frozenset(
    """about all anything data else every everything fact facts info
    information know knew learned mine myself remember remembered said stuff
    thing things told""".split()
)


def _subject_words(about: str) -> List[str]:
    """The words of *about* that name a subject; empty for "everything"."""
    from openjarvis.memory.recall import tokens

    return [t for t in tokens(about) if t not in _SWEEP_WORDS]


def _same_word(a: str, b: str) -> bool:
    # "locker"/"lockers", "address"/"addresses"; short words must be exact,
    # or "code" would be part of "coder" and "cod".
    if a == b:
        return True
    return min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a))


def _clear_matches(facts: List[Any], about: str, subject: List[str]) -> List[Any]:
    """Facts that are plainly about *subject*, best first.

    A fact counts when it has every subject word ("gym locker code" needs
    gym, locker and code). With none like that, the facts sharing the most
    words -- at least half of them -- that score near the best. Any shared
    word was the old rule, and "gym locker code" also took the facts about
    Claude Code (16 October).
    """
    from openjarvis.memory.recall import score_facts, tokens

    scores = score_facts(facts, about)
    wanted = set(subject)
    rows = []
    for fact, score in zip(facts, scores):
        words = set(tokens(fact.text))
        hits = sum(1 for w in wanted if any(_same_word(w, t) for t in words))
        if hits:
            rows.append((hits, score, fact))
    if not rows:
        return []
    full = [r for r in rows if r[0] == len(wanted)]
    if not full:
        most = max(r[0] for r in rows)
        if most * 2 < len(wanted):
            return []
        top = [r for r in rows if r[0] == most]
        best = max(r[1] for r in top)
        full = [r for r in top if r[1] >= 0.75 * best]
    full.sort(key=lambda r: r[1], reverse=True)
    return [r[2] for r in full]


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
                "Only facts that clearly match are removed, so name the "
                "subject the way the user did. One or two clear matches go "
                "at once; more, or a sweeping subject ('everything', 'all "
                "about me'), come back as a confirmation request for the "
                "user. Do not ask before calling: the tool asks when it must."
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
        from openjarvis.security import confirmations

        store = _store()
        facts = store.list()
        subject = _subject_words(about)
        if subject:
            limit = max(1, min(int(params.get("limit") or 5), 20))
            matched = _clear_matches(facts, about, subject)[:limit]
        else:
            # "everything", "all about me": the whole memory, never on a
            # guess -- 16 October, one sentence took 53 facts unasked.
            matched = list(facts)
        if not matched:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"I have nothing remembered about {about!r}.",
                success=True,
                metadata={"removed": []},
            )
        if not subject or len(matched) > _FORGET_WITHOUT_ASKING:
            # Fingerprinted on the subject alone: the facts themselves can
            # change between the ask and the "yes" (the turn learns some).
            asked = {"about": " ".join(about.lower().split())}
            if not confirmations.decide(self.tool_id, asked):
                if subject:
                    what = (
                        f"forget these {len(matched)} facts: "
                        + "; ".join(f.text for f in matched)
                    )
                else:
                    what = (
                        f"forget ALL {len(matched)} remembered facts about the "
                        "user (restorable for seven days)"
                    )
                return confirmation_required(self.tool_id, asked, what)
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
                "more than what is already in context. Only the facts that "
                "bear on the message are in context; if the user asks about "
                "themselves or their own things and the answer is not shown, "
                "search here once before saying you don't know. Not for "
                "general topics; at most three memory searches per message. "
                "Each hit says where it came from and when."
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
        from openjarvis.tools import memory_budget

        if memory_budget.web_searched():
            return ToolResult(
                tool_name=self.tool_id,
                content=memory_budget.AFTER_WEB_MESSAGE,
                success=False,
            )
        if not memory_budget.take():
            return ToolResult(
                tool_name=self.tool_id,
                content=memory_budget.SPENT_MESSAGE,
                success=False,
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
