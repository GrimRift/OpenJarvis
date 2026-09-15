"""Relevance recall (M38): what reaches the model is chosen by the message."""

from __future__ import annotations

from openjarvis.memory.recall import select_facts
from openjarvis.memory.store import Fact


def _f(text, created_at, pinned=False):
    return Fact(text=text, created_at=created_at, pinned=pinned, id=f"{created_at}")


def _count(text: str) -> int:
    return len(text.split())


class TestSelectFacts:
    def test_relevant_old_facts_beat_irrelevant_new_ones(self) -> None:
        facts = [
            _f("User's capstone is about concrete curing", 1),
            _f("User prefers dark mode", 2),
            _f("User drinks coffee", 3),
            _f("User likes k-drama", 4),
        ]
        chosen = select_facts(
            facts, "how is the capstone going", budget_tokens=6, count_tokens=_count
        )
        assert [f.text for f in chosen] == ["User's capstone is about concrete curing"]

    def test_pinned_always_go_first(self) -> None:
        facts = [
            _f("Full name: Mark", 1, pinned=True),
            _f("User's capstone is about concrete curing", 2),
            _f("User likes k-drama", 3),
        ]
        chosen = select_facts(
            facts, "k-drama tonight", budget_tokens=7, count_tokens=_count
        )
        assert [f.text for f in chosen] == ["Full name: Mark", "User likes k-drama"]

    def test_no_overlap_falls_back_to_newest(self) -> None:
        facts = [_f("old thing", 1), _f("newer thing", 2), _f("newest thing", 3)]
        chosen = select_facts(facts, "hi", budget_tokens=4, count_tokens=_count)
        assert [f.text for f in chosen] == ["newer thing", "newest thing"]

    def test_returned_in_learned_order(self) -> None:
        facts = [
            _f("alpha capstone", 1),
            _f("beta capstone", 2),
            _f("gamma capstone", 3),
        ]
        chosen = select_facts(facts, "capstone", budget_tokens=100, count_tokens=_count)
        assert [f.created_at for f in chosen] == [1, 2, 3]

    def test_untrusted_never(self) -> None:
        bad = Fact(text="capstone hostile", created_at=5, trust="untrusted", id="x")
        chosen = select_facts([bad, _f("capstone fine", 1)], "capstone", 100, _count)
        assert [f.text for f in chosen] == ["capstone fine"]
