"""The Memory page's API and the remember/forget/recall tools (M38)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="openjarvis[server] not installed")

from openjarvis.memory.store import LocalFactStore


class _Service:
    def __init__(self, store):
        self._store = store


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = LocalFactStore(tmp_path / "facts.jsonl")
    store.add(
        "User studies civil engineering at NU Laguna", source="curated", pinned=True
    )
    store.add("User likes k-drama on weekends", source="auto")
    store.add("User is writing a capstone on concrete curing", source="auto")
    monkeypatch.setattr(
        "openjarvis.core.config.DEFAULT_CONFIG_DIR", tmp_path, raising=False
    )
    monkeypatch.setattr(
        "openjarvis.server.memory_routes.DEFAULT_CONFIG_DIR", tmp_path, raising=False
    )
    monkeypatch.setattr("openjarvis.memory.settings.DEFAULT_CONFIG_DIR", tmp_path)
    monkeypatch.setattr("openjarvis.memory.hygiene.DEFAULT_CONFIG_DIR", tmp_path)
    monkeypatch.setattr("openjarvis.tools.memory_tools._store", lambda: store)
    return store


def _client(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.server.memory_routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.memory_service = _Service(store)
    return TestClient(app)


class TestFacts:
    def test_list_search_add_edit_delete_restore(self, store) -> None:
        client = _client(store)
        facts = client.get("/v1/memory/facts").json()["facts"]
        assert len(facts) == 3 and facts[0]["text"].startswith("User is writing")
        assert facts[-1]["pinned"] is True
        assert facts[0]["pending"] is True  # auto, just now

        hits = client.get("/v1/memory/facts", params={"q": "capstone concrete"}).json()[
            "facts"
        ]
        assert [h["text"] for h in hits] == [
            "User is writing a capstone on concrete curing"
        ]

        added = client.post(
            "/v1/memory/facts",
            json={"text": "User's adviser is Dr. Cruz", "pinned": True},
        )
        assert added.status_code == 200 and added.json()["fact"]["source"] == "you"
        assert (
            client.post(
                "/v1/memory/facts", json={"text": "User's adviser is Dr. Cruz"}
            ).status_code
            == 409
        )

        fid = hits[0]["id"]
        edited = client.put(
            f"/v1/memory/facts/{fid}",
            json={"private": True, "text": "Capstone: concrete curing"},
        )
        assert (
            edited.json()["fact"]["private"] is True
            and edited.json()["fact"]["source"] == "you"
        )

        assert client.delete(f"/v1/memory/facts/{fid}").status_code == 200
        assert fid not in [
            f["id"] for f in client.get("/v1/memory/facts").json()["facts"]
        ]
        removed = client.get("/v1/memory/facts", params={"removed": True}).json()[
            "facts"
        ]
        assert removed[0]["id"] == fid and "Memory page" in removed[0]["removed_reason"]
        assert client.post(f"/v1/memory/facts/{fid}/restore").status_code == 200
        assert fid in [f["id"] for f in client.get("/v1/memory/facts").json()["facts"]]

    def test_validation(self, store) -> None:
        client = _client(store)
        assert client.post("/v1/memory/facts", json={"text": "  "}).status_code == 400
        assert (
            client.put("/v1/memory/facts/nope", json={"pinned": True}).status_code
            == 404
        )
        assert client.delete("/v1/memory/facts/nope").status_code == 404


class TestSettingsAndProfile:
    def test_settings_round_trip(self, store) -> None:
        client = _client(store)
        assert client.get("/v1/memory/settings").json()["extraction_mode"] == "cloud"
        assert (
            client.put("/v1/memory/settings", json={"extraction_mode": "local"}).json()[
                "extraction_mode"
            ]
            == "local"
        )
        assert (
            client.put(
                "/v1/memory/settings", json={"extraction_mode": "mars"}
            ).status_code
            == 400
        )

    def test_profile_round_trip(self, store, tmp_path) -> None:
        client = _client(store)
        (tmp_path / "USER.md").write_text("# Profile\n", encoding="utf-8")
        assert client.get("/v1/memory/profile").json()["text"] == "# Profile\n"
        assert (
            client.put(
                "/v1/memory/profile", json={"text": "# Profile\n- new"}
            ).status_code
            == 200
        )
        assert (tmp_path / "USER.md").read_text(encoding="utf-8").endswith("- new")
        assert (tmp_path / "USER.md.bak").exists()


class TestTools:
    def test_remember_forget_restore_recall(self, store) -> None:
        from openjarvis.tools.memory_tools import (
            ForgetTool,
            RecallTool,
            RememberTool,
            RestoreMemoryTool,
        )

        assert (
            RememberTool().execute(fact="User's adviser is Dr. Cruz").metadata["added"]
            is True
        )
        adviser = next(f for f in store.list() if "Cruz" in f.text)
        assert adviser.pinned and adviser.source == "you"

        result = ForgetTool().execute(about="k-drama")
        assert result.success and len(result.metadata["removed"]) == 1
        assert "k-drama" not in " ".join(f.text for f in store.list())

        restored = RestoreMemoryTool().execute()
        assert restored.success and "k-drama" in restored.content
        assert "k-drama" in " ".join(f.text for f in store.list())

        recall = RecallTool().execute(query="capstone")
        assert (
            recall.success
            and "capstone" in recall.content
            and "a conversation on" in recall.content
        )
        assert "Nothing remembered" in RecallTool().execute(query="zeppelins").content

    def test_forget_with_no_match_removes_nothing(self, store) -> None:
        from openjarvis.tools.memory_tools import ForgetTool

        assert ForgetTool().execute(about="zeppelins").metadata["removed"] == []
        assert store.count() == 3


class TestFold:
    def test_memory_md_bullets_skip_the_rules(self, store, tmp_path) -> None:
        from openjarvis.memory.migrate import fold_memory_md, memory_md_bullets

        text = "\n".join(
            [
                "# Sage Memory",
                "",
                "## Current System",
                "",
                "- Engine is Ollama.",
                "- Model is qwen.",
                "",
                "## Memory Rules",
                "",
                "- Store only durable info.",
                "",
            ]
        )
        assert memory_md_bullets(text) == ["Engine is Ollama.", "Model is qwen."]
        (tmp_path / "MEMORY.md").write_text(text, encoding="utf-8")
        assert fold_memory_md(store, tmp_path) == 2
        assert (tmp_path / "MEMORY.md.folded").exists() and not (
            tmp_path / "MEMORY.md"
        ).exists()
        assert all(f.pinned for f in store.list() if f.source == "curated")
