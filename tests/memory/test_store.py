

def test_reworded_facts_are_treated_as_duplicates(tmp_path):
    """Exact matching let one fact accumulate once per phrasing.

    A real store held five variants of "User is currently in the 1st Term of
    AY 2026-2027", each re-injected into every prompt.
    """
    from openjarvis.memory.store import LocalFactStore

    store = LocalFactStore(tmp_path / "facts.jsonl")

    assert store.add("User is currently in the AY 2026-2027, 1st Term.")
    assert not store.add("User is currently in the 1st Term of AY 2026-2027.")
    assert not store.add("The user is in the 1st Term of AY 2026-2027 currently")
    assert store.add("User prefers dark blue and black.")
    assert store.count() == 2
