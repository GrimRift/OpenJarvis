"""Only the facts that bear on the message, plus what else is on file (1 Oct).

Every turn used to fill the 2,048-token fact budget: ~98 facts even for "hi".
The user's middle ground: the pinned core, the facts sharing a word with the
message, and the five newest; the topics of the rest are named so the model
knows to call ``recall``. A topic per fact is chosen by the model.
"""

from __future__ import annotations

import time

from openjarvis.memory.recall import NEWEST_ALWAYS, select_facts, topics_not_sent
from openjarvis.memory.service import MemoryService
from openjarvis.memory.store import Fact, LocalFactStore
from openjarvis.memory.topics import TOPICS, tag_facts, tag_untagged


def _words(text: str) -> int:
    return len(text.split())


def _fact(text, created_at, *, topic="", pinned=False, trust="auto"):
    return Fact(
        text=text,
        created_at=created_at,
        id=f"id{created_at}",
        topic=topic,
        pinned=pinned,
        trust=trust,
    )


def _facts():
    facts = [
        _fact("Mark's home is in Quezon Province.", 1, pinned=True, topic="personal")
    ]
    facts.append(_fact("User uses an AULA HERO 60HE keyboard.", 2, topic="computer"))
    facts.append(_fact("User plans to sell a 2019 Suzuki Ertiga.", 3, topic="personal"))
    for n in range(20):
        facts.append(
            _fact(f"User likes Monster Hunter thing {n}.", 10 + n, topic="games")
        )
    return facts


def test_relevant_only_sends_pinned_matches_and_newest_five():
    facts = _facts()
    got = select_facts(
        facts, "which keyboard do I use", 10_000, _words, relevant_only=True
    )
    texts = [f.text for f in got]
    assert "Mark's home is in Quezon Province." in texts  # pinned
    assert "User uses an AULA HERO 60HE keyboard." in texts  # matches
    assert "User plans to sell a 2019 Suzuki Ertiga." not in texts  # unrelated
    newest = [f.text for f in facts[-NEWEST_ALWAYS:]]
    assert all(t in texts for t in newest)
    assert len(got) == 1 + 1 + NEWEST_ALWAYS


def test_switch_off_fills_the_budget_as_before():
    facts = _facts()
    got = select_facts(facts, "which keyboard do I use", 10_000, _words)
    assert len(got) == len(facts)


def test_hi_gets_only_pinned_and_newest():
    got = select_facts(_facts(), "hi", 10_000, _words, relevant_only=True)
    assert len(got) == 1 + NEWEST_ALWAYS


def test_topics_line_names_what_was_left_out_most_first():
    facts = _facts()
    sent = select_facts(
        facts, "which keyboard do I use", 10_000, _words, relevant_only=True
    )
    assert topics_not_sent(facts, sent) == "games (15), personal (1)"


def test_untrusted_and_untagged_facts_are_not_named():
    facts = [
        _fact("hidden", 1, topic="people", trust="untrusted"),
        _fact("untagged", 2),
        _fact("kept", 3, topic="music"),
    ]
    assert topics_not_sent(facts, []) == "music (1)"


def test_context_carries_the_topics_line_and_the_recall_hint():
    from openjarvis.core.types import Message, Role
    from openjarvis.tools.storage.context import inject_context

    msgs = inject_context(
        "which keyboard do I use",
        [Message(role=Role.USER, content="which keyboard do I use")],
        None,
        facts=_facts(),
    )
    text = msgs[0].content
    assert "AULA HERO 60HE" in text
    assert "Suzuki" not in text
    assert (
        "Also on file but not shown here, by topic: games (15), personal (1)." in text
    )
    assert "call recall once before saying you don't know" in text
    assert "For general topics, do not search memory." in text


def test_context_with_the_switch_off_has_no_topics_line(tmp_path, monkeypatch):
    from openjarvis.core.types import Message, Role
    from openjarvis.memory import settings as memory_settings
    from openjarvis.tools.storage.context import inject_context

    memory_settings.save_memory_settings(
        memory_settings.MemorySettings(relevant_only=False)
    )
    msgs = inject_context(
        "which keyboard do I use",
        [Message(role=Role.USER, content="which keyboard do I use")],
        None,
        facts=_facts(),
    )
    assert "Suzuki" in msgs[0].content
    assert "Also on file" not in msgs[0].content


# -- topics -----------------------------------------------------------------


class FakeEngine:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def generate(self, messages, **kwargs):
        self.calls.append(messages)
        return {"content": self.answer}


def test_the_model_tags_with_names_from_the_list_only():
    facts = [_fact("a", 1), _fact("b", 2), _fact("c", 3)]
    engine = FakeEngine('Sure: {"1": "Games", "2": "astrology", "3": "school"}')
    assert tag_facts(engine, "m", facts) == {"id1": "games", "id3": "school"}
    prompt = engine.calls[0][0].content
    assert all(name in prompt for name in TOPICS)


def test_tag_untagged_writes_topics_and_tries_each_fact_once(tmp_path):
    store = LocalFactStore(tmp_path / "facts.jsonl")
    store.add("User plays Monster Hunter.")
    store.add("User has a class on Mondays.")
    engine = FakeEngine('{"1": "games"}')  # the second one is not answered
    tried: set[str] = set()
    assert tag_untagged(store, engine, "m", tried) == 1
    assert [f.topic for f in store.list()] == ["games", ""]
    # Reloaded from disk, the topic is still there.
    assert LocalFactStore(tmp_path / "facts.jsonl").list()[0].topic == "games"
    # The fact the model skipped is not asked about again this run.
    assert tag_untagged(store, engine, "m", tried) == 0
    assert len(engine.calls) == 1


class TaggingExtractor:
    def __init__(self, engine):
        self.engine = engine

    def tagging_model(self):
        return "m"

    def extract(self, user_text, assistant_text="", answered_by=""):
        return ["User drinks black coffee."]


def test_the_worker_backfills_at_start_and_tags_new_facts(tmp_path):
    store = LocalFactStore(tmp_path / "facts.jsonl")
    store.add("User plays Monster Hunter.")
    engine = FakeEngine('{"1": "personal"}')
    svc = MemoryService(store, TaggingExtractor(engine))
    svc.start()
    try:
        deadline = time.time() + 3
        while time.time() < deadline and not store.list()[0].topic:
            time.sleep(0.02)
        assert store.list()[0].topic == "personal"  # backfill
        svc.submit("I take my coffee black", "Noted.")
        while time.time() < deadline and (
            len(store.list()) < 2 or not store.list()[1].topic
        ):
            time.sleep(0.02)
        assert [f.topic for f in store.list()] == ["personal", "personal"]
    finally:
        svc.stop()
