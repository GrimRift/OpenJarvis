"""Which facts reach the model for a turn (M38).

Until now facts were injected newest-first until the budget ran out: with
388 facts and a 2,048-token budget, the newest ninety went and the other
three hundred never did -- including the curated identity facts, which are
the oldest. "Sage forgot" nearly always meant "crowded out".

Selection is now by relevance to the message, with a pinned core that
always goes and recency only as the tie-break. A small BM25 of our own
rather than the document backend: facts are one line each, there are a few
hundred, and scoring them is microseconds; the point is that the ranking
is the same one the Memory page's search shows, so what the user sees is
what the model can recall.

"Relevant only" (1 Oct, the user's middle ground, on by default): the budget
is no longer filled with unrelated newest facts. A turn gets the pinned
core, the facts that share a word with the message and the five newest --
about 1,000 fewer prompt tokens a turn -- and a line naming the topics of
what was left out, so the model knows to call ``recall``.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Callable, List, Sequence

from openjarvis.memory.store import Fact

_TOKEN = re.compile(r"[\w']+", re.UNICODE)
_STOP = frozenset(
    """a an and are as at be by for from has have i in is it its me my of on or
    sage sir that the this to user was we what when who will with you your""".split()
)
_K1 = 1.5
_B = 0.75


def tokens(text: str) -> List[str]:
    return [t for t in (w.lower() for w in _TOKEN.findall(text)) if t not in _STOP]


def score_facts(facts: Sequence[Fact], query: str) -> List[float]:
    """BM25 score of every fact against *query*, in order. Zero means no
    shared term at all."""
    query_terms = tokens(query)
    if not query_terms or not facts:
        return [0.0] * len(facts)
    docs = [tokens(f.text) for f in facts]
    n = len(docs)
    avg_len = sum(len(d) for d in docs) / n if n else 1.0
    df: Counter[str] = Counter()
    for doc in docs:
        for term in set(doc):
            df[term] += 1
    scores: List[float] = []
    for doc in docs:
        tf = Counter(doc)
        length = len(doc) or 1
        score = 0.0
        for term in query_terms:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            freq = tf[term]
            score += (
                idf
                * (freq * (_K1 + 1))
                / (freq + _K1 * (1 - _B + _B * length / avg_len))
            )
        scores.append(score)
    return scores


NEWEST_ALWAYS = 5


def select_facts(
    facts: Sequence[Fact],
    query: str,
    budget_tokens: int,
    count_tokens: Callable[[str], int],
    *,
    relevant_only: bool = False,
) -> List[Fact]:
    """The facts for this turn, within *budget_tokens*.

    Pinned facts go first, oldest first, whatever the query. The rest are
    ranked by relevance, newest first among equals; facts with no term in
    common with the message are still eligible, so a bare "hi" gets the
    newest few rather than nothing. Returned in stored order, so the model
    sees them in the order they were learned.

    With *relevant_only*, facts sharing no term with the message are left
    out except the :data:`NEWEST_ALWAYS` newest, even when budget remains.
    """
    # Positions, not ids: facts built in tests may share an empty id.
    live = [(i, f) for i, f in enumerate(facts) if f.trusted_for_recall]
    pinned = [(i, f) for i, f in live if f.pinned]
    rest = [(i, f) for i, f in live if not f.pinned]
    scores = score_facts([f for _, f in rest], query)
    ranked = sorted(
        zip(rest, scores),
        key=lambda item: (item[1], item[0][1].created_at, item[0][0]),
        reverse=True,
    )
    if relevant_only:
        matched = [entry for entry, score in ranked if score > 0]
        unmatched = [entry for entry, score in ranked if score <= 0]
        candidates = pinned + matched + unmatched[:NEWEST_ALWAYS]
    else:
        candidates = pinned + [entry for entry, _ in ranked]
    chosen: List[tuple[int, Fact]] = []
    used = 0
    for i, fact in candidates:
        cost = count_tokens(fact.text)
        if used + cost > budget_tokens:
            continue
        chosen.append((i, fact))
        used += cost
    chosen.sort(key=lambda item: item[0])
    return [f for _, f in chosen]


def topics_not_sent(facts: Sequence[Fact], sent: Sequence[Fact]) -> str:
    """'school (41), research (38), ...' for the recallable facts not in
    *sent*, most first; "" when nothing tagged was left out."""
    sent_ids = {id(f) for f in sent}
    counts: Counter[str] = Counter(
        f.topic
        for f in facts
        if id(f) not in sent_ids and f.trusted_for_recall and f.topic
    )
    return ", ".join(f"{topic} ({n})" for topic, n in counts.most_common())


__all__ = [
    "NEWEST_ALWAYS",
    "score_facts",
    "select_facts",
    "tokens",
    "topics_not_sent",
]
