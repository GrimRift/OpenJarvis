"""pin_curated runs once: an unpin on the Memory page survives a restart."""

from __future__ import annotations

from openjarvis.memory.migrate import pin_curated
from openjarvis.memory.store import LocalFactStore


def _store(tmp_path):
    return LocalFactStore(tmp_path / "facts.jsonl")


def test_first_run_pins_the_old_curated_facts(tmp_path):
    store = _store(tmp_path)
    store.add("User's name is Mark", source="curated")
    store.add("User likes tea", source="auto")
    assert pin_curated(store, tmp_path) == 1
    by_text = {f.text: f for f in store.list()}
    assert by_text["User's name is Mark"].pinned is True
    assert by_text["User likes tea"].pinned is False


def test_an_unpinned_curated_fact_stays_unpinned_after_a_restart(tmp_path):
    store = _store(tmp_path)
    store.add("User's name is Mark", source="curated")
    store.add("Sage runs on Ollama", source="curated")
    pin_curated(store, tmp_path)
    ollama = next(f for f in store.list() if "Ollama" in f.text)
    store.update(ollama.id, pinned=False)

    assert pin_curated(store, tmp_path) == 0  # the next start
    assert next(f for f in store.list() if "Ollama" in f.text).pinned is False


def test_a_store_migrated_before_the_marker_is_left_alone(tmp_path):
    """Live ran the old every-start version: its curated pins are in place,
    and a curated fact that is unpinned now was unpinned by the user."""
    store = _store(tmp_path)
    store.add("User's name is Mark", source="curated", pinned=True)
    store.add("Sage runs on Ollama", source="curated", pinned=False)
    assert pin_curated(store, tmp_path) == 0
    assert next(f for f in store.list() if "Ollama" in f.text).pinned is False
    assert (tmp_path / "memory_pin_curated.done").exists()
