"""Nightly memory hygiene (M38): applied at once, restorable for a week."""

from __future__ import annotations

import json

from openjarvis.memory.hygiene import apply_plan, load_runs, parse_plan, run_hygiene
from openjarvis.memory.store import LocalFactStore


class _Engine:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list = []

    def generate(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return {"content": self.reply}


def _store(tmp_path):
    store = LocalFactStore(tmp_path / "facts.jsonl")
    store.add("User drinks coffee in the morning", source="auto")
    store.add("The user has coffee every morning", source="auto")
    store.add("User uses Opera GX as the browser", source="auto")
    store.add("User uses Chrome as the browser", source="auto")
    store.add("User has a class at 11 today", source="auto")
    store.add("Full name: Mark Yanson", source="curated", pinned=True)
    return store


class TestParsePlan:
    def test_reads_json_with_or_without_a_fence(self) -> None:
        plan = parse_plan('```json\n{"stale": ["a"]}\n```')
        assert plan["stale"] == ["a"] and plan["merges"] == []
        assert parse_plan("not json")["stale"] == []


class TestApplyPlan:
    def test_merges_contradictions_and_stale_are_soft_deletes(self, tmp_path) -> None:
        store = _store(tmp_path)
        f = {fact.text: fact for fact in store.list()}
        plan = {
            "merges": [
                {
                    "keep": f["User drinks coffee in the morning"].id,
                    "text": "User drinks coffee every morning",
                    "remove": [f["The user has coffee every morning"].id],
                }
            ],
            "contradictions": [
                {
                    "keep": f["User uses Opera GX as the browser"].id,
                    "remove": [f["User uses Chrome as the browser"].id],
                }
            ],
            "stale": [f["User has a class at 11 today"].id],
        }
        changes = apply_plan(store, plan, list(f.values()))
        assert [c.kind for c in changes] == ["merge", "contradiction", "stale"]
        texts = [fact.text for fact in store.list()]
        assert "User drinks coffee every morning" in texts
        assert "The user has coffee every morning" not in texts
        assert "User uses Chrome as the browser" not in texts
        assert "User has a class at 11 today" not in texts
        removed = {fact.text: fact for fact in store.list_removed()}
        assert removed["User uses Chrome as the browser"].removed_reason.startswith(
            "hygiene"
        )
        # Restorable.
        assert store.restore(removed["User has a class at 11 today"].id)
        assert "User has a class at 11 today" in [fact.text for fact in store.list()]

    def test_pinned_and_unknown_ids_are_never_removed(self, tmp_path) -> None:
        store = _store(tmp_path)
        pinned = next(fact for fact in store.list() if fact.pinned)
        changes = apply_plan(
            store,
            {"stale": [pinned.id, "nope"], "merges": [], "contradictions": []},
            store.list(),
        )
        assert changes == []
        assert pinned.id in {fact.id for fact in store.list()}


class TestRunHygiene:
    def test_a_run_is_logged_and_old_removals_purged(self, tmp_path) -> None:
        store = _store(tmp_path)
        stale = next(fact for fact in store.list() if "today" in fact.text)
        engine = _Engine(json.dumps({"stale": [stale.id]}))
        run = run_hygiene(store, engine, "gpt-5.6-luna", config_dir=tmp_path)
        assert run.facts_before == 6 and run.facts_after == 5
        assert run.changes[0].kind == "stale"
        assert load_runs(tmp_path)[-1]["facts_after"] == 5
        # The model saw ids, days and the pinned mark.
        prompt = engine.calls[0][0][1].content
        assert stale.id in prompt and "PINNED" in prompt

    def test_a_model_failure_changes_nothing_and_is_logged(self, tmp_path) -> None:
        store = _store(tmp_path)

        class _Broken:
            def generate(self, *a, **k):
                raise RuntimeError("cloud down")

        run = run_hygiene(store, _Broken(), "gpt-5.6-luna", config_dir=tmp_path)
        assert run.error == "cloud down" and run.facts_after == 6
